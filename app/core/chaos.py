"""
Chaos engineering and failure injection for testing system resilience.

This module provides safe failure injection capabilities for testing
system behavior under failure conditions.

IMPORTANT:
- Only enabled in dev/staging environments
- Feature-flag protected
- NEVER enabled in production by default
- All injections are reversible
"""

import logging
import asyncio
from typing import Optional, Callable, Any
from datetime import datetime, timedelta, timezone
from enum import Enum

import config

logger = logging.getLogger(__name__)


class FailureType(str, Enum):
    """Types of failures that can be injected"""
    DB_UNAVAILABLE = "db_unavailable"
    VPN_API_TIMEOUT = "vpn_api_timeout"
    PAYMENT_FAILURE = "payment_failure"
    DB_SLOW = "db_slow"


class ChaosEngine:
    """
    Chaos engineering engine for failure injection.
    
    Provides controlled failure injection for testing system resilience
    without affecting production systems.
    """
    
    def __init__(self):
        """Initialize chaos engine"""
        self._enabled = False
        self._active_failures: dict[str, dict] = {}  # failure_id -> failure_config
        self._failure_callbacks: dict[FailureType, Callable] = {}
    
    def is_enabled(self) -> bool:
        """
        Check if chaos engineering is enabled.
        
        Returns:
            True if enabled (dev/staging only), False otherwise
        """
        # Only enable in non-production environments
        if config.APP_ENV == "prod":
            return False
        
        # Check feature flag
        chaos_enabled = config.env("CHAOS_ENABLED", default="false").lower() == "true"
        return chaos_enabled and self._enabled
    
    def enable(self) -> None:
        """Enable chaos engineering (only in dev/staging)"""
        if config.APP_ENV == "prod":
            logger.warning("Chaos engineering cannot be enabled in production")
            return
        
        self._enabled = True
        logger.info("Chaos engineering enabled (dev/staging only)")
    
    def disable(self) -> None:
        """Disable chaos engineering"""
        self._enabled = False
        # Clear all active failures
        self._active_failures.clear()
        logger.info("Chaos engineering disabled")
    
    def inject_db_unavailable(
        self,
        duration_seconds: float = 300.0,
        failure_id: Optional[str] = None
    ) -> str:
        """
        Simulate database unavailable failure.
        
        Args:
            duration_seconds: Duration of failure in seconds (default: 5 minutes)
            failure_id: Optional failure ID (auto-generated if not provided)
            
        Returns:
            Failure ID for later removal
        """
        if not self.is_enabled():
            logger.warning("Chaos engineering not enabled - failure injection ignored")
            return ""
        
        failure_id = failure_id or f"db_unavailable_{datetime.now(timezone.utc).timestamp()}"
        
        self._active_failures[failure_id] = {
            "type": FailureType.DB_UNAVAILABLE,
            "started_at": datetime.now(timezone.utc),
            "duration_seconds": duration_seconds,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=duration_seconds),
        }
        
        logger.warning(
            f"[CHAOS] Injected DB unavailable failure: {failure_id} "
            f"(duration: {duration_seconds}s)"
        )
        
        # Schedule automatic removal
        asyncio.create_task(self._remove_failure_after_duration(failure_id, duration_seconds))
        
        return failure_id
    
    def inject_vpn_api_timeout(
        self,
        duration_seconds: float = 60.0,
        failure_id: Optional[str] = None
    ) -> str:
        """
        Simulate VPN API timeout failure.
        
        Args:
            duration_seconds: Duration of failure in seconds (default: 1 minute)
            failure_id: Optional failure ID (auto-generated if not provided)
            
        Returns:
            Failure ID for later removal
        """
        if not self.is_enabled():
            logger.warning("Chaos engineering not enabled - failure injection ignored")
            return ""
        
        failure_id = failure_id or f"vpn_api_timeout_{datetime.now(timezone.utc).timestamp()}"
        
        self._active_failures[failure_id] = {
            "type": FailureType.VPN_API_TIMEOUT,
            "started_at": datetime.now(timezone.utc),
            "duration_seconds": duration_seconds,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=duration_seconds),
        }
        
        logger.warning(
            f"[CHAOS] Injected VPN API timeout failure: {failure_id} "
            f"(duration: {duration_seconds}s)"
        )
        
        # Schedule automatic removal
        asyncio.create_task(self._remove_failure_after_duration(failure_id, duration_seconds))
        
        return failure_id
    
    def inject_payment_failure(
        self,
        duration_seconds: float = 120.0,
        failure_id: Optional[str] = None
    ) -> str:
        """
        Simulate payment provider failure.
        
        Args:
            duration_seconds: Duration of failure in seconds (default: 2 minutes)
            failure_id: Optional failure ID (auto-generated if not provided)
            
        Returns:
            Failure ID for later removal
        """
        if not self.is_enabled():
            logger.warning("Chaos engineering not enabled - failure injection ignored")
            return ""
        
        failure_id = failure_id or f"payment_failure_{datetime.now(timezone.utc).timestamp()}"
        
        self._active_failures[failure_id] = {
            "type": FailureType.PAYMENT_FAILURE,
            "started_at": datetime.now(timezone.utc),
            "duration_seconds": duration_seconds,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=duration_seconds),
        }
        
        logger.warning(
            f"[CHAOS] Injected payment failure: {failure_id} "
            f"(duration: {duration_seconds}s)"
        )
        
        # Schedule automatic removal
        asyncio.create_task(self._remove_failure_after_duration(failure_id, duration_seconds))
        
        return failure_id
    
    def remove_failure(self, failure_id: str) -> bool:
        """
        Remove an active failure injection.
        
        Args:
            failure_id: Failure ID to remove
            
        Returns:
            True if failure was removed, False if not found
        """
        if failure_id in self._active_failures:
            failure = self._active_failures.pop(failure_id)
            logger.info(
                f"[CHAOS] Removed failure injection: {failure_id} "
                f"(type: {failure['type']})"
            )
            return True
        return False
    
    def is_failure_active(self, failure_type: FailureType) -> bool:
        """
        Check if a specific failure type is currently active.
        
        Args:
            failure_type: Type of failure to check
            
        Returns:
            True if failure is active, False otherwise
        """
        if not self.is_enabled():
            return False
        
        now = datetime.now(timezone.utc)
        for failure in self._active_failures.values():
            if failure["type"] == failure_type and failure["expires_at"] > now:
                return True
        return False
    
    def get_active_failures(self) -> dict:
        """Get all active failures"""
        if not self.is_enabled():
            return {}
        
        now = datetime.now(timezone.utc)
        # Filter out expired failures
        active = {
            fid: failure
            for fid, failure in self._active_failures.items()
            if failure["expires_at"] > now
        }
        # Update internal state
        self._active_failures = active
        return active
    
    async def _remove_failure_after_duration(self, failure_id: str, duration_seconds: float) -> None:
        """Remove failure after specified duration"""
        await asyncio.sleep(duration_seconds)
        self.remove_failure(failure_id)
        logger.info(f"[CHAOS] Auto-removed failure injection: {failure_id}")


# Global singleton instance
_chaos_engine: Optional[ChaosEngine] = None


def get_chaos_engine() -> ChaosEngine:
    """
    Get or create global chaos engine instance.
    
    Returns:
        Global ChaosEngine instance
    """
    global _chaos_engine
    
    if _chaos_engine is None:
        _chaos_engine = ChaosEngine()
    
    return _chaos_engine


# Helper functions for checking failures in code
def should_simulate_db_unavailable() -> bool:
    """
    Check if DB unavailable failure should be simulated.
    
    Returns:
        True if failure should be simulated, False otherwise
    """
    chaos = get_chaos_engine()
    return chaos.is_failure_active(FailureType.DB_UNAVAILABLE)


def should_simulate_vpn_api_timeout() -> bool:
    """
    Check if VPN API timeout failure should be simulated.
    
    Returns:
        True if failure should be simulated, False otherwise
    """
    chaos = get_chaos_engine()
    return chaos.is_failure_active(FailureType.VPN_API_TIMEOUT)


def should_simulate_payment_failure() -> bool:
    """
    Check if payment failure should be simulated.
    
    Returns:
        True if failure should be simulated, False otherwise
    """
    chaos = get_chaos_engine()
    return chaos.is_failure_active(FailureType.PAYMENT_FAILURE)
