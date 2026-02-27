# Architecture

**Analysis Date:** 2026-02-27

## Pattern Overview

**Overall:** Async-first Blueprint-based Azure Functions V4 with decoupled layers for scheduling, logging, and SQL execution.

**Key Characteristics:**
- Three-layer logging architecture (Seq, SQL, Application Insights) for comprehensive observability
- Blueprint pattern for modular function registration and organization
- Unlimited timeout support via Keystone ASP (no artificial execution cutoffs)
- Service context propagation through workflow tracking (parent_id, root_id)
- SQL Executor API abstraction for database access with client credentials authentication

## Layers

**Entry Point Layer:**
- Purpose: App initialization and blueprint registration
- Location: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/function_app.py`
- Contains: Seq logging configuration, FunctionApp instantiation, blueprint imports
- Depends on: `seq_logging`, `timer_function`, `status_endpoints`, `trigger_function`
- Used by: Azure Functions runtime

**Function Layer:**
- Purpose: HTTP and timer-triggered endpoints for orchestration and service triggering
- Location: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/scheduler/`, `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/master_services_log/`, `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/trigger_function/`
- Contains: Timer triggers, HTTP routes, business logic for scheduling and status queries
- Depends on: `SQLClient`, `ServiceLogger`, `settings`, `telemetry`
- Used by: Azure Functions runtime and HTTP clients

**Shared Services Layer:**
- Purpose: Cross-cutting concerns including configuration, logging, database access, and telemetry
- Location: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/`
- Contains: Settings management, SQL client, service logger, Seq configuration, telemetry
- Depends on: Pydantic, httpx, Azure Identity, Azure Functions
- Used by: All function modules

## Data Flow

**Scheduler Timer Execution:**

1. Timer fires every 15 minutes (at :00, :15, :30, :45)
2. `scheduler_timer()` in `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/scheduler/timer_function.py` queries `jgilpatrick.apps_central_scheduling` table
3. For each active scheduled service, evaluates schedule config (cron, window, trigger limits)
4. Makes HTTP request to function trigger URL with payload
5. Handles 202 (async) responses by polling status endpoint until completion
6. Updates scheduling table with status, trigger count, last execution time
7. If services triggered, logs execution to `jgilpatrick.apps_master_services_log` via `ServiceLogger`
8. Emits structured events to Seq throughout lifecycle

**Manual Trigger Flow:**

1. POST to `/api/scheduler/manual-trigger` endpoint
2. Optional params: `force_service_ids` (list), `bypass_window_check` (bool)
3. Same execution path as timer, but respects force parameters
4. Returns JSON response with services found/triggered

**Status Query Flow:**

1. GET `/api/status/{log_id}` or `/api/result/{log_id}` from `status_endpoints.py`
2. Queries `jgilpatrick.apps_master_services_log` by `log_id`
3. Returns status (pending/success/failed/warning), timing, error messages
4. `/result/{log_id}` includes full request/response/metadata payloads
5. Health check endpoint tests database connectivity

**Function Trigger Flow:**

1. POST to `/api/trigger/{id}` or `/api/trigger?app={app_name}&function={function_name}`
2. Looks up function details from `jgilpatrick.apps_function_apps` catalog
3. Acquires Azure AD token or appends host key based on auth requirements
4. Makes HTTP POST to target function with payload
5. Returns response (immediately for sync, 202 with status URL for async)

**State Management:**

- **Execution State:** Stored in `jgilpatrick.apps_master_services_log` (pending → success/failed/warning)
- **Scheduled Service State:** Stored in `jgilpatrick.apps_central_scheduling` (last_triggered_at, times_triggered, is_active)
- **Function Catalog:** Stored in `jgilpatrick.apps_function_apps` (endpoint URLs, auth methods, active status)
- **Transient State:** Held in memory during function execution (no external state store beyond SQL)

## Key Abstractions

**ServiceLogger:**
- Purpose: Centralized logging to `apps_master_services_log` table with workflow context
- Examples: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/service_logger.py`
- Pattern: Async context manager emitting `ServiceStarted` → `ServiceCompleted/ServiceFailed/ServiceWarning` with timing and error details
- Methods: `log_start()`, `log_success()`, `log_error()`, `log_warning()`, `get_child_context()` for parent-child relationships
- Features: SQL injection protection via string escaping, Seq event emission, parent/root_id tracking

**SQLClient:**
- Purpose: SQL Executor API client with client credentials authentication
- Examples: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/sql_client.py`
- Pattern: Async context manager with token caching and retry logic for cold starts
- Methods: `execute(sql, method="query"|"execute", server=None, title=None, timeout=None)`
- Features: Automatic token acquisition, server override, SQL cold start retries (5s delay × 3 attempts), configurable timeout

**Settings:**
- Purpose: Pydantic-based configuration with environment variable binding and LRU caching
- Examples: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/settings.py`
- Pattern: Singleton via `@functools.lru_cache(maxsize=1)`
- Fields: SQL Executor URL/scope/server, Logic App email URL, Azure Storage connection, email API, Application Insights, timeouts
- Features: HttpUrl validation, case-insensitive env var names, lazy initialization

**Seq Logging:**
- Purpose: Structured event logging with sensitive data sanitization
- Examples: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/seq_logging.py`
- Pattern: Module-level configuration on app startup; logging.Logger emits structured properties
- Events: `ServiceStarted`, `ServiceCompleted`, `ServiceFailed`, `ServiceWarning` with EventType, ServiceName, FunctionApp, Status, LogId, DurationMs
- Features: Sensitive data masking (API keys, tokens, emails), emoticon-based message formatting, graceful fallback on failure

**FunctionAppTrigger:**
- Purpose: Service for triggering cataloged Azure Function Apps with flexible auth
- Examples: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/trigger_function/trigger_function.py`
- Pattern: Class-based service with credential caching
- Methods: `get_function_by_id()`, `get_function_by_name()`, `get_azure_ad_token()`, `trigger_function()`
- Features: Supports both Azure AD and host key authentication, httpx client for HTTP calls

## Entry Points

**Timer Trigger:**
- Location: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/scheduler/timer_function.py`, `scheduler_timer()` function
- Triggers: Every 15 minutes (CRON: `0 0,15,30,45 * * * *`)
- Responsibilities: Query active scheduled services, evaluate schedules, trigger functions, poll for 202 responses, update scheduling table, log execution
- Key Config: `SERVICE_REQUEST_TIMEOUT = 600` (10 min per service), `POLLING_INTERVAL = 30` (poll every 30s)

**Manual Scheduler Trigger:**
- Location: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/scheduler/timer_function.py`, `scheduler_manual_trigger()` function
- Triggers: POST `/api/scheduler/manual-trigger`
- Responsibilities: Same as timer trigger but respects `force_service_ids` and `bypass_window_check` parameters
- Response: JSON with `{"status": "triggered", "services_found": N}`

**Status Endpoints:**
- Location: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/master_services_log/status_endpoints.py`
- Triggers: GET `/api/status/{log_id}`, GET `/api/result/{log_id}`, GET `/api/health/master-services-log`
- Responsibilities: Query and return service execution status, full results with payloads, health check with DB connectivity test
- Response: JSON with status object including timing, error messages, request/response/metadata

**Function App Trigger:**
- Location: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/trigger_function/trigger_function.py`
- Triggers: POST `/api/trigger/{id}`, POST `/api/trigger?app={app_name}&function={function_name}`
- Responsibilities: Look up function catalog entry, acquire auth token, trigger remote function, return response
- Response: Immediate for sync, 202 with status URL for async functions

## Error Handling

**Strategy:** Structured exception handling with logging at multiple levels, graceful degradation, and detailed error context.

**Patterns:**

- **Try-Catch-Log-Raise:** Catch exceptions, log details with context to Seq and SQL, then re-raise or return error response
- **Retry with Backoff:** SQL cold start retries with 5s delay, max 3 attempts (in SQLClient)
- **Polling Resilience:** Long-polling with 30s intervals tolerates transient service failures; tracks timeout separately
- **Workflow Tracking:** Parent/root IDs preserved in error logs for tracing service chains across failures
- **Sensitive Data Masking:** Error messages sanitized before logging to prevent credential leaks
- **Fallback Logging:** If Seq logging fails, error is printed to stderr but service continues

## Cross-Cutting Concerns

**Logging:** Three-layer approach:
- **Seq (Real-Time):** Structured events via standard Python logging with structured properties; immediate alerting capability
- **SQL (Persistent):** `jgilpatrick.apps_master_services_log` table with full execution history, request/response payloads, error details
- **Application Insights:** Automatic telemetry from Azure Functions runtime; sampling enabled (20 items/sec max)

**Validation:**
- SQL injection prevention: String escaping in ServiceLogger and status_endpoints
- Input validation: Numeric log_id validation, HTTP request schema validation
- Environment validation: Required env vars checked at startup (SQL_EXECUTOR_CLIENT_ID/SECRET/TENANT_ID)

**Authentication:**
- Azure AD via ClientSecretCredential (for SQL Executor API)
- Host key appended to URLs (for target functions)
- Token caching in SQLClient to avoid repeated auth calls
- Scope: `api://8b3542fd-41c7-4aec-b14d-d0ee8342e57a/.default` (SQL Executor)

**Configuration:**
- Settings loaded via Pydantic from environment at first request (lazy initialization)
- All secrets via Key Vault references (resolved by Azure Functions runtime)
- Timeout config: `host.json` sets `"functionTimeout": "-1"` for unlimited execution
- Log levels: Default `Information`, captures `Host.Results`, `Function`, `Host.Aggregator`

---

*Architecture analysis: 2026-02-27*
