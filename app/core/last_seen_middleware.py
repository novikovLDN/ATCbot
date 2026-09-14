"""
Touch users.last_seen_at on every interaction.

Used by the Farm storm mechanic: at storm execution we need to know
whether the user actually saw the 24h warning, and "saw it" boils down
to "had any activity in the bot after announced_at".

Also restores users.is_reachable = TRUE (N-02, docs/notifications/): any
message or button press — /start included — proves the chat is reachable
again after a "blocked" / "chat not found" set it FALSE.

Fire-and-forget — must never block or fail the surrounding handler.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


class LastSeenMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        telegram_id = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            telegram_id = event.from_user.id

        if telegram_id is not None:
            # Fire-and-forget — never await, never block the handler.
            try:
                asyncio.create_task(_touch_user(telegram_id))
            except Exception as e:
                logger.warning("last_seen scheduling failed: %s", type(e).__name__)

        return await handler(event, data)


async def _touch_user(telegram_id: int) -> None:
    """One background task per update: last_seen_at, then is_reachable (N-02).
    Both database calls swallow their own errors."""
    import database
    await database.touch_last_seen(telegram_id)
    await database.mark_user_reachable(telegram_id)
