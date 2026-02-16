# fx-app-apps-services

Azure Functions app providing core infrastructure services for the Eventus ecosystem.

**Runtime:** Python 3.11 | **Plan:** Keystone ASP (unlimited timeout) | **Resource Group:** rg-keystone-platform | **Functions Version:** 4

## Services

- **Centralized Scheduler** - Timer trigger (every 15 minutes) to process scheduled services from `apps_central_scheduling` table
  - Initially disabled (schedule set to impossible date for safe migration testing)
  - Manual trigger via HTTP endpoint: `POST /api/scheduler/manual-trigger`
  - Supports `force_service_ids` and `bypass_window_check` parameters

- **Master Services Log Status Endpoints** - Distributed async tracking for long-running services
  - `GET /api/status/{log_id}` - Service status (pending/success/failed)
  - `GET /api/result/{log_id}` - Complete execution data with request/response payloads

## Architecture

### Key Improvements Over apps_services

| Aspect | apps_services | fx-app-apps-services |
|--------|---------------|----------------------|
| **Plan** | Flex Consumption (10-min timeout) | Keystone ASP (unlimited) |
| **Scheduler Timeout** | 9-minute limit with polling cutoff | Unlimited - polls until completion |
| **Timeout Tracking** | TimeoutTracker class managing limits | Removed - no timeout constraints |
| **Long-Running Services** | Max 8 minutes with artificial cutoffs | Fully supported (15+ minutes tested) |
| **Code Complexity** | Timeout checks throughout | Simplified, focused logic |

### Function Modules

```
functions/
├── shared/              # Reusable services (unchanged from apps_services)
│   ├── settings.py      # Pydantic config with caching
│   ├── sql_client.py    # SQL Executor API client
│   ├── service_logger.py  # Service log tracking
│   ├── seq_logging.py   # Structured logging
│   └── telemetry.py     # Application Insights
├── scheduler/
│   └── timer_function.py  # Scheduler (simplified - no TimeoutTracker)
└── master_services_log/
    └── status_endpoints.py # Status/result HTTP endpoints
```

### Three-Layer Logging

1. **Seq** - Real-time structured events (`ServiceStarted`/`Completed`/`Failed`)
2. **SQL** - Persistent log in `jgilpatrick.apps_master_services_log`
3. **Application Insights** - Azure telemetry via `app-insights-master` (shared with fx-app-pims-services, fx-app-data-services)

## Local Development

### Setup

```bash
# Create centralized virtual environment
python -m venv ~/venv/fx-app-apps-services
source ~/venv/fx-app-apps-services/bin/activate

# Install dependencies
cd fx-app-apps-services
pip install -r requirements.txt
```

### Running Locally

```bash
func start
```

Functions will be available at:
- Scheduler manual trigger: `http://localhost:7071/api/scheduler/manual-trigger`
- Status endpoint: `http://localhost:7071/api/status/{log_id}`
- Result endpoint: `http://localhost:7071/api/result/{log_id}`

### Testing

```bash
# Test status endpoints (use existing log_id from apps_master_services_log)
curl http://localhost:7071/api/status/12345

# Test manual scheduler trigger
curl -X POST http://localhost:7071/api/scheduler/manual-trigger \
  -H "Content-Type: application/json" \
  -d '{"force_service_ids": [1]}'
```

## Deployment

Automatic deployment on push to main via GitHub Actions:

```bash
git add .
git commit -m "Deploy fx-app-apps-services updates"
git push origin main
```

Check deployment status in GitHub Actions (`.github/workflows/main_fx-app-apps-services.yml`).

## Configuration

All environment variables are configured in Azure via app settings. Key references:
- `SQL_EXECUTOR_CLIENT_ID`, `SQL_EXECUTOR_CLIENT_SECRET`, `SQL_EXECUTOR_TENANT_ID` - SQL Executor API credentials
- `SQL_EXECUTOR_URL`, `SQL_EXECUTOR_SCOPE`, `SQL_EXECUTOR_SERVER` - SQL Executor API endpoints
- `SEQ_SERVER_URL` - Seq structured logging endpoint
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - Application Insights connection

## Database Tables

| Table | Purpose |
|-------|---------|
| `jgilpatrick.apps_master_services_log` | Execution tracking for all services |
| `jgilpatrick.apps_central_scheduling` | Scheduled service definitions |

## API Reference

### Scheduler Manual Trigger

```bash
POST /api/scheduler/manual-trigger
Content-Type: application/json

{
  "force_service_ids": [1, 2, 3],  # Optional: force specific services
  "bypass_window_check": false     # Optional: bypass scheduling window
}
```

Response: `{"status": "triggered", "services_found": 3}`

### Status Endpoint

```bash
GET /api/status/{log_id}
```

Response:
```json
{
  "status": "success",  // or "pending", "failed"
  "started_at": "2024-02-15T18:30:00Z",
  "completed_at": "2024-02-15T18:45:00Z"
}
```

### Result Endpoint

```bash
GET /api/result/{log_id}
```

Response: Complete execution data with request/response payloads

## Migration Notes

**Phase of migration:** This is the core infrastructure app for the transition to unlimited timeout architecture.

**Timer status:** Currently disabled (schedule: `0 0 0 0 * *` - impossible date for safe testing). Will be enabled after verification.

**Next phases:**
1. Migrate all fx apps to distributed status endpoints
2. Enable timer trigger in this app
3. Decommission apps_services after monitoring period

## Support

For issues or questions:
1. Check logs in Seq: filter by `AppName = 'fx-app-apps-services'`
2. Check SQL logs: query `jgilpatrick.apps_master_services_log`
3. Check Application Insights for telemetry

See `CLAUDE.md` for developer guidelines.
