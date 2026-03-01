# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-27)

**Core value:** Every scheduled service execution is visible, recoverable, and controllable — no silent failures, no stuck rows, no mystery.
**Current focus:** Phase 3 — Keystone Dashboard

## Current Position

Phase: 3 of 3 (Keystone Dashboard)
Plan: 1 of 3 in current phase
Status: Executing
Last activity: 2026-03-01 — Plan 03-01 complete (Foundation + Service Overview)

Progress: [████████░░] 71%

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Average duration: 7 min
- Total execution time: 0.6 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Scheduler Reliability | 2/2 | 12 min | 6 min |
| 2. API Layer | 2/2 | 16 min | 8 min |
| 3. Keystone Dashboard | 1/3 | 8 min | 8 min |

**Recent Trend:**
- Last 5 plans: 01-02 (8 min), 02-01 (8 min), 02-02 (8 min), 03-01 (8 min)
- Trend: Stable

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Two-step SELECT + batch UPDATE for watchdog — SQL Executor API compatibility
- Retry logic in both failure and exception handlers — exceptions deserve retry too
- Exponential backoff capped at 120 minutes — prevents excessive delay
- CTE with ROW_NUMBER() for efficient N+1-free health computation
- next_run_time computed in Python per-service (SQL date arithmetic across frequencies is unwieldy)
- Option C for manual trigger — await full execution, Keystone ASP unlimited timeout
- Soft-delete only — no hard DELETE on scheduling table
- Proxy pattern using httpx.AsyncClient for Keystone-to-fx-app-apps-services forwarding
- SWR hooks unwrap {success, data} envelope; 45s auto-refresh
- SubGrid UI component for expandable service rows (EQuIP pattern)

### Pending Todos

- Verify `next_retry_at` and `max_execution_minutes` columns exist in apps_central_scheduling (may need ALTER TABLE)

### Blockers/Concerns

- Two separate codebases: fx-app-apps-services (backend) and keystone-platform (frontend) — Phase 3 requires coordinating work across repos
- Phase 3 dashboard in keystone-platform will consume the 7 API endpoints built in Phase 2

## Session Continuity

Last session: 2026-03-01
Stopped at: Completed 03-01-PLAN.md (Foundation + Service Overview)
Resume file: .planning/phases/03-keystone-dashboard/03-01-SUMMARY.md
