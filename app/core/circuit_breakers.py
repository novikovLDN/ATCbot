"""
Circuit breakers for fault tolerance.

This module provides circuit breaker pattern for external dependencies
to prevent cascading failures.

IMPORTANT:
- Circuit breakers are for fault tolerance only
- They do NOT affect business logic
- They are for observability and protection
"""

from enum import Enum
from typing import Optional, Callable, Any
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
import asyncio


class CircuitState(str, Enum):
    """Circuit breaker states"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, fast-fail
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """
    Configuration for a circuit breaker.
    
    Defines thresholds and timeouts.
    """
    name: str
    failure_threshold: int  # Failures before opening
    success_threshold: int  # Successes before closing (half-open → closed)
    timeout_seconds: float  # Time before half-open (open → half-open)
    half_open_max_calls: int  # Max calls in half-open state


class CircuitBreaker:
    """
    Circuit breaker for fault tolerance.
    
    Prevents cascading failures by opening circuit on repeated failures.
    """
    
    def __init__(self, config: CircuitBreakerConfig):
        """Initialize circuit breaker"""
        self.config = config
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._opened_at: Optional[datetime] = None
        self._half_open_calls = 0
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.
        
        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenError: If circuit is open
        """
        with self._lock:
            # Check if circuit should transition
            self._update_state()
            
            # Fast-fail if open
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.config.name} is OPEN"
                )
            
            # Limit calls in half-open
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker {self.config.name} is HALF_OPEN (max calls reached)"
                    )
                self._half_open_calls += 1
        
        # Execute function
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            raise
    
    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute async function through circuit breaker.
        
        Args:
            func: Async function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenError: If circuit is open
        """
        with self._lock:
            # Check if circuit should transition
            self._update_state()
            
            # Fast-fail if open
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker {self.config.name} is OPEN"
                )
            
            # Limit calls in half-open
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker {self.config.name} is HALF_OPEN (max calls reached)"
                    )
                self._half_open_calls += 1
        
        # Execute async function
        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            raise
    
    def _update_state(self) -> None:
        """Update circuit breaker state based on thresholds"""
        now = datetime.now(timezone.utc)
        
        # Closed → Open: Too many failures
        if self._state == CircuitState.CLOSED:
            if self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now
                self._half_open_calls = 0
        
        # Open → Half-Open: Timeout elapsed
        elif self._state == CircuitState.OPEN:
            if self._opened_at and (now - self._opened_at).total_seconds() >= self.config.timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._failure_count = 0
                self._success_count = 0
                self._half_open_calls = 0
        
        # Half-Open → Closed: Enough successes
        elif self._state == CircuitState.HALF_OPEN:
            if self._success_count >= self.config.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                self._half_open_calls = 0
        
        # Half-Open → Open: Too many failures
        elif self._state == CircuitState.HALF_OPEN:
            if self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now
                self._half_open_calls = 0
    
    def _record_success(self) -> None:
        """Record successful operation"""
        with self._lock:
            self._success_count += 1
            if self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0
    
    def _record_failure(self) -> None:
        """Record failed operation"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now(timezone.utc)
            if self._state == CircuitState.HALF_OPEN:
                # Reset success count on failure in half-open
                self._success_count = 0
    
    def get_state(self) -> CircuitState:
        """Get current circuit breaker state"""
        with self._lock:
            self._update_state()
            return self._state
    
    def get_status(self) -> dict:
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
                "last_failure_time": self._last_failure_time.isoformat() if self._last_failure_time else None,
                "opened_at": self._opened_at.isoformat() if self._opened_at else None,
                "half_open_calls": self._half_open_calls,
            }


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open"""
    pass


class CircuitBreakerRegistry:
    """
    Registry of circuit breakers.
    
    Manages circuit breakers for different external dependencies.
    """
    
    def __init__(self):
        """Initialize circuit breaker registry"""
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._initialize_default_breakers()
    
    def _initialize_default_breakers(self) -> None:
        """Initialize default circuit breakers"""
        default_configs = [
            CircuitBreakerConfig(
                name="database",
                failure_threshold=5,
                success_threshold=3,
                timeout_seconds=60.0,
                half_open_max_calls=3,
            ),
            CircuitBreakerConfig(
                name="vpn_api",
                failure_threshold=5,
                success_threshold=3,
                timeout_seconds=30.0,
                half_open_max_calls=2,
            ),
            CircuitBreakerConfig(
                name="payment_api",
                failure_threshold=3,
                success_threshold=2,
                timeout_seconds=60.0,
                half_open_max_calls=2,
            ),
            CircuitBreakerConfig(
                name="telegram_api",
                failure_threshold=10,
                success_threshold=5,
                timeout_seconds=30.0,
                half_open_max_calls=5,
            ),
        ]
        
        for config in default_configs:
            self._breakers[config.name] = CircuitBreaker(config)
    
    def get_breaker(self, name: str) -> CircuitBreaker:
        """
        Get circuit breaker by name.
        
        Args:
            name: Circuit breaker name
            
        Returns:
            CircuitBreaker instance
        """
        with self._lock:
            if name not in self._breakers:
                # Create default breaker if not exists
                config = CircuitBreakerConfig(
                    name=name,
                    failure_threshold=5,
                    success_threshold=3,
                    timeout_seconds=60.0,
                    half_open_max_calls=3,
                )
                self._breakers[name] = CircuitBreaker(config)
            return self._breakers[name]
    
    def get_all_breakers(self) -> Dict[str, CircuitBreaker]:
        """Get all circuit breakers"""
        with self._lock:
            return dict(self._breakers)
    
    def get_all_status(self) -> dict:
        """
        Get status of all circuit breakers.
        
        Returns:
            Dictionary mapping breaker name to status
        """
        with self._lock:
            return {
                name: breaker.get_status()
                for name, breaker in self._breakers.items()
            }


# Global singleton instance
_circuit_breaker_registry: Optional[CircuitBreakerRegistry] = None


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """
    Get or create global circuit breaker registry instance.
    
    Returns:
        Global CircuitBreakerRegistry instance
    """
    global _circuit_breaker_registry
    
    if _circuit_breaker_registry is None:
        _circuit_breaker_registry = CircuitBreakerRegistry()
    
    return _circuit_breaker_registry


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """
    Get circuit breaker by name (convenience function).
    
    Args:
        name: Circuit breaker name
        
    Returns:
        CircuitBreaker instance
    """
    return get_circuit_breaker_registry().get_breaker(name)
