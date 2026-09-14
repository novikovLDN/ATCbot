"""Scenario 6 — gifts (buy + activate), promo codes, referral cashback, GB packs.

Owner rules: a gift is a paid purchase for someone else (the receiver gets the
tariff's term + 10 GB, the buyer nothing); a promo code discounts the invoice
and is consumed once; referral cashback only for PURCHASES (any payment
method, balance included, GB packs, gifts), once per purchase on the paid
amount, never for a balance top-up (merged fix 540da874); a GB pack adds N GB
to the remaining GB and never touches premium.
"""
from datetime import timedelta
from urllib.parse import unquote

import pytest

import database
from tests.e2e import flows
from tests.e2e.world import GIB, new_user, utcnow
from tests.fakes import providers_http as prov


# ── gifts ───────────────────────────────────────────────────────────────

async def _gift_code(e2e, buyer_id: int) -> str:
    return await e2e.val(
        "SELECT gift_code FROM gift_subscriptions WHERE buyer_telegram_id=$1 ORDER BY id DESC LIMIT 1", buyer_id)


async def test_gift_bought_through_the_ui_and_activated_by_link(e2e):
    buyer = new_user()
    await e2e.register(buyer)
    await e2e.tap(buyer, "gift_subscription")
    await e2e.tap(buyer, "gift_tariff:plus")
    await e2e.tap(buyer, "gift_period:30")
    assert e2e.tg.with_button(buyer.id, "gift_pay:sbp"), "no SBP button on the gift screen"
    await e2e.tap(buyer, "gift_pay:sbp")
    pending = await flows.latest_pending(e2e, buyer.id)
    assert pending["purchase_type"] == "gift" and pending["tariff"] == "plus"
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.platega_webhook(pending["purchase_id"], pending["price_kopecks"] / 100))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    code = await _gift_code(e2e, buyer.id)
    shared = " ".join(unquote(str(target)) for s in e2e.tg.since(mark, buyer.id) for _, target in s.buttons())
    assert code and f"start=gift_{code}" in shared, "buyer did not get the gift share link"
    assert await e2e.sub(buyer.id) is None and e2e.panel.users == {}

    u = new_user()
    before = await flows.snapshot(e2e, u.id)
    await e2e.start_user(u, f"gift_{code}")
    await flows.check_purchase(e2e, u.id, before, "plus", 30, paid=False)
    # replay of the code by the same user: nothing more
    exp = (await e2e.sub(u.id))["expires_at"]
    await e2e.send(u, f"/start gift_{code}")
    assert (await e2e.sub(u.id))["expires_at"] == exp
    assert e2e.panel.bypass_limit(u.id) == 10 * GIB


async def test_gift_paid_from_balance(e2e):
    buyer = new_user()
    await e2e.register(buyer, balance_rub=1000)
    await e2e.tap(buyer, "gift_subscription")
    await e2e.tap(buyer, "gift_tariff:basic")
    await e2e.tap(buyer, "gift_period:30")
    await e2e.tap(buyer, "gift_pay:balance")
    assert await e2e.balance(buyer.id) == 801.0
    code = await _gift_code(e2e, buyer.id)
    assert code
    assert await e2e.sub(buyer.id) is None


async def test_gift_to_an_active_subscriber_extends_from_the_end(e2e):
    buyer, u = new_user(), new_user()
    await e2e.register(buyer)
    gift = await e2e.create_purchase(buyer, "basic", 30, provider="platega", purchase_type="gift")
    await flows.pay(e2e, buyer, gift, "sbp")
    code = await _gift_code(e2e, buyer.id)
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=15))
    before = await flows.snapshot(e2e, u.id)
    await e2e.send(u, f"/start gift_{code}")
    await e2e.settle()
    await flows.check_purchase(e2e, u.id, before, "basic", 30, paid=False)


async def test_self_activation_of_own_gift_is_refused(e2e):
    buyer = new_user()
    await e2e.register(buyer)
    gift = await e2e.create_purchase(buyer, "basic", 30, provider="platega", purchase_type="gift")
    await flows.pay(e2e, buyer, gift, "sbp")
    code = await _gift_code(e2e, buyer.id)
    await e2e.send(buyer, f"/start gift_{code}")
    assert await e2e.sub(buyer.id) is None
    assert await e2e.val("SELECT status FROM gift_subscriptions WHERE gift_code=$1", code) != "activated"


# ── promo codes through the UI ──────────────────────────────────────────

async def test_promo_code_entered_in_the_bot_discounts_and_is_consumed_once(e2e):
    await database.create_promocode_atomic("E2E30", 30, 86400, 10, 1)
    u = new_user()
    await e2e.register(u)
    await e2e.tap(u, "menu_buy_vpn")
    await e2e.tap(u, "enter_promo")
    await e2e.send(u, "E2E30")
    await e2e.tap(u, "tariff:basic")
    await e2e.tap(u, "period:basic:30")
    await e2e.tap(u, "pay:sbp")
    pending = await flows.latest_pending(e2e, u.id)
    assert pending["promo_code"] and pending["promo_code"].upper() == "E2E30"
    assert pending["price_kopecks"] == round(19_900 * 0.7)
    before = await flows.snapshot(e2e, u.id)
    res = await e2e.webhook(prov.platega_webhook(pending["purchase_id"], pending["price_kopecks"] / 100))
    assert res.body["status"] == "ok", res
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.val("SELECT used_count FROM promo_codes WHERE code='E2E30'") == 1
    again = await e2e.webhook(prov.platega_webhook(pending["purchase_id"], pending["price_kopecks"] / 100))
    assert again.body["status"] == "already_processed"
    assert await e2e.val("SELECT used_count FROM promo_codes WHERE code='E2E30'") == 1


async def test_unknown_promo_code_changes_nothing(e2e):
    u = new_user()
    await e2e.register(u)
    await e2e.tap(u, "menu_buy_vpn")
    await e2e.tap(u, "enter_promo")
    mark = e2e.tg.mark()
    await e2e.send(u, "NOSUCHCODE")
    reply = e2e.tg.since(mark, u.id)
    assert reply and "promo_back" in reply[-1].callback_data()
    await e2e.tap(u, "promo_back")          # the only way out of the promo input
    await e2e.tap(u, "tariff:basic")
    await e2e.tap(u, "period:basic:30")
    await e2e.tap(u, "pay:sbp")
    pending = await flows.latest_pending(e2e, u.id)
    assert pending["price_kopecks"] == 19_900 and not pending["promo_code"]


# ── referral cashback ───────────────────────────────────────────────────

async def _referral_pair(e2e):
    ref = new_user()
    await e2e.start_user(ref)
    code = await e2e.val("SELECT referral_code FROM users WHERE telegram_id=$1", ref.id)
    u = new_user()
    await e2e.start_user(u, f"ref_{code}")
    assert await e2e.val("SELECT referrer_id FROM users WHERE telegram_id=$1", u.id) == ref.id
    return ref, u


async def _rewards(e2e, ref_id):
    return await e2e.rows("SELECT * FROM referral_rewards WHERE referrer_id=$1 ORDER BY id", ref_id)


async def test_cashback_zero_for_topup_once_for_purchase_none_for_replay(e2e):
    ref, u = await _referral_pair(e2e)

    # 1) balance top-up → no cashback
    await e2e.tap(u, "topup_balance")
    await e2e.tap(u, "topup_amount:750")
    await e2e.tap(u, "topup_sbp:750")
    top = await flows.latest_pending(e2e, u.id)
    await e2e.webhook(prov.platega_webhook(top["purchase_id"], top["price_kopecks"] / 100))
    assert await e2e.balance(u.id) == 750.0
    assert await _rewards(e2e, ref.id) == [] and await e2e.balance(ref.id) == 0.0

    # 2) purchase via provider → exactly one reward on the paid amount
    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    hook = prov.platega_webhook(p["purchase_id"], 199.0)
    assert (await e2e.webhook(hook)).body["status"] == "ok"
    rw = await _rewards(e2e, ref.id)
    assert len(rw) == 1 and rw[0]["buyer_id"] == u.id and rw[0]["purchase_id"] == p["purchase_id"]
    assert float(rw[0]["reward_amount"]) == pytest.approx(199.0 * rw[0]["percent"] / 100, abs=0.01) or \
        float(rw[0]["reward_amount"]) == pytest.approx(19_900 * rw[0]["percent"] / 100, abs=1)
    ref_balance = await e2e.balance(ref.id)
    assert ref_balance > 0

    # 3) the same webhook again → nothing more
    assert (await e2e.webhook(hook)).body["status"] == "already_processed"
    assert len(await _rewards(e2e, ref.id)) == 1 and await e2e.balance(ref.id) == ref_balance

    # 4) purchase from balance → cashback too (any payment method). The provider
    #    purchase above is moved out of the 60 s balance double-tap window
    #    (see test_balance_purchase_right_after_provider_purchase_is_not_a_double_tap).
    await e2e.pool.execute("UPDATE payments SET created_at = created_at - interval '5 minutes' "
                           "WHERE telegram_id=$1", u.id)
    await flows.buy(e2e, u, "basic", 30, "balance")
    assert len(await _rewards(e2e, ref.id)) == 2
    assert await e2e.balance(ref.id) > ref_balance


async def test_cashback_for_gb_pack_purchase(e2e):
    ref, u = await _referral_pair(e2e)
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20))
    n = len(await _rewards(e2e, ref.id))
    await e2e.tap(u, "buy_traffic")
    await e2e.tap(u, "buy_traffic_pack:15")
    await e2e.tap(u, "traffic_pay_sbp:15")
    p = await flows.latest_pending(e2e, u.id)
    assert p["purchase_type"] == "traffic_pack"
    await e2e.webhook(prov.platega_webhook(p["purchase_id"], p["price_kopecks"] / 100))
    assert len(await _rewards(e2e, ref.id)) == n + 1


# ── GB packs ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("button,gb", [("traffic_pay_sbp", 15), ("traffic_pay_wata", 50)])
async def test_gb_pack_adds_to_remaining_and_keeps_premium(e2e, flag, button, gb):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20))
    e2e.provisioning(flag)
    premium = e2e.panel.premium_expire(u.id)
    exp = (await e2e.sub(u.id))["expires_at"]
    limit = e2e.panel.bypass_limit(u.id)
    await e2e.tap(u, "buy_traffic")
    await e2e.tap(u, f"buy_traffic_pack:{gb}")
    await e2e.tap(u, f"{button}:{gb}")
    p = await flows.latest_pending(e2e, u.id)
    assert p["purchase_type"] == "traffic_pack" and p["tariff"] == f"traffic_{gb}gb"
    hook = (prov.platega_webhook(p["purchase_id"], p["price_kopecks"] / 100) if button.endswith("sbp")
            else prov.wata_webhook(p["purchase_id"], p["price_kopecks"] / 100))
    mark = e2e.tg.mark()
    res = await e2e.webhook(hook)
    assert (res.status, res.body["status"]) == (200, "ok"), res
    await e2e.provisioning_tick()
    assert e2e.panel.bypass_limit(u.id) == limit + gb * GIB
    assert e2e.panel.premium_expire(u.id) == premium
    assert (await e2e.sub(u.id))["expires_at"] == exp
    assert e2e.user_texts(u.id, mark) and e2e.admin_texts(mark) == []
    # replay does not add the GB twice
    again = await e2e.webhook(hook)
    assert again.body["status"] == "already_processed"
    await e2e.provisioning_tick()
    assert e2e.panel.bypass_limit(u.id) == limit + gb * GIB


async def test_gb_pack_without_subscription_gives_bypass_only_not_premium(e2e):
    u = new_user()
    await e2e.register(u)
    await e2e.tap(u, "buy_traffic")
    await e2e.tap(u, "buy_traffic_pack:15")
    await e2e.tap(u, "traffic_pay_sbp:15")
    p = await flows.latest_pending(e2e, u.id)
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], p["price_kopecks"] / 100))
    assert res.body["status"] == "ok", res
    assert e2e.panel.bypass_limit(u.id) == 15 * GIB
    assert e2e.panel.premium(u.id) is None, "a GB pack must never create premium"
    sub = await e2e.sub(u.id)
    assert sub is None or sub.get("is_bypass_only")
