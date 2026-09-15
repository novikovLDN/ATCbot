"""Broadcast period gift buttons «−40% на 1 год» / «−30% на 1 месяц» /
«−30% на 3 месяца» (owner 2026-09-15): the button turns on a discount on ITS
period only (any plan of it) for 24 h and opens the tariff screen; the user
buys there, now or later, at the regular-flow price — the price the Combo price
guard computes too. «If the button says 1 year −40 %, it is 1 year −40 %.»

Prod 2026-09-15 (COMBO_PRICE_BELOW_COMBO user=502708): the old buttons put their
own price into the FSM and the guard refused every paid Combo gift.
"""
from datetime import datetime, timedelta, timezone

import pytest

import config
import database
import wata_service
from tests.e2e import flows
from tests.e2e.world import new_user

METHODS = ["sbp", "wata", "crypto", "card", "stars", "balance"]


def _off(rub: int, pct: int) -> int:
    """calculate_final_price: base − int(base × pct %), kopecks."""
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
    price = _off(config.COMBO_TARIFFS["combo_plus"][365]["price"], 40)

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


@pytest.mark.parametrize("button,pct,period,other", [
    ("broadcast_gift_1m", 30, 30, 90), ("broadcast_gift_3m", 30, 90, 30), ("broadcast_gift_1y_40", 40, 365, 30),
    # the inner buttons of broadcasts already sent do the same
    ("bcg1m:buy:combo_plus", 30, 30, 365), ("bcg3m:info", 30, 90, 365), ("bcg1y40:buy:combo_plus:365", 40, 365, 90),
])
async def test_period_gift_discounts_only_its_period_for_24h(e2e, button, pct, period, other):
    u = new_user()
    await _prepare(e2e, u, "sbp", "on")
    await e2e.tap(u, button)

    row = await e2e.row("SELECT discount_percent, expires_at FROM user_period_discounts "
                        "WHERE telegram_id=$1 AND period_days=$2", u.id, period)
    assert row and row["discount_percent"] == pct
    left = row["expires_at"] - datetime.now(timezone.utc).replace(tzinfo=None)    # naive UTC column
    assert timedelta(hours=23) < left <= timedelta(hours=24)
    assert await database.get_user_discount(u.id) is None, "the general personal discount was touched"

    on_period = await flows.buy(e2e, u, "basic", period, "sbp")
    assert on_period["price_kopecks"] == _off(config.TARIFFS["basic"][period]["price"], pct)
    off_period = await flows.buy(e2e, u, "basic", other, "sbp")
    assert off_period["price_kopecks"] == config.TARIFFS["basic"][other]["price"] * 100, "discount leaked"
    assert await database.get_user_traffic_discount(u.id) is None     # bypass GB packs: no discount


async def test_a_general_discount_stays_on_the_other_periods(e2e):
    u = new_user()
    await _prepare(e2e, u, "sbp", "on")
    await database.create_user_discount(
        telegram_id=u.id, discount_percent=15,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=5), created_by=0)
    await e2e.tap(u, "broadcast_gift_1y_40")

    month = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert month["price_kopecks"] == _off(config.TARIFFS["basic"][30]["price"], 15)
    year = await flows.buy(e2e, u, "basic", 365, "sbp")
    assert year["price_kopecks"] == _off(config.TARIFFS["basic"][365]["price"], 40)
    assert (await database.get_user_discount(u.id))["discount_percent"] == 15
