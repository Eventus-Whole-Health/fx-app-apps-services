"""Job manager: polls dispatched scheduler jobs to terminal state.

Runs on a 2-minute timer. For each row in apps_scheduler_execution_log with
status='dispatched' and a non-null log_id, checks apps_master_services_log
for terminal state.  If terminal, writes back to both execution_log and
apps_central_scheduling (receipt columns only).  If the job has exceeded its
max_execution_minutes, marks it as 'timeout'.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import azure.functions as func
import pytz

from ..shared.sql_client import SQLClient
from ..shared.telemetry import track_event, track_exception

LOGGER = logging.getLogger(__name__)

# Terminal statuses in apps_master_services_log
TERMINAL_STATUSES = ("success", "failed", "warning")


def get_eastern_time_sql() -> str:
    """Return a SQL expression for the current Eastern time (DST-aware)."""
    eastern = pytz.timezone("US/Eastern")
    current_eastern = datetime.now(eastern)

    offset_seconds = current_eastern.utcoffset().total_seconds()
    offset_hours = int(offset_seconds / 3600)

    if offset_hours >= 0:
        offset_str = f"+{offset_hours:02d}:00"
    else:
        offset_str = f"{offset_hours:03d}:00"

    return f"CONVERT(datetime, SWITCHOFFSET(SYSDATETIMEOFFSET(), '{offset_str}'))"


def sanitize_sql_string(value: str, max_length: int = 3900) -> str:
    """Sanitize a string for safe SQL insertion.

    Truncates to *max_length* before escaping so the final value stays
    within column limits.
    """
    if not value:
        return ""
    truncated = value[:max_length]
    sanitized = truncated.replace("'", "''")
    sanitized = sanitized.replace("\x00", "")
    sanitized = sanitized.replace("\\", "\\\\")
    return sanitized


async def process_dispatched_jobs() -> Dict[str, Any]:
    """Check all dispatched jobs and reconcile their terminal state.

    Uses a single JOIN query to fetch dispatched execution-log rows together
    with their master-log status and the per-schedule timeout, avoiding N+1
    round-trips.
    """
    eastern = pytz.timezone("US/Eastern")
    now_eastern = datetime.now(eastern)
    eastern_time_sql = get_eastern_time_sql()

    stats: Dict[str, Any] = {
        "checked": 0,
        "completed": 0,
        "timed_out": 0,
        "still_running": 0,
        "errors": [],
    }

    async with SQLClient() as sql:
        # ----------------------------------------------------------------
        # Single query: dispatched rows + master log status + timeout config
        # ----------------------------------------------------------------
        fetch_sql = f"""
            SELECT
                el.execution_id,
                el.schedule_id,
                el.log_id,
                el.function_app,
                el.service_name,
                el.triggered_at,
                cs.max_execution_minutes,
                msl.status   AS master_status,
                msl.error_message AS master_error,
                DATEDIFF(MINUTE, el.triggered_at, {eastern_time_sql}) AS elapsed_min
            FROM jgilpatrick.apps_scheduler_execution_log el
            JOIN jgilpatrick.apps_central_scheduling cs
                ON el.schedule_id = cs.id
            LEFT JOIN jgilpatrick.apps_master_services_log msl
                ON el.log_id = msl.log_id
            WHERE el.status = 'dispatched'
              AND el.log_id IS NOT NULL
        """

        rows = await sql.execute(fetch_sql, method="query", title="Fetch dispatched jobs")

        if not rows or not isinstance(rows, list):
            LOGGER.info("Job manager: no dispatched jobs to check")
            return stats

        stats["checked"] = len(rows)
        LOGGER.info(f"Job manager: checking {len(rows)} dispatched job(s)")

        for row in rows:
            execution_id = row["execution_id"]
            schedule_id = row["schedule_id"]
            log_id = row["log_id"]
            fn_app = row["function_app"]
            svc_name = row["service_name"]
            master_status = (row.get("master_status") or "").lower().strip()
            master_error = row.get("master_error") or ""
            max_exec_min = row.get("max_execution_minutes") or 30
            elapsed_min = row.get("elapsed_min") or 0

            try:
                # ----------------------------------------------------------
                # Path A: master log reached terminal state
                # ----------------------------------------------------------
                if master_status in TERMINAL_STATUSES:
                    completed_at_sql = eastern_time_sql
                    # Compute duration from triggered_at to now
                    triggered_at_raw = row.get("triggered_at")
                    duration_ms = _compute_duration_ms(triggered_at_raw, now_eastern)

                    # Derive HTTP-like code for receipt
                    if master_status == "success":
                        response_code = 200
                    elif master_status == "warning":
                        response_code = 200
                    else:
                        response_code = 500

                    safe_error = sanitize_sql_string(master_error)
                    safe_detail = sanitize_sql_string(
                        f"Master log terminal: {master_status}"
                        + (f" - {master_error}" if master_error else "")
                    )

                    # Update execution_log to terminal
                    update_exec_sql = f"""
                        UPDATE jgilpatrick.apps_scheduler_execution_log
                        SET status = '{master_status}',
                            completed_at = {completed_at_sql},
                            duration_ms = {duration_ms},
                            response_detail = '{safe_detail}',
                            http_status_code = {response_code},
                            error_message = {f"'{safe_error}'" if safe_error else "NULL"}
                        WHERE execution_id = {execution_id}
                    """
                    await sql.execute(
                        update_exec_sql, method="execute",
                        title=f"Mark execution {execution_id} as {master_status}",
                    )

                    # Update central_scheduling receipt columns
                    update_central_sql = f"""
                        UPDATE jgilpatrick.apps_central_scheduling
                        SET last_response_code = {response_code},
                            last_response_detail = '{safe_detail}',
                            log_id = {log_id},
                            error_message = {f"'{safe_error}'" if safe_error else "NULL"}
                        WHERE id = {schedule_id}
                    """
                    await sql.execute(
                        update_central_sql, method="execute",
                        title=f"Update central receipt for schedule {schedule_id}",
                    )

                    stats["completed"] += 1
                    LOGGER.info(
                        f"  Job {execution_id} ({fn_app}/{svc_name}): "
                        f"terminal '{master_status}' after {elapsed_min}m"
                    )

                # ----------------------------------------------------------
                # Path B: not terminal, check timeout
                # ----------------------------------------------------------
                elif elapsed_min > max_exec_min:
                    triggered_at_raw = row.get("triggered_at")
                    duration_ms = _compute_duration_ms(triggered_at_raw, now_eastern)
                    timeout_detail = sanitize_sql_string(
                        f"Job exceeded max_execution_minutes ({max_exec_min}); "
                        f"elapsed {elapsed_min}m"
                    )

                    # Mark execution_log as timeout
                    update_exec_sql = f"""
                        UPDATE jgilpatrick.apps_scheduler_execution_log
                        SET status = 'timeout',
                            completed_at = {eastern_time_sql},
                            duration_ms = {duration_ms},
                            http_status_code = 408,
                            response_detail = '{timeout_detail}',
                            error_message = '{timeout_detail}'
                        WHERE execution_id = {execution_id}
                    """
                    await sql.execute(
                        update_exec_sql, method="execute",
                        title=f"Mark execution {execution_id} as timeout",
                    )

                    # Update central_scheduling receipt
                    update_central_sql = f"""
                        UPDATE jgilpatrick.apps_central_scheduling
                        SET last_response_code = 408,
                            last_response_detail = '{timeout_detail}',
                            log_id = {log_id},
                            error_message = '{timeout_detail}'
                        WHERE id = {schedule_id}
                    """
                    await sql.execute(
                        update_central_sql, method="execute",
                        title=f"Update central receipt for timed-out schedule {schedule_id}",
                    )

                    stats["timed_out"] += 1
                    LOGGER.warning(
                        f"  Job {execution_id} ({fn_app}/{svc_name}): "
                        f"TIMEOUT after {elapsed_min}m (limit {max_exec_min}m)"
                    )

                # ----------------------------------------------------------
                # Path C: still running, within timeout -- skip
                # ----------------------------------------------------------
                else:
                    stats["still_running"] += 1
                    LOGGER.info(
                        f"  Job {execution_id} ({fn_app}/{svc_name}): "
                        f"still running ({elapsed_min}m / {max_exec_min}m limit)"
                    )

            except Exception as exc:
                error_msg = f"Error processing execution {execution_id}: {exc}"
                stats["errors"].append(error_msg)
                LOGGER.error(error_msg)
                await track_exception(exc, {
                    "operation": "job_manager_process_row",
                    "execution_id": execution_id,
                    "schedule_id": schedule_id,
                })

    return stats


def _compute_duration_ms(triggered_at_raw: Any, now_eastern: datetime) -> int:
    """Compute milliseconds between triggered_at and now in Eastern time."""
    eastern = pytz.timezone("US/Eastern")
    if triggered_at_raw is None:
        return 0

    if isinstance(triggered_at_raw, str):
        try:
            if "Z" in triggered_at_raw or "+" in triggered_at_raw or triggered_at_raw.count("-") > 2:
                triggered_dt = datetime.fromisoformat(triggered_at_raw.replace("Z", "+00:00"))
                triggered_dt = triggered_dt.astimezone(eastern)
            else:
                triggered_dt = datetime.fromisoformat(triggered_at_raw)
                triggered_dt = eastern.localize(triggered_dt)
        except (ValueError, AttributeError):
            triggered_dt = datetime.fromisoformat(triggered_at_raw)
            triggered_dt = eastern.localize(triggered_dt)
    elif hasattr(triggered_at_raw, "tzinfo") and triggered_at_raw.tzinfo is None:
        triggered_dt = eastern.localize(triggered_at_raw)
    else:
        triggered_dt = triggered_at_raw

    delta = now_eastern - triggered_dt
    return max(int(delta.total_seconds() * 1000), 0)


# ---------------------------------------------------------------------------
# Azure Functions Blueprint
# ---------------------------------------------------------------------------
bp = func.Blueprint()


@bp.timer_trigger(schedule="0 */2 * * * *", arg_name="timer", run_on_startup=False)
async def job_manager_timer(timer: func.TimerRequest) -> None:
    """Timer function that runs every 2 minutes to poll dispatched jobs.

    For each dispatched execution-log row:
      - If the master services log shows a terminal status, update both
        execution_log and central_scheduling receipt columns.
      - If the job has exceeded its max_execution_minutes, mark as 'timeout'.
      - Otherwise, skip (check again next tick).
    """
    LOGGER.info("Job manager timer started")

    try:
        if timer.past_due:
            LOGGER.warning("Job manager timer is running late")

        stats = await process_dispatched_jobs()

        summary = (
            f"Job manager completed - "
            f"Checked: {stats['checked']}, "
            f"Completed: {stats['completed']}, "
            f"Timed out: {stats['timed_out']}, "
            f"Still running: {stats['still_running']}"
        )
        LOGGER.info(summary)

        if stats["errors"]:
            LOGGER.error(f"Job manager errors: {stats['errors']}")

        await track_event("job_manager_completed", stats)

    except Exception as exc:
        LOGGER.error(f"Job manager timer failed: {exc}")
        await track_exception(exc, {"operation": "job_manager_timer"})
        raise
