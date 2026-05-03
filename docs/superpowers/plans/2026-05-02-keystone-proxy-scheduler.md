# Keystone Proxy Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `KeystoneClient` and a `keystone_proxy` HTTP function to fx-app-apps-services so the scheduler can call authenticated keystone-platform endpoints, then register the Charta 360 populate-queue job as the first scheduled use.

**Architecture:** Two additions to fx-app-apps-services: (1) a shared `KeystoneClient` using `DefaultAzureCredential` identical in pattern to charta_services, and (2) a thin `keystone_proxy` blueprint that accepts `{path, method, body}`, calls keystone with an Azure AD token, logs to master_services_log, and returns 200. The `apps_central_scheduling` row points to this proxy URL — the `json_body` column carries the keystone path and body to forward.

**Tech Stack:** Python 3.11, Azure Functions v4, httpx, azure-identity DefaultAzureCredential, existing SQLClient/MasterServiceLogger patterns.

**Auth context:** The `fx-app-apps-services` managed identity has already been granted the `charta.admin` app role on the keystone-platform app registration (`api://7d02f10f-a472-4b0a-9113-82c12b2259a9`). Azure app settings `KEYSTONE_API_URL` and `KEYSTONE_SCOPE` still need to be added (Task 3).

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| **Create** | `functions/shared/keystone_client.py` | DefaultAzureCredential-based async HTTP client for keystone-platform |
| **Create** | `tests/test_keystone_client.py` | Unit tests for KeystoneClient |
| **Create** | `functions/keystone_proxy/__init__.py` | Empty — marks module |
| **Create** | `functions/keystone_proxy/keystone_proxy.py` | Blueprint: POST /api/keystone-proxy |
| **Create** | `tests/test_keystone_proxy.py` | Unit tests for keystone_proxy endpoint |
| **Modify** | `function_app.py` | Register keystone_proxy blueprint |

---

## Task 1: KeystoneClient shared module

**Files:**
- Create: `functions/shared/keystone_client.py`
- Create: `tests/test_keystone_client.py`

- [ ] **Step 1: Create the test file**

```python
# tests/test_keystone_client.py
"""Tests for KeystoneClient."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import httpx


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("KEYSTONE_API_URL", "https://keystone-test.azurewebsites.net")
    monkeypatch.setenv("KEYSTONE_SCOPE", "api://test-app-id/.default")


@pytest.fixture
def mock_credential():
    with patch("functions.shared.keystone_client.DefaultAzureCredential") as mock:
        credential = MagicMock()
        token = MagicMock()
        token.token = "test-bearer-token"
        credential.get_token.return_value = token
        mock.return_value = credential
        yield mock


class TestKeystoneClientInit:
    def test_raises_on_missing_env_vars(self):
        from functions.shared.keystone_client import KeystoneClient
        with pytest.raises(ValueError, match="Missing required"):
            KeystoneClient()

    def test_initializes_with_valid_env(self, mock_env, mock_credential):
        from functions.shared.keystone_client import KeystoneClient
        client = KeystoneClient()
        assert client.base_url == "https://keystone-test.azurewebsites.net"


class TestKeystoneClientRequests:
    @pytest.mark.asyncio
    async def test_get_adds_auth_header(self, mock_env, mock_credential):
        from functions.shared.keystone_client import KeystoneClient
        client = KeystoneClient()
        mock_response = httpx.Response(200, json={"ok": True})
        with patch.object(client._http, "request", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get("/api/charta-360/scheduler/status")
            call_kwargs = client._http.request.call_args
            assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-bearer-token"
            assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_post_sends_json_body(self, mock_env, mock_credential):
        from functions.shared.keystone_client import KeystoneClient
        client = KeystoneClient()
        mock_response = httpx.Response(200, json={"queued": 5})
        body = {"cycleMonth": "2026-05-01"}
        with patch.object(client._http, "request", new_callable=AsyncMock, return_value=mock_response):
            result = await client.post("/api/charta-360/scheduler/populate-queue", json=body)
            call_kwargs = client._http.request.call_args
            assert call_kwargs.kwargs["json"] == body
            assert result == {"queued": 5}

    @pytest.mark.asyncio
    async def test_raises_on_4xx_5xx(self, mock_env, mock_credential):
        from functions.shared.keystone_client import KeystoneClient, KeystoneAPIError
        client = KeystoneClient()
        mock_response = httpx.Response(500, json={"detail": "Internal error"})
        with patch.object(client._http, "request", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(KeystoneAPIError, match="500"):
                await client.get("/api/charta-360/scheduler/status")

    @pytest.mark.asyncio
    async def test_retries_once_on_401(self, mock_env, mock_credential):
        from functions.shared.keystone_client import KeystoneClient
        client = KeystoneClient()
        response_401 = httpx.Response(401, json={"detail": "Unauthorized"})
        response_200 = httpx.Response(200, json={"ok": True})
        with patch.object(client._http, "request", new_callable=AsyncMock,
                          side_effect=[response_401, response_200]):
            result = await client.get("/api/charta-360/scheduler/status")
            assert result == {"ok": True}
            assert client._http.request.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/jgilpatrick/Development/active/fx-app-apps-services
source ~/venv/fx-app-apps-services/bin/activate
python -m pytest tests/test_keystone_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'functions.shared.keystone_client'`

- [ ] **Step 3: Implement KeystoneClient**

```python
# functions/shared/keystone_client.py
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
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_keystone_client.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add functions/shared/keystone_client.py tests/test_keystone_client.py
git commit -m "feat: add KeystoneClient to shared for keystone-platform auth"
```

---

## Task 2: keystone_proxy blueprint

**Files:**
- Create: `functions/keystone_proxy/__init__.py`
- Create: `functions/keystone_proxy/keystone_proxy.py`
- Create: `tests/test_keystone_proxy.py`

The proxy accepts `{"path": "...", "method": "POST", "body": {...}}` in the POST body, calls keystone, logs to master_services_log, and returns 200 with the keystone result. The scheduler's `json_body` column carries these fields.

- [ ] **Step 1: Create the test file**

```python
# tests/test_keystone_proxy.py
"""Tests for keystone_proxy HTTP function."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import azure.functions as func


def make_request(body: dict) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="https://fx-app-apps-services.azurewebsites.net/api/keystone-proxy",
        body=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        params={},
    )


@pytest.fixture
def mock_sql_client():
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    mock.execute = AsyncMock(return_value=[{"log_id": 42}])
    return mock


@pytest.fixture
def mock_logger():
    mock = AsyncMock()
    mock.log_id = 42
    mock.log_start = AsyncMock(return_value=42)
    mock.log_success = AsyncMock()
    mock.log_failure = AsyncMock()
    return mock


class TestKeystoneProxy:
    @pytest.mark.asyncio
    async def test_post_to_keystone_path(self, mock_sql_client, mock_logger):
        """Should call keystone with the path and body from the request."""
        from functions.keystone_proxy.keystone_proxy import keystone_proxy_handler

        mock_keystone = AsyncMock()
        mock_keystone.post = AsyncMock(return_value={"queued": 3})
        mock_keystone.get = AsyncMock(return_value={"status": "ok"})

        req = make_request({
            "path": "/api/charta-360/scheduler/populate-queue",
            "method": "POST",
            "body": {"cycleMonth": "2026-05-01"},
        })

        with patch("functions.keystone_proxy.keystone_proxy.KeystoneClient", return_value=mock_keystone), \
             patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql_client), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "ok"
        assert body["result"] == {"queued": 3}
        mock_keystone.post.assert_called_once_with(
            "/api/charta-360/scheduler/populate-queue",
            json={"cycleMonth": "2026-05-01"},
        )

    @pytest.mark.asyncio
    async def test_get_to_keystone_path(self, mock_sql_client, mock_logger):
        """Should call keystone GET when method is GET."""
        from functions.keystone_proxy.keystone_proxy import keystone_proxy_handler

        mock_keystone = AsyncMock()
        mock_keystone.get = AsyncMock(return_value={"queueDepth": 10})

        req = make_request({
            "path": "/api/charta-360/scheduler/status",
            "method": "GET",
        })

        with patch("functions.keystone_proxy.keystone_proxy.KeystoneClient", return_value=mock_keystone), \
             patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql_client), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 200
        mock_keystone.get.assert_called_once_with("/api/charta-360/scheduler/status")

    @pytest.mark.asyncio
    async def test_returns_500_on_keystone_error(self, mock_sql_client, mock_logger):
        """Should return 500 when keystone returns an error."""
        from functions.keystone_proxy.keystone_proxy import keystone_proxy_handler
        from functions.shared.keystone_client import KeystoneAPIError

        mock_keystone = AsyncMock()
        mock_keystone.post = AsyncMock(side_effect=KeystoneAPIError(503, "Service unavailable"))

        req = make_request({"path": "/api/charta-360/scheduler/populate-queue", "method": "POST"})

        with patch("functions.keystone_proxy.keystone_proxy.KeystoneClient", return_value=mock_keystone), \
             patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql_client), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 503
        body = json.loads(response.get_body())
        assert "error" in body

    @pytest.mark.asyncio
    async def test_returns_400_on_missing_path(self, mock_sql_client, mock_logger):
        """Should return 400 when path is missing from request body."""
        from functions.keystone_proxy.keystone_proxy import keystone_proxy_handler

        req = make_request({"method": "POST"})

        with patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql_client), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_keystone_proxy.py -v
```

Expected: `ModuleNotFoundError: No module named 'functions.keystone_proxy'`

- [ ] **Step 3: Create the module files**

Create `functions/keystone_proxy/__init__.py` (empty):
```python
```

Create `functions/keystone_proxy/keystone_proxy.py`:
```python
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

            if method == "GET":
                result = await client.get(path)
            else:
                result = await client.post(path, json=request_body)

            await svc_logger.log_success(
                sql, result_summary=json.dumps({"path": path, "method": method})
            )

            return func.HttpResponse(
                json.dumps({"status": "ok", "result": result}),
                status_code=200,
                mimetype="application/json",
            )

        except KeystoneAPIError as e:
            await svc_logger.log_failure(sql, error_message=str(e))
            return func.HttpResponse(
                json.dumps({"error": str(e), "keystone_status": e.status_code}),
                status_code=e.status_code,
                mimetype="application/json",
            )
        except Exception as e:
            await svc_logger.log_failure(sql, error_message=str(e))
            logger.error(f"keystone_proxy unexpected error: {e}")
            return func.HttpResponse(
                json.dumps({"error": str(e)}),
                status_code=500,
                mimetype="application/json",
            )


@bp.route(route="keystone-proxy", methods=["POST"])
async def keystone_proxy(req: func.HttpRequest) -> func.HttpResponse:
    return await keystone_proxy_handler(req)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_keystone_proxy.py -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add functions/keystone_proxy/__init__.py functions/keystone_proxy/keystone_proxy.py tests/test_keystone_proxy.py
git commit -m "feat: add keystone_proxy blueprint for scheduled keystone-platform calls"
```

---

## Task 3: Register blueprint + add Azure app settings

**Files:**
- Modify: `function_app.py`

- [ ] **Step 1: Register the blueprint in function_app.py**

Add after the existing blueprint imports and registrations:

```python
# In the imports section (after line 36):
from functions.keystone_proxy.keystone_proxy import bp as keystone_proxy_bp

# After the existing app.register_blueprint calls (after line 43):
app.register_blueprint(keystone_proxy_bp)
```

- [ ] **Step 2: Verify func start loads cleanly**

```bash
source ~/venv/fx-app-apps-services/bin/activate
func start 2>&1 | head -40
```

Expected: All functions listed without import errors. You will see a new `keystone_proxy` function in the list. Timer triggers will error (Azurite not running) — that is expected.

- [ ] **Step 3: Add app settings to Azure**

```bash
az functionapp config appsettings set \
  --name fx-app-apps-services \
  --resource-group rg-keystone-platform \
  --subscription 57aa22c1-1b18-40c5-ab91-0d9c9059d0a9 \
  --settings \
    KEYSTONE_API_URL="https://keystone-platform.azurewebsites.net" \
    KEYSTONE_SCOPE="api://7d02f10f-a472-4b0a-9113-82c12b2259a9/.default"
```

Expected: JSON output listing all app settings including the two new ones.

- [ ] **Step 4: Commit**

```bash
git add function_app.py
git commit -m "feat: register keystone_proxy blueprint"
```

---

## Task 4: Insert apps_central_scheduling row for Charta 360

This adds the first scheduled use of the proxy — the daily Charta 360 queue populate job.

- [ ] **Step 1: Insert the schedule row**

```bash
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "
INSERT INTO jgilpatrick.apps_central_scheduling
    (function_app, service, trigger_url, json_body, frequency, schedule_config,
     is_active, status, max_execution_minutes)
VALUES (
    'fx-app-apps-services',
    'charta_360_queue_populate',
    'https://fx-app-apps-services.azurewebsites.net/api/keystone-proxy',
    '{\"path\": \"/api/charta-360/scheduler/populate-queue\", \"method\": \"POST\", \"body\": {}}',
    'daily',
    '{\"times\": [\"06:00\"]}',
    1,
    'pending',
    5
)"
```

The `schedule_config` `{"times": ["06:00"]}` means the scheduler will fire this job during the 6:00–6:15 AM Eastern window, once per day. `max_execution_minutes=5` — the keystone call should complete within seconds.

- [ ] **Step 2: Verify the row was inserted**

```bash
sqlcmd -S asqls-ewh-apps-dev-01.database.windows.net -d asqldb-ewh-apps-dev-01 -G -Q "
SELECT id, function_app, service, trigger_url, frequency, schedule_config, is_active, status
FROM jgilpatrick.apps_central_scheduling
WHERE service = 'charta_360_queue_populate'"
```

Expected: One row with `is_active=1`, `status=pending`.

- [ ] **Step 3: Smoke test the proxy manually**

After deploying (see Task 5), trigger the scheduler manually to verify end-to-end:

```bash
curl -X POST https://fx-app-apps-services.azurewebsites.net/api/scheduler/manual-trigger \
  -H "Content-Type: application/json" \
  -H "x-functions-key: <host-key>" \
  -d '{"force_service_ids": [<id from step 2>], "bypass_window_check": true}'
```

Expected: `{"status": "triggered", ...}` — then check Seq and master_services_log for the proxy execution.

---

## Task 5: Deploy

- [ ] **Step 1: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: All tests PASS (including the 5 keystone_client tests and 4 keystone_proxy tests).

- [ ] **Step 2: Push to main to trigger GitHub Actions deploy**

GitHub Actions auto-deploys on push to main. Confirm the workflow succeeds in the Actions tab, then verify the new `keystone_proxy` function appears in the Azure portal under fx-app-apps-services.

---

## Verification

After deploy, confirm end-to-end by:

1. Manually triggering the `charta_360_queue_populate` schedule row (using the manual-trigger endpoint above)
2. Checking Seq: `~/.dotnet/tools/seqcli search --filter="ServiceName = 'keystone_proxy'"`
3. Checking the execution log: `sqlcmd ... -Q "SELECT TOP 5 * FROM jgilpatrick.apps_scheduler_execution_log WHERE schedule_id = <id> ORDER BY execution_id DESC"`
