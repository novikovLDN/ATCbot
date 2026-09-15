"""Broadcast «🎁 Забрать подарок» (broadcast_gift_combo) — owner 2026-09-15: the
button turns on the broadcast's discount (percent + hours set in the wizard) and
opens the tariff screen. The old button put round(329 × (100 − d) / 100) ₽ into
the FSM; where that rounded DOWN (d = 30: 230 ₽ < the guard's 230.30 ₽) the
Combo price guard refused the payment on every method.

For EVERY payment method and both roundings: Combo Basic / 30 bought after the
gift is paid at the regular-flow discounted price, a month + the combo GB granted.
"""
from datetime import datetime, timedelta, timezone

import pytest

import config
import database
import wata_service
from tests.e2e import flows
from tests.e2e.world import new_user

METHODS = ["sbp", "wata", "crypto", "card", "stars", "balance"]
BASE_RUB = config.COMBO_TARIFFS["combo_basic"][30]["price"]    # 329


async def _gift(e2e, u, percent, hours=24):
    bid = await database.create_broadcast("e2e gift", "Подарок", "custom", "all_users", config.ADMIN_TELEGRAM_ID)
    await database.save_broadcast_discount(bid, percent, hours)
    await e2e.tap(u, f"broadcast_gift_combo:{bid}")


@pytest.mark.parametrize("percent", [30, 25])                   # the old screen rounded down / up
@pytest.mark.parametrize("method", METHODS)
async def test_gift_combo_is_paid_with_the_broadcast_discount(e2e, method, percent):
    u = new_user()
    await e2e.register(u, balance_rub=5000 if method == "balance" else 0)
    if method == "card":
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    e2e.provisioning("on")
    await _gift(e2e, u, percent)
    before = await flows.snapshot(e2e, u.id)
    price = BASE_RUB * 100 - int(BASE_RUB * 100 * percent / 100)

    pending = await flows.buy(e2e, u, "combo_basic", 30, method)

    assert await e2e.rows("SELECT stage FROM payment_errors WHERE telegram_id=$1", u.id) == []
    if pending is not None:
        assert pending["is_combo"] is True
        if method != "stars":
            assert pending["price_kopecks"] == price, pending["price_kopecks"]
        res = await flows.pay(e2e, u, pending, method)
        if hasattr(res, "status"):
            assert res.body["status"] == "ok", res
    else:
        assert await e2e.balance(u.id) == 5000.0 - price / 100
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "combo_basic", 30)


async def test_gift_combo_lasts_the_broadcast_hours(e2e):
    u = new_user()
    await e2e.register(u)
    await _gift(e2e, u, 30, hours=72)
    disc = await database.get_user_discount(u.id)
    assert disc["discount_percent"] == 30
    assert timedelta(hours=71) < disc["expires_at"] - datetime.now(timezone.utc) <= timedelta(hours=72)
