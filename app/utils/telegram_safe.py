"""
Centralized safe wrapper for bot.send_message.

Handles TelegramBadRequest (chat not found), TelegramForbiddenError (blocked),
and marks unreachable users in DB for background worker filtering.
"""
import asyncio
import logging
import re
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

logger = logging.getLogger(__name__)

# TG-RT-7 (docs/audit/11_telegram_runtime.md): a short flood wait (429 with
# retry_after <= this many seconds) is slept out and the message sent ONCE
# more — the "payment received" / key message used to be dropped. Longer waits
# are still dropped (logged); broadcasts pass raise_retry_after=True and pace
# themselves (one retry layer per call-site).
FLOOD_WAIT_INLINE_MAX_S = 5

_TG_ADS_EMOJI_RE = re.compile(r'!\[(.+?)\]\(tg://emoji\?id=(\d+)\)')


def convert_tg_emoji(text: str) -> str:
    """Convert Telegram Ads emoji format to HTML tg-emoji tags.

    Input:  ![🎮](tg://emoji?id=5319247469165433798)
    Output: <tg-emoji emoji-id="5319247469165433798">🎮</tg-emoji>
    """
    return _TG_ADS_EMOJI_RE.sub(
        r'<tg-emoji emoji-id="\2">\1</tg-emoji>', text
    )


async def safe_send_message(bot, telegram_id: int, text: str, **kwargs):
    """
    Send Telegram message with graceful error handling.
    On chat_not_found: logs, marks user unreachable, returns None.
    On blocked/forbidden: logs, returns None.
    On success: returns Message.

    Defaults to parse_mode="HTML" so <b>, <code> etc. render correctly.
    Callers can override with parse_mode=None or parse_mode="Markdown".

    Returns:
        Message on success, None on any handled failure.
    """
    # Broadcasts pass raise_retry_after=True: a flood wait is re-raised so the
    # sender can sleep and retry instead of losing the message.
    raise_retry_after = kwargs.pop("raise_retry_after", False)
    if "parse_mode" not in kwargs:
        kwargs["parse_mode"] = "HTML"
    text = convert_tg_emoji(text)
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

    except TelegramRetryAfter as e:
        if raise_retry_after:
            raise
        wait = getattr(e, "retry_after", None) or 0
        if wait <= FLOOD_WAIT_INLINE_MAX_S:
            logger.warning(f"SAFE_SEND_FLOOD_WAIT user={telegram_id} retry_after={wait} — retrying once")
            await asyncio.sleep(wait)
            try:
                return await bot.send_message(telegram_id, text, **kwargs)
            except Exception as retry_err:
                logger.warning(
                    f"SAFE_SEND_FLOOD_WAIT_RETRY_FAILED user={telegram_id} error={type(retry_err).__name__}"
                )
                return None
        logger.warning(f"SAFE_SEND_FLOOD_WAIT user={telegram_id} retry_after={wait} — dropped")
        return None

    except Exception:
        logger.exception(f"SAFE_SEND_UNKNOWN_ERROR user={telegram_id}")
        return None
