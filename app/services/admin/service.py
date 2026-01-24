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
    
    return AdminUserOverview(
        user=user,
        subscription=subscription,
        subscription_status=subscription_status,
        stats=stats,
        user_discount=user_discount,
        is_vip=is_vip,
        trial_available=trial_available
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
