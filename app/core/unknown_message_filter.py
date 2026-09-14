"""
Catch-all handlers для неизвестных сообщений и callback'ов.
Регистрируется ПОСЛЕДНИМ — ловит всё что не поймали другие хэндлеры.
Бот молча игнорирует неизвестные сообщения; на неизвестный callback
(кнопка из старого сообщения) отвечает «кнопка устарела», чтобы
кнопка не зависала с «часиками».
"""
import logging
from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.state import default_state

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language, DEFAULT_LANGUAGE

logger = logging.getLogger(__name__)

unknown_message_router = Router()


@unknown_message_router.message(StateFilter(default_state))
async def catch_unknown_message(message: Message):
    """
    Ловит ВСЕ сообщения в default_state которые не были обработаны другими хэндлерами.
    Молча игнорирует — НЕ отвечает.
    """
    user_id = message.from_user.id if message.from_user else "unknown"
    text_preview = (
        (message.text or "")[:30] if message.text else f"[{message.content_type}]"
    )
    logger.debug(
        "IGNORED_UNKNOWN_MESSAGE user=%s content=%s", user_id, text_preview
    )
    return  # Молча игнорируем


@unknown_message_router.callback_query()
async def catch_unknown_callback(callback: CallbackQuery):
    """
    Ловит callback'и, которые не обработал ни один хэндлер (кнопки из старых
    сообщений, чьи хэндлеры удалены). Только отвечает на callback.
    """
    logger.debug(
        "UNKNOWN_CALLBACK user=%s data=%s",
        callback.from_user.id if callback.from_user else "unknown",
        (callback.data or "")[:64],
    )
    try:
        language = await resolve_user_language(callback.from_user.id)
    except Exception:
        language = DEFAULT_LANGUAGE
    try:
        await callback.answer(i18n_get_text(language, "common.button_outdated"))
    except Exception as e:
        logger.debug("UNKNOWN_CALLBACK answer failed: %s", e)
