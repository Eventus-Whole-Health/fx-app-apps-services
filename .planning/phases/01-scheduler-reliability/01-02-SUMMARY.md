---
phase: 01-scheduler-reliability
plan: 02
subsystem: scheduler
tags: [watchdog, retry, exponential-backoff, polling, stuck-rows]

requires:
  - phase: 01-scheduler-reliability
    provides: "Status lifecycle (log_running, log_timeout) and error capture from plan 01-01"
provides:
  - "Configurable per-service watchdog via max_execution_minutes with COALESCE default"
  - "Exponential backoff retry (2/4/8/16/32/64/120 min cap) for services with max_retries > 0"
  - "Bounded polling loop (MAX_POLLING_DURATION = 3300s / 55 min)"
  - "next_retry_at filter in service fetch queries to respect backoff timing"
  - "retry_count and next_retry_at reset on successful execution"
affects: [api-layer, dashboard]

tech-stack:
  added: []
  patterns:
    - "Two-step SELECT + batch UPDATE for watchdog (avoids N+1 and OUTPUT clause limitations)"
    - "calculate_next_retry_at() returns SQL DATEADD expression for server-side time calculation"
    - "next_retry_at IS NULL OR next_retry_at <= eastern_time in WHERE clause gates retry eligibility"

key-files:
  created: []
  modified:
    - "functions/scheduler/timer_function.py"

key-decisions:
  - "Two-step SELECT + batch UPDATE for watchdog instead of OUTPUT clause — SQL Executor API may not support OUTPUT with UPDATE"
  - "Retry logic in both handle_service_failure() and handle_service_exception() — exceptions deserve retry too"
  - "Bypass query does NOT filter by next_retry_at — forced execution means run now regardless"
  - "Backoff capped at 120 minutes (2 hours) to prevent excessive delay"

patterns-established:
  - "All retry/failure paths check max_retries before deciding between retry and permanent failure"
  - "Success always resets retry_count=0 and next_retry_at=NULL"

requirements-completed:
  - SCHED-03
  - SCHED-04

duration: 8min
completed: 2026-02-27
---

# Phase 01 Plan 02: Watchdog and Retry Summary

**Configurable per-service watchdog with COALESCE thresholds, exponential backoff retry respecting max_retries, and bounded 55-minute polling loop**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-28T00:04:00Z
- **Completed:** 2026-02-28T00:12:00Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- Rewrote watchdog to use per-service `max_execution_minutes` with COALESCE default of 30 minutes, using two-step SELECT + batch UPDATE
- Added `calculate_next_retry_at()` helper returning SQL DATEADD expression for exponential backoff (2/4/8/16/32/64/120 min cap)
- Replaced `handle_service_failure_no_retry()` with `handle_service_failure()` supporting configurable retry with exponential backoff
- Updated `handle_service_exception()` with same retry logic (exceptions can retry too)
- Added `next_retry_at` filter to standard and forced service fetch queries (not bypass query)
- Added `next_retry_at = NULL` to success UPDATE block to clear stale retry timestamps
- Bounded `poll_master_log_for_completion()` at 55 minutes with graceful timeout (returns 408)
- Removed dead code: `is_sleeping_service_response()`

## Task Commits

Each task was committed atomically:

1. **Task 1: Configurable watchdog thresholds and bounded polling loop** - `f9e644d` (feat)
2. **Task 2: Exponential backoff retry for failed services** - `3582f57` (feat)

## Files Created/Modified
- `functions/scheduler/timer_function.py` - Watchdog rewrite, retry logic, bounded polling, dead code removal

## Decisions Made
- Two-step SELECT + batch UPDATE for watchdog instead of OUTPUT clause (SQL Executor API compatibility)
- Retry logic added to both handle_service_failure() AND handle_service_exception() so exceptions also benefit from retry
- Bypass query intentionally omits next_retry_at filter since bypass means "run now regardless"
- Exponential backoff capped at 120 minutes to prevent excessive delay while still giving services meaningful retry windows

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required

**Database schema:** The `next_retry_at` column must exist in `jgilpatrick.apps_central_scheduling`. If it does not already exist, run:
```sql
ALTER TABLE jgilpatrick.apps_central_scheduling ADD next_retry_at DATETIME NULL;
```

The `max_execution_minutes` column must also exist. If not:
```sql
ALTER TABLE jgilpatrick.apps_central_scheduling ADD max_execution_minutes INT NULL;
```

## Next Phase Readiness
- Phase 1 (Scheduler Reliability) is complete: status lifecycle, error capture, watchdog, retry, and schedule evaluation all implemented
- Ready for Phase 2 (API Layer) which will expose scheduler state and control endpoints
- No blockers for next phase

---
*Phase: 01-scheduler-reliability*
*Completed: 2026-02-27*
