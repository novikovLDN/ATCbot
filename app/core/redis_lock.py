"""
Redis Distributed Lock Module

Production-grade distributed locking using Redis SET NX PX pattern.
Safe for multi-instance deployment with automatic TTL-based release.

INFRASTRUCTURE ONLY - No business logic changes.
"""
import logging
import uuid
import asyncio
import os
from typing import Optional
import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Lua script for atomic compare-and-delete (safe lock release)
RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class RedisDistributedLock:
    """
    Production-grade Redis distributed lock.
    
    Uses SET key value NX PX pattern with UUID token for safe release.
    Implements context manager protocol for async with support.
    
    Features:
    - Atomic lock acquisition (SET NX PX)
    - Token-based safe release (Lua script)
    - Automatic TTL release on process crash
    - Retry logic with timeout
    - Structured logging
    
    Example:
        lock = RedisDistributedLock(
            redis_client=redis_client,
            key="lock:stage:reissue:12345",
            ttl_seconds=60,
            wait_timeout=5
        )
        async with lock:
            # Critical section
            pass
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        key: str,
        ttl_seconds: int = 60,
        wait_timeout: int = 5,
    ):
        """
        Initialize Redis distributed lock.
        
        Args:
            redis_client: Redis client instance (must be connected)
            key: Redis key for the lock (e.g., "lock:stage:reissue:12345")
            ttl_seconds: Lock TTL in seconds (auto-release after this time)
            wait_timeout: Maximum time to wait for lock acquisition (seconds)
        """
        self.redis_client = redis_client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.wait_timeout = wait_timeout
        self.token: Optional[str] = None
        self.acquired = False
        self.instance_id = os.getenv("INSTANCE_ID", f"pid-{os.getpid()}")
        self.pid = os.getpid()
        
        # Compile Lua script once for efficiency
        self._release_script = None
    
    async def _get_release_script(self):
        """Get compiled Lua script for safe lock release"""
        if self._release_script is None:
            self._release_script = self.redis_client.register_script(RELEASE_SCRIPT)
        return self._release_script
    
    async def acquire(self, correlation_id: Optional[str] = None) -> bool:
        """
        Acquire distributed lock with retry logic.
        
        Args:
            correlation_id: Optional correlation ID for logging
        
        Returns:
            True if lock acquired, False if timeout
        """
        if self.acquired:
            logger.warning(
                "REDIS_LOCK_ERROR",
                extra={
                    "component": "infra",
                    "operation": "lock_acquire",
                    "outcome": "failed",
                    "reason": "lock_already_acquired",
                    "key": self.key,
                    "correlation_id": correlation_id,
                }
            )
            return False
        
        # Generate unique token for this lock acquisition
        self.token = str(uuid.uuid4())
        start_time = asyncio.get_event_loop().time()
        attempt = 0
        
        while True:
            attempt += 1
            elapsed = asyncio.get_event_loop().time() - start_time
            
            # Check timeout
            if elapsed >= self.wait_timeout:
                logger.warning(
                    "REDIS_LOCK_TIMEOUT",
                    extra={
                        "component": "infra",
                        "operation": "lock_acquire",
                        "outcome": "timeout",
                        "key": self.key,
                        "attempts": attempt,
                        "elapsed_seconds": round(elapsed, 2),
                        "wait_timeout": self.wait_timeout,
                        "correlation_id": correlation_id,
                        "instance_id": self.instance_id,
                        "pid": self.pid,
                    }
                )
                self.token = None
                return False
            
            try:
                # SET key token NX PX ttl_ms
                # NX = only set if not exists
                # PX = set expiration in milliseconds
                ttl_ms = self.ttl_seconds * 1000
                result = await self.redis_client.set(
                    self.key,
                    self.token,
                    nx=True,  # Only set if not exists
                    px=ttl_ms,  # Expiration in milliseconds
                )
                
                if result:
                    # Lock acquired successfully
                    self.acquired = True
                    logger.info(
                        "REDIS_LOCK_ACQUIRED",
                        extra={
                            "component": "infra",
                            "operation": "lock_acquire",
                            "outcome": "success",
                            "key": self.key,
                            "attempts": attempt,
                            "elapsed_seconds": round(elapsed, 2),
                            "ttl_seconds": self.ttl_seconds,
                            "correlation_id": correlation_id,
                            "instance_id": self.instance_id,
                            "pid": self.pid,
                        }
                    )
                    return True
                
                # Lock not acquired, wait before retry
                await asyncio.sleep(0.1)  # Small delay between retries
                
            except Exception as e:
                # Redis error - log and retry
                logger.error(
                    "REDIS_LOCK_ERROR",
                    extra={
                        "component": "infra",
                        "operation": "lock_acquire",
                        "outcome": "error",
                        "reason": str(e)[:100],
                        "key": self.key,
                        "attempt": attempt,
                        "correlation_id": correlation_id,
                        "instance_id": self.instance_id,
                        "pid": self.pid,
                    }
                )
                # Wait before retry on error
                await asyncio.sleep(0.2)
    
    async def release(self, correlation_id: Optional[str] = None) -> None:
        """
        Release distributed lock safely.
        
        Uses Lua script to ensure only the lock owner can release.
        Safe to call multiple times (idempotent).
        
        Args:
            correlation_id: Optional correlation ID for logging
        """
        if not self.acquired:
            # Lock not acquired, nothing to release
            return
        
        if not self.token:
            logger.warning(
                "REDIS_LOCK_ERROR",
                extra={
                    "component": "infra",
                    "operation": "lock_release",
                    "outcome": "failed",
                    "reason": "no_token",
                    "key": self.key,
                    "correlation_id": correlation_id,
                }
            )
            self.acquired = False
            return
        
        try:
            # Use Lua script for atomic compare-and-delete
            release_script = await self._get_release_script()
            result = await release_script(keys=[self.key], args=[self.token])
            
            if result:
                logger.info(
                    "REDIS_LOCK_RELEASED",
                    extra={
                        "component": "infra",
                        "operation": "lock_release",
                        "outcome": "success",
                        "key": self.key,
                        "correlation_id": correlation_id,
                        "instance_id": self.instance_id,
                        "pid": self.pid,
                    }
                )
            else:
                # Lock was already released or token mismatch
                logger.warning(
                    "REDIS_LOCK_ERROR",
                    extra={
                        "component": "infra",
                        "operation": "lock_release",
                        "outcome": "failed",
                        "reason": "token_mismatch_or_already_released",
                        "key": self.key,
                        "correlation_id": correlation_id,
                        "instance_id": self.instance_id,
                        "pid": self.pid,
                    }
                )
        except Exception as e:
            logger.error(
                "REDIS_LOCK_ERROR",
                extra={
                    "component": "infra",
                    "operation": "lock_release",
                    "outcome": "error",
                    "reason": str(e)[:100],
                    "key": self.key,
                    "correlation_id": correlation_id,
                    "instance_id": self.instance_id,
                    "pid": self.pid,
                }
            )
        finally:
            # Always mark as released locally
            self.acquired = False
            self.token = None
    
    async def __aenter__(self):
        """Async context manager entry"""
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(f"Failed to acquire Redis lock: {self.key}")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - always release lock"""
        await self.release()
        return False  # Don't suppress exceptions
