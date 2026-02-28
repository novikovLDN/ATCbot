"""
Shared handler utilities: safe edits, formatting, validation, message builders.
"""
import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional

import database
from aiogram.types import Message
from aiogram.types import InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text

logger = logging.getLogger(__name__)

# Максимальная длина отображаемого имени
MAX_DISPLAY_NAME_LENGTH = 64

# Допустимые символы в callback_data
_CALLBACK_DATA_RE = re.compile(r"^[a-zA-Z0-9_:.\-]+$")
MAX_CALLBACK_DATA_LENGTH = 64

# Regex для удаления опасных Unicode символов
_DANGEROUS_UNICODE_RE = re.compile(
    r"[\u0000-\u001f"
    r"\u007f-\u009f"
    r"\u200b-\u200f"
    r"\u2028-\u202f"
    r"\u2060-\u2069"
    r"\u206a-\u206f"
    r"\ufeff"
    r"\ufff0-\uffff"
    r"\U000e0000-\U000e007f"
    r"]"
)


def sanitize_display_name(name: str) -> str:
    """
    Санитизация имени пользователя для безопасного отображения.

    - Удаляет опасные Unicode символы (RTL override, zero-width, control chars)
    - Обрезает до MAX_DISPLAY_NAME_LENGTH символов
    - Удаляет ведущие/завершающие пробелы
    - Возвращает пустую строку если после фильтрации ничего не осталось
    """
    if not name:
        return ""

    name = _DANGEROUS_UNICODE_RE.sub("", name)
    name = name.strip()
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        name = name[:MAX_DISPLAY_NAME_LENGTH].rstrip()
    return name


def validate_callback_data(data: str) -> bool:
    """Валидация callback_data: длина и символы."""
    if not data or len(data) > MAX_CALLBACK_DATA_LENGTH:
        return False
    return bool(_CALLBACK_DATA_RE.match(data))


def safe_resolve_username(user_obj, language: str, telegram_id: int = None) -> str:
    """
    Безопасное разрешение username для отображения.

    Priority:
    1. user_obj.username (Telegram username) — санитизируется
    2. user_obj.first_name (имя пользователя) — санитизируется
    3. localized fallback (user_fallback key)

    Args:
        user_obj: Telegram user object (Message.from_user, CallbackQuery.from_user, etc.)
        language: User language for fallback text (from DB)
        telegram_id: Optional telegram ID for logging

    Returns:
        Строка для отображения (никогда не None)
    """
    if not user_obj:
        return i18n_get_text(language, "common.user")

    if hasattr(user_obj, "username") and user_obj.username:
        sanitized = sanitize_display_name(user_obj.username)
        if sanitized:
            return sanitized

    if hasattr(user_obj, "first_name") and user_obj.first_name:
        sanitized = sanitize_display_name(user_obj.first_name)
        if sanitized:
            return sanitized

    return i18n_get_text(language, "common.user")


def safe_resolve_username_from_db(
    user_dict: Optional[Dict], language: str, telegram_id: int = None
) -> str:
    """
    Безопасное разрешение username из словаря пользователя из БД.
    Все поля санитизируются через sanitize_display_name().

    Priority:
    1. user_dict.get("username")
    2. user_dict.get("first_name")
    3. "ID: <telegram_id>" if telegram_id provided
    4. localized fallback (user_fallback key)
    """
    if not user_dict:
        if telegram_id:
            return f"ID: {telegram_id}"
        return i18n_get_text(language, "common.user")

    username = user_dict.get("username")
    if username:
        sanitized = sanitize_display_name(username)
        if sanitized:
            return sanitized

    first_name = user_dict.get("first_name")
    if first_name:
        sanitized = sanitize_display_name(first_name)
        if sanitized:
            return sanitized

    if telegram_id:
        return f"ID: {telegram_id}"

    return i18n_get_text(language, "common.user")


def _markups_equal(markup1: InlineKeyboardMarkup, markup2: InlineKeyboardMarkup) -> bool:
    """
    Упрощённое сравнение клавиатур (проверка по callback_data)

    Args:
        markup1: Первая клавиатура
        markup2: Вторая клавиатура

    Returns:
        True если клавиатуры идентичны, False иначе
    """
    try:
        if markup1 is None and markup2 is None:
            return True
        if markup1 is None or markup2 is None:
            return False

        kb1 = markup1.inline_keyboard if hasattr(markup1, 'inline_keyboard') else []
        kb2 = markup2.inline_keyboard if hasattr(markup2, 'inline_keyboard') else []

        if len(kb1) != len(kb2):
            return False

        for row1, row2 in zip(kb1, kb2):
            if len(row1) != len(row2):
                return False
            for btn1, btn2 in zip(row1, row2):
                if btn1.callback_data != btn2.callback_data:
                    return False

        return True
    except Exception:
        return False


async def safe_edit_text(message: Message, text: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = None, bot=None):
    """
    Безопасное редактирование текста сообщения с обработкой ошибок

    Сравнивает текущий контент с новым перед редактированием, чтобы избежать ненужных вызовов API.
    Если сообщение недоступно (inaccessible), использует send_message вместо edit_message.

    Args:
        message: Message объект для редактирования
        text: Новый текст сообщения
        reply_markup: Новая клавиатура (опционально) - MUST be InlineKeyboardMarkup, NOT coroutine
        parse_mode: Режим парсинга (HTML, Markdown и т.д.)
        bot: Bot instance (требуется для fallback на send_message)
    """
    if asyncio.iscoroutine(reply_markup):
        raise RuntimeError("reply_markup coroutine passed without await. Must await keyboard builder before passing to safe_edit_text.")

    if not hasattr(message, 'chat'):
        if bot is None:
            logger.warning("Message is inaccessible (no chat attr) and bot not provided, cannot send fallback message")
            return
        try:
            chat_id = None
            if hasattr(message, 'from_user') and hasattr(message.from_user, 'id'):
                chat_id = message.from_user.id

            if chat_id:
                await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                logger.info(f"Message inaccessible (no chat attr), sent new message instead: chat_id={chat_id}")
            else:
                logger.warning("Message inaccessible (no chat attr) and cannot determine chat_id")
        except Exception as send_error:
            logger.error(f"Failed to send fallback message after inaccessible check: {send_error}")
        return

    current_text = None
    try:
        if hasattr(message, 'text'):
            text_attr = getattr(message, 'text', None)
            if text_attr:
                current_text = text_attr
        if not current_text and hasattr(message, 'caption'):
            caption_attr = getattr(message, 'caption', None)
            if caption_attr:
                current_text = caption_attr
    except AttributeError:
        logger.debug("AttributeError while checking message text/caption, treating as inaccessible")
        current_text = None

    if current_text and current_text == text:
        current_markup = None
        try:
            if hasattr(message, 'reply_markup'):
                markup_attr = getattr(message, 'reply_markup', None)
                if markup_attr:
                    current_markup = markup_attr
        except AttributeError:
            current_markup = None

        if reply_markup is None:
            if current_markup is None:
                return
        else:
            if current_markup and _markups_equal(current_markup, reply_markup):
                return

    has_photo = getattr(message, "photo", None) and len(message.photo) > 0
    if has_photo:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                logger.debug(f"Caption not modified (expected): {e}")
                return
            if any(k in err for k in ["message to edit not found", "message can't be edited", "chat not found", "message is inaccessible"]):
                if bot is not None:
                    chat_id = getattr(getattr(message, "chat", None), "id", None) or (getattr(getattr(message, "from_user", None), "id", None) if getattr(message, "from_user", None) else None)
                    if chat_id:
                        await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                        logger.info(f"Photo message inaccessible, sent new message instead: chat_id={chat_id}")
                return
            raise

    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if "message is not modified" in error_msg:
            logger.debug(f"Message not modified (expected): {e}")
            return
        elif any(keyword in error_msg for keyword in ["message to edit not found", "message can't be edited", "chat not found", "message is inaccessible"]):
            if bot is None:
                logger.warning(f"Message inaccessible and bot not provided, cannot send fallback message: {e}")
                return

            try:
                chat_id = None
                try:
                    if hasattr(message, 'chat'):
                        chat_obj = getattr(message, 'chat', None)
                        if chat_obj and hasattr(chat_obj, 'id'):
                            chat_id = getattr(chat_obj, 'id', None)
                except AttributeError:
                    pass

                if not chat_id:
                    try:
                        if hasattr(message, 'from_user'):
                            user_obj = getattr(message, 'from_user', None)
                            if user_obj and hasattr(user_obj, 'id'):
                                chat_id = getattr(user_obj, 'id', None)
                    except AttributeError:
                        pass

                if chat_id:
                    await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                    logger.info(f"Message inaccessible, sent new message instead: chat_id={chat_id}")
                else:
                    logger.warning(f"Message inaccessible and cannot determine chat_id: {e}")
            except Exception as send_error:
                logger.error(f"Failed to send fallback message after edit failure: {send_error}")
        else:
            raise
    except AttributeError as e:
        logger.warning(f"AttributeError in safe_edit_text, message may be inaccessible: {e}")
        if bot is not None:
            try:
                chat_id = None
                try:
                    if hasattr(message, 'chat'):
                        chat_obj = getattr(message, 'chat', None)
                        if chat_obj and hasattr(chat_obj, 'id'):
                            chat_id = getattr(chat_obj, 'id', None)
                except AttributeError:
                    pass

                if not chat_id:
                    try:
                        if hasattr(message, 'from_user'):
                            user_obj = getattr(message, 'from_user', None)
                            if user_obj and hasattr(user_obj, 'id'):
                                chat_id = getattr(user_obj, 'id', None)
                    except AttributeError:
                        pass

                if chat_id:
                    await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                    logger.info(f"AttributeError handled, sent new message instead: chat_id={chat_id}")
                else:
                    logger.warning(f"AttributeError handled but cannot determine chat_id: {e}")
            except Exception as send_error:
                logger.error(f"Failed to send fallback message after AttributeError: {send_error}")


async def safe_edit_reply_markup(message: Message, reply_markup: InlineKeyboardMarkup = None):
    """
    Безопасное редактирование клавиатуры сообщения с обработкой ошибки "message is not modified"

    Args:
        message: Message объект для редактирования
        reply_markup: Новая клавиатура (или None для удаления)
    """
    if reply_markup is None:
        if message.reply_markup is None:
            return
    else:
        if message.reply_markup and _markups_equal(message.reply_markup, reply_markup):
            return

    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
        logger.debug(f"Reply markup not modified (expected): {e}")


async def get_promo_session(state: FSMContext) -> Optional[Dict[str, Any]]:
    """
    Получить активную промо-сессию из FSM state

    Returns:
        {
            "promo_code": str,
            "discount_percent": int,
            "expires_at": float (unix timestamp)
        } или None если сессия отсутствует или истекла
    """
    fsm_data = await state.get_data()
    promo_session = fsm_data.get("promo_session")

    if not promo_session:
        return None

    expires_at = promo_session.get("expires_at")
    current_time = time.time()

    if expires_at and current_time > expires_at:
        await state.update_data(promo_session=None)
        telegram_id = fsm_data.get("_telegram_id", "unknown")
        logger.info(
            f"promo_session_expired: user={telegram_id}, "
            f"promo_code={promo_session.get('promo_code')}"
        )
        return None

    return promo_session


async def create_promo_session(
    state: FSMContext,
    promo_code: str,
    discount_percent: int,
    telegram_id: int,
    ttl_seconds: int = 300
) -> Dict[str, Any]:
    """
    Создать промо-сессию с TTL

    Args:
        state: FSM context
        promo_code: Код промокода
        discount_percent: Процент скидки
        telegram_id: Telegram ID пользователя (для логирования)
        ttl_seconds: Время жизни в секундах (по умолчанию 300 = 5 минут)

    Returns:
        Созданная промо-сессия
    """
    current_time = time.time()
    expires_at = current_time + ttl_seconds

    promo_session = {
        "promo_code": promo_code.upper(),
        "discount_percent": discount_percent,
        "expires_at": expires_at
    }

    await state.update_data(promo_session=promo_session, _telegram_id=telegram_id)

    expires_in = int(expires_at - current_time)
    logger.info(
        f"promo_session_created: user={telegram_id}, promo_code={promo_code.upper()}, "
        f"discount_percent={discount_percent}%, expires_in={expires_in}s"
    )

    return promo_session


async def clear_promo_session(state: FSMContext):
    """Удалить промо-сессию"""
    await state.update_data(promo_session=None)


async def format_text_with_incident(text: str, language: str) -> str:
    """Добавить баннер инцидента к тексту, если режим активен"""
    try:
        if not database.DB_READY:
            return text
        incident = await database.get_incident_settings()
        if incident and incident.get("is_active"):
            banner = i18n_get_text(language, "incident.banner")
            incident_text = incident.get("incident_text")
            if incident_text:
                banner += f"\n{incident_text}"
            return f"{banner}\n\n⸻\n\n{text}"
        return text
    except Exception as e:
        logger.warning(f"Error getting incident settings: {e}")
        return text


def detect_platform(callback_or_message) -> str:
    """
    Определить платформу пользователя (iOS, Android, или unknown)

    Args:
        callback_or_message: CallbackQuery или Message объект из aiogram

    Returns:
        "ios", "android", или "unknown"
    """
    try:
        if hasattr(callback_or_message, 'from_user'):
            user = callback_or_message.from_user
        elif hasattr(callback_or_message, 'user'):
            user = callback_or_message.user
        else:
            return "unknown"

        language_code = getattr(user, 'language_code', None)

        if language_code:
            lang_lower = language_code.lower()
            if '-' in language_code:
                pass

        return "unknown"

    except Exception as e:
        logger.debug(f"Platform detection error: {e}")
        return "unknown"


def format_promo_stats_text(stats: list) -> str:
    """Форматировать статистику промокодов в текст"""
    if not stats:
        return "Промокоды не найдены."

    text = "📊 Статистика промокодов\n\n"

    for promo in stats:
        code = promo["code"]
        discount_percent = promo["discount_percent"]
        max_uses = promo["max_uses"]
        used_count = promo["used_count"]
        is_active = promo["is_active"]

        text += f"{code}\n"
        text += f"— Скидка: {discount_percent}%\n"

        if max_uses is not None:
            text += f"— Использовано: {used_count} / {max_uses}\n"
            if is_active:
                text += "— Статус: активен\n"
            else:
                text += "— Статус: исчерпан\n"
        else:
            text += f"— Использовано: {used_count}\n"
            text += "— Статус: без ограничений\n"

        text += "\n"

    return text


_REISSUE_LOCKS: Dict[int, asyncio.Lock] = {}


def get_reissue_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _REISSUE_LOCKS:
        _REISSUE_LOCKS[user_id] = asyncio.Lock()
    return _REISSUE_LOCKS[user_id]


def get_reissue_notification_text(vpn_key: str, language: str = "ru") -> str:
    """Текст уведомления о перевыпуске VPN-ключа"""
    title = i18n_get_text(language, "main.reissue_notification_title")
    text_body = i18n_get_text(language, "main.reissue_notification_text", vpn_key=vpn_key)
    return f"{title}\n\n{text_body}"
