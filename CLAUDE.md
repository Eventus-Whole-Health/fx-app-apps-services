# CLAUDE.md - fx-app-apps-services

This file provides guidance to Claude Code when working with this function app.

## Project Overview

Azure Functions V4 application (Python 3.11) providing core infrastructure services for the Eventus ecosystem with unlimited timeout support.

**Key Innovation:** Simplified scheduler with no timeout tracking - services can run indefinitely without artificial cutoffs.

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

### Deployment

GitHub Actions auto-deploys on push to main:

```bash
# Deploy changes
git add .
git commit -m "Update fx-app-apps-services"
git push origin main

# Monitor deployment in GitHub Actions
```

### SQL Queries

```bash
# Query master services log
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT TOP 20 * FROM jgilpatrick.apps_master_services_log WHERE function_app = 'fx-app-apps-services' ORDER BY started_at DESC"

# Query central scheduling
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT * FROM jgilpatrick.apps_central_scheduling WHERE is_active = 1"
```

## Architecture

### Entry Point

`function_app.py` - Configures Seq logging BEFORE creating FunctionApp, then registers blueprints:
- `scheduler_bp` - Timer and manual trigger for scheduler
- `master_services_log_bp` - Status/result endpoints

### Function Modules

| Module | Purpose | Key Files |
|--------|---------|-----------|
| **scheduler** | Processes scheduled services, no timeout limits | `timer_function.py` (1,307 lines, simplified) |
| **master_services_log** | Status tracking endpoints | `status_endpoints.py` (distributed async tracking) |

### Shared Services (Copied Verbatim from apps_services)

| File | Purpose |
|------|---------|
| `settings.py` | Pydantic-based config with caching |
| `sql_client.py` | SQL Executor API client (client credentials auth) |
| `service_logger.py` | Logs to apps_master_services_log with workflow tracking |
| `seq_logging.py` | Structured logging to Seq with sensitive data sanitization |
| `telemetry.py` | Application Insights integration |

### Timeout Architecture

**Key Difference:** This app runs on Keystone ASP with unlimited timeout, enabling the scheduler to:
- Poll indefinitely until services complete
- Support long-running operations (15+ minutes tested)
- Eliminate timeout-tracking complexity

**Removed from apps_services:**
- `TimeoutTracker` class (entire class deleted)
- `FUNCTION_TIMEOUT_SECONDS` constant
- `MAX_POLLING_TIME` constant
- All timeout checks in polling loop
- Artificial execution cutoffs

**Result:** Scheduler is ~80 lines simpler, polls until completion without time pressure.

### Three-Layer Logging

1. **Seq** - Real-time structured events with tags (ServiceStarted, Completed, Failed)
2. **SQL** - Persistent `jgilpatrick.apps_master_services_log` with request/response payloads
3. **Application Insights** - Azure telemetry (shared resource: `fx-apps-shared`)

## Key Tables

| Table | Purpose | Notes |
|-------|---------|-------|
| `jgilpatrick.apps_master_services_log` | Execution tracking for all services | Tracks parent_id, root_id for workflow context |
| `jgilpatrick.apps_central_scheduling` | Scheduled service definitions | Scheduler reads this to determine what to trigger |

## API Endpoints

### Scheduler

- `POST /api/scheduler/manual-trigger` - Run scheduler immediately
  - Optional params: `force_service_ids: [1, 2, 3]`, `bypass_window_check: bool`
  - Response: `{"status": "triggered", "services_found": 3}`

- **Timer trigger** - Every 15 minutes at :00, :15, :30, :45
  - **Currently disabled** with schedule `"0 0 0 0 * *"` (impossible date - day 0 doesn't exist)
  - Will be enabled in Phase 4 after testing

### Status Endpoints

- `GET /api/status/{log_id}` - Check service status (pending/success/failed)
- `GET /api/result/{log_id}` - Get complete result with request/response data

## Standard Function Pattern

```python
from ..shared.sql_client import SQLClient
from ..shared.service_logger import ServiceLogger

@bp.route(route="example", methods=["POST"])
async def example_function(req: func.HttpRequest) -> func.HttpResponse:
    async with SQLClient() as sql:
        logger = ServiceLogger("example_service", function_app="fx-app-apps-services")
        await logger.log_start(sql, request_data=json.dumps(request_body))

        # Business logic here
        result = await do_work()

        await logger.log_success(sql, response_data=json.dumps(result))
        return func.HttpResponse(json.dumps(result))
```

## Configuration

All secrets stored in Key Vault (`eventus-apps`). Function app references via settings:

- `SQL_EXECUTOR_CLIENT_ID`, `SQL_EXECUTOR_CLIENT_SECRET`, `SQL_EXECUTOR_TENANT_ID`
- `SQL_EXECUTOR_URL`, `SQL_EXECUTOR_SCOPE`, `SQL_EXECUTOR_SERVER`
- `SEQ_SERVER_URL`, `SEQ_API_KEY`
- `APPLICATIONINSIGHTS_CONNECTION_STRING`

## Deployment Target

- **Resource Group:** `rg-keystone-platform` (same as fx-app-pims-services, fx-app-data-services)
- **App Service Plan:** `keystone-platform-asp` (Keystone ASP - unlimited timeout)
- **Storage Account:** `eventusappsbs` (shared)
- **Application Insights:** `app-insights-master` (shared with fx-app-pims-services, fx-app-data-services)
- **Key Vault:** `eventus-apps` (existing, all secrets)

## Changes from apps_services

| What Changed | Why | Impact |
|--------------|-----|--------|
| Removed TimeoutTracker | Unlimited timeout eliminates need for tracking | Code simpler, services can run longer |
| Removed polling time limits | Keystone ASP has no timeout | Polling continues until completion |
| Disabled timer trigger | Safe migration testing | Enable in Phase 4 after verification |
| Increased REQUEST_TIMEOUT | Allow longer service calls | Services get full 10-minute calls instead of 5 |

## Migration Phase

**Current Status:** Phase 2 - New infrastructure deployed, timer disabled for testing

**Timeline:**
- Phase 2 (now): Deploy fx-app-apps-services with timer disabled
- Phase 3: Migrate other fx apps to distributed status endpoints
- Phase 4: Enable timer, decommission apps_services

## Testing Checklist

When deploying changes:

- [ ] Status endpoints return correct data from `apps_master_services_log`
- [ ] Manual scheduler trigger executes without timeout errors
- [ ] Long-running services (15+ min) complete successfully
- [ ] Seq logs show no timeout warnings
- [ ] Timer remains disabled (Day 0 schedule still impossible date)
- [ ] All logging layers working (Seq, SQL, App Insights)

## Debugging

### Check Logs in Seq

```bash
# Filter by app
~/.dotnet/tools/seqcli search --filter="AppName = 'fx-app-apps-services'"

# Check for errors
~/.dotnet/tools/seqcli search --filter="@Level = 'Error' AND AppName = 'fx-app-apps-services'"
```

### Check SQL Logs

```bash
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "SELECT TOP 10 * FROM jgilpatrick.apps_master_services_log WHERE function_app = 'fx-app-apps-services' AND status IN ('failed', 'error') ORDER BY started_at DESC"
```

### Common Issues

| Issue | Likely Cause | Fix |
|-------|--------------|-----|
| "Timeout approaching" in logs | Still references TimeoutTracker | Check timer_function.py was modified correctly |
| Services fail after 10 minutes | Timer trigger still has old timeout | Verify host.json has `"functionTimeout": "-1"` |
| Status endpoint returns 404 | log_id doesn't exist | Verify log_id is in apps_master_services_log |

## Notes

- Shared services files (`shared/`) are copied verbatim from apps_services - no modifications needed
- Timer function (`timer_function.py`) is the main simplification - all timeout logic removed
- Status endpoints are unchanged from apps_services - they work with distributed architecture
- This app serves as foundation for retiring apps_services after all fx apps are migrated
