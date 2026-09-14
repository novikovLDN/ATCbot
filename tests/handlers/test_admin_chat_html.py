"""P2: «Написать пользователю» sent the admin's raw message.text with
parse_mode=HTML. A text like "a < b" or "<3" → Telegram "can't parse
entities" → not delivered; any bold/italic the admin applied was lost.
Now the entity-preserving, escaped message.html_text / html_caption is sent.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import config
from app.handlers.admin import base


def _message(**kw):
    m = MagicMock()
    m.from_user.id = config.ADMIN_TELEGRAM_ID
    m.photo = None
    m.document = None
    m.sticker = None
    m.answer = AsyncMock()
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def _state():
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"chat_target_id": 555, "chat_target_name": "user"})
    return state


async def test_text_is_sent_as_escaped_html():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    msg = _message(text="a < b", html_text="a &lt; b")
    await base.process_admin_chat_message(msg, _state(), bot)
    assert bot.send_message.await_args.kwargs["text"] == "a &lt; b"
    assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"


async def test_photo_caption_is_sent_as_escaped_html():
    bot = MagicMock()
    bot.send_photo = AsyncMock()
    photo = MagicMock()
    photo.file_id = "f1"
    msg = _message(text=None, photo=[photo], caption="<3", html_caption="&lt;3")
    await base.process_admin_chat_message(msg, _state(), bot)
    assert bot.send_photo.await_args.kwargs["caption"] == "&lt;3"
