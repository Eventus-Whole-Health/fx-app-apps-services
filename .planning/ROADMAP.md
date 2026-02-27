# Roadmap: Scheduler Reliability & Management UI

## Overview

The existing scheduler is fundamentally sound but has implementation gaps that cause silent failures, stuck rows, and unreliable status tracking across 43+ scheduled services. This roadmap fixes the backend first (make the scheduler trustworthy), then exposes a clean API layer, then builds the Keystone dashboard that puts full visibility and control in front of the dev team. Each phase delivers something independently verifiable before the next begins.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Scheduler Reliability** - Fix status lifecycle, error capture, stuck row detection, retry logic, and schedule correctness in fx-app-apps-services
- [ ] **Phase 2: API Layer** - Build scheduler management endpoints that serve accurate, real-time data to the dashboard
- [ ] **Phase 3: Keystone Dashboard** - React UI in Keystone Platform for full visibility and control of all scheduled services

## Phase Details

### Phase 1: Scheduler Reliability
**Goal**: The scheduler correctly tracks every execution outcome — no rows stuck on "pending", no silent failures, failed services retry automatically, and schedules fire reliably across timezone transitions
**Depends on**: Nothing (first phase)
**Requirements**: SCHED-01, SCHED-02, SCHED-03, SCHED-04, SCHED-05
**Success Criteria** (what must be TRUE):
  1. Every service execution row transitions to a terminal state (success, failed, or timeout) — no rows remain "pending" after execution completes
  2. Every failure path (HTTP error, SQL error, polling timeout, unhandled exception) produces a log entry in both `apps_master_services_log` and Seq with error details
  3. A watchdog process identifies rows "running" longer than the configured threshold and marks them timed out
  4. Services with `max_retries > 0` automatically retry on failure with exponential backoff, and retry attempts are visible in the log
  5. Schedules fire correctly in Eastern time including across DST transitions, and daily/weekly/monthly/hourly/once frequency types all evaluate without false triggers or skipped runs
**Plans**: 2 plans

Plans:
- [ ] 01-01-PLAN.md — Status lifecycle and error capture: fix pending->running->terminal transitions in master services log, ensure all failure paths log to SQL+Seq, fix schedule evaluation for all frequency types and DST (SCHED-01, SCHED-02, SCHED-05)
- [ ] 01-02-PLAN.md — Watchdog and retry: configurable per-service stuck row detection, exponential backoff retry with max_retries, bounded polling loop (SCHED-03, SCHED-04)

### Phase 2: API Layer
**Goal**: fx-app-apps-services exposes clean endpoints that return accurate scheduler state, execution history, health summaries, and support CRUD and manual trigger operations
**Depends on**: Phase 1
**Requirements**: API-01, API-02, API-03, API-04, API-05
**Success Criteria** (what must be TRUE):
  1. A single endpoint returns all scheduled services with computed health status (red/yellow/green), last run time, and next run time
  2. An execution history endpoint returns paginated runs for any service, filterable by status and date range, including error details and request/response payloads
  3. CRUD endpoints create, update, and delete schedule definitions with validation (no invalid cron, no orphan service IDs)
  4. A manual trigger endpoint accepts a service ID, triggers that service, and returns a tracking ID that can be polled for completion status
  5. A health summary endpoint returns aggregate counts (total, healthy, degraded, failing) for the dashboard header
**Plans**: TBD

Plans:
- [ ] 02-01: Read endpoints — list schedules with health, execution history, health summary
- [ ] 02-02: Write endpoints — schedule CRUD and manual trigger with validation

### Phase 3: Keystone Dashboard
**Goal**: Developers can see every scheduled service's status, history, and health at a glance, and can create, edit, trigger, or disable any service from the UI without touching SQL directly
**Depends on**: Phase 2
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, DASH-05, DASH-06
**Success Criteria** (what must be TRUE):
  1. The dashboard lists all 43+ scheduled services with name, function app, frequency, last run time, next run time, and current status visible without scrolling configuration
  2. Each service shows a red/yellow/green health indicator based on recent execution history — a developer can identify failing services at a glance
  3. Clicking any service opens an execution history panel showing past runs with timing, status, error details, and request/response payloads
  4. A developer can create a new schedule, edit an existing one, enable/disable it, or delete it entirely from the UI without writing SQL
  5. A developer can trigger any service on demand from the dashboard and see the execution status update in real time
  6. The service list can be filtered by function app, status, and frequency, and sorted by last run time
**Plans**: TBD

Plans:
- [ ] 03-01: Service overview and health indicators — list view, health badges, filtering and sorting
- [ ] 03-02: Execution history and detail panels — drill-down view with full run history
- [ ] 03-03: Schedule CRUD and manual trigger — create/edit forms, enable/disable toggle, trigger button with live status

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Scheduler Reliability | 0/2 | Not started | - |
| 2. API Layer | 0/2 | Not started | - |
| 3. Keystone Dashboard | 0/3 | Not started | - |
