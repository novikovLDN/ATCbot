"""
Referral Service - Deterministic Referral Tracking

This module provides business logic for referral registration, activation, and lifecycle management.
All functions are pure business logic - no aiogram imports, no Telegram calls.
"""

from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
import database
import logging

logger = logging.getLogger(__name__)


class ReferralState(Enum):
    """Referral lifecycle states"""
    NONE = "none"  # No referral relationship
    REGISTERED = "registered"  # User came via referral link
    ACTIVATED = "activated"  # First paid action OR trial


async def process_referral_registration(
    telegram_id: int,
    referral_code: Optional[str] = None,
    conn: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Process referral registration on FIRST user interaction.
    
    This is the SINGLE SOURCE OF TRUTH for referral registration.
    Must be called on first interaction (any update) if referral_code is present.
    
    Rules:
    - referrer_id is IMMUTABLE (set once, never overwritten)
    - If referrer_id already exists → ignore
    - Self-referral is blocked
    - Referral loops are blocked
    
    Args:
        telegram_id: Telegram ID of the user
        referral_code: Referral code from start payload (e.g., "ref_123456")
        conn: Database connection (if None, creates new)
    
    Returns:
        {
            "success": bool,
            "state": ReferralState,
            "referrer_id": Optional[int],
            "reason": str
        }
    """
    if not referral_code:
        return {
            "success": False,
            "state": ReferralState.NONE,
            "referrer_id": None,
            "reason": "no_referral_code"
        }
    
    # Extract telegram_id from referral code (format: "ref_<telegram_id>")
    if not referral_code.startswith("ref_"):
        return {
            "success": False,
            "state": ReferralState.NONE,
            "referrer_id": None,
            "reason": "invalid_referral_code_format"
        }
    
    try:
        referrer_telegram_id = int(referral_code[4:])
    except (ValueError, TypeError):
        return {
            "success": False,
            "state": ReferralState.NONE,
            "referrer_id": None,
            "reason": "invalid_referral_code_value"
        }
    
    # Self-referral check
    if referrer_telegram_id == telegram_id:
        logger.warning(f"REFERRAL_SELF_ATTEMPT [user_id={telegram_id}, referral_code={referral_code}]")
        return {
            "success": False,
            "state": ReferralState.NONE,
            "referrer_id": None,
            "reason": "self_referral"
        }
    
    # Check if user already has referrer_id (IMMUTABLE)
    if conn is None:
        user = await database.get_user(telegram_id)
    else:
        user_row = await conn.fetchrow(
            "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
            telegram_id
        )
        user = dict(user_row) if user_row else None
    
    if user and (user.get("referrer_id") or user.get("referred_by")):
        existing_referrer = user.get("referrer_id") or user.get("referred_by")
        logger.debug(
            f"REFERRAL_IMMUTABLE [user={telegram_id}, existing_referrer={existing_referrer}, "
            f"attempted_code={referral_code}]"
        )
        return {
            "success": False,
            "state": ReferralState.REGISTERED,
            "referrer_id": existing_referrer,
            "reason": "referrer_id_already_set"
        }
    
    # Verify referrer exists
    referrer = await database.find_user_by_referral_code(referral_code)
    if not referrer:
        logger.warning(f"REFERRAL_REFERRER_NOT_FOUND [user={telegram_id}, code={referral_code}]")
        return {
            "success": False,
            "state": ReferralState.NONE,
            "referrer_id": None,
            "reason": "referrer_not_found"
        }
    
    referrer_user_id = referrer["telegram_id"]
    
    # Check for referral loop
    referrer_user = await database.get_user(referrer_user_id)
    if referrer_user:
        referrer_referrer = referrer_user.get("referrer_id") or referrer_user.get("referred_by")
        if referrer_referrer == telegram_id:
            logger.warning(
                f"REFERRAL_LOOP_DETECTED [user={telegram_id}, referrer={referrer_user_id}]"
            )
            return {
                "success": False,
                "state": ReferralState.NONE,
                "referrer_id": None,
                "reason": "referral_loop"
            }
    
    # Register referral (atomic operation)
    if conn is None:
        success = await database.register_referral(referrer_user_id, telegram_id)
    else:
        # Inline registration within transaction
        try:
            # Check if already registered
            existing = await conn.fetchrow(
                "SELECT * FROM referrals WHERE referred_user_id = $1",
                telegram_id
            )
            if existing:
                success = False
            else:
                # Create referral record
                await conn.execute(
                    """INSERT INTO referrals (referrer_user_id, referred_user_id, is_rewarded, reward_amount)
                       VALUES ($1, $2, FALSE, 0)
                       ON CONFLICT (referred_user_id) DO NOTHING""",
                    referrer_user_id, telegram_id
                )
                
                # Update referrer_id (IMMUTABLE - only if NULL)
                await conn.execute(
                    """UPDATE users 
                       SET referrer_id = $1, referred_by = $1, referred_at = NOW()
                       WHERE telegram_id = $2 
                       AND referrer_id IS NULL 
                       AND referred_by IS NULL""",
                    referrer_user_id, telegram_id
                )
                success = True
        except Exception as e:
            logger.exception(f"Error registering referral in transaction: {e}")
            success = False
    
    if success:
        logger.info(
            f"REFERRAL_REGISTERED [referrer={referrer_user_id}, referred={telegram_id}, "
            f"code={referral_code}, state=REGISTERED]"
        )
        return {
            "success": True,
            "state": ReferralState.REGISTERED,
            "referrer_id": referrer_user_id,
            "reason": "registered"
        }
    else:
        return {
            "success": False,
            "state": ReferralState.REGISTERED,
            "referrer_id": referrer_user_id,
            "reason": "already_registered"
        }


async def activate_referral(
    telegram_id: int,
    activation_type: str = "payment",  # "payment", "trial", "topup"
    conn: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Activate referral (transition from REGISTERED to ACTIVATED).
    
    Called on:
    - First paid subscription purchase
    - First balance topup
    - Trial activation
    
    Args:
        telegram_id: Telegram ID of the referred user
        activation_type: Type of activation ("payment", "trial", "topup")
        conn: Database connection (if None, creates new)
    
    Returns:
        {
            "success": bool,
            "state": ReferralState,
            "referrer_id": Optional[int],
            "was_activated": bool  # True if just activated, False if already active
        }
    """
    # Get user's referrer_id
    if conn is None:
        user = await database.get_user(telegram_id)
    else:
        user_row = await conn.fetchrow(
            "SELECT referrer_id FROM users WHERE telegram_id = $1",
            telegram_id
        )
        user = dict(user_row) if user_row else None
    
    if not user or not user.get("referrer_id"):
        return {
            "success": False,
            "state": ReferralState.NONE,
            "referrer_id": None,
            "was_activated": False
        }
    
    referrer_id = user.get("referrer_id")
    
    # Check if already activated (has first_paid_at)
    if conn is None:
        pool = await database.get_pool()
        if pool is None:
            return {
                "success": False,
                "state": ReferralState.REGISTERED,
                "referrer_id": referrer_id,
                "was_activated": False
            }
        async with pool.acquire() as conn:
            return await _activate_referral_internal(telegram_id, referrer_id, activation_type, conn)
    else:
        return await _activate_referral_internal(telegram_id, referrer_id, activation_type, conn)


async def _activate_referral_internal(
    telegram_id: int,
    referrer_id: int,
    activation_type: str,
    conn: Any
) -> Dict[str, Any]:
    """Internal helper for activation"""
    try:
        # Check current state
        referral_row = await conn.fetchrow(
            "SELECT first_paid_at FROM referrals WHERE referrer_user_id = $1 AND referred_user_id = $2",
            referrer_id, telegram_id
        )
        
        if not referral_row:
            # Create referral record if it doesn't exist (shouldn't happen, but safety)
            await conn.execute(
                """INSERT INTO referrals (referrer_user_id, referred_user_id, is_rewarded, reward_amount, first_paid_at)
                   VALUES ($1, $2, FALSE, 0, NOW())
                   ON CONFLICT (referred_user_id) DO UPDATE
                   SET first_paid_at = COALESCE(referrals.first_paid_at, NOW())""",
                referrer_id, telegram_id
            )
            logger.info(
                f"REFERRAL_ACTIVATED [referrer={referrer_id}, referred={telegram_id}, "
                f"type={activation_type}, state=ACTIVATED]"
            )
            return {
                "success": True,
                "state": ReferralState.ACTIVATED,
                "referrer_id": referrer_id,
                "was_activated": True
            }
        
        was_already_activated = referral_row.get("first_paid_at") is not None
        
        if not was_already_activated:
            # Activate now
            await conn.execute(
                "UPDATE referrals SET first_paid_at = NOW() WHERE referrer_user_id = $1 AND referred_user_id = $2 AND first_paid_at IS NULL",
                referrer_id, telegram_id
            )
            logger.info(
                f"REFERRAL_ACTIVATED [referrer={referrer_id}, referred={telegram_id}, "
                f"type={activation_type}, state=ACTIVATED]"
            )
            return {
                "success": True,
                "state": ReferralState.ACTIVATED,
                "referrer_id": referrer_id,
                "was_activated": True
            }
        else:
            return {
                "success": True,
                "state": ReferralState.ACTIVATED,
                "referrer_id": referrer_id,
                "was_activated": False
            }
    except Exception as e:
        logger.exception(f"Error activating referral: referred={telegram_id}, error={e}")
        return {
            "success": False,
            "state": ReferralState.REGISTERED,
            "referrer_id": referrer_id,
            "was_activated": False
        }


async def get_referral_state(telegram_id: int) -> ReferralState:
    """
    Get current referral state for a user.
    
    Args:
        telegram_id: Telegram ID of the user
    
    Returns:
        ReferralState enum value
    """
    user = await database.get_user(telegram_id)
    if not user or not user.get("referrer_id"):
        return ReferralState.NONE
    
    referrer_id = user.get("referrer_id")
    pool = await database.get_pool()
    if pool is None:
        return ReferralState.REGISTERED
    
    try:
        async with pool.acquire() as conn:
            referral_row = await conn.fetchrow(
                "SELECT first_paid_at FROM referrals WHERE referrer_user_id = $1 AND referred_user_id = $2",
                referrer_id, telegram_id
            )
            
            if referral_row and referral_row.get("first_paid_at"):
                return ReferralState.ACTIVATED
            else:
                return ReferralState.REGISTERED
    except Exception:
        return ReferralState.REGISTERED
