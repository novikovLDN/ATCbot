"""
Centralized system state abstraction for health and degradation status.

This module provides a pure, passive data model for representing the health
status of core infrastructure components (database, VPN API, payments).

Characteristics:
- Pure state + computation only
- No side effects
- No logging
- No async code
- No config/environment access
- Typed and deterministic

STEP 1.1 - RUNTIME GUARDRAILS:
- SystemState is a READ-ONLY snapshot of system health
- SystemState is constructed centrally (healthcheck / health_server)
- SystemState is NEVER mutated by runtime code
- SystemState is used for awareness only, NOT for control flow
- Handlers read SystemState but do NOT block based on it
- Workers read SystemState at iteration start to decide skip/continue
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class ComponentStatus(Enum):
    """Status of a system component."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ComponentState:
    """
    State of a single system component.
    
    Attributes:
        status: Current status of the component
        last_checked_at: Timestamp of last health check (None if never checked)
        error: Error message if component is not healthy (None if healthy)
    """
    status: ComponentStatus
    last_checked_at: Optional[datetime] = None
    error: Optional[str] = None
    
    def __post_init__(self) -> None:
        """Validate component state consistency."""
        if self.status == ComponentStatus.HEALTHY and self.error is not None:
            raise ValueError("HEALTHY component cannot have an error")
        if self.status != ComponentStatus.HEALTHY and self.error is None:
            raise ValueError("Non-HEALTHY component must have an error")


@dataclass(frozen=True)
class SystemState:
    """
    Global system state representing health of all core components.
    
    Attributes:
        database: State of database component
        vpn_api: State of VPN API component
        payments: State of payments component
    
    Computed properties:
        is_healthy: True if ALL components are HEALTHY
        is_degraded: True if at least one component is DEGRADED but none UNAVAILABLE
        is_unavailable: True if any component is UNAVAILABLE
    """
    database: ComponentState
    vpn_api: ComponentState
    payments: ComponentState
    
    @property
    def is_healthy(self) -> bool:
        """
        Check if all components are healthy.
        
        Returns:
            True if all components have status HEALTHY
        """
        return all(
            component.status == ComponentStatus.HEALTHY
            for component in [self.database, self.vpn_api, self.payments]
        )
    
    @property
    def is_degraded(self) -> bool:
        """
        Check if system is in degraded mode.
        
        Returns:
            True if at least one component is DEGRADED but none are UNAVAILABLE
        """
        has_degraded = any(
            component.status == ComponentStatus.DEGRADED
            for component in [self.database, self.vpn_api, self.payments]
        )
        has_unavailable = any(
            component.status == ComponentStatus.UNAVAILABLE
            for component in [self.database, self.vpn_api, self.payments]
        )
        return has_degraded and not has_unavailable
    
    @property
    def is_unavailable(self) -> bool:
        """
        Check if any component is unavailable.
        
        Returns:
            True if any component has status UNAVAILABLE
        """
        return any(
            component.status == ComponentStatus.UNAVAILABLE
            for component in [self.database, self.vpn_api, self.payments]
        )
    
    def summary(self) -> Dict[str, Any]:
        """
        Generate a summary dictionary of system state.
        
        Returns:
            Dictionary with component statuses and global state:
            {
                "database": {"status": "healthy", "error": None, "last_checked_at": ...},
                "vpn_api": {"status": "healthy", "error": None, "last_checked_at": ...},
                "payments": {"status": "healthy", "error": None, "last_checked_at": ...},
                "global": {
                    "is_healthy": True,
                    "is_degraded": False,
                    "is_unavailable": False
                }
            }
        """
        return {
            "database": {
                "status": self.database.status.value,
                "error": self.database.error,
                "last_checked_at": (
                    self.database.last_checked_at.isoformat()
                    if self.database.last_checked_at else None
                ),
            },
            "vpn_api": {
                "status": self.vpn_api.status.value,
                "error": self.vpn_api.error,
                "last_checked_at": (
                    self.vpn_api.last_checked_at.isoformat()
                    if self.vpn_api.last_checked_at else None
                ),
            },
            "payments": {
                "status": self.payments.status.value,
                "error": self.payments.error,
                "last_checked_at": (
                    self.payments.last_checked_at.isoformat()
                    if self.payments.last_checked_at else None
                ),
            },
            "global": {
                "is_healthy": self.is_healthy,
                "is_degraded": self.is_degraded,
                "is_unavailable": self.is_unavailable,
            },
        }


# Factory helpers for creating component states

def healthy_component(last_checked_at: Optional[datetime] = None) -> ComponentState:
    """
    Create a healthy component state.
    
    Args:
        last_checked_at: Optional timestamp of last health check
    
    Returns:
        ComponentState with status HEALTHY
    """
    return ComponentState(
        status=ComponentStatus.HEALTHY,
        last_checked_at=last_checked_at,
        error=None,
    )


def degraded_component(error: str, last_checked_at: Optional[datetime] = None) -> ComponentState:
    """
    Create a degraded component state.
    
    Args:
        error: Error message describing the degradation
        last_checked_at: Optional timestamp of last health check
    
    Returns:
        ComponentState with status DEGRADED
    
    Raises:
        ValueError: If error is empty or None
    """
    if not error:
        raise ValueError("Degraded component must have a non-empty error message")
    return ComponentState(
        status=ComponentStatus.DEGRADED,
        last_checked_at=last_checked_at,
        error=error,
    )


def unavailable_component(error: str, last_checked_at: Optional[datetime] = None) -> ComponentState:
    """
    Create an unavailable component state.
    
    Args:
        error: Error message describing why component is unavailable
        last_checked_at: Optional timestamp of last health check
    
    Returns:
        ComponentState with status UNAVAILABLE
    
    Raises:
        ValueError: If error is empty or None
    """
    if not error:
        raise ValueError("Unavailable component must have a non-empty error message")
    return ComponentState(
        status=ComponentStatus.UNAVAILABLE,
        last_checked_at=last_checked_at,
        error=error,
    )


# Default constructor

def create_default_system_state() -> SystemState:
    """
    Create a default system state with all components healthy.
    
    Returns:
        SystemState with all components in HEALTHY status
    """
    return SystemState(
        database=healthy_component(),
        vpn_api=healthy_component(),
        payments=healthy_component(),
    )
