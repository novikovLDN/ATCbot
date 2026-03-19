"""
Global concurrency limiter middleware for update processing.

Prevents the bot from being overwhelmed by too many concurrent updates.
"""
import asyncio
import logging
from typing import Callable, Awaitable, Dict, Any

from aiogram import BaseMiddleware

logger = logging.getLogger(__name__)


class ConcurrencyLimiterMiddleware(BaseMiddleware):
    """
    Middleware that limits concurrent update processing using a semaphore.
    
    Ensures that at most MAX_CONCURRENT_UPDATES updates are processed simultaneously.
    """
    
    def __init__(self, semaphore: asyncio.Semaphore):
        super().__init__()
        self._semaphore = semaphore

    _ACQUIRE_TIMEOUT = 10.0  # seconds to wait for semaphore slot

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
            logger.warning("Concurrency limiter: semaphore acquire timed out, dropping update")
            return None
        try:
            return await handler(event, data)
        finally:
            self._semaphore.release()
