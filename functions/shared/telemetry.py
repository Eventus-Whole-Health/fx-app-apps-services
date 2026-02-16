"""Application Insights telemetry client (generic events/metrics)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .settings import get_settings

LOGGER = logging.getLogger(__name__)


class TelemetryClient:
    """Minimal wrapper to send logs/metrics to Application Insights if configured."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._initialized = False
        self._initialize()

    def _initialize(self) -> None:
        connection_string = self._settings.application_insights_connection_string
        if not connection_string:
            LOGGER.warning(
                "APPLICATION_INSIGHTS_CONNECTION_STRING not configured. Telemetry disabled."
            )
            return

        try:
            from opencensus.ext.azure.log_exporter import AzureLogHandler

            handler = AzureLogHandler(connection_string=connection_string)
            LOGGER.addHandler(handler)
            self._initialized = True
            LOGGER.info("Application Insights log handler initialized")
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error(f"Failed to initialize Application Insights log handler: {exc}")

    def track_event(self, event_name: str, properties: Optional[Dict[str, Any]] = None) -> None:
        if not self._initialized:
            return
        try:
            LOGGER.info("event", extra={"event_name": event_name, "properties": properties or {}})
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error(f"Failed to track event: {exc}")

    def track_metric(self, name: str, value: float, properties: Optional[Dict[str, Any]] = None) -> None:
        if not self._initialized:
            return
        try:
            LOGGER.info("metric", extra={"metric_name": name, "value": value, "properties": properties or {}})
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error(f"Failed to track metric: {exc}")


# Singleton instance
_telemetry_client: Optional[TelemetryClient] = None


def get_telemetry_client() -> TelemetryClient:
    """Get or create the singleton telemetry client instance."""
    global _telemetry_client
    if _telemetry_client is None:
        _telemetry_client = TelemetryClient()
    return _telemetry_client


# Convenience functions for direct usage
async def track_event(event_name: str, properties: Optional[Dict[str, Any]] = None) -> None:
    """Track an event using the singleton telemetry client."""
    client = get_telemetry_client()
    client.track_event(event_name, properties)


async def track_exception(exception: Exception, properties: Optional[Dict[str, Any]] = None) -> None:
    """Track an exception using the singleton telemetry client."""
    client = get_telemetry_client()
    # Log the exception with properties
    LOGGER.error(f"Exception tracked: {exception}", extra={"properties": properties or {}}, exc_info=True)
