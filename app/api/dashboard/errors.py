"""Dashboard API error helpers."""
import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def server_error(code: str) -> HTTPException:
    """500 with a stable error code only. Call inside `except`: the exception
    text (SQL, DSNs, driver/provider messages) goes to the log with the
    traceback, never into the response (P2-16)."""
    logger.exception("DASHBOARD_ROUTE_FAIL %s", code)
    return HTTPException(500, code)
