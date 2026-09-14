"""my_chat_member: the user blocked / unblocked the bot (TG-RT-5).

Telegram sends my_chat_member when a user blocks the bot (new status
"kicked") or unblocks it ("member"); it is the only signal of a block
before the next send fails with 403. users.is_reachable is what reminders,
trial notifications and broadcasts filter on. Registering this handler also
adds "my_chat_member" to allowed_updates (main.py uses
dp.resolve_used_update_types()). Never sends anything; DB helpers swallow
their own errors.
"""
import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated

import database

logger = logging.getLogger(__name__)

reachability_router = Router()

_GONE = {"kicked", "left"}


@reachability_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated) -> None:
    if event.chat.type != "private":
        return
    telegram_id = event.chat.id
    status = str(getattr(event.new_chat_member.status, "value", event.new_chat_member.status))
    if status in _GONE:
        logger.info("USER_BLOCKED_BOT tg=%s", telegram_id)
        await database.mark_user_unreachable(telegram_id)
    elif status == "member":
        logger.info("USER_UNBLOCKED_BOT tg=%s", telegram_id)
        await database.mark_user_reachable(telegram_id)
