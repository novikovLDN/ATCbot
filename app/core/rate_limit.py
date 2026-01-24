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
import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple, Optional
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
    
    STEP 6 — F3: RATE LIMITING
    Implements token bucket algorithm for rate limiting.
    """
    
    def __init__(self, max_tokens: int, refill_rate: float):
        """
        Initialize token bucket.
        
        Args:
            max_tokens: Maximum tokens in bucket
            refill_rate: Tokens refilled per second
        """
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.tokens = float(max_tokens)
        self.last_refill = time.time()
        self._lock = threading.Lock()
    
    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from bucket.
        
        Args:
            tokens: Number of tokens to consume (default: 1)
            
        Returns:
            True if tokens consumed, False if insufficient tokens
        """
        with self._lock:
            now = time.time()
            elapsed = now - self.last_refill
            
            # Refill tokens
            self.tokens = min(
                self.max_tokens,
                self.tokens + elapsed * self.refill_rate
            )
            self.last_refill = now
            
            # Check if enough tokens
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            return False
    
    def get_remaining(self) -> float:
        """Get remaining tokens in bucket"""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_refill
            
            # Refill tokens
            self.tokens = min(
                self.max_tokens,
                self.tokens + elapsed * self.refill_rate
            )
            self.last_refill = now
            
            return self.tokens


class RateLimiter:
    """
    Rate limiter for human & bot safety.
    
    STEP 6 — F3: RATE LIMITING
    Provides per-user, per-action rate limiting.
    """
    
    def __init__(self):
        """Initialize rate limiter"""
        self._buckets: Dict[Tuple[int, str], TokenBucket] = {}
        self._lock = threading.Lock()
        self._configs = DEFAULT_RATE_LIMITS.copy()
    
    def check_rate_limit(
        self,
        telegram_id: int,
        action_key: str,
        custom_config: Optional[RateLimitConfig] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if action is within rate limit.
        
        STEP 6 — F3: RATE LIMITING
        Returns (is_allowed, error_message).
        Soft fail: returns False with message, NO exceptions.
        
        Args:
            telegram_id: Telegram ID of the user
            action_key: Action identifier
            custom_config: Optional custom rate limit config
            
        Returns:
            Tuple of (is_allowed, error_message)
            - is_allowed: True if within limit, False if exceeded
            - error_message: Human-readable message if limit exceeded
        """
        with self._lock:
            # Get config
            config = custom_config or self._configs.get(action_key)
            if not config:
                # No rate limit for this action
                return True, None
            
            # Get or create bucket
            key = (telegram_id, action_key)
            if key not in self._buckets:
                # Create bucket with max_tokens = max_requests, refill_rate = max_requests / window_seconds
                self._buckets[key] = TokenBucket(
                    max_tokens=config.max_requests,
                    refill_rate=config.max_requests / config.window_seconds
                )
            
            bucket = self._buckets[key]
            
            # Try to consume token
            if bucket.consume(1):
                return True, None
            else:
                remaining = bucket.get_remaining()
                wait_seconds = int((1.0 - remaining) / bucket.refill_rate) if bucket.refill_rate > 0 else config.window_seconds
                
                logger.warning(
                    f"[RATE_LIMIT] Rate limit exceeded: user={telegram_id}, action={action_key}, "
                    f"limit={config.max_requests}/{config.window_seconds}s, wait={wait_seconds}s"
                )
                
                return False, f"Слишком много запросов. Попробуйте через {wait_seconds} секунд."
    
    def get_status(self, telegram_id: int, action_key: str) -> Dict[str, any]:
        """
        Get rate limit status for user and action.
        
        Args:
            telegram_id: Telegram ID of the user
            action_key: Action identifier
            
        Returns:
            Dictionary with rate limit status
        """
        with self._lock:
            key = (telegram_id, action_key)
            bucket = self._buckets.get(key)
            config = self._configs.get(action_key)
            
            if not bucket or not config:
                return {
                    "action": action_key,
                    "limited": False,
                    "remaining": None,
                    "limit": None,
                }
            
            return {
                "action": action_key,
                "limited": True,
                "remaining": int(bucket.get_remaining()),
                "limit": config.max_requests,
                "window_seconds": config.window_seconds,
            }


# Global singleton instance
_rate_limiter: Optional[RateLimiter] = None
_rate_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """
    Get or create global rate limiter instance.
    
    STEP 6 — F3: RATE LIMITING
    Returns singleton RateLimiter instance.
    
    Returns:
        RateLimiter instance
    """
    global _rate_limiter
    
    with _rate_limiter_lock:
        if _rate_limiter is None:
            _rate_limiter = RateLimiter()
        
        return _rate_limiter


def check_rate_limit(telegram_id: int, action_key: str) -> Tuple[bool, Optional[str]]:
    """
    Check rate limit (convenience function).
    
    STEP 6 — F3: RATE LIMITING
    Convenience function for checking rate limits.
    
    Args:
        telegram_id: Telegram ID of the user
        action_key: Action identifier
        
    Returns:
        Tuple of (is_allowed, error_message)
    """
    return get_rate_limiter().check_rate_limit(telegram_id, action_key)
