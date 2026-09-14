"""P0 9c497027 — a stale Combo flag in the FSM (Combo screen, then Back →
Basic/Plus) sold a Combo purchase (combo GB) at the Basic/Plus price. The
random invariant test reproduced it independently: +190 GB = Plus 90 sold as
Combo Plus 90.

For EVERY payment method:
  * open Combo → Back → buy Basic 1 month → a plain Basic purchase: Basic
    price, +10 GB, not combo;
  * a forged FSM (combo flag + Basic price) → the purchase is refused: no
    combo purchase row, no money taken, no combo GB.
Owner rules (docs/audit/SCOPE.md 2026-09-14): Basic/Plus renewal +10 GB each
time; Combo = combo-table GB for the period.
"""
import pytest
from aiogram.fsm.storage.base import StorageKey

import config
import wata_service
from app.handlers.common.states import PurchaseState
from tests.e2e import flows
from tests.e2e.world import GIB, new_user

METHODS = ["sbp", "wata", "crypto", "card", "stars", "balance"]


async def _prepare(e2e, u, method):
    await e2e.register(u, balance_rub=1000 if method == "balance" else 0)
    if method == "card":
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("method", METHODS)
async def test_combo_then_back_then_basic_is_a_plain_basic_purchase(e2e, method, flag):
    u = new_user()
    await _prepare(e2e, u, method)
    e2e.provisioning(flag)
    # the Combo screens put combo_bypass_gb into the FSM …
    await e2e.tap(u, "buy_combo")
    await e2e.tap(u, "combo_tariff:combo_basic")
    await e2e.tap(u, "combo_period:combo_basic:30")
    assert e2e.tg.with_button(u.id, f"pay:{method}") or method == "card"
    # … then the user goes Back and buys a regular Basic month
    await e2e.tap(u, "menu_buy_vpn")
    before = await flows.snapshot(e2e, u.id)
    pending = await flows.buy(e2e, u, "basic", 30, method)
    if pending is not None:
        assert pending["is_combo"] is False, "stale Combo flag reached the purchase row"
        expected = 19_900 if method != "stars" else pending["price_kopecks"]
        assert pending["price_kopecks"] == expected
        res = await flows.pay(e2e, u, pending, method)
        if hasattr(res, "status"):
            assert res.body["status"] == "ok", res
    else:
        assert await e2e.balance(u.id) == 1000.0 - 199.0
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "basic", 30, bypass_gain=10 * GIB)
    sub = await e2e.sub(u.id)
    assert not sub.get("is_combo"), "a Basic purchase made the subscription Combo"


@pytest.mark.parametrize("method", METHODS)
async def test_forged_fsm_combo_flag_with_basic_price_is_refused(e2e, method):
    u = new_user()
    await _prepare(e2e, u, method)
    key = StorageKey(bot_id=e2e.bot.id, chat_id=u.id, user_id=u.id)
    await e2e.dp.fsm.storage.set_state(key, PurchaseState.choose_payment_method)
    await e2e.dp.fsm.storage.set_data(key, {
        "tariff_type": "basic", "period_days": 30, "final_price_kopecks": 19_900,
        "discount_percent": 0, "combo_bypass_gb": 75,
    })
    mark = e2e.tg.mark()
    await e2e.tap(u, f"pay:{method}")
    # a Combo row may exist only at the Combo price (Stars re-derives it from the
    # combo RUB price and ignores the forged one) — never below it
    combo_floor = config.COMBO_TARIFFS["combo_basic"][30]["price"] * 100
    cheap_combo = await e2e.rows(
        "SELECT purchase_id, price_kopecks FROM pending_purchases "
        "WHERE telegram_id=$1 AND is_combo = TRUE AND price_kopecks < $2", u.id, combo_floor)
    assert cheap_combo == [], f"a Combo purchase below the Combo price was created: {cheap_combo}"
    assert await e2e.payments(u.id) == [], "a forged Combo at the Basic price was paid"
    if method == "balance":
        assert await e2e.balance(u.id) == 1000.0, "money taken for a forged Combo"
    assert e2e.panel.bypass_limit(u.id) in (None, 0), "combo GB granted for the Basic price"
    assert any(a.show_alert for a in e2e.tg.answers[-3:]) or e2e.user_texts(u.id, mark), "no answer to the user"
