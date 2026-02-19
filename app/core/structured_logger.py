"""
Structured logging normalization.

Single contract for critical lifecycle logs:
- component
- operation
- correlation_id (optional)
- outcome
- duration_ms (optional, omitted if None)
- reason (optional)

Do not log secrets or full payloads.
"""
from typing import Optional

# Type alias for logger (logging.Logger)
from logging import Logger


def log_event(
    logger: Logger,
    *,
    component: str,
    operation: str,
    correlation_id: Optional[str] = None,
    outcome: str,
    duration_ms: Optional[int] = None,
    reason: Optional[str] = None,
    level: str = "info",
    message: Optional[str] = None,
) -> None:
    """
    Emit structured log event.

    Args:
        logger: Logger instance
        component: Component name (e.g., "webhook", "worker", "http", "telegram")
        operation: Operation name (e.g., "webhook_start", "reminders_iteration", "health_check")
        correlation_id: Request/task/iteration identifier (optional)
        outcome: Outcome (e.g., "success", "failed", "cancelled")
        duration_ms: Duration in milliseconds (omitted if None)
        reason: Short non-PII explanation (optional)
        level: Log level ("info", "warning", "error", "critical", "debug")
        message: Optional override message (defaults to operation)
    """
    extra: dict = {
        "component": component,
        "operation": operation,
        "outcome": outcome,
    }
    if correlation_id is not None:
        extra["correlation_id"] = str(correlation_id)
    if duration_ms is not None:
        extra["duration_ms"] = duration_ms
    if reason is not None:
        extra["reason"] = reason

    msg = message or f"{component} {operation} outcome={outcome}"
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(msg, extra=extra)
