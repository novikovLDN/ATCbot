# -*- coding: utf-8 -*-
"""
Production-grade logging configuration.

Routes logs by severity for correct container/platform classification:
- INFO, WARNING → STDOUT (platform shows as [inf])
- ERROR, CRITICAL → STDERR (platform shows as [err])

POOL STABILITY: Uses QueueHandler + QueueListener so the event loop never
blocks on stdout/stderr. If stdout blocks, only the listener thread blocks,
not the main event loop or watchdog exit path.
"""

import atexit
import logging
import queue
import sys
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


# Module-level listener so it can be stopped on shutdown
_log_listener: QueueListener | None = None


def setup_logging():
    """
    Configure logging: QueueHandler on root logger; QueueListener in background
    thread with StreamHandlers (same format and routing as before).

    Event loop never blocks on I/O for logging; watchdog exit cannot be
    blocked by logging. Must be called before any logger is used.
    """
    global _log_listener

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

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
