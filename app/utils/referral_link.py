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


async def build_share_discount_link(telegram_id: int, bot_username: str) -> str:
    """Build a share-discount deep-link (`refd_` prefix).

    Отличается от обычной реф-ссылки префиксом `refd_` вместо `ref_`.
    По нему start.py:
      • закрепляет нового юзера как реферала (если ещё не закреплён);
      • выдаёт 30%-скидку на 24 часа (lifetime-once на recipient);
      • существующего юзера НЕ перепривязывает (referrer_id immutable),
        но всё равно даёт скидку, если ещё не получал её ранее.

    Тот же opaque referral_code из колонки users.referral_code — никакой
    дополнительной таблицы для кодов не нужно."""
    code = await database.get_user_referral_code(telegram_id)
    if code:
        return f"https://t.me/{bot_username}?start=refd_{code}"
    logger.warning(
        "share_discount_link_fallback user=%s (referral_code unavailable)",
        telegram_id,
    )
    return f"https://t.me/{bot_username}?start=refd_{telegram_id}"
