"""
Global rate limiting middleware.
Ограничивает количество update'ов от одного пользователя.
При агрессивном флуде — временный бан.

Uses Redis when available (survives restarts, works across instances).
Falls back to in-memory when Redis is unavailable.
"""
import asyncio
import time
import logging
from typing import Callable, Dict, Any, Awaitable, Optional
from collections import defaultdict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)

# Обычный лимит: 30 запросов за 60 секунд
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 30

# Лимит для /start: 8 за 60 секунд
START_RATE_LIMIT_MAX = 8

# Агрессивный флуд: если 60+ запросов за 60 сек → бан на 5 минут
FLOOD_BAN_THRESHOLD = 60
FLOOD_BAN_DURATION = 300  # 5 минут

# SECURITY: Maximum tracked users to prevent memory exhaustion during DDoS
MAX_TRACKED_USERS = 50_000
MAX_BANNED_USERS = 10_000

# Redis key prefixes
_REDIS_RATE_PREFIX = "rl:"
_REDIS_BAN_PREFIX = "rl:ban:"


class GlobalRateLimitMiddleware(BaseMiddleware):
    """Per-user rate limiting + temporary ban for aggressive flooding.

    Redis mode: uses sorted sets for sliding window + keys with TTL for bans.
    Memory mode: fallback when Redis is unavailable (same logic as before).
    """

    def __init__(self):
        # In-memory fallback
        self._user_requests: Dict[int, list] = defaultdict(list)
        self._banned_users: Dict[int, float] = {}  # user_id -> ban_expires_at
        self._last_cleanup = time.monotonic()
        # Redis handle (set once at startup)
        self._redis = None
        self._redis_checked = False

    async def _get_redis(self):
        """Lazy-load Redis client. Returns None if unavailable."""
        if not self._redis_checked:
            self._redis_checked = True
            try:
                from app.utils.redis_client import get_redis
                self._redis = await get_redis()
                if self._redis:
                    logger.info("RATE_LIMIT using Redis backend")
                else:
                    logger.info("RATE_LIMIT using in-memory backend (Redis not configured)")
            except Exception as e:
                logger.warning("RATE_LIMIT Redis init failed, using in-memory: %s", e)
                self._redis = None
        return self._redis

    # ── Redis-backed rate limiting ─────────────────────────────────────

    async def _is_rate_limited_redis(self, user_id: int, is_start: bool = False) -> bool:
        """Check rate limit using Redis sorted set (sliding window)."""
        redis = self._redis
        now = time.time()

        # Check ban first
        ban_key = f"{_REDIS_BAN_PREFIX}{user_id}"
        try:
            banned = await redis.exists(ban_key)
            if banned:
                return True
        except Exception:
            # Redis error — fall through to memory
            return self._is_rate_limited_memory(user_id, is_start)

        rate_key = f"{_REDIS_RATE_PREFIX}{user_id}"
        cutoff = now - RATE_LIMIT_WINDOW

        try:
            pipe = redis.pipeline(transaction=True)
            # Remove old entries
            pipe.zremrangebyscore(rate_key, 0, cutoff)
            # Add current request
            pipe.zadd(rate_key, {str(now): now})
            # Count requests in window
            pipe.zcard(rate_key)
            # Set TTL so keys auto-expire
            pipe.expire(rate_key, RATE_LIMIT_WINDOW * 2)
            results = await pipe.execute()
            request_count = results[2]
        except Exception as e:
            logger.warning("RATE_LIMIT Redis pipeline error: %s", e)
            return self._is_rate_limited_memory(user_id, is_start)

        # Aggressive flood → ban
        if request_count >= FLOOD_BAN_THRESHOLD:
            try:
                await redis.setex(ban_key, FLOOD_BAN_DURATION, "1")
            except Exception:
                pass
            logger.warning(
                "FLOOD_BAN user=%s requests=%d ban_duration=%ds",
                user_id, request_count, FLOOD_BAN_DURATION,
            )
            try:
                from app.core.metrics import get_metrics
                get_metrics().flood_bans.inc()
            except Exception:
                pass
            return True

        max_requests = START_RATE_LIMIT_MAX if is_start else RATE_LIMIT_MAX
        return request_count > max_requests

    # ── In-memory fallback ─────────────────────────────────────────────

    def _cleanup_old(self):
        now = time.monotonic()
        if now - self._last_cleanup < 60:
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

        expired = [
            uid for uid, expires in self._banned_users.items() if expires <= now
        ]
        for uid in expired:
            del self._banned_users[uid]

        if len(self._user_requests) > MAX_TRACKED_USERS:
            sorted_users = sorted(
                self._user_requests.items(),
                key=lambda item: item[1][-1] if item[1] else 0,
            )
            evict_count = len(sorted_users) // 2
            for uid, _ in sorted_users[:evict_count]:
                del self._user_requests[uid]
            logger.warning(
                "RATE_LIMIT_EMERGENCY_EVICTION evicted=%d remaining=%d",
                evict_count, len(self._user_requests),
            )

        if len(self._banned_users) > MAX_BANNED_USERS:
            sorted_bans = sorted(
                self._banned_users.items(), key=lambda item: item[1]
            )
            evict_count = len(sorted_bans) // 2
            for uid, _ in sorted_bans[:evict_count]:
                del self._banned_users[uid]

    def _is_rate_limited_memory(self, user_id: int, is_start: bool = False) -> bool:
        now = time.monotonic()
        self._cleanup_old()

        if user_id in self._banned_users:
            if now < self._banned_users[user_id]:
                return True
            del self._banned_users[user_id]

        cutoff = now - RATE_LIMIT_WINDOW
        requests = self._user_requests[user_id]
        self._user_requests[user_id] = [t for t in requests if t > cutoff]
        self._user_requests[user_id].append(now)

        request_count = len(self._user_requests[user_id])

        if request_count >= FLOOD_BAN_THRESHOLD:
            self._banned_users[user_id] = now + FLOOD_BAN_DURATION
            logger.warning(
                "FLOOD_BAN user=%s requests=%d ban_duration=%ds",
                user_id, request_count, FLOOD_BAN_DURATION,
            )
            try:
                from app.core.metrics import get_metrics
                get_metrics().flood_bans.inc()
            except Exception:
                pass
            return True

        max_requests = START_RATE_LIMIT_MAX if is_start else RATE_LIMIT_MAX
        return request_count > max_requests

    # ── Middleware entry point ─────────────────────────────────────────

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

        if user_id:
            redis = await self._get_redis()
            if redis:
                limited = await self._is_rate_limited_redis(user_id, is_start)
            else:
                limited = self._is_rate_limited_memory(user_id, is_start)

            if limited:
                logger.warning(
                    "RATE_LIMITED user=%s is_start=%s", user_id, is_start
                )
                # Record metrics
                try:
                    from app.core.metrics import get_metrics
                    get_metrics().rate_limit_hits.inc()
                    get_metrics().requests_rate_limited.inc()
                except Exception:
                    pass
                return

        return await handler(event, data)
