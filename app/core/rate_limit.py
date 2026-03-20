"""
Rate limiting for human & bot safety (in-memory token bucket).

STEP 6 — PRODUCTION HARDENING & OPERATIONAL READINESS:
F3. RATE LIMITING (HUMAN & BOT SAFETY)

This module provides simple in-memory token bucket rate limiting
for protecting against abuse and mistakes.

IMPORTANT:
- Soft fail (message shown, NO exceptions)
- NO bans
- Configurable limits
- Handlers only (services untouched)
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, Any
from collections import defaultdict

logger = logging.getLogger(__name__)


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

    Lock-free: designed for single-threaded asyncio event loop.
    """

    # Evict buckets unused for longer than this (seconds)
    _EVICTION_INTERVAL = 300  # 5 minutes between cleanups
    _BUCKET_MAX_AGE = 600  # evict buckets idle for 10 minutes

    def __init__(self):
        self._buckets: Dict[Tuple[int, str], TokenBucket] = {}
        self._configs = DEFAULT_RATE_LIMITS.copy()
        self._last_eviction = time.monotonic()

    def check_rate_limit(
        self,
        telegram_id: int,
        action_key: str,
        custom_config: Optional[RateLimitConfig] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if action is within rate limit.

        Returns (is_allowed, error_message).
        Soft fail: returns False with message, NO exceptions.
        """
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
                logger.debug("RATE_LIMIT_EVICTION evicted=%d remaining=%d", len(stale_keys), len(self._buckets))

        # Get config
        config = custom_config or self._configs.get(action_key)
        if not config:
            return True, None

        # Get or create bucket
        key = (telegram_id, action_key)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(
                max_tokens=config.max_requests,
                refill_rate=config.max_requests / config.window_seconds,
            )
            self._buckets[key] = bucket

        if bucket.consume(1):
            return True, None

        wait_seconds = max(1, int(1.0 / bucket.refill_rate)) if bucket.refill_rate > 0 else config.window_seconds
        logger.warning(
            "[RATE_LIMIT] exceeded: user=%s action=%s limit=%s/%ss wait=%ss",
            telegram_id, action_key, config.max_requests, config.window_seconds, wait_seconds,
        )
        return False, f"Слишком много запросов. Попробуйте через {wait_seconds} секунд."

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
