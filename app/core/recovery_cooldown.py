"""
Recovery Cooldown Module

In-memory cooldown mechanism to prevent thrashing after component recovery.
Prevents background workers from immediately overwhelming recovered services.

IMPORTANT:
- In-memory only (does not persist across restarts)
- Does NOT block handlers
- Does NOT block admin operations
- Does NOT mutate SystemState
- Pure observation and coordination
"""
from datetime import datetime, timedelta
from typing import Optional, Dict
from enum import Enum

from app.core.system_state import ComponentStatus


class ComponentName(str, Enum):
    """Component names for cooldown tracking"""
    DATABASE = "database"
    VPN_API = "vpn_api"
    PAYMENTS = "payments"


class RecoveryCooldown:
    """
    In-memory cooldown tracker for component recovery.
    
    Prevents immediate retry storms after component recovery by enforcing
    a cooldown period before allowing background operations to resume.
    """
    
    def __init__(self, cooldown_seconds: int = 60):
        """
        Initialize recovery cooldown tracker.
        
        Args:
            cooldown_seconds: Cooldown duration in seconds (default: 60)
        """
        self.cooldown_seconds = cooldown_seconds
        # In-memory state: component_name -> (last_unavailable_at, cooldown_until)
        self._cooldowns: Dict[ComponentName, tuple[Optional[datetime], Optional[datetime]]] = {
            ComponentName.DATABASE: (None, None),
            ComponentName.VPN_API: (None, None),
            ComponentName.PAYMENTS: (None, None),
        }
    
    def mark_unavailable(self, component: ComponentName, now: datetime) -> None:
        """
        Mark component as unavailable and start cooldown period.
        
        Args:
            component: Component that became unavailable
            now: Current timestamp
        """
        cooldown_until = now + timedelta(seconds=self.cooldown_seconds)
        self._cooldowns[component] = (now, cooldown_until)
    
    def is_in_cooldown(self, component: ComponentName, now: datetime) -> bool:
        """
        Check if component is in cooldown period.
        
        Args:
            component: Component to check
            now: Current timestamp
            
        Returns:
            True if component is in cooldown, False otherwise
        """
        last_unavailable_at, cooldown_until = self._cooldowns[component]
        
        if cooldown_until is None:
            return False
        
        return now < cooldown_until
    
    def get_cooldown_remaining(self, component: ComponentName, now: datetime) -> Optional[int]:
        """
        Get remaining cooldown seconds for component.
        
        Args:
            component: Component to check
            now: Current timestamp
            
        Returns:
            Remaining seconds in cooldown, or None if not in cooldown
        """
        if not self.is_in_cooldown(component, now):
            return None
        
        _, cooldown_until = self._cooldowns[component]
        if cooldown_until is None:
            return None
        
        remaining = (cooldown_until - now).total_seconds()
        return max(0, int(remaining))
    
    def clear_cooldown(self, component: ComponentName) -> None:
        """
        Clear cooldown for component (e.g., after successful recovery).
        
        Args:
            component: Component to clear cooldown for
        """
        self._cooldowns[component] = (None, None)
    
    def get_state(self) -> Dict[str, Dict[str, Optional[str]]]:
        """
        Get current cooldown state for observability.
        
        Returns:
            Dictionary with component cooldown status
        """
        now = datetime.utcnow()
        state = {}
        
        for component in ComponentName:
            in_cooldown = self.is_in_cooldown(component, now)
            remaining = self.get_cooldown_remaining(component, now)
            
            state[component.value] = {
                "in_cooldown": in_cooldown,
                "remaining_seconds": remaining,
            }
        
        return state


# Global singleton instance
_recovery_cooldown: Optional[RecoveryCooldown] = None


def get_recovery_cooldown(cooldown_seconds: int = 60) -> RecoveryCooldown:
    """
    Get or create global recovery cooldown instance.
    
    Args:
        cooldown_seconds: Cooldown duration in seconds (used only on first call)
        
    Returns:
        Global RecoveryCooldown instance
    """
    global _recovery_cooldown
    
    if _recovery_cooldown is None:
        _recovery_cooldown = RecoveryCooldown(cooldown_seconds=cooldown_seconds)
    
    return _recovery_cooldown


def reset_recovery_cooldown() -> None:
    """
    Reset global recovery cooldown instance (for testing).
    """
    global _recovery_cooldown
    _recovery_cooldown = None
