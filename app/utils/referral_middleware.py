"""
Referral Middleware - Process referral registration on first interaction

This middleware ensures referral registration happens on FIRST user interaction,
not just on /start command.
"""

from typing import Optional
from aiogram.types import Update, Message, CallbackQuery
import logging
import database
from app.services.referrals import process_referral_registration, ReferralState

logger = logging.getLogger(__name__)


async def process_referral_on_first_interaction(
    update: Update,
    telegram_id: int
) -> Optional[dict]:
    """
    Process referral registration on first user interaction.
    
    This function:
    1. Extracts referral code from start payload (if present)
    2. Processes referral registration (idempotent)
    3. Returns referral info for notification
    
    Must be called from handlers on first interaction.
    
    Args:
        update: Aiogram Update object
        telegram_id: Telegram ID of the user
    
    Returns:
        {
            "success": bool,
            "referrer_id": Optional[int],
            "state": ReferralState,
            "should_notify": bool  # True if just registered and should notify referrer
        } or None if no referral code
    """
    referral_code = None
    
    # Extract referral code from different update types
    if isinstance(update, Message):
        # From /start command
        if update.text and update.text.startswith("/start"):
            parts = update.text.split(" ", 1)
            if len(parts) > 1 and parts[1].startswith("ref_"):
                referral_code = parts[1]
    elif isinstance(update, CallbackQuery):
        # From callback (if referral code is in callback data)
        if update.data and update.data.startswith("ref_"):
            referral_code = update.data
    
    if not referral_code:
        return None
    
    # Process referral registration
    result = await process_referral_registration(telegram_id, referral_code)
    
    if result["success"] and result["state"] == ReferralState.REGISTERED:
        logger.info(
            f"REFERRAL_REGISTERED [referrer={result['referrer_id']}, "
            f"referred={telegram_id}, code={referral_code}]"
        )
        return {
            "success": True,
            "referrer_id": result["referrer_id"],
            "state": result["state"],
            "should_notify": True  # Notify referrer about new registration
        }
    elif result["state"] == ReferralState.REGISTERED:
        # Already registered
        return {
            "success": False,
            "referrer_id": result.get("referrer_id"),
            "state": result["state"],
            "should_notify": False
        }
    else:
        # Registration failed (self-referral, loop, etc.)
        return {
            "success": False,
            "referrer_id": None,
            "state": result["state"],
            "should_notify": False
        }
