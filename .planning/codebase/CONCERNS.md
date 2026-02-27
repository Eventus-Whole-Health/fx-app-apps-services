# Codebase Concerns

**Analysis Date:** 2026-02-27

## Tech Debt

**Duplicate SQL Sanitization Logic:**
- Issue: SQL string sanitization logic is duplicated across three locations with identical implementation
- Files: `functions/shared/service_logger.py` (lines 287-312), `functions/scheduler/timer_function.py` (lines 110-135), `functions/master_services_log/status_endpoints.py` (lines 22-42)
- Impact: Maintenance burden—changes to SQL escaping logic must be propagated to all three files. Risk of inconsistency if one location is updated and others aren't.
- Fix approach: Create shared utility function `functions/shared/sql_utils.py` with single `sanitize_sql_string()` implementation. Import and use from all three modules.

**Manual SQL String Building Throughout Codebase:**
- Issue: SQL queries are built using string interpolation and manual escaping in multiple places
- Files: `functions/scheduler/timer_function.py` (lines 188-196, 230-237, 340-348, 398-410, 711-720, 773-781, 926-932), `functions/trigger_function/trigger_function.py` (lines 36, 44-45, 288)
- Impact: SQL injection risk if sanitization is missed. Harder to audit all query paths. Schema changes require multiple updates.
- Fix approach: Create query builder layer in `functions/shared/sql_builder.py` with parameterized query helpers (e.g., `build_update_scheduling_status()`, `build_stuck_services_query()`) to enforce consistent escaping and reduce manual string manipulation.

**Settings Class Requires Unused Configuration:**
- Issue: `Settings` class in `functions/shared/settings.py` (lines 16, 23-24) requires `LOGIC_APP_EMAIL_URL` and `AZURE_STORAGE_CONNECTION_STRING` environment variables even though scheduler never uses them
- Files: `functions/shared/settings.py` (lines 11-37)
- Impact: Deployment friction—these settings must be set or app fails at startup with Pydantic ValidationError. Misleading configuration requirements.
- Fix approach: Make these fields optional with defaults, or separate settings into app-specific subsets (SchedulerSettings, TriggerSettings, etc). Document which settings are actually required for the scheduler vs. inherited from apps_services.

**Unreachable Code in Timer Function:**
- Issue: Timer function has polling logic that may be unreachable due to schedule configuration
- Files: `functions/scheduler/timer_function.py` (lines 944, 1000-1040 containing the Day 0 schedule check)
- Impact: Complexity without execution. The check `if datetime(2000, 1, 1) == datetime.now().date()` is always False, so timer never runs. Logic exists but untested in production timer context.
- Fix approach: Document intent clearly or remove schedule guard if intent is to run continuously. If timer should be disabled in Phase 3, use Azure portal schedule override instead of code checks.

**Hardcoded Table Names and Schema References:**
- Issue: Database schema names (e.g., `jgilpatrick.*`) are hardcoded throughout codebase
- Files: `functions/scheduler/timer_function.py` (lines 190, 231, 273, 340, 372, etc.), `functions/master_services_log/status_endpoints.py` (lines 82), `functions/trigger_function/trigger_function.py` (lines 36, 44, 288)
- Impact: Difficult to test in dev/staging with different schemas. Single-tenancy assumption baked in. Schema migration requires code changes.
- Fix approach: Centralize table references in a `constants.py` or `schemas.py` file, or pass schema name via settings for flexibility.

## Known Bugs

**Infinite Polling Loop Risk:**
- Symptoms: If a service is triggered and returns 202 but service never completes in master services log, scheduler will poll indefinitely
- Files: `functions/scheduler/timer_function.py` (lines 709-742, the `poll_master_log_for_completion()` function)
- Trigger: Service at target endpoint crashes before logging completion, or SQL connectivity issue prevents master log from being updated
- Workaround: Timeout will eventually be hit at Azure Functions level (after hours), but scheduler will block that execution slot. Stuck processing check (lines 174-249) only catches services stuck for 15+ minutes.
- Fix approach: Add maximum polling duration limit (e.g., 55 minutes for Functions timeout) and exit polling with failure if exceeded. Log clear error message.

**Race Condition in log_id Retrieval:**
- Symptoms: `ServiceLogger.log_start()` retrieves log_id using invocation_id, but if SQL triggers fire slowly or network latency occurs, query may not find the newly inserted row
- Files: `functions/shared/service_logger.py` (lines 165-172)
- Trigger: High concurrency or SQL performance issues
- Workaround: Uses invocation_id (UUID) which is unique, but no retry if query returns empty
- Fix approach: Add retry loop with 3-5 attempts + 100ms backoff in `log_start()` before raising RuntimeError. OR use `SCOPE_IDENTITY()` properly by ensuring single connection context.

**Master Log Query Injection in trigger_function.py:**
- Symptoms: User-controlled app_name and function_name parameters are directly interpolated into SQL query without escaping
- Files: `functions/trigger_function/trigger_function.py` (lines 44-45, 288)
- Trigger: POST request with specially crafted app name like `test' OR '1'='1`
- Impact: SQL injection vulnerability. Attacker could extract schema information or manipulate function catalog.
- Fix approach: Apply `sanitize_sql_string()` to app_name and function_name before query construction in lines 44-45 and 288. Better: use parameterized queries via SQL executor API.

**Missing Error Handling in Master Log Status Endpoints:**
- Symptoms: If `log_entry["started_at"]` or `log_entry["ended_at"]` is None and needs `.isoformat()` call, KeyError or AttributeError will occur
- Files: `functions/master_services_log/status_endpoints.py` (lines 188-189, 359-360)
- Trigger: Database returns NULL datetime values
- Workaround: None—returns 500 error
- Fix approach: Add defensive checks: `(log_entry["started_at"].isoformat() if log_entry["started_at"] else None)` pattern is already used in line 188 for status endpoint, but line 359 in result endpoint calls `.isoformat()` without None check.

**Log ID Type Mismatch in Status Endpoint Route:**
- Symptoms: GET `/api/status/{log_id}` receives log_id as string but directly interpolates into SQL query without int conversion
- Files: `functions/master_services_log/status_endpoints.py` (lines 141-142, 83)
- Trigger: Caller passes non-numeric log_id
- Impact: Will fail validation in helper (lines 58-62) but validation error is silent—returns None instead of logging specific SQL error
- Fix approach: Add explicit int conversion with error handling: `int(log_id)` at line 142, catch ValueError and return 400 immediately.

## Security Considerations

**API Keys and Credentials in Function Configurations:**
- Risk: Host keys and Azure AD credentials are stored in Azure portal application settings and Key Vault references
- Files: Affected by settings in `functions/shared/settings.py` and environment variable usage throughout
- Current mitigation: Key Vault references (`@Microsoft.KeyVault(...)`) used for sensitive values in Azure portal; local.settings.json excluded via .gitignore
- Recommendations:
  - Audit all Key Vault secret names against actual Key Vault—document exact mapping
  - Add warning comment in code wherever credentials are read from environment
  - Implement rotation policy for SQL Executor client secrets (currently no documented rotation schedule)
  - Log all credential acquisition attempts (already done in `sql_client.py` lines 63-66) but monitor for repeated failures

**Sensitive Data in Error Messages and Logs:**
- Risk: Error messages containing SQL connection strings, API responses with PII, or bearer tokens may be logged
- Files: `functions/shared/seq_logging.py` (lines 98-140 has sanitization logic), but not all error paths use it
- Current mitigation: `sanitize_sensitive_data()` function redacts common patterns (passwords, tokens, keys)
- Recommendations:
  - Explicitly call `sanitize_sensitive_data()` on all caught exceptions before logging to Seq (currently only done in ServiceLogger line 92)
  - Add pre-commit hook to warn on direct `LOGGER.error()` calls without sanitization
  - Audit ServiceLogger error handling (line 220) to ensure error_message is always redacted

**Cross-Service Authorization:**
- Risk: Trigger endpoints (`trigger_by_id`, `trigger_by_name`) and status endpoints use `auth_level=func.AuthLevel.ANONYMOUS`
- Files: `functions/master_services_log/status_endpoints.py` (lines 106, 244, 413), `functions/trigger_function/trigger_function.py` (lines 114, 194)
- Impact: Anyone with function URL can query any service's status or trigger functions
- Current mitigation: None detected—endpoints are public
- Recommendations:
  - Change `auth_level` to `FUNCTION` or `ADMIN` for status and trigger endpoints
  - Implement request signing or API key validation middleware
  - Document the security model—is public access intentional for status polling by triggered services?

## Performance Bottlenecks

**Blocking Polling in Timer Function:**
- Problem: `poll_master_log_for_completion()` polls every 30 seconds (POLLING_INTERVAL) with no maximum duration
- Files: `functions/scheduler/timer_function.py` (lines 703-742)
- Cause: Long-running service blocks timer slot. If service takes 5 minutes, scheduler can't process other queued services in that time.
- Observations: With timer running every 15 minutes and scheduler potentially triggering multiple services, overlapping executions become likely if any service takes >15 minutes
- Improvement path:
  - Implement async job queue (Azure Service Bus or Queue Storage) instead of blocking in timer
  - Or: Use webhook callback pattern—target service calls scheduler endpoint when complete instead of scheduler polling
  - Or: Add configurable max polling time (e.g., 5 minutes) and auto-fail if exceeded

**SQL Connection Cold Starts on Timer:**
- Problem: Timer function creates new SQLClient per SQL operation, requiring credential exchange and token acquisition
- Files: `functions/scheduler/timer_function.py` (lines 53-107 retry logic, 280 new SQLClient context)
- Cause: One SQLClient instance per `async with` block. For 20+ scheduled services, creates 20+ separate credential objects and Azure AD token requests
- Observations: Retry logic (MAX_SQL_COLD_START_RETRIES = 3) masks this but doesn't fix root cause
- Improvement path:
  - Reuse single SQLClient instance throughout scheduler run: `async with SQLClient() as sql:` once at function start, pass to all helpers
  - Cache credential object at module level
  - Benchmark: Estimate each token acquisition = 500ms → reusing could save 10 seconds for 20 services

**N+1 Query Pattern in check_and_handle_stuck_processing_services():**
- Problem: Queries for stuck services (1 query), then loops and issues UPDATE for each stuck service (N queries)
- Files: `functions/scheduler/timer_function.py` (lines 187-241)
- Cause: Could use single UPDATE with CASE/WHEN to handle all stuck services
- Observations: With 100+ scheduled services, this becomes 100+ individual updates
- Improvement path: Rewrite stuck service check to batch update:
  ```sql
  UPDATE jgilpatrick.apps_central_scheduling
  SET status = 'failed', processed_at = GETDATE(), error_message = 'Service execution timeout'
  WHERE status = 'processing' AND (last_triggered_at IS NULL OR DATEDIFF(minute, last_triggered_at, GETDATE()) > 15)
  ```

## Fragile Areas

**Timer Schedule Configuration:**
- Files: `functions/scheduler/timer_function.py` (lines 944, 1000-1040)
- Why fragile: Timer schedule is defined in code (`"0 0,15,30,45 * * * *"`) but also has a runtime check for a magic date (Day 0 = 2000-01-01). This creates confusion: is the timer actually running or not?
- Safe modification:
  1. Remove the `datetime(2000, 1, 1)` check and rely solely on host.json schedule if timer should be disabled
  2. OR: Move schedule configuration to Azure portal HTTP Triggered endpoint so ops can control when scheduler runs
  3. Document decision clearly in CLAUDE.md and code comments
- Test coverage: No unit tests visible for timer_function.py—schedule logic untested

**Scheduled Services Query Logic:**
- Files: `functions/scheduler/timer_function.py` (lines 300-390, the `process_scheduled_services_with_overrides()` function)
- Why fragile: Complex branching logic for determining which services should run (frequency, window checks, bypass flags). Easy to trigger wrong services if conditions are misunderstood.
- Safe modification:
  1. Extract schedule evaluation logic into testable helper functions (e.g., `should_service_run(service, current_time, bypass_window_check)`)
  2. Add comprehensive unit tests for edge cases (scheduled time at boundaries, DST transitions, etc.)
  3. Document each condition with examples
- Test coverage: None visible

**Stuck Service Detection Timing:**
- Files: `functions/scheduler/timer_function.py` (lines 174-249)
- Why fragile: Hardcoded 15-minute threshold. If a service legitimately takes 15+ minutes, it will be marked failed incorrectly.
- Safe modification:
  1. Add `max_duration_minutes` field to `apps_central_scheduling` table
  2. Make threshold configurable per service
  3. Alternatively: only mark as failed if `last_triggered_at IS NULL` (never started) and created >24 hours ago
- Test coverage: None visible

**Master Log Polling Status Interpretation:**
- Files: `functions/scheduler/timer_function.py` (lines 722-734, status value mapping)
- Why fragile: Maps database status strings ("success", "failed", "warning") to HTTP-like codes. "warning" mapped to 200 (non-failure). If status changes in database schema, code must update.
- Safe modification:
  1. Define status enum in `functions/shared/enums.py`
  2. Use enum for all status checks
  3. Add migration guide for status value changes
- Test coverage: None visible

**JSON Body Parsing and Merging:**
- Files: `functions/scheduler/timer_function.py` (lines 835-841)
- Why fragile: Merges parent_service_id and root_id into service's JSON body by mutating dict. If service expects exact payload structure, extra fields could break downstream logic.
- Safe modification:
  1. Wrap parent context in separate `_context` or `metadata` key instead of root level
  2. Document that parent context will be injected
  3. Test that target service can handle extra fields
- Test coverage: None visible

## Scaling Limits

**No Horizontal Scaling of Scheduler:**
- Current capacity: Single timer instance per function app. Runs every 15 minutes.
- Limit: With 500+ scheduled services, looping through and triggering each in sequence could exceed 15-minute window. If service processing blocks for 5+ minutes, scheduler falls behind.
- Scaling path:
  1. Switch from timer + blocking polling to event-driven queue-based model (Service Bus, Queue Storage)
  2. Use function instances with in-process queuing to process services in parallel
  3. Implement partitioned scheduler (multiple timer instances with service ID ranges) if queue model not feasible
  4. Monitor: Add Application Insights metrics for "services per minute" and "scheduler backlog"

**SQL Connection Pool Exhaustion:**
- Current capacity: No connection pooling detected. Each SQLClient creates new credential + token request.
- Limit: Peak load of 20+ concurrent service triggers could exhaust available connections if target services also query SQL.
- Scaling path:
  1. Implement connection pooling via SQLAlchemy or custom async pool
  2. Reuse HTTPClient instances across requests (partial fix: wrap SQLClient in singleton with connection reuse)
  3. Monitor: Track SQL connection wait times and token acquisition latency

**Master Services Log Table Growth:**
- Current capacity: One row per service execution. 100 services × 96 executions/day = 9.6k rows/day = ~3.5M rows/year.
- Limit: Query performance degrades as table grows; full table scans become expensive; storage costs increase.
- Scaling path:
  1. Add clustering index on `(function_app, status, started_at)` for faster lookups
  2. Implement partitioning by date (monthly or quarterly)
  3. Archive rows >1 year old to separate table
  4. Add retention policy (e.g., delete rows >2 years old)

## Dependencies at Risk

**azure-identity without Version Pin:**
- Risk: `azure-identity` in `requirements.txt` (line 2) has no version specified. Automatic updates could break API compatibility.
- Current status: Works with azure-functions==1.18.0 but no contract defined.
- Impact: `ClientSecretCredential` API or token acquisition behavior could change, breaking authentication.
- Migration plan:
  1. Pin to specific version: `azure-identity>=1.14.0,<2.0` (or current stable + major constraint)
  2. Document minimum supported version in CLAUDE.md
  3. Test upgrade path before updating pinned version

**httpx without Version Pin:**
- Risk: `httpx` has no version constraint. Breaking changes in timeout handling or async behavior could occur.
- Current status: Used for HTTP requests in timer and trigger functions.
- Impact: `httpx.TimeoutException` inheritance or API changes could break error handling (lines 900-903).
- Migration plan: Pin `httpx>=0.24.0,<1.0` or current stable equivalent.

**seqlog Requirement Loose:**
- Risk: `seqlog>=0.3.28` specifies minimum but no upper bound. Major version changes (e.g., 1.0) could alter logging behavior.
- Current status: Used for structured logging to Seq.
- Impact: Seq event properties or sanitization could change, breaking monitoring dashboards.
- Migration plan: Pin to `seqlog>=0.3.28,<1.0` to prevent major version surprise.

**OpenCensus Azure Extension Deprecated:**
- Risk: `opencensus-ext-azure` is deprecated in favor of `azure-monitor-opentelemetry`
- Files: `requirements.txt` (line 8), imported implicitly by function app
- Current status: Works but no longer maintained by Microsoft
- Impact: Will not receive security updates; recommended to migrate
- Migration plan:
  1. Test `azure-monitor-opentelemetry` with fx-app-apps-services
  2. Update Application Insights integration (likely in `telemetry.py`)
  3. Deprecate `opencensus-ext-azure` in favor of new package

## Missing Critical Features

**No Circuit Breaker for Failing Services:**
- Problem: If a service consistently fails (e.g., target endpoint is down), scheduler will keep retrying every 15 minutes indefinitely
- Blocks: Services can't self-heal. Ops must manually intervene to pause service or fix target.
- Feature gap: No "disable after N consecutive failures" mechanism
- Approach: Add `consecutive_failures` counter and `disabled_at` timestamp to `apps_central_scheduling`. Auto-disable after 5 consecutive failures with alert.

**No Webhook Callback Pattern:**
- Problem: Scheduler blocks polling while waiting for services to complete. No way for async/fire-and-forget patterns.
- Blocks: Long-running services (15+ minutes) can't be properly orchestrated.
- Feature gap: Services can't notify scheduler when complete—only scheduler can pull status.
- Approach: Implement webhook endpoint where services can POST completion status instead of scheduler polling.

**No Service Dependency DAG:**
- Problem: No way to express "Service B should run only after Service A completes successfully"
- Blocks: Complex workflows requiring sequential or conditional execution can't be modeled.
- Feature gap: All services are independent. No parent-child relationship definition in `apps_central_scheduling`.
- Approach: Add `depends_on_service_id` field + workflow DAG evaluation logic. Already have parent_id in logs—extend to scheduling table.

**No Manual Service Run History UI:**
- Problem: Ops must use SQL queries to check service status. No centralized dashboard.
- Blocks: Support tickets harder to diagnose without clear status visibility.
- Feature gap: Status endpoints exist but no UI. Master services log is SQL-only.
- Approach: Create simple dashboard in React (reuse existing Keystone platform setup) to query and display master services log with filtering.

## Test Coverage Gaps

**Timer Function Untested:**
- What's not tested: Entire scheduling logic—determining which services to run, handling stuck services, executing requests, polling completion
- Files: `functions/scheduler/timer_function.py` (1,306 lines with zero visible unit tests)
- Risk: Regressions in schedule evaluation could silently cause services to skip or trigger incorrectly. No safety net for refactoring.
- Priority: HIGH—this is the core scheduler logic

**Status Endpoint Edge Cases Untested:**
- What's not tested: NULL datetime handling, missing log entries, metadata JSON parsing errors, malformed log_id values
- Files: `functions/master_services_log/status_endpoints.py` (473 lines, no unit tests visible)
- Risk: Unhandled exceptions cause 500 errors instead of graceful 400/404 responses. Example: Line 359 calls `.isoformat()` on potentially NULL datetime.
- Priority: MEDIUM—affects operational visibility but not core business logic

**Trigger Function SQL Injection Untested:**
- What's not tested: Malicious input to `app_name` and `function_name` parameters
- Files: `functions/trigger_function/trigger_function.py` (308 lines, no security tests visible)
- Risk: SQL injection vulnerability (lines 44-45, 288) would go undetected if not manually tested.
- Priority: HIGH—security concern

**SQL Sanitization Logic Untested:**
- What's not tested: Edge cases for `sanitize_sql_string()` (unicode, special characters, truncation boundaries)
- Files: `functions/shared/service_logger.py` (lines 287-312), duplicated in two other places
- Risk: Escaping bugs could allow SQL injection or data truncation issues.
- Priority: MEDIUM—affects data integrity

**Seq Logging Configuration Untested:**
- What's not tested: Sensitive data redaction patterns, Seq event format, logging under errors
- Files: `functions/shared/seq_logging.py` (800+ lines, no unit tests visible)
- Risk: Sensitive data could leak to logs if regex patterns don't match expected formats. Example: New token format not caught by pattern matching.
- Priority: MEDIUM—security/compliance concern

---

*Concerns audit: 2026-02-27*
