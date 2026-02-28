"""
Global rate limiting middleware.
Ограничивает количество update'ов от одного пользователя.
"""
import time
import logging
from typing import Callable, Dict, Any, Awaitable
from collections import defaultdict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)

# Максимум запросов: 30 за 60 секунд на пользователя
RATE_LIMIT_WINDOW = 60  # секунды
RATE_LIMIT_MAX = 30  # максимум запросов в окне

# Отдельный лимит для /start: 5 за 60 секунд
START_RATE_LIMIT_MAX = 5


class GlobalRateLimitMiddleware(BaseMiddleware):
    """Per-user rate limiting для всех update'ов."""

    def __init__(self):
        self._user_requests: Dict[int, list] = defaultdict(list)
        self._last_cleanup = time.monotonic()

    def _cleanup_old(self):
        """Очистка старых записей раз в 5 минут."""
        now = time.monotonic()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now
        cutoff = now - RATE_LIMIT_WINDOW * 2
        to_delete = [
            uid
            for uid, times in self._user_requests.items()
            if not times or times[-1] < cutoff
        ]
        for uid in to_delete:
            del self._user_requests[uid]

    def _is_rate_limited(self, user_id: int, is_start: bool = False) -> bool:
        now = time.monotonic()
        self._cleanup_old()

        cutoff = now - RATE_LIMIT_WINDOW
        requests = self._user_requests[user_id]
        self._user_requests[user_id] = [t for t in requests if t > cutoff]

        max_requests = START_RATE_LIMIT_MAX if is_start else RATE_LIMIT_MAX

        if len(self._user_requests[user_id]) >= max_requests:
            return True

        self._user_requests[user_id].append(now)
        return False

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        is_start = False

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            is_start = bool(event.text and event.text.strip().startswith("/start"))
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None

        if user_id and self._is_rate_limited(user_id, is_start):
            logger.warning(
                "RATE_LIMITED user=%s is_start=%s", user_id, is_start
            )
            return  # Молча игнорируем

        return await handler(event, data)
