"""Scheduler dispatcher — evaluates due services and fires them.

Refactored to be a pure dispatcher (Phase 3 of scheduler-simplification).
Retry logic has been removed; a failed run simply waits for its next
scheduled occurrence. Long-running async jobs (202 Accepted) are handed off
to the job manager via a 'dispatched' execution-log row.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
import pytz

import azure.functions as func
import httpx

from ..shared.settings import get_settings
from ..shared.sql_client import SQLClient
from ..shared.telemetry import track_event, track_exception
from ..shared.master_service_logger import MasterServiceLogger

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SERVICE_REQUEST_TIMEOUT = 300  # 5 min timeout per HTTP POST
SQL_COLD_START_RETRY_DELAY = 5
MAX_SQL_COLD_START_RETRIES = 3


# ---------------------------------------------------------------------------
# Helpers (pure utilities, no retry/state logic)
# ---------------------------------------------------------------------------

def get_eastern_time_sql() -> str:
    """SQL expression for current Eastern time accounting for DST."""
    eastern = pytz.timezone('US/Eastern')
    current_eastern = datetime.now(eastern)
    offset_seconds = current_eastern.utcoffset().total_seconds()
    offset_hours = int(offset_seconds / 3600)
    if offset_hours >= 0:
        offset_str = f"+{offset_hours:02d}:00"
    else:
        offset_str = f"{offset_hours:03d}:00"
    return f"CONVERT(datetime, SWITCHOFFSET(SYSDATETIMEOFFSET(), '{offset_str}'))"


async def execute_sql_with_cold_start_retry(
    sql_client: SQLClient, sql: str, method: str = "query", title: str = None
) -> Any:
    """Execute SQL with retry logic for SQL server cold starts."""
    retry_count = 0
    last_exception = None

    while retry_count <= MAX_SQL_COLD_START_RETRIES:
        try:
            result = await sql_client.execute(sql, method=method, title=title)
            return result
        except Exception as e:
            last_exception = e
            error_msg = str(e).lower()
            cold_start_indicators = [
                "connection timeout", "timeout expired", "server is not responding",
                "connection was closed", "login timeout", "connection reset",
                "network-related", "provider: tcp provider", "error: 2",
            ]
            is_cold_start = any(ind in error_msg for ind in cold_start_indicators)
            if is_cold_start and retry_count < MAX_SQL_COLD_START_RETRIES:
                retry_count += 1
                LOGGER.warning(
                    f"SQL cold start retry {retry_count}/{MAX_SQL_COLD_START_RETRIES} in {SQL_COLD_START_RETRY_DELAY}s"
                )
                await asyncio.sleep(SQL_COLD_START_RETRY_DELAY)
                continue
            raise e

    raise last_exception


def sanitize_sql_string(value: str, max_length: int = 3900) -> str:
    """Sanitize a string for safe SQL insertion."""
    if not value:
        return ""
    truncated = value[:max_length]
    sanitized = truncated.replace("'", "''")
    sanitized = sanitized.replace("\x00", "")
    sanitized = sanitized.replace("\\", "\\\\")
    return sanitized


def is_within_schedule_window(
    current_time: datetime, scheduled_time_str: str, window_minutes: int = 15
) -> bool:
    """Check if current time falls in the same 15-minute window as scheduled_time_str."""
    try:
        hour, minute = map(int, scheduled_time_str.split(':'))
        if current_time.hour != hour:
            return False
        return current_time.minute // window_minutes == minute // window_minutes
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Execution log writer
# ---------------------------------------------------------------------------

async def log_execution(
    sql_client: SQLClient,
    schedule_id: int,
    function_app: str,
    service_name: str,
    triggered_at: datetime,
    status: str,
    http_status_code: Optional[int] = None,
    request_payload: Optional[str] = None,
    response_detail: Optional[str] = None,
    error_message: Optional[str] = None,
    trigger_source: Optional[str] = None,
    log_id: Optional[int] = None,
) -> None:
    """Insert a row into apps_scheduler_execution_log."""
    try:
        eastern = pytz.timezone("US/Eastern")
        now = datetime.now(eastern)
        # For 'dispatched' status, completed_at and duration are set later by job manager
        if status == "dispatched":
            completed_at_val = "NULL"
            duration_ms_val = "NULL"
        else:
            completed_at_val = f"'{now.strftime('%Y-%m-%d %H:%M:%S.%f')}'"
            duration_ms_val = str(int((now - triggered_at).total_seconds() * 1000))

        fa = sanitize_sql_string(function_app)
        sn = sanitize_sql_string(service_name)
        stat = sanitize_sql_string(status)
        req_payload = f"'{sanitize_sql_string(request_payload)}'" if request_payload else "NULL"
        resp_detail = f"'{sanitize_sql_string(response_detail)}'" if response_detail else "NULL"
        err_msg = f"'{sanitize_sql_string(error_message)}'" if error_message else "NULL"
        http_code = str(http_status_code) if http_status_code is not None else "NULL"
        lid = str(log_id) if log_id is not None else "NULL"
        ts_val = f"'{sanitize_sql_string(trigger_source)}'" if trigger_source else "NULL"

        insert_sql = f"""
            INSERT INTO jgilpatrick.apps_scheduler_execution_log
                (schedule_id, function_app, service_name, triggered_at, completed_at,
                 duration_ms, status, http_status_code, request_payload, response_detail,
                 error_message, trigger_source, log_id, retry_attempt)
            VALUES
                ({schedule_id}, '{fa}', '{sn}',
                 '{triggered_at.strftime("%Y-%m-%d %H:%M:%S.%f")}',
                 {completed_at_val}, {duration_ms_val},
                 '{stat}', {http_code}, {req_payload}, {resp_detail},
                 {err_msg}, {ts_val}, {lid}, 0)
        """
        await execute_sql_with_cold_start_retry(
            sql_client, insert_sql, method="execute",
            title=f"Log execution for schedule {schedule_id} ({status})"
        )
    except Exception as e:
        LOGGER.error(f"Failed to log execution for schedule {schedule_id}: {e}")


# ---------------------------------------------------------------------------
# Stuck-service recovery (simplified — no retry, just unstick)
# ---------------------------------------------------------------------------

async def unstick_stale_processing_services(sql_client: SQLClient) -> int:
    """Reset services stuck in 'processing' beyond their max_execution_minutes.

    No retry scheduling — just resets to 'pending' so the next scheduled
    occurrence fires normally.
    """
    eastern_time_sql = get_eastern_time_sql()

    stuck_sql = f"""
        SELECT id, function_app, service, last_triggered_at,
               COALESCE(max_execution_minutes, 30) AS timeout_min
        FROM jgilpatrick.apps_central_scheduling
        WHERE status = 'processing'
        AND (
            last_triggered_at IS NULL
            OR DATEDIFF(minute, last_triggered_at, {eastern_time_sql}) >
               COALESCE(max_execution_minutes, 30)
        )
    """

    try:
        stuck = await execute_sql_with_cold_start_retry(
            sql_client, stuck_sql, method="query",
            title="Check for stuck processing services"
        )

        if not stuck or not isinstance(stuck, list):
            return 0

        count = len(stuck)
        if count > 0:
            LOGGER.warning(f"Found {count} stuck-processing services — resetting to pending")

        for svc in stuck:
            sid = svc["id"]
            LOGGER.warning(
                f"  Unsticking service {sid} ({svc['function_app']}/{svc['service']}) "
                f"last_triggered_at={svc['last_triggered_at']}"
            )
            await execute_sql_with_cold_start_retry(
                sql_client,
                f"""
                UPDATE jgilpatrick.apps_central_scheduling
                SET status = 'pending',
                    processed_at = {eastern_time_sql},
                    last_response_code = 408,
                    error_message = 'Reset from stuck processing (exceeded timeout)'
                WHERE id = {sid} AND status = 'processing'
                """,
                method="execute",
                title=f"Unstick service {sid}"
            )
        return count

    except Exception as e:
        LOGGER.error(f"Error checking stuck services: {e}")
        return 0


# ---------------------------------------------------------------------------
# Schedule evaluation (pure — no retry/next_retry_at logic)
# ---------------------------------------------------------------------------

async def should_trigger_service(
    service: Dict[str, Any], current_time: datetime
) -> bool:
    """Determine if a service should fire based on its schedule. Window check ON."""
    return await _evaluate_schedule(service, current_time, check_window=True)


async def should_trigger_service_bypass_window(
    service: Dict[str, Any], current_time: datetime, check_window: bool = False
) -> bool:
    """Determine if a service should fire, optionally bypassing window checks."""
    return await _evaluate_schedule(service, current_time, check_window=check_window)


async def _evaluate_schedule(
    service: Dict[str, Any], current_time: datetime, check_window: bool = True
) -> bool:
    """Core schedule evaluation — pure declarative, no retry state.

    Checks: start_date gating, frequency, schedule_config time windows,
    and same-period dedup via last_triggered_at.
    """
    frequency = service["frequency"]
    last_triggered = service["last_triggered_at"]
    start_date = service["start_date"]
    schedule_config = service.get("schedule_config")

    eastern = pytz.timezone('US/Eastern')

    # --- Parse last_triggered ---
    if last_triggered:
        last_triggered = _parse_eastern_datetime(last_triggered, eastern)

    # --- Parse start_date ---
    start_date = _parse_eastern_datetime(start_date, eastern)

    # Universal gate: don't run before start_date
    if start_date > current_time:
        return False

    # --- Frequency handlers ---
    if frequency == "once":
        return last_triggered is None

    elif frequency == "daily":
        return _check_daily(schedule_config, service, last_triggered, current_time, check_window)

    elif frequency == "weekly":
        return _check_weekly(schedule_config, service, last_triggered, current_time, check_window)

    elif frequency == "hourly":
        return _check_hourly(schedule_config, service, last_triggered, current_time, check_window)

    elif frequency == "monthly":
        return _check_monthly(schedule_config, service, last_triggered, current_time, check_window)

    # Unknown frequency
    return False


def _parse_eastern_datetime(value, eastern) -> datetime:
    """Parse a datetime value, assuming Eastern when no timezone info."""
    if isinstance(value, str):
        try:
            if 'Z' in value or '+' in value or value.count('-') > 2:
                utc_time = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return utc_time.astimezone(eastern)
            else:
                dt = datetime.fromisoformat(value)
                return eastern.localize(dt)
        except (ValueError, AttributeError):
            dt = datetime.fromisoformat(value)
            return eastern.localize(dt)
    elif hasattr(value, 'tzinfo'):
        if value.tzinfo is None:
            return eastern.localize(value)
        return value
    return value


def _check_daily(config_str, service, last_triggered, current_time, check_window) -> bool:
    if not config_str:
        return False
    try:
        config = json.loads(config_str)
        times = config.get("times", ["00:00"])

        if check_window:
            if not any(is_within_schedule_window(current_time, t) for t in times):
                return False

        # Same-day dedup
        if last_triggered:
            return last_triggered.date() < current_time.date()
        return True
    except (json.JSONDecodeError, KeyError):
        LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {config_str}")
        return False


def _check_weekly(config_str, service, last_triggered, current_time, check_window) -> bool:
    if not config_str:
        return False
    try:
        config = json.loads(config_str)
        days = config.get("days", ["monday"])
        time_str = config.get("time", "00:00")

        current_day = current_time.strftime("%A").lower()
        if current_day not in [d.lower() for d in days]:
            return False

        if check_window and not is_within_schedule_window(current_time, time_str):
            return False

        if last_triggered:
            return last_triggered.date() != current_time.date()
        return True
    except (json.JSONDecodeError, KeyError):
        LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {config_str}")
        return False


def _check_hourly(config_str, service, last_triggered, current_time, check_window) -> bool:
    if not config_str:
        return False
    try:
        config = json.loads(config_str)

        if "minutes" in config:
            minutes = config["minutes"]
            if not isinstance(minutes, list):
                minutes = [minutes]
        else:
            minutes = [config.get("minute", 0)]

        if check_window and current_time.minute not in minutes:
            return False

        if last_triggered:
            same_hour_same_date = (
                last_triggered.hour == current_time.hour
                and last_triggered.date() == current_time.date()
            )
            if same_hour_same_date:
                return last_triggered.minute != current_time.minute
            return True
        return True
    except (json.JSONDecodeError, KeyError):
        LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {config_str}")
        return False


def _check_monthly(config_str, service, last_triggered, current_time, check_window) -> bool:
    if not config_str:
        return False
    try:
        config = json.loads(config_str)
        day = config.get("day", 1)
        time_str = config.get("time", "00:00")

        if current_time.day != day:
            return False
        if check_window and not is_within_schedule_window(current_time, time_str):
            return False

        if last_triggered:
            return (last_triggered.month != current_time.month
                    or last_triggered.year != current_time.year)
        return True
    except (json.JSONDecodeError, KeyError):
        LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {config_str}")
        return False


# ---------------------------------------------------------------------------
# HTTP execution (fire-and-forget for 202)
# ---------------------------------------------------------------------------

async def execute_service_request(
    service: Dict[str, Any],
    master_logger: Optional[MasterServiceLogger] = None,
) -> tuple[bool, int, str, Optional[int]]:
    """Fire the HTTP POST to the service trigger URL.

    Returns (success, response_code, response_detail, log_id).
    For 202 Accepted, returns (True, 202, detail, log_id) — caller writes
    a 'dispatched' execution-log row and the job manager polls it to completion.
    """
    service_id = service["id"]
    trigger_url = service["trigger_url"]
    json_body = service["json_body"]

    try:
        parsed_json = json.loads(json_body) if isinstance(json_body, str) else json_body

        if master_logger and master_logger.log_id is not None:
            child_context = master_logger.get_child_context()
            parsed_json.update(child_context)

    except json.JSONDecodeError as e:
        return False, 400, f"Invalid JSON body: {e}", None

    try:
        async with httpx.AsyncClient(timeout=SERVICE_REQUEST_TIMEOUT) as client:
            response = await client.post(
                trigger_url, json=parsed_json,
                headers={"Content-Type": "application/json"},
            )
            response_code = response.status_code
            try:
                response_detail = response.text[:4000]
            except Exception:
                response_detail = "Unable to read response body"

            # Extract log_id from response JSON
            log_id = None
            try:
                body_json = response.json()
                if isinstance(body_json, dict):
                    raw = body_json.get("log_id")
                    if raw is not None:
                        log_id = int(raw)
            except Exception:
                pass

            if response_code == 202:
                if log_id is None:
                    LOGGER.error(
                        f"Service {service_id} returned 202 but no log_id in response"
                    )
                    return False, 500, "202 Accepted but missing log_id", None
                LOGGER.info(
                    f"Service {service_id} returned 202 — dispatched (log_id: {log_id})"
                )
                return True, 202, response_detail, log_id

            elif 200 <= response_code < 300:
                LOGGER.info(f"Service {service_id} succeeded: HTTP {response_code}")
                return True, response_code, response_detail, log_id

            else:
                LOGGER.error(f"Service {service_id} failed: HTTP {response_code}")
                return False, response_code, response_detail, log_id

    except httpx.TimeoutException:
        return False, 408, f"HTTP timeout after {SERVICE_REQUEST_TIMEOUT}s", None
    except Exception as e:
        return False, 500, f"HTTP error: {e}", None


def get_next_status(service: Dict[str, Any]) -> str:
    """Next central-scheduling status after successful execution."""
    if service["frequency"] == "once":
        return "completed"
    tl = service["trigger_limit"]
    tc = service["triggered_count"]
    if tl and tc + 1 >= tl:
        return "completed"
    return "pending"


# ---------------------------------------------------------------------------
# Core dispatcher
# ---------------------------------------------------------------------------

async def process_scheduled_services_with_overrides(
    bypass_window_check: bool = False,
    force_service_ids: list[int] = None,
    master_logger: Optional[MasterServiceLogger] = None,
) -> Dict[str, Any]:
    """Evaluate active schedules and fire due services.

    This is a pure dispatcher: it fires each service at most once per
    scheduled occurrence, records the attempt in the execution log, and
    moves on. No retry logic. Async jobs (202) get a 'dispatched' log
    row for the job manager to poll.
    """
    results = {
        "triggered_services": [],
        "processed": 0,
        "successful": 0,
        "failed": 0,
        "skipped": 0,
        "dispatched": 0,
        "stuck_services_found": 0,
        "errors": [],
    }

    async with SQLClient() as sql_client:
        try:
            eastern = pytz.timezone('US/Eastern')
            current_time = datetime.now(eastern)

            # Unstick any services stuck in 'processing' past their timeout
            LOGGER.info("Checking for stuck processing services...")
            stuck_count = await unstick_stale_processing_services(sql_client)
            results["stuck_services_found"] = stuck_count

            # Build fetch query — status='pending' only (no 'failed')
            base_cols = """
                id, function_app, service, trigger_url, json_body,
                start_date, frequency, schedule_config,
                triggered_count, trigger_limit, last_triggered_at
            """

            if force_service_ids:
                ids_str = ','.join(map(str, force_service_ids))
                if bypass_window_check:
                    fetch_sql = f"""
                        SELECT {base_cols}
                        FROM jgilpatrick.apps_central_scheduling
                        WHERE is_active = 1 AND id IN ({ids_str})
                        ORDER BY start_date ASC
                    """
                else:
                    fetch_sql = f"""
                        SELECT {base_cols}
                        FROM jgilpatrick.apps_central_scheduling
                        WHERE is_active = 1
                        AND status = 'pending'
                        AND (trigger_limit IS NULL OR triggered_count < trigger_limit)
                        AND id IN ({ids_str})
                        ORDER BY start_date ASC
                    """
            else:
                fetch_sql = f"""
                    SELECT {base_cols}
                    FROM jgilpatrick.apps_central_scheduling
                    WHERE is_active = 1
                    AND status = 'pending'
                    AND (trigger_limit IS NULL OR triggered_count < trigger_limit)
                    ORDER BY start_date ASC
                """

            services = await execute_sql_with_cold_start_retry(
                sql_client, fetch_sql, method="query",
                title="Fetch active scheduled services"
            )

            if not services or not isinstance(services, list):
                LOGGER.info("No active services to evaluate")
                return results

            LOGGER.info(f"Evaluating {len(services)} active services")

            for service in services:
                service_id = service["id"]
                function_app = service["function_app"]
                service_name = service["service"]

                results["processed"] += 1

                try:
                    # Determine if this service should fire now
                    if force_service_ids and service_id in force_service_ids:
                        should_fire = True
                        LOGGER.info(f"  Service {service_id}: FORCED execution")
                    elif bypass_window_check:
                        should_fire = await should_trigger_service_bypass_window(
                            service, current_time
                        )
                    else:
                        should_fire = await should_trigger_service(service, current_time)

                    if not should_fire:
                        results["skipped"] += 1
                        continue

                    # Check trigger limit
                    if (service["trigger_limit"]
                            and service["triggered_count"] >= service["trigger_limit"]):
                        LOGGER.info(f"  Service {service_id}: trigger limit reached")
                        await execute_sql_with_cold_start_retry(
                            sql_client,
                            f"UPDATE jgilpatrick.apps_central_scheduling "
                            f"SET status = 'completed' WHERE id = {service_id}",
                            method="execute",
                            title=f"Mark service {service_id} completed (limit)"
                        )
                        results["skipped"] += 1
                        continue

                    # --- Atomic claim ---
                    _claim_ts = datetime.now(eastern).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    claim_ts_sql = f"'{_claim_ts}'"
                    await execute_sql_with_cold_start_retry(
                        sql_client,
                        f"""
                        UPDATE jgilpatrick.apps_central_scheduling
                        SET status = 'processing',
                            last_triggered_at = {claim_ts_sql}
                        WHERE id = {service_id}
                        AND status = 'pending'
                        """,
                        method="execute",
                        title=f"Claim service {service_id}"
                    )
                    claim_check = await execute_sql_with_cold_start_retry(
                        sql_client,
                        f"""
                        SELECT id FROM jgilpatrick.apps_central_scheduling
                        WHERE id = {service_id}
                        AND status = 'processing'
                        AND last_triggered_at = {claim_ts_sql}
                        """,
                        method="query",
                        title=f"Verify claim {service_id}"
                    )
                    if not claim_check or not isinstance(claim_check, list) or len(claim_check) == 0:
                        LOGGER.warning(f"  Service {service_id}: already claimed, skipping")
                        results["skipped"] += 1
                        continue

                    results["triggered_services"].append({
                        "function_app": function_app,
                        "service": service_name,
                    })

                    exec_triggered_at = datetime.now(eastern)
                    trigger_src = (
                        getattr(master_logger, "trigger_source", "timer")
                        if master_logger else "timer"
                    )

                    # --- Fire the HTTP request ---
                    success, response_code, response_detail, log_id = (
                        await execute_service_request(service, master_logger)
                    )

                    eastern_time_sql = get_eastern_time_sql()
                    sanitized_detail = sanitize_sql_string(response_detail or "")
                    log_id_value = str(log_id) if log_id is not None else "NULL"

                    if success and response_code == 202:
                        # --- Async job: write 'dispatched', let job manager poll ---
                        next_status = get_next_status(service)
                        await execute_sql_with_cold_start_retry(
                            sql_client,
                            f"""
                            UPDATE jgilpatrick.apps_central_scheduling
                            SET status = '{next_status}',
                                triggered_count = triggered_count + 1,
                                last_response_code = 202,
                                last_response_detail = '{sanitized_detail}',
                                processed_at = {eastern_time_sql},
                                error_message = NULL,
                                log_id = {log_id_value}
                            WHERE id = {service_id}
                            """,
                            method="execute",
                            title=f"Update service {service_id} — dispatched"
                        )
                        await log_execution(
                            sql_client, schedule_id=service_id,
                            function_app=function_app, service_name=service_name,
                            triggered_at=exec_triggered_at, status="dispatched",
                            http_status_code=202,
                            request_payload=service.get("json_body"),
                            response_detail=response_detail,
                            trigger_source=trigger_src, log_id=log_id,
                        )
                        results["dispatched"] += 1
                        LOGGER.info(
                            f"  Service {service_id}: dispatched (202, log_id={log_id})"
                        )

                    elif success:
                        # --- Synchronous success ---
                        next_status = get_next_status(service)
                        await execute_sql_with_cold_start_retry(
                            sql_client,
                            f"""
                            UPDATE jgilpatrick.apps_central_scheduling
                            SET status = '{next_status}',
                                triggered_count = triggered_count + 1,
                                last_triggered_at = {eastern_time_sql},
                                last_response_code = {response_code},
                                last_response_detail = '{sanitized_detail}',
                                processed_at = {eastern_time_sql},
                                error_message = NULL,
                                log_id = {log_id_value}
                            WHERE id = {service_id}
                            """,
                            method="execute",
                            title=f"Mark service {service_id} successful"
                        )
                        await log_execution(
                            sql_client, schedule_id=service_id,
                            function_app=function_app, service_name=service_name,
                            triggered_at=exec_triggered_at, status="success",
                            http_status_code=response_code,
                            request_payload=service.get("json_body"),
                            response_detail=response_detail,
                            trigger_source=trigger_src, log_id=log_id,
                        )
                        results["successful"] += 1
                        LOGGER.info(
                            f"  Service {service_id}: success (HTTP {response_code})"
                        )
                        await track_event("scheduler_service_success", {
                            "service_id": service_id,
                            "function_app": function_app,
                            "service": service_name,
                            "response_code": response_code,
                            "log_id": log_id,
                        })

                    else:
                        # --- Failure: reset to pending, no retry scheduling ---
                        err_msg_sql = sanitize_sql_string(
                            f"Service failed with HTTP {response_code}"
                        )
                        await execute_sql_with_cold_start_retry(
                            sql_client,
                            f"""
                            UPDATE jgilpatrick.apps_central_scheduling
                            SET status = 'pending',
                                processed_at = {eastern_time_sql},
                                last_response_code = {response_code},
                                last_response_detail = '{sanitized_detail}',
                                error_message = '{err_msg_sql}',
                                log_id = {log_id_value}
                            WHERE id = {service_id}
                            """,
                            method="execute",
                            title=f"Mark service {service_id} failed (no retry)"
                        )
                        await log_execution(
                            sql_client, schedule_id=service_id,
                            function_app=function_app, service_name=service_name,
                            triggered_at=exec_triggered_at, status="failed",
                            http_status_code=response_code,
                            request_payload=service.get("json_body"),
                            response_detail=response_detail,
                            error_message=response_detail[:2000] if response_detail else None,
                            trigger_source=trigger_src, log_id=log_id,
                        )
                        results["failed"] += 1
                        LOGGER.error(
                            f"  Service {service_id}: failed (HTTP {response_code})"
                        )

                except Exception as e:
                    error_msg = str(e)
                    results["failed"] += 1
                    results["errors"].append(
                        f"Service {service_id} ({function_app}/{service_name}): {error_msg}"
                    )

                    # Best-effort: reset to pending
                    try:
                        await execute_sql_with_cold_start_retry(
                            sql_client,
                            f"""
                            UPDATE jgilpatrick.apps_central_scheduling
                            SET status = 'pending',
                                processed_at = {get_eastern_time_sql()},
                                error_message = '{sanitize_sql_string(error_msg)}'
                            WHERE id = {service_id}
                            """,
                            method="execute",
                            title=f"Reset service {service_id} after exception"
                        )
                    except Exception:
                        pass

                    # Best-effort execution log
                    try:
                        await log_execution(
                            sql_client, schedule_id=service_id,
                            function_app=function_app, service_name=service_name,
                            triggered_at=datetime.now(eastern), status="error",
                            request_payload=service.get("json_body"),
                            error_message=error_msg[:2000],
                            trigger_source="timer",
                        )
                    except Exception:
                        pass

                    LOGGER.error(f"  Service {service_id}: exception — {error_msg}")
                    await track_exception(e, {
                        "service_id": service_id,
                        "function_app": function_app,
                        "service": service_name,
                    })

        except Exception as e:
            results["errors"].append(f"Scheduler error: {e}")
            LOGGER.error(f"Error in scheduler dispatcher: {e}")
            await track_exception(e, {"operation": "scheduler_dispatcher"})

    return results


# Keep legacy alias for callers
async def process_scheduled_services() -> Dict[str, Any]:
    return await process_scheduled_services_with_overrides()


# ---------------------------------------------------------------------------
# Blueprint & entry points
# ---------------------------------------------------------------------------

bp = func.Blueprint()


@bp.timer_trigger(schedule="0 0,15,30,45 * * * *", arg_name="timer", run_on_startup=False)
async def scheduler_timer(timer: func.TimerRequest) -> None:
    """Timer dispatcher — runs every 15 minutes, fires due services."""
    LOGGER.info("Scheduler dispatcher started")

    master_logger = MasterServiceLogger(
        service_name="scheduler_timer",
        function_app="apps_services",
        trigger_source="timer",
    )

    async with SQLClient() as sql_client:
        try:
            await track_event("scheduler_timer_started", {
                "is_past_due": timer.past_due,
            })

            if timer.past_due:
                LOGGER.warning("Timer is running late")

            results = await process_scheduled_services_with_overrides(
                master_logger=master_logger
            )

            summary = (
                f"Scheduler completed — "
                f"Processed: {results['processed']}, "
                f"Successful: {results['successful']}, "
                f"Failed: {results['failed']}, "
                f"Dispatched: {results['dispatched']}, "
                f"Skipped: {results['skipped']}, "
                f"Stuck reset: {results['stuck_services_found']}"
            )
            LOGGER.info(summary)

            await track_event("scheduler_timer_completed", results)

            # Only write master log if work was done
            if results['successful'] > 0 or results['failed'] > 0 or results['dispatched'] > 0:
                await master_logger.log_start(
                    sql_client,
                    request_data=json.dumps({"is_past_due": timer.past_due}),
                    metadata={"schedule": "0 0,15,30,45 * * * *"},
                )

                if results["errors"]:
                    await master_logger.log_warning(
                        sql_client,
                        f"Completed with {len(results['errors'])} errors",
                        response_data=json.dumps(results),
                    )
                else:
                    await master_logger.log_success(
                        sql_client,
                        response_data=json.dumps(results),
                    )
            else:
                LOGGER.info("No services triggered — skipping master log entry")

        except Exception as e:
            LOGGER.error(f"Scheduler timer failed: {e}")
            try:
                await master_logger.log_error(sql_client, str(e))
            except Exception:
                pass
            await track_exception(e, {"operation": "scheduler_timer"})
            raise


@bp.route(route="scheduler/manual-trigger", methods=["POST"])
async def scheduler_http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP trigger for manual scheduler execution.

    POST /api/scheduler/manual-trigger

    Optional JSON body:
    {
        "bypass_window_check": false,
        "force_service_ids": [1, 2, 3],
        "schedule_id": 1
    }
    """
    start_time = time.time()
    LOGGER.info("HTTP scheduler trigger started")

    master_logger = MasterServiceLogger(
        service_name="scheduler_http_trigger",
        function_app="apps_services",
        trigger_source="HTTP",
    )

    async with SQLClient() as sql_client:
        try:
            await master_logger.log_start(
                sql_client,
                request_data=json.dumps({
                    "method": req.method,
                    "url": str(req.url),
                }),
                metadata={"endpoint": "scheduler/manual-trigger"},
            )

            # Parse options
            bypass_window_check = False
            force_service_ids = None
            schedule_id = None

            try:
                if req.get_body():
                    body = req.get_json()
                    if body:
                        bypass_window_check = body.get("bypass_window_check", False)
                        force_service_ids = body.get("force_service_ids")
                        schedule_id = body.get("schedule_id")
            except (ValueError, TypeError):
                pass

            # Execute
            if schedule_id:
                results = await process_scheduled_services_with_overrides(
                    bypass_window_check=True,
                    force_service_ids=[schedule_id],
                    master_logger=master_logger,
                )
            else:
                results = await process_scheduled_services_with_overrides(
                    bypass_window_check=bypass_window_check,
                    force_service_ids=force_service_ids,
                    master_logger=master_logger,
                )

            execution_time = time.time() - start_time

            await track_event("scheduler_http_completed", {
                **results,
                "execution_time_seconds": execution_time,
            })

            response_data = {
                "success": True,
                "message": "Scheduler executed successfully",
                "results": results,
                "execution_time_seconds": round(execution_time, 2),
                "master_log_id": master_logger.log_id,
            }

            if results["errors"]:
                await master_logger.log_warning(
                    sql_client,
                    f"Completed with {len(results['errors'])} errors",
                    response_data=json.dumps(response_data),
                )
            else:
                await master_logger.log_success(
                    sql_client,
                    response_data=json.dumps(response_data),
                )

            return func.HttpResponse(
                json.dumps(response_data, indent=2),
                status_code=200,
                mimetype="application/json",
            )

        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"HTTP scheduler failed: {e}"
            LOGGER.error(error_msg)

            try:
                await master_logger.log_error(sql_client, error_msg)
            except Exception:
                pass

            await track_exception(e, {"operation": "scheduler_http_trigger"})

            return func.HttpResponse(
                json.dumps({
                    "success": False,
                    "message": error_msg,
                    "execution_time_seconds": round(execution_time, 2),
                }, indent=2),
                status_code=500,
                mimetype="application/json",
            )
