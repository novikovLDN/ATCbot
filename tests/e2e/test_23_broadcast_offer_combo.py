"""Broadcast gift buttons «−40% на 1 год» / «−30% на 1 месяц» / «−30% на 3 месяца»
(owner 2026-09-15): the button turns on a personal discount for 24 h and opens
the tariff screen; the user buys any plan / period there, now or later, at the
regular-flow price — the same price the Combo price guard computes.

Prod 2026-09-15 (COMBO_PRICE_BELOW_COMBO user=502708): the old buttons put their
own price into the FSM and the guard refused every paid Combo offer.
"""
from datetime import datetime, timedelta, timezone

import pytest

import database
import wata_service
from tests.e2e import flows
from tests.e2e.world import new_user

METHODS = ["sbp", "wata", "crypto", "card", "stars", "balance"]


def _discounted_kopecks(rub: int, pct: int) -> int:
    """calculate_final_price: base − int(base × pct %)."""
    base = rub * 100
    return base - int(base * pct / 100)


async def _prepare(e2e, u, method, flag):
    await e2e.register(u, balance_rub=5000 if method == "balance" else 0)
    if method == "card":
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    e2e.provisioning(flag)


def _cases():
    """Legacy flag-off pipeline grants the wrong combo GB for ANY Combo purchase
    on the Telegram / balance paths (not gift-specific; the default flag «on»
    is right): card / Stars 2 × combo GB, balance combo GB + 10."""
    for flag in ("off", "on"):
        for method in METHODS:
            marks = []
            if flag == "off" and method in ("card", "stars", "balance"):
                marks = [pytest.mark.xfail(strict=True, reason="legacy flag-off bug T0-COMBO-GB-TG: "
                                           "Telegram/balance Combo purchase grants 2 × combo GB / combo GB + 10")]
            yield pytest.param(method, flag, marks=marks, id=f"{method}-{flag}")


@pytest.mark.parametrize("method,flag", list(_cases()))
async def test_gift_1y40_then_combo_plus_year_is_paid_with_the_discount(e2e, method, flag):
    u = new_user()
    await _prepare(e2e, u, method, flag)
    await e2e.tap(u, "broadcast_gift_1y_40")
    before = await flows.snapshot(e2e, u.id)
    price = _discounted_kopecks(3999, 40)

    pending = await flows.buy(e2e, u, "combo_plus", 365, method)

    assert await e2e.rows("SELECT stage FROM payment_errors WHERE telegram_id=$1", u.id) == []
    if pending is not None:
        assert pending["is_combo"] is True
        if method != "stars":                           # Stars rows keep their own RUB rule
            assert pending["price_kopecks"] == price, pending["price_kopecks"]
        res = await flows.pay(e2e, u, pending, method)
        if hasattr(res, "status"):
            assert res.body["status"] == "ok", res
    else:
        assert await e2e.balance(u.id) == 5000.0 - price / 100
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "combo_plus", 365)


@pytest.mark.parametrize("button,pct", [
    ("broadcast_gift_1m", 30), ("broadcast_gift_3m", 30), ("broadcast_gift_1y_40", 40),
    # the inner buttons of broadcasts already sent do the same
    ("bcg1m:buy:combo_plus", 30), ("bcg3m:info", 30), ("bcg1y40:buy:combo_plus:365", 40),
])
async def test_gift_button_is_a_24h_discount_on_any_plan(e2e, button, pct):
    u = new_user()
    await _prepare(e2e, u, "sbp", "on")
    await e2e.tap(u, button)

    disc = await database.get_user_discount(u.id)
    assert disc and disc["discount_percent"] == pct
    left = disc["expires_at"] - datetime.now(timezone.utc)
    assert timedelta(hours=23) < left <= timedelta(hours=24)
    # any plan and period, not only the one named on the button
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert pending["is_combo"] is False and pending["price_kopecks"] == _discounted_kopecks(199, pct)
    assert await database.get_user_traffic_discount(u.id) is None     # bypass GB packs: no discount


async def test_a_bigger_active_discount_stays(e2e):
    u = new_user()
    await _prepare(e2e, u, "sbp", "on")
    await database.create_user_discount(
        telegram_id=u.id, discount_percent=50,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=5), created_by=0)
    await e2e.tap(u, "broadcast_gift_1m")
    assert (await database.get_user_discount(u.id))["discount_percent"] == 50
