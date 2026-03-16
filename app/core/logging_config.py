# -*- coding: utf-8 -*-
"""
Production-grade logging configuration.

Routes logs by severity for correct container/platform classification:
- INFO, WARNING → STDOUT (platform shows as [inf])
- ERROR, CRITICAL → STDERR (platform shows as [err])

Supports two formats:
- text (default): human-readable for development
- json: structured JSON for log aggregators (Railway, Datadog, etc.)

Set LOG_FORMAT=json environment variable to enable JSON logging.

POOL STABILITY: Uses QueueHandler + QueueListener so the event loop never
blocks on stdout/stderr. If stdout blocks, only the listener thread blocks,
not the main event loop or watchdog exit path.
"""

import atexit
import json as json_module
import logging
import os
import queue
import re
import sys
import traceback
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener


class MaxLevelFilter(logging.Filter):
    """
    Allows only records up to a specified level (inclusive).
    Used to prevent ERROR/CRITICAL logs from going to stdout.
    """

    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno <= self.max_level


class PIISanitizingFilter(logging.Filter):
    """
    SECURITY: Sanitize PII from log messages and exception tracebacks.

    Masks VPN keys (vless://...), long tokens, and other sensitive data
    that might leak through exception tracebacks in production logs.
    """

    # Patterns to sanitize (compiled once for performance)
    _PATTERNS = [
        # VLESS keys: vless://uuid@host:port?...#name
        (re.compile(r'vless://[^\s"\']+', re.IGNORECASE), 'vless://***REDACTED***'),
        # Bearer tokens
        (re.compile(r'Bearer\s+[A-Za-z0-9._\-]+', re.IGNORECASE), 'Bearer ***'),
        # Bot tokens: 123456:ABC-DEF...
        (re.compile(r'\b\d{8,10}:[A-Za-z0-9_\-]{30,50}\b'), '***BOT_TOKEN***'),
        # Database URLs: postgresql://user:pass@host/db
        (re.compile(r'postgresql://[^\s"\']+', re.IGNORECASE), 'postgresql://***REDACTED***'),
        # UUID values in arguments (only in traceback context, 36 chars)
        (re.compile(r'uuid=["\']?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}["\']?', re.IGNORECASE),
         'uuid=***'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize PII in log message and exception info."""
        if record.args:
            # Sanitize formatted message args
            record.msg = self._sanitize(record.getMessage())
            record.args = None

        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)

        if record.exc_text:
            record.exc_text = self._sanitize(record.exc_text)

        return True

    @classmethod
    def _sanitize(cls, text: str) -> str:
        """Apply all sanitization patterns."""
        for pattern, replacement in cls._PATTERNS:
            text = pattern.sub(replacement, text)
        return text


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }
        return json_module.dumps(log_entry, ensure_ascii=False, default=str)


# Module-level listener so it can be stopped on shutdown
_log_listener: QueueListener | None = None


def setup_logging():
    """
    Configure logging: QueueHandler on root logger; QueueListener in background
    thread with StreamHandlers (same format and routing as before).

    Set LOG_FORMAT=json for structured JSON output (recommended for production).

    Event loop never blocks on I/O for logging; watchdog exit cannot be
    blocked by logging. Must be called before any logger is used.
    """
    global _log_listener

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    log_format = os.getenv("LOG_FORMAT", "text").lower()
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    # Handlers that run in the listener thread (same format and routing as before)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(MaxLevelFilter(logging.WARNING))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)

    # SECURITY: Add PII sanitizing filter to prevent sensitive data in logs
    pii_filter = PIISanitizingFilter()
    stdout_handler.addFilter(pii_filter)
    stderr_handler.addFilter(pii_filter)

    log_queue = queue.Queue()
    root_logger.addHandler(QueueHandler(log_queue))

    _log_listener = QueueListener(
        log_queue,
        stdout_handler,
        stderr_handler,
        respect_handler_level=True,
    )
    _log_listener.start()
    atexit.register(_stop_log_listener)


def _stop_log_listener():
    """Stop the queue listener (called at exit)."""
    global _log_listener
    if _log_listener is not None:
        _log_listener.stop()
        _log_listener = None
