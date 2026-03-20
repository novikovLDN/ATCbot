"""
Shared Redis client for the application.

Provides a singleton Redis connection pool used by:
- FSM storage (aiogram RedisStorage)
- Rate limiting (global + action-specific)
- IP abuse protection (webhook auth failures)
- Health checks

The client is lazy-initialized on first access and properly
cleaned up during shutdown.
"""
import logging
from typing import Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis

import config

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None

# Connection pool limits — prevents connection exhaustion under load
_MAX_CONNECTIONS = 20
_SOCKET_CONNECT_TIMEOUT = 5.0
_SOCKET_TIMEOUT = 5.0


def is_configured() -> bool:
    """Check if Redis URL is set in config."""
    return bool(config.REDIS_URL)


def _requires_ssl(url: str) -> bool:
    """Check if Redis URL uses TLS (rediss:// scheme)."""
    return urlparse(url).scheme == "rediss"


async def get_redis() -> Optional[aioredis.Redis]:
    """
    Get or create the shared Redis client (singleton).

    Returns None if REDIS_URL is not configured.

    Connection hardening:
    - max_connections pool limit prevents connection exhaustion
    - SSL/TLS auto-detected from rediss:// scheme
    - Socket timeouts prevent hanging on network issues
    - retry_on_timeout for transient network failures
    """
    global _redis_client

    if not is_configured():
        return None

    if _redis_client is None:
        connection_kwargs = {
            "decode_responses": True,
            "socket_connect_timeout": _SOCKET_CONNECT_TIMEOUT,
            "socket_timeout": _SOCKET_TIMEOUT,
            "retry_on_timeout": True,
            "max_connections": _MAX_CONNECTIONS,
        }

        # Auto-enable SSL for rediss:// URLs
        if _requires_ssl(config.REDIS_URL):
            import ssl
            ssl_context = ssl.create_default_context()
            # Railway / managed Redis providers use valid certs
            connection_kwargs["ssl"] = ssl_context

        _redis_client = aioredis.from_url(
            config.REDIS_URL,
            **connection_kwargs,
        )
        logger.info(
            "Redis client created (max_connections=%d ssl=%s)",
            _MAX_CONNECTIONS, _requires_ssl(config.REDIS_URL),
        )

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


async def info_stats() -> Optional[dict]:
    """
    Get Redis server info for health monitoring.

    Returns dict with key metrics or None on failure.
    """
    if not is_configured():
        return None

    try:
        client = await get_redis()
        if client is None:
            return None
        info = await client.info(section="memory")
        clients_info = await client.info(section="clients")
        return {
            "used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 1),
            "used_memory_peak_mb": round(info.get("used_memory_peak", 0) / 1024 / 1024, 1),
            "connected_clients": clients_info.get("connected_clients", 0),
            "blocked_clients": clients_info.get("blocked_clients", 0),
        }
    except Exception as e:
        logger.debug("Redis info_stats failed: %s", e)
        return None


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
