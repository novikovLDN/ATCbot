"""
Redis Distributed Idempotency Module

Production-grade distributed idempotency keys using Redis.
Prevents duplicate execution of operations across restarts and multiple instances.

INFRASTRUCTURE ONLY - No business logic changes.
"""
import logging
import os
from typing import Optional
import redis.asyncio as redis
import config

logger = logging.getLogger(__name__)


class RedisIdempotency:
    """
    Production-grade Redis distributed idempotency.
    
    Uses Redis SET NX EX pattern for atomic idempotency key acquisition.
    Prevents duplicate execution of operations across restarts and multiple instances.
    
    Features:
    - Atomic key acquisition (SET NX EX)
    - Automatic TTL expiration
    - Fail-safe: allows execution if Redis unavailable
    - Structured logging
    
    Example:
        idempotency = RedisIdempotency(
            redis_client=redis_client,
            environment="stage",
            default_ttl_seconds=86400
        )
        if await idempotency.acquire("balance_topup", "payment_123"):
            # Execute operation
        else:
            # Already processed - skip
    """
    
    def __init__(
        self,
        redis_client: Optional[redis.Redis],
        environment: str,
        default_ttl_seconds: int = 86400,  # 24 hours default
    ):
        """
        Initialize Redis idempotency.
        
        Args:
            redis_client: Redis client instance (can be None for fail-safe)
            environment: Environment name (e.g., "stage", "prod")
            default_ttl_seconds: Default TTL for idempotency keys (default: 24 hours)
        """
        self.redis_client = redis_client
        self.environment = environment
        self.default_ttl_seconds = default_ttl_seconds
        self.instance_id = os.getenv("INSTANCE_ID", f"pid-{os.getpid()}")
        self.pid = os.getpid()
    
    async def acquire(
        self,
        operation: str,
        unique_id: str,
        ttl: Optional[int] = None,
        correlation_id: Optional[str] = None,
        telegram_id: Optional[int] = None,
    ) -> bool:
        """
        Acquire idempotency key (atomic operation).
        
        Args:
            operation: Operation identifier (e.g., "balance_topup", "purchase")
            unique_id: Unique identifier for this operation (e.g., payment_id, purchase_id)
            ttl: Optional TTL in seconds (defaults to default_ttl_seconds)
            correlation_id: Optional correlation ID for logging
            telegram_id: Optional Telegram ID for logging
        
        Returns:
            True if key acquired (operation should proceed)
            False if key already exists (operation already processed - skip)
        """
        # Get Redis client async (may be None)
        redis_client_instance = self.redis_client
        if redis_client_instance is None:
            try:
                import redis_client as rc_module
                redis_client_instance = await rc_module.get_redis_client()
            except Exception:
                pass
        
        # Fail-safe: if Redis unavailable, allow execution
        if not redis_client_instance:
            logger.debug(
                "IDEMPOTENCY_GRANTED",
                extra={
                    "component": "infra",
                    "operation": "idempotency_acquire",
                    "outcome": "granted",
                    "reason": "redis_unavailable_fail_safe",
                    "operation_type": operation,
                    "unique_id": unique_id,
                    "correlation_id": correlation_id,
                    "telegram_id": telegram_id,
                }
            )
            return True
        
        try:
            # Build Redis key: idempotency:{environment}:{operation}:{unique_id}
            redis_key = f"idempotency:{self.environment}:{operation}:{unique_id}"
            
            # Use TTL parameter or default
            ttl_seconds = ttl if ttl is not None else self.default_ttl_seconds
            
            # SET key value NX EX ttl_seconds
            # NX = only set if not exists
            # EX = set expiration in seconds
            result = await redis_client_instance.set(
                redis_key,
                "1",  # Value doesn't matter, we only check existence
                nx=True,  # Only set if not exists
                ex=ttl_seconds,  # Expiration in seconds
            )
            
            if result:
                # Key acquired successfully - operation should proceed
                logger.info(
                    "IDEMPOTENCY_GRANTED",
                    extra={
                        "component": "infra",
                        "operation": "idempotency_acquire",
                        "outcome": "granted",
                        "operation_type": operation,
                        "unique_id": unique_id,
                        "ttl_seconds": ttl_seconds,
                        "correlation_id": correlation_id,
                        "telegram_id": telegram_id,
                        "instance_id": self.instance_id,
                        "pid": self.pid,
                    }
                )
                return True
            else:
                # Key already exists - operation already processed
                logger.warning(
                    "IDEMPOTENCY_DUPLICATE",
                    extra={
                        "component": "infra",
                        "operation": "idempotency_acquire",
                        "outcome": "duplicate",
                        "operation_type": operation,
                        "unique_id": unique_id,
                        "correlation_id": correlation_id,
                        "telegram_id": telegram_id,
                        "instance_id": self.instance_id,
                        "pid": self.pid,
                    }
                )
                return False
                
        except Exception as e:
            # Redis error - fail-safe: allow execution
            logger.error(
                "IDEMPOTENCY_ERROR",
                extra={
                    "component": "infra",
                    "operation": "idempotency_acquire",
                    "outcome": "error",
                    "reason": str(e)[:100],
                    "operation_type": operation,
                    "unique_id": unique_id,
                    "correlation_id": correlation_id,
                    "telegram_id": telegram_id,
                    "instance_id": self.instance_id,
                    "pid": self.pid,
                }
            )
            # Fail-safe: allow execution if Redis unavailable
            return True


def create_idempotency() -> RedisIdempotency:
    """
    Create Redis idempotency instance.
    
    Returns:
        RedisIdempotency instance (Redis client fetched async in acquire())
    """
    return RedisIdempotency(
        redis_client=None,  # Will be fetched async in acquire()
        environment=config.APP_ENV,
        default_ttl_seconds=86400,  # 24 hours default
    )
