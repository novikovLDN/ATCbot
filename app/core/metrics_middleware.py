"""
Metrics collection middleware for Telegram updates.

Tracks:
- Request count (total, success, error)
- Request latency (histogram)
- Concurrent update count
- Rate limit hits
- Error classification
"""
import asyncio
import time
import logging
from typing import Callable, Awaitable, Dict, Any

from aiogram import BaseMiddleware

from app.core.metrics import get_metrics

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseMiddleware):
    """
    Collects per-update metrics: latency, success/error counts, concurrency.

    Should be registered BEFORE the error boundary middleware so it captures
    all outcomes including errors caught by the boundary.
    """

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        m = get_metrics()
        m.requests_total.inc()
        m.request_rate.record()
        m.concurrent_updates.inc()

        # Track peak concurrency
        current = m.concurrent_updates.value
        if current > m.peak_concurrent_updates.value:
            m.peak_concurrent_updates.set(current)

        start = time.monotonic()
        try:
            result = await handler(event, data)
            m.requests_success.inc()
            return result
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            m.requests_timeout.inc()
            m.requests_error.inc()
            m.error_rate.record()
            m.errors.record("TimeoutError", "Update processing timeout", "middleware")
            raise
        except Exception as e:
            m.requests_error.inc()
            m.error_rate.record()
            m.errors.record(type(e).__name__, str(e)[:200], "middleware")
            raise
        finally:
            duration = time.monotonic() - start
            m.request_latency.observe(duration)
            m.concurrent_updates.dec()
