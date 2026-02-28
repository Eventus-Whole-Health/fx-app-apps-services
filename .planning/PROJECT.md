# Scheduler Reliability & Management UI

## What This Is

A project to make the Eventus centralized scheduler bulletproof and give it a management interface in Keystone Platform. The scheduler orchestrates 43+ scheduled services across 7 Azure Function apps (ai-scribing, charta, training, data-services, zus, inbox-zero, fx-app-template). Today it suffers from silent failures, skipped schedules, and stuck rows — undermining the centralization value. This project fixes the scheduler's reliability and builds a React dashboard in Keystone for full visibility and control.

## Core Value

Every scheduled service execution is visible, recoverable, and controllable — no silent failures, no stuck rows, no mystery.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- ✓ Centralized schedule definitions in SQL (`apps_central_scheduling`) — existing
- ✓ Timer-based execution every 15 minutes — existing
- ✓ HTTP triggering of remote function apps — existing
- ✓ Async (202) polling for long-running services — existing
- ✓ Three-layer logging (Seq, SQL, App Insights) — existing
- ✓ Manual trigger endpoint — existing
- ✓ Status/result query endpoints — existing
- ✓ Function app trigger service with catalog (`apps_function_apps`) — existing
- ✓ Zero silent failures — every execution outcome captured and queryable — Phase 1
- ✓ Self-healing — stuck/failed services automatically retry with configurable backoff and max retries — Phase 1
- ✓ Proper status lifecycle — rows transition pending -> running -> success/failed/timeout — Phase 1
- ✓ Stuck row detection — watchdog identifies rows running too long, marks them timed out — Phase 1

### Active

<!-- Current scope. Building toward these. -->

- [ ] Keystone dashboard — view all 43+ scheduled services with status, last run, next run, history
- [ ] Schedule CRUD — create, edit, enable/disable, delete schedules from the UI
- [ ] Execution history — drill into any service's past runs with timing, status, error details, request/response
- [ ] Manual trigger from UI — trigger any service on demand from the dashboard
- [ ] Service health indicators — at-a-glance red/yellow/green for each service based on recent execution history

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Email/Slack/push notifications — Dashboard visibility is sufficient; notification channels add complexity without proportional value for the dev team audience
- Multi-tenant scheduling — Single Eventus tenant only; no need for tenant isolation
- DAG/workflow dependencies — Services are independent; no need for Airflow-style dependency chains
- Log aggregation replacement — Seq and App Insights stay as-is; this project adds scheduler-specific visibility, not general log management

## Context

- **Existing codebase:** `fx-app-apps-services` — Azure Functions V4, Python 3.11, deployed to Keystone ASP (unlimited timeout)
- **Keystone Platform:** Full-stack React + FastAPI app where the management UI will live
- **Current state:** 43 scheduled services, 6 currently in `failed` status, most show `pending` even after successful runs (status not updated properly)
- **Users:** Dev team (3-5 developers) who manage and monitor scheduled services
- **Schedule types in use:** daily, weekly, monthly, hourly, once
- **Function apps being orchestrated:** ai-scribing-services, charta-services, training-services, data-services, fx-app-zus-services, inbox-zero, fx-app-template

## Constraints

- **Tech stack**: Backend stays Python/Azure Functions; UI is React + shadcn/ui + Tailwind in Keystone Platform
- **Infrastructure**: No new infrastructure (no Redis, no message brokers) — SQL-backed scheduling pattern stays
- **Database**: All data in Azure SQL via SQL Executor API — no direct DB connections from frontend
- **Auth**: Keystone Platform handles auth; scheduler endpoints may need API key or Azure AD protection
- **Deployment**: GitHub Actions auto-deploy on push to main for both fx-app-apps-services and keystone-platform

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep custom SQL-backed scheduler over Prefect/Temporal/Airflow | Custom UI requirement in Keystone; off-the-shelf tools add infrastructure and give their own dashboard, not ours | Confirmed — Phase 1 |
| Dashboard-only visibility (no email/Slack alerts) | Dev team checks dashboard regularly; notification infrastructure adds complexity without proportional value | — Pending |
| Fix scheduler in-place vs rewrite | Architecture is sound; problems are implementation gaps (error handling, status lifecycle, retry logic) | Confirmed — Phase 1 proved the architecture is sound |
| Two-step SELECT + batch UPDATE for watchdog | SQL Executor API may not support OUTPUT with UPDATE; two-step avoids compatibility issues | Phase 1 |
| Retry logic in both failure and exception handlers | Exceptions (network errors, timeouts) deserve retry just like HTTP failures | Phase 1 |
| Exponential backoff capped at 120 minutes | Prevents excessive delay while giving services meaningful retry windows | Phase 1 |

---
*Last updated: 2026-02-27 after Phase 1*
