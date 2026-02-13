"""
System Health Evaluator - Enterprise-grade health assessment.

This module provides centralized system health evaluation with severity levels
(GREEN/YELLOW/RED) based on component states, pending operations, and error patterns.

Characteristics:
- Pure computation (no side effects)
- Deterministic severity calculation
- Human-readable summaries
- Observable and auditable
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from enum import Enum

from app.core.system_state import SystemState, SystemSeverity, recalculate_from_runtime, ComponentStatus


@dataclass
class ComponentHealth:
    """Health status of a single component."""
    name: str
    status: ComponentStatus
    message: str
    last_checked: Optional[datetime] = None


@dataclass
class SystemHealthReport:
    """
    Complete system health report with severity and actionable insights.
    
    Attributes:
        level: SystemSeverity (GREEN/YELLOW/RED)
        summary: Short human-readable summary
        components: Dict of component health statuses
        pending_activations: Count of pending activations
        last_critical_error: Most recent critical error (if any)
        updated_at: Timestamp of report generation
        actionable_issues: List of issues requiring attention
    """
    level: SystemSeverity
    summary: str
    components: Dict[str, ComponentHealth]
    pending_activations: int
    last_critical_error: Optional[str]
    updated_at: datetime
    actionable_issues: List[str]


async def evaluate_system_health() -> SystemHealthReport:
    """
    1. SYSTEM HEALTH & SEVERITY ENGINE
    
    Evaluate complete system health based on:
    - Component states (database, payments, vpn_api)
    - Pending activations
    - System state
    - Recent errors
    
    Returns:
        SystemHealthReport with severity level and detailed status
    """
    now = datetime.now(timezone.utc)
    
    # Get current system state
    system_state = recalculate_from_runtime()
    
    # Count pending activations
    pending_activations = 0
    try:
        import database
        if database.DB_READY:
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                pending_activations = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                ) or 0
    except Exception:
        pass
    
    # Build component health statuses
    components = {
        "database": ComponentHealth(
            name="database",
            status=system_state.database.status,
            message=_get_component_message(system_state.database),
            last_checked=system_state.database.last_checked_at
        ),
        "payments": ComponentHealth(
            name="payments",
            status=system_state.payments.status,
            message=_get_component_message(system_state.payments),
            last_checked=system_state.payments.last_checked_at
        ),
        "vpn_api": ComponentHealth(
            name="vpn_api",
            status=system_state.vpn_api.status,
            message=_get_component_message(system_state.vpn_api),
            last_checked=system_state.vpn_api.last_checked_at
        ),
    }
    
    # Calculate severity
    severity = system_state.get_severity(pending_activations=pending_activations)
    
    # Build actionable issues list
    actionable_issues = []
    if system_state.database.status != ComponentStatus.HEALTHY:
        actionable_issues.append(f"Database: {system_state.database.error}")
    if system_state.payments.status != ComponentStatus.HEALTHY:
        actionable_issues.append(f"Payments: {system_state.payments.error}")
    if system_state.vpn_api.status == ComponentStatus.DEGRADED:
        actionable_issues.append(f"VPN API: {system_state.vpn_api.error}")
    if pending_activations > 0:
        actionable_issues.append(f"Pending activations: {pending_activations} subscriptions waiting for VPN provisioning")
    
    # Get last critical error (if any)
    last_critical_error = None
    if system_state.is_unavailable:
        # Find the first critical component error
        if system_state.database.status != ComponentStatus.HEALTHY:
            last_critical_error = f"Database: {system_state.database.error}"
        elif system_state.payments.status != ComponentStatus.HEALTHY:
            last_critical_error = f"Payments: {system_state.payments.error}"
    
    # Build summary
    summary = _build_summary(severity, components, pending_activations, last_critical_error)
    
    return SystemHealthReport(
        level=severity,
        summary=summary,
        components=components,
        pending_activations=pending_activations,
        last_critical_error=last_critical_error,
        updated_at=now,
        actionable_issues=actionable_issues
    )


def _get_component_message(component_state) -> str:
    """Get human-readable message for component state."""
    if component_state.status == ComponentStatus.HEALTHY:
        return "OK"
    elif component_state.status == ComponentStatus.DEGRADED:
        return component_state.error or "Degraded"
    else:
        return component_state.error or "Unavailable"


def _build_summary(
    severity: SystemSeverity,
    components: Dict[str, ComponentHealth],
    pending_activations: int,
    last_critical_error: Optional[str]
) -> str:
    """Build human-readable summary text."""
    severity_emoji = {
        SystemSeverity.GREEN: "🟢",
        SystemSeverity.YELLOW: "🟡",
        SystemSeverity.RED: "🔴"
    }
    
    lines = [
        f"{severity_emoji[severity]} SYSTEM STATUS: {severity.value.upper()}",
        "",
        f"• Database: {components['database'].message}",
        f"• Payments: {components['payments'].message}",
        f"• VPN API: {components['vpn_api'].message}",
        f"• Pending activations: {pending_activations}",
    ]
    
    if last_critical_error:
        lines.append(f"• Last critical error: {last_critical_error}")
    else:
        lines.append("• No critical errors detected")
    
    return "\n".join(lines)


async def get_error_summary_compact() -> List[Dict[str, str]]:
    """
    3. ERROR SUMMARY (NO LOG SPAM)
    
    Get compact, human-readable error summary.
    Deduplicated, actionable issues only.
    
    Returns:
        List of error dicts with: component, reason, impact
    """
    system_state = recalculate_from_runtime()
    errors = system_state.get_error_summary()
    
    # Add pending activations as actionable issue if present
    try:
        import database
        if database.DB_READY:
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                pending_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                ) or 0
                if pending_count > 0:
                    errors.append({
                        "component": "activations",
                        "reason": f"{pending_count} subscriptions pending activation",
                        "impact": "Will auto-complete once VPN API is available"
                    })
    except Exception:
        pass
    
    return errors
