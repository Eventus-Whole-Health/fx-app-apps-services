# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Azure Functions V4 application (Python 3.11) providing core infrastructure services for the Eventus ecosystem.

**Key Design:** Dispatcher/job-manager architecture — scheduler fires services and moves on, a separate 2-minute timer reconciles async jobs to terminal state.

## Commands

### Local Development

```bash
# Create centralized virtual environment
python -m venv ~/venv/fx-app-apps-services
source ~/venv/fx-app-apps-services/bin/activate

# Install dependencies and run locally
cd fx-app-apps-services
pip install -r requirements.txt
func start
```

Note: timer triggers (scheduler, job manager, watchdog) will fail to start locally without Azurite running — this is expected and does not affect HTTP function testing.

### Deployment

GitHub Actions auto-deploys on push to main:

```bash
git add .
git commit -m "feat: description"
git push origin main
```

### SQL Queries

```bash
# Query master services log
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT TOP 20 * FROM jgilpatrick.apps_master_services_log WHERE function_app = 'fx-app-apps-services' ORDER BY started_at DESC"

# Query active schedules
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT id, function_app, service, status, last_triggered_at FROM jgilpatrick.apps_central_scheduling WHERE is_active = 1"

# Query execution log (recent)
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT TOP 20 * FROM jgilpatrick.apps_scheduler_execution_log ORDER BY execution_id DESC"
```

## Architecture

### Entry Point

`function_app.py` - Configures Seq logging BEFORE creating FunctionApp, then registers blueprints:
- `scheduler_bp` - 15-min timer dispatcher + manual HTTP trigger
- `master_services_log_bp` - Status/result endpoints
- `trigger_bp` - Trigger any cataloged function app
- `scheduler_api_bp` - CRUD endpoints for schedule management
- `job_manager_bp` - 2-min timer to poll dispatched jobs to terminal state
- `ots_redis_watchdog_bp` - OTS Redis snapshot/restore/watchdog

### Function Modules

| Module | Purpose | Key Files |
|--------|---------|-----------|
| **scheduler** | Pure dispatcher: evaluate schedule, fire once, move on | `timer_function.py` |
| **scheduler_jobs** | Job manager: polls dispatched rows against master log | `job_manager.py` |
| **master_services_log** | Status/result HTTP endpoints | `status_endpoints.py` |
| **trigger_function** | Trigger any cataloged function app by ID or name | `trigger_function.py` |
| **scheduler_api** | CRUD for `apps_central_scheduling` (list, create, update, delete, trigger) | `scheduler_endpoints.py` |
| **ots_redis_watchdog** | OTS Redis snapshot, restore, and health watchdog | `watchdog.py` |

### Shared Services

| File | Purpose |
|------|---------|
| `settings.py` | Pydantic-based config with caching |
| `sql_client.py` | SQL Executor API client (client credentials auth) |
| `service_logger.py` | Logs to apps_master_services_log with workflow tracking |
| `seq_logging.py` | Structured logging to Seq with sensitive data sanitization |
| `telemetry.py` | Application Insights integration |

### Dispatcher / Job Manager Architecture

**Dispatcher** (`scheduler/timer_function.py`, every 15 min):
1. Fetch `pending` rows from `apps_central_scheduling` where schedule is due
2. Atomic claim via `UPDATE SET status='processing'` + verify
3. POST to service endpoint; if 202 Accepted → write `dispatched` execution log row and return immediately
4. If non-202 → mark failed, reset central row to `pending`

**Job Manager** (`scheduler_jobs/job_manager.py`, every 2 min):
1. JOIN `apps_scheduler_execution_log` (status='dispatched') with `apps_master_services_log`
2. If master log shows terminal state → write back to execution log + update central receipt columns
3. If elapsed > `max_execution_minutes` → mark timeout (408)
4. Otherwise skip (check again next tick)

**Key properties:**
- No retry logic — failures wait for next scheduled occurrence
- No inline polling — dispatcher is fire-and-forget for async services
- `max_execution_minutes` on each schedule row controls job manager timeout (default 30)

### Three-Layer Logging

1. **Seq** - Real-time structured events with tags (ServiceStarted, Completed, Failed)
2. **SQL** - Persistent `jgilpatrick.apps_master_services_log` with request/response payloads
3. **Application Insights** - Azure telemetry (shared resource: `app-insights-master`)

## Key Tables

| Table | Purpose | Notes |
|-------|---------|-------|
| `jgilpatrick.apps_master_services_log` | Execution tracking for all services | Tracks parent_id, root_id for workflow context |
| `jgilpatrick.apps_central_scheduling` | Scheduled service definitions | `status`, `last_triggered_at`, `max_execution_minutes` |
| `jgilpatrick.apps_scheduler_execution_log` | Per-execution log with status lifecycle | dispatched → success/failed/timeout |
| `jgilpatrick.apps_function_apps` | Function app catalog | Trigger module looks up endpoints, auth method, host keys |

## API Endpoints

### Scheduler

- `POST /api/scheduler/manual-trigger` - Run scheduler immediately
  - Optional params: `force_service_ids: [1, 2, 3]`, `bypass_window_check: bool`
  - Response: `{"status": "triggered", "services_found": 3, "dispatched": 2}`

- **Timer trigger** - Every 15 minutes at :00, :15, :30, :45
  - Schedule: `"0 0,15,30,45 * * * *"` — ACTIVE in production

- **Job manager timer** - Every 2 minutes
  - Schedule: `"0 */2 * * * *"` — ACTIVE in production

### Scheduler API (CRUD)

- `GET /api/scheduler/services` - List all schedules
- `POST /api/scheduler/services` - Create a new schedule
- `PUT /api/scheduler/services/{id}` - Update a schedule
- `DELETE /api/scheduler/services/{id}` - Delete a schedule
- `POST /api/scheduler/services/{id}/trigger` - Manually trigger a specific service
- `GET /api/scheduler/services/{id}/history` - Execution history for a service
- `GET /api/scheduler/health` - Scheduler health summary

### Status Endpoints

- `GET /api/status/{log_id}` - Check service status (pending/success/failed)
- `GET /api/result/{log_id}` - Get complete result with request/response data

### Trigger Endpoints

- `POST /api/trigger/{id}` - Trigger a cataloged function by its `apps_function_apps` ID
- `POST /api/trigger?app={app_name}&function={function_name}` - Trigger by app/function name
- `GET /api/trigger/list` - List all cataloged functions

## Standard Function Pattern

```python
from ..shared.sql_client import SQLClient
from ..shared.service_logger import ServiceLogger

@bp.route(route="example", methods=["POST"])
async def example_function(req: func.HttpRequest) -> func.HttpResponse:
    async with SQLClient() as sql:
        logger = ServiceLogger("example_service", function_app="fx-app-apps-services")
        await logger.log_start(sql, request_data=json.dumps(request_body))

        result = await do_work()

        await logger.log_success(sql, response_data=json.dumps(result))
        return func.HttpResponse(json.dumps(result))
```

## Configuration

All secrets stored in Key Vault (`eventus-apps`). Function app references via settings:

- `SQL_EXECUTOR_CLIENT_ID` → KV secret `executor-client-id`
- `SQL_EXECUTOR_CLIENT_SECRET` → KV secret `executor-client-secret`
- `SQL_EXECUTOR_TENANT_ID` → KV secret `azure-tenant-id`
- `SQL_EXECUTOR_URL` = `https://fx-app-data-services.azurewebsites.net/api/sql-executor`
- `SQL_EXECUTOR_SCOPE` = `api://8b3542fd-41c7-4aec-b14d-d0ee8342e57a/.default`
- `SQL_EXECUTOR_SERVER` = `apps`
- `LOGIC_APP_EMAIL_URL` → Logic App webhook URL (required by settings.py even if not used)
- `AZURE_STORAGE_CONNECTION_STRING` → fxappsstorage connection string (required by settings.py)
- `SEQ_SERVER_URL`, `SEQ_API_KEY` → KV secret `seq-api-key`
- `APPLICATIONINSIGHTS_CONNECTION_STRING` → KV secret `app-insights-connection-string`

**NOTE:** `LOGIC_APP_EMAIL_URL` and `AZURE_STORAGE_CONNECTION_STRING` are required by `settings.py` even though the scheduler doesn't use them. Missing values cause a Pydantic `ValidationError` at startup.

## Deployment Target

- **Resource Group:** `rg-keystone-platform`
- **App Service Plan:** `keystone-platform-asp` (Keystone ASP - unlimited timeout)
- **Storage Account:** `eventusappsbs` (shared)
- **Application Insights:** `app-insights-master` (shared with fx-app-pims-services, fx-app-data-services)
- **Key Vault:** `eventus-apps`

## Schema Migrations

Migration SQL lives in `migrations/`. Run against Apps DB before or alongside deploys.

| File | Purpose | Status |
|------|---------|--------|
| `001_scheduler_simplification_additive.sql` | Backfill `max_execution_minutes`, add DEFAULT 30 + NOT NULL, clear stuck retry state | Deployed 2026-05-01 |

## Changelog

| Date | Change | Impact |
|------|--------|--------|
| 2026-05-01 | Dispatcher/job-manager split, retry logic removed (fixes #5) | Stops duplicate service firing caused by stale next_retry_at |
| 2026-02-23 | Timer enabled, end-to-end validation passed | Scheduler active in production |
| Initial | Removed TimeoutTracker, unlimited polling on Keystone ASP | Replaced apps_services scheduler |

## Testing Checklist

When deploying changes:

- [ ] `func start` loads all 20 functions without import errors
- [ ] Status endpoints return correct data from `apps_master_services_log`
- [ ] Manual scheduler trigger fires without errors
- [ ] Seq logs show no unexpected errors
- [ ] Timer fires every 15 minutes (check execution log after tick)
- [ ] Job manager reconciles dispatched rows (check after 2-min tick)

## Debugging

### Check Logs in Seq

```bash
~/.dotnet/tools/seqcli search --filter="AppName = 'fx-app-apps-services'"
~/.dotnet/tools/seqcli search --filter="@Level = 'Error' AND AppName = 'fx-app-apps-services'"
```

### Check SQL Logs

```bash
# Recent master services log errors
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT TOP 10 * FROM jgilpatrick.apps_master_services_log WHERE function_app = 'fx-app-apps-services' AND status IN ('failed', 'error') ORDER BY started_at DESC"

# Stuck dispatched jobs
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT * FROM jgilpatrick.apps_scheduler_execution_log WHERE status = 'dispatched' ORDER BY triggered_at"
```

### Common Issues

| Issue | Likely Cause | Fix |
|-------|--------------|-----|
| Timer listeners fail locally | Azurite not running (port 10000) | Expected — timers need Azure Storage emulator locally |
| Status endpoint returns 404 | log_id doesn't exist | Verify log_id is in apps_master_services_log |
| HTTP 500 empty body on any endpoint | Missing `LOGIC_APP_EMAIL_URL` or `AZURE_STORAGE_CONNECTION_STRING` | Verify both settings in Azure portal |
| `AADSTS500011` error | Wrong `SQL_EXECUTOR_SCOPE` — must use UUID form | Use `api://8b3542fd-41c7-4aec-b14d-d0ee8342e57a/.default` |
| SQL auth fails silently | Wrong KV secret names | Use `executor-client-id`, `executor-client-secret`, `azure-tenant-id` |
| Service fires repeatedly | Stale `next_retry_at` or `status='failed'` in central scheduling | Run migration Step 4 to clear stuck state |
| Job manager not reconciling | `log_id` NULL in execution log | Service didn't return a log_id — check service response |
