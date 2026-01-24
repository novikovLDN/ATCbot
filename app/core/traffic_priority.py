"""
Traffic priority for load shedding and degradation.

This module provides traffic prioritization for graceful degradation
under load or during failures.

IMPORTANT:
- Priorities are for load shedding only
- They do NOT affect business logic
- Priorities are observable and explicit
- No silent throttling
"""

from enum import Enum
from typing import Optional, Dict
from dataclasses import dataclass


class TrafficPriority(str, Enum):
    """Traffic priority levels"""
    CRITICAL = "critical"  # Payments finalization, always on
    HIGH = "high"  # Subscription activation, protected
    NORMAL = "normal"  # UI handlers, throttled under load
    LOW = "low"  # Analytics, retries, disabled under load


@dataclass
class PriorityConfig:
    """
    Configuration for traffic priority.
    
    Defines behavior under degradation.
    """
    priority: TrafficPriority
    enabled_under_degradation: bool  # Is this priority enabled during degradation
    enabled_under_unavailable: bool  # Is this priority enabled during unavailability
    max_concurrent: Optional[int] = None  # Optional max concurrent operations


class TrafficPriorityManager:
    """
    Manager for traffic prioritization.
    
    Provides priority-aware load shedding and degradation.
    """
    
    def __init__(self):
        """Initialize traffic priority manager"""
        self._priorities: Dict[TrafficPriority, PriorityConfig] = {
            TrafficPriority.CRITICAL: PriorityConfig(
                priority=TrafficPriority.CRITICAL,
                enabled_under_degradation=True,
                enabled_under_unavailable=True,
                max_concurrent=None,  # No limit
            ),
            TrafficPriority.HIGH: PriorityConfig(
                priority=TrafficPriority.HIGH,
                enabled_under_degradation=True,
                enabled_under_unavailable=False,
                max_concurrent=50,
            ),
            TrafficPriority.NORMAL: PriorityConfig(
                priority=TrafficPriority.NORMAL,
                enabled_under_degradation=True,
                enabled_under_unavailable=False,
                max_concurrent=100,
            ),
            TrafficPriority.LOW: PriorityConfig(
                priority=TrafficPriority.LOW,
                enabled_under_degradation=False,
                enabled_under_unavailable=False,
                max_concurrent=None,
            ),
        }
    
    def is_enabled(
        self,
        priority: TrafficPriority,
        is_degraded: bool = False,
        is_unavailable: bool = False
    ) -> bool:
        """
        Check if priority is enabled under current conditions.
        
        Args:
            priority: Traffic priority
            is_degraded: Whether system is degraded
            is_unavailable: Whether system is unavailable
            
        Returns:
            True if enabled, False if disabled
        """
        config = self._priorities.get(priority)
        if not config:
            return True  # Default: enabled
        
        if is_unavailable:
            return config.enabled_under_unavailable
        if is_degraded:
            return config.enabled_under_degradation
        
        return True  # Normal operation: all enabled
    
    def get_priority_config(self, priority: TrafficPriority) -> PriorityConfig:
        """
        Get priority configuration.
        
        Args:
            priority: Traffic priority
            
        Returns:
            PriorityConfig for the priority
        """
        return self._priorities.get(
            priority,
            PriorityConfig(
                priority=priority,
                enabled_under_degradation=True,
                enabled_under_unavailable=False,
            )
        )


# Global singleton instance
_traffic_priority_manager: Optional[TrafficPriorityManager] = None


def get_traffic_priority_manager() -> TrafficPriorityManager:
    """
    Get or create global traffic priority manager instance.
    
    Returns:
        Global TrafficPriorityManager instance
    """
    global _traffic_priority_manager
    
    if _traffic_priority_manager is None:
        _traffic_priority_manager = TrafficPriorityManager()
    
    return _traffic_priority_manager


def is_priority_enabled(
    priority: TrafficPriority,
    is_degraded: bool = False,
    is_unavailable: bool = False
) -> bool:
    """
    Check if priority is enabled (convenience function).
    
    Args:
        priority: Traffic priority
        is_degraded: Whether system is degraded
        is_unavailable: Whether system is unavailable
        
    Returns:
        True if enabled, False if disabled
    """
    return get_traffic_priority_manager().is_enabled(
        priority, is_degraded, is_unavailable
    )
