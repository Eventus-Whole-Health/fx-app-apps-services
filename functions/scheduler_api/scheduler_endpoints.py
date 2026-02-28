"""Scheduler management API endpoints for the Keystone dashboard.

Provides endpoints for listing schedules with health status, execution
history with pagination and filtering, aggregate health summary, schedule
CRUD (create, update, soft-delete) with validation, and manual trigger
using existing scheduler infrastructure.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import azure.functions as func
import pytz

from ..shared.sql_client import SQLClient

LOGGER = logging.getLogger(__name__)

# Create the blueprint for scheduler API endpoints
bp = func.Blueprint()

LOGGER.info("Scheduler API Blueprint Created")
LOGGER.info("Registering scheduler management endpoints...")

# Valid frequency types for schedule definitions
VALID_FREQUENCIES = {"once", "daily", "weekly", "monthly", "hourly"}

# Valid execution statuses for filtering
VALID_STATUSES = {"success", "failed", "error", "timeout", "pending", "warning"}

# Default stuck threshold in minutes
DEFAULT_MAX_EXECUTION_MINUTES = 30


def sanitize_sql_string(value: str) -> str:
    """
    Sanitize a string for safe SQL insertion by escaping special characters.

    Args:
        value: String to sanitize

    Returns:
        Sanitized string safe for SQL
    """
    if not value:
        return ""
    sanitized = value.replace("'", "''")
    sanitized = sanitized.replace("\x00", "")
    sanitized = sanitized.replace("\\", "\\\\")
    return sanitized


def compute_health_status(
    failure_count: int,
    total_recent: int,
    current_status: str,
    processed_at: Any,
    max_execution_minutes: Optional[int],
) -> str:
    """
    Compute health status for a scheduled service based on recent execution history.

    Args:
        failure_count: Number of failures in the last 5 runs
        total_recent: Total number of recent log entries (up to 5)
        current_status: Current scheduling status from apps_central_scheduling
        processed_at: Timestamp of last processing
        max_execution_minutes: Per-service stuck threshold (or None for default)

    Returns:
        One of: "healthy", "degraded", "failing"
    """
    # Check for stuck service first
    if current_status == "processing" and processed_at:
        try:
            eastern = pytz.timezone("US/Eastern")
            now_eastern = datetime.now(eastern)

            if isinstance(processed_at, str):
                processed_dt = datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
                if processed_dt.tzinfo is None:
                    processed_dt = eastern.localize(processed_dt)
            else:
                processed_dt = processed_at
                if processed_dt.tzinfo is None:
                    processed_dt = eastern.localize(processed_dt)

            threshold = max_execution_minutes or DEFAULT_MAX_EXECUTION_MINUTES
            if (now_eastern - processed_dt).total_seconds() > threshold * 60:
                return "failing"
        except Exception:
            pass  # If we can't determine stuck status, fall through to log-based check

    # No log entries means never failed
    if total_recent == 0:
        return "healthy"

    # Health based on failure count in last 5 runs
    if failure_count == 0:
        return "healthy"
    elif failure_count <= 2:
        return "degraded"
    else:
        return "failing"


def compute_next_run_time(service: Dict[str, Any]) -> Optional[str]:
    """
    Compute the next expected run time for a scheduled service based on its
    frequency, schedule_config, and last_triggered_at.

    Args:
        service: Service row from apps_central_scheduling

    Returns:
        ISO 8601 string of next run time, or None if not computable
    """
    frequency = (service.get("frequency") or "").lower()
    last_triggered = service.get("last_triggered_at")
    config_str = service.get("schedule_config")

    eastern = pytz.timezone("US/Eastern")
    now_eastern = datetime.now(eastern)

    # Parse schedule_config if present
    config = {}
    if config_str:
        try:
            config = json.loads(config_str) if isinstance(config_str, str) else config_str
        except (json.JSONDecodeError, TypeError):
            config = {}

    # Parse last_triggered_at
    last_dt = None
    if last_triggered:
        try:
            if isinstance(last_triggered, str):
                last_dt = datetime.fromisoformat(last_triggered.replace("Z", "+00:00"))
            else:
                last_dt = last_triggered
            if last_dt.tzinfo is None:
                last_dt = eastern.localize(last_dt)
        except Exception:
            last_dt = None

    try:
        if frequency == "hourly":
            if last_dt:
                next_run = last_dt + timedelta(hours=1)
            else:
                # Next hour mark
                next_run = now_eastern.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return next_run.isoformat()

        elif frequency == "daily":
            # Parse start_time from config if available
            start_time_str = config.get("start_time", "00:00")
            try:
                hour, minute = map(int, start_time_str.split(":"))
            except (ValueError, AttributeError):
                hour, minute = 0, 0

            next_run = now_eastern.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now_eastern:
                next_run += timedelta(days=1)
            return next_run.isoformat()

        elif frequency == "weekly":
            day_of_week = config.get("day_of_week", "monday").lower()
            day_map = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6,
            }
            target_day = day_map.get(day_of_week, 0)

            start_time_str = config.get("start_time", "00:00")
            try:
                hour, minute = map(int, start_time_str.split(":"))
            except (ValueError, AttributeError):
                hour, minute = 0, 0

            days_ahead = target_day - now_eastern.weekday()
            if days_ahead < 0:
                days_ahead += 7
            next_run = now_eastern.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
            if next_run <= now_eastern:
                next_run += timedelta(weeks=1)
            return next_run.isoformat()

        elif frequency == "monthly":
            day_of_month = config.get("day_of_month", 1)
            try:
                day_of_month = int(day_of_month)
            except (ValueError, TypeError):
                day_of_month = 1

            start_time_str = config.get("start_time", "00:00")
            try:
                hour, minute = map(int, start_time_str.split(":"))
            except (ValueError, AttributeError):
                hour, minute = 0, 0

            # Try this month first
            try:
                next_run = now_eastern.replace(day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                # Day doesn't exist in current month, try next month
                if now_eastern.month == 12:
                    next_run = now_eastern.replace(year=now_eastern.year + 1, month=1, day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0)
                else:
                    next_run = now_eastern.replace(month=now_eastern.month + 1, day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0)

            if next_run <= now_eastern:
                if now_eastern.month == 12:
                    next_run = now_eastern.replace(year=now_eastern.year + 1, month=1, day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0)
                else:
                    try:
                        next_run = now_eastern.replace(month=now_eastern.month + 1, day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0)
                    except ValueError:
                        return None

            return next_run.isoformat()

        elif frequency == "once":
            # Once-type schedules don't have a next run
            return None

        else:
            return None

    except Exception:
        return None


# ---------------------------------------------------------------------------
# GET /api/scheduler/services — List all schedules with computed health
# ---------------------------------------------------------------------------
@bp.route(route="scheduler/services", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def list_scheduler_services(req: func.HttpRequest) -> func.HttpResponse:
    """
    List all active scheduled services with computed health status and next run time.

    Returns every active schedule from apps_central_scheduling with:
    - Health status (healthy/degraded/failing) based on last 5 execution log entries
    - Next computed run time based on frequency and schedule_config
    - Retry state (retry_count, max_retries, next_retry_at) from Phase 1

    GET /api/scheduler/services

    Response:
    {
        "success": true,
        "data": {
            "services": [...],
            "count": 43
        }
    }
    """
    LOGGER.info("Scheduler services list requested")

    async with SQLClient() as sql:
        try:
            # Single query with CTE to get services + health data efficiently
            query = """
                WITH recent_logs AS (
                    SELECT
                        function_app,
                        service_name,
                        status,
                        ROW_NUMBER() OVER (
                            PARTITION BY function_app, service_name
                            ORDER BY started_at DESC
                        ) as rn
                    FROM jgilpatrick.apps_master_services_log
                    WHERE started_at >= DATEADD(day, -7, GETDATE())
                ),
                health_summary AS (
                    SELECT
                        function_app,
                        service_name,
                        COUNT(*) as total_recent,
                        SUM(CASE WHEN status IN ('failed', 'error', 'timeout') THEN 1 ELSE 0 END) as failure_count
                    FROM recent_logs
                    WHERE rn <= 5
                    GROUP BY function_app, service_name
                )
                SELECT
                    s.id,
                    s.function_app,
                    s.service,
                    s.trigger_url,
                    s.frequency,
                    s.schedule_config,
                    s.json_body,
                    s.is_active,
                    s.status,
                    s.start_date,
                    s.last_triggered_at,
                    s.triggered_count,
                    s.trigger_limit,
                    s.processed_at,
                    s.retry_count,
                    s.max_retries,
                    s.next_retry_at,
                    s.max_execution_minutes,
                    s.error_message,
                    s.log_id,
                    COALESCE(h.failure_count, 0) as failure_count,
                    COALESCE(h.total_recent, 0) as total_recent
                FROM jgilpatrick.apps_central_scheduling s
                LEFT JOIN health_summary h
                    ON s.function_app = h.function_app
                    AND s.service = h.service_name
                WHERE s.is_active = 1
                ORDER BY s.function_app, s.service
            """

            result = await sql.execute(query, method="query", title="List scheduler services with health")

            if not result:
                result = []

            # Compute health_status and next_run_time in Python for each service
            services = []
            for row in result:
                health_status = compute_health_status(
                    failure_count=row.get("failure_count", 0),
                    total_recent=row.get("total_recent", 0),
                    current_status=row.get("status", ""),
                    processed_at=row.get("processed_at"),
                    max_execution_minutes=row.get("max_execution_minutes"),
                )

                next_run_time = compute_next_run_time(row)

                service_data = {
                    "id": row.get("id"),
                    "function_app": row.get("function_app"),
                    "service": row.get("service"),
                    "trigger_url": row.get("trigger_url"),
                    "frequency": row.get("frequency"),
                    "schedule_config": row.get("schedule_config"),
                    "is_active": bool(row.get("is_active", 0)),
                    "status": row.get("status"),
                    "last_triggered_at": row.get("last_triggered_at"),
                    "next_run_time": next_run_time,
                    "health_status": health_status,
                    "retry_count": row.get("retry_count", 0),
                    "max_retries": row.get("max_retries", 0),
                    "next_retry_at": row.get("next_retry_at"),
                    "triggered_count": row.get("triggered_count", 0),
                    "trigger_limit": row.get("trigger_limit"),
                    "max_execution_minutes": row.get("max_execution_minutes"),
                    "error_message": row.get("error_message"),
                }
                services.append(service_data)

            response_data = {
                "success": True,
                "data": {
                    "services": services,
                    "count": len(services),
                },
            }

            LOGGER.info(f"Returning {len(services)} scheduler services")
            return func.HttpResponse(
                json.dumps(response_data, indent=2, default=str),
                status_code=200,
                mimetype="application/json",
            )

        except Exception as e:
            error_msg = f"Error listing scheduler services: {str(e)}"
            LOGGER.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": "Internal server error", "detail": "Failed to retrieve scheduler services"}),
                status_code=500,
                mimetype="application/json",
            )


# ---------------------------------------------------------------------------
# GET /api/scheduler/health — Aggregate health summary
# ---------------------------------------------------------------------------
@bp.route(route="scheduler/health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def scheduler_health_summary(req: func.HttpRequest) -> func.HttpResponse:
    """
    Aggregate health summary across all active scheduled services.

    Returns counts of total, healthy, degraded, and failing services.
    Lightweight endpoint for dashboard header — calls on load and on interval.

    GET /api/scheduler/health

    Response:
    {
        "success": true,
        "data": {
            "total": 43,
            "healthy": 38,
            "degraded": 3,
            "failing": 2,
            "timestamp": "2026-02-27T15:30:00-05:00"
        }
    }
    """
    LOGGER.info("Scheduler health summary requested")

    async with SQLClient() as sql:
        try:
            # Same CTE pattern as list endpoint but only need aggregate counts
            query = """
                WITH recent_logs AS (
                    SELECT
                        function_app,
                        service_name,
                        status,
                        ROW_NUMBER() OVER (
                            PARTITION BY function_app, service_name
                            ORDER BY started_at DESC
                        ) as rn
                    FROM jgilpatrick.apps_master_services_log
                    WHERE started_at >= DATEADD(day, -7, GETDATE())
                ),
                health_summary AS (
                    SELECT
                        function_app,
                        service_name,
                        COUNT(*) as total_recent,
                        SUM(CASE WHEN status IN ('failed', 'error', 'timeout') THEN 1 ELSE 0 END) as failure_count
                    FROM recent_logs
                    WHERE rn <= 5
                    GROUP BY function_app, service_name
                )
                SELECT
                    s.id,
                    s.function_app,
                    s.service,
                    s.status,
                    s.processed_at,
                    s.max_execution_minutes,
                    COALESCE(h.failure_count, 0) as failure_count,
                    COALESCE(h.total_recent, 0) as total_recent
                FROM jgilpatrick.apps_central_scheduling s
                LEFT JOIN health_summary h
                    ON s.function_app = h.function_app
                    AND s.service = h.service_name
                WHERE s.is_active = 1
            """

            result = await sql.execute(query, method="query", title="Scheduler health summary")

            if not result:
                result = []

            # Compute aggregate counts
            total = len(result)
            healthy = 0
            degraded = 0
            failing = 0

            for row in result:
                status = compute_health_status(
                    failure_count=row.get("failure_count", 0),
                    total_recent=row.get("total_recent", 0),
                    current_status=row.get("status", ""),
                    processed_at=row.get("processed_at"),
                    max_execution_minutes=row.get("max_execution_minutes"),
                )
                if status == "healthy":
                    healthy += 1
                elif status == "degraded":
                    degraded += 1
                else:
                    failing += 1

            eastern = pytz.timezone("US/Eastern")
            timestamp = datetime.now(eastern).isoformat()

            response_data = {
                "success": True,
                "data": {
                    "total": total,
                    "healthy": healthy,
                    "degraded": degraded,
                    "failing": failing,
                    "timestamp": timestamp,
                },
            }

            LOGGER.info(f"Health summary: {total} total, {healthy} healthy, {degraded} degraded, {failing} failing")
            return func.HttpResponse(
                json.dumps(response_data, indent=2, default=str),
                status_code=200,
                mimetype="application/json",
            )

        except Exception as e:
            error_msg = f"Error computing scheduler health summary: {str(e)}"
            LOGGER.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": "Internal server error", "detail": "Failed to compute health summary"}),
                status_code=500,
                mimetype="application/json",
            )


# ---------------------------------------------------------------------------
# GET /api/scheduler/services/{service_id}/history — Execution history
# ---------------------------------------------------------------------------
@bp.route(route="scheduler/services/{service_id}/history", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def get_service_history(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get paginated execution history for a specific scheduled service.

    Queries apps_master_services_log filtered by function_app and service_name
    derived from the schedule definition. Supports filtering by status and
    date range, with offset-based pagination.

    GET /api/scheduler/services/{service_id}/history
    Query params: page, page_size, status, start_date, end_date

    Response:
    {
        "success": true,
        "data": {
            "service_id": 1,
            "function_app": "ai-scribing-services",
            "service_name": "daily-transcription-sync",
            "executions": [...],
            "pagination": {
                "page": 1,
                "page_size": 20,
                "total": 142,
                "total_pages": 8
            }
        }
    }
    """
    service_id = req.route_params.get("service_id")
    LOGGER.info(f"Execution history requested for service {service_id}")

    # Validate service_id is numeric
    if not service_id:
        return func.HttpResponse(
            json.dumps({"error": "Missing service_id", "detail": "service_id is required in URL path"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        service_id_int = int(service_id)
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid service_id", "detail": "service_id must be a number"}),
            status_code=400,
            mimetype="application/json",
        )

    # Parse and validate query parameters
    try:
        page = max(1, int(req.params.get("page", "1")))
    except (ValueError, TypeError):
        page = 1

    try:
        page_size = min(100, max(1, int(req.params.get("page_size", "20"))))
    except (ValueError, TypeError):
        page_size = 20

    status_filter_value = req.params.get("status")
    start_date = req.params.get("start_date")
    end_date = req.params.get("end_date")

    # Validate status filter
    if status_filter_value and status_filter_value.lower() not in VALID_STATUSES:
        return func.HttpResponse(
            json.dumps({
                "error": "Invalid status filter",
                "detail": f"status must be one of: {', '.join(sorted(VALID_STATUSES))}",
            }),
            status_code=400,
            mimetype="application/json",
        )

    # Validate date formats
    for date_param, date_name in [(start_date, "start_date"), (end_date, "end_date")]:
        if date_param:
            try:
                datetime.fromisoformat(date_param.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return func.HttpResponse(
                    json.dumps({
                        "error": f"Invalid {date_name}",
                        "detail": f"{date_name} must be a valid ISO 8601 date string",
                    }),
                    status_code=400,
                    mimetype="application/json",
                )

    async with SQLClient() as sql:
        try:
            # Look up the service to get function_app and service name
            service_query = f"""
                SELECT id, function_app, service
                FROM jgilpatrick.apps_central_scheduling
                WHERE id = {service_id_int}
            """
            service_result = await sql.execute(service_query, method="query", title=f"Look up service {service_id_int}")

            if not service_result or len(service_result) == 0:
                return func.HttpResponse(
                    json.dumps({"error": "Service not found", "detail": f"No schedule with id {service_id_int}"}),
                    status_code=404,
                    mimetype="application/json",
                )

            service_row = service_result[0]
            function_app = sanitize_sql_string(service_row["function_app"])
            service_name = sanitize_sql_string(service_row["service"])

            # Build WHERE clause filters
            where_clauses = [
                f"function_app = '{function_app}'",
                f"service_name = '{service_name}'",
            ]

            if status_filter_value:
                safe_status = sanitize_sql_string(status_filter_value.lower())
                where_clauses.append(f"status = '{safe_status}'")

            if start_date:
                safe_start = sanitize_sql_string(start_date)
                where_clauses.append(f"started_at >= '{safe_start}'")

            if end_date:
                safe_end = sanitize_sql_string(end_date)
                where_clauses.append(f"started_at <= '{safe_end}'")

            where_sql = " AND ".join(where_clauses)
            offset = (page - 1) * page_size

            # Count total matching rows
            count_query = f"""
                SELECT COUNT(*) as total
                FROM jgilpatrick.apps_master_services_log
                WHERE {where_sql}
            """
            count_result = await sql.execute(count_query, method="query", title=f"Count history for service {service_id_int}")
            total = count_result[0]["total"] if count_result else 0

            # Fetch paginated results
            history_query = f"""
                SELECT
                    log_id,
                    function_app,
                    service_name,
                    status,
                    started_at,
                    ended_at,
                    duration_ms,
                    error_message,
                    request,
                    response,
                    trigger_source
                FROM jgilpatrick.apps_master_services_log
                WHERE {where_sql}
                ORDER BY started_at DESC
                OFFSET {offset} ROWS
                FETCH NEXT {page_size} ROWS ONLY
            """
            history_result = await sql.execute(history_query, method="query", title=f"Execution history for service {service_id_int}")

            if not history_result:
                history_result = []

            # Build execution records with parsed request/response JSON
            executions = []
            for row in history_result:
                # Parse request data
                request_data = None
                if row.get("request"):
                    try:
                        request_data = json.loads(row["request"])
                    except (json.JSONDecodeError, TypeError):
                        request_data = row["request"]

                # Parse response data
                response_data = None
                if row.get("response"):
                    try:
                        response_data = json.loads(row["response"])
                    except (json.JSONDecodeError, TypeError):
                        response_data = row["response"]

                executions.append({
                    "log_id": row.get("log_id"),
                    "status": row.get("status"),
                    "started_at": row.get("started_at"),
                    "ended_at": row.get("ended_at"),
                    "duration_ms": row.get("duration_ms"),
                    "error_message": row.get("error_message"),
                    "request": request_data,
                    "response": response_data,
                    "trigger_source": row.get("trigger_source"),
                })

            total_pages = math.ceil(total / page_size) if total > 0 else 0

            response_body = {
                "success": True,
                "data": {
                    "service_id": service_id_int,
                    "function_app": service_row["function_app"],
                    "service_name": service_row["service"],
                    "executions": executions,
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total": total,
                        "total_pages": total_pages,
                    },
                },
            }

            LOGGER.info(f"Returning {len(executions)} history records for service {service_id_int} (page {page}/{total_pages})")
            return func.HttpResponse(
                json.dumps(response_body, indent=2, default=str),
                status_code=200,
                mimetype="application/json",
            )

        except Exception as e:
            error_msg = f"Error retrieving execution history for service {service_id}: {str(e)}"
            LOGGER.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": "Internal server error", "detail": "Failed to retrieve execution history"}),
                status_code=500,
                mimetype="application/json",
            )


# ---------------------------------------------------------------------------
# Validation helper for CRUD operations
# ---------------------------------------------------------------------------
def validate_schedule_input(data: Dict[str, Any], require_all: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validate schedule input data for create/update operations.

    Args:
        data: Request body dictionary
        require_all: If True (create), require frequency/trigger_url/function_app/service.
                     If False (update), only validate fields that are present.

    Returns:
        Tuple of (is_valid, error_message). error_message is None if valid.
    """
    # Check required fields for create
    if require_all:
        if not data.get("frequency"):
            return False, "frequency is required"
        if not data.get("trigger_url"):
            return False, "trigger_url is required"
        if not data.get("function_app"):
            return False, "function_app is required"
        if not data.get("service"):
            return False, "service is required"

    # Validate frequency if provided
    frequency = data.get("frequency")
    if frequency is not None:
        if not isinstance(frequency, str) or frequency.strip().lower() not in VALID_FREQUENCIES:
            return False, f"frequency must be one of: {', '.join(sorted(VALID_FREQUENCIES))}"

    # Validate trigger_url if provided
    trigger_url = data.get("trigger_url")
    if trigger_url is not None:
        if not isinstance(trigger_url, str) or not trigger_url.strip():
            return False, "trigger_url must be a non-empty string"

    # Validate function_app if provided
    function_app = data.get("function_app")
    if function_app is not None:
        if not isinstance(function_app, str) or not function_app.strip():
            return False, "function_app must be a non-empty string"

    # Validate service if provided
    service = data.get("service")
    if service is not None:
        if not isinstance(service, str) or not service.strip():
            return False, "service must be a non-empty string"

    # Validate schedule_config is valid JSON if provided
    schedule_config = data.get("schedule_config")
    if schedule_config is not None and schedule_config != "":
        if isinstance(schedule_config, str):
            try:
                json.loads(schedule_config)
            except (json.JSONDecodeError, TypeError):
                return False, "schedule_config must be valid JSON"

    return True, None


def _sql_value(value: Any, field_type: str = "string") -> str:
    """
    Convert a Python value to a SQL-safe string for insertion.

    Args:
        value: The Python value to convert
        field_type: One of "string", "int", "bool", "nullable_string", "nullable_int"

    Returns:
        SQL-safe string representation
    """
    if value is None:
        return "NULL"

    if field_type == "bool":
        return "1" if value else "0"
    elif field_type in ("int", "nullable_int"):
        try:
            return str(int(value))
        except (ValueError, TypeError):
            return "NULL"
    elif field_type in ("string", "nullable_string"):
        return f"'{sanitize_sql_string(str(value))}'"
    else:
        return f"'{sanitize_sql_string(str(value))}'"


# System-managed fields that cannot be set via API
SYSTEM_MANAGED_FIELDS = {
    "id", "status", "triggered_count", "last_triggered_at",
    "retry_count", "next_retry_at", "processed_at", "log_id",
    "error_message", "last_response_code", "last_response_detail",
}

# Updatable fields with their SQL types
UPDATABLE_FIELDS = {
    "function_app": "string",
    "service": "string",
    "trigger_url": "string",
    "frequency": "string",
    "schedule_config": "nullable_string",
    "json_body": "nullable_string",
    "start_date": "nullable_string",
    "trigger_limit": "nullable_int",
    "max_retries": "nullable_int",
    "max_execution_minutes": "nullable_int",
    "is_active": "bool",
}


# ---------------------------------------------------------------------------
# POST /api/scheduler/services — Create a new schedule
# ---------------------------------------------------------------------------
@bp.route(route="scheduler/services", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def create_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """
    Create a new schedule definition in apps_central_scheduling.

    Validates required fields (frequency, trigger_url, function_app, service)
    and optional fields (schedule_config must be valid JSON). Returns the
    created service record.

    POST /api/scheduler/services

    Request body:
    {
        "function_app": "ai-scribing-services",
        "service": "new-daily-sync",
        "trigger_url": "https://...",
        "frequency": "daily",
        "schedule_config": "{}",
        "json_body": "{}",
        "start_date": "2026-03-01T06:00:00",
        "trigger_limit": null,
        "max_retries": 3,
        "max_execution_minutes": 30
    }

    Response (201):
    {
        "success": true,
        "data": {"service": {...}}
    }
    """
    LOGGER.info("Create schedule request received")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON", "detail": "Request body must be valid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    if not body:
        return func.HttpResponse(
            json.dumps({"error": "Empty request", "detail": "Request body is required"}),
            status_code=400,
            mimetype="application/json",
        )

    # Validate input
    is_valid, error_msg = validate_schedule_input(body, require_all=True)
    if not is_valid:
        return func.HttpResponse(
            json.dumps({"error": "Validation failed", "detail": error_msg}),
            status_code=400,
            mimetype="application/json",
        )

    async with SQLClient() as sql:
        try:
            # Build INSERT query with sanitized values
            function_app = _sql_value(body["function_app"], "string")
            service = _sql_value(body["service"], "string")
            trigger_url = _sql_value(body["trigger_url"], "string")
            frequency = _sql_value(body["frequency"].strip().lower(), "string")
            schedule_config = _sql_value(body.get("schedule_config"), "nullable_string")
            json_body = _sql_value(body.get("json_body"), "nullable_string")
            start_date = _sql_value(body.get("start_date"), "nullable_string")
            trigger_limit = _sql_value(body.get("trigger_limit"), "nullable_int")
            max_retries = _sql_value(body.get("max_retries"), "nullable_int")
            max_execution_minutes = _sql_value(body.get("max_execution_minutes"), "nullable_int")

            insert_query = f"""
                INSERT INTO jgilpatrick.apps_central_scheduling
                    (function_app, service, trigger_url, frequency, schedule_config,
                     json_body, start_date, trigger_limit, max_retries, max_execution_minutes,
                     is_active, status, triggered_count, retry_count)
                VALUES
                    ({function_app}, {service}, {trigger_url}, {frequency}, {schedule_config},
                     {json_body}, {start_date}, {trigger_limit}, {max_retries}, {max_execution_minutes},
                     1, 'pending', 0, 0)
            """

            await sql.execute(insert_query, method="execute", title="Create new schedule")

            # Query back the newly created row (cannot use SCOPE_IDENTITY via SQL Executor API)
            safe_app = sanitize_sql_string(body["function_app"])
            safe_service = sanitize_sql_string(body["service"])
            fetch_query = f"""
                SELECT TOP 1 *
                FROM jgilpatrick.apps_central_scheduling
                WHERE function_app = '{safe_app}'
                AND service = '{safe_service}'
                ORDER BY id DESC
            """
            fetch_result = await sql.execute(fetch_query, method="query", title="Fetch newly created schedule")

            created_service = fetch_result[0] if fetch_result else None

            LOGGER.info(f"Schedule created: {body['function_app']}/{body['service']}")
            return func.HttpResponse(
                json.dumps({"success": True, "data": {"service": created_service}}, indent=2, default=str),
                status_code=201,
                mimetype="application/json",
            )

        except Exception as e:
            error_msg = f"Error creating schedule: {str(e)}"
            LOGGER.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": "Internal server error", "detail": "Failed to create schedule"}),
                status_code=500,
                mimetype="application/json",
            )


# ---------------------------------------------------------------------------
# PUT /api/scheduler/services/{service_id} — Update a schedule
# ---------------------------------------------------------------------------
@bp.route(route="scheduler/services/{service_id}", methods=["PUT"], auth_level=func.AuthLevel.ANONYMOUS)
async def update_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """
    Update an existing schedule definition. Supports partial updates — only
    provided fields are modified. System-managed fields (status, triggered_count,
    retry_count, etc.) cannot be updated via this endpoint.

    PUT /api/scheduler/services/{service_id}

    Request body (any subset):
    {
        "frequency": "weekly",
        "schedule_config": "{}",
        "trigger_url": "https://...",
        "is_active": true
    }

    Response (200):
    {
        "success": true,
        "data": {"service": {...}}
    }
    """
    service_id = req.route_params.get("service_id")
    LOGGER.info(f"Update schedule request for service {service_id}")

    # Validate service_id
    try:
        service_id_int = int(service_id)
    except (ValueError, TypeError):
        return func.HttpResponse(
            json.dumps({"error": "Invalid service_id", "detail": "service_id must be a number"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON", "detail": "Request body must be valid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    if not body:
        return func.HttpResponse(
            json.dumps({"error": "Empty request", "detail": "Request body is required"}),
            status_code=400,
            mimetype="application/json",
        )

    # Validate provided fields (partial update — don't require all fields)
    is_valid, error_msg = validate_schedule_input(body, require_all=False)
    if not is_valid:
        return func.HttpResponse(
            json.dumps({"error": "Validation failed", "detail": error_msg}),
            status_code=400,
            mimetype="application/json",
        )

    # Check for attempts to update system-managed fields
    attempted_system_fields = set(body.keys()) & SYSTEM_MANAGED_FIELDS
    if attempted_system_fields:
        return func.HttpResponse(
            json.dumps({
                "error": "Validation failed",
                "detail": f"Cannot update system-managed fields: {', '.join(sorted(attempted_system_fields))}",
            }),
            status_code=400,
            mimetype="application/json",
        )

    async with SQLClient() as sql:
        try:
            # Look up existing service
            lookup_query = f"""
                SELECT id FROM jgilpatrick.apps_central_scheduling
                WHERE id = {service_id_int}
            """
            lookup_result = await sql.execute(lookup_query, method="query", title=f"Look up service {service_id_int}")

            if not lookup_result or len(lookup_result) == 0:
                return func.HttpResponse(
                    json.dumps({"error": "Service not found", "detail": f"No schedule with id {service_id_int}"}),
                    status_code=404,
                    mimetype="application/json",
                )

            # Build dynamic SET clause from provided fields
            set_clauses = []
            for field_name, field_type in UPDATABLE_FIELDS.items():
                if field_name in body:
                    value = body[field_name]
                    # Normalize frequency to lowercase
                    if field_name == "frequency" and value is not None:
                        value = str(value).strip().lower()
                    set_clauses.append(f"{field_name} = {_sql_value(value, field_type)}")

            if not set_clauses:
                return func.HttpResponse(
                    json.dumps({"error": "No updatable fields", "detail": "Request body contains no recognized updatable fields"}),
                    status_code=400,
                    mimetype="application/json",
                )

            set_sql = ", ".join(set_clauses)
            update_query = f"""
                UPDATE jgilpatrick.apps_central_scheduling
                SET {set_sql}
                WHERE id = {service_id_int}
            """

            await sql.execute(update_query, method="execute", title=f"Update schedule {service_id_int}")

            # Fetch the updated row
            fetch_query = f"""
                SELECT * FROM jgilpatrick.apps_central_scheduling
                WHERE id = {service_id_int}
            """
            fetch_result = await sql.execute(fetch_query, method="query", title=f"Fetch updated schedule {service_id_int}")
            updated_service = fetch_result[0] if fetch_result else None

            LOGGER.info(f"Schedule {service_id_int} updated: {', '.join(body.keys())}")
            return func.HttpResponse(
                json.dumps({"success": True, "data": {"service": updated_service}}, indent=2, default=str),
                status_code=200,
                mimetype="application/json",
            )

        except Exception as e:
            error_msg = f"Error updating schedule {service_id}: {str(e)}"
            LOGGER.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": "Internal server error", "detail": "Failed to update schedule"}),
                status_code=500,
                mimetype="application/json",
            )


# ---------------------------------------------------------------------------
# DELETE /api/scheduler/services/{service_id} — Soft-delete a schedule
# ---------------------------------------------------------------------------
@bp.route(route="scheduler/services/{service_id}", methods=["DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
async def delete_schedule(req: func.HttpRequest) -> func.HttpResponse:
    """
    Soft-delete a schedule by setting is_active=0.

    This does NOT hard-delete the row — it marks it inactive so it no longer
    appears in the active schedule list or gets triggered by the scheduler.

    DELETE /api/scheduler/services/{service_id}

    Response (200):
    {
        "success": true,
        "data": {"id": 1, "deleted": true}
    }
    """
    service_id = req.route_params.get("service_id")
    LOGGER.info(f"Delete schedule request for service {service_id}")

    try:
        service_id_int = int(service_id)
    except (ValueError, TypeError):
        return func.HttpResponse(
            json.dumps({"error": "Invalid service_id", "detail": "service_id must be a number"}),
            status_code=400,
            mimetype="application/json",
        )

    async with SQLClient() as sql:
        try:
            # Check if service exists
            lookup_query = f"""
                SELECT id, is_active FROM jgilpatrick.apps_central_scheduling
                WHERE id = {service_id_int}
            """
            lookup_result = await sql.execute(lookup_query, method="query", title=f"Look up service {service_id_int} for deletion")

            if not lookup_result or len(lookup_result) == 0:
                return func.HttpResponse(
                    json.dumps({"error": "Service not found", "detail": f"No schedule with id {service_id_int}"}),
                    status_code=404,
                    mimetype="application/json",
                )

            # Soft delete: set is_active = 0
            delete_query = f"""
                UPDATE jgilpatrick.apps_central_scheduling
                SET is_active = 0
                WHERE id = {service_id_int}
            """

            await sql.execute(delete_query, method="execute", title=f"Soft-delete schedule {service_id_int}")

            LOGGER.info(f"Schedule {service_id_int} soft-deleted (is_active=0)")
            return func.HttpResponse(
                json.dumps({"success": True, "data": {"id": service_id_int, "deleted": True}}, indent=2),
                status_code=200,
                mimetype="application/json",
            )

        except Exception as e:
            error_msg = f"Error deleting schedule {service_id}: {str(e)}"
            LOGGER.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": "Internal server error", "detail": "Failed to delete schedule"}),
                status_code=500,
                mimetype="application/json",
            )


# ---------------------------------------------------------------------------
# POST /api/scheduler/services/{service_id}/trigger — Manual trigger
# ---------------------------------------------------------------------------
@bp.route(route="scheduler/services/{service_id}/trigger", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
async def trigger_service(req: func.HttpRequest) -> func.HttpResponse:
    """
    Manually trigger a specific scheduled service.

    Reuses the existing scheduler infrastructure via
    process_scheduled_services_with_overrides(force_service_ids=[id],
    bypass_window_check=True). The endpoint awaits the full execution
    (Keystone ASP unlimited timeout) and returns the result with a log_id
    that the dashboard can use to poll /api/status/{log_id}.

    POST /api/scheduler/services/{service_id}/trigger

    Response (200):
    {
        "success": true,
        "data": {
            "service_id": 1,
            "triggered": true,
            "message": "Service triggered successfully",
            "log_id": 5001,
            "status_url": "/api/status/5001",
            "result": {...}
        }
    }
    """
    service_id = req.route_params.get("service_id")
    LOGGER.info(f"Manual trigger request for service {service_id}")

    # Validate service_id
    try:
        service_id_int = int(service_id)
    except (ValueError, TypeError):
        return func.HttpResponse(
            json.dumps({"error": "Invalid service_id", "detail": "service_id must be a number"}),
            status_code=400,
            mimetype="application/json",
        )

    async with SQLClient() as sql:
        try:
            # Verify service exists and is active
            lookup_query = f"""
                SELECT id, function_app, service, is_active
                FROM jgilpatrick.apps_central_scheduling
                WHERE id = {service_id_int}
            """
            lookup_result = await sql.execute(lookup_query, method="query", title=f"Look up service {service_id_int} for trigger")

            if not lookup_result or len(lookup_result) == 0:
                return func.HttpResponse(
                    json.dumps({"error": "Service not found", "detail": f"No schedule with id {service_id_int}"}),
                    status_code=404,
                    mimetype="application/json",
                )

            service_row = lookup_result[0]
            if not service_row.get("is_active"):
                return func.HttpResponse(
                    json.dumps({"error": "Service inactive", "detail": f"Schedule {service_id_int} is inactive and cannot be triggered"}),
                    status_code=400,
                    mimetype="application/json",
                )

        except Exception as e:
            error_msg = f"Error looking up service {service_id}: {str(e)}"
            LOGGER.error(error_msg)
            return func.HttpResponse(
                json.dumps({"error": "Internal server error", "detail": "Failed to look up service"}),
                status_code=500,
                mimetype="application/json",
            )

    # Import and call the existing scheduler trigger function
    # Using absolute import since both modules are under the functions package
    try:
        from ..scheduler.timer_function import process_scheduled_services_with_overrides
    except ImportError:
        LOGGER.error("Failed to import process_scheduled_services_with_overrides from scheduler module")
        return func.HttpResponse(
            json.dumps({"error": "Internal server error", "detail": "Scheduler module not available"}),
            status_code=500,
            mimetype="application/json",
        )

    try:
        # Trigger the service using existing infrastructure
        # bypass_window_check=True means run now regardless of schedule window
        result = await process_scheduled_services_with_overrides(
            force_service_ids=[service_id_int],
            bypass_window_check=True,
        )

        # After trigger completes, query for the log_id from the scheduling table
        # The scheduler stores the most recent log_id in apps_central_scheduling.log_id
        log_id = None
        status_url = None

        async with SQLClient() as sql:
            try:
                log_query = f"""
                    SELECT log_id FROM jgilpatrick.apps_central_scheduling
                    WHERE id = {service_id_int}
                """
                log_result = await sql.execute(log_query, method="query", title=f"Get log_id for triggered service {service_id_int}")
                if log_result and log_result[0].get("log_id"):
                    log_id = log_result[0]["log_id"]
                    status_url = f"/api/status/{log_id}"
            except Exception:
                pass  # log_id is optional — don't fail the response if we can't get it

        triggered = result.get("processed", 0) > 0

        response_data = {
            "success": True,
            "data": {
                "service_id": service_id_int,
                "triggered": triggered,
                "message": "Service triggered successfully" if triggered else "Service was not triggered (may already be processing)",
                "log_id": log_id,
                "status_url": status_url,
                "result": {
                    "processed": result.get("processed", 0),
                    "successful": result.get("successful", 0),
                    "failed": result.get("failed", 0),
                },
            },
        }

        LOGGER.info(f"Service {service_id_int} triggered: processed={result.get('processed', 0)}, log_id={log_id}")
        return func.HttpResponse(
            json.dumps(response_data, indent=2, default=str),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        error_msg = f"Error triggering service {service_id}: {str(e)}"
        LOGGER.error(error_msg)
        return func.HttpResponse(
            json.dumps({"error": "Trigger failed", "detail": f"Failed to trigger service: {str(e)[:200]}"}),
            status_code=500,
            mimetype="application/json",
        )
