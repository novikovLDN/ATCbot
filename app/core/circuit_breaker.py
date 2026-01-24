"""
Circuit breaker lite for operational safety (no external infrastructure).

STEP 6 — PRODUCTION HARDENING & OPERATIONAL READINESS:
F2. CIRCUIT BREAKER LITE (NO INFRA)

This module provides a minimal in-memory circuit breaker for
operational safety without external dependencies.

IMPORTANT:
- Circuit breaker is optional (defaults to CLOSED)
- NEVER raises exceptions by itself
- Only signals "should_skip" via should_skip() method
- When OPEN: skip operation, log once per interval
"""

import logging
import threading
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, skip operations
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """
    Configuration for a circuit breaker.
    
    Attributes:
        name: Component name (e.g., "db", "vpn_api", "payments")
        failure_threshold: Number of failures before opening
        cooldown_seconds: Time to wait before half-open (open → half-open)
        half_open_success_threshold: Successes needed to close (half-open → closed)
    """
    name: str
    failure_threshold: int = 5  # Failures before opening
    cooldown_seconds: float = 60.0  # Time before half-open
    half_open_success_threshold: int = 2  # Successes to close


class CircuitBreakerLite:
    """
    Minimal in-memory circuit breaker for operational safety.
    
    STEP 6 — F2: CIRCUIT BREAKER LITE
    Provides should_skip() method that returns True if circuit is OPEN.
    Never raises exceptions - only signals via should_skip().
    
    Usage:
        breaker = get_circuit_breaker("vpn_api")
        if breaker.should_skip():
            logger.warning("Circuit breaker OPEN, skipping operation")
            return
        # Proceed with operation
    """
    
    def __init__(self, config: CircuitBreakerConfig):
        """Initialize circuit breaker"""
        self.config = config
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: Optional[datetime] = None
        self._last_log_time: Optional[datetime] = None  # Throttle logging
    
    def should_skip(self) -> bool:
        """
        Check if operation should be skipped.
        
        STEP 6 — F2: CIRCUIT BREAKER LITE
        Returns True if circuit is OPEN (should skip operation).
        Never raises exceptions - only signals via return value.
        
        Returns:
            True if operation should be skipped, False otherwise
        """
        with self._lock:
            self._update_state()
            
            if self._state == CircuitState.OPEN:
                # Throttle logging (once per minute)
                now = datetime.utcnow()
                if not self._last_log_time or (now - self._last_log_time).total_seconds() >= 60:
                    logger.warning(
                        f"[CIRCUIT_BREAKER] {self.config.name} is OPEN, skipping operation "
                        f"(failures={self._failure_count}, opened_at={self._opened_at})"
                    )
                    self._last_log_time = now
                return True
            
            return False
    
    def record_success(self) -> None:
        """
        Record successful operation.
        
        Args:
            None (state tracked internally)
        """
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info(f"[CIRCUIT_BREAKER] {self.config.name} transitioned to CLOSED")
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success in closed state
                self._failure_count = 0
    
    def record_failure(self) -> None:
        """
        Record failed operation.
        
        Args:
            None (state tracked internally)
        """
        with self._lock:
            self._failure_count += 1
            
            if self._state == CircuitState.HALF_OPEN:
                # Failure in half-open → back to open
                self._state = CircuitState.OPEN
                self._opened_at = datetime.utcnow()
                self._success_count = 0
                logger.warning(f"[CIRCUIT_BREAKER] {self.config.name} transitioned back to OPEN")
    
    def _update_state(self) -> None:
        """Update circuit breaker state based on thresholds"""
        now = datetime.utcnow()
        
        # Closed → Open: Too many failures
        if self._state == CircuitState.CLOSED:
            if self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning(
                    f"[CIRCUIT_BREAKER] {self.config.name} transitioned to OPEN "
                    f"(failures={self._failure_count})"
                )
        
        # Open → Half-Open: Cooldown elapsed
        elif self._state == CircuitState.OPEN:
            if self._opened_at and (now - self._opened_at).total_seconds() >= self.config.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._failure_count = 0
                self._success_count = 0
                logger.info(f"[CIRCUIT_BREAKER] {self.config.name} transitioned to HALF_OPEN")
    
    def get_state(self) -> CircuitState:
        """Get current circuit breaker state"""
        with self._lock:
            self._update_state()
            return self._state
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get circuit breaker status.
        
        Returns:
            Dictionary with circuit breaker status
        """
        with self._lock:
            self._update_state()
            return {
                "name": self.config.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "opened_at": self._opened_at.isoformat() if self._opened_at else None,
            }


# Global registry of circuit breakers
_circuit_breakers: Dict[str, CircuitBreakerLite] = {}
_registry_lock = threading.Lock()


def get_circuit_breaker(component: str) -> CircuitBreakerLite:
    """
    Get or create circuit breaker for component.
    
    STEP 6 — F2: CIRCUIT BREAKER LITE
    Returns circuit breaker for component (db, vpn_api, payments).
    Creates with default config if not exists.
    
    Args:
        component: Component name (e.g., "db", "vpn_api", "payments")
        
    Returns:
        CircuitBreakerLite instance
    """
    with _registry_lock:
        if component not in _circuit_breakers:
            config = CircuitBreakerConfig(
                name=component,
                failure_threshold=5,
                cooldown_seconds=60.0,
                half_open_success_threshold=2,
            )
            _circuit_breakers[component] = CircuitBreakerLite(config)
        
        return _circuit_breakers[component]
