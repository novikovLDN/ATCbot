"""HOW_IT_WORKS P2 «alerts over budget may be silently dropped» (admin_alerts.py:58-61, :75).

- A non-forced alert inside the category cooldown was dropped (return False,
  nothing kept). Now it is buffered and delivered as ONE digest when the
  cooldown ends: never lost.
- A send that failed even after the retry is buffered the same way.
- Forced alerts no longer start the category cooldown (they used to silence
  the next ordinary alert of the category).
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

import config
from app.services import admin_alerts


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    admin_alerts.reset_state()
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 1)
    monkeypatch.setattr(admin_alerts, "_RETRY_DELAY", 0)
    monkeypatch.setattr(admin_alerts, "_DIGEST_MIN_DELAY", 0.01)
    yield
    admin_alerts.reset_state()


def _bot(fail_times=0):
    bot = AsyncMock()
    calls = {"n": 0}

    async def send_message(chat_id, text, **kw):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise RuntimeError("telegram down")
        bot.sent.append(text)
    bot.sent = []
    bot.send_message = send_message
    return bot


async def test_forced_alert_does_not_start_the_cooldown():
    bot = _bot()
    assert await admin_alerts.send_alert(bot, "payment", "forced one", force=True)
    assert await admin_alerts.send_alert(bot, "payment", "ordinary one")
    assert len(bot.sent) == 2


async def test_alert_inside_cooldown_is_delivered_in_a_digest(monkeypatch):
    monkeypatch.setitem(admin_alerts._ALERT_COOLDOWNS, "payment", 0.1)
    bot = _bot()
    assert await admin_alerts.send_alert(bot, "payment", "first")
    assert not await admin_alerts.send_alert(bot, "payment", "second: renewal failed tg=42")
    assert not await admin_alerts.send_alert(bot, "payment", "third: GB not delivered tg=43")
    assert admin_alerts.pending_digest_counts() == {"payment": 2}
    assert len(bot.sent) == 1

    await asyncio.sleep(0.4)          # the digest goes out when the cooldown ends
    assert len(bot.sent) == 2
    digest = bot.sent[1]
    assert "2" in digest and "tg=42" in digest and "tg=43" in digest
    assert admin_alerts.pending_digest_counts() == {}


async def test_failed_send_is_kept_and_retried_in_a_digest():
    bot = _bot(fail_times=2)          # the send and its retry both fail
    assert not await admin_alerts.send_alert(bot, "payment", "lost? tg=7", force=True)
    assert admin_alerts.pending_digest_counts() == {"payment": 1}
    await asyncio.sleep(0.2)
    assert any("tg=7" in t for t in bot.sent)
    assert admin_alerts.pending_digest_counts() == {}


async def test_unreachable_admin_chat_is_retried_with_backoff_not_in_a_loop():
    """TG-RT-8 (docs/audit/11_telegram_runtime.md): the admin blocked the bot /
    ADMIN_TELEGRAM_ID is wrong. A failed digest re-held itself and flushed
    again after _DIGEST_MIN_DELAY — forever (every ~5 s in prod: 2 Bot API
    calls + 2 ERROR logs per round). Now the retry delay backs off; nothing is
    dropped (the count stays)."""
    calls = {"n": 0}
    bot = AsyncMock()

    async def send_message(chat_id, text, **kw):
        calls["n"] += 1
        raise RuntimeError("Forbidden: bot was blocked by the user")
    bot.send_message = send_message

    assert not await admin_alerts.send_alert(bot, "payment", "lost? tg=9", force=True)
    await asyncio.sleep(0.6)
    assert calls["n"] <= 16, calls          # ~80 without backoff
    assert admin_alerts.pending_digest_counts() == {"payment": 1}


async def test_digest_backoff_resets_once_the_admin_chat_works_again():
    state = {"fail": True}
    bot = AsyncMock()
    bot.sent = []

    async def send_message(chat_id, text, **kw):
        if state["fail"]:
            raise RuntimeError("chat not found")
        bot.sent.append(text)
    bot.send_message = send_message

    await admin_alerts.send_alert(bot, "payment", "tg=11", force=True)
    await asyncio.sleep(0.1)
    state["fail"] = False
    await asyncio.sleep(0.5)
    assert any("tg=11" in t for t in bot.sent)
    assert admin_alerts.pending_digest_counts() == {}
    assert await admin_alerts.send_alert(bot, "payment", "next", force=True)


async def test_digest_is_capped_but_counts_are_exact(monkeypatch):
    monkeypatch.setitem(admin_alerts._ALERT_COOLDOWNS, "worker", 0.1)
    bot = _bot()
    await admin_alerts.send_alert(bot, "worker", "first")
    for i in range(100):
        await admin_alerts.send_alert(bot, "worker", f"alert {i}")
    assert admin_alerts.pending_digest_counts() == {"worker": 100}
    await asyncio.sleep(0.4)
    assert len(bot.sent) == 2
    assert "100" in bot.sent[1] and len(bot.sent[1]) <= 4000
