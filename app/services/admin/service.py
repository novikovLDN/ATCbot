"""
Admin Service Layer

This module provides business logic for admin operations, user overview, and action decisions.
It coordinates between database, subscription service, and trial service.

All functions are pure business logic:
- No aiogram imports
- No logging
- No Telegram formatting
- Pure business logic only
"""

from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass
import database
from app.services.subscriptions.service import get_subscription_status
from app.services.trials import service as trial_service
from app.services.admin.exceptions import (
    AdminServiceError,
    UserNotFoundError,
    InvalidAdminActionError,
)


# ====================================================================================
# Result Types
# ====================================================================================

@dataclass
class AdminUserOverview:
    """Comprehensive user overview for admin"""
    user: Dict[str, Any]
    subscription: Optional[Dict[str, Any]]
    subscription_status: Any  # SubscriptionStatus from subscription_service
    stats: Dict[str, Any]
    user_discount: Optional[Dict[str, Any]]
    is_vip: bool
    trial_available: bool
    referral_stats: Optional[Dict[str, Any]] = None
    game_stats: Optional[Dict[str, Any]] = None
    payment_stats: Optional[Dict[str, Any]] = None


@dataclass
class AdminActions:
    """Available admin actions for a user"""
    can_reissue_key: bool
    can_grant_access: bool
    can_revoke_access: bool
    can_grant_vip: bool
    can_revoke_vip: bool
    can_grant_discount: bool
    can_revoke_discount: bool
    can_view_history: bool


# ====================================================================================
# Admin User Overview
# ====================================================================================

async def _get_game_and_payment_stats(user_id: int):
    """Fetch game and payment statistics for admin user card."""
    pool = await database.get_pool()
    if pool is None:
        return None, None

    async with pool.acquire() as conn:
        # --- Game stats ---
        # Bowling wins (subscription_history where source contains game info, or audit_log)
        bowling_plays = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log WHERE telegram_id = $1 AND action ILIKE '%bowling%'",
            user_id,
        ) or 0
        bowling_wins = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log WHERE telegram_id = $1 AND action ILIKE '%bowling%' AND result = 'success'",
            user_id,
        ) or 0

        dice_plays = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log WHERE telegram_id = $1 AND action ILIKE '%dice%'",
            user_id,
        ) or 0

        bomber_plays = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log WHERE telegram_id = $1 AND action ILIKE '%bomber%'",
            user_id,
        ) or 0

        # Farm earnings from balance_transactions
        farm_earnings_kopecks = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM balance_transactions WHERE user_id = $1 AND source = 'farm_harvest'",
            user_id,
        ) or 0
        farm_harvests = await conn.fetchval(
            "SELECT COUNT(*) FROM balance_transactions WHERE user_id = $1 AND source = 'farm_harvest'",
            user_id,
        ) or 0

        # Days earned from games (via subscription grants from games)
        game_days_earned = await conn.fetchval(
            """SELECT COALESCE(SUM(
                CASE
                    WHEN details ILIKE '%bowling%' OR details ILIKE '%dice%'
                    THEN COALESCE(
                        CAST(NULLIF(SUBSTRING(details FROM '\\+(\\d+)\\s*д'), '') AS INTEGER),
                        0
                    )
                    ELSE 0
                END
            ), 0)
            FROM audit_log
            WHERE telegram_id = $1
            AND action ILIKE '%game%'
            AND result = 'success'""",
            user_id,
        ) or 0

        game_stats = {
            "bowling_plays": bowling_plays,
            "bowling_wins": bowling_wins,
            "dice_plays": dice_plays,
            "bomber_plays": bomber_plays,
            "farm_harvests": farm_harvests,
            "farm_earnings_rub": farm_earnings_kopecks / 100.0,
            "game_days_earned": game_days_earned,
        }

        # --- Payment stats ---
        total_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE telegram_id = $1 AND status = 'approved'",
            user_id,
        ) or 0
        total_spent_kopecks = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE telegram_id = $1 AND status = 'approved'",
            user_id,
        ) or 0
        first_payment = await conn.fetchval(
            "SELECT MIN(paid_at) FROM payments WHERE telegram_id = $1 AND status = 'approved'",
            user_id,
        )
        last_payment = await conn.fetchval(
            "SELECT MAX(paid_at) FROM payments WHERE telegram_id = $1 AND status = 'approved'",
            user_id,
        )

        payment_stats = {
            "total_payments": total_payments,
            "total_spent_rub": total_spent_kopecks / 100.0,
            "first_payment_at": first_payment,
            "last_payment_at": last_payment,
        }

    return game_stats, payment_stats


async def get_admin_user_overview(user_id: int) -> AdminUserOverview:
    """
    Get comprehensive user overview for admin panel.
    
    This function fetches all relevant user data:
    - User information
    - Subscription status (using subscription_service)
    - Trial availability (using trial_service)
    - VIP status
    - Personal discount
    - User statistics
    
    Args:
        user_id: Telegram ID of the user
        
    Returns:
        AdminUserOverview with all user data
        
    Raises:
        UserNotFoundError: If user is not found
    """
    # Fetch user
    user = await database.get_user(user_id)
    if not user:
        raise UserNotFoundError(f"User not found: {user_id}")
    
    # Fetch subscription
    subscription = await database.get_subscription(user_id)
    
    # Get subscription status using subscription service
    subscription_status = get_subscription_status(subscription)
    
    # Get user statistics
    stats = await database.get_user_extended_stats(user_id)
    
    # Get user discount
    user_discount = await database.get_user_discount(user_id)
    
    # Get VIP status
    is_vip = await database.is_vip_user(user_id)

    # Check trial availability using trial service
    trial_available = await trial_service.is_trial_available(user_id)

    # Referral statistics
    referral_stats = None
    try:
        referral_stats = await database.get_referral_statistics(user_id)
    except Exception:
        pass

    # Game & payment statistics (via single DB query)
    game_stats = None
    payment_stats = None
    try:
        game_stats, payment_stats = await _get_game_and_payment_stats(user_id)
    except Exception:
        pass

    return AdminUserOverview(
        user=user,
        subscription=subscription,
        subscription_status=subscription_status,
        stats=stats,
        user_discount=user_discount,
        is_vip=is_vip,
        trial_available=trial_available,
        referral_stats=referral_stats,
        game_stats=game_stats,
        payment_stats=payment_stats,
    )


# ====================================================================================
# Admin Action Decisions
# ====================================================================================

def get_admin_user_actions(overview: AdminUserOverview) -> AdminActions:
    """
    Determine which admin actions are available for a user.
    
    This function implements all business rules for admin actions:
    - Key reissue: only if subscription is active
    - Grant/revoke access: always available
    - Grant/revoke VIP: based on current VIP status
    - Grant/revoke discount: based on current discount status
    - View history: always available
    
    Args:
        overview: AdminUserOverview with user data
        
    Returns:
        AdminActions with available actions
    """
    subscription_status = overview.subscription_status
    has_active_subscription = subscription_status.is_active
    
    # Key reissue: only if subscription is active
    can_reissue_key = has_active_subscription
    
    # Grant/revoke access: always available
    can_grant_access = True
    can_revoke_access = has_active_subscription
    
    # VIP actions: grant if not VIP, revoke if VIP
    can_grant_vip = not overview.is_vip
    can_revoke_vip = overview.is_vip
    
    # Discount actions: grant if no discount, revoke if has discount
    can_grant_discount = overview.user_discount is None
    can_revoke_discount = overview.user_discount is not None
    
    # View history: always available
    can_view_history = True
    
    return AdminActions(
        can_reissue_key=can_reissue_key,
        can_grant_access=can_grant_access,
        can_revoke_access=can_revoke_access,
        can_grant_vip=can_grant_vip,
        can_revoke_vip=can_revoke_vip,
        can_grant_discount=can_grant_discount,
        can_revoke_discount=can_revoke_discount,
        can_view_history=can_view_history
    )
