# External Integrations

**Analysis Date:** 2026-02-27

## APIs & External Services

**SQL Executor Service:**
- Service: `fx-app-data-services` (Azure Function App)
- Endpoint: `https://fx-app-data-services.azurewebsites.net/api/sql-executor`
- What it's used for: Execute SQL queries against Azure SQL databases (Apps and Analytics servers)
  - SDK/Client: Custom async `SQLClient` class in `functions/shared/sql_client.py`
  - Auth: Azure AD client credentials (ClientSecretCredential)
  - Scope: `api://8b3542fd-41c7-4aec-b14d-d0ee8342e57a/.default`
  - Timeout: 60 seconds default, configurable per call

**Downstream Service Triggers:**
- Pattern: Services are triggered via HTTP POST to target function endpoints
- Used by: Scheduler (`functions/scheduler/timer_function.py`) polls status endpoints
- Response handling: 202 Accepted returns status URL for polling async operations
- Polling interval: 30 seconds between status checks

**Optional Email Service:**
- Service: Logic App webhook (legacy) or future EMAIL_API
- Endpoint: `LOGIC_APP_EMAIL_URL` environment variable
- What it's used for: Email delivery (not used by scheduler, required for settings validation)
- SDK/Client: httpx async client
- Auth: Not specified in scheduler code

## Data Storage

**Databases:**
- **Apps** (Azure SQL Database)
  - Connection: Via SQL Executor service, configured by `SQL_EXECUTOR_SERVER = "apps"`
  - Client: `SQLClient` wrapper around SQL Executor HTTP API
  - Tables accessed:
    - `jgilpatrick.apps_master_services_log` - All service executions logged here
    - `jgilpatrick.apps_central_scheduling` - Scheduled service definitions
  - Cold start retry: 3 attempts with 5-second delays for SQL server cold starts

- **Analytics** (Azure Synapse)
  - Connection: Via SQL Executor service, configurable per query
  - Tables: Accessible but not used by scheduler directly

**File Storage:**
- **Azure Blob Storage**
  - Connection: `AZURE_STORAGE_CONNECTION_STRING` (eventusappsbs account)
  - Container: `app-data` (default, configurable via `AZURE_STORAGE_BLOB_CONTAINER`)
  - Client: `azure-storage-blob` SDK
  - Purpose: App data storage (required by settings, not actively used by scheduler)

**Caching:**
- None - Settings cached in-process via `@functools.lru_cache` only

## Authentication & Identity

**Auth Provider:**
- Azure AD (Microsoft Entra ID)
  - Implementation: `ClientSecretCredential` from `azure-identity` package
  - Client ID, Secret, Tenant ID stored in Key Vault and referenced in app settings

**Service-to-Service Auth:**
- Mutual TLS or OAuth 2.0 client credentials for service calls
- Token cached in `SQLClient._cached_token` to avoid repeated auth calls

## Monitoring & Observability

**Error Tracking:**
- Application Insights (shared resource: `fx-apps-shared`)
  - Connection: `APPLICATIONINSIGHTS_CONNECTION_STRING` env var
  - Integration: `opencensus-ext-azure` log handler
  - Telemetry: Generic event/metric tracking via `TelemetryClient` in `functions/shared/telemetry.py`

**Logs:**
- **Seq** - Primary structured logging
  - Connection: `SEQ_SERVER_URL` and `SEQ_API_KEY` env vars
  - Client: `seqlog` library configured in `functions/shared/seq_logging.py`
  - Events emitted: ServiceStarted, ServiceCompleted, ServiceFailed, ServiceWarning
  - Sensitive data sanitization: Passwords, tokens, connection strings redacted automatically

- **SQL** - Persistent audit trail
  - Table: `jgilpatrick.apps_master_services_log`
  - Logged by: `ServiceLogger` class in `functions/shared/service_logger.py`
  - Data: Service name, status, request/response payloads, timing, parent/root IDs

- **Python logging** - stdout to Azure Functions runtime
  - Levels: Information for all layers
  - Handler added by opencensus for Application Insights forwarding

## CI/CD & Deployment

**Hosting:**
- Azure App Service (App Service Plan: `keystone-platform-asp`)
- Resource Group: `rg-keystone-platform`
- Deployment slots: Standard production deployment

**CI Pipeline:**
- GitHub Actions - Auto-deploys on push to main branch
- Workflow: `.github/workflows/` directory
- Trigger: Any commit to main automatically builds and publishes to `fx-app-apps-services`

## Environment Configuration

**Required env vars:**
- `SQL_EXECUTOR_URL` - HTTP endpoint for SQL Executor
- `SQL_EXECUTOR_SCOPE` - Azure AD app ID URI for token requests
- `SQL_EXECUTOR_CLIENT_ID` - KV reference: `executor-client-id`
- `SQL_EXECUTOR_CLIENT_SECRET` - KV reference: `executor-client-secret`
- `SQL_EXECUTOR_TENANT_ID` - KV reference: `azure-tenant-id`
- `SQL_EXECUTOR_SERVER` - Default server (e.g., "apps")
- `LOGIC_APP_EMAIL_URL` - Required by Pydantic validation (even if unused)
- `AZURE_STORAGE_CONNECTION_STRING` - Required by Pydantic validation
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - KV reference: `app-insights-connection-string`
- `SEQ_SERVER_URL` - Seq logging endpoint
- `SEQ_API_KEY` - KV reference: `seq-api-key`

**Secrets location:**
- Azure Key Vault: `eventus-apps`
- Function app settings reference via KV syntax (e.g., `@Microsoft.KeyVault(SecretUri=https://eventus-apps.vault.azure.net/secrets/executor-client-id/)`)

**Local Development:**
- local.settings.json contains above vars (git-ignored)
- Values can be literal strings or Key Vault URIs for testing

## Webhooks & Callbacks

**Incoming:**
- `POST /api/scheduler/manual-trigger` - Manual scheduler invocation
  - Optional params: `force_service_ids`, `bypass_window_check`
  - Returns: `{"status": "triggered", "services_found": N}`

- `GET /api/status/{log_id}` - Check service execution status
  - Returns: Status record from `apps_master_services_log`

- `GET /api/result/{log_id}` - Get complete execution result
  - Returns: Full log entry with request/response data

- Timer trigger - Scheduled via Azure Functions timer
  - Schedule: `"0 0,15,30,45 * * * *"` (every 15 minutes)
  - Currently set to impossible date (phase 3: timer running)

**Outgoing:**
- HTTP POST to target service endpoints (via `httpx` async client)
  - Returns: 202 Accepted with status URL for async operations
  - Polling: Periodic GET to status endpoint until completion

- Seq API POST - Structured events
  - Endpoint: Configured via `SEQ_SERVER_URL`
  - Method: seqlog client auto-posts to Seq endpoint

- SQL Executor API POST - SQL execution
  - Endpoint: `SQL_EXECUTOR_URL`
  - Method: `httpx.AsyncClient.post()` with Bearer token auth

## Data Flow

**Service Scheduling & Execution:**

1. Timer trigger fires every 15 minutes
2. Scheduler queries `apps_central_scheduling` for active services
3. For each service:
   - Creates log entry in `apps_master_services_log` (pending status)
   - Emits `ServiceStarted` event to Seq
   - Issues HTTP POST to target service endpoint
4. Service responds with 202 Accepted + status URL
5. Scheduler polls status endpoint every 30 seconds
6. On completion:
   - Updates log entry with success/failure status
   - Emits `ServiceCompleted` or `ServiceFailed` to Seq
   - Logs full request/response to SQL

**Workflow Tracking:**
- Root services: Have no parent_id, set root_id to own log_id
- Child services: Include parent_id and root_id for relationship tracking
- Multi-level workflow support via parent_id chaining

---

*Integration audit: 2026-02-27*
