"""
IP-level abuse protection using Redis.

Tracks failed webhook authentication attempts per IP address.
After BLOCK_THRESHOLD failures within BLOCK_WINDOW, the IP is
temporarily blocked for BLOCK_DURATION seconds.

Redis keys:
- ip:fail:{ip}  — sorted set of failure timestamps (sliding window)
- ip:block:{ip} — flag key with TTL (blocked IP)

Falls back to no-op when Redis is unavailable (fail-open for
availability, since webhook HMAC validation is the primary guard).
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Block IP after this many auth failures within the window
BLOCK_THRESHOLD = 10
# Sliding window for counting failures (seconds)
BLOCK_WINDOW = 300  # 5 minutes
# How long to block the IP (seconds)
BLOCK_DURATION = 600  # 10 minutes

_REDIS_FAIL_PREFIX = "ip:fail:"
_REDIS_BLOCK_PREFIX = "ip:block:"


async def is_ip_blocked(ip: str) -> bool:
    """
    Check if an IP is currently blocked.

    Returns False if Redis unavailable (fail-open).
    """
    try:
        from app.utils.redis_client import get_redis
        redis = await get_redis()
        if redis is None:
            return False
        return bool(await redis.exists(f"{_REDIS_BLOCK_PREFIX}{ip}"))
    except Exception as e:
        logger.debug("ip_abuse check error: %s", e)
        return False


async def record_auth_failure(ip: str) -> bool:
    """
    Record a failed authentication attempt from an IP.

    Returns True if the IP was just blocked (threshold exceeded).
    Returns False otherwise or if Redis unavailable.
    """
    try:
        from app.utils.redis_client import get_redis
        redis = await get_redis()
        if redis is None:
            return False

        now = time.time()
        fail_key = f"{_REDIS_FAIL_PREFIX}{ip}"
        block_key = f"{_REDIS_BLOCK_PREFIX}{ip}"
        cutoff = now - BLOCK_WINDOW

        pipe = redis.pipeline(transaction=True)
        pipe.zremrangebyscore(fail_key, 0, cutoff)
        pipe.zadd(fail_key, {str(now): now})
        pipe.zcard(fail_key)
        pipe.expire(fail_key, BLOCK_WINDOW * 2)
        results = await pipe.execute()

        failure_count = results[2]

        if failure_count >= BLOCK_THRESHOLD:
            await redis.setex(block_key, BLOCK_DURATION, "1")
            # Clean up failure tracking (no longer needed while blocked)
            await redis.delete(fail_key)
            logger.warning(
                "IP_BLOCKED ip=%s failures=%d duration=%ds",
                ip, failure_count, BLOCK_DURATION,
            )
            return True

        return False
    except Exception as e:
        logger.debug("ip_abuse record error: %s", e)
        return False


async def get_blocked_count() -> int:
    """Get count of currently blocked IPs (for metrics)."""
    try:
        from app.utils.redis_client import get_redis
        redis = await get_redis()
        if redis is None:
            return 0
        # SCAN for blocked keys — safe for production (non-blocking iterator)
        count = 0
        async for _ in redis.scan_iter(f"{_REDIS_BLOCK_PREFIX}*", count=100):
            count += 1
        return count
    except Exception:
        return 0
