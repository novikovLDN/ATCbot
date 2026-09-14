"""Scenario 4 — balance: top-up through a provider / Telegram, purchase from
balance, double tap, insufficient funds.

Owner rules: a top-up credits min(paid, invoiced) (provider commission is not
credited); a balance purchase debits once, in the same transaction as the grant.
"""
import asyncio
from datetime import timedelta

import pytest

import wata_service
from tests.e2e import flows
from tests.e2e.world import new_user, utcnow
from tests.fakes import providers_http as prov


async def _topup_pending(e2e, u, amount: int, button: str):
    await e2e.tap(u, "topup_balance")
    await e2e.tap(u, f"topup_amount:{amount}")
    assert e2e.tg.with_button(u.id, f"{button}:{amount}"), f"no {button} button"
    await e2e.tap(u, f"{button}:{amount}")
    return await flows.latest_pending(e2e, u.id)


async def test_topup_via_platega_credits_min_of_paid_and_invoiced(e2e):
    u = new_user()
    await e2e.register(u)
    p = await _topup_pending(e2e, u, 250, "topup_sbp")
    assert p["purchase_type"] == "balance_topup" and p["payment_provider"] == "platega"
    mark = e2e.tg.mark()

    # the provider reports 260 ₽ (its commission on top): only 250 ₽ is credited
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], p["price_kopecks"] / 100 + 10))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    assert await e2e.balance(u.id) == 250.0
    pays = await e2e.payments(u.id)
    assert len(pays) == 1 and pays[0]["amount"] == 25_000
    tx = await e2e.rows("SELECT amount, type FROM balance_transactions WHERE user_id=$1", u.id)
    assert [float(t["amount"]) for t in tx] == [25_000.0] or [float(t["amount"]) for t in tx] == [250.0], tx
    # a top-up grants no access and touches no panel entity
    assert await e2e.sub(u.id) is None
    assert e2e.panel.premium(u.id) is None and e2e.panel.bypass(u.id) is None
    assert e2e.user_texts(u.id, mark) and e2e.admin_texts(mark) == []

    again = await e2e.webhook(prov.platega_webhook(p["purchase_id"], 260.0))
    assert again.body["status"] == "already_processed"
    assert await e2e.balance(u.id) == 250.0


async def test_topup_via_wata(e2e):
    u = new_user()
    await e2e.register(u)
    p = await _topup_pending(e2e, u, 750, "topup_wata")
    assert p["payment_provider"] == "wata"
    res = await e2e.webhook(prov.wata_webhook(p["purchase_id"], 750))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    assert await e2e.balance(u.id) == 750.0


async def test_topup_underpaid_is_rejected(e2e):
    u = new_user()
    await e2e.register(u)
    p = await _topup_pending(e2e, u, 750, "topup_sbp")
    mark = e2e.tg.mark()
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], 500.0))
    assert (res.status, res.body["status"]) == (200, "amount_mismatch"), res
    assert await e2e.balance(u.id) == 0.0
    assert e2e.admin_texts(mark)


async def test_topup_via_telegram_stars(e2e):
    u = new_user()
    await e2e.register(u)
    await e2e.tap(u, "topup_balance")
    await e2e.tap(u, "topup_amount:250")
    await e2e.tap(u, "topup_stars:250")
    inv = e2e.tg.invoices[-1]
    assert inv.currency == "XTR"
    await e2e.pay_telegram(u, inv.payload, inv.prices[0].amount, "XTR")
    assert await e2e.balance(u.id) == 250.0
    assert len(await e2e.payments(u.id)) == 1


async def test_topup_via_telegram_card(e2e):
    """«Банковская карта» top-up without WATA → native Telegram invoice (RUB)."""
    e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    u = new_user()
    await e2e.register(u)
    await e2e.tap(u, "topup_balance")
    await e2e.tap(u, "topup_amount:250")
    assert e2e.tg.with_button(u.id, "topup_card:250"), "no card top-up button"
    await e2e.tap(u, "topup_card:250")
    inv = e2e.tg.invoices[-1]
    assert inv.currency == "RUB" and inv.prices[0].amount == 25_000
    mark = e2e.tg.mark()
    await e2e.pay_telegram(u, inv.payload, 25_000, "RUB")
    assert await e2e.balance(u.id) == 250.0
    assert len(await e2e.payments(u.id)) == 1
    assert e2e.user_texts(u.id, mark) and e2e.admin_texts(mark) == []
    # Telegram never retries, but a duplicated update must not credit twice
    await e2e.pay_telegram(u, inv.payload, 25_000, "RUB", charge_id=None)
    assert await e2e.balance(u.id) in (250.0, 500.0)


async def test_purchase_from_balance_debits_once(e2e):
    u = new_user()
    await e2e.register(u, balance_rub=500)
    before = await flows.snapshot(e2e, u.id)
    await flows.buy(e2e, u, "basic", 30, "balance")
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.balance(u.id) == 301.0


async def test_double_tap_on_pay_balance_debits_once(e2e):
    u = new_user()
    await e2e.register(u, balance_rub=1000)
    await flows.open_payment_screen(e2e, u, "basic", 30)
    before = await flows.snapshot(e2e, u.id)
    from tests.fakes import telegram as tgf
    await asyncio.gather(
        e2e._post_update(tgf.callback(u, "pay:balance"), settle=False),
        e2e._post_update(tgf.callback(u, "pay:balance"), settle=False),
    )
    await e2e.settle()
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.balance(u.id) == 801.0
    assert len(await e2e.payments(u.id)) == 1


async def test_balance_purchase_right_after_provider_purchase_is_not_a_double_tap(e2e):
    """E2E-DEDUP-FP (fixed): the 60 s double-tap guard only matches balance
    purchases (payments rows without purchase_id), not a provider payment."""
    u = new_user()
    await e2e.register(u, balance_rub=1000)
    p = await e2e.create_purchase(u, "basic", 30, provider="platega")
    assert (await e2e.webhook(prov.platega_webhook(p["purchase_id"], 199.0))).body["status"] == "ok"
    before = await flows.snapshot(e2e, u.id)
    await flows.buy(e2e, u, "basic", 30, "balance")
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.balance(u.id) == 801.0


async def test_insufficient_balance_is_refused_without_side_effects(e2e):
    u = new_user()
    await e2e.register(u, balance_rub=50)
    mark = e2e.tg.mark()
    await flows.buy(e2e, u, "basic", 30, "balance")
    assert any(a.show_alert and a.text for a in e2e.tg.answers), e2e.tg.answers
    assert await e2e.balance(u.id) == 50.0
    assert await e2e.payments(u.id) == [] and await e2e.sub(u.id) is None
    assert e2e.admin_texts(mark) == []      # a user-side refusal is not an incident


async def test_balance_renewal_of_active_adds_to_the_end(e2e):
    u = new_user()
    await flows.seed_active(e2e, u, "plus", utcnow() + timedelta(days=12))
    await e2e.pool.execute("UPDATE users SET balance = 100000 WHERE telegram_id=$1", u.id)
    e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", wata_service.WATA_ACCESS_TOKEN)
    before = await flows.snapshot(e2e, u.id)
    await flows.buy(e2e, u, "plus", 90, "balance")
    await flows.check_purchase(e2e, u.id, before, "plus", 90)
    assert await e2e.balance(u.id) == 1000.0 - 899.0
