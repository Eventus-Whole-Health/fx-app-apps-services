"""Keystone-platform proxy function.

Accepts {path, method, body} and forwards the call to keystone-platform
using the function app's managed identity (DefaultAzureCredential).

Designed to be called by the scheduler — apps_central_scheduling rows
set trigger_url to this endpoint and put the keystone path/body in json_body.
"""
import json
import logging

import azure.functions as func

from functions.shared.keystone_client import KeystoneClient, KeystoneAPIError
from functions.shared.master_service_logger import MasterServiceLogger
from functions.shared.sql_client import SQLClient

logger = logging.getLogger(__name__)

bp = func.Blueprint()


async def keystone_proxy_handler(req: func.HttpRequest) -> func.HttpResponse:
    """Core handler — separated for testability."""
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    path = body.get("path")
    if not path:
        return func.HttpResponse(
            json.dumps({"error": "Missing required field: path"}),
            status_code=400,
            mimetype="application/json",
        )

    method = body.get("method", "POST").upper()
    request_body = body.get("body", {})

    async with SQLClient() as sql:
        svc_logger = MasterServiceLogger(
            "keystone_proxy",
            function_app="fx-app-apps-services",
            trigger_source="http",
        )
        await svc_logger.log_start(sql, request_data=json.dumps(body))

        try:
            client = KeystoneClient()
            try:
                if method == "GET":
                    result = await client.get(path)
                else:
                    result = await client.post(path, request_body)
            finally:
                await client.close()

            await svc_logger.log_success(
                sql, response_data=json.dumps({"path": path, "method": method})
            )

            return func.HttpResponse(
                json.dumps({"status": "ok", "result": result}),
                status_code=200,
                mimetype="application/json",
            )

        except KeystoneAPIError as e:
            await svc_logger.log_error(sql, error_message=str(e))
            return func.HttpResponse(
                json.dumps({"error": str(e), "keystone_status": e.status_code}),
                status_code=e.status_code,
                mimetype="application/json",
            )
        except Exception as e:
            await svc_logger.log_error(sql, error_message=str(e))
            logger.error(f"keystone_proxy unexpected error: {e}")
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                mimetype="application/json",
            )


@bp.route(route="keystone-proxy", methods=["POST"])
async def keystone_proxy(req: func.HttpRequest) -> func.HttpResponse:
    return await keystone_proxy_handler(req)
