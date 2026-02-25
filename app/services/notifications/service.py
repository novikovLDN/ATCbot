"""
Notification Service Layer

This module provides business logic for notifications, reminders, and idempotency checks.
It handles all decisions about when to send notifications and when to skip them.

All functions are pure business logic:
- No aiogram imports
- No logging
- No Telegram calls
- Pure business logic only
"""

import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import Enum

import database
from app.services.notifications.exceptions import (
    NotificationServiceError,
    NotificationAlreadySentError,
    InvalidReminderTypeError,
    ReminderNotApplicableError,
)

logger = logging.getLogger(__name__)


# ====================================================================================
# Reminder Types
# ====================================================================================

class ReminderType(Enum):
    """Types of reminders"""
    REMINDER_3D = "reminder_3d"  # 3 days before expiry (paid subscriptions)
    REMINDER_24H = "reminder_24h"  # 24 hours before expiry
    REMINDER_3H = "reminder_3h"  # 3 hours before expiry (paid subscriptions)
    REMINDER_6H = "reminder_6h"  # 6 hours before expiry (admin 1-day grants)
    ADMIN_1DAY_6H = "admin_1day_6h"  # 6 hours before expiry (admin 1-day grants)
    ADMIN_7DAYS_24H = "admin_7days_24h"  # 24 hours before expiry (admin 7-day grants)


# ====================================================================================
# Result Types
# ====================================================================================

@dataclass
class ReminderDecision:
    """Decision about whether to send a reminder"""
    should_send: bool
    reminder_type: Optional[ReminderType]
    reason: Optional[str] = None  # Reason if should_send is False


# ====================================================================================
# Reminder Time Calculations
# ====================================================================================

def calculate_time_until_expiry(expires_at: datetime, now: Optional[datetime] = None) -> timedelta:
    """
    Calculate time until subscription expiry.
    
    Args:
        expires_at: Subscription expiration date
        now: Current time (defaults to datetime.now(timezone.utc))
        
    Returns:
        timedelta until expiry
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    return expires_at - now


def is_within_time_window(
    time_until_expiry: timedelta,
    target_duration: timedelta,
    tolerance: timedelta
) -> bool:
    """
    Check if time until expiry is within a time window.
    
    Args:
        time_until_expiry: Time until subscription expires
        target_duration: Target duration (e.g., timedelta(days=3))
        tolerance: Tolerance window (e.g., timedelta(hours=2))
        
    Returns:
        True if within window, False otherwise
    """
    lower_bound = target_duration - tolerance
    upper_bound = target_duration + tolerance
    
    return lower_bound <= time_until_expiry <= upper_bound


# ====================================================================================
# Reminder Decision Logic
# ====================================================================================

def should_send_reminder(
    subscription: Dict[str, Any],
    now: Optional[datetime] = None
) -> ReminderDecision:
    """
    Determine if a reminder should be sent for a subscription.
    
    This function implements all business rules for reminders:
    - Different rules for admin grants vs paid subscriptions
    - Time window checks
    - Idempotency checks (already sent flags)
    
    Args:
        subscription: Subscription dictionary from database
        now: Current time (defaults to datetime.now(timezone.utc))
        
    Returns:
        ReminderDecision with should_send flag and reminder type
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    expires_at = subscription.get("expires_at")
    if not expires_at:
        return ReminderDecision(
            should_send=False,
            reminder_type=None,
            reason="Subscription has no expiration date"
        )
    
    # Parse expires_at if it's a string
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        except Exception as e:
            logger.debug("Invalid expiration date format: %s", e)
            return ReminderDecision(
                should_send=False,
                reminder_type=None,
                reason="Invalid expiration date format"
            )
    
    # Check if subscription has expired
    if expires_at <= now:
        return ReminderDecision(
            should_send=False,
            reminder_type=None,
            reason="Subscription has already expired"
        )
    
    time_until_expiry = calculate_time_until_expiry(expires_at, now)
    
    # Determine subscription type
    admin_grant_days = subscription.get("admin_grant_days")
    last_action_type = subscription.get("last_action_type")
    is_admin_grant = admin_grant_days is not None or last_action_type == "admin_grant"
    
    # ADMIN-GRANTED ACCESS
    if is_admin_grant:
        if admin_grant_days == 1:
            # 1 day - reminder at 6 hours
            if is_within_time_window(time_until_expiry, timedelta(hours=6), timedelta(hours=0.5)):
                # Check idempotency
                if subscription.get("reminder_6h_sent", False):
                    return ReminderDecision(
                        should_send=False,
                        reminder_type=ReminderType.ADMIN_1DAY_6H,
                        reason="Reminder already sent (reminder_6h_sent flag)"
                    )
                
                return ReminderDecision(
                    should_send=True,
                    reminder_type=ReminderType.ADMIN_1DAY_6H
                )
        
        elif admin_grant_days == 7:
            # 7 days - reminder at 24 hours
            if is_within_time_window(time_until_expiry, timedelta(hours=24), timedelta(hours=1)):
                # Check idempotency
                if subscription.get("reminder_24h_sent", False):
                    return ReminderDecision(
                        should_send=False,
                        reminder_type=ReminderType.ADMIN_7DAYS_24H,
                        reason="Reminder already sent (reminder_24h_sent flag)"
                    )
                
                return ReminderDecision(
                    should_send=True,
                    reminder_type=ReminderType.ADMIN_7DAYS_24H
                )
    
    # PAID SUBSCRIPTIONS
    else:
        # Reminder at 3 days
        if is_within_time_window(time_until_expiry, timedelta(days=3), timedelta(hours=2.4)):
            # Check idempotency
            if subscription.get("reminder_3d_sent", False):
                return ReminderDecision(
                    should_send=False,
                    reminder_type=ReminderType.REMINDER_3D,
                    reason="Reminder already sent (reminder_3d_sent flag)"
                )
            
            return ReminderDecision(
                should_send=True,
                reminder_type=ReminderType.REMINDER_3D
            )
        
        # Reminder at 24 hours
        elif is_within_time_window(time_until_expiry, timedelta(hours=24), timedelta(hours=1)):
            # Check idempotency
            if subscription.get("reminder_24h_sent", False):
                return ReminderDecision(
                    should_send=False,
                    reminder_type=ReminderType.REMINDER_24H,
                    reason="Reminder already sent (reminder_24h_sent flag)"
                )
            
            return ReminderDecision(
                should_send=True,
                reminder_type=ReminderType.REMINDER_24H
            )
        
        # Reminder at 3 hours
        elif is_within_time_window(time_until_expiry, timedelta(hours=3), timedelta(hours=0.5)):
            # Check idempotency
            if subscription.get("reminder_3h_sent", False):
                return ReminderDecision(
                    should_send=False,
                    reminder_type=ReminderType.REMINDER_3H,
                    reason="Reminder already sent (reminder_3h_sent flag)"
                )
            
            return ReminderDecision(
                should_send=True,
                reminder_type=ReminderType.REMINDER_3H
            )
    
    # No reminder should be sent
    return ReminderDecision(
        should_send=False,
        reminder_type=None,
        reason="Not within any reminder time window"
    )


def get_reminder_flag_name(reminder_type: ReminderType) -> str:
    """
    Get database flag name for a reminder type.
    
    Args:
        reminder_type: Type of reminder
        
    Returns:
        Database flag name (e.g., "reminder_3d_sent")
    """
    mapping = {
        ReminderType.REMINDER_3D: "reminder_3d_sent",
        ReminderType.REMINDER_24H: "reminder_24h_sent",
        ReminderType.REMINDER_3H: "reminder_3h_sent",
        ReminderType.REMINDER_6H: "reminder_6h_sent",
        ReminderType.ADMIN_1DAY_6H: "reminder_6h_sent",
        ReminderType.ADMIN_7DAYS_24H: "reminder_24h_sent",
    }
    
    return mapping.get(reminder_type, "reminder_sent")


# ====================================================================================
# Payment Notification Idempotency
# ====================================================================================

async def check_notification_idempotency(
    payment_id: int,
    conn: Optional[Any] = None
) -> bool:
    """
    Check if payment notification has already been sent (idempotency check).
    
    Args:
        payment_id: Payment ID
        conn: Database connection (if None, creates new connection)
        
    Returns:
        True if notification already sent, False otherwise
    """
    return await database.is_payment_notification_sent(payment_id, conn=conn)


async def mark_notification_sent(
    payment_id: int,
    conn: Optional[Any] = None
) -> bool:
    """
    Mark payment notification as sent (idempotency).
    
    Args:
        payment_id: Payment ID
        conn: Database connection (if None, creates new connection)
        
    Returns:
        True if marked successfully, False if already marked
    """
    return await database.mark_payment_notification_sent(payment_id, conn=conn)


async def mark_reminder_sent(
    telegram_id: int,
    reminder_type: ReminderType,
    conn: Optional[Any] = None
) -> None:
    """
    Mark reminder as sent for a user.
    
    Args:
        telegram_id: Telegram ID of the user
        reminder_type: Type of reminder that was sent
        conn: Database connection (if None, creates new connection)
    """
    flag_name = get_reminder_flag_name(reminder_type)
    
    if conn is None:
        await database.mark_reminder_flag_sent(telegram_id, flag_name)
    else:
        # Validate flag_name against whitelist (SQL injection protection)
        if flag_name not in database._ALLOWED_REMINDER_FLAGS:
            raise ValueError(
                f"Invalid flag_name '{flag_name}'. "
                f"Allowed: {sorted(database._ALLOWED_REMINDER_FLAGS)}"
            )
        await conn.execute(
            f"UPDATE subscriptions SET {flag_name} = TRUE WHERE telegram_id = $1",
            telegram_id
        )


# ====================================================================================
# Referral Notification Logic
# ====================================================================================

def format_referral_notification_text(
    referred_username: Optional[str],
    referred_id: int,
    purchase_amount: float,
    cashback_amount: float,
    cashback_percent: int,
    paid_referrals_count: int,
    referrals_needed: int,
    action_type: str,
    subscription_period: Optional[str] = None,
    language: str = "en"
) -> str:
    """
    Format referral cashback notification text.
    """
    from app.i18n import get_text as i18n_get_text

    referred_display = f"@{referred_username}" if referred_username else f"ID: {referred_id}"

    if referrals_needed > 0:
        if language == "ru":
            if referrals_needed % 10 == 1 and referrals_needed % 100 != 11:
                friend_word = i18n_get_text(language, "referral.friend_singular")
            elif 2 <= referrals_needed % 10 <= 4 and (referrals_needed % 100 < 10 or referrals_needed % 100 >= 20):
                friend_word = i18n_get_text(language, "referral.friend_dual")
            else:
                friend_word = i18n_get_text(language, "referral.friend_plural")
        else:
            friend_word = i18n_get_text(language, "referral.friend_plural")

        progress_text = i18n_get_text(
            language,
            "referral.cashback_progress",
            needed=referrals_needed,
            friend=friend_word
        )
    else:
        progress_text = i18n_get_text(language, "referral.cashback_max_level")

    title = i18n_get_text(language, "referral.cashback_title", action_type=action_type)
    referred_line = i18n_get_text(language, "referral.cashback_referred", referred=referred_display)
    amount_line = i18n_get_text(
        language,
        "referral.cashback_amount",
        action_type=action_type,
        amount=purchase_amount
    )

    notification_text = f"{title}\n\n{referred_line}\n{amount_line}\n"

    if subscription_period:
        period_line = i18n_get_text(
            language,
            "referral.cashback_subscription_period",
            period=subscription_period
        )
        notification_text += f"{period_line}\n"

    reward_line = i18n_get_text(
        language,
        "referral.cashback_reward",
        amount=cashback_amount,
        percent=cashback_percent
    )

    level_line = i18n_get_text(language, "referral.cashback_level", percent=cashback_percent)
    balance_line = i18n_get_text(language, "referral.cashback_balance_auto")
    
    notification_text += (
        f"{reward_line}\n\n"
        f"{level_line}\n"
        f"{progress_text}\n\n"
        f"{balance_line}"
    )
    
    return notification_text
