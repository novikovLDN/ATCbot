"""docs/audit/10_telegram_static.md §4 / open item 4: admin.degraded_mode,
admin.recovered and admin.pending_activations_* used Markdown (**…**, `…`)
but were sent with parse_mode=None — the admin saw literal asterisks and
backticks. They are HTML now, sent with parse_mode="HTML"; values that come
from outside (the panel error text) are escaped, so the message is always
valid for Telegram's parser.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import admin_notifications as an
import config
import database
from app.utils.telegram_html import telegram_html_errors, visible_text


@pytest.fixture
def bot(monkeypatch, request):
    lang = getattr(request, "param", "ru")

    async def get_user(telegram_id):
        return {"telegram_id": telegram_id, "language": lang}

    monkeypatch.setattr(database, "get_user", get_user)
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 111)
    monkeypatch.setattr(an, "_last_pending_notification_time", None)
    an.reset_notification_flags()
    yield MagicMock(send_message=AsyncMock())
    an.reset_notification_flags()


def _sent(bot):
    bot.send_message.assert_awaited_once()
    call = bot.send_message.await_args
    return call.args[1], call.kwargs.get("parse_mode")


def _assert_clean_html(text, parse_mode):
    assert parse_mode == "HTML"
    assert telegram_html_errors(text) == []
    assert "**" not in visible_text(text) and "`" not in text


@pytest.mark.parametrize("bot", ["ru", "en"], indirect=True)
async def test_degraded_mode_is_html(bot):
    await an.notify_admin_degraded_mode(bot)
    _assert_clean_html(*_sent(bot))


@pytest.mark.parametrize("bot", ["ru", "en"], indirect=True)
async def test_recovered_is_html(bot):
    await an.notify_admin_recovered(bot)
    _assert_clean_html(*_sent(bot))


@pytest.mark.parametrize("bot", ["ru", "en"], indirect=True)
async def test_pending_activations_is_html_with_hostile_error(bot):
    hostile = "<b>boom & `x` **y** <script>"
    await an.notify_admin_pending_activations(bot, 3, [{
        "subscription_id": 17, "telegram_id": 42, "attempts": 5,
        "error": hostile, "pending_since": datetime(2026, 9, 14, 12, 30),
    }])
    text, parse_mode = _sent(bot)
    assert parse_mode == "HTML"
    assert telegram_html_errors(text) == []
    assert "**" not in text.replace("**y**", "")      # only the hostile value may carry them
    assert hostile in visible_text(text), "the panel error must be shown verbatim, escaped"
    assert "17" in text and "42" in text and "14.09.2026 12:30" in text
