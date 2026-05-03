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
def mock_sql():
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def mock_logger():
    mock = AsyncMock()
    mock.log_id = 42
    mock.log_start = AsyncMock(return_value=42)
    mock.log_success = AsyncMock()
    mock.log_error = AsyncMock()
    return mock


class TestKeystoneProxy:
    @pytest.mark.asyncio
    async def test_post_to_keystone_path(self, mock_sql, mock_logger):
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
             patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "ok"
        assert body["result"] == {"queued": 3}
        mock_keystone.post.assert_called_once_with(
            "/api/charta-360/scheduler/populate-queue",
            {"cycleMonth": "2026-05-01"},
        )

    @pytest.mark.asyncio
    async def test_get_to_keystone_path(self, mock_sql, mock_logger):
        """Should call keystone GET when method is GET."""
        from functions.keystone_proxy.keystone_proxy import keystone_proxy_handler

        mock_keystone = AsyncMock()
        mock_keystone.get = AsyncMock(return_value={"queueDepth": 10})

        req = make_request({
            "path": "/api/charta-360/scheduler/status",
            "method": "GET",
        })

        with patch("functions.keystone_proxy.keystone_proxy.KeystoneClient", return_value=mock_keystone), \
             patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 200
        mock_keystone.get.assert_called_once_with("/api/charta-360/scheduler/status")

    @pytest.mark.asyncio
    async def test_returns_error_status_on_keystone_error(self, mock_sql, mock_logger):
        """Should return keystone's status code when it returns an error."""
        from functions.keystone_proxy.keystone_proxy import keystone_proxy_handler
        from functions.shared.keystone_client import KeystoneAPIError

        mock_keystone = AsyncMock()
        mock_keystone.post = AsyncMock(side_effect=KeystoneAPIError(503, "Service unavailable"))

        req = make_request({"path": "/api/charta-360/scheduler/populate-queue", "method": "POST"})

        with patch("functions.keystone_proxy.keystone_proxy.KeystoneClient", return_value=mock_keystone), \
             patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 503
        body = json.loads(response.get_body())
        assert "error" in body

    @pytest.mark.asyncio
    async def test_returns_400_on_missing_path(self, mock_sql, mock_logger):
        """Should return 400 when path is missing from request body."""
        from functions.keystone_proxy.keystone_proxy import keystone_proxy_handler

        req = make_request({"method": "POST"})

        with patch("functions.keystone_proxy.keystone_proxy.SQLClient", return_value=mock_sql), \
             patch("functions.keystone_proxy.keystone_proxy.MasterServiceLogger", return_value=mock_logger):
            response = await keystone_proxy_handler(req)

        assert response.status_code == 400
