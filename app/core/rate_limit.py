"""
Async-safe rate limiting (token bucket) for human & bot safety.

This module is used by handlers via ``check_rate_limit()``. All locks are
``asyncio.Lock`` — no ``threading.Lock`` — because the bot is single-threaded
under asyncio. Using a thread lock here previously could stall the event loop
when the bucket map grew under flood (audit finding 2026-05).

Properties:
- Soft fail (returns ``(False, message)``, never raises).
- Bucket map is bounded (``MAX_TRACKED_KEYS``); LRU eviction on overflow.
- Stale buckets (untouched > 2 windows) are pruned periodically.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    action_key: str
    max_requests: int
    window_seconds: int


DEFAULT_RATE_LIMITS: Dict[str, RateLimitConfig] = {
    "admin_action":   RateLimitConfig("admin_action",   max_requests=10, window_seconds=60),
    "payment_init":   RateLimitConfig("payment_init",   max_requests=5,  window_seconds=60),
    "trial_activate": RateLimitConfig("trial_activate", max_requests=1,  window_seconds=3600),
    "vpn_reissue":    RateLimitConfig("vpn_reissue",    max_requests=3,  window_seconds=300),
    "vpn_regenerate": RateLimitConfig("vpn_regenerate", max_requests=2,  window_seconds=300),
}

# Memory cap. Each bucket ≈ ~200 bytes — 50K = ~10 MB worst-case.
MAX_TRACKED_KEYS = 50_000


class TokenBucket:
    """Token bucket with monotonic-time refill."""

    __slots__ = ("max_tokens", "refill_rate", "tokens", "last_refill", "last_touch")

    def __init__(self, max_tokens: int, refill_rate: float):
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.tokens = float(max_tokens)
        now = time.monotonic()
        self.last_refill = now
        self.last_touch = now

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now
        self.last_touch = now

    def consume(self, tokens: int = 1) -> bool:
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def remaining(self) -> float:
        self._refill()
        return self.tokens


class RateLimiter:
    """Per-(user, action) async rate limiter. All buckets share one asyncio.Lock."""

    def __init__(self):
        # OrderedDict gives O(1) LRU eviction.
        self._buckets: "OrderedDict[Tuple[int, str], TokenBucket]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._configs: Dict[str, RateLimitConfig] = DEFAULT_RATE_LIMITS.copy()

    async def check_rate_limit(
        self,
        telegram_id: int,
        action_key: str,
        custom_config: Optional[RateLimitConfig] = None,
    ) -> Tuple[bool, Optional[str]]:
        cfg = custom_config or self._configs.get(action_key)
        if not cfg:
            return True, None

        async with self._lock:
            key = (telegram_id, action_key)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(
                    max_tokens=cfg.max_requests,
                    refill_rate=cfg.max_requests / cfg.window_seconds,
                )
                self._buckets[key] = bucket
                self._evict_if_needed()
            else:
                # Touch for LRU.
                self._buckets.move_to_end(key)

            if bucket.consume(1):
                return True, None

            wait = (
                max(1, int(1.0 / bucket.refill_rate))
                if bucket.refill_rate > 0
                else cfg.window_seconds
            )
            logger.warning(
                "RATE_LIMIT_EXCEEDED user=%s action=%s limit=%s/%ss wait=%ss",
                telegram_id, action_key, cfg.max_requests, cfg.window_seconds, wait,
            )
            try:
                from app.core import metrics as _metrics
                _metrics.counter(_metrics.M.RATE_LIMIT_HIT_TOTAL).inc(
                    labels={"action": action_key},
                )
            except Exception:
                pass
            return False, f"Слишком много запросов. Попробуйте через {wait} секунд."

    def _evict_if_needed(self) -> None:
        # Caller holds self._lock.
        while len(self._buckets) > MAX_TRACKED_KEYS:
            self._buckets.popitem(last=False)

    async def prune_stale(self, max_age_seconds: float = 7200.0) -> int:
        """Remove buckets untouched longer than ``max_age_seconds``. Returns count pruned."""
        cutoff = time.monotonic() - max_age_seconds
        async with self._lock:
            stale = [k for k, b in self._buckets.items() if b.last_touch < cutoff]
            for k in stale:
                self._buckets.pop(k, None)
            return len(stale)

    async def get_status(self, telegram_id: int, action_key: str) -> Dict[str, Any]:
        cfg = self._configs.get(action_key)
        async with self._lock:
            bucket = self._buckets.get((telegram_id, action_key))
            if not bucket or not cfg:
                return {"action": action_key, "limited": False, "remaining": None, "limit": None}
            return {
                "action": action_key,
                "limited": True,
                "remaining": int(bucket.remaining()),
                "limit": cfg.max_requests,
                "window_seconds": cfg.window_seconds,
            }

    async def snapshot(self) -> Dict[str, int]:
        """Diagnostic: bucket count and approximate exhausted-bucket count."""
        async with self._lock:
            total = len(self._buckets)
            exhausted = sum(1 for b in self._buckets.values() if b.remaining() < 1.0)
            return {"buckets_total": total, "buckets_exhausted": exhausted}


_rate_limiter: Optional[RateLimiter] = None
_rate_limiter_init_lock = asyncio.Lock()


def get_rate_limiter() -> RateLimiter:
    """Synchronous singleton accessor — safe in asyncio because only assignment is mutated."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


async def check_rate_limit(telegram_id: int, action_key: str) -> Tuple[bool, Optional[str]]:
    return await get_rate_limiter().check_rate_limit(telegram_id, action_key)
