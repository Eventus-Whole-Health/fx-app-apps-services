# Phase 2: API Layer - Context

**Gathered:** 2026-02-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Build scheduler management API endpoints in fx-app-apps-services that serve accurate, real-time data to the Keystone dashboard. Five endpoint groups: list schedules with health, execution history, schedule CRUD, manual trigger, and health summary. The dashboard (Phase 3) will consume these endpoints exclusively -- no direct SQL from the frontend.

</domain>

<decisions>
## Implementation Decisions

### Response format and data shape
- All endpoints return JSON with consistent envelope: `{"success": true, "data": {...}}` for success, `{"error": "message", "detail": "..."}` for errors
- Follow existing patterns in status_endpoints.py and trigger_function.py (azure.functions Blueprint, async with SQLClient, ServiceLogger)
- Dates serialized as ISO 8601 strings
- Health status computed server-side as "healthy" (last 5 runs all success), "degraded" (1-2 failures in last 5), "failing" (3+ failures in last 5 or currently stuck)
- "Next run time" computed from frequency + schedule_config + last_triggered_at for each service
- List endpoint returns flat array of services -- dashboard handles grouping/filtering client-side

### Authentication and authorization
- Endpoints use `auth_level=func.AuthLevel.ANONYMOUS` (same as existing status endpoints) since the function app itself is protected by Azure infrastructure
- No per-user authorization -- all authenticated users can read and write schedules
- Manual trigger uses the existing scheduler's `process_scheduled_services_with_overrides(force_service_ids=[id], bypass_window_check=True)` pattern

### CRUD validation
- Create/update validates: frequency must be one of (once, daily, weekly, monthly, hourly), trigger_url must be non-empty, function_app must be non-empty
- Schedule_config validated as parseable JSON when provided
- Delete is soft-delete (set is_active=0), not hard delete
- Cannot create a schedule with an ID that references a non-existent function app in apps_function_apps -- BUT this is a loose coupling (services can have arbitrary trigger_urls), so validation is optional/warning only

### Manual trigger behavior
- Fire-and-forget: endpoint returns immediately with `{"success": true, "log_id": <id>, "status_url": "/api/status/<log_id>"}` after marking the service as processing
- Dashboard polls `/api/status/{log_id}` for completion (existing endpoint)
- Uses the existing scheduler infrastructure (marks processing, triggers HTTP, polls completion) -- not a new code path

### Execution history
- Query `apps_master_services_log` filtered by service_name and function_app
- Paginated with `page` and `page_size` query parameters (default page_size=20, max 100)
- Filterable by status and date range (start_date, end_date query params)
- Returns newest first (ORDER BY started_at DESC)
- Includes error_message and request/response payloads in detail

### Health summary
- Single endpoint returning aggregate counts: total active services, healthy count, degraded count, failing count
- Computed from the same health logic as the list endpoint
- Lightweight query -- dashboard header calls this on load and on interval

### Claude's Discretion
- Exact SQL query structure and JOIN patterns
- Whether to create a new Blueprint module or extend existing ones
- Error message wording and HTTP status code choices beyond the standard patterns
- Whether to compute next_run_time in SQL or Python
- Pagination implementation details (offset-based vs cursor-based)

</decisions>

<specifics>
## Specific Ideas

- The existing `/api/scheduler/manual-trigger` endpoint already supports `force_service_ids` and `bypass_window_check` -- the new manual trigger endpoint for the dashboard should reuse this logic rather than duplicating it
- Health computation should be resilient to services that have never run (treat as "healthy" since they haven't failed)
- The list endpoint should include `retry_count`, `max_retries`, `next_retry_at` from Phase 1 so the dashboard can show retry state
- All new endpoints should follow the existing Blueprint pattern used in `status_endpoints.py` and `trigger_function.py`

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 02-api-layer*
*Context gathered: 2026-02-27*
