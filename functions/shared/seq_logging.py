"""
SEQ logging configuration for Apps Services.

This module provides structured logging to SEQ for centralized log management.
Following the standard configuration pattern from SEQ_AZURE_FUNCTIONS_SETUP.md
"""

import logging
import os
import re
from datetime import datetime
from typing import Dict, Optional, Any
from urllib.parse import urlparse
import azure.functions as func

# Lazy import seqlog to avoid issues if not installed
_seqlog_configured = False
_seq_enabled = False


class Emoticons:
    """Standard emoticons for log messages as defined in SEQ_AZURE_FUNCTIONS_SETUP.md"""

    # Lifecycle & Flow
    STARTED = "🚀"
    COMPLETED = "✅"
    FINISHED = "🏁"
    STOPPED = "⏹️"

    # Data Operations
    RECEIVING = "📥"
    SENDING = "📤"
    SAVING = "💾"
    PROCESSING = "📊"
    SYNCING = "🔄"
    CREATING = "📝"
    UPDATING = "✏️"
    DELETING = "🗑️"

    # External Integrations
    API_CALL = "🌐"
    EXTERNAL_SERVICE = "📞"
    CONNECTED = "🔗"
    EMAIL = "📨"
    QUEUED = "📬"

    # Security & Auth
    AUTH_SUCCESS = "🔐"
    API_KEY_VALID = "🔑"
    ACCESS_DENIED = "🚫"

    # Performance & Timing
    TIMING = "⏱️"
    SLOW = "🐌"
    CACHE_HIT = "⚡"

    # Errors & Issues
    FAILED = "❌"
    UNEXPECTED_ERROR = "💥"
    CRITICAL = "🔥"
    WARNING = "⚠️"

    # System Health
    HEALTH_OK = "💚"
    HEALTH_DEGRADED = "💛"
    HEALTH_FAILED = "💔"
    MAINTENANCE = "🔧"


# =============================================================================
# Security: Sensitive Data Sanitization
# =============================================================================

# Patterns that indicate sensitive data in error messages
_SENSITIVE_PATTERNS = [
    # Connection strings
    (r'(?i)(password|pwd)\s*=\s*[^\s;]+', r'\1=***REDACTED***'),
    (r'(?i)(server|data source)\s*=\s*[^\s;]+', r'\1=***REDACTED***'),
    (r'(?i)(user id|uid)\s*=\s*[^\s;]+', r'\1=***REDACTED***'),
    # Bearer tokens
    (r'(?i)bearer\s+[a-zA-Z0-9\-_\.]+', 'Bearer ***REDACTED***'),
    # API keys (common patterns - 20+ alphanumeric chars)
    (r'(?i)(api[_-]?key|apikey)\s*[=:]\s*[a-zA-Z0-9\-_]{16,}', r'\1=***REDACTED***'),
    # Azure connection strings
    (r'(?i)AccountKey\s*=\s*[a-zA-Z0-9+/=]+', 'AccountKey=***REDACTED***'),
    (r'(?i)SharedAccessKey\s*=\s*[a-zA-Z0-9+/=]+', 'SharedAccessKey=***REDACTED***'),
    (r'(?i)sig\s*=\s*[a-zA-Z0-9%+/=]+', 'sig=***REDACTED***'),
    # Client secrets (Azure AD pattern)
    (r'(?i)(client[_-]?secret)\s*[=:]\s*[a-zA-Z0-9\-_~\.]{20,}', r'\1=***REDACTED***'),
    # Instrumentation keys (GUIDs in specific contexts)
    (r'(?i)InstrumentationKey\s*=\s*[a-f0-9\-]{36}', 'InstrumentationKey=***REDACTED***'),
]

# Compiled patterns for performance
_COMPILED_PATTERNS = [(re.compile(pattern), replacement) for pattern, replacement in _SENSITIVE_PATTERNS]


def sanitize_sensitive_data(text: str) -> str:
    """
    Sanitize sensitive data from text (error messages, logs, etc.).

    Removes or redacts:
    - Connection string components (password, server, user id)
    - Bearer tokens
    - API keys
    - Azure storage account keys
    - SAS signatures
    - Client secrets
    - Instrumentation keys

    Args:
        text: The text to sanitize

    Returns:
        Sanitized text with sensitive data redacted
    """
    if not text:
        return text

    result = text
    for pattern, replacement in _COMPILED_PATTERNS:
        result = pattern.sub(replacement, result)

    return result


def sanitize_url(url: str) -> str:
    """
    Sanitize a URL by removing query parameters which may contain sensitive data.

    Args:
        url: The full URL including query string

    Returns:
        URL with only scheme, netloc, and path (no query params)
    """
    if not url:
        return url

    try:
        parsed = urlparse(url)
        # Reconstruct URL without query string and fragment
        safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme else parsed.path
        return safe_url
    except Exception:
        # If parsing fails, return a safe fallback
        return "[URL parsing failed]"


def configure_seq_logging() -> bool:
    """
    Configure SEQ logging with optimized settings for Azure Function Apps.

    Returns:
        bool: True if SEQ logging was successfully configured, False otherwise
    """
    global _seqlog_configured, _seq_enabled

    if _seqlog_configured:
        return _seq_enabled

    _seqlog_configured = True

    # Check if SEQ is configured
    seq_server_url = os.getenv("SEQ_SERVER_URL")
    seq_api_key = os.getenv("SEQ_API_KEY")

    if not seq_server_url:
        logging.info("SEQ_SERVER_URL not configured - SEQ logging disabled")
        _seq_enabled = False
        return False

    # API key is required for non-development environments
    environment = os.getenv("ENVIRONMENT", "development")
    if not seq_api_key:
        if environment != "development":
            logging.error(
                f"SEQ_API_KEY is required in non-development environments (current: {environment}). "
                "SEQ logging disabled for security."
            )
            _seq_enabled = False
            return False
        else:
            logging.warning("SEQ_API_KEY not configured - using unauthenticated SEQ logging (dev only)")

    try:
        import seqlog

        # Configure SEQ logging with optimized settings
        seqlog.log_to_seq(
            server_url=seq_server_url,
            api_key=seq_api_key if seq_api_key else None,
            level=logging.INFO,
            batch_size=50,
            auto_flush_timeout=5,
            override_root_logger=True,  # CRITICAL: Must be True
            support_extra_properties=True,  # CRITICAL: Required for **{} properties
        )

        # Suppress noisy Azure SDK loggers AFTER SEQ is configured
        logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.WARNING)
        logging.getLogger('azure.identity').setLevel(logging.WARNING)
        logging.getLogger('azure.identity.aio').setLevel(logging.WARNING)
        logging.getLogger('azure.identity.aio._internal.get_token_mixin').setLevel(logging.WARNING)
        logging.getLogger('azure.storage').setLevel(logging.WARNING)
        logging.getLogger('azure.storage.blob').setLevel(logging.WARNING)
        logging.getLogger('azure.core').setLevel(logging.WARNING)
        logging.getLogger('azure.storage.queue').setLevel(logging.WARNING)
        logging.getLogger('azure_functions_worker').setLevel(logging.WARNING)

        # Suppress HTTP request logging from httpx and httpcore
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        logging.getLogger('httpcore.http11').setLevel(logging.WARNING)
        logging.getLogger('httpcore.connection').setLevel(logging.WARNING)

        # Suppress Azure Functions HTTP request logging
        logging.getLogger('azure.functions').setLevel(logging.WARNING)
        logging.getLogger('azure.functions.worker').setLevel(logging.WARNING)
        logging.getLogger('azure.functions.worker.process').setLevel(logging.WARNING)

        # Suppress any HTTP request logging that might come from Azure Functions runtime
        logging.getLogger('azure.functions.worker.dispatcher').setLevel(logging.WARNING)
        logging.getLogger('azure.functions.worker.logging').setLevel(logging.WARNING)

        # Get properties for global enrichment
        app_version = os.getenv("APP_VERSION", "1.0.0")
        environment = os.getenv("ENVIRONMENT", "development")
        region = os.getenv("AZURE_REGION", "eastus2")
        app_name = os.getenv("APP_NAME", "apps-services")

        # Set global properties that will be added to ALL log entries in SEQ
        seqlog.set_global_log_properties(
            AppName=app_name,
            Environment=environment,
            AppVersion=app_version,
            Region=region
        )

        # Log success
        logging.info(f"{Emoticons.STARTED} SEQ logging configured successfully")

        _seq_enabled = True
        return True

    except ImportError:
        logging.warning("seqlog package not installed - SEQ logging disabled")
        _seq_enabled = False
        return False
    except Exception as e:
        logging.error(f"Failed to configure SEQ logging: {e}")
        _seq_enabled = False
        return False


def is_seq_enabled() -> bool:
    """Check if SEQ logging is enabled."""
    global _seq_enabled, _seqlog_configured

    if not _seqlog_configured:
        configure_seq_logging()

    return _seq_enabled


# =============================================================================
# Property Helpers
# =============================================================================

def get_base_properties(
    context: func.Context,
    trigger_type: str,
    correlation_id: Optional[int] = None,
    correlation_id_name: str = "CorrelationId"
) -> Dict[str, Any]:
    """
    Get standardized base properties for all logs.

    Args:
        context: Azure Function context
        trigger_type: Type of trigger (http, timer, queue, blob, etc.)
        correlation_id: Optional correlation ID for tracing across async operations
        correlation_id_name: Name of the correlation ID property in logs

    Returns:
        Dict containing standardized base properties
    """
    props = {
        "AppName": os.getenv("APP_NAME", "apps-services"),
        "FunctionName": context.function_name,
        "AppVersion": os.getenv("APP_VERSION", "1.0.0"),
        "Environment": os.getenv("ENVIRONMENT", "development"),
        "InvocationId": context.invocation_id,
        "ExecutionTimestamp": datetime.utcnow().isoformat(),
        "TriggerType": trigger_type,
        "Region": os.getenv("AZURE_REGION", "eastus2"),
    }

    if correlation_id is not None:
        props[correlation_id_name] = correlation_id

    return props


def get_http_properties(req: func.HttpRequest) -> Dict[str, Any]:
    """Get HTTP-specific properties for logging.

    Note: URL is sanitized to remove query parameters which may contain
    sensitive data like tokens, API keys, or PII.
    """
    return {
        "HttpMethod": req.method,
        "HttpPath": sanitize_url(req.url),  # Strip query params for security
        "HttpRequestId": req.headers.get("x-request-id"),
        "HttpClientIP": req.headers.get("x-forwarded-for"),
        "HttpUserAgent": req.headers.get("user-agent"),
    }


def get_data_properties(
    entity_type: str,
    entity_id: Optional[str] = None,
    record_count: Optional[int] = None,
    batch_id: Optional[str] = None,
    data_source: Optional[str] = None,
    data_destination: Optional[str] = None
) -> Dict[str, Any]:
    """Get data processing properties for logging."""
    props = {"EntityType": entity_type}

    if entity_id is not None:
        props["EntityId"] = entity_id
    if record_count is not None:
        props["RecordCount"] = record_count
    if batch_id is not None:
        props["BatchId"] = batch_id
    if data_source is not None:
        props["DataSource"] = data_source
    if data_destination is not None:
        props["DataDestination"] = data_destination

    return props


def get_performance_properties(
    duration_ms: Optional[float] = None,
    external_call_duration_ms: Optional[float] = None,
    database_query_duration_ms: Optional[float] = None,
    retry_attempt: int = 0
) -> Dict[str, Any]:
    """Get performance tracking properties for logging."""
    props = {}

    if duration_ms is not None:
        props["DurationMs"] = duration_ms
    if external_call_duration_ms is not None:
        props["ExternalCallDurationMs"] = external_call_duration_ms
    if database_query_duration_ms is not None:
        props["DatabaseQueryDurationMs"] = database_query_duration_ms
    if retry_attempt > 0:
        props["RetryAttempt"] = retry_attempt

    return props


# =============================================================================
# Lifecycle Helpers
# =============================================================================

def log_function_start(
    context: func.Context,
    trigger_type: str,
    additional_props: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[int] = None,
    correlation_id_name: str = "CorrelationId"
) -> None:
    """Log function start with standard emoticon and properties."""
    props = get_base_properties(context, trigger_type, correlation_id, correlation_id_name)
    if additional_props:
        props.update(additional_props)

    message = f"{Emoticons.STARTED} Function started"
    logging.info(message, **props)


def log_function_complete(
    context: func.Context,
    trigger_type: str,
    duration_ms: float,
    additional_props: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[int] = None,
    correlation_id_name: str = "CorrelationId"
) -> None:
    """Log function completion with standard emoticon and properties."""
    props = get_base_properties(context, trigger_type, correlation_id, correlation_id_name)
    props.update(get_performance_properties(duration_ms=duration_ms))
    if additional_props:
        props.update(additional_props)

    message = f"{Emoticons.COMPLETED} Function completed successfully"
    logging.info(message, **props)


def log_function_stopped(
    context: func.Context,
    trigger_type: str,
    reason: str,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log function stopped (early termination without error)."""
    props = get_base_properties(context, trigger_type)
    props["StopReason"] = reason
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.STOPPED} Function stopped", **props)


# =============================================================================
# Data Operation Helpers
# =============================================================================

def log_receiving(
    context: func.Context,
    trigger_type: str,
    entity_type: str,
    source: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log receiving data operation."""
    props = get_base_properties(context, trigger_type)
    props["EntityType"] = entity_type
    if source:
        props["DataSource"] = source
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.RECEIVING} Receiving data", **props)


def log_sending(
    context: func.Context,
    trigger_type: str,
    entity_type: str,
    destination: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log sending data operation."""
    props = get_base_properties(context, trigger_type)
    props["EntityType"] = entity_type
    if destination:
        props["DataDestination"] = destination
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.SENDING} Sending data", **props)


def log_saving(
    context: func.Context,
    trigger_type: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    record_count: Optional[int] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log saving to database operation."""
    props = get_base_properties(context, trigger_type)
    props.update(get_data_properties(entity_type, entity_id, record_count))
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.SAVING} Saving to database", **props)


def log_processing(
    context: func.Context,
    trigger_type: str,
    entity_type: str,
    record_count: Optional[int] = None,
    batch_id: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log processing data operation."""
    props = get_base_properties(context, trigger_type)
    props.update(get_data_properties(entity_type, batch_id=batch_id, record_count=record_count))
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.PROCESSING} Processing data", **props)


def log_data_operation(
    message: str,
    operation_type: str,
    context: func.Context,
    trigger_type: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    record_count: Optional[int] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log data operation with appropriate emoticon."""
    emoticons = {
        "receive": Emoticons.RECEIVING,
        "send": Emoticons.SENDING,
        "save": Emoticons.SAVING,
        "process": Emoticons.PROCESSING,
        "sync": Emoticons.SYNCING,
        "create": Emoticons.CREATING,
        "update": Emoticons.UPDATING,
        "delete": Emoticons.DELETING,
    }

    emoticon = emoticons.get(operation_type.lower(), Emoticons.PROCESSING)
    props = get_base_properties(context, trigger_type)
    props.update(get_data_properties(entity_type, entity_id, record_count))

    if additional_props:
        props.update(additional_props)

    logging.info(f"{emoticon} {message}", **props)


# =============================================================================
# External Integration Helpers
# =============================================================================

def log_api_call(
    context: func.Context,
    trigger_type: str,
    endpoint: str,
    method: str = "GET",
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log API call initiated."""
    props = get_base_properties(context, trigger_type)
    props["Endpoint"] = endpoint
    props["HttpMethod"] = method
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.API_CALL} API call initiated", **props)


def log_external_service(
    context: func.Context,
    trigger_type: str,
    service_name: str,
    operation: str,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log external service call."""
    props = get_base_properties(context, trigger_type)
    props["ServiceName"] = service_name
    props["Operation"] = operation
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.EXTERNAL_SERVICE} Calling external service", **props)


def log_connected(
    context: func.Context,
    trigger_type: str,
    system_name: str,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log connected to external system."""
    props = get_base_properties(context, trigger_type)
    props["SystemName"] = system_name
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.CONNECTED} Connected to external system", **props)


def log_message_queued(
    context: func.Context,
    trigger_type: str,
    queue_name: str,
    message_id: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log message queued."""
    props = get_base_properties(context, trigger_type)
    props["QueueName"] = queue_name
    if message_id:
        props["MessageId"] = message_id
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.QUEUED} Message queued", **props)


# =============================================================================
# Security Helpers
# =============================================================================

def log_auth_success(
    context: func.Context,
    trigger_type: str,
    user_id: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log authentication successful."""
    props = get_base_properties(context, trigger_type)
    if user_id:
        props["UserId"] = user_id
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.AUTH_SUCCESS} Authentication successful", **props)


def log_access_denied(
    context: func.Context,
    trigger_type: str,
    user_id: Optional[str] = None,
    resource: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log access denied."""
    props = get_base_properties(context, trigger_type)
    if user_id:
        props["UserId"] = user_id
    if resource:
        props["Resource"] = resource
    if additional_props:
        props.update(additional_props)

    logging.warning(f"{Emoticons.ACCESS_DENIED} Access denied", **props)


# =============================================================================
# Performance Helpers
# =============================================================================

def log_slow_operation(
    context: func.Context,
    trigger_type: str,
    operation: str,
    duration_ms: float,
    threshold_ms: float = 5000,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log slow operation detected."""
    props = get_base_properties(context, trigger_type)
    props["Operation"] = operation
    props["DurationMs"] = duration_ms
    props["ThresholdMs"] = threshold_ms
    if additional_props:
        props.update(additional_props)

    logging.warning(f"{Emoticons.SLOW} Slow operation detected", **props)


def log_cache_hit(
    context: func.Context,
    trigger_type: str,
    cache_key: str,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log cache hit."""
    props = get_base_properties(context, trigger_type)
    props["CacheKey"] = cache_key
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.CACHE_HIT} Cache hit", **props)


# =============================================================================
# Error Helpers
# =============================================================================

def log_error(
    message: str,
    error: Exception,
    context: func.Context,
    trigger_type: str,
    additional_props: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[int] = None,
    correlation_id_name: str = "CorrelationId"
) -> None:
    """Log error with standard emoticon and properties.

    Note: Error messages are sanitized to remove sensitive data like
    credentials, connection strings, and API keys.
    """
    props = get_base_properties(context, trigger_type, correlation_id, correlation_id_name)
    props.update({
        "ErrorType": type(error).__name__,
        "ErrorMessage": sanitize_sensitive_data(str(error)),
    })

    if additional_props:
        props.update(additional_props)

    log_message = f"{Emoticons.FAILED} {message}"
    logging.error(log_message, exc_info=True, **props)


def log_critical(
    message: str,
    error: Exception,
    context: func.Context,
    trigger_type: str,
    component: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log critical system failure.

    Note: Error messages are sanitized to remove sensitive data like
    credentials, connection strings, and API keys.
    """
    props = get_base_properties(context, trigger_type)
    props.update({
        "ErrorType": type(error).__name__,
        "ErrorMessage": sanitize_sensitive_data(str(error)),
    })
    if component:
        props["Component"] = component
    if additional_props:
        props.update(additional_props)

    logging.error(f"{Emoticons.CRITICAL} {message}", exc_info=True, **props)


def log_warning(
    message: str,
    context: func.Context,
    trigger_type: str,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log warning."""
    props = get_base_properties(context, trigger_type)
    if additional_props:
        props.update(additional_props)

    logging.warning(f"{Emoticons.WARNING} {message}", **props)


def log_validation_warning(
    context: func.Context,
    trigger_type: str,
    validation_errors: list,
    entity_id: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log validation warning."""
    props = get_base_properties(context, trigger_type)
    props["ValidationErrors"] = validation_errors
    if entity_id:
        props["EntityId"] = entity_id
    if additional_props:
        props.update(additional_props)

    logging.warning(f"{Emoticons.WARNING} Validation warning", **props)


# =============================================================================
# Health Check Helpers
# =============================================================================

def log_health_ok(
    context: func.Context,
    trigger_type: str,
    component: str,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log health check passed."""
    props = get_base_properties(context, trigger_type)
    props["Component"] = component
    props["HealthStatus"] = "OK"
    if additional_props:
        props.update(additional_props)

    logging.info(f"{Emoticons.HEALTH_OK} Health check passed", **props)


def log_health_degraded(
    context: func.Context,
    trigger_type: str,
    component: str,
    reason: Optional[str] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log degraded performance."""
    props = get_base_properties(context, trigger_type)
    props["Component"] = component
    props["HealthStatus"] = "Degraded"
    if reason:
        props["DegradationReason"] = reason
    if additional_props:
        props.update(additional_props)

    logging.warning(f"{Emoticons.HEALTH_DEGRADED} Degraded performance", **props)


def log_health_failed(
    context: func.Context,
    trigger_type: str,
    component: str,
    error: Optional[Exception] = None,
    additional_props: Optional[Dict[str, Any]] = None
) -> None:
    """Log health check failed.

    Note: Error messages are sanitized to remove sensitive data.
    """
    props = get_base_properties(context, trigger_type)
    props["Component"] = component
    props["HealthStatus"] = "Failed"
    if error:
        props["ErrorType"] = type(error).__name__
        props["ErrorMessage"] = sanitize_sensitive_data(str(error))
    if additional_props:
        props.update(additional_props)

    logging.error(f"{Emoticons.HEALTH_FAILED} Health check failed", **props)
