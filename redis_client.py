"""
Redis Client Module

Async Redis client using redis.asyncio with singleton pattern.
Provides connection pool, health checks, and structured logging.

INFRASTRUCTURE ONLY - No business logic changes.
"""
import logging
from typing import Optional
import redis.asyncio as redis
import config

logger = logging.getLogger(__name__)

# Global Redis client instance (singleton)
_redis_client: Optional[redis.Redis] = None
REDIS_READY: bool = False


async def get_redis_client() -> Optional[redis.Redis]:
    """
    Get or create Redis client instance (singleton pattern).
    
    Returns:
        Redis client instance if configured, None if Redis URL not set
    
    Raises:
        RuntimeError: If Redis URL is invalid or connection fails
    """
    global _redis_client, REDIS_READY
    
    # If Redis URL not configured, return None (graceful degradation)
    if not config.REDIS_URL:
        return None
    
    # If client already exists and is ready, return it
    if _redis_client is not None and REDIS_READY:
        return _redis_client
    
    # Create new client if not exists
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(
                config.REDIS_URL,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=10
            )
            logger.info("Redis client created")
        except Exception as e:
            logger.error(f"Failed to create Redis client: {e}")
            _redis_client = None
            REDIS_READY = False
            raise RuntimeError(f"Redis client creation failed: {e}")
    
    return _redis_client


async def check_redis_connection() -> bool:
    """
    Check Redis connection health.
    
    Returns:
        True if Redis is connected and responsive, False otherwise
    
    This function does NOT raise exceptions - returns False on any error.
    """
    global REDIS_READY
    
    if not config.REDIS_URL:
        REDIS_READY = False
        return False
    
    try:
        client = await get_redis_client()
        if client is None:
            REDIS_READY = False
            return False
        
        # Test connection with PING
        result = await client.ping()
        if result:
            REDIS_READY = True
            logger.info(
                "REDIS_CONNECTED",
                extra={
                    "component": "infra",
                    "operation": "redis_health_check",
                    "outcome": "success"
                }
            )
            return True
        else:
            REDIS_READY = False
            logger.warning(
                "REDIS_CONNECTION_FAILED",
                extra={
                    "component": "infra",
                    "operation": "redis_health_check",
                    "outcome": "failed",
                    "reason": "ping_returned_false"
                }
            )
            return False
            
    except Exception as e:
        REDIS_READY = False
        logger.warning(
            "REDIS_CONNECTION_FAILED",
            extra={
                "component": "infra",
                "operation": "redis_health_check",
                "outcome": "failed",
                "reason": str(e)[:100]
            }
        )
        return False


async def close_redis_client():
    """
    Close Redis client connection pool.
    
    Safe to call multiple times - idempotent.
    """
    global _redis_client, REDIS_READY
    
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("Redis client closed")
        except Exception as e:
            logger.error(f"Error closing Redis client: {e}")
        finally:
            _redis_client = None
            REDIS_READY = False
