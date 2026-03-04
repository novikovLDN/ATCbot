"""
Catch-all handler для неизвестных сообщений.
Регистрируется ПОСЛЕДНИМ — ловит всё что не поймали другие хэндлеры.
Бот молча игнорирует неизвестные сообщения.
"""
import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import StateFilter
from aiogram.fsm.state import default_state

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
