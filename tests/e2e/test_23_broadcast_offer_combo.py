"""Broadcast gift offer «−40% на 1 год» on Combo — prod 2026-09-15:
COMBO_PRICE_BELOW_COMBO user=502708. The offer sold Combo Plus / 365 at 2399 ₽;
the P0 Combo guard (2026-09-14) recomputed 3999 ₽ without the offer's discount
and refused the payment — every paid Combo offer was refused.

For EVERY payment method:
  * offer button → pay → a Combo purchase at the offer price, paid, the year
    of Plus + the combo GB granted;
  * offer button → Back → a regular Plus year → a plain Plus purchase: the
    stale offer key neither makes it Combo nor lowers its price.
"""
import pytest

import config
import wata_service
from tests.e2e import flows
from tests.e2e.world import new_user

METHODS = ["sbp", "wata", "crypto", "card", "stars", "balance"]
OFFER_RUB = round(config.COMBO_TARIFFS["combo_plus"][365]["price"] * 60 / 100)   # 2399
COMBO_RUB = config.COMBO_TARIFFS["combo_plus"][365]["price"]                      # 3999
PLUS_RUB = config.TARIFFS["plus"][365]["price"]                                   # 2599


async def _prepare(e2e, u, method, flag):
    await e2e.register(u, balance_rub=5000 if method == "balance" else 0)
    if method == "card":
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    e2e.provisioning(flag)


async def _errors(e2e, tg):
    return await e2e.rows("SELECT stage FROM payment_errors WHERE telegram_id=$1", tg)


def _offer_cases():
    """Legacy flag-off pipeline grants the wrong combo GB for ANY Combo purchase
    on the Telegram / balance paths (not offer-specific; the default flag «on»
    is right): card / Stars 2 × combo GB, balance combo GB + 10."""
    for flag in ("off", "on"):
        for method in METHODS:
            marks = []
            if flag == "off" and method in ("card", "stars", "balance"):
                marks = [pytest.mark.xfail(strict=True, reason="legacy flag-off bug T0-COMBO-GB-TG: "
                                           "Telegram/balance Combo purchase grants 2 × combo GB / combo GB + 10")]
            yield pytest.param(method, flag, marks=marks, id=f"{method}-{flag}")


@pytest.mark.parametrize("method,flag", list(_offer_cases()))
async def test_combo_gift_offer_is_sold_at_the_offer_price(e2e, method, flag):
    u = new_user()
    await _prepare(e2e, u, method, flag)
    await e2e.tap(u, "bcg1y40:buy:combo_plus:365")
    before = await flows.snapshot(e2e, u.id)
    last_id = await e2e.val("SELECT COALESCE(max(id), 0) FROM pending_purchases WHERE telegram_id=$1", u.id)

    await e2e.tap(u, f"pay:{method}")

    assert await _errors(e2e, u.id) == []
    if method == "balance":
        assert await e2e.balance(u.id) == 5000.0 - OFFER_RUB, "the balance paid something else than the offer"
    else:
        row = await flows.latest_pending(e2e, u.id)
        assert row is not None and row["id"] > last_id, (
            "the offer was refused at the payment button" + await flows.screen_evidence(e2e, u))
        assert row["is_combo"] is True
        assert OFFER_RUB * 100 <= row["price_kopecks"] < COMBO_RUB * 100, row["price_kopecks"]
        res = await flows.pay(e2e, u, row, method)
        if hasattr(res, "status"):
            assert res.body["status"] == "ok", res
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "combo_plus", 365)


@pytest.mark.parametrize("method", METHODS)
async def test_offer_then_back_then_plus_is_a_plain_plus_purchase(e2e, method):
    u = new_user()
    await _prepare(e2e, u, method, "on")
    await e2e.tap(u, "bcg1y40:buy:combo_plus:365")      # FSM: combo flag + offer key + 2399 ₽
    await e2e.tap(u, "menu_buy_vpn")                    # Back → the regular purchase screens
    before = await flows.snapshot(e2e, u.id)

    pending = await flows.buy(e2e, u, "plus", 365, method)

    if pending is not None:
        assert pending["is_combo"] is False, "the stale offer made a Plus purchase Combo"
        assert pending["price_kopecks"] >= PLUS_RUB * 100 or method == "stars", pending["price_kopecks"]
        res = await flows.pay(e2e, u, pending, method)
        if hasattr(res, "status"):
            assert res.body["status"] == "ok", res
    else:
        assert await e2e.balance(u.id) == 5000.0 - PLUS_RUB
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "plus", 365)
    assert not (await e2e.sub(u.id)).get("is_combo"), "a Plus purchase made the subscription Combo"
