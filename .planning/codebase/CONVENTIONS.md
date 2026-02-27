# Coding Conventions

**Analysis Date:** 2026-02-27

## Naming Patterns

**Files:**
- Module files use lowercase with underscores: `service_logger.py`, `sql_client.py`, `seq_logging.py`
- Async function files include function type: `timer_function.py`, `status_endpoints.py`, `trigger_function.py`
- Shared modules in `functions/shared/`: settings, logging, clients, telemetry

**Functions:**
- Async functions prefixed with `async def`: `async def log_start()`, `async def execute_sql_with_cold_start_retry()`
- Helper functions use descriptive verb-noun patterns: `get_eastern_time_sql()`, `sanitize_sql_string()`, `check_and_handle_stuck_processing_services()`
- Private methods prefixed with underscore: `_emit_seq_event()`, `_escape_sql_string()`, `_get_token()`, `_log_completion()`
- Constants in ALL_CAPS: `SERVICE_REQUEST_TIMEOUT`, `MAX_SQL_COLD_START_RETRIES`, `POLLING_INTERVAL`

**Variables:**
- camelCase for local variables: `logId`, `requestData`, `errorMessage`
- snake_case for module-level and class attributes: `service_name`, `function_app`, `parent_service_id`
- Typed parameters: `sql_client: SQLClient`, `status: str`, `duration_ms: float`

**Types:**
- Type hints on all function signatures using `from __future__ import annotations`
- Dict with specific keys: `Dict[str, Any]`, `Dict[str, int]`
- Optional types for nullable values: `Optional[str]`, `Optional[int]`
- Return type annotations: `-> func.HttpResponse`, `-> str`, `-> Dict[str, Any]`

## Code Style

**Formatting:**
- No explicit linting configuration found (no `.pylintrc`, `pyproject.toml`, `.flake8`)
- Follows implicit Python 3.11 conventions
- Line length not strictly enforced (timer_function.py has lines >100 chars)
- Spacing around operators, no trailing whitespace observed

**Linting:**
- Code uses `# pylint: disable=broad-except` for specific warnings (see `telemetry.py` line 35, 43, 51)
- Exception handling uses broad `except Exception` with intention comments
- No type checking via mypy observed in codebase

## Import Organization

**Order:**
1. Future imports: `from __future__ import annotations`
2. Standard library imports: `import logging`, `import json`, `from typing import ...`
3. Third-party imports: `import azure.functions as func`, `import httpx`, `from pydantic import ...`
4. Local imports: `from ..shared.settings import get_settings`, `from .sql_client import SQLClient`

**Example from `function_app.py` (lines 1-37):**
```python
"""Azure Functions entry point for fx-app-apps-services."""
from __future__ import annotations

# SECTION 1: Standard library imports
import logging

# SECTION 2: Third-party imports
import azure.functions as func

# SECTION 3: Seq configuration (BEFORE FunctionApp)
from functions.shared.seq_logging import configure_seq_logging
configure_seq_logging()

# Get logger after configuration
logger = logging.getLogger(__name__)

# SECTION 4: Create FunctionApp instance
app = func.FunctionApp()

# SECTION 5: Blueprint imports and registrations
from functions.scheduler.timer_function import bp as scheduler_bp
```

**Path Aliases:**
- Relative imports using `..shared` for sibling packages
- Blueprint imports aliased as `bp`: `from .timer_function import bp as scheduler_bp`

## Error Handling

**Patterns:**
- Broad exception handling with logging and tracking: `except Exception as e: LOGGER.error(...); raise`
- SQL string sanitization before insertion: `_escape_sql_string()` method with quote doubling (see `service_logger.py` lines 287-312)
- Async context managers for resource cleanup: `async with SQLClient() as sql_client:` (see `status_endpoints.py` line 157)
- Cold-start retry logic with exponential backoff: `execute_sql_with_cold_start_retry()` (see `timer_function.py` lines 53-107)
- HTTP error handling with status code checks: `response.raise_for_status()` (see `sql_client.py` line 105)

**Error Messages:**
- Sanitized before logging: `sanitize_sensitive_data(str(error))` removes credentials, tokens, keys (see `seq_logging.py` lines 98-124)
- Truncated to 500 chars for Seq logging: `error_message[:500]` (see `service_logger.py` line 92)
- Include context: service name, log_id, invocation_id, operation type

## Logging

**Framework:**
- Python standard `logging` module with `getLogger(__name__)`
- Structured logging via Seqlog for Seq integration
- Application Insights via opencensus

**Patterns:**
- Module-level logger: `LOGGER = logging.getLogger(__name__)`
- Structured properties as kwargs: `LOGGER.info("message", **props)` (see `seq_logging.py` line 383)
- Emoticons for visual scanning: `🚀` (started), `✅` (completed), `❌` (failed), etc.
- Three-layer logging: Seq (real-time), SQL (persistence), Application Insights (telemetry)
- SQL logging includes request/response payloads via ServiceLogger (see `service_logger.py` lines 108-187)

**Example from `status_endpoints.py` (lines 136-145):**
```python
LOGGER.info("🚀 GET_STATUS FUNCTION CALLED!")
LOGGER.info(f"📝 Request URL: {req.url}")
LOGGER.info(f"📝 Request method: {req.method}")
LOGGER.info(f"📝 Route params: {req.route_params}")
log_id = req.route_params.get('log_id')
LOGGER.info(f"🔍 Extracted log_id: {log_id}")
LOGGER.info(f"📊 Status request for log_id: {log_id}")
```

## Comments

**When to Comment:**
- Function docstrings for all public functions with Args, Returns, Raises sections
- Inline comments for complex logic: DST calculations, SQL injection protection strategy
- Section markers for major code blocks: `# SECTION 1: Standard library imports`
- WARNING/NOTE comments for non-obvious behavior (e.g., "Cannot use SCOPE_IDENTITY() due to triggers")

**JSDoc/TSDoc:**
- Not used (Python project, uses docstrings instead)
- Docstrings use Google style with sections: Args, Returns, Raises
- Example from `service_logger.py` (lines 60-75):
```python
def _emit_seq_event(
    self,
    event_type: str,
    status: str,
    duration_ms: float = None,
    error_message: str = None
) -> None:
    """
    Emit structured event to Seq for real-time monitoring.

    Args:
        event_type: One of "ServiceStarted", "ServiceCompleted", "ServiceFailed", "ServiceWarning"
        status: Current status of the service
        duration_ms: Elapsed time in milliseconds (optional)
        error_message: Error or warning message (optional)
    """
```

## Function Design

**Size:**
- Helper functions (20-50 lines): `get_eastern_time_sql()`, `sanitize_sql_string()`, `is_within_schedule_window()`
- Core service functions (100-300 lines): `log_start()`, `process_scheduled_services_with_overrides()`
- Large functions (>500 lines): `timer_function.py` is 1,306 lines for orchestration
- Preference for single-responsibility functions with clear async boundaries

**Parameters:**
- Keyword-only arguments for clarity: `async def log_start(self, sql_client: SQLClient, *, request_data: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None)`
- Defaults for optional parameters: `method: str = "query"`
- Type hints on all parameters

**Return Values:**
- Explicit return types on all functions: `-> func.HttpResponse`, `-> int`, `-> None`
- JSON serialization for HTTP responses: `json.dumps(dict, indent=2, default=str)`
- Async functions return values directly or wrapped in HttpResponse

**Example from `trigger_function.py` (lines 114-192):**
```python
@bp.route(route="trigger/{function_id:int}", methods=["POST"])
async def trigger_by_id(req: func.HttpRequest) -> func.HttpResponse:
    """Trigger a function by its catalog ID."""
    try:
        function_id = req.route_params.get("function_id")
        payload = req.get_json() or {}
        # ... business logic with multiple error cases ...
        return func.HttpResponse(
            json.dumps({...}),
            status_code=200,
            mimetype="application/json"
        )
    except Exception as e:
        # ... error handling ...
        return func.HttpResponse(
            json.dumps({"error": "Internal server error"}),
            status_code=500,
            mimetype="application/json"
        )
```

## Module Design

**Exports:**
- Blueprints created as module-level objects: `bp = func.Blueprint()` (see all functions)
- Classes exported implicitly: `class ServiceLogger:`, `class SQLClient:`, `class FunctionAppTrigger:`
- Functions exported implicitly or via blueprint registration
- Singleton pattern for settings: `@functools.lru_cache(maxsize=1) def get_settings() -> Settings:` (see `settings.py` lines 34-37)
- Telemetry singleton: `_telemetry_client: Optional[TelemetryClient] = None` (see `telemetry.py` lines 55-64)

**Barrel Files:**
- Not used; imports are explicit from specific modules
- No `__init__.py` re-exports for convenience (modules import directly)

---

*Convention analysis: 2026-02-27*
