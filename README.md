# fx-app-apps-services

Azure Functions app providing core infrastructure services for the Eventus ecosystem.

**Runtime:** Python 3.11 | **Plan:** Keystone ASP (unlimited timeout) | **Resource Group:** rg-keystone-platform | **Functions Version:** 4

## Services

- **Centralized Scheduler** (dispatcher) - 15-min timer that evaluates schedules and fires services once per window. Fire-and-forget for async (202) services.
- **Job Manager** - 2-min timer that polls dispatched execution rows against master services log and reconciles terminal state or enforces per-schedule timeout.
- **Scheduler API** - CRUD endpoints for managing scheduled services
- **Master Services Log** - Distributed async tracking for long-running services
- **Trigger** - Trigger any cataloged function app by ID or name
- **OTS Redis Watchdog** - Snapshot, restore, and health monitoring for OTS Redis

## Architecture

### Dispatcher / Job Manager

The scheduler uses a two-timer architecture:

```
Every 15 min (dispatcher)          Every 2 min (job manager)
─────────────────────────          ─────────────────────────
1. Fetch pending schedules     →   1. Fetch dispatched exec rows
2. Atomic claim (processing)       2. Check master services log
3. POST to service                 3. Terminal? → write back
4. 202? → log 'dispatched'         4. Timeout? → mark 408
5. Move on                         5. Still running? → skip
```

No retry logic. Failures wait for the next scheduled occurrence.

### Function Modules

```
functions/
├── shared/                    # Reusable services
│   ├── settings.py            # Pydantic config with caching
│   ├── sql_client.py          # SQL Executor API client
│   ├── service_logger.py      # Service log tracking
│   ├── seq_logging.py         # Structured logging
│   └── telemetry.py           # Application Insights
├── scheduler/
│   └── timer_function.py      # 15-min dispatcher
├── scheduler_jobs/
│   └── job_manager.py         # 2-min job reconciler
├── scheduler_api/
│   └── scheduler_endpoints.py # Schedule CRUD + health
├── master_services_log/
│   └── status_endpoints.py    # Status/result HTTP endpoints
├── trigger_function/
│   └── trigger_function.py    # Trigger cataloged apps
└── ots_redis_watchdog/
    └── watchdog.py            # OTS Redis watchdog
```

### Three-Layer Logging

1. **Seq** - Real-time structured events (`ServiceStarted`/`Completed`/`Failed`)
2. **SQL** - Persistent log in `jgilpatrick.apps_master_services_log`
3. **Application Insights** - Azure telemetry via `app-insights-master`

## Local Development

```bash
# Create centralized virtual environment
python -m venv ~/venv/fx-app-apps-services
source ~/venv/fx-app-apps-services/bin/activate
pip install -r requirements.txt

# Run locally
func start
```

> Timer functions (scheduler, job manager, watchdog) will log listener errors locally — Azurite (Azure Storage emulator) is not running. HTTP functions work normally.

Available locally:
- Scheduler manual trigger: `http://localhost:7071/api/scheduler/manual-trigger`
- Scheduler services: `http://localhost:7071/api/scheduler/services`
- Status endpoint: `http://localhost:7071/api/status/{log_id}`
- Result endpoint: `http://localhost:7071/api/result/{log_id}`

## Deployment

Automatic on push to main via GitHub Actions:

```bash
git commit -m "feat: description"
git push origin main
```

## Database Tables

| Table | Purpose |
|-------|---------|
| `jgilpatrick.apps_master_services_log` | Execution tracking for all services |
| `jgilpatrick.apps_central_scheduling` | Scheduled service definitions (`status`, `max_execution_minutes`) |
| `jgilpatrick.apps_scheduler_execution_log` | Per-dispatch execution log (dispatched → success/failed/timeout) |
| `jgilpatrick.apps_function_apps` | Function app catalog (endpoints, auth, host keys) |

## API Reference

### Scheduler

```
POST /api/scheduler/manual-trigger
{
  "force_service_ids": [1, 2, 3],   // optional
  "bypass_window_check": false       // optional
}
→ {"status": "triggered", "services_found": 3, "dispatched": 2}

GET  /api/scheduler/services
POST /api/scheduler/services
PUT  /api/scheduler/services/{id}
DELETE /api/scheduler/services/{id}
POST /api/scheduler/services/{id}/trigger
GET  /api/scheduler/services/{id}/history
GET  /api/scheduler/health
```

### Status / Result

```
GET /api/status/{log_id}
→ {"status": "success", "started_at": "...", "completed_at": "..."}

GET /api/result/{log_id}
→ Full execution data with request/response payloads
```

### Trigger

```
POST /api/trigger/{function_id}
POST /api/trigger?app={app_name}&function={function_name}
GET  /api/trigger/list
```

## Configuration

Key environment variables (all sourced from Key Vault `eventus-apps`):

| Variable | Purpose |
|----------|---------|
| `SQL_EXECUTOR_CLIENT_ID/SECRET/TENANT_ID` | SQL Executor API auth |
| `SQL_EXECUTOR_URL` | `https://fx-app-data-services.azurewebsites.net/api/sql-executor` |
| `SQL_EXECUTOR_SCOPE` | `api://8b3542fd-41c7-4aec-b14d-d0ee8342e57a/.default` |
| `SEQ_SERVER_URL`, `SEQ_API_KEY` | Seq structured logging |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights telemetry |
| `LOGIC_APP_EMAIL_URL` | Required by settings.py (even if unused) |
| `AZURE_STORAGE_CONNECTION_STRING` | Required by settings.py (even if unused) |

## Support

1. Check Seq logs: `~/.dotnet/tools/seqcli search --filter="AppName = 'fx-app-apps-services'"`
2. Check SQL: `SELECT TOP 10 * FROM jgilpatrick.apps_master_services_log WHERE function_app = 'fx-app-apps-services' ORDER BY started_at DESC`
3. Check GitHub Actions for deploy status

See `CLAUDE.md` for full developer guidelines and debugging runbook.
