"""OTS Redis snapshot + auto-restore watchdog.

Problem: Onetime Secret (https://ots-ewh.azurewebsites.net) stores its admin
account on the shared Basic-tier Redis (DB 1), which has no persistence. Any
Azure host patching reboot wipes all keys, erasing the admin login.

Solution: capture a snapshot of DB 1 to blob storage once (after admin is
created), then let a timer re-seed DB 1 from that snapshot whenever the
sentinel admin key goes missing.

Endpoints:
    POST /api/ots-redis/snapshot   Capture current DB 1 → blob
    POST /api/ots-redis/restore    Force-restore from blob (admin operation)
    GET  /api/ots-redis/status     Report snapshot age + current sentinel state

Timer: runs every 15 minutes. If sentinel key is missing, restores from blob.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import azure.functions as func
import redis.asyncio as redis

from functions.shared.blob_client import get_blob_client
from functions.shared.master_service_logger import MasterServiceLogger
from functions.shared.settings import get_settings
from functions.shared.sql_client import SQLClient

LOGGER = logging.getLogger(__name__)

bp = func.Blueprint()

# OTS stores customers under UUID keys (customer:<uuid>:object). The email→uuid
# mapping lives in the customer:email_index hash, which is the stable sentinel:
# if that hash has our email field, the admin account survived.
EMAIL_INDEX_KEY = "customer:email_index"


async def _sentinel_present(client: redis.Redis, email: str) -> bool:
    return bool(await client.hexists(EMAIL_INDEX_KEY, email))


async def _connect() -> redis.Redis:
    settings = get_settings()
    if not settings.ots_redis_url:
        raise RuntimeError("OTS_REDIS_URL is not configured")
    return redis.from_url(settings.ots_redis_url, decode_responses=False)


async def _dump_db(client: redis.Redis) -> Dict[str, Any]:
    """Dump every key in the current DB to a JSON-serializable dict.

    Binary-safe: values are base64-encoded so any byte sequence round-trips.
    """
    entries: List[Dict[str, Any]] = []
    async for raw_key in client.scan_iter(count=500):
        key_type = (await client.type(raw_key)).decode("utf-8")
        pttl = await client.pttl(raw_key)
        entry: Dict[str, Any] = {
            "key": base64.b64encode(raw_key).decode("ascii"),
            "type": key_type,
            "pttl": pttl if pttl and pttl > 0 else None,
        }

        if key_type == "string":
            value = await client.get(raw_key)
            entry["value"] = base64.b64encode(value).decode("ascii") if value is not None else None
        elif key_type == "hash":
            data = await client.hgetall(raw_key)
            entry["value"] = {
                base64.b64encode(f).decode("ascii"): base64.b64encode(v).decode("ascii")
                for f, v in data.items()
            }
        elif key_type == "list":
            items = await client.lrange(raw_key, 0, -1)
            entry["value"] = [base64.b64encode(i).decode("ascii") for i in items]
        elif key_type == "set":
            items = await client.smembers(raw_key)
            entry["value"] = [base64.b64encode(i).decode("ascii") for i in items]
        elif key_type == "zset":
            items = await client.zrange(raw_key, 0, -1, withscores=True)
            entry["value"] = [
                [base64.b64encode(m).decode("ascii"), score] for m, score in items
            ]
        else:
            LOGGER.warning("Skipping unsupported key type %s", key_type)
            continue

        entries.append(entry)

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "key_count": len(entries),
        "entries": entries,
    }


async def _restore_db(client: redis.Redis, snapshot: Dict[str, Any]) -> int:
    """Restore keys from snapshot. Only writes keys that don't already exist."""
    restored = 0
    for entry in snapshot.get("entries", []):
        key = base64.b64decode(entry["key"])
        if await client.exists(key):
            continue

        key_type = entry["type"]
        value = entry["value"]

        if key_type == "string" and value is not None:
            await client.set(key, base64.b64decode(value))
        elif key_type == "hash":
            mapping = {
                base64.b64decode(f): base64.b64decode(v) for f, v in value.items()
            }
            if mapping:
                await client.hset(key, mapping=mapping)
        elif key_type == "list":
            items = [base64.b64decode(i) for i in value]
            if items:
                await client.rpush(key, *items)
        elif key_type == "set":
            items = [base64.b64decode(i) for i in value]
            if items:
                await client.sadd(key, *items)
        elif key_type == "zset":
            mapping = {base64.b64decode(m): score for m, score in value}
            if mapping:
                await client.zadd(key, mapping)
        else:
            continue

        pttl = entry.get("pttl")
        if pttl:
            await client.pexpire(key, pttl)

        restored += 1

    return restored


@bp.route(route="ots-redis/snapshot", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
async def snapshot_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    settings = get_settings()
    async with SQLClient() as sql:
        svc = MasterServiceLogger("ots_redis_snapshot", function_app="apps_services")
        await svc.log_start(sql, request_data="{}")

        client = await _connect()
        try:
            snapshot = await _dump_db(client)
        finally:
            await client.aclose()

        await get_blob_client().upload_json(settings.ots_snapshot_blob_path, snapshot)

        result = {
            "snapshot_path": settings.ots_snapshot_blob_path,
            "key_count": snapshot["key_count"],
            "captured_at": snapshot["captured_at"],
        }
        await svc.log_success(sql, response_data=json.dumps(result))
        return func.HttpResponse(json.dumps(result), mimetype="application/json")


@bp.route(route="ots-redis/restore", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
async def restore_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    settings = get_settings()
    async with SQLClient() as sql:
        svc = MasterServiceLogger("ots_redis_restore", function_app="apps_services")
        await svc.log_start(sql, request_data="{}")

        snapshot = await get_blob_client().download_json(settings.ots_snapshot_blob_path)
        if not snapshot:
            msg = {"error": "no snapshot found", "path": settings.ots_snapshot_blob_path}
            await svc.log_failure(sql, error_message=json.dumps(msg))
            return func.HttpResponse(json.dumps(msg), status_code=404, mimetype="application/json")

        client = await _connect()
        try:
            restored = await _restore_db(client, snapshot)
        finally:
            await client.aclose()

        result = {"restored_count": restored, "source_captured_at": snapshot.get("captured_at")}
        await svc.log_success(sql, response_data=json.dumps(result))
        return func.HttpResponse(json.dumps(result), mimetype="application/json")


@bp.route(route="ots-redis/status", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
async def status_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    settings = get_settings()
    snapshot = await get_blob_client().download_json(settings.ots_snapshot_blob_path)
    sentinel_present: Optional[bool] = None
    if settings.ots_admin_email:
        client = await _connect()
        try:
            sentinel_present = await _sentinel_present(client, settings.ots_admin_email)
        finally:
            await client.aclose()

    result = {
        "snapshot_path": settings.ots_snapshot_blob_path,
        "snapshot_exists": snapshot is not None,
        "snapshot_captured_at": snapshot.get("captured_at") if snapshot else None,
        "snapshot_key_count": snapshot.get("key_count") if snapshot else None,
        "admin_email": settings.ots_admin_email,
        "sentinel_present": sentinel_present,
    }
    return func.HttpResponse(json.dumps(result), mimetype="application/json")


@bp.timer_trigger(
    schedule="0 */15 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
async def watchdog_timer(timer: func.TimerRequest) -> None:
    """Every 15 min: if sentinel admin key is missing, restore from snapshot."""
    settings = get_settings()
    if not settings.ots_redis_url or not settings.ots_admin_email:
        LOGGER.info("OTS watchdog skipped — OTS_REDIS_URL or OTS_ADMIN_EMAIL not set")
        return

    async with SQLClient() as sql:
        svc = MasterServiceLogger("ots_redis_watchdog", function_app="apps_services")
        await svc.log_start(sql, request_data="{}")

        client = await _connect()
        try:
            if await _sentinel_present(client, settings.ots_admin_email):
                await svc.log_success(
                    sql, response_data=json.dumps({"action": "none", "sentinel_present": True})
                )
                return

            LOGGER.warning("OTS sentinel key missing — restoring from snapshot")
            snapshot = await get_blob_client().download_json(settings.ots_snapshot_blob_path)
            if not snapshot:
                msg = {"error": "sentinel missing but no snapshot available"}
                await svc.log_failure(sql, error_message=json.dumps(msg))
                return

            restored = await _restore_db(client, snapshot)
            result = {
                "action": "restored",
                "restored_count": restored,
                "source_captured_at": snapshot.get("captured_at"),
            }
            await svc.log_success(sql, response_data=json.dumps(result))
        finally:
            await client.aclose()
