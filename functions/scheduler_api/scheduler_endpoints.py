"""Scheduler management API endpoints for the Keystone dashboard.

Provides read-only endpoints for listing schedules with health status,
execution history with pagination and filtering, and aggregate health summary.
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
