"""Telegram-runtime unit tests (docs/audit/11_telegram_runtime.md).

Hermetic counterparts of tests/e2e/test_20_telegram_runtime.py: middleware,
safe_send_message, admin alerts and broadcast pacing against Telegram errors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message


def _msg(**content) -> Message:
    base = {
        "message_id": 1,
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "U"},
    }
    base.update(content)
    return Message.model_validate(base)


# ── TG-RT-1: rate limit never swallows a payment service message ────────

_PAID = {"currency": "RUB", "total_amount": 19900, "invoice_payload": "purchase:p1",
         "telegram_payment_charge_id": "c1", "provider_payment_charge_id": "pc1"}
_REFUNDED = {"currency": "XTR", "total_amount": 100, "invoice_payload": "purchase:p1",
             "telegram_payment_charge_id": "c1"}


@pytest.mark.parametrize("content", [{"successful_payment": _PAID}, {"refunded_payment": _REFUNDED}],
                         ids=["successful_payment", "refunded_payment"])
async def test_flood_banned_user_payment_messages_reach_the_handler(monkeypatch, content):
    import time

    from app.core import rate_limit_middleware as rlm
    mw = rlm.GlobalRateLimitMiddleware()
    monkeypatch.setattr(mw, "_get_redis", AsyncMock(return_value=None))
    mw._banned_users[42] = time.monotonic() + 300          # flood ban is on
    handler = AsyncMock(return_value="handled")

    assert await mw(handler, _msg(text="hi"), {}) is None   # ordinary message: dropped
    assert await mw(handler, _msg(**content), {}) == "handled"
    handler.assert_awaited_once()


# ── TG-RT-6: a late callback answer does not abort the handler ─────────

def _raiser(message: str):
    from aiogram.exceptions import TelegramBadRequest

    async def make_request(bot, method):
        raise TelegramBadRequest(method=method, message=message)
    return make_request


async def test_stale_callback_answer_is_ignored_other_errors_still_raise():
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import AnswerCallbackQuery, EditMessageText

    from app.utils.telegram_request_middleware import IgnoreStaleCallbackAnswer
    mw = IgnoreStaleCallbackAnswer()
    answer = AnswerCallbackQuery(callback_query_id="1")
    too_old = "Bad Request: query is too old and response timeout expired or query ID is invalid"

    assert await mw(_raiser(too_old), None, answer) is True
    with pytest.raises(TelegramBadRequest):          # same text, another method
        await mw(_raiser(too_old), None, EditMessageText(chat_id=1, message_id=1, text="x"))
    with pytest.raises(TelegramBadRequest):          # another error on the answer
        await mw(_raiser("Bad Request: MESSAGE_TOO_LONG"), None, answer)

    async def ok(bot, method):
        return True
    assert await mw(ok, None, answer) is True


# ── TG-RT-7: a short flood wait does not lose the message ──────────────

def _flood(seconds: int):
    from unittest.mock import MagicMock

    from aiogram.exceptions import TelegramRetryAfter
    return TelegramRetryAfter(method=MagicMock(), message=f"Too Many Requests: retry after {seconds}",
                              retry_after=seconds)


async def test_short_flood_wait_is_slept_out_and_sent_once_more(monkeypatch):
    from unittest.mock import MagicMock

    from app.utils import telegram_safe
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_safe.asyncio, "sleep", sleep)
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[_flood(2), "sent"])
    assert await telegram_safe.safe_send_message(bot, 1, "x") == "sent"
    sleep.assert_awaited_once_with(2)
    assert bot.send_message.await_count == 2


async def test_flood_wait_retries_only_once_and_long_waits_are_dropped(monkeypatch):
    from unittest.mock import MagicMock

    from app.utils import telegram_safe
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_safe.asyncio, "sleep", sleep)
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[_flood(1), _flood(1), "never"])
    assert await telegram_safe.safe_send_message(bot, 1, "x") is None     # no retry storm
    assert bot.send_message.await_count == 2

    bot.send_message = AsyncMock(side_effect=[_flood(60), "never"])
    sleep.reset_mock()
    assert await telegram_safe.safe_send_message(bot, 1, "x") is None
    assert bot.send_message.await_count == 1 and sleep.await_count == 0

    from aiogram.exceptions import TelegramRetryAfter
    bot.send_message = AsyncMock(side_effect=[_flood(1)])
    with pytest.raises(TelegramRetryAfter):                                # broadcasts pace themselves
        await telegram_safe.safe_send_message(bot, 1, "x", raise_retry_after=True)


def test_main_installs_the_request_middleware():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert "install_request_middlewares(bot)" in src


# ── TG-RT-10: a broadcast stays under Telegram's ~30 msg/s ──────────────

async def test_broadcast_is_paced_under_the_global_telegram_limit(monkeypatch):
    """BROADCAST_CONCURRENCY=15 limits parallelism, not rate: with a fast Bot
    API every slot sends again at once, so 200 messages left in well under a
    second and Telegram answered with a 429 storm. Sends must stay ≤ 30 in
    any 1-second window (Bot API FAQ: ~30 messages per second)."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import database
    from app.services import broadcast_sender as bs

    clock = {"t": 0.0}
    real_sleep = asyncio.sleep

    async def fake_sleep(delay=0, result=None):
        clock["t"] += max(float(delay or 0), 0.0)
        return await real_sleep(0, result)
    monkeypatch.setattr(bs.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(bs, "_clock", lambda: clock["t"], raising=False)
    sent_at = []

    async def fake_safe_send(bot, uid, text, **kw):
        sent_at.append(clock["t"])
        return SimpleNamespace(message_id=uid)
    monkeypatch.setattr(bs, "safe_send_message", fake_safe_send)
    monkeypatch.setattr(database, "log_broadcast_send", AsyncMock(), raising=False)
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", AsyncMock(), raising=False)

    res = await bs.send_broadcast(bot=MagicMock(), broadcast_id=1, user_ids=list(range(1, 121)), message="hi")

    assert res["sent"] == 120
    worst = max(sum(1 for t in sent_at if s <= t < s + 1.0) for s in sent_at)
    assert worst <= 30, f"{worst} messages in one second"
