# Codebase Structure

**Analysis Date:** 2026-02-27

## Directory Layout

```
fx-app-apps-services/
├── .github/                           # GitHub workflows
│   └── workflows/                     # CI/CD automation
├── .planning/                         # Planning documents
├── functions/                         # Azure Functions modules
│   ├── __init__.py
│   ├── scheduler/                     # Service scheduling engine
│   │   ├── __init__.py
│   │   └── timer_function.py          # Timer trigger & scheduler logic (1,307 lines)
│   ├── master_services_log/           # Status tracking & results
│   │   ├── __init__.py
│   │   └── status_endpoints.py        # Status, result, health endpoints
│   ├── trigger_function/              # Function app triggering
│   │   ├── __init__.py
│   │   └── trigger_function.py        # Trigger catalog functions
│   └── shared/                        # Cross-cutting services
│       ├── __init__.py
│       ├── settings.py                # Pydantic config, env binding
│       ├── sql_client.py              # SQL Executor API client
│       ├── service_logger.py          # Apps master services log integration
│       ├── seq_logging.py             # Seq structured logging setup
│       └── telemetry.py               # Application Insights tracking
├── function_app.py                    # Entry point, blueprint registration
├── host.json                          # Azure Functions config (timeout: -1)
├── requirements.txt                   # Python dependencies
├── .gitignore                         # Git ignore rules
├── CLAUDE.md                          # Project-specific instructions
├── README.md                          # Project documentation
└── DEPLOYMENT_STATUS.md               # Deployment records
```

## Directory Purposes

**functions/**
- Purpose: All Azure Function modules organized by responsibility
- Contains: Python packages for scheduler, endpoints, trigger service, and shared utilities
- Key files: `__init__.py` (empty in subdirs), function definitions, blueprint creation

**functions/scheduler/**
- Purpose: Central scheduling engine for orchestrating service execution
- Contains: Timer-triggered function that evaluates schedules and triggers services
- Key files: `timer_function.py` (main scheduler with full orchestration logic)

**functions/master_services_log/**
- Purpose: HTTP endpoints for querying execution status and results
- Contains: Status and result query endpoints, health check
- Key files: `status_endpoints.py` (GET `/api/status/{log_id}`, `/api/result/{log_id}`)

**functions/trigger_function/**
- Purpose: Service for triggering other Azure Function Apps
- Contains: Function catalog lookup, auth handling, HTTP triggering
- Key files: `trigger_function.py` (POST endpoints for function triggering)

**functions/shared/**
- Purpose: Shared services and utilities used across all functions
- Contains: Configuration, database client, logging, telemetry
- Key files: `settings.py`, `sql_client.py`, `service_logger.py`, `seq_logging.py`, `telemetry.py`

## Key File Locations

**Entry Points:**
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/function_app.py`: Initializes Seq logging, creates FunctionApp, registers blueprints

**Configuration:**
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/host.json`: Runtime config (timeout -1 = unlimited, logging levels, extension bundle)
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/settings.py`: Pydantic Settings class with env var binding
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/requirements.txt`: Python dependencies

**Core Logic:**
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/scheduler/timer_function.py`: Main scheduler (1,307 lines) - evaluates schedules, triggers services, polls for completion
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/master_services_log/status_endpoints.py`: Status queries (GET endpoints)
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/trigger_function/trigger_function.py`: Function triggering (POST endpoints)

**Shared Services:**
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/sql_client.py`: SQL Executor API client with token caching
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/service_logger.py`: ServiceLogger for apps_master_services_log
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/seq_logging.py`: Seq structured logging configuration
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/telemetry.py`: Application Insights tracking

**Documentation:**
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/CLAUDE.md`: Project-specific instructions including command reference, architecture notes, testing checklist
- `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/README.md`: Project overview and setup

## Naming Conventions

**Files:**
- Module files: `snake_case.py` (e.g., `timer_function.py`, `service_logger.py`)
- Config files: `lowercase.json` (e.g., `host.json`, `function.json`)
- Documentation: `UPPERCASE.md` (e.g., `CLAUDE.md`, `README.md`)

**Directories:**
- Function modules: `snake_case/` (e.g., `scheduler/`, `master_services_log/`, `trigger_function/`)
- Shared directory: `shared/` (for cross-cutting services)
- Hidden directories: `.{name}/` (e.g., `.github/`, `.planning/`, `.python_packages/`)

**Functions:**
- Entry points: `{module}_trigger()`, `{operation}_function()`, or `{operation}()` (e.g., `scheduler_timer()`, `get_status()`, `get_result()`)
- Async functions: Always `async def` (e.g., `async def scheduler_timer()`)
- Helper functions: `verb_noun()` pattern (e.g., `get_master_log_entry()`, `trigger_function()`)

**Variables & Classes:**
- Constants: `UPPER_CASE` (e.g., `SERVICE_REQUEST_TIMEOUT`, `POLLING_INTERVAL`)
- Functions/methods: `snake_case()` (e.g., `log_start()`, `execute()`)
- Classes: `PascalCase` (e.g., `ServiceLogger`, `SQLClient`, `Settings`)
- Private methods: `_snake_case()` (e.g., `_log_completion()`, `_escape_sql_string()`)

## Where to Add New Code

**New Service Function:**
- Primary code: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/{service_name}/{function_name}.py`
- Blueprint creation: `bp = func.Blueprint()` at module level
- Entry point: `@bp.route()`, `@bp.timer_trigger()`, or `@bp.queue_trigger()`
- Example: Scheduler uses `bp = func.Blueprint()` then registers with `app.register_functions(bp)`

**New Shared Utility:**
- Implementation: `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/{utility_name}.py`
- Export: Define class or functions, no __init__.py changes needed
- Usage: `from ..shared.{utility_name} import {class_or_function}`

**New Configuration:**
- Settings field: Add to `Settings` class in `/Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active/fx-app-apps-services/functions/shared/settings.py`
- Environment variable: Field with `env="ENV_VAR_NAME"`
- Access: Call `get_settings()` (cached singleton)

**New Endpoint:**
- Blueprint: Create in appropriate function module or new module
- Route: `@bp.route(route="{path}", methods=["GET"|"POST"], auth_level=func.AuthLevel.ANONYMOUS|FUNCTION|ADMIN)`
- Response: Return `func.HttpResponse(json.dumps(data), status_code=200, mimetype="application/json")`
- Registration: Add `app.register_functions({module_bp})` in `function_app.py`

## Special Directories

**.github/**
- Purpose: GitHub Actions CI/CD workflows
- Generated: Yes (by GitHub when workflows run)
- Committed: Yes (contains workflow definitions)

**.planning/**
- Purpose: Planning documents and codebase analysis
- Generated: Yes (created by GSD tools)
- Committed: Yes (for team reference)

**.python_packages/**
- Purpose: Local Python package cache
- Generated: Yes (created by func CLI)
- Committed: No (.gitignore)

**.git/**
- Purpose: Version control history
- Generated: Yes (by git init)
- Committed: Yes (automatically)

## Import Organization

**Order in function modules:**
1. Standard library (datetime, json, logging, asyncio, time, uuid)
2. Third-party (azure.functions, azure.identity, httpx, pytz, pydantic)
3. Seq configuration (before FunctionApp creation)
4. Relative imports from shared (..shared.*)
5. Logging and constants

**Example (from function_app.py):**
```python
# Standard library
import logging

# Third-party
import azure.functions as func

# Seq configuration (BEFORE FunctionApp)
from functions.shared.seq_logging import configure_seq_logging
configure_seq_logging()

# Create app
logger = logging.getLogger(__name__)
app = func.FunctionApp()

# Blueprint imports
from functions.scheduler.timer_function import bp as scheduler_bp
...
```

**Path aliases:**
- Relative from `functions/`: Use `..shared` (e.g., `from ..shared.sql_client import SQLClient`)
- Absolute imports avoided to maintain module independence

## How to Structure New Functions

**Standard Pattern:**
```python
"""Module docstring describing function purpose and endpoints."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import azure.functions as func

from ..shared.sql_client import SQLClient
from ..shared.service_logger import ServiceLogger
from ..shared.settings import get_settings

logger = logging.getLogger(__name__)

# Create blueprint
bp = func.Blueprint()

# Define routes
@bp.route(route="endpoint/{id}", methods=["GET|POST"])
async def endpoint_function(req: func.HttpRequest) -> func.HttpResponse:
    """Docstring with purpose and response schema."""
    async with SQLClient() as sql:
        logger = ServiceLogger("service_name")
        await logger.log_start(sql, request_data=json.dumps(req.get_json()))

        try:
            # Business logic here
            result = await do_work()

            await logger.log_success(sql, response_data=json.dumps(result))
            return func.HttpResponse(json.dumps(result), status_code=200)
        except Exception as e:
            await logger.log_error(sql, str(e))
            return func.HttpResponse(json.dumps({"error": str(e)}), status_code=500)
```

---

*Structure analysis: 2026-02-27*
