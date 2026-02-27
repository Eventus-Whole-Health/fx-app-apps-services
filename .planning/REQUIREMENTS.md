# Requirements: Scheduler Reliability & Management UI

**Defined:** 2026-02-27
**Core Value:** Every scheduled service execution is visible, recoverable, and controllable — no silent failures, no stuck rows, no mystery.

## v1 Requirements

### Scheduler Reliability

- [ ] **SCHED-01**: Status lifecycle — rows transition pending → running → success/failed/timeout (never stuck on "pending" after execution)
- [ ] **SCHED-02**: Error capture — every failure path (HTTP error, timeout, SQL failure, polling failure) is logged with error details to both SQL and Seq
- [ ] **SCHED-03**: Stuck row detection — watchdog identifies rows "running" longer than configurable threshold and marks them timed out
- [ ] **SCHED-04**: Configurable retry — failed services automatically retry with exponential backoff, respecting max_retries per service
- [ ] **SCHED-05**: Schedule evaluation correctness — timezone-aware (Eastern) execution windows, frequency logic, and trigger limits all work reliably across DST transitions

### Keystone Dashboard

- [ ] **DASH-01**: Service overview — list all scheduled services with name, function app, frequency, status, last run, next run
- [ ] **DASH-02**: Health indicators — at-a-glance red/yellow/green per service based on recent execution history
- [ ] **DASH-03**: Execution history — drill into any service's past runs with timing, status, error details, request/response payloads
- [ ] **DASH-04**: Schedule CRUD — create, edit, enable/disable, delete schedules from the UI
- [ ] **DASH-05**: Manual trigger — trigger any service on demand from the dashboard with real-time status feedback
- [ ] **DASH-06**: Filtering and sorting — by function app, status, frequency, last run time

### API Layer

- [ ] **API-01**: List schedules endpoint — returns all schedules with computed health status and next run time
- [ ] **API-02**: Execution history endpoint — paginated history for a specific service with filter by status/date range
- [ ] **API-03**: Schedule CRUD endpoints — create, update, delete schedule definitions with validation
- [ ] **API-04**: Manual trigger endpoint — trigger a specific service and return execution tracking ID
- [ ] **API-05**: Health summary endpoint — aggregate health across all services for dashboard header stats

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
| SCHED-01 | — | Pending |
| SCHED-02 | — | Pending |
| SCHED-03 | — | Pending |
| SCHED-04 | — | Pending |
| SCHED-05 | — | Pending |
| DASH-01 | — | Pending |
| DASH-02 | — | Pending |
| DASH-03 | — | Pending |
| DASH-04 | — | Pending |
| DASH-05 | — | Pending |
| DASH-06 | — | Pending |
| API-01 | — | Pending |
| API-02 | — | Pending |
| API-03 | — | Pending |
| API-04 | — | Pending |
| API-05 | — | Pending |

**Coverage:**
- v1 requirements: 16 total
- Mapped to phases: 0
- Unmapped: 16 ⚠️

---
*Requirements defined: 2026-02-27*
*Last updated: 2026-02-27 after initial definition*
