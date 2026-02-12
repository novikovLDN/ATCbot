"""
Redis Distributed Rate Limiter

Production-grade distributed rate limiting using Redis.
Safe for multi-instance deployment.

INFRASTRUCTURE ONLY - No business logic changes.
"""
import logging
import os
from typing import Optional
import redis.asyncio as redis
import config

logger = logging.getLogger(__name__)

# Lua script for atomic INCR + EXPIRE
RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RedisRateLimiter:
    """
    Production-grade Redis distributed rate limiter.
    
    Uses atomic Redis INCR + EXPIRE pattern for multi-instance safe rate limiting.
    Implements sliding window rate limiting.
    
    Features:
    - Atomic increment and expiration
    - Lua script for race condition prevention
    - Fail-safe: allows requests if Redis unavailable
    - Structured logging
    
    Example:
        limiter = RedisRateLimiter(
            redis_client=redis_client,
            prefix="rate:stage",
            max_requests=5,
            window_seconds=60
        )
        allowed = await limiter.allow("reissue:6214188086")
    """
    
    def __init__(
        self,
        redis_client: Optional[redis.Redis],
        prefix: str,
        max_requests: int,
        window_seconds: int,
    ):
        """
        Initialize Redis rate limiter.
        
        Args:
            redis_client: Redis client instance (can be None for fail-safe)
            prefix: Key prefix (e.g., "rate:stage")
            max_requests: Maximum requests per window
            window_seconds: Time window in seconds
        """
        self.redis_client = redis_client
        self.prefix = prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.instance_id = os.getenv("INSTANCE_ID", f"pid-{os.getpid()}")
        self.pid = os.getpid()
        
        # Compile Lua script once for efficiency
        self._rate_limit_script = None
    
    async def _get_rate_limit_script(self):
        """Get compiled Lua script for atomic rate limiting"""
        if self._rate_limit_script is None and self.redis_client:
            self._rate_limit_script = self.redis_client.register_script(RATE_LIMIT_SCRIPT)
        return self._rate_limit_script
    
    async def allow(
        self,
        key: str,
        correlation_id: Optional[str] = None,
        telegram_id: Optional[int] = None,
    ) -> bool:
        """
        Check if request is within rate limit.
        
        Args:
            key: Rate limit key (e.g., "reissue:6214188086")
            correlation_id: Optional correlation ID for logging
            telegram_id: Optional Telegram ID for logging
        
        Returns:
            True if request allowed, False if rate limit exceeded
        """
        # Get Redis client async (may be None)
        redis_client_instance = self.redis_client
        if redis_client_instance is None:
            try:
                import redis_client as rc_module
                redis_client_instance = await rc_module.get_redis_client()
            except Exception:
                pass
        
        # Fail-safe: if Redis unavailable, allow request
        if not redis_client_instance:
            logger.debug(
                "RATE_LIMIT_ALLOWED",
                extra={
                    "component": "infra",
                    "operation": "rate_limit_check",
                    "outcome": "allowed",
                    "reason": "redis_unavailable_fail_safe",
                    "key": key,
                    "prefix": self.prefix,
                    "correlation_id": correlation_id,
                    "telegram_id": telegram_id,
                }
            )
            return True
        
        try:
            # Build Redis key: prefix:key
            redis_key = f"{self.prefix}:{key}"
            
            # Use Lua script for atomic INCR + EXPIRE
            if not self._rate_limit_script:
                self._rate_limit_script = redis_client_instance.register_script(RATE_LIMIT_SCRIPT)
            
            rate_limit_script = self._rate_limit_script
            if not rate_limit_script:
                # Script compilation failed - fail-safe: allow request
                logger.warning(
                    "RATE_LIMIT_ERROR",
                    extra={
                        "component": "infra",
                        "operation": "rate_limit_check",
                        "outcome": "error",
                        "reason": "script_compilation_failed",
                        "key": key,
                        "correlation_id": correlation_id,
                    }
                )
                return True
            
            # Execute Lua script: INCR + EXPIRE atomically
            current_count = await rate_limit_script(
                keys=[redis_key],
                args=[self.window_seconds]
            )
            
            # Check if limit exceeded
            if current_count > self.max_requests:
                logger.warning(
                    "RATE_LIMIT_HIT",
                    extra={
                        "component": "infra",
                        "operation": "rate_limit_check",
                        "outcome": "denied",
                        "key": key,
                        "prefix": self.prefix,
                        "current_count": current_count,
                        "max_requests": self.max_requests,
                        "window_seconds": self.window_seconds,
                        "correlation_id": correlation_id,
                        "telegram_id": telegram_id,
                        "instance_id": self.instance_id,
                        "pid": self.pid,
                    }
                )
                return False
            
            # Request allowed
            logger.debug(
                "RATE_LIMIT_ALLOWED",
                extra={
                    "component": "infra",
                    "operation": "rate_limit_check",
                    "outcome": "allowed",
                    "key": key,
                    "prefix": self.prefix,
                    "current_count": current_count,
                    "max_requests": self.max_requests,
                    "window_seconds": self.window_seconds,
                    "correlation_id": correlation_id,
                    "telegram_id": telegram_id,
                    "instance_id": self.instance_id,
                    "pid": self.pid,
                }
            )
            return True
            
        except Exception as e:
            # Redis error - fail-safe: allow request
            logger.error(
                "RATE_LIMIT_ERROR",
                extra={
                    "component": "infra",
                    "operation": "rate_limit_check",
                    "outcome": "error",
                    "reason": str(e)[:100],
                    "key": key,
                    "prefix": self.prefix,
                    "correlation_id": correlation_id,
                    "telegram_id": telegram_id,
                    "instance_id": self.instance_id,
                    "pid": self.pid,
                }
            )
            # Fail-safe: allow request if Redis unavailable
            return True


def create_redis_rate_limiter(
    action: str,
    max_requests: int,
    window_seconds: int,
) -> RedisRateLimiter:
    """
    Create Redis rate limiter instance for specific action.
    
    Args:
        action: Action identifier (e.g., "reissue", "withdraw_create")
        max_requests: Maximum requests per window
        window_seconds: Time window in seconds
    
    Returns:
        RedisRateLimiter instance (Redis client fetched async in allow())
    """
    prefix = f"rate:{config.APP_ENV}:{action}"
    return RedisRateLimiter(
        redis_client=None,  # Will be fetched async in allow()
        prefix=prefix,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )
