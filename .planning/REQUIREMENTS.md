# Requirements: Scheduler Reliability & Management UI

**Defined:** 2026-02-27
**Core Value:** Every scheduled service execution is visible, recoverable, and controllable — no silent failures, no stuck rows, no mystery.

## v1 Requirements

### Scheduler Reliability

- [x] **SCHED-01**: Status lifecycle — rows transition pending -> running -> success/failed/timeout (never stuck on "pending" after execution)
- [x] **SCHED-02**: Error capture — every failure path (HTTP error, timeout, SQL failure, polling failure) is logged with error details to both SQL and Seq
- [x] **SCHED-03**: Stuck row detection — watchdog identifies rows "running" longer than configurable threshold and marks them timed out
- [x] **SCHED-04**: Configurable retry — failed services automatically retry with exponential backoff, respecting max_retries per service
- [x] **SCHED-05**: Schedule evaluation correctness — timezone-aware (Eastern) execution windows, frequency logic, and trigger limits all work reliably across DST transitions

### Keystone Dashboard

- [ ] **DASH-01**: Service overview — list all scheduled services with name, function app, frequency, status, last run, next run
- [ ] **DASH-02**: Health indicators — at-a-glance red/yellow/green per service based on recent execution history
- [ ] **DASH-03**: Execution history — drill into any service's past runs with timing, status, error details, request/response payloads
- [ ] **DASH-04**: Schedule CRUD — create, edit, enable/disable, delete schedules from the UI
- [ ] **DASH-05**: Manual trigger — trigger any service on demand from the dashboard with real-time status feedback
- [ ] **DASH-06**: Filtering and sorting — by function app, status, frequency, last run time

### API Layer

- [x] **API-01**: List schedules endpoint — returns all schedules with computed health status and next run time
- [x] **API-02**: Execution history endpoint — paginated history for a specific service with filter by status/date range
- [x] **API-03**: Schedule CRUD endpoints — create, update, delete schedule definitions with validation
- [x] **API-04**: Manual trigger endpoint — trigger a specific service and return execution tracking ID
- [x] **API-05**: Health summary endpoint — aggregate health across all services for dashboard header stats

## v2 Requirements

### Notifications

- **NOTF-01**: Email alerts for critical failures (services failing N times in a row)
- **NOTF-02**: Configurable alert thresholds per service

### Advanced Features

- **ADV-01**: Execution timeline visualization (Gantt-style view of overlapping service runs)
- **ADV-02**: Dependency chains between services (run B after A completes)
- **ADV-03**: Schedule templates (pre-built configs for common patterns)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Email/Slack/push notifications | Dashboard visibility sufficient for dev team; defer to v2 |
| Multi-tenant scheduling | Single Eventus tenant only |
| DAG/workflow dependencies | Services are independent; not needed for v1 |
| Log aggregation replacement | Seq and App Insights stay as-is |
| New infrastructure (Redis, message brokers) | SQL-backed pattern is sufficient; no new infra |
| Direct database connections from frontend | All data access through SQL Executor API |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SCHED-01 | Phase 1 | Complete |
| SCHED-02 | Phase 1 | Complete |
| SCHED-03 | Phase 1 | Complete |
| SCHED-04 | Phase 1 | Complete |
| SCHED-05 | Phase 1 | Complete |
| DASH-01 | Phase 3 | Pending |
| DASH-02 | Phase 3 | Pending |
| DASH-03 | Phase 3 | Pending |
| DASH-04 | Phase 3 | Pending |
| DASH-05 | Phase 3 | Pending |
| DASH-06 | Phase 3 | Pending |
| API-01 | Phase 2 | Complete |
| API-02 | Phase 2 | Complete |
| API-03 | Phase 2 | Complete |
| API-04 | Phase 2 | Complete |
| API-05 | Phase 2 | Complete |

**Coverage:**
- v1 requirements: 16 total
- Mapped to phases: 16
- Unmapped: 0

---
*Requirements defined: 2026-02-27*
*Last updated: 2026-02-27 after Phase 2 completion — 10/16 requirements complete*
