"""
Performance budget definitions for latency and throughput.

This module defines latency budgets for different operation types
to enable performance monitoring and capacity planning.

IMPORTANT:
- Budgets are metrics only (not enforced)
- Budgets do NOT block code execution
- Budgets are for observability and alerting
"""

from dataclasses import dataclass
from typing import Dict, Optional, Any
from enum import Enum


class OperationType(str, Enum):
    """Types of operations with performance budgets"""
    HTTP_HANDLER = "http_handler"
    BACKGROUND_TASK = "background_task"
    DB_OPERATION = "db_operation"
    VPN_API = "vpn_api"
    PAYMENT_API = "payment_api"
    SERVICE_CALL = "service_call"


@dataclass
class LatencyBudget:
    """
    Latency budget for an operation type.
    
    All values are in milliseconds.
    """
    operation_type: OperationType
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: Optional[float] = None  # Optional hard limit
    
    def is_within_budget(self, p95_latency_ms: float) -> bool:
        """
        Check if P95 latency is within budget.
        
        Args:
            p95_latency_ms: Measured P95 latency in milliseconds
            
        Returns:
            True if within budget, False otherwise
        """
        return p95_latency_ms <= self.p95_ms


class PerformanceBudget:
    """
    Performance budget definitions for system operations.
    
    Provides latency budgets for different operation types
    to enable performance monitoring and capacity planning.
    """
    
    def __init__(self):
        """Initialize performance budgets"""
        self._budgets: Dict[OperationType, LatencyBudget] = {
            # HTTP handlers: P95 ≤ 300ms
            OperationType.HTTP_HANDLER: LatencyBudget(
                operation_type=OperationType.HTTP_HANDLER,
                p50_ms=100.0,
                p95_ms=300.0,
                p99_ms=500.0,
                max_ms=1000.0,
            ),
            # Background tasks: P95 ≤ 500ms (less critical)
            OperationType.BACKGROUND_TASK: LatencyBudget(
                operation_type=OperationType.BACKGROUND_TASK,
                p50_ms=200.0,
                p95_ms=500.0,
                p99_ms=1000.0,
                max_ms=5000.0,
            ),
            # DB operations: P95 ≤ 80ms
            OperationType.DB_OPERATION: LatencyBudget(
                operation_type=OperationType.DB_OPERATION,
                p50_ms=20.0,
                p95_ms=80.0,
                p99_ms=150.0,
                max_ms=500.0,
            ),
            # VPN API: P95 ≤ 500ms
            OperationType.VPN_API: LatencyBudget(
                operation_type=OperationType.VPN_API,
                p50_ms=200.0,
                p95_ms=500.0,
                p99_ms=1000.0,
                max_ms=5000.0,
            ),
            # Payment API: P95 ≤ 1000ms (external dependency)
            OperationType.PAYMENT_API: LatencyBudget(
                operation_type=OperationType.PAYMENT_API,
                p50_ms=300.0,
                p95_ms=1000.0,
                p99_ms=2000.0,
                max_ms=10000.0,
            ),
            # Service calls: P95 ≤ 200ms
            OperationType.SERVICE_CALL: LatencyBudget(
                operation_type=OperationType.SERVICE_CALL,
                p50_ms=50.0,
                p95_ms=200.0,
                p99_ms=400.0,
                max_ms=1000.0,
            ),
        }
    
    def get_budget(self, operation_type: OperationType) -> LatencyBudget:
        """
        Get latency budget for operation type.
        
        Args:
            operation_type: Type of operation
            
        Returns:
            LatencyBudget for the operation type
        """
        return self._budgets.get(operation_type, self._budgets[OperationType.SERVICE_CALL])
    
    def check_budget_compliance(
        self,
        operation_type: OperationType,
        p95_latency_ms: float
    ) -> bool:
        """
        Check if operation complies with latency budget.
        
        Args:
            operation_type: Type of operation
            p95_latency_ms: Measured P95 latency in milliseconds
            
        Returns:
            True if within budget, False otherwise
        """
        budget = self.get_budget(operation_type)
        return budget.is_within_budget(p95_latency_ms)
    
    def get_all_budgets(self) -> Dict[OperationType, LatencyBudget]:
        """Get all defined budgets"""
        return dict(self._budgets)
    
    def check_all_budgets(self) -> Dict[OperationType, Dict[str, Any]]:
        """
        Check all budgets against current metrics (D1.1 - Latency Budgeting).
        
        Returns:
            Dictionary mapping operation type to budget compliance status
        """
        metrics = get_metrics()
        results = {}
        
        # Map operation types to metric names
        metric_mapping = {
            OperationType.DB_OPERATION: "db_latency_ms",
            OperationType.VPN_API: "vpn_api_latency_ms",
            OperationType.PAYMENT_API: "payment_latency_ms",
        }
        
        for op_type, budget in self._budgets.items():
            metric_name = metric_mapping.get(op_type)
            if metric_name:
                timer_stats = metrics.get_timer_stats(metric_name)
                if timer_stats and "p95" in timer_stats:
                    p95_latency = timer_stats["p95"]
                    is_compliant = budget.is_within_budget(p95_latency)
                    results[op_type] = {
                        "budget_p95_ms": budget.p95_ms,
                        "actual_p95_ms": p95_latency,
                        "is_compliant": is_compliant,
                        "p50_ms": timer_stats.get("p50", 0.0),
                        "p99_ms": timer_stats.get("p99", 0.0),
                    }
                else:
                    results[op_type] = {
                        "budget_p95_ms": budget.p95_ms,
                        "actual_p95_ms": None,
                        "is_compliant": None,
                        "status": "no_data",
                    }
            else:
                # No metric mapping (e.g., HTTP_HANDLER, BACKGROUND_TASK)
                results[op_type] = {
                    "budget_p95_ms": budget.p95_ms,
                    "actual_p95_ms": None,
                    "is_compliant": None,
                    "status": "not_tracked",
                }
        
        return results
    
    def estimate_capacity(
        self,
        operation_type: OperationType,
        target_latency_ms: float
    ) -> Optional[float]:
        """
        Estimate max sustainable RPS based on latency budget (D1.3 - Capacity Signals).
        
        Args:
            operation_type: Type of operation
            target_latency_ms: Target latency in milliseconds
            
        Returns:
            Estimated max RPS, or None if cannot estimate
        """
        budget = self.get_budget(operation_type)
        metrics = get_metrics()
        
        # Map operation types to metric names
        metric_mapping = {
            OperationType.DB_OPERATION: "db_latency_ms",
            OperationType.VPN_API: "vpn_api_latency_ms",
            OperationType.PAYMENT_API: "payment_latency_ms",
        }
        
        metric_name = metric_mapping.get(operation_type)
        if not metric_name:
            return None
        
        timer_stats = metrics.get_timer_stats(metric_name)
        if not timer_stats or "avg" not in timer_stats:
            return None
        
        avg_latency_ms = timer_stats["avg"]
        if avg_latency_ms <= 0:
            return None
        
        # Simple capacity estimation: 1000ms / avg_latency_ms = max RPS
        # This is a rough estimate, actual capacity depends on many factors
        max_rps = 1000.0 / avg_latency_ms
        
        # Apply safety factor (70% of theoretical max)
        safe_rps = max_rps * 0.7
        
        return safe_rps


# Global singleton instance
_performance_budget: Optional[PerformanceBudget] = None


def get_performance_budget() -> PerformanceBudget:
    """
    Get or create global performance budget instance.
    
    Returns:
        Global PerformanceBudget instance
    """
    global _performance_budget
    
    if _performance_budget is None:
        _performance_budget = PerformanceBudget()
    
    return _performance_budget
