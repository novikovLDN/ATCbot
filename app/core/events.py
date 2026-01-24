"""
Event taxonomy for analytics and data foundation.

This module defines event types and formats for system events
to enable analytics and business intelligence.

IMPORTANT:
- Events are for analytics only (not runtime behavior)
- Events are PII-safe (sensitive data masked)
- Events support correlation via correlation_id
"""

from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional
from enum import Enum
from datetime import datetime
import uuid


class EventType(str, Enum):
    """Types of events in the system"""
    # User lifecycle
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"
    
    # Subscription lifecycle
    SUBSCRIPTION_CREATED = "subscription_created"
    SUBSCRIPTION_ACTIVATED = "subscription_activated"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    SUBSCRIPTION_RENEWED = "subscription_renewed"
    SUBSCRIPTION_CANCELLED = "subscription_cancelled"
    
    # Payment lifecycle
    PAYMENT_INITIATED = "payment_initiated"
    PAYMENT_COMPLETED = "payment_completed"
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_REFUNDED = "payment_refunded"
    
    # VPN lifecycle
    VPN_KEY_CREATED = "vpn_key_created"
    VPN_KEY_REMOVED = "vpn_key_removed"
    VPN_KEY_REISSUED = "vpn_key_reissued"
    
    # Admin actions
    ADMIN_VIP_GRANTED = "admin_vip_granted"
    ADMIN_VIP_REVOKED = "admin_vip_revoked"
    ADMIN_DISCOUNT_CREATED = "admin_discount_created"
    ADMIN_DISCOUNT_DELETED = "admin_discount_deleted"
    ADMIN_USER_BLOCKED = "admin_user_blocked"
    ADMIN_USER_UNBLOCKED = "admin_user_unblocked"
    
    # System state transitions
    SYSTEM_DEGRADED = "system_degraded"
    SYSTEM_RECOVERED = "system_recovered"
    SYSTEM_UNAVAILABLE = "system_unavailable"
    
    # Trial lifecycle
    TRIAL_STARTED = "trial_started"
    TRIAL_COMPLETED = "trial_completed"
    TRIAL_EXPIRED = "trial_expired"


@dataclass
class Event:
    """
    Standard event format for analytics.
    
    All events follow this structure for consistency.
    """
    event_type: EventType
    entity_id: str  # ID of the entity (user_id, subscription_id, etc.)
    timestamp: datetime
    correlation_id: str  # For correlating related events
    metadata: Dict[str, Any]  # PII-safe metadata
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert event to dictionary (for serialization).
        
        Returns:
            Dictionary representation of event
        """
        result = asdict(self)
        result["event_type"] = self.event_type.value
        result["timestamp"] = self.timestamp.isoformat()
        return result


class EventBuilder:
    """
    Builder for creating events with proper formatting.
    
    Ensures events are PII-safe and follow standard format.
    """
    
    @staticmethod
    def create_event(
        event_type: EventType,
        entity_id: str,
        correlation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Event:
        """
        Create a standard event.
        
        Args:
            event_type: Type of event
            entity_id: ID of the entity
            correlation_id: Optional correlation ID (auto-generated if not provided)
            metadata: Optional metadata (must be PII-safe)
            
        Returns:
            Event instance
        """
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())
        
        if metadata is None:
            metadata = {}
        
        # Ensure metadata is PII-safe (no sensitive data)
        sanitized_metadata = EventBuilder._sanitize_metadata(metadata)
        
        return Event(
            event_type=event_type,
            entity_id=entity_id,
            timestamp=datetime.utcnow(),
            correlation_id=correlation_id,
            metadata=sanitized_metadata,
        )
    
    @staticmethod
    def _sanitize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize metadata to remove PII.
        
        Args:
            metadata: Raw metadata
            
        Returns:
            Sanitized metadata
        """
        sanitized = dict(metadata)
        
        # List of fields that should never be in events
        sensitive_fields = [
            "password",
            "token",
            "secret",
            "card_number",
            "cvv",
            "payment_token",
            "vless_link",  # May contain routing info
        ]
        
        # Remove or mask sensitive fields
        for field in sensitive_fields:
            if field in sanitized:
                sanitized[field] = "[REDACTED]"
        
        return sanitized


# Analytics readiness: Event categories for common queries
ANALYTICS_CATEGORIES = {
    "churn": [
        EventType.SUBSCRIPTION_EXPIRED,
        EventType.SUBSCRIPTION_CANCELLED,
        EventType.USER_DELETED,
    ],
    "conversion": [
        EventType.TRIAL_STARTED,
        EventType.SUBSCRIPTION_CREATED,
        EventType.PAYMENT_COMPLETED,
    ],
    "renewal": [
        EventType.SUBSCRIPTION_RENEWED,
        EventType.PAYMENT_COMPLETED,
    ],
    "ltv": [
        EventType.PAYMENT_COMPLETED,
        EventType.SUBSCRIPTION_RENEWED,
    ],
    "failure_rates": [
        EventType.PAYMENT_FAILED,
        EventType.SUBSCRIPTION_EXPIRED,
        EventType.SYSTEM_DEGRADED,
        EventType.SYSTEM_UNAVAILABLE,
    ],
    "retry_amplification": [
        EventType.PAYMENT_FAILED,
        EventType.VPN_KEY_CREATED,  # May indicate retries
    ],
}
