---
status: passed
phase: 01-scheduler-reliability
verified: 2026-02-27
requirements_verified: [SCHED-01, SCHED-02, SCHED-03, SCHED-04, SCHED-05]
---

# Phase 1: Scheduler Reliability — Verification Report

**Score:** 5/5 requirements verified | **Result:** PASSED

## Requirement Verification

### SCHED-01: Status Lifecycle
**Status:** PASS

Evidence:
- `ServiceLogger` has full lifecycle methods: `log_start()`, `log_running()`, `log_success()`, `log_error()`, `log_warning()`, `log_timeout()` (service_logger.py lines 108-293)
- `log_running()` transitions pending -> running in SQL and emits Seq event (lines 189-220)
- `log_timeout()` marks service as timeout with error message (lines 275-293)
- Timer trigger calls `log_start()` then `log_running()` before processing, then terminal state after (timer_function.py lines 1175-1254)
- HTTP trigger follows same pattern (timer_function.py lines 1329-1492)
- All execution paths end in a terminal state: success, failed, warning, or timeout

### SCHED-02: Error Capture
**Status:** PASS

Evidence:
- Structured Seq events on all failure paths:
  - `ScheduledServiceFailed` — permanent failure (timer_function.py line 937)
  - `ScheduledServiceRetry` — retry scheduled (lines 898, 1091)
  - `ScheduledServiceException` — exception during processing (line 1126)
  - `WatchdogTimeout` — stuck service detected (line 240)
  - `PollingTimeout` — polling loop exceeded max duration (line 788)
  - `SchedulerProcessingFailed` — outer exception handler (line 541)
  - `WatchdogError` — watchdog itself fails (line 273)
- SQL logging via ServiceLogger on all paths (log_error, log_warning, log_timeout)
- Error messages include sanitized details truncated to 500 chars for Seq, 3900 for SQL

### SCHED-03: Stuck Row Detection
**Status:** PASS

Evidence:
- `check_and_handle_stuck_processing_services()` function (timer_function.py lines 194-276)
- Uses `COALESCE(max_execution_minutes, 30)` for per-service configurable thresholds (lines 217, 257)
- Two-step approach: SELECT stuck services (for logging), then batch UPDATE (no N+1)
- Structured Seq event `WatchdogTimeout` emitted for each stuck service (line 240)
- Runs at start of every scheduler execution (line 315)
- `DEFAULT_MAX_EXECUTION_MINUTES = 30` constant (line 27)

### SCHED-04: Configurable Retry
**Status:** PASS

Evidence:
- `calculate_next_retry_at()` helper with exponential backoff capped at 120 min (timer_function.py lines 141-151)
- `RETRY_BASE_DELAY_MINUTES = 2` constant (line 29)
- `handle_service_failure()` checks `max_retries` and branches between retry and permanent failure (lines 840-939)
- `handle_service_exception()` has same retry logic (lines 1047-1133)
- Services with `max_retries = 0` or NULL fail immediately (preserving email-safe behavior)
- Retry sets status='pending', increments retry_count, sets next_retry_at
- Service fetch queries filter `AND (next_retry_at IS NULL OR next_retry_at <= ...)` (lines 349, 366)
- Success UPDATE resets `retry_count = 0, next_retry_at = NULL` (lines 485-486)
- Bypass query does NOT filter by next_retry_at (correct — forced execution)

### SCHED-05: Schedule Evaluation Correctness
**Status:** PASS

Evidence:
- `_safe_localize()` helper handles `pytz.exceptions.AmbiguousTimeError` during DST transitions (lines 564-570)
- All datetime parsing uses `_safe_localize()` for Eastern time localization (lines 586-615)
- All frequency types handled with both with-config and without-config branches:
  - `once` — triggers if never triggered and start_date reached (line 620)
  - `daily` with config — checks time windows and last_triggered date (lines 623-650)
  - `daily` without config — runs once per day at any window (lines 647-650)
  - `weekly` with config — checks day of week and time window (lines 652-683)
  - `weekly` without config — runs once per 7 days (lines 679-683)
  - `hourly` with config — checks minute windows (lines 685+)
  - `hourly` without config — runs once per hour
  - `monthly` with config — checks day of month and time
  - `monthly` without config — runs once per month
- Hour/minute range validation in `is_within_schedule_window()` rejects malformed values (lines 172-173)

## Success Criteria Verification

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Every execution row transitions to terminal state | PASS | log_start -> log_running -> log_success/error/warning/timeout in both timer and HTTP paths |
| 2 | Every failure path logged to SQL + Seq | PASS | 7 distinct Seq EventTypes cover all failure paths; ServiceLogger writes to SQL on all paths |
| 3 | Watchdog identifies stuck rows with configurable threshold | PASS | COALESCE(max_execution_minutes, 30) in SELECT/UPDATE, runs every 15 min |
| 4 | Services with max_retries > 0 retry with exponential backoff | PASS | calculate_next_retry_at with 2^n backoff, capped at 120 min, visible in Seq logs |
| 5 | Schedules fire correctly across DST with all frequency types | PASS | _safe_localize handles AmbiguousTimeError, all 5 frequency types have complete evaluation |

## Must-Have Artifacts

| Artifact | Verified |
|----------|----------|
| `functions/shared/service_logger.py` — log_running(), log_timeout() methods | Yes |
| `functions/scheduler/timer_function.py` — watchdog with max_execution_minutes | Yes |
| `functions/scheduler/timer_function.py` — exponential backoff retry | Yes |
| `functions/scheduler/timer_function.py` — bounded polling (MAX_POLLING_DURATION) | Yes |

## Schema Requirements

The following columns must exist in `jgilpatrick.apps_central_scheduling`:
- `next_retry_at DATETIME NULL` — stores next retry time for exponential backoff
- `max_execution_minutes INT NULL` — configurable watchdog threshold per service

If these columns do not exist, run the ALTER TABLE statements from the 01-02-SUMMARY.md.

---
*Phase: 01-scheduler-reliability*
*Verified: 2026-02-27*
