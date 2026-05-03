"""Async HTTP client for keystone-platform API with Managed Identity auth."""
import logging
import os
from typing import Any, Dict, Optional

import httpx
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


class KeystoneAPIError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Keystone API error {status_code}: {detail}")


class KeystoneClient:
    """Async client for keystone-platform endpoints.

    Uses DefaultAzureCredential (Managed Identity in Azure, az-cli locally).
    Requires KEYSTONE_API_URL and KEYSTONE_SCOPE env vars.
    """

    _REQUIRED_ENV = ["KEYSTONE_API_URL", "KEYSTONE_SCOPE"]

    def __init__(self):
        missing = [k for k in self._REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        self.base_url = os.environ["KEYSTONE_API_URL"].rstrip("/")
        self._scope = os.environ["KEYSTONE_SCOPE"]
        self._credential = DefaultAzureCredential()
        self._http = httpx.AsyncClient(timeout=60.0)

    def _get_token(self) -> str:
        return self._credential.get_token(self._scope).token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        _retry: bool = True,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        response = await self._http.request(method, url, headers=headers, json=json)
        if response.status_code == 401 and _retry:
            logger.warning("Keystone returned 401, refreshing token and retrying")
            headers["Authorization"] = f"Bearer {self._get_token()}"
            response = await self._http.request(method, url, headers=headers, json=json)
        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                pass
            raise KeystoneAPIError(response.status_code, detail)
        return response.json()

    async def get(self, path: str) -> Dict[str, Any]:
        return await self._request("GET", path)

    async def post(self, path: str, json: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", path, json=json)

    async def close(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
