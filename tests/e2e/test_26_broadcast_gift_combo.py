"""Broadcast «🎁 Забрать подарок» (broadcast_gift_combo): Combo Basic / 30 at the
broadcast's own discount, paid from the gift's own payment screen. The screen
prices it round(329 × (100 − d) / 100) ₽; the Combo price guard computed the
kopeck-exact personal-discount price (32 900 − int(32 900 × d %)). Where the
screen rounds DOWN (d = 30: 230 ₽ < 230.30 ₽) the guard refused the payment on
every method (prod since 2026-09-14).

For EVERY payment method and both roundings: the gift is sold and paid, a month
of Basic + the combo GB granted.
"""
import pytest

import config
import database
import wata_service
from tests.e2e import flows
from tests.e2e.world import new_user

METHODS = ["sbp", "wata", "crypto", "card", "stars", "balance"]
BASE_RUB = config.COMBO_TARIFFS["combo_basic"][30]["price"]    # 329


@pytest.mark.parametrize("percent", [30, 25])                   # screen rounds down / up
@pytest.mark.parametrize("method", METHODS)
async def test_gift_combo_is_sold_at_the_broadcast_price(e2e, method, percent):
    u = new_user()
    await e2e.register(u, balance_rub=5000 if method == "balance" else 0)
    if method == "card":
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    e2e.provisioning("on")
    bid = await database.create_broadcast("e2e gift", "Подарок", "custom", "all_users", config.ADMIN_TELEGRAM_ID)
    await database.save_broadcast_discount(bid, percent, 24)
    offer_rub = round(BASE_RUB * (100 - percent) / 100)

    await e2e.tap(u, f"broadcast_gift_combo:{bid}")
    before = await flows.snapshot(e2e, u.id)
    last_id = await e2e.val("SELECT COALESCE(max(id), 0) FROM pending_purchases WHERE telegram_id=$1", u.id)
    await e2e.tap(u, f"pay:{method}")

    assert await e2e.rows("SELECT stage FROM payment_errors WHERE telegram_id=$1", u.id) == []
    if method == "balance":
        assert await e2e.balance(u.id) == 5000.0 - offer_rub, "the balance refused or charged another price"
    else:
        row = await flows.latest_pending(e2e, u.id)
        assert row is not None and row["id"] > last_id, (
            "the gift was refused at the payment button" + await flows.screen_evidence(e2e, u))
        assert row["is_combo"] is True
        assert offer_rub * 100 <= row["price_kopecks"] < BASE_RUB * 100, row["price_kopecks"]
        res = await flows.pay(e2e, u, row, method)
        if hasattr(res, "status"):
            assert res.body["status"] == "ok", res
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "combo_basic", 30)
