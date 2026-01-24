"""
SLO (Service Level Objectives) definitions and calculations.

SLOs are computed from metrics and provide thresholds for system health.
SLOs do NOT block code execution or generate alerts directly.

IMPORTANT:
- SLOs are computed, not enforced
- SLOs are for observability and alerting rules
- SLOs do NOT affect business logic
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from app.core.metrics import get_metrics
from app.core.system_state import ComponentStatus


class SLOStatus(str, Enum):
    """SLO compliance status"""
    COMPLIANT = "compliant"
    AT_RISK = "at_risk"
    BREACHED = "breached"


@dataclass
class SLOResult:
    """Result of SLO evaluation"""
    name: str
    status: SLOStatus
    current_value: float
    target_value: float
    description: str
    timestamp: datetime


class SLO:
    """
    Service Level Objectives calculator.
    
    Computes SLO compliance from metrics without affecting system behavior.
    """
    
    def __init__(self):
        """Initialize SLO calculator"""
        self.metrics = get_metrics()
    
    def evaluate_availability_slo(
        self,
        system_state_status: float,
        target_availability: float = 0.999
    ) -> SLOResult:
        """
        Evaluate availability SLO.
        
        Availability = 1.0 - (time_unavailable / total_time)
        
        Args:
            system_state_status: Current system state (0=healthy,1=degraded,2=unavailable)
            target_availability: Target availability (default: 99.9%)
            
        Returns:
            SLOResult with compliance status
        """
        # For now, use current state as proxy
        # In production, this would aggregate over time window
        is_unavailable = system_state_status >= 2.0
        current_availability = 0.0 if is_unavailable else 1.0
        
        if current_availability >= target_availability:
            status = SLOStatus.COMPLIANT
        elif current_availability >= target_availability * 0.95:  # 5% margin
            status = SLOStatus.AT_RISK
        else:
            status = SLOStatus.BREACHED
        
        return SLOResult(
            name="availability",
            status=status,
            current_value=current_availability,
            target_value=target_availability,
            description=f"System availability SLO (target: {target_availability*100:.1f}%)",
            timestamp=datetime.utcnow(),
        )
    
    def evaluate_degradation_budget(
        self,
        system_state_status: float,
        max_degraded_percent: float = 5.0
    ) -> SLOResult:
        """
        Evaluate degradation budget SLO.
        
        Degradation budget = percentage of time system can be DEGRADED
        
        Args:
            system_state_status: Current system state (0=healthy,1=degraded,2=unavailable)
            max_degraded_percent: Maximum allowed degraded time percentage (default: 5%)
            
        Returns:
            SLOResult with compliance status
        """
        is_degraded = system_state_status == 1.0
        current_degraded_percent = 100.0 if is_degraded else 0.0
        
        if current_degraded_percent <= max_degraded_percent:
            status = SLOStatus.COMPLIANT
        elif current_degraded_percent <= max_degraded_percent * 1.5:  # 50% margin
            status = SLOStatus.AT_RISK
        else:
            status = SLOStatus.BREACHED
        
        return SLOResult(
            name="degradation_budget",
            status=status,
            current_value=current_degraded_percent,
            target_value=max_degraded_percent,
            description=f"Degradation budget SLO (max: {max_degraded_percent}%)",
            timestamp=datetime.utcnow(),
        )
    
    def evaluate_background_reliability(
        self,
        skipped_iterations: int,
        total_iterations: int,
        max_skip_percent: float = 10.0
    ) -> SLOResult:
        """
        Evaluate background task reliability SLO.
        
        Reliability = 1.0 - (skipped_iterations / total_iterations)
        
        Args:
            skipped_iterations: Number of skipped iterations
            total_iterations: Total iterations
            max_skip_percent: Maximum allowed skip percentage (default: 10%)
            
        Returns:
            SLOResult with compliance status
        """
        if total_iterations == 0:
            # No data yet - assume compliant
            current_reliability = 1.0
        else:
            skip_percent = (skipped_iterations / total_iterations) * 100.0
            current_reliability = 1.0 - (skip_percent / 100.0)
        
        target_reliability = 1.0 - (max_skip_percent / 100.0)
        
        if current_reliability >= target_reliability:
            status = SLOStatus.COMPLIANT
        elif current_reliability >= target_reliability * 0.95:  # 5% margin
            status = SLOStatus.AT_RISK
        else:
            status = SLOStatus.BREACHED
        
        return SLOResult(
            name="background_reliability",
            status=status,
            current_value=current_reliability,
            target_value=target_reliability,
            description=f"Background task reliability SLO (max skip: {max_skip_percent}%)",
            timestamp=datetime.utcnow(),
        )
    
    def evaluate_all_slos(
        self,
        system_state_status: float,
        skipped_iterations: int = 0,
        total_iterations: int = 0
    ) -> Dict[str, SLOResult]:
        """
        Evaluate all defined SLOs.
        
        Args:
            system_state_status: Current system state
            skipped_iterations: Number of skipped background iterations
            total_iterations: Total background iterations
            
        Returns:
            Dictionary of SLO name -> SLOResult
        """
        return {
            "availability": self.evaluate_availability_slo(system_state_status),
            "degradation_budget": self.evaluate_degradation_budget(system_state_status),
            "background_reliability": self.evaluate_background_reliability(
                skipped_iterations,
                total_iterations
            ),
        }


# Global singleton instance
_slo: Optional[SLO] = None


def get_slo() -> SLO:
    """
    Get or create global SLO instance.
    
    Returns:
        Global SLO instance
    """
    global _slo
    
    if _slo is None:
        _slo = SLO()
    
    return _slo
