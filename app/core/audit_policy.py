"""
Audit policy for security, compliance, and incident readiness.

This module defines what should be logged, what should never be logged,
and how to correlate events for incident forensics.

IMPORTANT:
- Audit policy is for guidance, not enforcement
- Policy does NOT block operations
- Policy is for compliance and security readiness
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set
from enum import Enum
from datetime import datetime, timezone
import uuid


class AuditEventType(str, Enum):
    """Types of events that should be audited"""
    PAYMENT_EVENT = "payment_event"
    SUBSCRIPTION_LIFECYCLE = "subscription_lifecycle"
    ADMIN_ACTION = "admin_action"
    SYSTEM_DEGRADATION = "system_degradation"
    SECURITY_EVENT = "security_event"
    TRIAL_ACTIVATION = "trial_activation"
    VPN_LIFECYCLE = "vpn_lifecycle"


class AuditLevel(str, Enum):
    """Audit logging levels"""
    REQUIRED = "required"  # Must be logged
    RECOMMENDED = "recommended"  # Should be logged
    OPTIONAL = "optional"  # May be logged


@dataclass
class AuditPolicy:
    """
    Audit policy definition.
    
    Defines what events should be logged and how.
    """
    event_type: AuditEventType
    audit_level: AuditLevel
    required_fields: List[str]
    sensitive_fields: List[str]  # Fields that should NOT be logged
    retention_days: int = 90  # Default retention period


class AuditPolicyEngine:
    """
    Audit policy engine for compliance and security readiness.
    
    Provides guidance on what to log and what to exclude.
    """
    
    def __init__(self):
        """Initialize audit policy engine"""
        self._policies: Dict[AuditEventType, AuditPolicy] = {
            # Payment events: REQUIRED
            AuditEventType.PAYMENT_EVENT: AuditPolicy(
                event_type=AuditEventType.PAYMENT_EVENT,
                audit_level=AuditLevel.REQUIRED,
                required_fields=["payment_id", "user_id", "amount", "status", "timestamp"],
                sensitive_fields=["card_number", "cvv", "payment_token"],
                retention_days=365,  # 1 year for payment events
            ),
            # Subscription lifecycle: REQUIRED
            AuditEventType.SUBSCRIPTION_LIFECYCLE: AuditPolicy(
                event_type=AuditEventType.SUBSCRIPTION_LIFECYCLE,
                audit_level=AuditLevel.REQUIRED,
                required_fields=["subscription_id", "user_id", "action", "status", "timestamp"],
                sensitive_fields=[],
                retention_days=365,
            ),
            # Admin actions: REQUIRED
            AuditEventType.ADMIN_ACTION: AuditPolicy(
                event_type=AuditEventType.ADMIN_ACTION,
                audit_level=AuditLevel.REQUIRED,
                required_fields=["admin_id", "action", "target_user_id", "timestamp"],
                sensitive_fields=[],
                retention_days=365,
            ),
            # System degradation: REQUIRED
            AuditEventType.SYSTEM_DEGRADATION: AuditPolicy(
                event_type=AuditEventType.SYSTEM_DEGRADATION,
                audit_level=AuditLevel.REQUIRED,
                required_fields=["component", "status", "transition", "timestamp"],
                sensitive_fields=[],
                retention_days=90,
            ),
            # Security events: REQUIRED
            AuditEventType.SECURITY_EVENT: AuditPolicy(
                event_type=AuditEventType.SECURITY_EVENT,
                audit_level=AuditLevel.REQUIRED,
                required_fields=["event_type", "severity", "timestamp"],
                sensitive_fields=["password", "token", "secret"],
                retention_days=365,
            ),
            # Trial activation: RECOMMENDED
            AuditEventType.TRIAL_ACTIVATION: AuditPolicy(
                event_type=AuditEventType.TRIAL_ACTIVATION,
                audit_level=AuditLevel.RECOMMENDED,
                required_fields=["user_id", "trial_id", "status", "timestamp"],
                sensitive_fields=[],
                retention_days=90,
            ),
            # VPN lifecycle: RECOMMENDED
            AuditEventType.VPN_LIFECYCLE: AuditPolicy(
                event_type=AuditEventType.VPN_LIFECYCLE,
                audit_level=AuditLevel.RECOMMENDED,
                required_fields=["user_id", "uuid", "action", "timestamp"],
                sensitive_fields=["vless_link"],  # May contain sensitive routing info
                retention_days=90,
            ),
        }
    
    def get_policy(self, event_type: AuditEventType) -> AuditPolicy:
        """
        Get audit policy for event type.
        
        Args:
            event_type: Type of audit event
            
        Returns:
            AuditPolicy for the event type
        """
        return self._policies.get(
            event_type,
            AuditPolicy(
                event_type=event_type,
                audit_level=AuditLevel.OPTIONAL,
                required_fields=["timestamp"],
                sensitive_fields=[],
            )
        )
    
    def should_audit(self, event_type: AuditEventType) -> bool:
        """
        Check if event type should be audited.
        
        Args:
            event_type: Type of audit event
            
        Returns:
            True if should be audited, False otherwise
        """
        policy = self.get_policy(event_type)
        return policy.audit_level in (AuditLevel.REQUIRED, AuditLevel.RECOMMENDED)
    
    def sanitize_for_audit(
        self,
        event_type: AuditEventType,
        data: Dict
    ) -> Dict:
        """
        Sanitize data for audit logging (remove sensitive fields).
        
        Args:
            event_type: Type of audit event
            data: Data to sanitize
            
        Returns:
            Sanitized data dictionary
        """
        policy = self.get_policy(event_type)
        sanitized = dict(data)
        
        # Remove sensitive fields
        for field in policy.sensitive_fields:
            if field in sanitized:
                sanitized[field] = "[REDACTED]"
        
        return sanitized


class IncidentContext:
    """
    Incident context for correlation and forensics.
    
    Provides incident ID and correlation for tracking degradation episodes.
    """
    
    def __init__(self):
        """Initialize incident context"""
        self._current_incident_id: Optional[str] = None
        self._incident_start_time: Optional[datetime] = None
    
    def start_incident(self) -> str:
        """
        Start a new incident context.
        
        Returns:
            Incident ID (UUID)
        """
        self._current_incident_id = str(uuid.uuid4())
        self._incident_start_time = datetime.now(timezone.utc)
        return self._current_incident_id
    
    def get_incident_id(self) -> Optional[str]:
        """Get current incident ID"""
        return self._current_incident_id
    
    def clear_incident(self) -> None:
        """Clear current incident context"""
        self._current_incident_id = None
        self._incident_start_time = None
    
    def get_correlation_id(self) -> str:
        """
        Get correlation ID for current context.
        
        Returns:
            Incident ID if active, or new UUID otherwise
        """
        if self._current_incident_id:
            return self._current_incident_id
        return str(uuid.uuid4())


# Global singleton instances
_audit_policy_engine: Optional[AuditPolicyEngine] = None
_incident_context: Optional[IncidentContext] = None


def get_audit_policy_engine() -> AuditPolicyEngine:
    """
    Get or create global audit policy engine instance.
    
    Returns:
        Global AuditPolicyEngine instance
    """
    global _audit_policy_engine
    
    if _audit_policy_engine is None:
        _audit_policy_engine = AuditPolicyEngine()
    
    return _audit_policy_engine


def get_incident_context() -> IncidentContext:
    """
    Get or create global incident context instance.
    
    Returns:
        Global IncidentContext instance
    """
    global _incident_context
    
    if _incident_context is None:
        _incident_context = IncidentContext()
    
    return _incident_context
