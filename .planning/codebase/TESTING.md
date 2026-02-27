# Testing Patterns

**Analysis Date:** 2026-02-27

## Test Framework

**Status: No tests found**

This codebase has no test files or testing framework configured:
- No `pytest.ini`, `conftest.py`, or test discovery configuration
- No `tests/` or `test_` prefixed files in the repository
- No test dependencies in `requirements.txt`
- No pytest/unittest import statements in codebase

This is a production Azure Functions application without unit test coverage.

**Runner:**
- No test runner configured (pytest, unittest, or other)
- `requirements.txt` contains no test dependencies (pytest, pytest-asyncio, unittest-mock, etc.)

**Assertion Library:**
- Not applicable (no testing framework in place)

**Run Commands:**
- No test execution commands exist
- Manual testing via local development: `func start` (see CLAUDE.md)
- Production validation via Azure Functions runtime and Seq logs

## Test File Organization

**Location:**
- No test files present in repository

**Naming:**
- No naming conventions established

**Structure:**
- Not applicable

## Testing Strategy (Current)

This codebase relies on:

1. **Local Development Testing via `func start`:**
   - Azure Functions local runtime at `http://localhost:7071`
   - Manual HTTP requests to endpoints via curl, Postman, or browser
   - Real-time log viewing via Seq and Application Insights

2. **Production Validation:**
   - End-to-end testing of timer trigger and scheduler (documented in `CLAUDE.md` as "Validation (2026-02-23)")
   - Manual scheduler trigger: `POST /api/scheduler/manual-trigger` with optional params
   - Status polling: `GET /api/status/{log_id}` to track service execution
   - Result retrieval: `GET /api/result/{log_id}` for complete execution details

3. **Logging-Based Verification:**
   - Seq structured logs with ServiceStarted, ServiceCompleted, ServiceFailed events
   - SQL master services log table: `jgilpatrick.apps_master_services_log`
   - Application Insights telemetry for Azure monitoring

## Mocking

**Not Applicable:**
- No test framework present
- No mocking library (unittest.mock, pytest-mock, responses, vcr) in use

**Manual Testing Approach:**
- Services trigger actual SQL Executor API calls (no mocking in code)
- HTTP calls use real endpoints via `httpx.AsyncClient`
- Database operations go against actual `apps` database via SQL Executor API
- No isolation or stubs in code

## Fixtures and Factories

**Not Applicable:**
- No test fixtures or factory patterns present
- No test data builders or seeding utilities

**Manual Test Data:**
- Services are configured in `jgilpatrick.apps_central_scheduling` table
- Test scheduling can be set via direct SQL updates
- Manual scheduler trigger allows testing specific service IDs (see `timer_function.py` lines 252-338 for `force_service_ids` parameter)

**Test Service Example:**
From `CLAUDE.md` - 2026-02-23 validation used `fx-app-template` as test service:
```
Manual trigger confirmed working — triggered hello-world on fx-app-template, polled
apps_master_services_log, updated scheduling table. 6.42s end-to-end.
```

## Coverage

**Requirements:**
- No coverage targets or CI/CD enforced (see `requirements.txt` - no coverage.py or similar)
- No pytest-cov configuration

**Current State:**
- Estimated 0% unit test coverage
- Functional coverage through end-to-end validation in production

## Test Types

**Unit Tests:**
- Not implemented
- Would cover individual functions like `sanitize_sql_string()`, `is_within_schedule_window()`, `_escape_sql_string()`
- Would test ServiceLogger lifecycle: log_start → log_success/error
- Would test SQL formatting and injection prevention

**Integration Tests:**
- Not formally implemented
- Manual end-to-end validation exists (documented in CLAUDE.md)
- Tests SQL Executor API connectivity, database operations, and service lifecycle
- Validates workflow tracking (parent_id, root_id) through master services log

**E2E Tests:**
- Manual validation via local development
- Production validation through scheduled execution
- Manual trigger endpoint for testing: `POST /api/scheduler/manual-trigger`
  - Optional params: `force_service_ids: [1, 2, 3]`, `bypass_window_check: bool`
  - Returns: `{"status": "triggered", "services_found": 3}`

## Common Patterns (Manual Testing)

**Async Testing Approach:**
All functions are async-first, requiring manual testing in async context:
- Local: `func start` creates Azure Functions runtime
- Manual trigger: `POST /api/scheduler/manual-trigger`
- Status check: Poll `GET /api/status/{log_id}` for completion
- Example from `CLAUDE.md`:
  ```
  Trigger → Poll status → Check Seq logs → Verify SQL log entries → Inspect metadata
  ```

**Error Testing:**
- Manually test error cases by triggering functions with invalid inputs:
  - Missing log_id: `GET /api/status/` → 400 Bad Request
  - Invalid log_id: `GET /api/status/invalid` → 400 Bad Request
  - Non-existent log_id: `GET /api/status/999999` → 404 Not Found
- Check error responses include proper JSON structure with "error" and "message" fields
- Verify Seq logs capture ServiceFailed events with sanitized error messages

**SQL Validation:**
```bash
# Query master services log for recent executions
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G \
  -Q "SELECT TOP 10 * FROM jgilpatrick.apps_master_services_log \
      WHERE function_app = 'fx-app-apps-services' ORDER BY started_at DESC"

# Verify workflow tracking (parent-child relationships)
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G \
  -Q "SELECT log_id, parent_id, root_id, service_name, status \
      FROM jgilpatrick.apps_master_services_log \
      WHERE root_id IS NOT NULL ORDER BY log_id DESC"
```

## Local Development & Testing

**Setup:**
```bash
# Create virtual environment
python -m venv ~/venv/fx-app-apps-services
source ~/venv/fx-app-apps-services/bin/activate

# Install dependencies
cd fx-app-apps-services
pip install -r requirements.txt
```

**Run Locally:**
```bash
# Start Azure Functions runtime
func start
```

**Manual Test - Scheduler Trigger:**
```bash
# Trigger scheduler immediately (will process pending/failed services)
curl -X POST http://localhost:7071/api/scheduler/manual-trigger \
  -H "Content-Type: application/json"

# Trigger with forced service IDs
curl -X POST http://localhost:7071/api/scheduler/manual-trigger \
  -H "Content-Type: application/json" \
  -d '{"force_service_ids": [1, 2, 3]}'

# Bypass scheduling window checks
curl -X POST http://localhost:7071/api/scheduler/manual-trigger \
  -H "Content-Type: application/json" \
  -d '{"bypass_window_check": true}'
```

**Manual Test - Status Endpoint:**
```bash
# Get status of service execution
curl http://localhost:7071/api/status/1001

# Get full result with request/response data
curl http://localhost:7071/api/result/1001

# Health check
curl http://localhost:7071/api/health/master-services-log
```

**Manual Test - Trigger Function:**
```bash
# Trigger function by catalog ID
curl -X POST http://localhost:7071/api/trigger/5 \
  -H "Content-Type: application/json" \
  -d '{"test_param": "value"}'

# Trigger function by name
curl -X POST "http://localhost:7071/api/trigger?app=fx-app-template&function=hello_world" \
  -H "Content-Type: application/json" \
  -d '{}'

# List available functions
curl http://localhost:7071/api/trigger/list
```

## Validation Checklist (from CLAUDE.md)

When deploying changes, manual validation should include:

- [ ] Status endpoints return correct data from `apps_master_services_log`
- [ ] Manual scheduler trigger executes without timeout errors
- [ ] Long-running services (15+ min) complete successfully
- [ ] Seq logs show no timeout warnings
- [ ] Timer remains disabled (Day 0 schedule still impossible date) or active per deployment phase
- [ ] All logging layers working (Seq, SQL, App Insights)

## Recommended Testing Approach for New Features

**For new endpoints:**
1. Test locally with `func start`
2. Verify SQL logging via query to `apps_master_services_log`
3. Check Seq logs for structured events
4. Verify Application Insights captures the event
5. Test error cases (missing params, invalid IDs, database errors)
6. Confirm response JSON format matches documented structure

**For new helper functions:**
1. Manually test with sample inputs in Python REPL or local test script
2. Verify edge cases (empty strings, nulls, special characters for SQL injection prevention)
3. Check return values match type hints
4. Verify logging output if applicable

**For schema changes:**
1. Test SQL queries locally with `sqlcmd`
2. Verify sanitization/escaping logic handles data types
3. Test round-trip: write via ServiceLogger.log_start → read via status_endpoints
4. Verify metadata JSON serialization/deserialization

---

*Testing analysis: 2026-02-27*
