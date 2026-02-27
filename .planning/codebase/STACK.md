# Technology Stack

**Analysis Date:** 2026-02-27

## Languages

**Primary:**
- Python 3.11 - All function implementations and orchestration

## Runtime

**Environment:**
- Azure Functions 4.x (Python)
- Azure App Service Plan: `keystone-platform-asp` (Premium V3 P1v3)

**Package Manager:**
- pip
- Lockfile: requirements.txt present

## Frameworks

**Core:**
- azure-functions 1.18.0 - Azure Functions SDK, blueprints, HTTP triggers, timer triggers

**Async/HTTP:**
- httpx - Async HTTP client for service-to-service calls
- aiohttp - Alternative async HTTP library

**Configuration & Validation:**
- pydantic - Runtime config validation and type safety
- pydantic-settings - Environment variable configuration with caching

**Logging & Observability:**
- seqlog 0.3.28+ - Structured logging to Seq server
- opencensus-ext-azure - Application Insights integration for telemetry
- logging (stdlib) - Python's standard logging module

**Azure Integration:**
- azure-identity - Azure AD authentication and token management
- azure-storage-blob - Azure Blob Storage client

**Utilities:**
- pytz - Timezone handling for Eastern Time calculations

## Key Dependencies

**Critical:**
- azure-functions 1.18.0 - Function app runtime, blueprints, triggers, decorators
- httpx - Service orchestration requires async HTTP calls to downstream services
- pydantic/pydantic-settings - Environment validation prevents startup failures from missing configuration
- azure-identity - Client credentials flow for SQL Executor API authentication

**Infrastructure:**
- opencensus-ext-azure - Application Insights telemetry (shared resource: `fx-apps-shared`)
- seqlog - Structured logging to Seq for real-time debugging and audit trails
- azure-storage-blob - Blob storage access for app data container

## Configuration

**Environment:**
- Environment variables for all runtime configuration
- Pydantic BaseSettings class (`functions/shared/settings.py`) validates all required vars at startup
- Caching with `@functools.lru_cache(maxsize=1)` on `get_settings()`

**Key Configs:**
- `SQL_EXECUTOR_URL` - HTTP endpoint for SQL Executor service
- `SQL_EXECUTOR_SCOPE`, `SQL_EXECUTOR_CLIENT_ID`, `SQL_EXECUTOR_CLIENT_SECRET`, `SQL_EXECUTOR_TENANT_ID` - Azure AD client credentials
- `SQL_EXECUTOR_SERVER` - Target server identifier (e.g., "apps")
- `LOGIC_APP_EMAIL_URL` - Legacy email webhook (required by settings but may not be used)
- `AZURE_STORAGE_CONNECTION_STRING` - Blob storage connection
- `APPLICATIONINSIGHTS_CONNECTION_STRING` - App Insights telemetry
- `SEQ_SERVER_URL`, `SEQ_API_KEY` - Seq logging endpoint

**Build:**
- host.json - Azure Functions runtime configuration
  - `functionTimeout: "-1"` - Unlimited timeout (key feature for long-running services)
  - Application Insights sampling: 20 items per second
  - Logging levels: Information for all layers

## Platform Requirements

**Development:**
- Python 3.11 runtime
- Virtual environment at `~/venv/fx-app-apps-services`
- Azure CLI for local auth with Azure AD
- local.settings.json with env vars for local development

**Production:**
- Azure App Service (keystone-platform-asp)
- Azure Key Vault for secrets (eventus-apps)
- Azure Storage Account (eventusappsbs) for blob storage
- Application Insights resource (fx-apps-shared)
- Seq server for structured logging

---

*Stack analysis: 2026-02-27*
