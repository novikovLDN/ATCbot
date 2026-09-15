"""Broadcast gift buttons (owner 2026-09-15): a discount for N hours, then the
tariff screen. «−30% на 1 месяц» / «−30% на 3 месяца» / «−40% на 1 год»
discount ONLY their period (user_period_discounts); «Забрать подарок» is the
general personal discount. No price in the FSM: the regular flow prices the
purchase, the same way the Combo price guard does (prod 2026-09-15: the old FSM
prices were refused, COMBO_PRICE_BELOW_COMBO).
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
    st = {"general": None, "period": {}, "user_calls": [], "period_calls": []}

    async def create_user(**kw):
        st["user_calls"].append(kw)
        if kw["discount_percent"] >= (st["general"] or {}).get("discount_percent", 0):
            st["general"] = {"discount_percent": kw["discount_percent"]}
        return True

    async def create_period(tg, period_days, percent, expires_at, source):
        st["period_calls"].append((period_days, percent, expires_at, source))
        if percent >= (st["period"].get(period_days) or {}).get("discount_percent", 0):
            st["period"][period_days] = {"discount_percent": percent}

    monkeypatch.setattr(database, "create_user_discount", AsyncMock(side_effect=create_user))
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(side_effect=lambda _tg: st["general"]))
    monkeypatch.setattr(database, "create_period_discount", AsyncMock(side_effect=create_period), raising=False)
    monkeypatch.setattr(database, "get_period_discount",
                        AsyncMock(side_effect=lambda _tg, p: st["period"].get(p)), raising=False)
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


@pytest.mark.parametrize("data,handler,pct,period", [
    ("broadcast_gift_1m", "callback_broadcast_gift_1m", 30, 30),
    ("bcg1m:buy:combo_plus", "callback_broadcast_gift_1m", 30, 30),
    ("broadcast_gift_3m", "callback_broadcast_gift_3m", 30, 90),
    ("bcg3m:info", "callback_broadcast_gift_3m", 30, 90),
    ("broadcast_gift_1y_40", "callback_broadcast_gift_1y_40", 40, 365),
    ("bcg1y40:buy:combo_plus:365", "callback_broadcast_gift_1y_40", 40, 365),
])
async def test_period_gift_discounts_only_its_period(env, data, handler, pct, period):
    cb, state = _press(data)
    before = datetime.now(timezone.utc)

    await getattr(bo, handler)(cb, state)

    ((got_period, got_pct, expires_at, _src),) = env["period_calls"]
    assert (got_period, got_pct) == (period, pct)
    assert before + timedelta(hours=24) <= expires_at <= datetime.now(timezone.utc) + timedelta(hours=24)
    assert env["user_calls"] == [], "a period gift must not touch the general personal discount"
    env["tariffs"].assert_awaited_once()
    assert [c.kwargs for c in state.update_data.await_args_list] == [{"from_broadcast": True}]   # no FSM price
    expected = get_text("ru", "broadcast.period_discount_applied", percent=pct, hours=24,
                        period=get_text("ru", f"broadcast.period_{period}"))
    assert expected in _sent(cb)


async def test_gift_combo_is_the_general_discount_with_the_broadcast_hours(env):
    cb, state = _press("broadcast_gift_combo:5")
    before = datetime.now(timezone.utc)
    await bo.callback_broadcast_gift_combo(cb, state)
    (kw,) = env["user_calls"]
    assert kw["discount_percent"] == 25 and kw["keep_max"] is True
    assert before + timedelta(hours=72) <= kw["expires_at"] <= datetime.now(timezone.utc) + timedelta(hours=72)
    assert env["period_calls"] == []
    assert get_text("ru", "broadcast.gift_discount_applied", percent=25, hours=72) in _sent(cb)
    env["tariffs"].assert_awaited_once()


async def test_the_1y40_button_plays_the_reveal_first(env):
    cb, state = _press("broadcast_gift_1y_40")
    await bo.callback_broadcast_gift_1y_40(cb, state)
    assert _sent(cb)[0] == bo._GIFT1Y40_REVEAL_EMOJI
    cb.bot.delete_message.assert_awaited_once()


async def test_a_bigger_general_discount_is_named(env):
    env["general"] = {"discount_percent": 50}
    cb, state = _press("broadcast_gift_1y_40")
    await bo.callback_broadcast_gift_1y_40(cb, state)
    assert get_text("ru", "main.discount_bigger_kept", percent=50) in _sent(cb)
    env["tariffs"].assert_awaited_once()


async def test_gift_combo_without_a_discount_opens_the_tariffs(env):
    env["broadcast"].return_value = None
    cb, state = _press("broadcast_gift_combo:5")
    await bo.callback_broadcast_gift_combo(cb, state)
    assert env["user_calls"] == [] and env["period_calls"] == []
    env["tariffs"].assert_awaited_once()
