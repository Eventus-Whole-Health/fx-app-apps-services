"""ServiceLogger for logging to apps_master_services_log table."""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from .sql_client import SQLClient
from .seq_logging import sanitize_sensitive_data

# Module-level logger for Seq events
LOGGER = logging.getLogger(__name__)


class ServiceLogger:
    """
    Logger for the centralized apps_master_services_log table.

    Usage:
        # Root service (no parent)
        logger = ServiceLogger("scheduler_timer")

        # Child service (has parent)
        logger = ServiceLogger("child_service", parent_service_id=123, root_id=123)
    """

    def __init__(
        self,
        service_name: str,
        *,
        parent_service_id: Optional[int] = None,
        root_id: Optional[int] = None,
        function_app: str = "fx-app-apps-services",
        trigger_source: str = "timer"
    ):
        """
        Initialize the logger and immediately log start of execution.

        Args:
            service_name: Name of the service/function being logged
            parent_service_id: log_id of the parent service (None for root services)
            root_id: log_id of the root service (None for root services)
            function_app: Name of the Azure Function App
            trigger_source: What triggered this service (timer, HTTP, queue, blob)
        """
        self.service_name = service_name
        self.parent_service_id = parent_service_id
        self.root_id = root_id
        self.function_app = function_app
        self.trigger_source = trigger_source
        self.invocation_id = str(uuid.uuid4())
        self.log_id: Optional[int] = None
        self.request_data: Optional[str] = None
        self.metadata: Dict[str, Any] = {}
        self._start_time = time.time()

    def _emit_seq_event(
        self,
        event_type: str,
        status: str,
        duration_ms: float = None,
        error_message: str = None
    ) -> None:
        """
        Emit structured event to Seq for real-time monitoring.

        Args:
            event_type: One of "ServiceStarted", "ServiceCompleted", "ServiceFailed", "ServiceWarning"
            status: Current status of the service
            duration_ms: Elapsed time in milliseconds (optional)
            error_message: Error or warning message (optional)
        """
        props = {
            "EventType": event_type,
            "ServiceName": self.service_name,
            "FunctionApp": self.function_app,
            "TriggerSource": self.trigger_source,
            "Status": status,
            "LogId": self.log_id,
            "ParentId": self.parent_service_id,
            "RootId": self.root_id,
            "InvocationId": self.invocation_id,
        }

        if duration_ms is not None:
            props["DurationMs"] = round(duration_ms, 2)
        if error_message:
            # Sanitize error message to remove sensitive data before logging
            props["ErrorMessage"] = sanitize_sensitive_data(error_message[:500])

        try:
            if event_type == "ServiceFailed":
                LOGGER.error(f"Service failed: {self.service_name}", **props)
            elif event_type == "ServiceWarning":
                LOGGER.warning(f"Service warning: {self.service_name}", **props)
            elif event_type == "ServiceStarted":
                LOGGER.info(f"Service started: {self.service_name}", **props)
            else:
                LOGGER.info(f"Service completed: {self.service_name}", **props)
        except Exception as e:
            # Seq logging should never crash the service, but log to stderr as fallback
            import sys
            print(f"Seq logging failed for {self.service_name}: {type(e).__name__}", file=sys.stderr)

    async def log_start(
        self,
        sql_client: SQLClient,
        request_data: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Log the start of service execution to master services log.

        Returns:
            The log_id (BIGINT) for this service execution
        """
        self.request_data = request_data
        if metadata:
            self.metadata.update(metadata)

        # Escape strings for SQL injection protection
        service_name_escaped = self._escape_sql_string(self.service_name)
        function_app_escaped = self._escape_sql_string(self.function_app)
        trigger_source_escaped = self._escape_sql_string(self.trigger_source)
        invocation_id_escaped = self._escape_sql_string(self.invocation_id)

        request_escaped = 'NULL'
        if self.request_data:
            request_escaped = f"'{self._escape_sql_string(self.request_data)}'"

        metadata_escaped = 'NULL'
        if self.metadata:
            metadata_json = json.dumps(self.metadata)
            metadata_escaped = f"'{self._escape_sql_string(metadata_json)}'"

        # Two-step process required due to SQL Server triggers
        # Step 1: INSERT with status='pending'
        insert_sql = f"""
        INSERT INTO jgilpatrick.apps_master_services_log (
            root_id, parent_id, function_app, service_name, invocation_id,
            status, trigger_source, request, metadata
        )
        VALUES (
            {self.root_id if self.root_id else 'NULL'},
            {self.parent_service_id if self.parent_service_id else 'NULL'},
            '{function_app_escaped}',
            '{service_name_escaped}',
            '{invocation_id_escaped}',
            'pending',
            '{trigger_source_escaped}',
            {request_escaped},
            {metadata_escaped}
        )
        """

        await sql_client.execute(
            insert_sql,
            method="execute",
            title=f"Log start of {self.service_name}"
        )

        # Step 2: Query back the log_id using unique invocation_id
        # Cannot use SCOPE_IDENTITY() due to triggers and different connections
        query_sql = f"""
        SELECT log_id
        FROM jgilpatrick.apps_master_services_log
        WHERE invocation_id = '{invocation_id_escaped}'
        """

        result = await sql_client.execute(
            query_sql,
            method="query",
            title=f"Get log_id for {self.service_name}"
        )

        if not result or len(result) == 0:
            raise RuntimeError(f"Failed to retrieve log_id for invocation_id: {self.invocation_id}")

        self.log_id = result[0]["log_id"]

        # Emit Seq event after SQL logging succeeds
        self._emit_seq_event("ServiceStarted", "pending")

        return self.log_id

    async def log_success(
        self,
        sql_client: SQLClient,
        response_data: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log successful completion of service execution."""
        await self._log_completion(sql_client, "success", response_data, metadata)

        # Emit Seq event
        duration_ms = (time.time() - self._start_time) * 1000
        self._emit_seq_event("ServiceCompleted", "success", duration_ms=duration_ms)

    async def log_error(
        self,
        sql_client: SQLClient,
        error_message: str,
        response_data: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log failed completion of service execution."""
        await self._log_completion(
            sql_client,
            "failed",
            response_data,
            metadata,
            error_message=error_message
        )

        # Emit Seq event
        duration_ms = (time.time() - self._start_time) * 1000
        self._emit_seq_event("ServiceFailed", "failed", duration_ms=duration_ms, error_message=error_message)

    async def log_warning(
        self,
        sql_client: SQLClient,
        warning_message: str,
        response_data: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log completion with warnings."""
        await self._log_completion(
            sql_client,
            "warning",
            response_data,
            metadata,
            error_message=warning_message
        )

        # Emit Seq event
        duration_ms = (time.time() - self._start_time) * 1000
        self._emit_seq_event("ServiceWarning", "warning", duration_ms=duration_ms, error_message=warning_message)

    async def _log_completion(
        self,
        sql_client: SQLClient,
        status: str,
        response_data: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Internal method to log completion with given status."""
        if self.log_id is None:
            raise RuntimeError("Must call log_start() before logging completion")

        # Update metadata
        if metadata:
            self.metadata.update(metadata)

        # Build update SQL
        response_escaped = 'NULL'
        if response_data:
            response_escaped = f"'{self._escape_sql_string(response_data)}'"

        metadata_escaped = 'NULL'
        if self.metadata:
            metadata_json = json.dumps(self.metadata)
            metadata_escaped = f"'{self._escape_sql_string(metadata_json)}'"

        error_escaped = 'NULL'
        if error_message:
            error_escaped = f"'{self._escape_sql_string(error_message)}'"

        update_sql = f"""
        UPDATE jgilpatrick.apps_master_services_log
        SET status = '{self._escape_sql_string(status)}',
            response = {response_escaped},
            error_message = {error_escaped},
            metadata = {metadata_escaped}
        WHERE log_id = {self.log_id}
        """

        await sql_client.execute(
            update_sql,
            method="execute",
            title=f"Log completion of {self.service_name}"
        )

    def _escape_sql_string(self, value: str) -> str:
        """
        Sanitize a string for safe SQL insertion by escaping special characters.
        Truncates to max_length BEFORE escaping to ensure final length stays within limits.

        Args:
            value: String to sanitize

        Returns:
            Sanitized string safe for SQL
        """
        if not value:
            return ""

        # Truncate first to leave room for escaping (use conservative limit)
        max_length = 3900
        truncated = value[:max_length]

        # Escape single quotes by doubling them
        sanitized = truncated.replace("'", "''")

        # Replace other potentially problematic characters
        sanitized = sanitized.replace("\x00", "")  # Remove null bytes
        sanitized = sanitized.replace("\\", "\\\\")  # Escape backslashes

        return sanitized

    def get_child_context(self) -> Dict[str, int]:
        """
        Get context to pass to child services.

        Returns:
            Dictionary with parent_service_id and root_id for child services
        """
        if self.log_id is None:
            raise RuntimeError("Must call log_start() before getting child context")

        # For root services: children use this log_id as both parent and root
        # For child services: children use this log_id as parent, but keep same root
        return {
            "parent_service_id": self.log_id,
            "root_id": self.log_id if self.root_id is None else self.root_id
        }
