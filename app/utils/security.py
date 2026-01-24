"""
Security utilities for trust boundaries and input validation.

STEP 4 — SECURITY & TRUST BOUNDARIES:
This module provides security utilities for:
- Input validation (type, length, format)
- Authorization guards
- Security logging
- Secret masking
"""

import logging
import re
from typing import Optional, Tuple, Any, Dict
from functools import wraps

logger = logging.getLogger(__name__)

# ====================================================================================
# STEP 4 — PART A: INPUT TRUST BOUNDARIES
# ====================================================================================

# Input validation limits
MAX_MESSAGE_LENGTH = 4096  # Telegram message limit
MAX_CALLBACK_DATA_LENGTH = 64  # Telegram callback_data limit
MAX_PAYLOAD_LENGTH = 256  # Payment payload limit
MAX_PROMO_CODE_LENGTH = 50  # Promo code length limit
MAX_USERNAME_LENGTH = 100  # Username length limit

# Allowed callback data patterns
ALLOWED_CALLBACK_PATTERNS = [
    r"^menu_(main|profile|buy_vpn|instruction|referral|about|support)$",
    r"^lang_(ru|en|uz|tj)$",
    r"^tariff:(basic|plus)$",
    r"^period:\d+$",
    r"^payment_method:(balance|card)$",
    r"^toggle_auto_renew:(on|off)$",
    r"^topup_balance$",
    r"^activate_trial$",
    r"^enter_promo$",
    r"^admin_.*$",  # Admin actions (validated separately)
]


def validate_telegram_id(telegram_id: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate Telegram ID.
    
    STEP 4 — PART A: INPUT TRUST BOUNDARIES
    Type validation and range checks.
    
    Args:
        telegram_id: Telegram ID to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(telegram_id, int):
        try:
            telegram_id = int(telegram_id)
        except (ValueError, TypeError):
            return False, "Telegram ID must be an integer"
    
    # Telegram IDs are positive integers
    if telegram_id <= 0:
        return False, "Telegram ID must be positive"
    
    # Reasonable upper bound (Telegram user IDs are typically < 2^63)
    if telegram_id > 2**63:
        return False, "Telegram ID exceeds maximum value"
    
    return True, None


def validate_message_text(text: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate Telegram message text.
    
    STEP 4 — PART A: INPUT TRUST BOUNDARIES
    Length and format validation.
    
    Args:
        text: Message text to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if text is None:
        return True, None  # None is valid (e.g., photo messages)
    
    if not isinstance(text, str):
        return False, "Message text must be a string"
    
    if len(text) > MAX_MESSAGE_LENGTH:
        return False, f"Message text exceeds maximum length ({MAX_MESSAGE_LENGTH})"
    
    return True, None


def validate_callback_data(callback_data: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate callback data.
    
    STEP 4 — PART A: INPUT TRUST BOUNDARIES
    Length and format validation against allowed patterns.
    
    Args:
        callback_data: Callback data to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if callback_data is None:
        return False, "Callback data cannot be None"
    
    if not isinstance(callback_data, str):
        return False, "Callback data must be a string"
    
    if len(callback_data) > MAX_CALLBACK_DATA_LENGTH:
        return False, f"Callback data exceeds maximum length ({MAX_CALLBACK_DATA_LENGTH})"
    
    # Check against allowed patterns
    for pattern in ALLOWED_CALLBACK_PATTERNS:
        if re.match(pattern, callback_data):
            return True, None
    
    # Admin callbacks are validated separately (authorization check)
    if callback_data.startswith("admin_"):
        return True, None  # Will be validated by authorization guard
    
    return False, f"Callback data does not match allowed patterns: {callback_data[:50]}"


def validate_payment_payload(payload: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate payment payload.
    
    STEP 4 — PART A: INPUT TRUST BOUNDARIES
    Length and format validation.
    
    Args:
        payload: Payment payload to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if payload is None or not payload:
        return False, "Payment payload cannot be empty"
    
    if not isinstance(payload, str):
        return False, "Payment payload must be a string"
    
    if len(payload) > MAX_PAYLOAD_LENGTH:
        return False, f"Payment payload exceeds maximum length ({MAX_PAYLOAD_LENGTH})"
    
    # Basic format check (more detailed validation in payment service)
    if not payload.strip():
        return False, "Payment payload cannot be whitespace only"
    
    return True, None


def validate_promo_code(promo_code: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validate promo code.
    
    STEP 4 — PART A: INPUT TRUST BOUNDARIES
    Length and format validation.
    
    Args:
        promo_code: Promo code to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if promo_code is None or not promo_code:
        return False, "Promo code cannot be empty"
    
    if not isinstance(promo_code, str):
        return False, "Promo code must be a string"
    
    if len(promo_code) > MAX_PROMO_CODE_LENGTH:
        return False, f"Promo code exceeds maximum length ({MAX_PROMO_CODE_LENGTH})"
    
    # Alphanumeric and underscore only
    if not re.match(r"^[A-Za-z0-9_]+$", promo_code):
        return False, "Promo code contains invalid characters"
    
    return True, None


# ====================================================================================
# STEP 4 — PART B: AUTHORIZATION GUARDS
# ====================================================================================

def is_admin(telegram_id: int) -> bool:
    """
    Check if user is admin.
    
    STEP 4 — PART B: AUTHORIZATION GUARDS
    Explicit authorization check - fail closed.
    
    Args:
        telegram_id: Telegram ID to check
        
    Returns:
        True if user is admin, False otherwise
    """
    import config
    
    # Validate telegram_id first
    is_valid, error = validate_telegram_id(telegram_id)
    if not is_valid:
        logger.warning(f"[SECURITY_WARNING] Invalid telegram_id in is_admin check: {error}")
        return False
    
    return telegram_id == config.ADMIN_TELEGRAM_ID


def require_admin(telegram_id: int) -> Tuple[bool, Optional[str]]:
    """
    Require admin authorization.
    
    STEP 4 — PART B: AUTHORIZATION GUARDS
    Explicit guard that fails closed.
    
    Args:
        telegram_id: Telegram ID to check
        
    Returns:
        Tuple of (is_authorized, error_message)
    """
    if not is_admin(telegram_id):
        logger.warning(
            f"[SECURITY_WARNING] Unauthorized admin access attempt: telegram_id={telegram_id}"
        )
        return False, "Access denied"
    
    return True, None


def owns_resource(telegram_id: int, resource_telegram_id: int) -> bool:
    """
    Check if user owns the resource.
    
    STEP 4 — PART B: AUTHORIZATION GUARDS
    Explicit ownership check - fail closed.
    
    Args:
        telegram_id: User's Telegram ID
        resource_telegram_id: Resource owner's Telegram ID
        
    Returns:
        True if user owns resource, False otherwise
    """
    # Validate both IDs
    is_valid1, error1 = validate_telegram_id(telegram_id)
    is_valid2, error2 = validate_telegram_id(resource_telegram_id)
    
    if not is_valid1 or not is_valid2:
        logger.warning(
            f"[SECURITY_WARNING] Invalid telegram_id in owns_resource check: "
            f"telegram_id={telegram_id}, resource_telegram_id={resource_telegram_id}, "
            f"errors=({error1}, {error2})"
        )
        return False
    
    return telegram_id == resource_telegram_id


def require_ownership(telegram_id: int, resource_telegram_id: int) -> Tuple[bool, Optional[str]]:
    """
    Require resource ownership.
    
    STEP 4 — PART B: AUTHORIZATION GUARDS
    Explicit guard that fails closed.
    
    Args:
        telegram_id: User's Telegram ID
        resource_telegram_id: Resource owner's Telegram ID
        
    Returns:
        Tuple of (is_authorized, error_message)
    """
    if not owns_resource(telegram_id, resource_telegram_id):
        logger.warning(
            f"[SECURITY_WARNING] Unauthorized resource access attempt: "
            f"telegram_id={telegram_id}, resource_telegram_id={resource_telegram_id}"
        )
        return False, "Access denied"
    
    return True, None


# ====================================================================================
# STEP 4 — PART E: SECRET & CONFIG SAFETY
# ====================================================================================

def mask_secret(secret: Optional[str], visible_chars: int = 4) -> str:
    """
    Mask secret in logs.
    
    STEP 4 — PART E: SECRET & CONFIG SAFETY
    Prevents accidental secret exposure in logs.
    
    Args:
        secret: Secret to mask
        visible_chars: Number of characters to show at the end
        
    Returns:
        Masked secret (e.g., "****token1234")
    """
    if not secret:
        return "****"
    
    if len(secret) <= visible_chars:
        return "****"
    
    return "*" * (len(secret) - visible_chars) + secret[-visible_chars:]


def sanitize_for_logging(data: Any, sensitive_keys: Optional[list] = None) -> Any:
    """
    Sanitize data for logging (remove secrets).
    
    STEP 4 — PART E: SECRET & CONFIG SAFETY
    Removes or masks sensitive data from log entries.
    
    Args:
        data: Data to sanitize
        sensitive_keys: List of keys to mask (default: common secret keys)
        
    Returns:
        Sanitized data
    """
    if sensitive_keys is None:
        sensitive_keys = [
            "token", "password", "secret", "key", "api_key", "api_token",
            "bot_token", "database_url", "admin_telegram_id"
        ]
    
    if isinstance(data, dict):
        sanitized = {}
        for key, value in data.items():
            key_lower = key.lower()
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                sanitized[key] = mask_secret(str(value))
            elif isinstance(value, (dict, list)):
                sanitized[key] = sanitize_for_logging(value, sensitive_keys)
            else:
                sanitized[key] = value
        return sanitized
    
    if isinstance(data, list):
        return [sanitize_for_logging(item, sensitive_keys) for item in data]
    
    return data


# ====================================================================================
# STEP 4 — PART F: SECURITY LOGGING POLICY
# ====================================================================================

def log_security_warning(
    event: str,
    telegram_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
):
    """
    Log security warning.
    
    STEP 4 — PART F: SECURITY LOGGING POLICY
    Logs security-related warnings (unauthorized access, invalid input, etc.)
    
    Args:
        event: Security event description
        telegram_id: Telegram ID (if applicable)
        correlation_id: Correlation ID for tracing
        details: Additional details (will be sanitized)
    """
    log_data = {
        "event": event,
        "telegram_id": telegram_id,
        "correlation_id": correlation_id,
    }
    
    if details:
        log_data["details"] = sanitize_for_logging(details)
    
    logger.warning(f"[SECURITY_WARNING] {event}", extra=log_data)


def log_security_error(
    event: str,
    telegram_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
):
    """
    Log security error.
    
    STEP 4 — PART F: SECURITY LOGGING POLICY
    Logs security-related errors (critical failures, attacks, etc.)
    
    Args:
        event: Security event description
        telegram_id: Telegram ID (if applicable)
        correlation_id: Correlation ID for tracing
        details: Additional details (will be sanitized)
    """
    log_data = {
        "event": event,
        "telegram_id": telegram_id,
        "correlation_id": correlation_id,
    }
    
    if details:
        log_data["details"] = sanitize_for_logging(details)
    
    logger.error(f"[SECURITY_ERROR] {event}", extra=log_data)


def log_audit_event(
    event: str,
    telegram_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
):
    """
    Log audit event.
    
    STEP 4 — PART F: SECURITY LOGGING POLICY
    Logs audit events (admin actions, payment finalization, etc.)
    
    Args:
        event: Audit event description
        telegram_id: Telegram ID (if applicable)
        correlation_id: Correlation ID for tracing
        details: Additional details (will be sanitized)
    """
    log_data = {
        "event": event,
        "telegram_id": telegram_id,
        "correlation_id": correlation_id,
    }
    
    if details:
        log_data["details"] = sanitize_for_logging(details)
    
    logger.info(f"[AUDIT_EVENT] {event}", extra=log_data)
