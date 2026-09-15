"""Broadcast gift buttons (owner 2026-09-15): one simple rule — a personal
discount (keep_max) for N hours, then the tariff screen. No price in the FSM:
the regular flow prices the purchase, the same way the Combo price guard does
(prod 2026-09-15: the old FSM prices were refused, COMBO_PRICE_BELOW_COMBO).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import app.handlers.common.screens as screens
from app.handlers.payments import broadcast_offers as bo
from app.i18n import get_text


@pytest.fixture
def env(monkeypatch):
    st = {"current": None, "created": None}

    async def create(**kw):
        st["created"] = kw
        cur = (st["current"] or {}).get("discount_percent", 0)
        if kw["discount_percent"] >= cur:                     # keep_max
            st["current"] = {"discount_percent": kw["discount_percent"]}
        return True

    monkeypatch.setattr(database, "create_user_discount", AsyncMock(side_effect=create))
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(side_effect=lambda _tg: st["current"]))
    st["broadcast"] = AsyncMock(return_value={"discount_percent": 25, "discount_hours": 72})
    monkeypatch.setattr(database, "get_broadcast_discount", st["broadcast"])
    monkeypatch.setattr(bo, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(bo.asyncio, "sleep", AsyncMock())
    st["tariffs"] = AsyncMock()
    monkeypatch.setattr(screens, "show_tariffs_main_screen", st["tariffs"])
    return st


def _press(data):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = 7
    cb.message.chat.id = 7
    cb.answer = AsyncMock()
    cb.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    cb.bot.delete_message = AsyncMock()
    state = MagicMock()
    state.update_data = AsyncMock()
    return cb, state


def _sent(cb):
    return [c.args[1] for c in cb.bot.send_message.await_args_list]


@pytest.mark.parametrize("data,handler,pct,hours", [
    ("broadcast_gift_1m", "callback_broadcast_gift_1m", 30, 24),
    ("bcg1m:buy:combo_plus", "callback_broadcast_gift_1m", 30, 24),
    ("broadcast_gift_3m", "callback_broadcast_gift_3m", 30, 24),
    ("bcg3m:info", "callback_broadcast_gift_3m", 30, 24),
    ("broadcast_gift_1y_40", "callback_broadcast_gift_1y_40", 40, 24),
    ("bcg1y40:buy:combo_plus:365", "callback_broadcast_gift_1y_40", 40, 24),
    ("broadcast_gift_combo:5", "callback_broadcast_gift_combo", 25, 72),
])
async def test_gift_button_is_a_discount_then_the_tariff_screen(env, data, handler, pct, hours):
    cb, state = _press(data)
    before = datetime.now(timezone.utc)

    await getattr(bo, handler)(cb, state)

    kw = env["created"]
    assert kw["discount_percent"] == pct and kw["keep_max"] is True
    assert before + timedelta(hours=hours) <= kw["expires_at"] <= datetime.now(timezone.utc) + timedelta(hours=hours)
    env["tariffs"].assert_awaited_once()
    assert [c.kwargs for c in state.update_data.await_args_list] == [{"from_broadcast": True}]   # no FSM price
    assert get_text("ru", "broadcast.gift_discount_applied", percent=pct, hours=hours) in _sent(cb)


async def test_the_1y40_button_plays_the_reveal_first(env):
    cb, state = _press("broadcast_gift_1y_40")
    await bo.callback_broadcast_gift_1y_40(cb, state)
    assert _sent(cb)[0] == bo._GIFT1Y40_REVEAL_EMOJI
    cb.bot.delete_message.assert_awaited_once()


async def test_a_bigger_active_discount_stays_and_is_named(env):
    env["current"] = {"discount_percent": 50}
    cb, state = _press("broadcast_gift_1m")
    await bo.callback_broadcast_gift_1m(cb, state)
    assert get_text("ru", "main.discount_bigger_kept", percent=50) in _sent(cb)
    env["tariffs"].assert_awaited_once()


async def test_gift_combo_without_a_discount_opens_the_tariffs(env):
    env["broadcast"].return_value = None
    cb, state = _press("broadcast_gift_combo:5")
    await bo.callback_broadcast_gift_combo(cb, state)
    database.create_user_discount.assert_not_awaited()
    env["tariffs"].assert_awaited_once()
