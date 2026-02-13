"""
Cost model for tracking resource consumption and cost centers.

This module provides cost tracking for different resource types
to enable cost control and anomaly detection.

IMPORTANT:
- Cost tracking is for observability only
- Costs are NOT enforced
- Costs are NOT blocked
- Costs are for alerting and capacity planning
"""

from dataclasses import dataclass
from typing import Dict, Optional
from enum import Enum
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from app.core.metrics import get_metrics


class CostCenter(str, Enum):
    """Cost centers for resource tracking"""
    DB_CONNECTIONS = "db_connections"
    EXTERNAL_API_CALLS = "external_api_calls"
    BACKGROUND_ITERATIONS = "background_iterations"
    RETRIES = "retries"
    VPN_API_CALLS = "vpn_api_calls"
    PAYMENT_API_CALLS = "payment_api_calls"


@dataclass
class CostEvent:
    """Single cost event"""
    cost_center: CostCenter
    cost_units: float
    timestamp: datetime
    metadata: Optional[Dict] = None


class CostModel:
    """
    Cost model for tracking resource consumption.
    
    Tracks cost events and provides cost anomaly detection.
    """
    
    def __init__(self):
        """Initialize cost model"""
        self.metrics = get_metrics()
        # In-memory cost tracking (last hour)
        self._cost_events: list[CostEvent] = []
        self._cost_window_seconds = 3600  # 1 hour
    
    def record_cost(
        self,
        cost_center: CostCenter,
        cost_units: float = 1.0,
        metadata: Optional[Dict] = None
    ) -> None:
        """
        Record a cost event.
        
        Args:
            cost_center: Cost center for the event
            cost_units: Number of cost units (default: 1.0)
            metadata: Optional metadata about the cost event
        """
        now = datetime.now(timezone.utc)
        event = CostEvent(
            cost_center=cost_center,
            cost_units=cost_units,
            timestamp=now,
            metadata=metadata or {},
        )
        
        self._cost_events.append(event)
        
        # Cleanup old events (outside window)
        cutoff = now - timedelta(seconds=self._cost_window_seconds)
        self._cost_events = [
            e for e in self._cost_events
            if e.timestamp >= cutoff
        ]
    
    def get_cost_summary(self) -> Dict[CostCenter, float]:
        """
        Get cost summary for all cost centers (last hour).
        
        Returns:
            Dictionary mapping cost center to total cost units
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self._cost_window_seconds)
        
        recent_events = [
            e for e in self._cost_events
            if e.timestamp >= cutoff
        ]
        
        summary = defaultdict(float)
        for event in recent_events:
            summary[event.cost_center] += event.cost_units
        
        return dict(summary)
    
    def check_cost_anomaly(
        self,
        cost_center: CostCenter,
        threshold: float
    ) -> bool:
        """
        Check if cost center exceeds threshold.
        
        Args:
            cost_center: Cost center to check
            threshold: Cost threshold (units per hour)
            
        Returns:
            True if threshold exceeded, False otherwise
        """
        summary = self.get_cost_summary()
        current_cost = summary.get(cost_center, 0.0)
        return current_cost > threshold
    
    def get_cost_anomalies(
        self,
        thresholds: Dict[CostCenter, float]
    ) -> Dict[CostCenter, float]:
        """
        Get all cost centers that exceed thresholds.
        
        Args:
            thresholds: Dictionary mapping cost center to threshold
            
        Returns:
            Dictionary mapping cost center to current cost (if exceeded)
        """
        summary = self.get_cost_summary()
        anomalies = {}
        
        for cost_center, threshold in thresholds.items():
            current_cost = summary.get(cost_center, 0.0)
            if current_cost > threshold:
                anomalies[cost_center] = current_cost
        
        return anomalies
    
    def check_and_alert_cost_anomalies(
        self,
        thresholds: Optional[Dict[CostCenter, float]] = None
    ) -> None:
        """
        Check for cost anomalies and send alerts (D2.3 - Cost Anomaly Detection).
        
        Args:
            thresholds: Optional custom thresholds (uses defaults if not provided)
        """
        if thresholds is None:
            thresholds = DEFAULT_COST_THRESHOLDS
        
        anomalies = self.get_cost_anomalies(thresholds)
        
        if anomalies:
            from datetime import datetime
            # Determine alert severity based on anomaly magnitude
            max_anomaly = max(anomalies.values())
            max_threshold = max(thresholds.values())
            
            if max_anomaly > max_threshold * 2.0:
                severity = AlertSeverity.TICKET
            else:
                severity = AlertSeverity.INFO
            
            alert = Alert(
                severity=severity,
                title="Cost Anomaly Detected",
                message=f"Cost centers exceeding thresholds: {', '.join(anomalies.keys())}",
                component="cost_model",
                timestamp=datetime.now(timezone.utc),
                metadata={
                    "anomalies": anomalies,
                    "thresholds": {k.value: v for k, v in thresholds.items()},
                }
            )
            
            send_alert(alert)


# Default cost thresholds (soft limits, not enforced)
DEFAULT_COST_THRESHOLDS: Dict[CostCenter, float] = {
    CostCenter.RETRIES: 1000.0,  # 1000 retries per hour
    CostCenter.BACKGROUND_ITERATIONS: 10000.0,  # 10000 iterations per hour
    CostCenter.VPN_API_CALLS: 5000.0,  # 5000 calls per hour
    CostCenter.PAYMENT_API_CALLS: 1000.0,  # 1000 calls per hour
    CostCenter.DB_CONNECTIONS: 100.0,  # 100 connection acquisitions per hour
    CostCenter.EXTERNAL_API_CALLS: 10000.0,  # 10000 calls per hour
}


# Global singleton instance
_cost_model: Optional[CostModel] = None


def get_cost_model() -> CostModel:
    """
    Get or create global cost model instance.
    
    Returns:
        Global CostModel instance
    """
    global _cost_model
    
    if _cost_model is None:
        _cost_model = CostModel()
    
    return _cost_model
