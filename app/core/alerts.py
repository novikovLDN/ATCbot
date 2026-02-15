"""
Alert system for critical system events.

Alerts are generated based on metrics and SLOs, not directly from business logic.
Alerts are for operator visibility and intervention.

IMPORTANT:
- Alerts do NOT block handlers
- Alerts do NOT affect business logic
- Alerts are generated from metrics/SLOs, not from handlers/services
- False positives are suppressed (cooldown, recovery, admin actions)
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Callable
from enum import Enum
from dataclasses import dataclass
import logging

from app.core.metrics import get_metrics
from app.core.slo import get_slo, SLOStatus
from app.core.system_state import SystemState, ComponentStatus

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    """Alert severity levels"""
    PAGE = "page"  # Critical - requires immediate attention
    TICKET = "ticket"  # Warning - requires investigation
    INFO = "info"  # Informational - for visibility


@dataclass
class Alert:
    """Alert instance"""
    severity: AlertSeverity
    title: str
    message: str
    component: str
    timestamp: datetime
    metadata: Dict[str, Any]


class AlertRules:
    """
    Alert rules engine.
    
    Evaluates metrics and SLOs to generate alerts.
    Rules are evaluated periodically, not on every event.
    """
    
    def __init__(self):
        """Initialize alert rules"""
        self.metrics = get_metrics()
        self.slo = get_slo()
        # Track alert state to prevent spam
        self._alert_state: Dict[str, datetime] = {}  # alert_key -> last_fired_at
        self._suppressed_alerts: set[str] = set()  # Currently suppressed alert keys
    
    def evaluate_unavailable_alert(
        self,
        system_state: SystemState,
        unavailable_duration_seconds: float = 120.0
    ) -> Optional[Alert]:
        """
        Evaluate UNAVAILABLE alert rule.
        
        Rule: system_state == UNAVAILABLE > X minutes → PAGE
        
        Args:
            system_state: Current system state
            unavailable_duration_seconds: Duration threshold in seconds (default: 120s)
            
        Returns:
            Alert if rule triggered, None otherwise
        """
        if not system_state.is_unavailable:
            # System is not unavailable - clear any existing alert
            self._suppressed_alerts.discard("unavailable")
            return None
        
        # Check if alert was recently fired (prevent spam)
        now = datetime.now(timezone.utc)
        alert_key = "unavailable"
        last_fired = self._alert_state.get(alert_key)
        if last_fired and (now - last_fired).total_seconds() < unavailable_duration_seconds:
            return None
        
        # Fire alert
        self._alert_state[alert_key] = now
        self._suppressed_alerts.discard(alert_key)
        
        return Alert(
            severity=AlertSeverity.PAGE,
            title="System UNAVAILABLE",
            message=f"System is unavailable for >{unavailable_duration_seconds}s. Immediate attention required.",
            component="system",
            timestamp=now,
            metadata={
                "database_status": system_state.database.status.value,
                "vpn_api_status": system_state.vpn_api.status.value,
                "payments_status": system_state.payments.status.value,
            }
        )
    
    def evaluate_degraded_alert(
        self,
        system_state: SystemState,
        degraded_duration_seconds: float = 600.0
    ) -> Optional[Alert]:
        """
        Evaluate DEGRADED alert rule.
        
        Rule: DEGRADED > X minutes → TICKET
        
        Args:
            system_state: Current system state
            degraded_duration_seconds: Duration threshold in seconds (default: 600s = 10min)
            
        Returns:
            Alert if rule triggered, None otherwise
        """
        if not system_state.is_degraded:
            # System is not degraded - clear any existing alert
            self._suppressed_alerts.discard("degraded")
            return None
        
        # Check if we're in recovery (suppress alert during recovery)
        now = datetime.now(timezone.utc)
        recovery_in_progress = self.metrics.get_gauge("recovery_in_progress") == 1.0
        if recovery_in_progress:
            self._suppressed_alerts.add("degraded")
            return None
        
        # Check if alert was recently fired (prevent spam)
        alert_key = "degraded"
        last_fired = self._alert_state.get(alert_key)
        if last_fired and (now - last_fired).total_seconds() < degraded_duration_seconds:
            return None
        
        # Fire alert
        self._alert_state[alert_key] = now
        self._suppressed_alerts.discard(alert_key)
        
        return Alert(
            severity=AlertSeverity.TICKET,
            title="System DEGRADED",
            message=f"System has been degraded for >{degraded_duration_seconds}s. Investigation recommended.",
            component="system",
            timestamp=now,
            metadata={
                "database_status": system_state.database.status.value,
                "vpn_api_status": system_state.vpn_api.status.value,
            }
        )
    
    def evaluate_recovery_failed_alert(
        self,
        recovery_attempts: int,
        max_attempts: int = 3
    ) -> Optional[Alert]:
        """
        Evaluate recovery failed alert rule.
        
        Rule: recovery > N attempts → TICKET
        
        Args:
            recovery_attempts: Number of recovery attempts
            max_attempts: Maximum allowed attempts before alert (default: 3)
            
        Returns:
            Alert if rule triggered, None otherwise
        """
        if recovery_attempts <= max_attempts:
            return None
        
        now = datetime.now(timezone.utc)
        alert_key = "recovery_failed"
        
        # Check if alert was recently fired (prevent spam)
        last_fired = self._alert_state.get(alert_key)
        if last_fired and (now - last_fired).total_seconds() < 300:  # 5 min cooldown
            return None
        
        # Fire alert
        self._alert_state[alert_key] = now
        
        return Alert(
            severity=AlertSeverity.TICKET,
            title="Recovery Failed",
            message=f"Recovery attempts exceeded threshold ({recovery_attempts} > {max_attempts}). Manual intervention may be required.",
            component="recovery",
            timestamp=now,
            metadata={
                "recovery_attempts": recovery_attempts,
                "max_attempts": max_attempts,
            }
        )
    
    def evaluate_slo_breach_alert(
        self,
        slo_results: Dict[str, Any]
    ) -> Optional[Alert]:
        """
        Evaluate SLO breach alert rule.
        
        Rule: SLO BREACHED → TICKET
        
        Args:
            slo_results: Dictionary of SLO evaluation results
            
        Returns:
            Alert if any SLO is breached, None otherwise
        """
        breached_slos = [
            name for name, result in slo_results.items()
            if isinstance(result, dict) and result.get("status") == SLOStatus.BREACHED.value
        ]
        
        if not breached_slos:
            return None
        
        now = datetime.now(timezone.utc)
        alert_key = "slo_breach"
        
        # Check if alert was recently fired (prevent spam)
        last_fired = self._alert_state.get(alert_key)
        if last_fired and (now - last_fired).total_seconds() < 600:  # 10 min cooldown
            return None
        
        # Fire alert
        self._alert_state[alert_key] = now
        
        return Alert(
            severity=AlertSeverity.TICKET,
            title="SLO Breach",
            message=f"SLOs breached: {', '.join(breached_slos)}. Investigation recommended.",
            component="slo",
            timestamp=now,
            metadata={
                "breached_slos": breached_slos,
                "slo_results": slo_results,
            }
        )
    
    def evaluate_all_rules(
        self,
        system_state: SystemState,
        recovery_attempts: int = 0
    ) -> List[Alert]:
        """
        Evaluate all alert rules.
        
        Args:
            system_state: Current system state
            recovery_attempts: Number of recovery attempts
            
        Returns:
            List of triggered alerts
        """
        alerts = []
        
        # Evaluate SLOs first
        system_state_status = 2.0 if system_state.is_unavailable else (1.0 if system_state.is_degraded else 0.0)
        slo_results = self.slo.evaluate_all_slos(system_state_status)
        
        # Check for SLO breaches
        slo_alert = self.evaluate_slo_breach_alert(slo_results)
        if slo_alert:
            alerts.append(slo_alert)
        
        # Check for unavailable
        unavailable_alert = self.evaluate_unavailable_alert(system_state)
        if unavailable_alert:
            alerts.append(unavailable_alert)
        
        # Check for degraded
        degraded_alert = self.evaluate_degraded_alert(system_state)
        if degraded_alert:
            alerts.append(degraded_alert)
        
        # Check for recovery failures
        recovery_alert = self.evaluate_recovery_failed_alert(recovery_attempts)
        if recovery_alert:
            alerts.append(recovery_alert)
        
        return alerts


# Global singleton instance
_alert_rules: Optional[AlertRules] = None


def get_alert_rules() -> AlertRules:
    """
    Get or create global alert rules instance.
    
    Returns:
        Global AlertRules instance
    """
    global _alert_rules
    
    if _alert_rules is None:
        _alert_rules = AlertRules()
    
    return _alert_rules


def send_alert(alert: Alert, bot=None) -> None:
    """
    Send alert to operator (via logging and optionally Telegram).
    
    Args:
        alert: Alert instance
        bot: Optional Telegram bot for sending alerts
    """
    log_level = {
        AlertSeverity.PAGE: logging.CRITICAL,
        AlertSeverity.TICKET: logging.WARNING,
        AlertSeverity.INFO: logging.INFO,
    }.get(alert.severity, logging.INFO)
    
    logger.log(
        log_level,
        f"[ALERT {alert.severity.upper()}] {alert.title}: {alert.message} "
        f"(component={alert.component}, metadata={alert.metadata})"
    )
    
    # TODO: In production, integrate with PagerDuty, Opsgenie, or Telegram
    # For now, alerts are logged only
