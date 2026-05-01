# Scheduler Simplification PRD

**Issue:** [#5](https://github.com/Eventus-Whole-Health/fx-app-apps-services/issues/5)
**Date:** 2026-05-01
**Status:** In Progress (Phase 1-2)

## Problem

On 2026-05-01 the EQuIP Data Pipeline (`daily {"times": ["10:00"]}`) fired approximately 22 times in a single day. The runaway firing was caused by three interacting bugs in the scheduler's retry logic:

1. **Permanent-failure handler does not clear `next_retry_at`.** When `retry_count >= max_retries`, the handler sets `status='failed'` but leaves `next_retry_at` populated (`timer_function.py` lines 303-318).

2. **`should_trigger_service_bypass_window` short-circuits on elapsed `next_retry_at`.** If `next_retry_at` is set and in the past, the function returns `True` immediately -- bypassing all schedule window checks and same-day dedup guards (`timer_function.py` lines 718-741). The comment reads: "fire the retry regardless of schedule window or same-day dedup."

3. **Fetch query includes `status='failed'`.** The main service query uses `WHERE status IN ('pending', 'failed')`, so terminally failed services remain eligible every 15-minute tick (`timer_function.py` lines 421, 436).

Together: a service fails, gets marked `failed` with a stale `next_retry_at`, then re-fires every 15 minutes because it passes both the status filter and the bypass-window short-circuit.

## Solution

Split the scheduler into two independent timer functions and remove all retry logic.

### Dispatcher (15-minute timer, existing `scheduler_timer`)

Answers: "Is this job due now?" For each active schedule whose frequency window matches the current time and that has not already been triggered today (or this hour, etc.), fire the HTTP request and write a row to `apps_scheduler_execution_log` with status `dispatched`. Update `apps_central_scheduling` with the trigger receipt. No retry. A failed run waits for its next scheduled occurrence.

### Job Manager (2-minute timer, new `job_manager_timer`)

Polls `apps_scheduler_execution_log` for rows with `status='dispatched'` and a non-null `log_id`. For each, joins to `apps_master_services_log` to check terminal state:
- **Terminal** (`success`, `failed`, `warning`): update execution_log with final status, `completed_at`, `duration_ms`. Update `apps_central_scheduling` receipt columns (`last_response_code`, `last_response_detail`, `log_id`, `error_message`).
- **Timeout**: if `DATEDIFF(MINUTE, triggered_at, now) > max_execution_minutes`, mark execution_log as `timeout` (HTTP 408) and update central receipt.
- **Still running**: skip; will check again next tick.

### Schema Changes

`apps_central_scheduling.max_execution_minutes`:
- Already exists as `INT NULL`.
- Phase 1 migration backfills NULLs to 30, adds `DEFAULT 30` constraint, makes `NOT NULL`.

Deprecated columns left in place for Phase 5 removal:
- `retry_count`
- `max_retries`
- `next_retry_at`
- `processed_at` (receipt replaced by execution_log)
- `status` (transient state moves to execution_log)

### What Is NOT Changing

- `apps_central_scheduling` schema is additive only -- no columns dropped.
- Existing status endpoints (`/api/status/{log_id}`, `/api/result/{log_id}`) are unchanged.
- Trigger function (`/api/trigger/{id}`) is unchanged.

## Architecture Diagram

```
 Every 15 min                     Every 2 min
+--------------+               +---------------+
| Dispatcher   |               | Job Manager   |
| (scheduler_  |               | (job_manager_ |
|  timer)      |               |  timer)       |
+------+-------+               +-------+-------+
       |                               |
       | 1. Query central_scheduling   | 1. Query execution_log
       |    for due services           |    WHERE status='dispatched'
       |                               |
       | 2. Fire HTTP, write           | 2. JOIN master_services_log
       |    execution_log row          |    for terminal state
       |    (status='dispatched')      |
       |                               | 3. Update execution_log +
       | 3. Update central_scheduling  |    central_scheduling receipt
       |    receipt columns            |
       v                               v
+----------------------------------------------+
| apps_central_scheduling (declarative config) |
| apps_scheduler_execution_log (attempt log)   |
| apps_master_services_log (service telemetry) |
+----------------------------------------------+
```

## Acceptance Criteria

1. After deploy, no service fires more than its configured frequency on any day, regardless of failure history. (E.g., `daily {"times": ["10:00"]}` fires at most once per day.)
2. `apps_central_scheduling` rows contain no transient state.
3. `next_retry_at`, `retry_count`, `max_retries` columns are unused by application code (eligible for removal in Phase 5).
4. `apps_scheduler_execution_log` records every trigger attempt and its terminal status.
5. Long-running async jobs (e.g., EQuIP pipeline) reach terminal state via the job manager, without scheduler involvement.
6. A job that hangs longer than `max_execution_minutes` is marked `timeout` (HTTP 408) in the execution log and on the central row.
7. The 15-minute scheduler tick on a fully healthy system does no work if no jobs are due.

## Phased Implementation

| Phase | Scope | PR |
|-------|-------|----|
| 1 | Additive schema migration (`max_execution_minutes` NOT NULL DEFAULT 30, clear stuck state) | This branch |
| 2 | Job manager timer function (`job_manager.py`) | This branch |
| 3 | Dispatcher refactor (remove retry logic, simplified `should_trigger`, execution_log writes) | Next PR |
| 4 | Soak period (~2 weeks): verify no spurious fires, verify timeouts | Monitoring |
| 5 | Drop deprecated columns (`retry_count`, `max_retries`, `next_retry_at`, etc.) | Future PR |

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Job manager misses a dispatched row | 2-minute cadence means at most 2 min delay; execution_log is durable |
| `max_execution_minutes` too short for some jobs | Default 30 covers current workloads; EQuIP already runs ~20 min; can be tuned per-schedule |
| Race between dispatcher and job manager on central row | They write non-overlapping columns; dispatcher writes trigger receipt, job manager writes completion receipt |
