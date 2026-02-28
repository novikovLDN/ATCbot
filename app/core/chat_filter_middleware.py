"""
Middleware: отклоняет все update'ы не из private chat.
Бот работает ТОЛЬКО в личных сообщениях.
"""
import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Отклоняет все update'ы, которые НЕ из private chat."""

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        # Определяем chat из event
        chat = None
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat

        # Если chat определён и он НЕ private — игнорируем
        if chat and chat.type != "private":
            logger.debug(
                "Ignored non-private message from chat %s (type=%s)", chat.id, chat.type
            )
            return  # Молча игнорируем, НЕ отвечаем

        return await handler(event, data)
