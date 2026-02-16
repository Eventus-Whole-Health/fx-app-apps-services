"""SQL Services API client for apps-services."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx
from azure.identity.aio import ClientSecretCredential

from .settings import get_settings

LOGGER = logging.getLogger(__name__)


class SQLClient:
    """Client for interacting with the SQL executor service."""

    def __init__(self, credential: Optional[ClientSecretCredential] = None) -> None:
        self._settings = get_settings()

        if credential:
            self._credential = credential
            self._owns_credential = False
        else:
            # Get client credentials from environment variables
            # In production, these should be Key Vault references that Azure Functions resolves automatically
            # In local development, these come from local.settings.json
            client_id = os.environ.get("SQL_EXECUTOR_CLIENT_ID")
            client_secret = os.environ.get("SQL_EXECUTOR_CLIENT_SECRET")
            tenant_id = os.environ.get("SQL_EXECUTOR_TENANT_ID")

            if not client_id or not client_secret or not tenant_id:
                missing = []
                if not client_id:
                    missing.append("SQL_EXECUTOR_CLIENT_ID")
                if not client_secret:
                    missing.append("SQL_EXECUTOR_CLIENT_SECRET")
                if not tenant_id:
                    missing.append("SQL_EXECUTOR_TENANT_ID")
                error_msg = (
                    f"SQL Executor authentication requires client credentials. "
                    f"Missing environment variables: {', '.join(missing)}. "
                    f"In production, use Key Vault references in application settings. "
                    f"In local development, set values in local.settings.json"
                )
                LOGGER.error(error_msg)
                raise ValueError(error_msg)

            LOGGER.info("Using client credentials for SQL Executor authentication")
            self._credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret
            )
            self._owns_credential = True

        self._cached_token = None

    async def _get_token(self) -> str:
        if self._cached_token is None:
            try:
                LOGGER.info(f"Requesting token for scope: {self._settings.sql_executor_scope}")
                token = await self._credential.get_token(self._settings.sql_executor_scope)
                self._cached_token = token.token
                LOGGER.info("✅ Acquired and cached new Azure token for SQL operations")
            except Exception as e:
                LOGGER.error(f"❌ Failed to acquire Azure token: {str(e)}")
                LOGGER.error(f"Credential type: {type(self._credential).__name__}")
                raise
        return self._cached_token

    async def execute(
        self,
        sql: str,
        *,
        method: str = "query",
        server: Optional[str] = None,
        title: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Execute SQL via sql-executor."""
        payload: Dict[str, Any] = {
            "sql": sql,
            "method": method,
            "server": server or self._settings.sql_executor_server,
        }
        if title:
            payload["title"] = title

        headers = {
            "Authorization": f"Bearer {await self._get_token()}",
            "Content-Type": "application/json",
        }
        # Use provided timeout or default to 60 seconds for SQL operations
        timeout_value = timeout if timeout is not None else 60.0

        async with httpx.AsyncClient(timeout=timeout_value) as client:
            response = await client.post(
                str(self._settings.sql_executor_url),
                headers=headers,
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                LOGGER.error(
                    "SQL executor error", extra={"status_code": exc.response.status_code, "text": exc.response.text}
                )
                raise

            if not response.text:
                return None

            if "application/json" in response.headers.get("Content-Type", ""):
                return response.json()

            return response.text

    async def close(self) -> None:
        if self._owns_credential:
            await self._credential.close()

    async def __aenter__(self) -> "SQLClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
