"""
Redis client infrastructure layer.

Provides async Redis client singleton with connection pool.
Infrastructure-only module - does not modify business logic.

STEP 1 — INFRASTRUCTURE LAYER:
- Singleton pattern for Redis client
- Connection pool management
- Health check method
- Graceful degradation if Redis unavailable
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
    Get or create Redis client singleton.
    
    Returns:
        Redis client instance if Redis is configured and available, None otherwise.
    
    Behavior:
        - Creates client on first call if REDIS_URL is configured
        - Returns None if Redis is not configured (local dev)
        - Returns None if Redis connection fails (graceful degradation)
    """
    global _redis_client, REDIS_READY
    
    # If Redis is not configured, return None (local dev mode)
    if not config.REDIS_URL:
        return None
    
    # If client already exists, return it
    if _redis_client is not None:
        return _redis_client
    
    # Create new Redis client
    try:
        _redis_client = redis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        REDIS_READY = True
        logger.info("Redis client created successfully")
        return _redis_client
    except Exception as e:
        logger.error(f"Failed to create Redis client: {e}")
        REDIS_READY = False
        return None


async def check_redis_health() -> bool:
    """
    Check Redis connectivity by sending PING command.
    
    Returns:
        True if Redis is available and responding, False otherwise.
    
    Behavior:
        - Returns False if Redis is not configured
        - Returns False if Redis connection fails
        - Logs structured message: REDIS_CONNECTED or REDIS_CONNECTION_FAILED
    """
    if not config.REDIS_URL:
        logger.debug("Redis health check skipped: REDIS_URL not configured")
        return False
    
    client = await get_redis_client()
    if client is None:
        logger.warning("REDIS_CONNECTION_FAILED reason=client_creation_failed")
        return False
    
    try:
        result = await client.ping()
        if result:
            logger.info("REDIS_CONNECTED")
            return True
        else:
            logger.warning("REDIS_CONNECTION_FAILED reason=ping_failed")
            return False
    except Exception as e:
        error_msg = str(e)[:100] if str(e) else "unknown"
        logger.warning(f"REDIS_CONNECTION_FAILED reason=ping_exception error={type(e).__name__}: {error_msg}")
        return False
    finally:
        # Note: We don't close the client here - it's a singleton
        pass


async def close_redis_client():
    """
    Close Redis client connection pool.
    
    Should be called during application shutdown.
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
