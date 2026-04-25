"""Generic HTTP email client.

Sends HTML emails via a configured HTTP endpoint. The endpoint is expected to
accept JSON with at least: { to, from, subject, body }.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from .settings import get_settings

LOGGER = logging.getLogger(__name__)


class EmailClient:
    """Send emails through the configured HTTP endpoint."""

    def __init__(self) -> None:
        self._settings = get_settings()
        # Backward-compatible fallback to logic_app_email_url if EMAIL_API_URL not present
        self._endpoint = getattr(self._settings, "email_api_url", None) or self._settings.logic_app_email_url
        self._timeout_seconds = getattr(self._settings, "email_api_timeout_seconds", None) or self._settings.logic_app_timeout_seconds

    async def send_email(
        self,
        *,
        recipient: str,
        sender: str,
        subject: str,
        html_body: str,
        metadata: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "to": recipient,
            "from": sender,
            "subject": subject,
            "body": html_body,
        }
        if metadata:
            payload["metadata"] = metadata

        if client:
            response = await client.post(str(self._endpoint), json=payload)
        else:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as new_client:
                response = await new_client.post(str(self._endpoint), json=payload)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            LOGGER.error(
                "Email send failed", extra={"status_code": exc.response.status_code, "text": exc.response.text}
            )
            raise
