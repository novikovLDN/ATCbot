"""
Global rate limiting middleware.
Ограничивает количество update'ов от одного пользователя.
При агрессивном флуде — временный бан.
"""
import time
import logging
from typing import Callable, Dict, Any, Awaitable
from collections import defaultdict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)

# Обычный лимит: 30 запросов за 60 секунд
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 30

# Лимит для /start: 5 за 60 секунд
START_RATE_LIMIT_MAX = 5

# Агрессивный флуд: если 60+ запросов за 60 сек → бан на 5 минут
FLOOD_BAN_THRESHOLD = 60
FLOOD_BAN_DURATION = 300  # 5 минут


class GlobalRateLimitMiddleware(BaseMiddleware):
    """Per-user rate limiting + temporary ban for aggressive flooding."""

    def __init__(self):
        self._user_requests: Dict[int, list] = defaultdict(list)
        self._banned_users: Dict[int, float] = {}  # user_id -> ban_expires_at
        self._last_cleanup = time.monotonic()

    def _cleanup_old(self):
        now = time.monotonic()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now

        # Cleanup request history
        cutoff = now - RATE_LIMIT_WINDOW * 2
        to_delete = [
            uid
            for uid, times in self._user_requests.items()
            if not times or times[-1] < cutoff
        ]
        for uid in to_delete:
            del self._user_requests[uid]

        # Cleanup expired bans
        expired = [
            uid for uid, expires in self._banned_users.items() if expires <= now
        ]
        for uid in expired:
            del self._banned_users[uid]

    def _is_rate_limited(self, user_id: int, is_start: bool = False) -> bool:
        now = time.monotonic()
        self._cleanup_old()

        # Проверяем бан
        if user_id in self._banned_users:
            if now < self._banned_users[user_id]:
                return True
            del self._banned_users[user_id]

        # Очищаем старые запросы в окне
        cutoff = now - RATE_LIMIT_WINDOW
        requests = self._user_requests[user_id]
        self._user_requests[user_id] = [t for t in requests if t > cutoff]
        self._user_requests[user_id].append(now)

        request_count = len(self._user_requests[user_id])

        # Агрессивный флуд → бан
        if request_count >= FLOOD_BAN_THRESHOLD:
            self._banned_users[user_id] = now + FLOOD_BAN_DURATION
            logger.warning(
                "FLOOD_BAN user=%s requests=%d ban_duration=%ds",
                user_id,
                request_count,
                FLOOD_BAN_DURATION,
            )
            return True

        # Обычный rate limit
        max_requests = START_RATE_LIMIT_MAX if is_start else RATE_LIMIT_MAX
        if request_count > max_requests:
            return True

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
            is_start = bool(
                event.text and event.text.strip().startswith("/start")
            )
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None

        if user_id and self._is_rate_limited(user_id, is_start):
            logger.warning(
                "RATE_LIMITED user=%s is_start=%s", user_id, is_start
            )
            return

        return await handler(event, data)
