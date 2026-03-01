"""
Middleware: отклоняет все update'ы не из private chat.
Дополнительно фильтрует пустые и подозрительные сообщения.
"""
import re
import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)

# Regex для "пустых" сообщений: только пробелы, zero-width символы, невидимые unicode
_INVISIBLE_ONLY_RE = re.compile(
    r"^[\s\u200b-\u200f\u2028-\u202f\u2060-\u2069\u206a-\u206f\ufeff\u00a0\u00ad\u034f\u061c\u180e]*$"
)

# Максимальная длина текстового сообщения которое бот обрабатывает
MAX_MESSAGE_LENGTH = 4096


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Отклоняет update'ы не из private chat + фильтрует мусорные сообщения."""

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        chat = None
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat

        # Фильтр: только private chat
        if chat and chat.type != "private":
            logger.debug(
                "Ignored non-private chat %s (type=%s)", chat.id, chat.type
            )
            return

        # Фильтр для Message: пустые и подозрительные
        if isinstance(event, Message):
            # Пропускаем служебные типы (successful_payment, photo, etc.)
            if event.successful_payment or event.photo:
                return await handler(event, data)

            text = event.text

            if text is not None:
                # Слишком длинное сообщение — игнорируем
                if len(text) > MAX_MESSAGE_LENGTH:
                    logger.warning(
                        "IGNORED_LONG_MESSAGE user=%s len=%d",
                        event.from_user.id if event.from_user else "?",
                        len(text),
                    )
                    return

                # Невидимые символы only (пустое сообщение из unicode)
                if _INVISIBLE_ONLY_RE.match(text):
                    logger.debug(
                        "IGNORED_INVISIBLE_MESSAGE user=%s",
                        event.from_user.id if event.from_user else "?",
                    )
                    return

        return await handler(event, data)
