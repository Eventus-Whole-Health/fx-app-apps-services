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
