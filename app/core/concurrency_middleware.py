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

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        """
        Process update with concurrency limit.
        
        Acquires semaphore before processing, releases after completion.
        """
        async with self._semaphore:
            return await handler(event, data)
