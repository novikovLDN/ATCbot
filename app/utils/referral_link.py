"""Helper to build opaque referral links (no telegram_id in URL)."""
import logging
import database

logger = logging.getLogger(__name__)


async def build_referral_link(telegram_id: int, bot_username: str) -> str:
    """
    Build a referral link using the user's opaque referral_code.
    Falls back to legacy ref_<telegram_id> only if DB is unavailable.
    """
    code = await database.get_user_referral_code(telegram_id)
    if code:
        return f"https://t.me/{bot_username}?start=ref_{code}"
    # Fallback: legacy format (should rarely happen)
    logger.warning("referral_link_fallback user=%s (DB unavailable)", telegram_id)
    return f"https://t.me/{bot_username}?start=ref_{telegram_id}"
