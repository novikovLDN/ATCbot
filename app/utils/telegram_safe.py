"""
Centralized safe wrapper for bot.send_message.

Handles TelegramBadRequest (chat not found), TelegramForbiddenError (blocked),
and marks unreachable users in DB for background worker filtering.
"""
import logging
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)


async def safe_send_message(bot, telegram_id: int, text: str, **kwargs):
    """
    Send Telegram message with graceful error handling.
    On chat_not_found: logs, marks user unreachable, returns None.
    On blocked/forbidden: logs, returns None.
    On success: returns Message.

    Returns:
        Message on success, None on any handled failure.
    """
    try:
        return await bot.send_message(telegram_id, text, **kwargs)

    except TelegramBadRequest as e:
        err_str = str(e).lower()
        if "chat not found" in err_str:
            logger.warning(f"SAFE_SEND_SKIP_CHAT_NOT_FOUND user={telegram_id}")
            try:
                import database
                await database.mark_user_unreachable(telegram_id)
            except Exception as db_err:
                logger.warning(f"SAFE_SEND: Failed to mark user unreachable: {db_err}")
            return None
        logger.exception(f"SAFE_SEND_BAD_REQUEST user={telegram_id}")
        return None

    except TelegramForbiddenError:
        logger.warning(f"SAFE_SEND_FORBIDDEN user={telegram_id}")
        try:
            import database
            await database.mark_user_unreachable(telegram_id)
        except Exception as db_err:
            logger.warning(f"SAFE_SEND: Failed to mark user unreachable: {db_err}")
        return None

    except Exception:
        logger.exception(f"SAFE_SEND_UNKNOWN_ERROR user={telegram_id}")
        return None
