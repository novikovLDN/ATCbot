# -*- coding: utf-8 -*-
"""
Production-grade logging configuration.

Routes logs by severity for correct container/platform classification:
- INFO, WARNING → STDOUT (platform shows as [inf])
- ERROR, CRITICAL → STDERR (platform shows as [err])

Most container runtimes (Railway, Docker, K8s) classify logs by stream,
not by log level. Sending INFO to stderr causes false [err] classification.
"""

import logging
import sys


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


def setup_logging():
    """
    Configure logging to:
    - Send INFO and WARNING to stdout
    - Send ERROR and CRITICAL to stderr
    - Preserve existing format

    Must be called before any logger is used.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Clear existing handlers (important when replacing basicConfig)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # STDOUT handler (INFO + WARNING)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(MaxLevelFilter(logging.WARNING))
    stdout_handler.setFormatter(formatter)

    # STDERR handler (ERROR + CRITICAL)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)

    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(stderr_handler)
