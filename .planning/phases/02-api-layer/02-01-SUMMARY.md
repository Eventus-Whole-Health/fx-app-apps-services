---
phase: 02-api-layer
plan: 01
subsystem: api
tags: [azure-functions, blueprint, sql, health-computation, pagination]

requires:
  - phase: 01-scheduler-reliability
    provides: Status lifecycle, retry columns (retry_count, max_retries, next_retry_at), watchdog (max_execution_minutes)
provides:
  - Scheduler API Blueprint module (functions/scheduler_api/)
  - GET /api/scheduler/services — list all active schedules with computed health status and next run time
  - GET /api/scheduler/health — aggregate health summary (total, healthy, degraded, failing)
  - GET /api/scheduler/services/{id}/history — paginated execution history with status/date filtering
  - compute_health_status() helper — reusable health computation from log failure counts
  - compute_next_run_time() helper — reusable next-run calculation for all frequency types
  - sanitize_sql_string() helper — SQL injection protection for user inputs
affects: [02-api-layer]

tech-stack:
  added: []
  patterns:
    - CTE with ROW_NUMBER() + LEFT JOIN for efficient N+1-free health computation
    - Offset-based pagination with OFFSET/FETCH NEXT and separate COUNT query
    - Consistent JSON envelope {success: true, data: {...}} on all endpoints

key-files:
  created:
    - functions/scheduler_api/__init__.py
    - functions/scheduler_api/scheduler_endpoints.py
  modified:
    - function_app.py

key-decisions:
  - "Health computed in Python after single CTE query — avoids complex SQL CASE logic and allows stuck-service check with Eastern time"
  - "next_run_time computed in Python per-service — SQL date arithmetic across frequency types would be unwieldy"
  - "Offset-based pagination (not cursor-based) — simpler, adequate for expected data volumes"
  - "Blueprint registered alongside existing ones in function_app.py — follows established pattern"

patterns-established:
  - "Scheduler API endpoints use auth_level=ANONYMOUS (Azure infrastructure handles auth)"
  - "All read endpoints return {success: true, data: {...}} with appropriate HTTP status codes"
  - "Health status: healthy (0 failures in last 5), degraded (1-2), failing (3+, or stuck)"
  - "History pagination: page/page_size query params, OFFSET/FETCH NEXT, total_pages in response"

requirements-completed:
  - API-01
  - API-02
  - API-05

duration: 8min
completed: 2026-02-27
---

# Plan 02-01: Read Endpoints Summary

**Three read-only scheduler API endpoints: service list with CTE-computed health status, paginated execution history with filtering, and aggregate health summary**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-27
- **Completed:** 2026-02-27
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Created `functions/scheduler_api/` module with Blueprint following existing pattern
- List endpoint uses CTE + LEFT JOIN to efficiently compute per-service health from last 5 log entries
- Health summary endpoint reuses same CTE pattern for aggregate counts
- Execution history endpoint supports pagination (OFFSET/FETCH NEXT) and filtering by status/date range
- Next run time computed in Python for all 5 frequency types (hourly, daily, weekly, monthly, once)
- Blueprint registered in function_app.py alongside existing blueprints

## Task Commits

Each task was committed atomically:

1. **Task 1: Create scheduler API module with list schedules and health summary endpoints** - `8b82c53` (feat)
2. **Task 2: Add execution history endpoint and register blueprint** - `4832bc9` (feat)

## Files Created/Modified
- `functions/scheduler_api/__init__.py` - Package init (empty, follows existing pattern)
- `functions/scheduler_api/scheduler_endpoints.py` - Three GET endpoints with health computation, next-run-time calculation, and paginated history
- `function_app.py` - Added scheduler_api_bp import and registration

## Decisions Made
- Used a single CTE query with ROW_NUMBER() to get per-service failure counts from last 5 log entries, avoiding N+1 queries
- Computed health_status and next_run_time in Python rather than SQL — simpler to handle stuck-service detection with timezone logic and frequency parsing
- Used offset-based pagination (simpler, adequate for expected volumes) over cursor-based
- Imported pytz for Eastern timezone consistency with Phase 1 scheduler code

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All three read endpoints ready for Plan 02-02 write endpoints
- Blueprint module established — Plan 02-02 adds CRUD and trigger endpoints to same file
- Health computation helpers reusable by future endpoints

---
*Phase: 02-api-layer*
*Completed: 2026-02-27*
