"""
Bulkheads for fault isolation.

This module provides bulkhead isolation for different system components
to prevent cascading failures.

IMPORTANT:
- Bulkheads are for fault isolation only
- They do NOT affect business logic
- They are for observability and protection
"""

from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict
import threading


class BulkheadName(str, Enum):
    """Bulkhead names for fault isolation"""
    PAYMENTS = "payments"
    VPN = "vpn"
    BACKGROUND_WORKERS = "background_workers"
    ADMIN_OPERATIONS = "admin_operations"
    USER_TRAFFIC = "user_traffic"


@dataclass
class BulkheadConfig:
    """
    Configuration for a bulkhead.
    
    Defines limits and isolation for a component.
    """
    name: BulkheadName
    max_concurrent: int  # Max concurrent operations
    max_retries: int  # Max retries per operation
    timeout_seconds: float  # Operation timeout
    circuit_breaker_enabled: bool  # Enable circuit breaker


class Bulkhead:
    """
    Bulkhead for fault isolation.
    
    Tracks concurrent operations and enforces limits.
    """
    
    def __init__(self, config: BulkheadConfig):
        """Initialize bulkhead"""
        self.config = config
        self._lock = threading.Lock()
        self._active_operations = 0
        self._failed_operations = 0
        self._last_failure_time: Optional[datetime] = None
    
    def acquire(self) -> bool:
        """
        Acquire bulkhead slot.
        
        Returns:
            True if slot acquired, False if limit reached
        """
        with self._lock:
            if self._active_operations >= self.config.max_concurrent:
                return False
            self._active_operations += 1
            return True
    
    def release(self) -> None:
        """Release bulkhead slot"""
        with self._lock:
            if self._active_operations > 0:
                self._active_operations -= 1
    
    def record_failure(self) -> None:
        """Record operation failure"""
        with self._lock:
            self._failed_operations += 1
            self._last_failure_time = datetime.utcnow()
    
    def record_success(self) -> None:
        """Record operation success"""
        with self._lock:
            # Reset failure count on success (simple strategy)
            if self._failed_operations > 0:
                self._failed_operations = max(0, self._failed_operations - 1)
    
    def get_status(self) -> dict:
        """
        Get bulkhead status.
        
        Returns:
            Dictionary with bulkhead status
        """
        with self._lock:
            return {
                "name": self.config.name.value,
                "active_operations": self._active_operations,
                "max_concurrent": self.config.max_concurrent,
                "failed_operations": self._failed_operations,
                "last_failure_time": self._last_failure_time.isoformat() if self._last_failure_time else None,
                "utilization_percent": (self._active_operations / self.config.max_concurrent * 100) if self.config.max_concurrent > 0 else 0.0,
            }


class BulkheadRegistry:
    """
    Registry of bulkheads for fault isolation.
    
    Manages bulkheads for different system components.
    """
    
    def __init__(self):
        """Initialize bulkhead registry"""
        self._bulkheads: Dict[BulkheadName, Bulkhead] = {}
        self._lock = threading.Lock()
        self._initialize_default_bulkheads()
    
    def _initialize_default_bulkheads(self) -> None:
        """Initialize default bulkhead configurations"""
        default_configs = {
            BulkheadName.PAYMENTS: BulkheadConfig(
                name=BulkheadName.PAYMENTS,
                max_concurrent=10,
                max_retries=2,
                timeout_seconds=30.0,
                circuit_breaker_enabled=True,
            ),
            BulkheadName.VPN: BulkheadConfig(
                name=BulkheadName.VPN,
                max_concurrent=20,
                max_retries=2,
                timeout_seconds=10.0,
                circuit_breaker_enabled=True,
            ),
            BulkheadName.BACKGROUND_WORKERS: BulkheadConfig(
                name=BulkheadName.BACKGROUND_WORKERS,
                max_concurrent=5,
                max_retries=1,
                timeout_seconds=60.0,
                circuit_breaker_enabled=False,
            ),
            BulkheadName.ADMIN_OPERATIONS: BulkheadConfig(
                name=BulkheadName.ADMIN_OPERATIONS,
                max_concurrent=3,
                max_retries=1,
                timeout_seconds=15.0,
                circuit_breaker_enabled=False,
            ),
            BulkheadName.USER_TRAFFIC: BulkheadConfig(
                name=BulkheadName.USER_TRAFFIC,
                max_concurrent=100,
                max_retries=2,
                timeout_seconds=5.0,
                circuit_breaker_enabled=False,
            ),
        }
        
        for name, config in default_configs.items():
            self._bulkheads[name] = Bulkhead(config)
    
    def get_bulkhead(self, name: BulkheadName) -> Bulkhead:
        """
        Get bulkhead by name.
        
        Args:
            name: Bulkhead name
            
        Returns:
            Bulkhead instance
        """
        with self._lock:
            return self._bulkheads.get(name, self._bulkheads[BulkheadName.USER_TRAFFIC])
    
    def get_all_bulkheads(self) -> Dict[BulkheadName, Bulkhead]:
        """Get all bulkheads"""
        with self._lock:
            return dict(self._bulkheads)
    
    def get_all_status(self) -> dict:
        """
        Get status of all bulkheads.
        
        Returns:
            Dictionary mapping bulkhead name to status
        """
        with self._lock:
            return {
                name.value: bulkhead.get_status()
                for name, bulkhead in self._bulkheads.items()
            }


# Global singleton instance
_bulkhead_registry: Optional[BulkheadRegistry] = None


def get_bulkhead_registry() -> BulkheadRegistry:
    """
    Get or create global bulkhead registry instance.
    
    Returns:
        Global BulkheadRegistry instance
    """
    global _bulkhead_registry
    
    if _bulkhead_registry is None:
        _bulkhead_registry = BulkheadRegistry()
    
    return _bulkhead_registry


def get_bulkhead(name: BulkheadName) -> Bulkhead:
    """
    Get bulkhead by name (convenience function).
    
    Args:
        name: Bulkhead name
        
    Returns:
        Bulkhead instance
    """
    return get_bulkhead_registry().get_bulkhead(name)
