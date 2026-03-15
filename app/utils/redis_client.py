"""
Shared Redis client for the application.

Provides a singleton Redis connection pool used by:
- FSM storage (aiogram RedisStorage)
- Rate limiting
- Health checks

The client is lazy-initialized on first access and properly
cleaned up during shutdown.
"""
import logging
from typing import Optional

import redis.asyncio as aioredis

import config

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None


def is_configured() -> bool:
    """Check if Redis URL is set in config."""
    return bool(config.REDIS_URL)


async def get_redis() -> Optional[aioredis.Redis]:
    """
    Get or create the shared Redis client (singleton).

    Returns None if REDIS_URL is not configured.
    """
    global _redis_client

    if not is_configured():
        return None

    if _redis_client is None:
        _redis_client = aioredis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5.0,
            socket_timeout=5.0,
            retry_on_timeout=True,
        )
        logger.info("Redis client created")

    return _redis_client


async def ping() -> bool:
    """
    Check Redis connectivity.

    Returns True if PING succeeds, False otherwise.
    Returns True (healthy) if Redis is not configured (optional dependency).
    """
    if not is_configured():
        return True  # Not configured = not a failure

    try:
        client = await get_redis()
        if client is None:
            return False
        result = await client.ping()
        return result is True
    except Exception as e:
        logger.warning("Redis ping failed: %s", e)
        return False


async def close() -> None:
    """Close the Redis client connection pool."""
    global _redis_client

    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("Redis client closed")
        except Exception as e:
            logger.warning("Redis close error: %s", e)
        finally:
            _redis_client = None
