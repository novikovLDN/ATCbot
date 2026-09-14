"""P2: dashboard «test send» of an automated notification always failed.

The route did `from main import bot`, but `bot` is a local inside
`async def main()` — never a module attribute (and the app runs as
`python main.py`, so the import even re-executes main.py as a second module).
Every call → 500 "bot instance unavailable". It now uses the live bot set by
main.py on app.api.telegram_webhook, like the broadcast routes.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api import telegram_webhook
from app.api.dashboard.routes import automated_notifications as an


class _Conn:
    async def fetchrow(self, sql, *args):
        return {"title": "Напоминание"}


class _Pool:
    def acquire(self):
        class _A:
            async def __aenter__(self_inner):
                return _Conn()

            async def __aexit__(self_inner, *exc):
                return False
        return _A()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(an, "get_row", AsyncMock(return_value={"text": "Привет, {name}"}))

    async def get_pool():
        return _Pool()
    monkeypatch.setattr(an, "get_pool", get_pool)


async def test_test_send_uses_the_live_bot(env, monkeypatch):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    monkeypatch.setattr(telegram_webhook, "_bot", bot, raising=False)

    out = await an.test_send_notification(key="reminder_3d", admin={"sub": "42"})

    assert out == {"ok": True, "sent_to": 42, "key": "reminder_3d"}
    assert bot.send_message.await_args.kwargs["chat_id"] == 42


async def test_test_send_without_bot_is_503(env, monkeypatch):
    monkeypatch.setattr(telegram_webhook, "_bot", None, raising=False)
    with pytest.raises(HTTPException) as ei:
        await an.test_send_notification(key="reminder_3d", admin={"sub": "42"})
    assert ei.value.status_code == 503
