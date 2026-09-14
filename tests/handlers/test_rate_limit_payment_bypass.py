"""TG-RT-1 (port of refactor aee90cb7): the global rate limiter must never
swallow a payment service message of a flood-banned user — Telegram already
took the money and never resends the update."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from tests.handlers._tg_dispatch import PAID, REFUNDED, message_update


def _msg(**content) -> Message:
    return message_update(**content).message


@pytest.mark.parametrize("content", [{"successful_payment": PAID}, {"refunded_payment": REFUNDED}],
                         ids=["successful_payment", "refunded_payment"])
async def test_flood_banned_user_payment_messages_reach_the_handler(monkeypatch, content):
    from app.core import rate_limit_middleware as rlm
    mw = rlm.GlobalRateLimitMiddleware()
    monkeypatch.setattr(mw, "_get_redis", AsyncMock(return_value=None))
    mw._banned_users[42] = time.monotonic() + 300          # flood ban is on
    handler = AsyncMock(return_value="handled")

    assert await mw(handler, _msg(text="hi"), {}) is None   # ordinary message: dropped
    assert await mw(handler, _msg(**content), {}) == "handled"
    handler.assert_awaited_once()


async def test_payment_messages_are_not_counted_towards_the_limit(monkeypatch):
    from app.core import rate_limit_middleware as rlm
    mw = rlm.GlobalRateLimitMiddleware()
    monkeypatch.setattr(mw, "_get_redis", AsyncMock(return_value=None))
    handler = AsyncMock(return_value="handled")
    for _ in range(rlm.FLOOD_BAN_THRESHOLD + 5):
        assert await mw(handler, _msg(successful_payment=PAID), {}) == "handled"
    assert 42 not in mw._banned_users
    assert await mw(handler, _msg(text="hi"), {}) == "handled"
