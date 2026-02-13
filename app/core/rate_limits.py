"""
Global rate limiting for traffic management.

This module provides rate limiting at multiple levels to protect
against traffic spikes, DDoS-like patterns, and abuse.

IMPORTANT:
- Rate limits are for protection only
- They do NOT affect business logic
- Limits are observable and configurable
- No silent throttling
"""

from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import threading


class RateLimitScope(str, Enum):
    """Scopes for rate limiting"""
    PER_USER = "per_user"
    PER_IP = "per_ip"
    PER_ENDPOINT = "per_endpoint"
    PER_REGION = "per_region"
    PER_SERVICE = "per_service"
    GLOBAL = "global"


@dataclass
class RateLimitConfig:
    """
    Configuration for a rate limit.
    
    Defines limits and window for rate limiting.
    """
    scope: RateLimitScope
    identifier: str  # User ID, IP, endpoint, etc.
    max_requests: int  # Max requests per window
    window_seconds: int  # Time window in seconds


class RateLimiter:
    """
    Rate limiter for traffic management.
    
    Tracks requests and enforces rate limits.
    """
    
    def __init__(self):
        """Initialize rate limiter"""
        self._lock = threading.Lock()
        # Track requests: (scope, identifier) -> list of timestamps
        self._requests: Dict[tuple, list] = defaultdict(list)
        # Cleanup old entries periodically
        self._last_cleanup = datetime.now(timezone.utc)
        self._cleanup_interval = timedelta(seconds=300)  # 5 minutes
    
    def check_rate_limit(
        self,
        scope: RateLimitScope,
        identifier: str,
        max_requests: int,
        window_seconds: int
    ) -> bool:
        """
        Check if request is within rate limit.
        
        Args:
            scope: Rate limit scope
            identifier: Identifier (user ID, IP, endpoint, etc.)
            max_requests: Max requests per window
            window_seconds: Time window in seconds
            
        Returns:
            True if within limit, False if limit exceeded
        """
        with self._lock:
            # Cleanup old entries periodically
            self._cleanup_old_entries()
            
            key = (scope, identifier)
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(seconds=window_seconds)
            
            # Filter requests within window
            self._requests[key] = [
                ts for ts in self._requests[key]
                if ts >= cutoff
            ]
            
            # Check if limit exceeded
            if len(self._requests[key]) >= max_requests:
                return False
            
            # Record request
            self._requests[key].append(now)
            return True
    
    def _cleanup_old_entries(self) -> None:
        """Cleanup old rate limit entries"""
        now = datetime.now(timezone.utc)
        if (now - self._last_cleanup) < self._cleanup_interval:
            return
        
        # Remove entries older than 1 hour
        cutoff = now - timedelta(hours=1)
        keys_to_remove = []
        
        for key, timestamps in self._requests.items():
            filtered = [ts for ts in timestamps if ts >= cutoff]
            if not filtered:
                keys_to_remove.append(key)
            else:
                self._requests[key] = filtered
        
        for key in keys_to_remove:
            del self._requests[key]
        
        self._last_cleanup = now
    
    def get_rate_limit_status(
        self,
        scope: RateLimitScope,
        identifier: str,
        window_seconds: int
    ) -> dict:
        """
        Get rate limit status for identifier.
        
        Args:
            scope: Rate limit scope
            identifier: Identifier
            window_seconds: Time window in seconds
            
        Returns:
            Dictionary with rate limit status
        """
        with self._lock:
            key = (scope, identifier)
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(seconds=window_seconds)
            
            requests_in_window = [
                ts for ts in self._requests.get(key, [])
                if ts >= cutoff
            ]
            
            return {
                "scope": scope.value,
                "identifier": identifier,
                "requests_in_window": len(requests_in_window),
                "window_seconds": window_seconds,
            }


class RateLimitRegistry:
    """
    Registry of rate limit configurations.
    
    Manages rate limits for different scopes and identifiers.
    """
    
    def __init__(self):
        """Initialize rate limit registry"""
        self._limiter = RateLimiter()
        self._configs: Dict[tuple, RateLimitConfig] = {}
        self._lock = threading.Lock()
        self._initialize_default_limits()
    
    def _initialize_default_limits(self) -> None:
        """Initialize default rate limit configurations"""
        # Per-user limits
        # Per-IP limits
        # Per-endpoint limits
        # Per-region limits
        # Per-service limits
        # Global limits
        # These are defined in documentation, not hardcoded here
        pass
    
    def check_limit(
        self,
        scope: RateLimitScope,
        identifier: str,
        max_requests: Optional[int] = None,
        window_seconds: Optional[int] = None
    ) -> bool:
        """
        Check rate limit for scope and identifier.
        
        Args:
            scope: Rate limit scope
            identifier: Identifier
            max_requests: Optional max requests (uses default if not provided)
            window_seconds: Optional window seconds (uses default if not provided)
            
        Returns:
            True if within limit, False if limit exceeded
        """
        # Default limits (can be overridden)
        if max_requests is None:
            max_requests = self._get_default_max_requests(scope)
        if window_seconds is None:
            window_seconds = self._get_default_window_seconds(scope)
        
        return self._limiter.check_rate_limit(
            scope, identifier, max_requests, window_seconds
        )
    
    def _get_default_max_requests(self, scope: RateLimitScope) -> int:
        """Get default max requests for scope"""
        defaults = {
            RateLimitScope.PER_USER: 100,  # 100 requests per window
            RateLimitScope.PER_IP: 1000,  # 1000 requests per window
            RateLimitScope.PER_ENDPOINT: 10000,  # 10000 requests per window
            RateLimitScope.PER_REGION: 100000,  # 100000 requests per window
            RateLimitScope.PER_SERVICE: 5000,  # 5000 requests per window
            RateLimitScope.GLOBAL: 1000000,  # 1M requests per window
        }
        return defaults.get(scope, 1000)
    
    def _get_default_window_seconds(self, scope: RateLimitScope) -> int:
        """Get default window seconds for scope"""
        defaults = {
            RateLimitScope.PER_USER: 60,  # 1 minute
            RateLimitScope.PER_IP: 60,  # 1 minute
            RateLimitScope.PER_ENDPOINT: 60,  # 1 minute
            RateLimitScope.PER_REGION: 60,  # 1 minute
            RateLimitScope.PER_SERVICE: 60,  # 1 minute
            RateLimitScope.GLOBAL: 60,  # 1 minute
        }
        return defaults.get(scope, 60)


# Global singleton instance
_rate_limit_registry: Optional[RateLimitRegistry] = None


def get_rate_limit_registry() -> RateLimitRegistry:
    """
    Get or create global rate limit registry instance.
    
    Returns:
        Global RateLimitRegistry instance
    """
    global _rate_limit_registry
    
    if _rate_limit_registry is None:
        _rate_limit_registry = RateLimitRegistry()
    
    return _rate_limit_registry


def check_rate_limit(
    scope: RateLimitScope,
    identifier: str,
    max_requests: Optional[int] = None,
    window_seconds: Optional[int] = None
) -> bool:
    """
    Check rate limit (convenience function).
    
    Args:
        scope: Rate limit scope
        identifier: Identifier
        max_requests: Optional max requests
        window_seconds: Optional window seconds
        
    Returns:
        True if within limit, False if limit exceeded
    """
    return get_rate_limit_registry().check_limit(
        scope, identifier, max_requests, window_seconds
    )
