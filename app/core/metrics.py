"""
Metrics abstraction layer for system observability.

This module provides a simple, in-memory metrics collection system
that can be extended to export to Prometheus or other systems later.

IMPORTANT:
- Pure observation only (no side effects on business logic)
- In-memory storage (does not persist across restarts)
- Log-friendly format for easy debugging
- Ready for future export to Prometheus/other systems
"""

from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum
from collections import defaultdict
import threading
import time


class MetricType(str, Enum):
    """Types of metrics supported"""
    COUNTER = "counter"
    GAUGE = "gauge"
    TIMER = "timer"


class Metrics:
    """
    In-memory metrics collection system.
    
    Thread-safe metrics storage with support for:
    - Counters: monotonically increasing values
    - Gauges: point-in-time values
    - Timers: duration measurements
    """
    
    def __init__(self):
        """Initialize metrics storage"""
        self._lock = threading.Lock()
        # Counters: metric_name -> value
        self._counters: Dict[str, float] = defaultdict(float)
        # Gauges: metric_name -> value
        self._gauges: Dict[str, float] = {}
        # Timers: metric_name -> list of durations (ms)
        self._timers: Dict[str, list[float]] = defaultdict(list)
        # Metadata: metric_name -> {type, description, labels}
        self._metadata: Dict[str, Dict[str, Any]] = {}
    
    def register_counter(
        self,
        name: str,
        description: str = "",
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Register a counter metric.
        
        Args:
            name: Metric name (e.g., "requests_total")
            description: Human-readable description
            labels: Optional labels for multi-dimensional metrics
        """
        with self._lock:
            self._metadata[name] = {
                "type": MetricType.COUNTER,
                "description": description,
                "labels": labels or {},
            }
            if name not in self._counters:
                self._counters[name] = 0.0
    
    def increment_counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Increment a counter metric.
        
        Args:
            name: Metric name
            value: Amount to increment (default: 1.0)
            labels: Optional labels (for future multi-dimensional support)
        """
        with self._lock:
            if name not in self._metadata:
                self.register_counter(name)
            self._counters[name] += value
    
    def set_gauge(
        self,
        name: str,
        value: float,
        description: str = "",
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Set a gauge metric value.
        
        Args:
            name: Metric name (e.g., "system_state_status")
            value: Current value
            description: Human-readable description
            labels: Optional labels
        """
        with self._lock:
            if name not in self._metadata:
                self._metadata[name] = {
                    "type": MetricType.GAUGE,
                    "description": description,
                    "labels": labels or {},
                }
            self._gauges[name] = value
    
    def record_timer(
        self,
        name: str,
        duration_ms: float,
        description: str = "",
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Record a timer metric (duration in milliseconds).
        
        Args:
            name: Metric name (e.g., "db_latency_ms")
            duration_ms: Duration in milliseconds
            description: Human-readable description
            labels: Optional labels
        """
        with self._lock:
            if name not in self._metadata:
                self._metadata[name] = {
                    "type": MetricType.TIMER,
                    "description": description,
                    "labels": labels or {},
                }
            # Keep last 1000 measurements for each timer
            if len(self._timers[name]) >= 1000:
                self._timers[name] = self._timers[name][-999:]
            self._timers[name].append(duration_ms)
    
    def get_counter(self, name: str) -> float:
        """Get current counter value"""
        with self._lock:
            return self._counters.get(name, 0.0)
    
    def get_gauge(self, name: str) -> Optional[float]:
        """Get current gauge value"""
        with self._lock:
            return self._gauges.get(name)
    
    def get_timer_stats(self, name: str) -> Dict[str, float]:
        """
        Get timer statistics (min, max, avg, p50, p95, p99).
        
        Args:
            name: Timer metric name
            
        Returns:
            Dictionary with statistics or empty dict if no data
        """
        with self._lock:
            values = self._timers.get(name, [])
            if not values:
                return {}
            
            sorted_values = sorted(values)
            n = len(sorted_values)
            
            return {
                "min": sorted_values[0],
                "max": sorted_values[-1],
                "avg": sum(sorted_values) / n,
                "p50": sorted_values[int(n * 0.50)],
                "p95": sorted_values[int(n * 0.95)],
                "p99": sorted_values[int(n * 0.99)],
                "count": n,
            }
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """
        Get all metrics in a structured format.
        
        Returns:
            Dictionary with counters, gauges, and timer stats
        """
        with self._lock:
            timers_stats = {
                name: self.get_timer_stats(name)
                for name in self._timers.keys()
            }
            
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timers": timers_stats,
                "metadata": dict(self._metadata),
            }
    
    def get_hot_paths(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top hot paths by P95 latency (D1.2 - Hot Path Identification).
        
        Args:
            limit: Maximum number of hot paths to return (default: 10)
            
        Returns:
            List of hot paths sorted by P95 latency (descending)
        """
        with self._lock:
            hot_paths = []
            
            for name, stats in {
                name: self.get_timer_stats(name)
                for name in self._timers.keys()
            }.items():
                if stats and "p95" in stats:
                    hot_paths.append({
                        "metric_name": name,
                        "p95_ms": stats["p95"],
                        "p99_ms": stats.get("p99", 0.0),
                        "count": stats.get("count", 0),
                        "avg_ms": stats.get("avg", 0.0),
                    })
            
            # Sort by P95 latency (descending)
            hot_paths.sort(key=lambda x: x["p95_ms"], reverse=True)
            
            return hot_paths[:limit]
    
    def get_slow_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top slow queries by P95 latency (D1.2 - Hot Path Identification).
        
        Args:
            limit: Maximum number of slow queries to return (default: 10)
            
        Returns:
            List of slow queries sorted by P95 latency (descending)
        """
        # Filter for DB-related metrics
        db_metrics = [
            name for name in self._timers.keys()
            if "db" in name.lower() or "database" in name.lower()
        ]
        
        slow_queries = []
        for name in db_metrics:
            stats = self.get_timer_stats(name)
            if stats and "p95" in stats:
                slow_queries.append({
                    "metric_name": name,
                    "p95_ms": stats["p95"],
                    "p99_ms": stats.get("p99", 0.0),
                    "count": stats.get("count", 0),
                    "avg_ms": stats.get("avg", 0.0),
                })
        
        # Sort by P95 latency (descending)
        slow_queries.sort(key=lambda x: x["p95_ms"], reverse=True)
        
        return slow_queries[:limit]
    
    def get_retry_heavy_operations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get operations with high retry counts (D1.2 - Hot Path Identification).
        
        Args:
            limit: Maximum number of operations to return (default: 10)
            
        Returns:
            List of retry-heavy operations sorted by retry count (descending)
        """
        # This would require tracking retries per operation
        # For now, return empty list (can be extended later)
        return []
    
    def reset(self) -> None:
        """Reset all metrics (for testing)"""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._timers.clear()
            self._metadata.clear()


# Global singleton instance
_metrics: Optional[Metrics] = None


def get_metrics() -> Metrics:
    """
    Get or create global metrics instance.
    
    Returns:
        Global Metrics instance
    """
    global _metrics
    
    if _metrics is None:
        _metrics = Metrics()
        # Register default metrics
        _register_default_metrics(_metrics)
    
    return _metrics


def reset_metrics() -> None:
    """Reset global metrics instance (for testing)"""
    global _metrics
    _metrics = None


def _register_default_metrics(metrics: Metrics) -> None:
    """Register default system metrics"""
    # Counters
    metrics.register_counter("requests_total", "Total number of requests")
    metrics.register_counter("background_iterations_total", "Total background task iterations")
    metrics.register_counter("retries_total", "Total retry attempts")
    metrics.register_counter("failures_total", "Total failures")
    
    # Gauges
    metrics.set_gauge("system_state_status", 0.0, "System state (0=healthy,1=degraded,2=unavailable)")
    
    # Timers (registered but not set until first measurement)
    metrics._metadata["db_latency_ms"] = {
        "type": MetricType.TIMER,
        "description": "Database operation latency in milliseconds",
        "labels": {},
    }
    metrics._metadata["vpn_api_latency_ms"] = {
        "type": MetricType.TIMER,
        "description": "VPN API operation latency in milliseconds",
        "labels": {},
    }
    metrics._metadata["payment_latency_ms"] = {
        "type": MetricType.TIMER,
        "description": "Payment operation latency in milliseconds",
        "labels": {},
    }


# Context manager for timing operations
class TimerContext:
    """Context manager for measuring operation duration"""
    
    def __init__(self, metric_name: str):
        """
        Initialize timer context.
        
        Args:
            metric_name: Name of the timer metric
        """
        self.metric_name = metric_name
        self.start_time: Optional[float] = None
    
    def __enter__(self):
        """Start timing"""
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop timing and record metric"""
        if self.start_time is not None:
            duration_ms = (time.time() - self.start_time) * 1000.0
            get_metrics().record_timer(self.metric_name, duration_ms)
        return False


def timer(metric_name: str) -> TimerContext:
    """
    Create a timer context manager for measuring operation duration.
    
    Usage:
        with timer("db_latency_ms"):
            await database.query(...)
    
    Args:
        metric_name: Name of the timer metric
        
    Returns:
        TimerContext instance
    """
    return TimerContext(metric_name)
