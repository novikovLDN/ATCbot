"""Единая безопасная обёртка над bot.send_message.

ЧТО ОНА БЕРЁТ НА СЕБЯ
    • «чат не найден» и «бот заблокирован» — помечает пользователя
      недостижимым, чтобы фоновые воркеры его больше не дёргали;
    • TelegramRetryAfter — ждёт ровно столько, сколько велел Telegram, и
      повторяет отправку;
    • ошибку разметки — повторяет отправку простым текстом, чтобы человек
      получил хотя бы содержание.

ПОЧЕМУ ЭТИ ТРИ ВЕЩИ ИМЕННО ЗДЕСЬ
    Через эту функцию идут все рассылки и уведомления. Обрабатывать лимиты
    и разметку в каждом вызывающем месте значит однажды забыть — и получить
    временную блокировку бота или молчание вместо сообщения.
"""
import logging
import re
import asyncio

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

logger = logging.getLogger(__name__)

_TG_ADS_EMOJI_RE = re.compile(r'!\[(.+?)\]\(tg://emoji\?id=(\d+)\)')

# Потолок ожидания по требованию Telegram. Провайдер иногда просит
# десятки минут — столько держать корутину бессмысленно, лучше признать
# отправку неудачной и дать воркеру продолжить.
MAX_RETRY_AFTER_SECONDS = 60.0

# По этим словам в тексте ошибки узнаём проблему разметки, а не адресата.
_PARSE_ERROR_MARKERS = (
    "can't parse entities",
    "unsupported start tag",
    "unclosed start tag",
    "can't find end tag",
    "invalid custom emoji",
    "entity_id_invalid",
)

# Снятие HTML-разметки для повторной отправки простым текстом.
_HTML_TAG_RE = re.compile(r'<[^>]+>')


def _strip_markup(text: str) -> str:
    """Убрать теги, оставив читаемый текст.

    Нужна там, где Telegram отверг разметку: отправлять тот же текст с
    parse_mode=None нельзя — теги останутся видны человеку как есть.
    """
    return _HTML_TAG_RE.sub('', text)


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
    if "parse_mode" not in kwargs:
        kwargs["parse_mode"] = "HTML"
    text = convert_tg_emoji(text)
    try:
        return await bot.send_message(telegram_id, text, **kwargs)

    except TelegramRetryAfter as e:
        # Telegram сам говорит, сколько ждать. Игнорировать это нельзя:
        # массовая рассылка идёт быстрее лимита (~30 сообщений в секунду),
        # и без паузы бот получает временную блокировку — встаёт РАССЫЛКА
        # ЦЕЛИКОМ, а не одна отправка.
        #
        # Ждём указанное время (с потолком, чтобы не зависнуть навсегда) и
        # пробуем ещё раз ровно один раз: если и вторая попытка упирается в
        # лимит, значит темп неверный в принципе, и это должно быть видно
        # в логах, а не спрятано в бесконечных ретраях.
        delay = min(float(getattr(e, "retry_after", 1) or 1), MAX_RETRY_AFTER_SECONDS)
        logger.warning(
            "SAFE_SEND_RATE_LIMITED user=%s retry_after=%ss — ждём и повторяем",
            telegram_id, delay,
        )
        await asyncio.sleep(delay)
        try:
            return await bot.send_message(telegram_id, text, **kwargs)
        except Exception as retry_err:
            logger.error(
                "SAFE_SEND_RATE_LIMIT_RETRY_FAILED user=%s: %s", telegram_id, retry_err,
            )
            return None

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

        # Ошибка разметки: битый HTML или premium-эмодзи, которого у бота
        # нет. Раньше сообщение просто терялось — человек не получал ничего.
        # Содержание важнее оформления, поэтому повторяем простым текстом.
        if any(marker in err_str for marker in _PARSE_ERROR_MARKERS):
            logger.warning(
                "SAFE_SEND_PARSE_FAILED user=%s: %s — повторяем без разметки",
                telegram_id, e,
            )
            plain_kwargs = dict(kwargs)
            plain_kwargs["parse_mode"] = None
            try:
                return await bot.send_message(telegram_id, _strip_markup(text), **plain_kwargs)
            except Exception as retry_err:
                logger.error(
                    "SAFE_SEND_PLAIN_RETRY_FAILED user=%s: %s", telegram_id, retry_err,
                )
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
