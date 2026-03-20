"""
Rate limiting for human & bot safety.

STEP 6 — PRODUCTION HARDENING & OPERATIONAL READINESS:
F3. RATE LIMITING (HUMAN & BOT SAFETY)

This module provides rate limiting with Redis backend (persistent,
distributed) and in-memory fallback (TokenBucket).

IMPORTANT:
- Soft fail (message shown, NO exceptions)
- NO bans
- Configurable limits
- Handlers only (services untouched)
- Redis: sliding window via sorted sets (survives restarts)
- Memory: token bucket fallback when Redis unavailable
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)

# Redis key prefix for action-specific rate limiting
_REDIS_ACTION_PREFIX = "rl:action:"


@dataclass
class RateLimitConfig:
    """
    Configuration for a rate limit.

    Attributes:
        action_key: Action identifier (e.g., "admin_action", "payment_init", "trial_activate")
        max_requests: Maximum requests per window
        window_seconds: Time window in seconds
    """
    action_key: str
    max_requests: int
    window_seconds: int


# Default rate limit configurations
DEFAULT_RATE_LIMITS = {
    "admin_action": RateLimitConfig("admin_action", max_requests=10, window_seconds=60),
    "payment_init": RateLimitConfig("payment_init", max_requests=5, window_seconds=60),
    "trial_activate": RateLimitConfig("trial_activate", max_requests=1, window_seconds=3600),  # Once per hour
    "vpn_reissue": RateLimitConfig("vpn_reissue", max_requests=3, window_seconds=300),  # 3 per 5 minutes
    "vpn_regenerate": RateLimitConfig("vpn_regenerate", max_requests=2, window_seconds=300),  # 2 per 5 minutes
}


class TokenBucket:
    """
    Simple token bucket rate limiter.

    Lock-free: all operations are non-blocking.  In single-threaded asyncio
    only one coroutine executes at a time (between awaits), so no lock is needed.
    """

    __slots__ = ("max_tokens", "refill_rate", "tokens", "last_refill")

    def __init__(self, max_tokens: int, refill_rate: float):
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.tokens = float(max_tokens)
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(
                self.max_tokens,
                self.tokens + elapsed * self.refill_rate,
            )
            self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def get_remaining(self) -> float:
        self._refill()
        return self.tokens


class RateLimiter:
    """
    Rate limiter for human & bot safety.

    Uses Redis sorted sets when available (persistent, distributed).
    Falls back to in-memory TokenBucket when Redis is unavailable.
    """

    # Evict buckets unused for longer than this (seconds)
    _EVICTION_INTERVAL = 300  # 5 minutes between cleanups
    _BUCKET_MAX_AGE = 600  # evict buckets idle for 10 minutes
    # Re-check Redis availability every 60 seconds after failure
    _REDIS_RETRY_INTERVAL = 60

    def __init__(self):
        self._buckets: Dict[Tuple[int, str], TokenBucket] = {}
        self._configs = DEFAULT_RATE_LIMITS.copy()
        self._last_eviction = time.monotonic()
        # Redis state
        self._redis = None
        self._redis_checked = False
        self._last_redis_attempt = 0.0

    async def _get_redis(self):
        """Lazy-load Redis client. Retries periodically if unavailable."""
        now = time.monotonic()
        if not self._redis_checked or (
            self._redis is None
            and now - self._last_redis_attempt > self._REDIS_RETRY_INTERVAL
        ):
            self._redis_checked = True
            self._last_redis_attempt = now
            try:
                from app.utils.redis_client import get_redis
                self._redis = await get_redis()
                if self._redis:
                    logger.info("ACTION_RATE_LIMIT using Redis backend")
            except Exception as e:
                logger.warning("ACTION_RATE_LIMIT Redis init failed: %s", e)
                self._redis = None
        return self._redis

    async def _check_redis(
        self, telegram_id: int, action_key: str, cfg: RateLimitConfig
    ) -> Tuple[bool, Optional[str]]:
        """Check rate limit via Redis sorted set (sliding window)."""
        redis = self._redis
        now = time.time()
        key = f"{_REDIS_ACTION_PREFIX}{action_key}:{telegram_id}"
        cutoff = now - cfg.window_seconds

        try:
            pipe = redis.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zadd(key, {str(now): now})
            pipe.zcard(key)
            pipe.expire(key, cfg.window_seconds * 2)
            results = await pipe.execute()
            count = results[2]
        except Exception as e:
            logger.warning("ACTION_RATE_LIMIT Redis error: %s", e)
            # Fallback to in-memory for this request
            return self._check_memory(telegram_id, action_key, cfg)

        if count <= cfg.max_requests:
            return True, None

        wait_seconds = cfg.window_seconds
        logger.warning(
            "[RATE_LIMIT] exceeded: user=%s action=%s count=%d limit=%s/%ss",
            telegram_id, action_key, count, cfg.max_requests, cfg.window_seconds,
        )
        return False, f"Слишком много запросов. Попробуйте через {wait_seconds} секунд."

    def _check_memory(
        self, telegram_id: int, action_key: str, cfg: RateLimitConfig
    ) -> Tuple[bool, Optional[str]]:
        """In-memory TokenBucket fallback."""
        # Periodic eviction of stale buckets
        now = time.monotonic()
        if now - self._last_eviction > self._EVICTION_INTERVAL:
            self._last_eviction = now
            stale_keys = [
                k for k, b in self._buckets.items()
                if now - b.last_refill > self._BUCKET_MAX_AGE
            ]
            for k in stale_keys:
                del self._buckets[k]
            if stale_keys:
                logger.debug(
                    "RATE_LIMIT_EVICTION evicted=%d remaining=%d",
                    len(stale_keys), len(self._buckets),
                )

        key = (telegram_id, action_key)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(
                max_tokens=cfg.max_requests,
                refill_rate=cfg.max_requests / cfg.window_seconds,
            )
            self._buckets[key] = bucket

        if bucket.consume(1):
            return True, None

        wait_seconds = (
            max(1, int(1.0 / bucket.refill_rate))
            if bucket.refill_rate > 0
            else cfg.window_seconds
        )
        logger.warning(
            "[RATE_LIMIT] exceeded: user=%s action=%s limit=%s/%ss wait=%ss",
            telegram_id, action_key, cfg.max_requests, cfg.window_seconds, wait_seconds,
        )
        return False, f"Слишком много запросов. Попробуйте через {wait_seconds} секунд."

    async def check_rate_limit_async(
        self,
        telegram_id: int,
        action_key: str,
        custom_config: Optional[RateLimitConfig] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check rate limit (async, Redis-first with in-memory fallback).

        Returns (is_allowed, error_message).
        Soft fail: returns False with message, NO exceptions.
        """
        cfg = custom_config or self._configs.get(action_key)
        if not cfg:
            return True, None

        redis = await self._get_redis()
        if redis:
            return await self._check_redis(telegram_id, action_key, cfg)
        return self._check_memory(telegram_id, action_key, cfg)

    def check_rate_limit(
        self,
        telegram_id: int,
        action_key: str,
        custom_config: Optional[RateLimitConfig] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check rate limit (sync, in-memory only).

        Kept for backwards compatibility with sync callers.
        Prefer check_rate_limit_async() for Redis support.
        """
        cfg = custom_config or self._configs.get(action_key)
        if not cfg:
            return True, None
        return self._check_memory(telegram_id, action_key, cfg)

    def get_status(self, telegram_id: int, action_key: str) -> Dict[str, Any]:
        key = (telegram_id, action_key)
        bucket = self._buckets.get(key)
        config = self._configs.get(action_key)

        if not bucket or not config:
            return {"action": action_key, "limited": False, "remaining": None, "limit": None}

        return {
            "action": action_key,
            "limited": True,
            "remaining": int(bucket.get_remaining()),
            "limit": config.max_requests,
            "window_seconds": config.window_seconds,
        }


# Global singleton — no lock needed in single-threaded asyncio
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def check_rate_limit(telegram_id: int, action_key: str) -> Tuple[bool, Optional[str]]:
    return get_rate_limiter().check_rate_limit(telegram_id, action_key)


async def check_rate_limit_async(telegram_id: int, action_key: str) -> Tuple[bool, Optional[str]]:
    """Async version with Redis support. Prefer this in async handlers."""
    return await get_rate_limiter().check_rate_limit_async(telegram_id, action_key)
