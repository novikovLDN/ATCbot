"""
Structured logging helpers for observability and SLO tracking.

STEP 2 — OBSERVABILITY & SLO FOUNDATION:
This module provides structured logging utilities to ensure consistent
logging patterns across handlers and workers for observability.

Logging contract:
- request_id / correlation_id: Unique identifier for request/operation
- component: Component name (handler, worker, service)
- operation: Operation name (payment_finalization, activation, etc.)
- outcome: success | degraded | failed

Failure taxonomy:
- infra_error: Infrastructure errors (DB, network, timeouts)
- dependency_error: External dependency errors (VPN API, payment provider)
- domain_error: Business logic errors (validation, business rules)
- unexpected_error: Unexpected errors (bugs, unhandled exceptions)
"""

import logging
import json
import uuid
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from contextvars import ContextVar

# Context variable for correlation ID (per-request/operation)
_correlation_id: ContextVar[Optional[str]] = ContextVar('correlation_id', default=None)

logger = logging.getLogger(__name__)


def generate_correlation_id() -> str:
    """
    Generate a unique correlation ID for request/operation tracking.
    
    Returns:
        UUID string (e.g., "550e8400-e29b-41d4-a716-446655440000")
    """
    return str(uuid.uuid4())


def set_correlation_id(correlation_id: str) -> None:
    """
    Set correlation ID for current context (request/operation).
    
    Args:
        correlation_id: Correlation ID to set
    """
    _correlation_id.set(correlation_id)


def get_correlation_id() -> Optional[str]:
    """
    Get correlation ID from current context.
    
    Returns:
        Correlation ID if set, None otherwise
    """
    return _correlation_id.get()


def log_handler_entry(
    handler_name: str,
    telegram_id: Optional[int] = None,
    operation: Optional[str] = None,
    correlation_id: Optional[str] = None,
    **kwargs
) -> str:
    """
    Log handler entry point.
    
    STEP 2 — OBSERVABILITY: Structured logging for handler entry.
    
    PART B — CORRELATION IDS:
    For handlers, correlation_id should be update_id or message_id from Telegram.
    If not provided, generates a UUID as fallback.
    
    Args:
        handler_name: Name of the handler (e.g., "process_successful_payment")
        telegram_id: Telegram ID of the user (optional)
        operation: Operation name (e.g., "payment_finalization", optional)
        correlation_id: Correlation ID (prefer message_id/update_id, optional)
        **kwargs: Additional context to log
    
    Returns:
        Correlation ID for this request
    """
    if correlation_id is None:
        correlation_id = generate_correlation_id()
    set_correlation_id(correlation_id)
    
    log_data = {
        "event": "HANDLER_ENTRY",
        "handler": handler_name,
        "correlation_id": correlation_id,
        "component": "handler",
        "operation": operation or handler_name,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    
    if telegram_id:
        log_data["telegram_id"] = telegram_id
    
    if kwargs:
        log_data.update(kwargs)
    
    # Emit as JSON with proper level field for external log aggregation
    log_data["level"] = "INFO"
    logger.info(json.dumps(log_data))
    return correlation_id


def log_handler_exit(
    handler_name: str,
    outcome: str,  # "success" | "degraded" | "failed"
    telegram_id: Optional[int] = None,
    operation: Optional[str] = None,
    error_type: Optional[str] = None,  # "infra_error" | "dependency_error" | "domain_error" | "unexpected_error"
    duration_ms: Optional[float] = None,
    **kwargs
) -> None:
    """
    Log handler exit point.
    
    STEP 2 — OBSERVABILITY: Structured logging for handler exit.
    
    Args:
        handler_name: Name of the handler
        outcome: Outcome of the operation ("success" | "degraded" | "failed")
        telegram_id: Telegram ID of the user (optional)
        operation: Operation name (optional)
        error_type: Type of error if outcome is "failed" (optional)
        duration_ms: Duration of the operation in milliseconds (optional)
        **kwargs: Additional context to log
    """
    correlation_id = get_correlation_id()
    
    log_data = {
        "event": "HANDLER_EXIT",
        "handler": handler_name,
        "correlation_id": correlation_id,
        "component": "handler",
        "operation": operation or handler_name,
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    
    if telegram_id:
        log_data["telegram_id"] = telegram_id
    
    if error_type:
        log_data["error_type"] = error_type
    
    if duration_ms is not None:
        log_data["duration_ms"] = duration_ms
    
    if kwargs:
        log_data.update(kwargs)
    
    # Emit as JSON with proper level field matching Python logging level
    if outcome == "failed":
        log_data["level"] = "ERROR"
        logger.error(json.dumps(log_data))
    elif outcome == "degraded":
        log_data["level"] = "WARNING"
        logger.warning(json.dumps(log_data))
    else:
        log_data["level"] = "INFO"
        logger.info(json.dumps(log_data))


def log_worker_iteration_start(
    worker_name: str,
    iteration_number: Optional[int] = None,
    **kwargs
) -> str:
    """
    Log worker iteration start.
    
    STEP 2 — OBSERVABILITY: Structured logging for worker iteration start.
    
    Args:
        worker_name: Name of the worker (e.g., "activation_worker")
        iteration_number: Iteration number (optional)
        **kwargs: Additional context to log
    
    Returns:
        Correlation ID for this iteration
    """
    # WATCHDOG_FIX: worker heartbeat for multi-signal freeze detection
    try:
        from app.core.watchdog_heartbeats import mark_worker_iteration
        mark_worker_iteration()
    except Exception:
        pass
    correlation_id = generate_correlation_id()
    set_correlation_id(correlation_id)
    
    log_data = {
        "event": "ITERATION_START",
        "worker": worker_name,
        "correlation_id": correlation_id,
        "component": "worker",
        "operation": f"{worker_name}_iteration",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    
    if iteration_number is not None:
        log_data["iteration_number"] = iteration_number
    
    if kwargs:
        log_data.update(kwargs)
    
    # Emit as JSON with proper level field for external log aggregation
    log_data["level"] = "INFO"
    logger.info(json.dumps(log_data))
    return correlation_id


def log_worker_iteration_end(
    worker_name: str,
    outcome: str,  # "success" | "degraded" | "failed" | "skipped"
    items_processed: Optional[int] = None,
    error_type: Optional[str] = None,
    duration_ms: Optional[float] = None,
    **kwargs
) -> None:
    """
    Log worker iteration end.
    
    STEP 2 — OBSERVABILITY: Structured logging for worker iteration end.
    
    Args:
        worker_name: Name of the worker
        outcome: Outcome of the iteration ("success" | "degraded" | "failed" | "skipped")
        items_processed: Number of items processed (optional)
        error_type: Type of error if outcome is "failed" (optional)
        duration_ms: Duration of the iteration in milliseconds (optional)
        **kwargs: Additional context to log
    """
    correlation_id = get_correlation_id()
    
    log_data = {
        "event": "ITERATION_END",
        "worker": worker_name,
        "correlation_id": correlation_id,
        "component": "worker",
        "operation": f"{worker_name}_iteration",
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    
    if items_processed is not None:
        log_data["items_processed"] = items_processed
    
    if error_type:
        log_data["error_type"] = error_type
    
    if duration_ms is not None:
        log_data["duration_ms"] = duration_ms
    
    if kwargs:
        log_data.update(kwargs)
    
    # Emit as JSON with proper level field matching Python logging level
    if outcome == "failed":
        log_data["level"] = "ERROR"
        logger.error(json.dumps(log_data))
    elif outcome == "degraded":
        log_data["level"] = "WARNING"
        logger.warning(json.dumps(log_data))
    elif outcome == "skipped":
        log_data["level"] = "INFO"
        logger.info(json.dumps(log_data))
    else:
        log_data["level"] = "INFO"
        logger.info(json.dumps(log_data))


def classify_error(exception: Exception) -> str:
    """
    Classify error type for failure taxonomy.
    
    STEP 2 — OBSERVABILITY: Failure taxonomy classification.
    
    Failure taxonomy:
    - infra_error: Infrastructure errors (DB, network, timeouts)
    - dependency_error: External dependency errors (VPN API, payment provider)
    - domain_error: Business logic errors (validation, business rules)
    - unexpected_error: Unexpected errors (bugs, unhandled exceptions)
    
    Args:
        exception: Exception to classify
    
    Returns:
        Error type: "infra_error" | "dependency_error" | "domain_error" | "unexpected_error"
    """
    import asyncpg
    import asyncio
    from app.services.payments.exceptions import PaymentServiceError
    from app.services.activation.exceptions import ActivationServiceError
    # P0 HOTFIX: VPNServiceError is defined in service.py, not exceptions.py
    try:
        from app.services.vpn.service import VPNServiceError
    except ImportError:
        # Fallback: define minimal exception class if import fails
        class VPNServiceError(Exception):
            pass
    from app.services.subscriptions.exceptions import SubscriptionServiceError
    from app.services.trials.exceptions import TrialServiceError
    from app.services.admin.exceptions import AdminServiceError
    from app.services.notifications.exceptions import NotificationServiceError
    
    # Domain errors (business logic)
    if isinstance(exception, (
        PaymentServiceError,
        ActivationServiceError,
        VPNServiceError,
        SubscriptionServiceError,
        TrialServiceError,
        AdminServiceError,
        NotificationServiceError,
    )):
        return "domain_error"
    
    # Infrastructure errors (DB, network, timeouts)
    if isinstance(exception, (
        asyncpg.PostgresError,
        asyncio.TimeoutError,
        ConnectionError,
        OSError,
    )):
        return "infra_error"
    
    # Dependency errors (external APIs)
    # These are typically wrapped in domain exceptions, but check for HTTP errors
    error_str = str(exception).lower()
    if any(keyword in error_str for keyword in ["vpn api", "payment provider", "telegram api", "http", "api"]):
        return "dependency_error"
    
    # Unexpected errors (bugs, unhandled exceptions)
    return "unexpected_error"


def log_operation(
    component: str,
    operation: str,
    outcome: str,
    error_type: Optional[str] = None,
    duration_ms: Optional[float] = None,
    **kwargs
) -> None:
    """
    Log a generic operation with structured format.
    
    STEP 2 — OBSERVABILITY: Generic structured logging.
    
    Args:
        component: Component name (e.g., "handler", "worker", "service")
        operation: Operation name (e.g., "payment_finalization")
        outcome: Outcome ("success" | "degraded" | "failed")
        error_type: Error type if outcome is "failed" (optional)
        duration_ms: Duration in milliseconds (optional)
        **kwargs: Additional context
    """
    correlation_id = get_correlation_id()
    
    log_data = {
        "event": "OPERATION",
        "correlation_id": correlation_id,
        "component": component,
        "operation": operation,
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    
    if error_type:
        log_data["error_type"] = error_type
    
    if duration_ms is not None:
        log_data["duration_ms"] = duration_ms
    
    if kwargs:
        log_data.update(kwargs)
    
    # Emit as JSON with proper level field matching Python logging level
    if outcome == "failed":
        log_data["level"] = "ERROR"
        logger.error(json.dumps(log_data))
    elif outcome == "degraded":
        log_data["level"] = "WARNING"
        logger.warning(json.dumps(log_data))
    else:
        log_data["level"] = "INFO"
        logger.info(json.dumps(log_data))
