"""
Audit event logging for compliance and forensics.

STEP 5 — COMPLIANCE & AUDITABILITY:
This module provides audit event logging with:
- Canonical audit event structure
- Correlation ID propagation
- Data redaction
- Non-blocking writes
- Append-only, immutable events
"""

import logging
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from app.utils.logging_helpers import get_correlation_id
from app.utils.security import sanitize_for_logging, mask_secret

logger = logging.getLogger(__name__)

# ====================================================================================
# STEP 5 — PART A: AUDIT EVENT MODEL
# ====================================================================================

@dataclass
class AuditEvent:
    """
    Canonical audit event structure.
    
    STEP 5 — PART A: AUDIT EVENT MODEL
    Defines the standard format for all audit events.
    
    Fields:
    - event_type: Type of event (mandatory)
    - actor_id: ID of the actor (user/admin/system) (mandatory)
    - actor_type: Type of actor ("user" | "admin" | "system") (mandatory)
    - target_id: ID of the target resource (optional)
    - target_type: Type of target ("user" | "subscription" | "payment" | "vpn") (optional)
    - timestamp: UTC timestamp (mandatory, auto-generated)
    - correlation_id: Correlation ID for tracing (mandatory)
    - metadata: Additional safe, redacted metadata (optional)
    - decision: Authorization decision ("ALLOW" | "DENY") (optional, for auth events)
    """
    event_type: str
    actor_id: int
    actor_type: str  # "user" | "admin" | "system"
    target_id: Optional[int] = None
    target_type: Optional[str] = None  # "user" | "subscription" | "payment" | "vpn"
    timestamp: Optional[str] = None  # ISO 8601 UTC
    correlation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    decision: Optional[str] = None  # "ALLOW" | "DENY" (for auth events)
    
    def __post_init__(self):
        """Auto-generate timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        
        # Auto-get correlation_id if not provided
        if self.correlation_id is None:
            self.correlation_id = get_correlation_id()
        
        # Redact metadata
        if self.metadata:
            self.metadata = sanitize_for_logging(self.metadata)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON string for storage."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ====================================================================================
# STEP 5 — PART B: WHAT MUST BE AUDITED
# ====================================================================================

# Mandatory audit event types
AUDIT_EVENT_TYPES = {
    # Authentication / Authorization
    "auth_decision_allow": "Authorization decision: ALLOW",
    "auth_decision_deny": "Authorization decision: DENY",
    
    # Payments
    "payment_received": "Payment received from user",
    "payment_verified": "Payment verified",
    "payment_finalized": "Payment finalized (subscription activated)",
    "payment_failed": "Payment finalization failed",
    
    # Subscription lifecycle
    "subscription_created": "Subscription created",
    "subscription_renewed": "Subscription renewed",
    "subscription_expired": "Subscription expired",
    "subscription_disabled": "Subscription disabled",
    
    # VPN lifecycle
    "vpn_uuid_created": "VPN UUID created",
    "vpn_uuid_removed": "VPN UUID removed",
    "vpn_key_reissued": "VPN key reissued",
    
    # Admin actions
    "admin_action": "Admin action (state change affecting users)",
    
    # Background worker side effects
    "worker_side_effect": "Background worker side effect",
}


# ====================================================================================
# STEP 5 — PART D: DATA MINIMIZATION & REDACTION
# ====================================================================================

# Sensitive data fields that must be redacted
SENSITIVE_FIELDS = [
    "token", "password", "secret", "key", "api_key", "api_token",
    "bot_token", "database_url", "admin_telegram_id",
    "vpn_key", "vless_link", "uuid_full",  # Full VPN keys
    "payment_provider_token", "invoice_payload_full",  # Payment identifiers
]

# Fields that are safe to log (preview only)
SAFE_PREVIEW_FIELDS = {
    "uuid": 8,  # First 8 chars
    "vpn_key": 20,  # First 20 chars
    "invoice_payload": 50,  # First 50 chars
}


def redact_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Redact sensitive data from metadata.
    
    STEP 5 — PART D: DATA MINIMIZATION & REDACTION
    Ensures audit logs are safe to export externally.
    
    Args:
        metadata: Metadata dictionary to redact
        
    Returns:
        Redacted metadata dictionary
    """
    if not metadata:
        return {}
    
    redacted = {}
    for key, value in metadata.items():
        key_lower = key.lower()
        
        # Check if field is sensitive
        is_sensitive = any(sensitive in key_lower for sensitive in SENSITIVE_FIELDS)
        
        if is_sensitive:
            # Mask sensitive fields
            if isinstance(value, str):
                redacted[key] = mask_secret(value)
            else:
                redacted[key] = "***REDACTED***"
        elif key in SAFE_PREVIEW_FIELDS and isinstance(value, str):
            # Show preview for safe fields
            max_len = SAFE_PREVIEW_FIELDS[key]
            redacted[key] = value[:max_len] + "..." if len(value) > max_len else value
        elif isinstance(value, (dict, list)):
            # Recursively redact nested structures
            redacted[key] = sanitize_for_logging(value)
        else:
            redacted[key] = value
    
    return redacted


# ====================================================================================
# STEP 5 — PART F: FAILURE SAFETY
# ====================================================================================

async def log_audit_event_safe(
    event: AuditEvent,
    connection: Optional[Any] = None
) -> bool:
    """
    Log audit event safely (non-blocking, best-effort).
    
    STEP 5 — PART F: FAILURE SAFETY
    Audit logging must never break production.
    - Best-effort: Tries to log, but doesn't fail if it can't
    - Non-blocking: Never throws exceptions
    - Logs SECURITY_ERROR if audit write fails
    
    Args:
        event: Audit event to log
        connection: Optional database connection (for atomic writes)
        
    Returns:
        True if logged successfully, False otherwise
    """
    try:
        # Redact metadata before logging
        if event.metadata:
            event.metadata = redact_metadata(event.metadata)
        
        # Import here to avoid circular dependencies
        import database
        
        # Use atomic write if connection provided, otherwise standalone
        if connection:
            await database._log_audit_event_atomic(
                conn=connection,
                action=event.event_type,
                telegram_id=event.actor_id,
                target_user=event.target_id,
                details=event.to_json() if event.metadata else None
            )
        else:
            await database._log_audit_event_atomic_standalone(
                action=event.event_type,
                telegram_id=event.actor_id,
                target_user=event.target_id,
                details=event.to_json() if event.metadata else None,
                correlation_id=event.correlation_id
            )
        
        return True
        
    except Exception as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log SECURITY_ERROR but never throw
        from app.utils.security import log_security_error
        
        log_security_error(
            event="audit_log_write_failed",
            telegram_id=event.actor_id,
            correlation_id=event.correlation_id,
            details={
                "event_type": event.event_type,
                "error": str(e)[:200]
            }
        )
        
        logger.error(
            f"[SECURITY_ERROR] Failed to write audit event: event_type={event.event_type}, "
            f"actor_id={event.actor_id}, error={str(e)[:200]}"
        )
        
        return False


# ====================================================================================
# STEP 5 — PART B: CONVENIENCE FUNCTIONS FOR MANDATORY EVENTS
# ====================================================================================

async def audit_auth_decision(
    decision: str,  # "ALLOW" | "DENY"
    actor_id: int,
    actor_type: str,  # "user" | "admin" | "system"
    target_id: Optional[int] = None,
    target_type: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection: Optional[Any] = None
) -> bool:
    """
    Audit authentication/authorization decision.
    
    STEP 5 — PART B: WHAT MUST BE AUDITED
    All auth decisions must be audited.
    """
    event = AuditEvent(
        event_type=f"auth_decision_{decision.lower()}",
        actor_id=actor_id,
        actor_type=actor_type,
        target_id=target_id,
        target_type=target_type,
        correlation_id=correlation_id,
        metadata=metadata,
        decision=decision
    )
    return await log_audit_event_safe(event, connection)


async def audit_payment_event(
    event_type: str,  # "received" | "verified" | "finalized" | "failed"
    actor_id: int,
    target_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection: Optional[Any] = None
) -> bool:
    """
    Audit payment event.
    
    STEP 5 — PART B: WHAT MUST BE AUDITED
    All payment events must be audited.
    """
    event = AuditEvent(
        event_type=f"payment_{event_type}",
        actor_id=actor_id,
        actor_type="user",
        target_id=target_id,
        target_type="payment",
        correlation_id=correlation_id,
        metadata=metadata
    )
    return await log_audit_event_safe(event, connection)


async def audit_subscription_event(
    event_type: str,  # "created" | "renewed" | "expired" | "disabled"
    actor_id: int,
    target_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection: Optional[Any] = None
) -> bool:
    """
    Audit subscription lifecycle event.
    
    STEP 5 — PART B: WHAT MUST BE AUDITED
    All subscription lifecycle events must be audited.
    """
    event = AuditEvent(
        event_type=f"subscription_{event_type}",
        actor_id=actor_id,
        actor_type="user",
        target_id=target_id,
        target_type="subscription",
        correlation_id=correlation_id,
        metadata=metadata
    )
    return await log_audit_event_safe(event, connection)


async def audit_vpn_event(
    event_type: str,  # "uuid_created" | "uuid_removed" | "key_reissued"
    actor_id: int,
    target_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection: Optional[Any] = None
) -> bool:
    """
    Audit VPN lifecycle event.
    
    STEP 5 — PART B: WHAT MUST BE AUDITED
    All VPN lifecycle events must be audited.
    """
    event = AuditEvent(
        event_type=f"vpn_{event_type}",
        actor_id=actor_id,
        actor_type="user",
        target_id=target_id,
        target_type="vpn",
        correlation_id=correlation_id,
        metadata=metadata
    )
    return await log_audit_event_safe(event, connection)


async def audit_admin_action(
    actor_id: int,
    target_id: Optional[int] = None,
    target_type: Optional[str] = None,
    action_description: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection: Optional[Any] = None
) -> bool:
    """
    Audit admin action.
    
    STEP 5 — PART B: WHAT MUST BE AUDITED
    All admin actions affecting users must be audited.
    """
    event = AuditEvent(
        event_type="admin_action",
        actor_id=actor_id,
        actor_type="admin",
        target_id=target_id,
        target_type=target_type,
        correlation_id=correlation_id,
        metadata={
            **(metadata or {}),
            "action_description": action_description
        }
    )
    return await log_audit_event_safe(event, connection)


async def audit_worker_side_effect(
    worker_name: str,
    actor_id: int,  # Usually system (0) or user ID if applicable
    target_id: Optional[int] = None,
    target_type: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    connection: Optional[Any] = None
) -> bool:
    """
    Audit background worker side effect.
    
    STEP 5 — PART B: WHAT MUST BE AUDITED
    All background worker side effects must be audited.
    """
    event = AuditEvent(
        event_type="worker_side_effect",
        actor_id=actor_id,
        actor_type="system",
        target_id=target_id,
        target_type=target_type,
        correlation_id=correlation_id,
        metadata={
            **(metadata or {}),
            "worker_name": worker_name
        }
    )
    return await log_audit_event_safe(event, connection)
