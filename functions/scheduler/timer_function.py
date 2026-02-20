"""Timer function to process scheduled services from the apps_central_scheduling table."""
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
from ..shared.service_logger import ServiceLogger

LOGGER = logging.getLogger(__name__)

# Constants for timeout and retry handling
SERVICE_REQUEST_TIMEOUT = 600  # 10 minutes timeout per service request (increased for unlimited timeout support)
SQL_COLD_START_RETRY_DELAY = 5  # Wait 5 seconds before retrying SQL operations
MAX_SQL_COLD_START_RETRIES = 3  # Maximum retries for SQL cold starts
POLLING_INTERVAL = 30  # Poll every 30 seconds for 202 responses


def get_eastern_time_sql() -> str:
    """
    Get SQL expression for current Eastern time that accounts for daylight saving time.
    Returns the appropriate offset (-04:00 for EDT, -05:00 for EST).
    """
    import pytz
    from datetime import datetime
    
    eastern = pytz.timezone('US/Eastern')
    current_eastern = datetime.now(eastern)
    
    # Get the UTC offset (includes DST)
    offset_seconds = current_eastern.utcoffset().total_seconds()
    offset_hours = int(offset_seconds / 3600)
    
    # Format as SQL offset string
    if offset_hours >= 0:
        offset_str = f"+{offset_hours:02d}:00"
    else:
        offset_str = f"{offset_hours:03d}:00"
    
    return f"CONVERT(datetime, SWITCHOFFSET(SYSDATETIMEOFFSET(), '{offset_str}'))"


async def execute_sql_with_cold_start_retry(sql_client: SQLClient, sql: str, method: str = "query", title: str = None) -> Any:
    """
    Execute SQL with retry logic for SQL server cold starts.
    
    Args:
        sql_client: SQL client instance
        sql: SQL query to execute
        method: Query method (query/execute)
        title: Optional title for logging
    
    Returns:
        SQL execution result
    """
    retry_count = 0
    last_exception = None
    
    while retry_count <= MAX_SQL_COLD_START_RETRIES:
        try:
            LOGGER.info(f"Executing SQL query (attempt {retry_count + 1}/{MAX_SQL_COLD_START_RETRIES + 1})")
            result = await sql_client.execute(sql, method=method, title=title)
            LOGGER.info(f"SQL query executed successfully on attempt {retry_count + 1}")
            return result
            
        except Exception as e:
            last_exception = e
            error_msg = str(e).lower()
            
            # Check if this looks like a SQL server cold start error
            cold_start_indicators = [
                "connection timeout",
                "timeout expired",
                "server is not responding",
                "connection was closed",
                "login timeout",
                "connection reset",
                "network-related",
                "provider: tcp provider",
                "error: 2"
            ]
            
            is_cold_start = any(indicator in error_msg for indicator in cold_start_indicators)
            
            if is_cold_start and retry_count < MAX_SQL_COLD_START_RETRIES:
                retry_count += 1
                LOGGER.warning(f"SQL server appears to be cold starting, retrying in {SQL_COLD_START_RETRY_DELAY}s (attempt {retry_count}/{MAX_SQL_COLD_START_RETRIES})")
                await asyncio.sleep(SQL_COLD_START_RETRY_DELAY)
                continue
            else:
                # Not a cold start error or max retries exceeded
                LOGGER.error(f"SQL execution failed: {str(e)}")
                raise e
    
    # If we get here, all retries failed
    LOGGER.error(f"SQL execution failed after {MAX_SQL_COLD_START_RETRIES} retries")
    raise last_exception


def sanitize_sql_string(value: str, max_length: int = 3900) -> str:
    """
    Sanitize a string for safe SQL insertion by escaping special characters.
    Truncates to max_length BEFORE escaping to ensure final length stays within limits.
    
    Args:
        value: String to sanitize
        max_length: Maximum length before escaping (default 3900 to allow room for expansion)
    
    Returns:
        Sanitized and truncated string safe for SQL
    """
    if not value:
        return ""
    
    # Truncate first to leave room for escaping
    truncated = value[:max_length]
    
    # Escape single quotes by doubling them
    sanitized = truncated.replace("'", "''")
    
    # Replace other potentially problematic characters
    sanitized = sanitized.replace("\x00", "")  # Remove null bytes
    sanitized = sanitized.replace("\\", "\\\\")  # Escape backslashes
    
    return sanitized


def is_within_schedule_window(current_time: datetime, scheduled_time_str: str, window_minutes: int = 15) -> bool:
    """
    Check if current time is within the scheduled time window.
    Since timer runs every 15 minutes, we check if we're in the same 15-minute window as the scheduled time.
    
    Args:
        current_time: Current datetime
        scheduled_time_str: Time string in HH:MM format
        window_minutes: Window size in minutes (default 15)
    
    Returns:
        True if current time is within the schedule window
    """
    try:
        # Parse scheduled time
        hour, minute = map(int, scheduled_time_str.split(':'))
        
        # Check if current hour matches
        if current_time.hour != hour:
            return False
        
        # Check if we're within the same 15-minute window
        # Windows are: 00-14, 15-29, 30-44, 45-59
        current_window = current_time.minute // window_minutes
        scheduled_window = minute // window_minutes
        
        return current_window == scheduled_window
    except (ValueError, AttributeError):
        return False


async def process_scheduled_services() -> Dict[str, Any]:
    """Process all scheduled services that are due for execution."""
    return await process_scheduled_services_with_overrides()


async def check_and_handle_stuck_processing_services(sql_client: SQLClient, current_time: datetime) -> int:
    """
    Check for services stuck in 'processing' status for more than 15 minutes or with NULL last_triggered_at and mark them as failed.
    
    Args:
        sql_client: SQL client instance
        current_time: Current datetime in Eastern timezone
    
    Returns:
        Number of stuck services found and marked as failed
    """
    eastern_time_sql = get_eastern_time_sql()
    
    # Query for services stuck in processing status for more than 15 minutes OR with NULL last_triggered_at
    stuck_services_sql = f"""
        SELECT id, function_app, service, last_triggered_at
        FROM jgilpatrick.apps_central_scheduling
        WHERE status = 'processing'
        AND (
            last_triggered_at IS NULL 
            OR DATEDIFF(minute, last_triggered_at, {eastern_time_sql}) > 15
        )
    """
    
    try:
        stuck_services = await execute_sql_with_cold_start_retry(
            sql_client,
            stuck_services_sql,
            method="query",
            title="Check for stuck processing services"
        )
        
        if not stuck_services or not isinstance(stuck_services, list):
            return 0
        
        stuck_count = len(stuck_services)
        if stuck_count > 0:
            LOGGER.warning(f"🔍 Found {stuck_count} services stuck in 'processing' status (>15 minutes or NULL last_triggered_at)")
            
            # Mark all stuck services as failed
            for service in stuck_services:
                service_id = service["id"]
                function_app = service["function_app"]
                service_name = service["service"]
                last_triggered = service["last_triggered_at"]
                
                if last_triggered is None:
                    LOGGER.warning(f"   ⚠️  Service {service_id} ({function_app}/{service_name}) stuck with NULL last_triggered_at")
                    error_message = 'Service execution timeout - stuck in processing status with NULL last_triggered_at'
                else:
                    LOGGER.warning(f"   ⚠️  Service {service_id} ({function_app}/{service_name}) stuck since {last_triggered}")
                    error_message = 'Service execution timeout - stuck in processing status for >15 minutes'
                
                # Mark as failed with appropriate error message
                await execute_sql_with_cold_start_retry(
                    sql_client,
                    f"""
                    UPDATE jgilpatrick.apps_central_scheduling
                    SET status = 'failed',
                        processed_at = {eastern_time_sql},
                        last_response_code = 408,
                        last_response_detail = NULL,
                        error_message = '{error_message}'
                    WHERE id = {service_id}
                    """,
                    method="execute",
                    title=f"Mark stuck service {service_id} as failed"
                )
            
            LOGGER.warning(f"✅ Marked {stuck_count} stuck services as failed")
        
        return stuck_count
        
    except Exception as e:
        LOGGER.error(f"Error checking for stuck processing services: {str(e)}")
        return 0


async def process_scheduled_services_with_overrides(
    bypass_window_check: bool = False,
    force_service_ids: list[int] = None,
    master_logger: Optional[ServiceLogger] = None
) -> Dict[str, Any]:
    """Process all scheduled services that are due for execution with optional overrides."""
    settings = get_settings()
    
    # Log authentication environment for debugging
    import os
    LOGGER.info("🔐 Authentication Environment Check:")
    LOGGER.info(f"   • AZURE_CLIENT_ID: {'✓ Set' if os.getenv('AZURE_CLIENT_ID') else '✗ Missing'}")
    LOGGER.info(f"   • AZURE_CLIENT_SECRET: {'✓ Set' if os.getenv('AZURE_CLIENT_SECRET') else '✗ Missing'}")
    LOGGER.info(f"   • AZURE_TENANT_ID: {'✓ Set' if os.getenv('AZURE_TENANT_ID') else '✗ Missing'}")
    LOGGER.info(f"   • SQL_EXECUTOR_URL: {settings.sql_executor_url}")
    LOGGER.info(f"   • SQL_EXECUTOR_SCOPE: {settings.sql_executor_scope}")
    
    results = {
        "triggered_services": [],
        "processed": 0,
        "successful": 0,
        "failed": 0,
        "skipped": 0,
        "timed_out": 0,
        "stuck_services_found": 0,
        "errors": []
    }
    
    async with SQLClient() as sql_client:
        try:
            # Use Eastern timezone for all operations
            eastern = pytz.timezone('US/Eastern')
            current_time = datetime.now(eastern)
            
            # First, check for and handle stuck processing services
            LOGGER.info("🔍 Checking for stuck processing services...")
            stuck_count = await check_and_handle_stuck_processing_services(sql_client, current_time)
            results["stuck_services_found"] = stuck_count
            
            # Build query with optional service ID filter
            if force_service_ids:
                service_ids_str = ','.join(map(str, force_service_ids))
                service_filter = f"AND id IN ({service_ids_str})"
                
                # When forcing specific service IDs, we still respect normal conditions
                # unless bypass_window_check is True (which bypasses scheduling logic)
                if bypass_window_check:
                    # Bypass all scheduling conditions - only check is_active
                    fetch_services_sql = f"""
                        SELECT id, function_app, service, trigger_url, json_body, 
                               start_date, frequency, schedule_config, 
                               triggered_count, trigger_limit, last_triggered_at, 
                               retry_count, max_retries
                        FROM jgilpatrick.apps_central_scheduling
                        WHERE is_active = 1 
                        {service_filter}
                        ORDER BY start_date ASC
                    """
                else:
                    # Normal conditions with service ID filter
                    fetch_services_sql = f"""
                        SELECT id, function_app, service, trigger_url, json_body, 
                               start_date, frequency, schedule_config, 
                               triggered_count, trigger_limit, last_triggered_at, 
                               retry_count, max_retries
                        FROM jgilpatrick.apps_central_scheduling
                        WHERE is_active = 1 
                        AND status IN ('pending', 'failed')
                        AND (trigger_limit IS NULL OR triggered_count < trigger_limit)
                        {service_filter}
                        ORDER BY start_date ASC
                    """
            else:
                service_filter = ""
                # Standard query with normal conditions
                fetch_services_sql = f"""
                    SELECT id, function_app, service, trigger_url, json_body, 
                           start_date, frequency, schedule_config, 
                           triggered_count, trigger_limit, last_triggered_at, 
                           retry_count, max_retries
                    FROM jgilpatrick.apps_central_scheduling
                    WHERE is_active = 1 
                    AND status IN ('pending', 'failed')
                    AND (trigger_limit IS NULL OR triggered_count < trigger_limit)
                    {service_filter}
                    ORDER BY start_date ASC
                """
            
            services_response = await execute_sql_with_cold_start_retry(
                sql_client,
                fetch_services_sql,
                method="query",
                title="Fetch active scheduled services"
            )
            
            if not services_response or not isinstance(services_response, list):
                LOGGER.info("📭 No active services found in database")
                return results, timeout_tracker
            
            services = services_response
            LOGGER.info(f"📋 Found {len(services)} active services to evaluate")

            # Log service details
            if services:
                LOGGER.info(f"📝 Services to evaluate:")
                for service in services:
                    LOGGER.info(f"   • ID {service['id']}: {service['function_app']}/{service['service']} ({service['frequency']})")

            for service in services:
                service_id = service["id"]
                function_app = service["function_app"]
                service_name = service["service"]
                frequency = service["frequency"]
                
                LOGGER.info(f"   🔍 Evaluating Service ID {service_id}: {function_app}/{service_name}")
                
                results["processed"] += 1
                
                try:
                    # Check if service should be triggered (unless forced or bypassing window check)
                    if force_service_ids and service_id in force_service_ids:
                        # Force execution for specific service IDs
                        should_trigger = True
                        LOGGER.info(f"      🎯 FORCED execution for service {service_id}")
                    elif bypass_window_check:
                        # Bypass window check but still respect other scheduling rules
                        should_trigger = await should_trigger_service_bypass_window(service, current_time)
                        if should_trigger:
                            LOGGER.info(f"      ⚡ BYPASSED window check - service {service_id} will run")
                        else:
                            LOGGER.info(f"      ⏭️  BYPASSED window check - service {service_id} still not eligible")
                    else:
                        # Standard scheduling logic
                        should_trigger = await should_trigger_service(service, current_time)
                        if should_trigger:
                            LOGGER.info(f"      ✅ Service {service_id} is scheduled to run now")
                        else:
                            LOGGER.info(f"      ⏭️  Service {service_id} not scheduled for current time window")
                    
                    if not should_trigger:
                        results["skipped"] += 1
                        continue
                    
                    # Check trigger limit
                    if service["trigger_limit"] and service["triggered_count"] >= service["trigger_limit"]:
                        LOGGER.info(f"      🛑 Service {service_id} has reached trigger limit ({service['triggered_count']}/{service['trigger_limit']})")
                        await execute_sql_with_cold_start_retry(
                            sql_client,
                            f"UPDATE jgilpatrick.apps_central_scheduling SET status = 'completed' WHERE id = {service_id}",
                            method="execute",
                            title=f"Mark service {service_id} as completed (limit reached)"
                        )
                        results["skipped"] += 1
                        continue
                    
                    LOGGER.info(f"      🚀 Executing service {service_id}...")

                    # Mark as processing and set last_triggered_at
                    eastern_time_sql = get_eastern_time_sql()
                    await execute_sql_with_cold_start_retry(
                        sql_client,
                        f"""
                        UPDATE jgilpatrick.apps_central_scheduling
                        SET status = 'processing',
                            last_triggered_at = {eastern_time_sql}
                        WHERE id = {service_id}
                        """,
                        method="execute",
                        title=f"Mark service {service_id} as processing"
                    )

                    # Track triggered service
                    results["triggered_services"].append({
                        "function_app": function_app,
                        "service": service_name
                    })

                    # Execute the service
                    success, response_code, response_detail, log_id = await execute_service_request(
                        service, current_time, master_logger
                    )

                    if success:
                        # Update success counters and timestamps
                        sanitized_detail = sanitize_sql_string(response_detail)
                        next_status = get_next_status(service)
                        eastern_time_sql = get_eastern_time_sql()
                        
                        # Build log_id clause (NULL if not provided)
                        log_id_value = str(log_id) if log_id is not None else "NULL"
                        
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
                                retry_count = 0,
                                log_id = {log_id_value}
                            WHERE id = {service_id}
                            """,
                            method="execute",
                            title=f"Mark service {service_id} as successful"
                        )
                        results["successful"] += 1
                        if log_id is not None:
                            LOGGER.info(f"      ✅ Service {service_id} executed successfully (HTTP {response_code}, log_id: {log_id})")
                        else:
                            LOGGER.info(f"      ✅ Service {service_id} executed successfully (HTTP {response_code})")
                        
                        # Track successful execution
                        await track_event("scheduler_service_success", {
                            "service_id": service_id,
                            "function_app": function_app,
                            "service": service_name,
                            "frequency": frequency,
                            "triggered_count": service["triggered_count"] + 1,
                            "response_code": response_code,
                            "log_id": log_id
                        })
                        
                    else:
                        # Handle failure - no retries to prevent duplicate email sends
                        await handle_service_failure_no_retry(sql_client, service, current_time, response_code, response_detail, log_id)
                        results["failed"] += 1
                        if log_id is not None:
                            LOGGER.error(f"      ❌ Service {service_id} failed (HTTP {response_code}, log_id: {log_id}): {response_detail[:200]}...")
                        else:
                            LOGGER.error(f"      ❌ Service {service_id} failed (HTTP {response_code}): {response_detail[:200]}...")
                        
                except Exception as e:
                    error_msg = str(e)
                    results["failed"] += 1
                    results["errors"].append(f"Service {service_id} ({function_app}/{service_name}): {error_msg}")
                    
                    # Mark service as failed
                    await handle_service_exception(sql_client, service, error_msg)
                    
                    LOGGER.error(f"      💥 Service {service_id} exception: {error_msg}")
                    await track_exception(e, {
                        "service_id": service_id,
                        "function_app": function_app,
                        "service": service_name,
                        "operation": "process_scheduled_service"
                    })
        
        except Exception as e:
            error_msg = f"Error fetching or processing scheduled services: {str(e)}"
            results["errors"].append(error_msg)
            LOGGER.error(error_msg)
            await track_exception(e, {"operation": "fetch_scheduled_services"})

    return results


async def should_trigger_service(service: Dict[str, Any], current_time: datetime) -> bool:
    """Determine if a service should be triggered based on its schedule configuration."""
    return await should_trigger_service_bypass_window(service, current_time, check_window=True)


async def should_trigger_service_bypass_window(service: Dict[str, Any], current_time: datetime, check_window: bool = False) -> bool:
    """Determine if a service should be triggered, optionally bypassing window checks."""
    frequency = service["frequency"]
    last_triggered = service["last_triggered_at"]
    start_date = service["start_date"]
    schedule_config = service.get("schedule_config")
    
    # Parse last triggered time
    # Database stores Eastern time as naive datetime via jgilpatrick.GetEasternTime()
    eastern = pytz.timezone('US/Eastern')
    if last_triggered:
        if isinstance(last_triggered, str):
            # Try parsing with timezone info first (ISO format with Z or offset)
            try:
                if 'Z' in last_triggered or '+' in last_triggered or last_triggered.count('-') > 2:
                    # Has timezone info, assume UTC
                    utc_time = datetime.fromisoformat(last_triggered.replace('Z', '+00:00'))
                    last_triggered = utc_time.astimezone(eastern)
                else:
                    # No timezone info, assume Eastern (from SQL GetEasternTime)
                    last_triggered = datetime.fromisoformat(last_triggered)
                    last_triggered = eastern.localize(last_triggered)
            except (ValueError, AttributeError):
                # Fallback: parse as naive and assume Eastern
                last_triggered = datetime.fromisoformat(last_triggered)
                last_triggered = eastern.localize(last_triggered)
        elif hasattr(last_triggered, 'tzinfo'):
            if last_triggered.tzinfo is None:
                # Naive datetime from SQL - assume Eastern time
                last_triggered = eastern.localize(last_triggered)
            # If already timezone-aware, leave as is
    
    # Parse start date
    # Database stores Eastern time as naive datetime via jgilpatrick.GetEasternTime()
    if isinstance(start_date, str):
        try:
            if 'Z' in start_date or '+' in start_date or start_date.count('-') > 2:
                # Has timezone info, assume UTC
                utc_time = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                start_date = utc_time.astimezone(eastern)
            else:
                # No timezone info, assume Eastern (from SQL GetEasternTime)
                start_date = datetime.fromisoformat(start_date)
                start_date = eastern.localize(start_date)
        except (ValueError, AttributeError):
            start_date = datetime.fromisoformat(start_date)
            start_date = eastern.localize(start_date)
    elif hasattr(start_date, 'tzinfo'):
        if start_date.tzinfo is None:
            # Naive datetime from SQL - assume Eastern time
            start_date = eastern.localize(start_date)
        # If already timezone-aware, leave as is
    
    # Universal activation check - service doesn't run until start_date is reached
    if start_date > current_time:
        return False
    
    if frequency == "once":
        # One-time trigger: check if not already triggered
        # Note: start_date check already done above
        return last_triggered is None
    
    elif frequency == "daily":
        if schedule_config:
            try:
                config = json.loads(schedule_config)
                times = config.get("times", ["00:00"])
                
                # Check if current time is within any configured time windows (if window check enabled)
                if check_window:
                    matches_time_window = any(
                        is_within_schedule_window(current_time, time_str) 
                        for time_str in times
                    )
                    
                    if not matches_time_window:
                        return False
                
                # Check if already triggered today
                if last_triggered:
                    return last_triggered.date() < current_time.date()
                return True
                
            except (json.JSONDecodeError, KeyError):
                LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {schedule_config}")
                return False
    
    elif frequency == "weekly":
        if schedule_config:
            try:
                config = json.loads(schedule_config)
                days = config.get("days", ["monday"])
                time_str = config.get("time", "00:00")
                
                # Check if current day matches
                current_day = current_time.strftime("%A").lower()
                if current_day not in [day.lower() for day in days]:
                    return False
                
                # Check if current time is within schedule window (if window check enabled)
                if check_window and not is_within_schedule_window(current_time, time_str):
                    return False
                
                # Check if already triggered today (allows multiple days per week)
                # Services can run on multiple days like Monday AND Friday
                if last_triggered:
                    # Only skip if already triggered today
                    return last_triggered.date() != current_time.date()
                return True
                
            except (json.JSONDecodeError, KeyError):
                LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {schedule_config}")
                return False
    
    elif frequency == "hourly":
        if schedule_config:
            try:
                config = json.loads(schedule_config)

                # Support both single "minute" and multiple "minutes"
                if "minutes" in config:
                    minutes = config["minutes"]
                    if not isinstance(minutes, list):
                        minutes = [minutes]
                else:
                    minute = config.get("minute", 0)
                    minutes = [minute]

                # Check if current minute matches any configured minute (if window check enabled)
                if check_window and current_time.minute not in minutes:
                    return False

                # Check if already triggered at this minute in this hour
                if last_triggered:
                    # Only re-run if:
                    # - Different hour, OR
                    # - Different date, OR
                    # - Same hour but different minute (for multiple runs per hour)
                    same_hour_same_date = (
                        last_triggered.hour == current_time.hour and
                        last_triggered.date() == current_time.date()
                    )

                    if same_hour_same_date:
                        # Check if we already ran at this specific minute
                        # Allow re-run if current minute is different from last triggered minute
                        return last_triggered.minute != current_time.minute
                    else:
                        # Different hour or date, allow run
                        return True

                return True

            except (json.JSONDecodeError, KeyError):
                LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {schedule_config}")
                return False
    
    elif frequency == "monthly":
        if schedule_config:
            try:
                config = json.loads(schedule_config)
                day = config.get("day", 1)
                time_str = config.get("time", "00:00")
                
                # Check if current day of month matches
                if current_time.day != day:
                    return False
                
                # Check if current time is within schedule window (if window check enabled)
                if check_window and not is_within_schedule_window(current_time, time_str):
                    return False
                
                # Check if already triggered this month
                if last_triggered:
                    return (last_triggered.month != current_time.month or 
                           last_triggered.year != current_time.year)
                return True
                
            except (json.JSONDecodeError, KeyError):
                LOGGER.warning(f"Invalid schedule_config for service {service['id']}: {schedule_config}")
                return False
    
    # Default: don't trigger for unknown frequencies
    return False


async def poll_master_log_for_completion(log_id: str) -> tuple[bool, int, str]:
    """
    Poll the master services log for the given log_id until completion.
    Returns (success, http_like_code, detail_message).
    Polls indefinitely until the service completes.
    """
    while True:
        try:
            async with SQLClient() as sql_client:
                rows = await sql_client.execute(
                    f"""
                    SELECT status, error_message
                    FROM jgilpatrick.apps_master_services_log
                    WHERE log_id = {log_id}
                    """,
                    method="query",
                    title=f"Poll master log {log_id}"
                )

            if rows and isinstance(rows, list):
                status_val = (rows[0].get("status") or "").lower()
                error_msg = rows[0].get("error_message") or ""

                if status_val in ("success", "failed", "warning"):
                    if status_val == "success":
                        return True, 200, "Master log status: success"
                    if status_val == "warning":
                        # Treat as non-fatal but not success
                        return False, 200, "Master log status: warning"
                    # failed
                    detail = f"Master log status: failed{(f' - {error_msg}' if error_msg else '')}"
                    return False, 500, detail

            # Not complete yet; wait and continue polling
            await asyncio.sleep(POLLING_INTERVAL)
            continue

        except Exception as e:
            LOGGER.error(f"Error polling master log {log_id}: {str(e)}")
            return False, 500, f"Polling error: {str(e)}"


def get_next_status(service: Dict[str, Any]) -> str:
    """Determine the next status for a service after successful execution."""
    frequency = service["frequency"]
    trigger_limit = service["trigger_limit"]
    triggered_count = service["triggered_count"]
    
    # If it's a one-time service or has reached its limit, mark as completed
    if frequency == "once" or (trigger_limit and triggered_count + 1 >= trigger_limit):
        return "completed"
    
    # Otherwise, reset to pending for recurring services
    return "pending"


async def handle_service_failure_no_retry(sql_client: SQLClient, service: Dict[str, Any], current_time: datetime, response_code: int, response_detail: str, log_id: Optional[int] = None) -> None:
    """Handle service execution failure without retries to prevent duplicate email sends."""
    service_id = service["id"]
    
    # Sanitize response detail
    sanitized_detail = sanitize_sql_string(response_detail)
    eastern_time_sql = get_eastern_time_sql()
    
    # Build log_id value (NULL if not provided)
    log_id_value = str(log_id) if log_id is not None else "NULL"
    
    # Mark as failed immediately - no retries to prevent duplicate email sends
    await execute_sql_with_cold_start_retry(
        sql_client,
        f"""
        UPDATE jgilpatrick.apps_central_scheduling
        SET status = 'failed',
            processed_at = {eastern_time_sql},
            last_response_code = {response_code},
            last_response_detail = '{sanitized_detail}',
            error_message = 'Service execution failed with HTTP {response_code}',
            log_id = {log_id_value}
        WHERE id = {service_id}
        """,
        method="execute",
        title=f"Mark service {service_id} as failed (no retry)"
    )
    if log_id is not None:
        LOGGER.error(f"Service {service_id} failed and marked as failed (log_id: {log_id}, no retries to prevent duplicate emails)")
    else:
        LOGGER.error(f"Service {service_id} failed and marked as failed (no retries to prevent duplicate emails)")


def is_sleeping_service_response(response_code: int, response_detail: str) -> bool:
    """Determine if the response indicates a sleeping Azure Function App."""
    # Common indicators of sleeping services
    sleeping_indicators = [
        "function host is not running",
        "service unavailable",
        "502 bad gateway",
        "503 service unavailable",
        "cold start",
        "warming up",
        "host not available"
    ]
    
    # Check for specific status codes that often indicate sleeping services
    if response_code in [502, 503, 504]:
        return True
    
    # Check response text for sleeping indicators
    if response_detail:
        response_lower = response_detail.lower()
        return any(indicator in response_lower for indicator in sleeping_indicators)
    
    return False


async def execute_service_request(
    service: Dict[str, Any],
    current_time: datetime,
    master_logger: Optional[ServiceLogger] = None
) -> tuple[bool, int, str, Optional[int]]:
    """Execute HTTP request to the service trigger URL and 202 polling.

    Returns:
        Tuple of (success, response_code, response_detail, log_id)
        log_id is extracted from response body if present, None otherwise.
    """
    service_id = service["id"]
    trigger_url = service["trigger_url"]
    json_body = service["json_body"]

    try:
        # Parse JSON body
        try:
            parsed_json = json.loads(json_body) if isinstance(json_body, str) else json_body

            # Add parent context to JSON body if master_logger is provided
            if master_logger and master_logger.log_id is not None:
                child_context = master_logger.get_child_context()
                parsed_json.update(child_context)
                LOGGER.info(f"Service {service_id}: Added parent context - parent_service_id: {child_context['parent_service_id']}, root_id: {child_context['root_id']}")

        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON body: {str(e)}"
            LOGGER.error(f"Service {service_id} has invalid JSON body: {error_msg}")
            return False, 400, error_msg, None  # 400 Bad Request for invalid JSON

        # Make HTTP request - NO RETRIES to prevent duplicate email sends
        try:
            async with httpx.AsyncClient(timeout=SERVICE_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    trigger_url,
                    json=parsed_json,
                    headers={"Content-Type": "application/json"}
                )

                # Get response details
                response_code = response.status_code
                try:
                    response_detail = response.text[:4000]  # Limit response size
                except Exception:
                    response_detail = "Unable to read response body"

                # Extract log_id from response body if present (for any 2xx response)
                log_id = None
                try:
                    body_json = response.json()
                    if isinstance(body_json, dict):
                        log_id = body_json.get("log_id")
                        if log_id is not None:
                            try:
                                log_id = int(log_id)  # Ensure it's an integer
                            except (ValueError, TypeError):
                                log_id = None
                except Exception:
                    # Response might not be JSON, that's okay
                    pass

                # Handle 202 Accepted - start polling master services log by log_id (no HTTP polling)
                if response_code == 202:
                    LOGGER.info(f"Service {service_id} returned 202 Accepted, parsing log_id and polling master services log...")
                    if log_id is None:
                        LOGGER.error(f"Service {service_id} returned 202 but no log_id found in response; cannot poll status")
                        return False, 500, "202 Accepted but missing log_id for status polling", None

                    success, final_code, final_detail = await poll_master_log_for_completion(str(log_id))
                    return success, final_code, final_detail, log_id

                # Consider 2xx status codes as successful
                elif 200 <= response_code < 300:
                    if log_id is not None:
                        LOGGER.info(f"Service {service_id} HTTP request successful: {response_code} (log_id: {log_id})")
                    else:
                        LOGGER.info(f"Service {service_id} HTTP request successful: {response_code}")
                    return True, response_code, response_detail, log_id
                else:
                    LOGGER.error(f"Service {service_id} HTTP request failed: {response_code} - {response_detail}")
                    return False, response_code, response_detail, log_id

        except httpx.TimeoutException:
            error_msg = f"HTTP request timed out after {SERVICE_REQUEST_TIMEOUT} seconds"
            LOGGER.error(f"Service {service_id} HTTP request timed out")
            return False, 408, error_msg, None  # 408 Request Timeout
        except Exception as e:
            error_msg = f"HTTP request error: {str(e)}"
            LOGGER.error(f"Service {service_id} HTTP request error: {error_msg}")
            return False, 500, error_msg, None  # 500 Internal Server Error for connection errors

    except Exception as e:
        error_msg = f"Service execution error: {str(e)}"
        LOGGER.error(f"Service {service_id} execution error: {error_msg}")
        return False, 500, error_msg, None  # 500 Internal Server Error


async def handle_service_exception(sql_client: SQLClient, service: Dict[str, Any], error_msg: str) -> None:
    """Handle service processing exception."""
    service_id = service["id"]
    
    # Sanitize error message
    sanitized_error = sanitize_sql_string(error_msg)
    eastern_time_sql = get_eastern_time_sql()
    
    try:
        await execute_sql_with_cold_start_retry(
            sql_client,
            f"""
            UPDATE jgilpatrick.apps_central_scheduling
            SET status = 'failed',
                processed_at = {eastern_time_sql},
                last_response_detail = NULL,
                error_message = '{sanitized_error}'
            WHERE id = {service_id}
            """,
            method="execute",
            title=f"Mark service {service_id} as failed due to exception"
        )
    except Exception as db_error:
        LOGGER.error(f"Failed to update service {service_id} status in database: {str(db_error)}")


# Create the blueprint for scheduler functions
bp = func.Blueprint()

@bp.timer_trigger(schedule="0 0,15,30,45 * * * *", arg_name="timer", run_on_startup=False)
async def scheduler_timer(timer: func.TimerRequest) -> None:
    """
    Timer function that runs every 15 minutes (at 00, 15, 30, 45 minutes past the hour) to process scheduled services.
    
    This function:
    1. Queries the jgilpatrick.apps_central_scheduling table for active services
    2. Evaluates each service's schedule configuration to determine if it should run
    3. Makes HTTP requests to the specified trigger URLs with JSON payloads
    4. Updates service status, counters, and timestamps in the database
    5. Handles retries for failed services and respects trigger limits
    6. Passes parent context (log_id) to child services for workflow tracking
    7. Only logs to master services log when services are actually triggered
    """
    LOGGER.info("Scheduler timer function started")
    
    # Initialize master service logger for this timer execution (but don't log yet)
    master_logger = ServiceLogger(
        service_name="scheduler_timer",
        function_app="fx-app-apps-services",
        trigger_source="timer"
    )
    
    async with SQLClient() as sql_client:
        try:
            # Track the timer execution start
            await track_event("scheduler_timer_started", {
                "is_past_due": timer.past_due,
                "schedule_status": str(timer.schedule_status) if timer.schedule_status else None
            })
            
            if timer.past_due:
                LOGGER.warning("Timer function is running late")
            
            # Process scheduled services with master logger context
            results = await process_scheduled_services_with_overrides(
                master_logger=master_logger
            )

            # Log summary
            summary = (
                f"Scheduler timer completed - "
                f"Processed: {results['processed']}, "
                f"Successful: {results['successful']}, "
                f"Failed: {results['failed']}, "
                f"Skipped: {results['skipped']}, "
                f"Stuck services found: {results['stuck_services_found']}"
            )
            LOGGER.info(summary)
            
            # Track completion
            await track_event("scheduler_timer_completed", {
                **results
            })
            
            # Only log to master services log if services were actually executed (not just processed)
            if results['successful'] > 0 or results['failed'] > 0:
                # Log start of timer execution to master services log
                await master_logger.log_start(
                    sql_client,
                    request_data=json.dumps({
                        "is_past_due": timer.past_due,
                        "schedule_status": str(timer.schedule_status) if timer.schedule_status else None
                    }),
                    metadata={
                        "function_type": "timer",
                        "schedule": "0 0,15,30,45 * * * *",
                        "is_past_due": timer.past_due
                    }
                )
                
                LOGGER.info(f"Scheduler timer logged to master services log with ID: {master_logger.log_id}")
                
                if results["errors"]:
                    LOGGER.error(f"Errors during processing: {results['errors']}")
                    # Log as warning since some services succeeded
                    await master_logger.log_warning(
                        sql_client,
                        f"Completed with {len(results['errors'])} errors",
                        response_data=json.dumps(results),
                        metadata={
                            "errors": results['errors']
                        }
                    )
                else:
                    # Log successful completion
                    await master_logger.log_success(
                        sql_client,
                        response_data=json.dumps(results),
                        metadata={}
                    )
            else:
                LOGGER.info("No services were triggered - skipping master services log entry")
                
        except Exception as e:
            error_msg = f"Scheduler timer function failed: {str(e)}"
            LOGGER.error(error_msg)
            
            # Log error to master services log if possible
            try:
                await master_logger.log_error(
                    sql_client,
                    error_msg,
                    metadata={"exception_type": type(e).__name__}
                )
            except Exception as log_error:
                LOGGER.error(f"Failed to log error to master services log: {str(log_error)}")
            
            await track_exception(e, {
                "operation": "scheduler_timer",
                "master_log_id": master_logger.log_id
            })
            raise


@bp.route(route="scheduler/manual-trigger", methods=["POST"])
async def scheduler_http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger for manual scheduler execution.
    
    This allows manual triggering of the scheduler outside of the normal 15-minute intervals.
    Uses the same logic as the timer function but can be called on-demand.
    
    POST /api/scheduler/manual-trigger
    
    Optional JSON body:
    {
        "bypass_window_check": false,  // If true, runs all eligible services regardless of time windows
        "force_service_ids": [1, 2, 3], // If provided, only runs these specific service IDs
        "schedule_id": 1  // If provided, forces execution of this specific schedule ID regardless of all conditions
    }
    
    Returns:
    {
        "success": true,
        "message": "Scheduler executed successfully",
        "results": {
            "processed": 5,
            "successful": 3, 
            "failed": 1,
            "skipped": 1,
            "timed_out": 0,
            "errors": []
        },
        "execution_time_seconds": 45.2,
        "timeout_reached": false
    }
    """
    start_time = time.time()
    
    # Get request details for logging
    request_method = req.method
    request_url = req.url
    request_headers = dict(req.headers) if req.headers else {}
    
    LOGGER.info(f"🔥 HTTP SCHEDULER TRIGGER STARTED")
    LOGGER.info(f"   📨 Request: {request_method} {request_url}")
    LOGGER.info(f"   🕐 Timestamp: {datetime.now().isoformat()}")
    
    # Initialize master service logger for this HTTP execution
    master_logger = ServiceLogger(
        service_name="scheduler_http_trigger", 
        function_app="fx-app-apps-services",
        trigger_source="HTTP"
    )
    
    async with SQLClient() as sql_client:
        try:
            # Log start of HTTP execution to master services log
            await master_logger.log_start(
                sql_client,
                request_data=json.dumps({
                    "method": request_method,
                    "url": str(request_url),
                    "headers": request_headers
                }),
                metadata={
                    "function_type": "HTTP",
                    "endpoint": "scheduler/manual-trigger"
                }
            )
            
            LOGGER.info(f"HTTP Scheduler trigger logged to master services log with ID: {master_logger.log_id}")
            
            # Parse request body for options
            bypass_window_check = False
            force_service_ids = None
            schedule_id = None
            request_body_str = "empty"
            
            try:
                if req.get_body():
                    request_body_str = req.get_body().decode('utf-8')
                    LOGGER.info(f"   📝 Request Body: {request_body_str}")
                    
                    body = req.get_json()
                    if body:
                        bypass_window_check = body.get("bypass_window_check", False)
                        force_service_ids = body.get("force_service_ids")
                        schedule_id = body.get("schedule_id")
                        
                        LOGGER.info(f"   ⚙️  Configuration:")
                        LOGGER.info(f"      • bypass_window_check: {bypass_window_check}")
                        LOGGER.info(f"      • force_service_ids: {force_service_ids}")
                        LOGGER.info(f"      • schedule_id: {schedule_id}")
                else:
                    LOGGER.info(f"   📝 Request Body: {request_body_str}")
                    
            except (ValueError, TypeError) as e:
                LOGGER.warning(f"   ⚠️  Invalid JSON in request body: {str(e)}")
                LOGGER.warning(f"   📝 Raw body: {request_body_str}")
            
            # Determine execution mode
            if schedule_id:
                execution_mode = f"FORCE_SCHEDULE_ID ({schedule_id})"
            elif force_service_ids:
                execution_mode = f"FORCE_SERVICES (IDs: {force_service_ids})"
            elif bypass_window_check:
                execution_mode = "BYPASS_WINDOWS"
            else:
                execution_mode = "STANDARD"
            
            LOGGER.info(f"   🎯 Execution Mode: {execution_mode}")
            
            # Track the HTTP execution
            await track_event("scheduler_http_trigger_started", {
                "bypass_window_check": bypass_window_check,
                "force_service_ids": force_service_ids is not None,
                "forced_service_count": len(force_service_ids) if force_service_ids else 0,
                "schedule_id": schedule_id,
                "execution_mode": execution_mode,
                "request_method": request_method,
                "has_request_body": request_body_str != "empty",
                "master_log_id": master_logger.log_id
            })
            
            # Process scheduled services with optional overrides
            LOGGER.info(f"   🚀 Starting service processing...")

            if schedule_id:
                # Force execution of specific schedule_id - bypass ALL checks
                results = await process_scheduled_services_with_overrides(
                    bypass_window_check=True,
                    force_service_ids=[schedule_id],
                    master_logger=master_logger
                )
            elif bypass_window_check or force_service_ids:
                results = await process_scheduled_services_with_overrides(
                    bypass_window_check=bypass_window_check,
                    force_service_ids=force_service_ids,
                    master_logger=master_logger
                )
            else:
                # Use standard processing logic
                results = await process_scheduled_services_with_overrides(
                    master_logger=master_logger
                )

            execution_time = time.time() - start_time

            LOGGER.info(f"   ✅ HTTP SCHEDULER COMPLETED")
            LOGGER.info(f"   📊 EXECUTION SUMMARY:")
            LOGGER.info(f"      • Execution Time: {execution_time:.1f}s")
            LOGGER.info(f"      • Services Processed: {results['processed']}")
            LOGGER.info(f"      • ✅ Successful: {results['successful']}")
            LOGGER.info(f"      • ❌ Failed: {results['failed']}")
            LOGGER.info(f"      • ⏭️  Skipped: {results['skipped']}")
            LOGGER.info(f"      • 🔍 Stuck Services Found: {results['stuck_services_found']}")

            if results['errors']:
                LOGGER.error(f"   🚨 ERRORS ENCOUNTERED:")
                for i, error in enumerate(results['errors'], 1):
                    LOGGER.error(f"      {i}. {error}")
            else:
                LOGGER.info(f"      • 🎉 No errors encountered")

            # Track completion
            await track_event("scheduler_http_trigger_completed", {
                **results,
                "execution_time_seconds": execution_time,
                "master_log_id": master_logger.log_id
            })

            # Return success response
            response_data = {
                "success": True,
                "message": "Scheduler executed successfully",
                "results": results,
                "execution_time_seconds": round(execution_time, 2),
                "master_log_id": master_logger.log_id
            }

            # Log completion to master services log
            if results["errors"]:
                await master_logger.log_warning(
                    sql_client,
                    f"Completed with {len(results['errors'])} errors",
                    response_data=json.dumps(response_data),
                    metadata={
                        "execution_mode": execution_mode,
                        "errors": results['errors']
                    }
                )
            else:
                await master_logger.log_success(
                    sql_client,
                    response_data=json.dumps(response_data),
                    metadata={
                        "execution_mode": execution_mode
                    }
                )
            
            return func.HttpResponse(
                json.dumps(response_data, indent=2),
                status_code=200,
                mimetype="application/json"
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"HTTP scheduler trigger failed: {str(e)}"
            
            LOGGER.error(f"   💥 HTTP SCHEDULER FAILED")
            LOGGER.error(f"   📊 FAILURE SUMMARY:")
            LOGGER.error(f"      • Execution Time: {execution_time:.1f}s")
            LOGGER.error(f"      • Error: {error_msg}")
            LOGGER.error(f"      • Exception Type: {type(e).__name__}")
            
            # Log error to master services log if possible
            try:
                await master_logger.log_error(
                    sql_client,
                    error_msg,
                    metadata={
                        "execution_time_seconds": execution_time,
                        "exception_type": type(e).__name__,
                        "request_method": request_method,
                        "request_body": request_body_str
                    }
                )
            except Exception as log_error:
                LOGGER.error(f"Failed to log error to master services log: {str(log_error)}")
            
            await track_exception(e, {
                "operation": "scheduler_http_trigger",
                "master_log_id": master_logger.log_id if hasattr(master_logger, 'log_id') else None
            })
            
            # Return error response
            error_response = {
                "success": False,
                "message": error_msg,
                "execution_time_seconds": round(execution_time, 2),
                "error": str(e),
                "error_type": type(e).__name__,
                "master_log_id": master_logger.log_id if hasattr(master_logger, 'log_id') and master_logger.log_id else None
            }
            
            return func.HttpResponse(
                json.dumps(error_response, indent=2),
                status_code=500,
                mimetype="application/json"
            )
