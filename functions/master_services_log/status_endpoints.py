"""Master Services Log status and result endpoints."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import azure.functions as func

from ..shared.sql_client import SQLClient
from ..shared.telemetry import track_event, track_exception

LOGGER = logging.getLogger(__name__)

# Create the blueprint for master services log endpoints
bp = func.Blueprint()

LOGGER.info("🔧 Master Services Log Blueprint Created!")
LOGGER.info("📋 Registering status endpoints...")


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
    
    # Escape single quotes by doubling them
    sanitized = value.replace("'", "''")
    
    # Replace other potentially problematic characters
    sanitized = sanitized.replace("\x00", "")  # Remove null bytes
    sanitized = sanitized.replace("\\", "\\\\")  # Escape backslashes
    
    return sanitized


async def get_master_log_entry(log_id: str, sql_client: SQLClient) -> Optional[Dict[str, Any]]:
    """
    Get master services log entry by log_id.
    
    Args:
        log_id: The log_id to query for
        sql_client: SQL client instance
        
    Returns:
        Dictionary with log entry data or None if not found
    """
    try:
        # Validate log_id is numeric
        try:
            int(log_id)
        except ValueError:
            LOGGER.warning(f"Invalid log_id format: {log_id}")
            return None
        
        # Query master services log
        query = f"""
        SELECT 
            log_id,
            root_id,
            parent_id,
            function_app,
            service_name,
            invocation_id,
            started_at,
            ended_at,
            duration_ms,
            status,
            trigger_source,
            error_message,
            request,
            response,
            metadata
        FROM jgilpatrick.apps_master_services_log
        WHERE log_id = {log_id}
        """
        
        result = await sql_client.execute(
            query,
            method="query",
            title=f"Get master log entry {log_id}"
        )
        
        if result and isinstance(result, list) and len(result) > 0:
            return result[0]
        
        return None
        
    except Exception as e:
        LOGGER.error(f"Error querying master services log for log_id {log_id}: {str(e)}")
        await track_exception(e, {
            "operation": "get_master_log_entry",
            "log_id": log_id
        })
        raise


@bp.route(route="status/{log_id}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def get_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get the status of a service execution by log_id.
    
    Returns basic status information including:
    - log_id
    - status (pending, success, failed, warning)
    - started_at, ended_at, duration_ms
    - function_app, service_name
    - error_message (if failed)
    
    GET /api/status/{log_id}
    
    Response:
    {
        "log_id": 1001,
        "status": "success", 
        "started_at": "2025-01-15T10:30:00",
        "ended_at": "2025-01-15T10:32:15",
        "duration_ms": 135000,
        "function_app": "pims_services",
        "service_name": "facility_batch_orchestrator",
        "error_message": null,
        "metadata": {
            "invocation_id": "abc-123",
            "trigger_source": "HTTP"
        }
    }
    """
    LOGGER.info("🚀 GET_STATUS FUNCTION CALLED!")
    LOGGER.info(f"📝 Request URL: {req.url}")
    LOGGER.info(f"📝 Request method: {req.method}")
    LOGGER.info(f"📝 Route params: {req.route_params}")
    
    # Extract log_id from route parameters
    log_id = req.route_params.get('log_id')
    
    LOGGER.info(f"🔍 Extracted log_id: {log_id}")
    LOGGER.info(f"📊 Status request for log_id: {log_id}")
    
    if not log_id:
        return func.HttpResponse(
            json.dumps({
                "error": "Missing log_id parameter",
                "message": "log_id is required in URL path"
            }),
            status_code=400,
            mimetype="application/json"
        )
    
    async with SQLClient() as sql_client:
        try:
            # Track the request
            await track_event("master_log_status_request", {
                "log_id": log_id,
                "endpoint": "status"
            })
            
            # Get log entry
            log_entry = await get_master_log_entry(log_id, sql_client)
            
            if not log_entry:
                # Track not found
                await track_event("master_log_status_not_found", {
                    "log_id": log_id,
                    "endpoint": "status"
                })
                
                return func.HttpResponse(
                    json.dumps({
                        "error": "Log entry not found",
                        "message": f"No master services log entry found for log_id: {log_id}"
                    }),
                    status_code=404,
                    mimetype="application/json"
                )
            
            # Build status response
            response_data = {
                "log_id": log_entry["log_id"],
                "status": log_entry["status"],
                "started_at": log_entry["started_at"] if isinstance(log_entry["started_at"], str) else (log_entry["started_at"].isoformat() if log_entry["started_at"] else None),
                "ended_at": log_entry["ended_at"] if isinstance(log_entry["ended_at"], str) else (log_entry["ended_at"].isoformat() if log_entry["ended_at"] else None),
                "duration_ms": log_entry["duration_ms"],
                "function_app": log_entry["function_app"],
                "service_name": log_entry["service_name"],
                "error_message": log_entry["error_message"],
                "metadata": {
                    "invocation_id": log_entry["invocation_id"],
                    "trigger_source": log_entry["trigger_source"],
                    "root_id": log_entry["root_id"],
                    "parent_id": log_entry["parent_id"]
                }
            }
            
            # Add parsed metadata if available
            if log_entry.get("metadata"):
                try:
                    parsed_metadata = json.loads(log_entry["metadata"])
                    response_data["metadata"].update(parsed_metadata)
                except (json.JSONDecodeError, TypeError):
                    # Keep metadata as string if it can't be parsed
                    response_data["metadata"]["raw_metadata"] = log_entry["metadata"]
            
            # Track successful response
            await track_event("master_log_status_success", {
                "log_id": log_id,
                "status": log_entry["status"],
                "function_app": log_entry["function_app"],
                "service_name": log_entry["service_name"]
            })
            
            return func.HttpResponse(
                json.dumps(response_data, indent=2, default=str),
                status_code=200,
                mimetype="application/json"
            )
            
        except Exception as e:
            error_msg = f"Error retrieving status for log_id {log_id}: {str(e)}"
            LOGGER.error(error_msg)
            
            await track_exception(e, {
                "operation": "get_status",
                "log_id": log_id
            })
            
            return func.HttpResponse(
                json.dumps({
                    "error": "Internal server error",
                    "message": "An error occurred while retrieving the status"
                }),
                status_code=500,
                mimetype="application/json"
            )


@bp.route(route="result/<log_id>", methods=["GET"])
async def get_result(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get the complete result of a service execution by log_id.
    
    Returns full execution details including:
    - All status information
    - Complete request and response data
    - Full metadata
    
    GET /api/result/{log_id}
    
    Response:
    {
        "log_id": 1001,
        "status": "success",
        "started_at": "2025-01-15T10:30:00",
        "ended_at": "2025-01-15T10:32:15", 
        "duration_ms": 135000,
        "function_app": "pims_services",
        "service_name": "facility_batch_orchestrator",
        "invocation_id": "abc-123-def-456",
        "trigger_source": "HTTP",
        "error_message": null,
        "request": {
            "facility_id": 123,
            "max_patients": 500
        },
        "response": {
            "patients_processed": 342,
            "success": true,
            "completion_time": "10:32:15"
        },
        "metadata": {
            "total_input_tokens": 1500,
            "total_output_tokens": 800,
            "total_cost": 0.15
        },
        "workflow": {
            "root_id": 1001,
            "parent_id": null,
            "is_root": true
        }
    }
    """
    # Extract log_id from route parameters  
    log_id = req.route_params.get('log_id')
    
    LOGGER.info(f"Result request for log_id: {log_id}")
    
    if not log_id:
        return func.HttpResponse(
            json.dumps({
                "error": "Missing log_id parameter", 
                "message": "log_id is required in URL path"
            }),
            status_code=400,
            mimetype="application/json"
        )
    
    async with SQLClient() as sql_client:
        try:
            # Track the request
            await track_event("master_log_result_request", {
                "log_id": log_id,
                "endpoint": "result"
            })
            
            # Get log entry
            log_entry = await get_master_log_entry(log_id, sql_client)
            
            if not log_entry:
                # Track not found
                await track_event("master_log_result_not_found", {
                    "log_id": log_id,
                    "endpoint": "result"
                })
                
                return func.HttpResponse(
                    json.dumps({
                        "error": "Log entry not found",
                        "message": f"No master services log entry found for log_id: {log_id}"
                    }),
                    status_code=404,
                    mimetype="application/json"
                )
            
            # Parse request data
            request_data = None
            if log_entry.get("request"):
                try:
                    request_data = json.loads(log_entry["request"])
                except (json.JSONDecodeError, TypeError):
                    request_data = log_entry["request"]  # Keep as string if can't parse
            
            # Parse response data
            response_data = None
            if log_entry.get("response"):
                try:
                    response_data = json.loads(log_entry["response"])
                except (json.JSONDecodeError, TypeError):
                    response_data = log_entry["response"]  # Keep as string if can't parse
            
            # Parse metadata
            metadata = {}
            if log_entry.get("metadata"):
                try:
                    metadata = json.loads(log_entry["metadata"])
                except (json.JSONDecodeError, TypeError):
                    metadata = {"raw_metadata": log_entry["metadata"]}
            
            # Build complete result response
            result_data = {
                "log_id": log_entry["log_id"],
                "status": log_entry["status"],
                "started_at": log_entry["started_at"].isoformat() if log_entry["started_at"] else None,
                "ended_at": log_entry["ended_at"].isoformat() if log_entry["ended_at"] else None,
                "duration_ms": log_entry["duration_ms"],
                "function_app": log_entry["function_app"],
                "service_name": log_entry["service_name"],
                "invocation_id": log_entry["invocation_id"],
                "trigger_source": log_entry["trigger_source"],
                "error_message": log_entry["error_message"],
                "request": request_data,
                "response": response_data,
                "metadata": metadata,
                "workflow": {
                    "root_id": log_entry["root_id"],
                    "parent_id": log_entry["parent_id"],
                    "is_root": log_entry["parent_id"] is None
                }
            }
            
            # Track successful response
            await track_event("master_log_result_success", {
                "log_id": log_id,
                "status": log_entry["status"],
                "function_app": log_entry["function_app"],
                "service_name": log_entry["service_name"],
                "has_request_data": request_data is not None,
                "has_response_data": response_data is not None,
                "has_metadata": bool(metadata)
            })
            
            return func.HttpResponse(
                json.dumps(result_data, indent=2, default=str),
                status_code=200,
                mimetype="application/json"
            )
            
        except Exception as e:
            error_msg = f"Error retrieving result for log_id {log_id}: {str(e)}"
            LOGGER.error(error_msg)
            
            await track_exception(e, {
                "operation": "get_result",
                "log_id": log_id
            })
            
            return func.HttpResponse(
                json.dumps({
                    "error": "Internal server error",
                    "message": "An error occurred while retrieving the result"
                }),
                status_code=500,
                mimetype="application/json"
            )


@bp.route(route="health/master-services-log", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """
    Health check endpoint for master services log endpoints.
    
    GET /api/health/master-services-log
    
    Response:
    {
        "status": "healthy",
        "service": "master-services-log-endpoints",
        "timestamp": "2025-01-15T10:30:00Z",
        "database_connection": "ok"
    }
    """
    from datetime import datetime, timezone
    
    async with SQLClient() as sql_client:
        try:
            # Test database connection with simple query
            await sql_client.execute(
                "SELECT 1 as test",
                method="query",
                title="Health check - database connection test"
            )
            
            health_data = {
                "status": "healthy",
                "service": "master-services-log-endpoints",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "database_connection": "ok",
                "endpoints": [
                    "/api/status/{log_id}",
                    "/api/result/{log_id}",
                    "/api/health/master-services-log"
                ]
            }
            
            return func.HttpResponse(
                json.dumps(health_data, indent=2),
                status_code=200,
                mimetype="application/json"
            )
            
        except Exception as e:
            LOGGER.error(f"Health check failed: {str(e)}")
            
            health_data = {
                "status": "unhealthy",
                "service": "master-services-log-endpoints",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "database_connection": "failed",
                "error": str(e)
            }
            
            return func.HttpResponse(
                json.dumps(health_data, indent=2),
                status_code=503,
                mimetype="application/json"
            )
