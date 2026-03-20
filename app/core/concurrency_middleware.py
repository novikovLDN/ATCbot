"""
Global concurrency limiter middleware for update processing.

Prevents the bot from being overwhelmed by too many concurrent updates.
Provides fast backpressure — under extreme load (10-20k concurrent /start),
updates are shed quickly instead of queueing and starving the event loop.
"""
import asyncio
import logging
import time
from typing import Callable, Awaitable, Dict, Any

from aiogram import BaseMiddleware

logger = logging.getLogger(__name__)


class ConcurrencyLimiterMiddleware(BaseMiddleware):
    """
    Middleware that limits concurrent update processing using a semaphore.

    Ensures that at most MAX_CONCURRENT_UPDATES updates are processed simultaneously.
    Under overload, sheds excess updates in < 5s instead of queueing for 25s+.
    """

    def __init__(self, semaphore: asyncio.Semaphore):
        super().__init__()
        self._semaphore = semaphore
        self._shed_count = 0
        self._last_shed_log = 0.0

    # Reduced from 10s to 3s — fail fast under overload.
    # With 10s timeout and 10k queued updates, each waits 10s then fails,
    # starving the event loop. With 3s, we shed quickly and keep serving.
    _ACQUIRE_TIMEOUT = 3.0

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        """
        Process update with concurrency limit.

        Acquires semaphore before processing, releases after completion.
        Times out if semaphore cannot be acquired within _ACQUIRE_TIMEOUT seconds.
        """
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._ACQUIRE_TIMEOUT)
        except asyncio.TimeoutError:
            self._shed_count += 1
            now = time.monotonic()
            # Log shed events at most once per 10 seconds to avoid log spam during DDoS
            if now - self._last_shed_log > 10.0:
                logger.warning(
                    "OVERLOAD_SHED count=%d (semaphore full, dropping updates to protect bot)",
                    self._shed_count,
                )
                self._last_shed_log = now
            try:
                from app.core.metrics import get_metrics
                get_metrics().requests_shed.inc()
            except Exception:
                pass
            return None
        try:
            return await handler(event, data)
        finally:
            self._semaphore.release()
