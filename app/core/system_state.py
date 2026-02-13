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
from typing import Optional, Dict, Any, Set


class ComponentStatus(Enum):
    """Status of a system component."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class SystemSeverity(Enum):
    """
    PART A.1: System severity level for admin dashboard.
    
    Severity levels:
    - GREEN: All critical components healthy, no issues
    - YELLOW: System degraded (optional components degraded)
    - RED: System unavailable (critical components unhealthy)
    """
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


# PART A.1: Explicitly define component criticality
# CRITICAL components: system cannot function without them
# OPTIONAL components: system can function with reduced capabilities
CRITICAL_COMPONENTS: Set[str] = {"database", "payments"}
OPTIONAL_COMPONENTS: Set[str] = {"vpn_api", "analytics", "notifications"}


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
        Check if system is healthy.
        
        PART B.2: System is HEALTHY if:
        - All CRITICAL components are HEALTHY
        - OPTIONAL components can be DEGRADED (system still healthy)
        
        Returns:
            True if all CRITICAL components are HEALTHY
        """
        # Check CRITICAL components only
        critical_components = {
            "database": self.database,
            "payments": self.payments,
        }
        return all(
            comp.status == ComponentStatus.HEALTHY
            for comp in critical_components.values()
        )
    
    @property
    def is_degraded(self) -> bool:
        """
        Check if system is in degraded mode.
        
        PART B.2: System is DEGRADED if:
        - All CRITICAL components are HEALTHY
        - At least one OPTIONAL component is DEGRADED
        - No component is UNAVAILABLE
        
        Returns:
            True if system is DEGRADED (optional components degraded, critical healthy)
        """
        # PART B.2: If ANY critical component is UNHEALTHY → system_state = UNAVAILABLE (not DEGRADED)
        critical_components = {
            "database": self.database,
            "payments": self.payments,
        }
        # If any critical component is not HEALTHY, system is not DEGRADED (it's UNAVAILABLE)
        if not all(comp.status == ComponentStatus.HEALTHY for comp in critical_components.values()):
            return False
        
        # PART B.2: Else if ANY optional component is DEGRADED → system_state = DEGRADED
        optional_components = {
            "vpn_api": self.vpn_api,
        }
        has_optional_degraded = any(
            comp.status == ComponentStatus.DEGRADED
            for comp in optional_components.values()
        )
        
        # PART B.2: No component must be UNAVAILABLE
        has_unavailable = any(
            comp.status == ComponentStatus.UNAVAILABLE
            for comp in [self.database, self.vpn_api, self.payments]
        )
        
        return has_optional_degraded and not has_unavailable
    
    def get_severity(self, pending_activations: int = 0) -> SystemSeverity:
        """
        PART A.2: Calculate system severity based on component states.
        
        Rules:
        - RED: system_state == UNAVAILABLE OR database != healthy OR payments != healthy
        - YELLOW: system_state == DEGRADED OR vpn_api degraded OR pending_activations > 0
        - GREEN: all critical components healthy AND no pending activations
        
        Args:
            pending_activations: Number of pending activations (default: 0)
        
        Returns:
            SystemSeverity enum value
        """
        # PART A.2: RED severity
        if self.is_unavailable:
            return SystemSeverity.RED
        if self.database.status != ComponentStatus.HEALTHY:
            return SystemSeverity.RED
        if self.payments.status != ComponentStatus.HEALTHY:
            return SystemSeverity.RED
        
        # PART A.2: YELLOW severity
        if self.is_degraded:
            return SystemSeverity.YELLOW
        if self.vpn_api.status == ComponentStatus.DEGRADED:
            return SystemSeverity.YELLOW
        if pending_activations > 0:
            return SystemSeverity.YELLOW
        
        # PART A.2: GREEN severity
        return SystemSeverity.GREEN
    
    def get_error_summary(self) -> list:
        """
        PART B.4: Get compact error summary with only actionable issues.
        
        Returns:
            List of error dicts with keys: component, reason, impact
        """
        errors = []
        
        # Check critical components
        if self.database.status != ComponentStatus.HEALTHY:
            errors.append({
                "component": "database",
                "reason": self.database.error or "Database unhealthy",
                "impact": "CRITICAL: System cannot process requests"
            })
        
        if self.payments.status != ComponentStatus.HEALTHY:
            errors.append({
                "component": "payments",
                "reason": self.payments.error or "Payments unhealthy",
                "impact": "CRITICAL: Payment processing unavailable"
            })
        
        # Check optional components (only if critical are healthy)
        if self.database.status == ComponentStatus.HEALTHY and self.payments.status == ComponentStatus.HEALTHY:
            if self.vpn_api.status == ComponentStatus.DEGRADED:
                errors.append({
                    "component": "vpn_api",
                    "reason": self.vpn_api.error or "VPN API degraded",
                    "impact": "VPN provisioning disabled, activations pending"
                })
        
        return errors
    
    @property
    def is_unavailable(self) -> bool:
        """
        Check if system is unavailable.
        
        PART B.2: System is UNAVAILABLE if:
        - ANY CRITICAL component is UNHEALTHY (DEGRADED or UNAVAILABLE)
        
        Returns:
            True if any CRITICAL component is not HEALTHY
        """
        # PART B.2: If ANY critical component is UNHEALTHY → system_state = UNAVAILABLE
        critical_components = {
            "database": self.database,
            "payments": self.payments,
        }
        return any(
            comp.status != ComponentStatus.HEALTHY
            for comp in critical_components.values()
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


def recalculate_from_runtime() -> SystemState:
    """
    PART B.2 / PART C.3: Recalculate SystemState from current runtime state.
    
    Called after:
    - init_db() success
    - retry success
    - on startup if DB_READY=True
    
    PART C.3: Expected state with missing XRAY_API:
    - database = healthy
    - payments = healthy
    - vpn_api = degraded
    - system_state = DEGRADED (NOT UNAVAILABLE)
    
    NOTE: Now async because VPN API health-check is async.
    
    Returns:
        SystemState reflecting current runtime health
    """
    from datetime import datetime, timezone
    import config
    
    now = datetime.now(timezone.utc)
    
    # Database: healthy if DB_READY=True
    try:
        import database
        if database.DB_READY:
            db_component = healthy_component(last_checked_at=now)
        else:
            db_component = unavailable_component(
                error="Database not ready (DB_READY=False)",
                last_checked_at=now
            )
    except Exception as e:
        db_component = unavailable_component(
            error=f"Database check failed: {e}",
            last_checked_at=now
        )
    
    # VPN API: проверка конфигурации (sync fallback)
    # Реальный health-check выполняется в healthcheck.py (async)
    try:
        if not config.XRAY_API_URL or not config.XRAY_API_KEY:
            vpn_component = degraded_component(
                error="XRAY_API_URL or XRAY_API_KEY not configured (non-critical)",
                last_checked_at=now
            )
        else:
            # Конфигурация есть - считаем healthy (реальный health-check в healthcheck.py)
            vpn_component = healthy_component(last_checked_at=now)
    except Exception as e:
        vpn_component = degraded_component(
            error=f"VPN API check failed: {e}",
            last_checked_at=now
        )
    
    # Payments: always healthy
    payments_component = healthy_component(last_checked_at=now)
    
    return SystemState(
        database=db_component,
        vpn_api=vpn_component,
        payments=payments_component,
    )
