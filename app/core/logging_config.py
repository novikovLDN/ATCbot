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
        # Остальные схемы клиентских ключей. Раньше фильтр знал только vless,
        # хотя проект раздаёт ссылки и в этих форматах — Happ/Incy собираются
        # в app/services/happ_crypto.py и incy_crypto.py.
        (re.compile(r'\b(happ|incy|ss|trojan|vmess)://[^\s"\']+', re.IGNORECASE),
         r'\1://***REDACTED***'),
        # Подписочная ссылка панели: https://<host>/sub/<shortUuid> и
        # выданная ботом https://<host>/api/sub/<token>. Это ОСНОВНОЙ секрет
        # проекта — кто открыл ссылку, тот получил конфиги VPN, — и до сих
        # пор он не редактировался вовсе: правило ловило только литеральное
        # `uuid=`, а здесь идентификатор лежит в пути.
        (re.compile(r'https?://[^\s"\']*?/(api/)?sub/[^\s"\']+', re.IGNORECASE),
         '***SUBSCRIPTION_URL_REDACTED***'),
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

        # Трейсбэки не санировались НИКОГДА.
        #
        # Здесь стояло `if record.exc_text: record.exc_text = ...`, но
        # exc_text заполняет форматтер внутри emit(), а фильтры отрабатывают
        # ДО него — на этот момент поле всегда None. То есть заявленная в
        # докстринге защита «might leak through exception tracebacks» не
        # работала ни разу.
        #
        # Форматируем трейсбэк сами и кладём в exc_text уже очищенным:
        # logging.Formatter.format переиспользует непустой exc_text и заново
        # его не собирает, поэтому в вывод попадает только санированный текст.
        if record.exc_info and record.exc_info[0] is not None:
            if not record.exc_text:
                record.exc_text = "".join(
                    traceback.format_exception(*record.exc_info)
                ).rstrip("\n")
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
        _clean = PIISanitizingFilter._sanitize
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _clean(record.getMessage()),
        }
        if record.exc_info and record.exc_info[0] is not None:
            # Собиралось напрямую из exc_info, мимо exc_text, — то есть мимо
            # PIISanitizingFilter: в JSON-режиме (LOG_FORMAT=json, штатный для
            # прода) текст исключения и трейсбэк уходили в лог как есть.
            # Чистим теми же правилами, что и обычный вывод.
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": _clean(str(record.exc_info[1])),
                "traceback": [
                    _clean(line) for line in traceback.format_exception(*record.exc_info)
                ],
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
