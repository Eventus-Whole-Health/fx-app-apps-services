---
phase: 02-api-layer
plan: 02
subsystem: api
tags: [azure-functions, crud, validation, manual-trigger, sql]

requires:
  - phase: 02-api-layer
    provides: Scheduler API Blueprint module, sanitize_sql_string helper, VALID_FREQUENCIES constant
provides:
  - POST /api/scheduler/services — create new schedule with validation
  - PUT /api/scheduler/services/{id} — partial update with field validation
  - DELETE /api/scheduler/services/{id} — soft-delete (is_active=0)
  - POST /api/scheduler/services/{id}/trigger — manual trigger via existing scheduler infrastructure
  - validate_schedule_input() — reusable input validation for schedule CRUD
  - _sql_value() helper — safe Python-to-SQL value conversion with NULL handling
  - UPDATABLE_FIELDS and SYSTEM_MANAGED_FIELDS constants for field protection
affects: [03-keystone-dashboard]

tech-stack:
  added: []
  patterns:
    - Dynamic UPDATE with partial field support (only SET columns present in request body)
    - Soft-delete pattern (is_active=0, not hard DELETE)
    - Cross-module import for scheduler reuse (from ..scheduler.timer_function)

key-files:
  created: []
  modified:
    - functions/scheduler_api/scheduler_endpoints.py

key-decisions:
  - "Option C for manual trigger — await full execution rather than fire-and-forget, since Keystone ASP has unlimited timeout and scheduler creates its own log entries"
  - "Query log_id from apps_central_scheduling.log_id column after trigger completes — avoids double-logging"
  - "System-managed fields explicitly blocked from update (SYSTEM_MANAGED_FIELDS constant)"
  - "Fetch newly created row by function_app + service DESC — cannot use SCOPE_IDENTITY via SQL Executor API"

patterns-established:
  - "CRUD validation via validate_schedule_input() with require_all flag for create vs update"
  - "_sql_value() handles NULL, bool, int, and string types consistently"
  - "Manual trigger reuses process_scheduled_services_with_overrides — no duplicate trigger paths"
  - "Soft-delete only — no hard DELETE operations on scheduling table"

requirements-completed:
  - API-03
  - API-04

duration: 8min
completed: 2026-02-27
---

# Plan 02-02: Write Endpoints Summary

**Schedule CRUD with validation and manual trigger reusing existing scheduler infrastructure — no duplicate trigger code paths**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-27
- **Completed:** 2026-02-27
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments
- Create endpoint validates frequency, trigger_url, function_app, service, and schedule_config JSON before INSERT
- Update endpoint supports partial updates — only provided fields are modified, system-managed fields are blocked
- Delete endpoint uses soft-delete (is_active=0) per CONTEXT.md decision
- Manual trigger endpoint imports and calls process_scheduled_services_with_overrides directly — zero duplicate trigger logic
- Trigger returns log_id and status_url from the scheduling table for dashboard polling
- All user input sanitized via sanitize_sql_string() and _sql_value() before SQL insertion

## Task Commits

Each task was committed atomically:

1. **Task 1: Add schedule CRUD endpoints with validation** - `0b25a12` (feat)
2. **Task 2: Add manual trigger endpoint using existing scheduler infrastructure** - `a2efc20` (feat)

## Files Created/Modified
- `functions/scheduler_api/scheduler_endpoints.py` - Added validate_schedule_input(), _sql_value(), CRUD endpoints (POST/PUT/DELETE), and manual trigger endpoint (POST)

## Decisions Made
- Used Option C for manual trigger: await full execution rather than fire-and-forget. Keystone ASP has unlimited timeout, and the scheduler already creates its own log entries internally. Returning after completion gives the dashboard a definitive result.
- Query log_id from `apps_central_scheduling.log_id` column after trigger completes rather than creating a separate log entry — avoids double-logging since `process_scheduled_services_with_overrides` handles all logging internally.
- Created `_sql_value()` helper for consistent Python-to-SQL conversion with proper NULL handling.
- Blocked system-managed fields from API updates using a SYSTEM_MANAGED_FIELDS constant — prevents callers from accidentally corrupting scheduler state.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All 7 scheduler API endpoints complete and registered in function_app.py
- Phase 2 API layer fully implements API-01 through API-05
- Dashboard (Phase 3) can consume these endpoints exclusively — no direct SQL from frontend

---
*Phase: 02-api-layer*
*Completed: 2026-02-27*
