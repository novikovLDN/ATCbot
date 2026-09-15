"""Discount buttons of automated notifications and broadcasts × a Combo
purchase — where the Combo price guard (P0, 2026-09-14) checks the price.

None of these buttons writes a price into the purchase FSM: each turns on / uses
a discount (special offer −15 %, the −15 % reminder window, the funnel's or the
broadcast's personal discount) and opens the regular purchase screens, which
price through calculate_final_price — the same function the guard uses. So the
guard must accept every such Combo purchase at the discounted price.
"""
from datetime import datetime, timedelta, timezone

import pytest

import config
import database
import wata_service
from tests.e2e import flows
from tests.e2e.world import new_user

METHODS = ["sbp", "card", "stars", "balance"]      # webhook / Telegram / Stars / balance guard
COMBO_RUB = config.COMBO_TARIFFS["combo_plus"][365]["price"]


def _off(rub: int, pct: int) -> int:
    base = rub * 100
    return base - int(base * pct / 100)


async def _broadcast(pct, *, reveal=False):
    bid = await database.create_broadcast("e2e", "Скидка", "custom", "all_users", config.ADMIN_TELEGRAM_ID)
    if reveal:
        await database.save_broadcast_gift_reveal_percent(bid, pct)
    else:
        await database.save_broadcast_discount(bid, pct, 24)
    return bid


async def _press(e2e, u, source):
    """Turn the discount on the way the notification / broadcast does; returns its %."""
    if source == "special_offer":                  # «подписка закончилась» −15 % for 3 days
        await database.set_special_offer(u.id)
        await e2e.tap(u, "special_offer_buy")
        return 15
    if source in ("paid_discount_15", "trial_discount_15"):   # 3 h reminders: the 72 h window
        await e2e.tap(u, source)
        return 15
    if source == "funnel":                         # the funnel step grants a personal discount
        await database.create_user_discount(
            telegram_id=u.id, discount_percent=30,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24), created_by=0, keep_max=True)
        await e2e.tap(u, "funnel_buy:start")
        return 30
    if source == "broadcast_promo_buy":
        await e2e.tap(u, f"broadcast_promo_buy:{await _broadcast(20)}")
        return 20
    if source == "gift_reveal":
        await e2e.tap(u, f"broadcast_gift_reveal:{await _broadcast(25, reveal=True)}")
        return 25
    raise ValueError(source)


SOURCES = ["special_offer", "paid_discount_15", "trial_discount_15", "funnel", "broadcast_promo_buy", "gift_reveal"]


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("source", SOURCES)
async def test_discount_button_then_combo_is_paid_at_the_discounted_price(e2e, source, method):
    u = new_user()
    await e2e.register(u, balance_rub=9000 if method == "balance" else 0)
    if method == "card":
        e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    e2e.provisioning("on")
    if source in ("paid_discount_15", "trial_discount_15"):
        # the reminder goes to a subscriber whose period ends in 3 h
        await flows.seed_active(e2e, u, "plus", datetime.now(timezone.utc) + timedelta(hours=3))
    pct = await _press(e2e, u, source)
    before = await flows.snapshot(e2e, u.id)
    price = _off(COMBO_RUB, pct)

    pending = await flows.buy(e2e, u, "combo_plus", 365, method)

    assert await e2e.rows("SELECT stage FROM payment_errors WHERE telegram_id=$1", u.id) == []
    if pending is not None:
        assert pending["is_combo"] is True
        if method != "stars":
            assert pending["price_kopecks"] == price, (pending["price_kopecks"], price)
        res = await flows.pay(e2e, u, pending, method)
        if hasattr(res, "status"):
            assert res.body["status"] == "ok", res
    else:
        assert await e2e.balance(u.id) == 9000.0 - price / 100, "the balance charged another price"
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "combo_plus", 365)
