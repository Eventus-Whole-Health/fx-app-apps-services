"""
Azure Function App Trigger Service

This module provides endpoints to trigger any cataloged Azure Function App
by ID or by function app name + function name.

Endpoints:
- POST /api/trigger/{id} - Trigger by catalog ID
- POST /api/trigger?app={app_name}&function={function_name} - Trigger by name
"""

import json
import logging
from typing import Any, Dict, Optional

import azure.functions as func
import httpx
from azure.identity.aio import DefaultAzureCredential

from functions.shared.service_logger import ServiceLogger
from functions.shared.sql_client import SQLClient

logger = logging.getLogger(__name__)


class FunctionAppTrigger:
    """Service for triggering cataloged Azure Function Apps."""

    def __init__(self):
        self.credential = DefaultAzureCredential()

    async def get_function_by_id(self, function_id: int) -> Optional[Dict[str, Any]]:
        """Get function details by catalog ID."""
        async with SQLClient() as sql:
            result = await sql.execute(
                f"SELECT * FROM jgilpatrick.apps_function_apps WHERE id = {function_id} AND is_active = 1",
                method="query"
            )
            return result[0] if result else None

    async def get_function_by_name(self, app_name: str, function_name: str) -> Optional[Dict[str, Any]]:
        """Get function details by app name and function name."""
        async with SQLClient() as sql:
            result = await sql.execute(
                f"SELECT * FROM jgilpatrick.apps_function_apps WHERE function_app_name = '{app_name}' AND function_name = '{function_name}' AND is_active = 1",
                method="query"
            )
            return result[0] if result else None

    async def get_azure_ad_token(self, resource_url: str) -> str:
        """Get Azure AD token for the target resource."""
        try:
            scope = f"{resource_url}/.default"
            token = await self.credential.get_token(scope)
            return token.token
        except Exception as e:
            logger.error(f"Failed to get Azure AD token: {e}")
            raise

    async def trigger_function(self, function_info: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Trigger a function with the given payload."""
        endpoint_url = function_info["endpoint_url"]
        requires_azure_ad = function_info["requires_azure_ad"]
        host_key = function_info.get("host_key")

        headers = {
            "Content-Type": "application/json"
        }

        # Handle authentication
        if requires_azure_ad:
            logger.info("Using Azure AD authentication")
            try:
                token = await self.get_azure_ad_token(endpoint_url)
                headers["Authorization"] = f"Bearer {token}"
            except Exception as e:
                logger.error(f"Azure AD authentication failed: {e}")
                raise
        elif host_key:
            logger.info("Using host key authentication")
            if "?code=" not in endpoint_url:
                separator = "&" if "?" in endpoint_url else "?"
                endpoint_url = f"{endpoint_url}{separator}code={host_key}"
        else:
            logger.warning("No authentication method available")

        # Make the HTTP request
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    endpoint_url,
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()

                try:
                    return response.json()
                except json.JSONDecodeError:
                    return {"response": response.text}

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Request failed: {e}")
                raise


# Create the blueprint
bp = func.Blueprint()


@bp.route(route="trigger/{function_id:int}", methods=["POST"])
async def trigger_by_id(req: func.HttpRequest) -> func.HttpResponse:
    """Trigger a function by its catalog ID."""
    try:
        function_id = req.route_params.get("function_id")
        payload = req.get_json() or {}

        logger.info(f"Triggering function by ID: {function_id}")

        # Initialize services
        trigger_service = FunctionAppTrigger()
        async with SQLClient() as sql:
            service_logger = ServiceLogger(
                "trigger_by_id",
                function_app="fx-app-apps-services",
                trigger_source="HTTP"
            )

            # Start logging
            log_id = await service_logger.log_start(
                sql,
                request_data=json.dumps(payload) if payload else None,
                metadata={"function_id": function_id, "payload_keys": list(payload.keys())}
            )

            try:
                # Get function details
                function_info = await trigger_service.get_function_by_id(int(function_id))
                if not function_info:
                    error_msg = f"Function with ID {function_id} not found or inactive"
                    await service_logger.log_error(sql, error_message=error_msg)
                    return func.HttpResponse(
                        json.dumps({"error": error_msg}),
                        status_code=404,
                        mimetype="application/json"
                    )

                # Trigger the function
                result = await trigger_service.trigger_function(function_info, payload)

                # Log success
                await service_logger.log_success(
                    sql,
                    response_data=json.dumps(result) if isinstance(result, (dict, list)) else str(result),
                    metadata={"endpoint": function_info["endpoint_url"]}
                )

                return func.HttpResponse(
                    json.dumps({
                        "success": True,
                        "function_id": function_id,
                        "function_app": function_info["function_app_name"],
                        "function_name": function_info["function_name"],
                        "result": result
                    }),
                    status_code=200,
                    mimetype="application/json"
                )

            except Exception as e:
                error_msg = f"Failed to trigger function: {str(e)}"
                await service_logger.log_error(
                    sql,
                    error_message=error_msg
                )
                return func.HttpResponse(
                    json.dumps({"error": error_msg}),
                    status_code=500,
                    mimetype="application/json"
                )

    except Exception as e:
        logger.error(f"Unexpected error in trigger_by_id: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Internal server error"}),
            status_code=500,
            mimetype="application/json"
        )


@bp.route(route="trigger", methods=["POST"])
async def trigger_by_name(req: func.HttpRequest) -> func.HttpResponse:
    """Trigger a function by app name and function name."""
    try:
        app_name = req.params.get("app")
        function_name = req.params.get("function")
        payload = req.get_json() or {}

        if not app_name or not function_name:
            return func.HttpResponse(
                json.dumps({"error": "Missing required parameters: app and function"}),
                status_code=400,
                mimetype="application/json"
            )

        logger.info(f"Triggering function by name: {app_name}/{function_name}")

        # Initialize services
        trigger_service = FunctionAppTrigger()
        async with SQLClient() as sql:
            service_logger = ServiceLogger(
                "trigger_by_name",
                function_app="fx-app-apps-services",
                trigger_source="HTTP"
            )

            # Start logging
            log_id = await service_logger.log_start(
                sql,
                request_data=json.dumps(payload) if payload else None,
                metadata={"app_name": app_name, "function_name": function_name, "payload_keys": list(payload.keys())}
            )

            try:
                # Get function details
                function_info = await trigger_service.get_function_by_name(app_name, function_name)
                if not function_info:
                    error_msg = f"Function {app_name}/{function_name} not found or inactive"
                    await service_logger.log_error(sql, error_message=error_msg)
                    return func.HttpResponse(
                        json.dumps({"error": error_msg}),
                        status_code=404,
                        mimetype="application/json"
                    )

                # Trigger the function
                result = await trigger_service.trigger_function(function_info, payload)

                # Log success
                await service_logger.log_success(
                    sql,
                    response_data=json.dumps(result) if isinstance(result, (dict, list)) else str(result),
                    metadata={"endpoint": function_info["endpoint_url"]}
                )

                return func.HttpResponse(
                    json.dumps({
                        "success": True,
                        "function_id": function_info["id"],
                        "function_app": function_info["function_app_name"],
                        "function_name": function_info["function_name"],
                        "result": result
                    }),
                    status_code=200,
                    mimetype="application/json"
                )

            except Exception as e:
                error_msg = f"Failed to trigger function: {str(e)}"
                await service_logger.log_error(
                    sql,
                    error_message=error_msg
                )
                return func.HttpResponse(
                    json.dumps({"error": error_msg}),
                    status_code=500,
                    mimetype="application/json"
                )

    except Exception as e:
        logger.error(f"Unexpected error in trigger_by_name: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Internal server error"}),
            status_code=500,
            mimetype="application/json"
        )


@bp.route(route="trigger/list", methods=["GET"])
async def list_functions(req: func.HttpRequest) -> func.HttpResponse:
    """List all available functions in the catalog."""
    try:
        async with SQLClient() as sql:
            result = await sql.execute(
                "SELECT id, function_app_name, function_name, function_description, endpoint_url, requires_azure_ad, is_active FROM jgilpatrick.apps_function_apps ORDER BY function_app_name, function_name",
                method="query"
            )

            return func.HttpResponse(
                json.dumps({
                    "success": True,
                    "functions": result
                }),
                status_code=200,
                mimetype="application/json"
            )

    except Exception as e:
        logger.error(f"Failed to list functions: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Failed to list functions"}),
            status_code=500,
            mimetype="application/json"
        )
