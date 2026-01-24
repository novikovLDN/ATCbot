"""
Trial Service Layer

This module provides business logic for trial subscriptions, notifications, and expiration.
It acts as a thin wrapper around database operations, providing a clean interface
for handlers and background tasks while keeping business logic separate from Telegram-specific code.

All functions are pure business logic:
- No aiogram imports
- No logging
- No Telegram calls
- Pure business logic only
"""

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
import database


# ====================================================================================
# Domain Exceptions
# ====================================================================================

class TrialServiceError(Exception):
    """Base exception for trial service errors"""
    pass


class TrialExpiredError(TrialServiceError):
    """Raised when trial has already expired"""
    pass


class InvalidTrialStateError(TrialServiceError):
    """Raised when trial state is invalid for the operation"""
    pass


# ====================================================================================
# Trial Expiration Logic
# ====================================================================================

async def is_trial_expired(
    telegram_id: int,
    trial_expires_at: datetime,
    now: datetime
) -> bool:
    """
    Check if trial has expired.
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        now: Current timestamp
        
    Returns:
        True if trial has expired, False otherwise
    """
    if not trial_expires_at:
        return True
    
    return trial_expires_at <= now


async def should_expire_trial(
    telegram_id: int,
    trial_expires_at: datetime,
    now: datetime,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if trial should be expired and processed.
    
    Checks:
    - Trial has expired (trial_expires_at <= now)
    - Trial expired within last 24 hours (to prevent duplicate processing)
    - User has active trial subscription
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        now: Current timestamp
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if trial should be expired
        - (False, reason) if trial should not be expired
    """
    if not trial_expires_at:
        return (False, "trial_expires_at_is_none")
    
    # Check if trial has expired
    if trial_expires_at > now:
        return (False, "trial_not_expired")
    
    # Check if trial expired within last 24 hours (prevent duplicate processing)
    if trial_expires_at <= now - timedelta(hours=24):
        return (False, "trial_expired_too_long_ago")
    
    # Check if user has active trial subscription
    subscription = await conn.fetchrow("""
        SELECT id, source, status, expires_at, uuid
        FROM subscriptions
        WHERE telegram_id = $1
        AND source = 'trial'
        AND status = 'active'
        LIMIT 1
    """, telegram_id)
    
    if not subscription:
        return (False, "no_active_trial_subscription")
    
    return (True, None)


# ====================================================================================
# Trial Notification Logic
# ====================================================================================

def calculate_trial_timing(
    trial_expires_at: datetime,
    now: datetime
) -> Dict[str, float]:
    """
    Calculate trial timing metrics.
    
    Args:
        trial_expires_at: Trial expiration timestamp
        now: Current timestamp
        
    Returns:
        {
            "hours_until_expiry": float,
            "hours_since_activation": float
        }
    """
    if not trial_expires_at:
        return {
            "hours_until_expiry": 0.0,
            "hours_since_activation": 0.0
        }
    
    time_until_expiry = trial_expires_at - now
    hours_until_expiry = time_until_expiry.total_seconds() / 3600
    
    # Calculate hours since activation (trial is 72 hours)
    # hours_since_activation = 72 - hours_until_expiry
    hours_since_activation = 72 - hours_until_expiry
    
    return {
        "hours_until_expiry": max(0.0, hours_until_expiry),
        "hours_since_activation": max(0.0, hours_since_activation)
    }


async def should_send_notification(
    telegram_id: int,
    trial_expires_at: datetime,
    subscription_expires_at: datetime,
    notification_schedule: Dict[str, Any],
    notification_flags: Dict[str, bool],
    now: datetime,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if a trial notification should be sent.
    
    Checks:
    - Subscription is still active
    - User doesn't have active paid subscription
    - Notification timing matches schedule
    - Notification hasn't been sent yet (idempotency)
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        subscription_expires_at: Subscription expiration timestamp
        notification_schedule: Notification schedule entry with "hours", "key", "has_button", "db_flag"
        notification_flags: Dictionary of notification flags (e.g., {"trial_notif_6h_sent": False})
        now: Current timestamp
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if notification should be sent
        - (False, reason) if notification should not be sent
    """
    # Check if subscription is still active
    if subscription_expires_at <= now:
        return (False, "subscription_expired")
    
    # Check if user has active paid subscription
    paid_subscription = await conn.fetchrow("""
        SELECT 1 FROM subscriptions 
        WHERE telegram_id = $1 
        AND source = 'payment'
        AND status = 'active'
        AND expires_at > $2
        LIMIT 1
    """, telegram_id, now)
    
    if paid_subscription:
        return (False, "has_active_paid_subscription")
    
    # Calculate timing
    timing = calculate_trial_timing(trial_expires_at, now)
    hours_until_expiry = timing["hours_until_expiry"]
    hours_since_activation = timing["hours_since_activation"]
    
    # Check notification schedule
    hours = notification_schedule["hours"]
    db_flag = notification_schedule.get("db_flag", f"trial_notif_{hours}h_sent")
    
    # Check if already sent
    already_sent = notification_flags.get(db_flag, False)
    if already_sent:
        return (False, "already_sent")
    
    # Check timing window (within 1 hour after scheduled time)
    if hours_since_activation < hours:
        return (False, "too_early")
    
    if hours_since_activation >= hours + 1:
        return (False, "too_late")
    
    # Don't send if too close to expiry (final reminder handles that)
    if hours_until_expiry <= 6:
        return (False, "too_close_to_expiry")
    
    return (True, None)


async def should_send_final_reminder(
    telegram_id: int,
    trial_expires_at: datetime,
    subscription_expires_at: datetime,
    final_reminder_sent: bool,
    now: datetime,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if final reminder (6h before expiry) should be sent.
    
    Args:
        telegram_id: Telegram ID of the user
        trial_expires_at: Trial expiration timestamp
        subscription_expires_at: Subscription expiration timestamp
        final_reminder_sent: Whether final reminder was already sent
        now: Current timestamp
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if final reminder should be sent
        - (False, reason) if final reminder should not be sent
    """
    # Check if already sent
    if final_reminder_sent:
        return (False, "already_sent")
    
    # Check if subscription is still active
    if subscription_expires_at <= now:
        return (False, "subscription_expired")
    
    # Check if user has active paid subscription
    paid_subscription = await conn.fetchrow("""
        SELECT 1 FROM subscriptions 
        WHERE telegram_id = $1 
        AND source = 'payment'
        AND status = 'active'
        AND expires_at > $2
        LIMIT 1
    """, telegram_id, now)
    
    if paid_subscription:
        return (False, "has_active_paid_subscription")
    
    # Calculate timing
    timing = calculate_trial_timing(trial_expires_at, now)
    hours_until_expiry = timing["hours_until_expiry"]
    
    # Final reminder: 6 hours before expiry (between 6h and 5h remaining)
    if hours_until_expiry > 6:
        return (False, "too_early")
    
    if hours_until_expiry <= 5:
        return (False, "too_late")
    
    return (True, None)


# ====================================================================================
# Trial Completion Logic
# ====================================================================================

async def mark_trial_completed(
    telegram_id: int,
    conn
) -> bool:
    """
    Mark trial as completed (idempotent).
    
    Updates trial_completed_sent flag only if it was False.
    This ensures idempotency - multiple calls won't cause duplicate notifications.
    
    Args:
        telegram_id: Telegram ID of the user
        conn: Database connection
        
    Returns:
        True if flag was updated (notification should be sent),
        False if flag was already set (notification already sent)
    """
    result = await conn.execute("""
        UPDATE users 
        SET trial_completed_sent = TRUE 
        WHERE telegram_id = $1 
        AND trial_completed_sent = FALSE
    """, telegram_id)
    
    # asyncpg execute returns string like "UPDATE 1" or "UPDATE 0"
    return "1" in result


async def should_send_completion_notification(
    telegram_id: int,
    conn
) -> Tuple[bool, Optional[str]]:
    """
    Determine if trial completion notification should be sent.
    
    Checks:
    - User has used trial (trial_used_at IS NOT NULL)
    - Trial completion notification hasn't been sent yet
    
    Args:
        telegram_id: Telegram ID of the user
        conn: Database connection
        
    Returns:
        Tuple[bool, Optional[str]]:
        - (True, None) if notification should be sent
        - (False, reason) if notification should not be sent
    """
    user = await conn.fetchrow("""
        SELECT trial_used_at, trial_completed_sent
        FROM users
        WHERE telegram_id = $1
    """, telegram_id)
    
    if not user:
        return (False, "user_not_found")
    
    if not user["trial_used_at"]:
        return (False, "trial_not_used")
    
    if user["trial_completed_sent"]:
        return (False, "already_sent")
    
    return (True, None)


# ====================================================================================
# Notification Payload Preparation
# ====================================================================================

def prepare_notification_payload(
    notification_key: str,
    has_button: bool = False
) -> Dict[str, Any]:
    """
    Prepare notification payload for sending.
    
    This is a pure function that prepares the data structure for notification sending.
    Actual Telegram sending is done by the caller.
    
    Args:
        notification_key: Localization key for notification text
        has_button: Whether notification should include a button
        
    Returns:
        {
            "notification_key": str,
            "has_button": bool,
            "button_callback": Optional[str]  # "menu_buy_vpn" if has_button
        }
    """
    return {
        "notification_key": notification_key,
        "has_button": has_button,
        "button_callback": "menu_buy_vpn" if has_button else None
    }


def get_notification_schedule() -> List[Dict[str, Any]]:
    """
    Get trial notification schedule.
    
    Returns:
        List of notification schedule entries with:
        - hours: Hours since activation
        - key: Localization key
        - has_button: Whether to show button
        - db_flag: Database flag name (optional)
    """
    return [
        {"hours": 6, "key": "trial_notification_6h", "has_button": False},
        {"hours": 48, "key": "trial_notification_60h", "has_button": True, "db_flag": "trial_notif_60h_sent"},
    ]


def get_final_reminder_config() -> Dict[str, Any]:
    """
    Get final reminder configuration (6h before expiry).
    
    Returns:
        {
            "hours_before_expiry": 6,
            "notification_key": str,
            "has_button": bool,
            "db_flag": str
        }
    """
    return {
        "hours_before_expiry": 6,
        "notification_key": "trial_notification_71h",
        "has_button": True,
        "db_flag": "trial_notif_71h_sent"
    }
