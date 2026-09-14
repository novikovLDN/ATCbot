"""Merged fixes of refactor/audit-2026-09 (HEAD 6d4bf1df), end to end:

  031380e3  combo via Telegram Stars is priced from the combo RUB price; Stars are
            recorded in rubles
  348d50d8  combo 24 months (730 d) can be bought
  f64fa1ee  Telegram successful_payment is shielded: the 25 s timeout stops only
            the wait, processing completes, the admin is told
  6048efad  gift paid from balance: refund + alert when the code is not created
  2d6f5b8e  admin alerts over the cooldown are held for a digest, never dropped
  4bd448f6  the "-15 %" offer never lowers a bigger discount
  ae6c2561  change-tariff screens say the new plan applies right away
  4f8e5a98  SBP top-up: the markup is charged but not credited (migration 083)
"""
import asyncio
from datetime import timedelta

import pytest

import config
import database
import wata_service
from app.api import telegram_webhook
from app.services import admin_alerts, tariffs
from tests.e2e import flows
from tests.e2e.world import ADMIN, GIB, new_user, utcnow
from tests.fakes import providers_http as prov


# ── combo via Stars / combo 730 ─────────────────────────────────────────

@pytest.mark.parametrize("tariff", ["combo_basic", "combo_plus"])
async def test_combo_via_stars_is_priced_as_combo_and_recorded_in_rubles(e2e, tariff):
    u = new_user()
    await flows.seed_active(e2e, u, flows.base_of(tariff), utcnow() + timedelta(days=10))
    e2e.provisioning("on")
    before = await flows.snapshot(e2e, u.id)
    pending = await flows.buy(e2e, u, tariff, 30, "stars")
    inv = e2e.tg.invoices[-1]
    combo_rub = config.COMBO_TARIFFS[tariff][30]["price"]
    assert inv.currency == "XTR" and inv.prices[0].amount == tariffs.stars_for_rub(combo_rub), inv.prices
    assert pending["price_kopecks"] == combo_rub * 100          # the row keeps the RUB list price
    await flows.pay(e2e, u, pending, "stars")
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, tariff, 30)
    assert (await e2e.payments(u.id))[-1]["amount"] == combo_rub * 100   # revenue in rubles, not stars


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_combo_24_months_can_be_bought(e2e, flag):
    u = new_user()
    await e2e.register(u)
    e2e.provisioning(flag)
    before = await flows.snapshot(e2e, u.id)
    pending = await flows.buy(e2e, u, "combo_basic", 730, "sbp")
    assert pending is not None and pending["period_days"] == 730, pending
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.body["status"] == "ok", res
    await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "combo_basic", 730)
    assert e2e.panel.bypass_limit(u.id) == config.COMBO_TARIFFS["combo_basic"][730]["gb"] * GIB


# ── Telegram successful_payment shield ──────────────────────────────────

async def test_slow_telegram_payment_is_not_cancelled_and_admin_is_told(e2e, monkeypatch):
    """The webhook stops WAITING after the timeout (Telegram gets 200), but the
    payment keeps processing to the end; the admin gets a timeout alert."""
    e2e.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
    u = new_user()
    await e2e.register(u)
    pending = await flows.buy(e2e, u, "basic", 30, "card")
    gate = asyncio.Event()
    real_finalize = database.finalize_purchase

    async def slow_finalize(*a, **k):
        await gate.wait()
        return await real_finalize(*a, **k)
    monkeypatch.setattr(database, "finalize_purchase", slow_finalize)
    monkeypatch.setattr(telegram_webhook, "_HANDLER_TIMEOUT", 0.2)
    real_sleep = e2e._real_sleep
    before = await flows.snapshot(e2e, u.id)
    mark = e2e.tg.mark()

    from tests.fakes import telegram as tgf
    await e2e.http.post("/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET},
                        json=tgf.pre_checkout(u, f"purchase:{pending['purchase_id']}", 19_900))
    post = asyncio.ensure_future(e2e.http.post(
        "/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET},
        json=tgf.successful_payment(u, f"purchase:{pending['purchase_id']}", 19_900)))
    for _ in range(100):                      # real time: the 0.2 s timeout must elapse
        if post.done():
            break
        await real_sleep(0.05)
    assert post.done() and post.result().status_code == 200, "webhook kept Telegram waiting"
    assert await e2e.payments(u.id) == [], "finished before the gate — the test proves nothing"
    await e2e.settle(timeout=2)
    assert any("timeout" in t.lower() or "time" in t.lower() for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)

    gate.set()                                # the slow processing now finishes
    for _ in range(100):
        if await e2e.payments(u.id):
            break
        await real_sleep(0.05)
    await e2e.settle()
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert e2e.user_texts(u.id, mark), "user not told after the slow payment completed"


# ── gift from balance: refund when the code is not created ──────────────

async def test_gift_from_balance_is_refunded_and_alerted_when_code_fails(e2e, monkeypatch):
    buyer = new_user()
    await e2e.register(buyer, balance_rub=1000)

    async def broken(*a, **k):
        raise RuntimeError("injected: gift code not created")
    monkeypatch.setattr(database, "create_gift_subscription", broken)
    await e2e.tap(buyer, "gift_subscription")
    await e2e.tap(buyer, "gift_tariff:basic")
    await e2e.tap(buyer, "gift_period:30")
    mark = e2e.tg.mark()
    await e2e.tap(buyer, "gift_pay:balance")
    assert await e2e.balance(buyer.id) == 1000.0, "gift money not refunded"
    assert any("refund" in t.lower() for t in e2e.admin_texts(mark)), e2e.admin_texts(mark)
    assert await e2e.val("SELECT count(*) FROM gift_subscriptions WHERE buyer_telegram_id=$1", buyer.id) == 0
    assert await e2e.payment_errors(), "no payment_errors row for the failed gift"


# ── admin alert digest instead of drops ────────────────────────────────

async def test_alerts_over_the_cooldown_arrive_as_a_digest(e2e):
    admin_alerts.reset_state()
    mark = e2e.tg.mark()
    assert await admin_alerts.send_alert(e2e.bot, "worker", "first worker failure")
    await admin_alerts.send_alert(e2e.bot, "worker", "second worker failure")   # inside the cooldown
    await admin_alerts.send_alert(e2e.bot, "worker", "third worker failure")
    await e2e.settle()
    for _ in range(3):
        if len(e2e.admin_texts(mark)) >= 2:
            break
        for task in list(admin_alerts._flush_tasks.values()):
            await asyncio.wait([task], timeout=2)
        await e2e.settle()
    texts = e2e.admin_texts(mark)
    assert any("first worker failure" in t for t in texts)
    joined = "\n".join(texts)
    assert "second worker failure" in joined and "third worker failure" in joined, texts
    assert admin_alerts.pending_digest_counts() == {}
    admin_alerts.reset_state()


# ── discounts / texts / SBP top-up credit ───────────────────────────────

async def test_expiry_offer_never_lowers_a_bigger_personal_discount(e2e):
    """Merged 6f866cc8 (VIP removed, the largest single discount wins): the −15 %
    offer no longer beats a 30 % personal discount at checkout."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=10))
    await database.create_user_discount(u.id, 30, utcnow() + timedelta(days=7), created_by=ADMIN)
    await database.set_special_offer(u.id)                   # the −15 % offer of an ended period
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert pending["price_kopecks"] == round(19_900 * 0.70), pending["price_kopecks"]


async def test_change_tariff_screen_says_it_applies_right_away(e2e):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=10))
    mark = e2e.tg.mark()
    await e2e.tap(u, "menu_buy_vpn")
    await e2e.tap(u, "switch_tariff_menu")
    await e2e.tap(u, "switch_tariff:plus")
    text = "\n".join(e2e.user_texts(u.id, mark))
    assert "после окончания текущей подписки" not in text, text
    assert "сразу" in text.lower(), text


@pytest.mark.xfail(strict=True, reason="E2E-MARKUP-FLOAT (low, report only): platega_service._apply_markup "
                   "does ceil(price * (1 + pct/100.0)); the float error pushes exact results up by 1 kopeck "
                   "(25 000 @ 10 % → 27 501, 19 900 @ 11 % → 22 090). Shared with the shop screens (SCOPE: "
                   "shop prices unchanged) — fix = integer math (price*(100+pct)+99)//100")
def test_sbp_markup_has_no_float_kopeck():
    import platega_service
    for price, pct, exact in ((25_000, 10, 27_500), (19_900, 11, 22_089), (34_900, 11, 38_739)):
        assert platega_service._apply_markup(price, pct) == exact, (price, pct)


async def test_broadcast_skips_unreachable_and_marks_blocked_users(e2e):
    """65e7dd04: a user known as unreachable is not sent to; a send refused by
    Telegram (blocked) marks the user unreachable, so the next broadcast skips him."""
    from app.services.broadcast_sender import send_broadcast
    a, blocked_before, blocks_now = new_user(), new_user(), new_user()
    for u in (a, blocked_before, blocks_now):
        await e2e.register(u)
    await e2e.pool.execute("UPDATE users SET is_reachable = FALSE WHERE telegram_id=$1", blocked_before.id)
    e2e.tg.fail_chats.add(blocks_now.id)

    # recipients are resolved the way the dashboard / scheduled worker do it
    ids = await database.get_users_by_segment("all_users")
    assert a.id in ids and blocks_now.id in ids
    assert blocked_before.id not in ids, "segment includes a user already known as unreachable"
    bid = await database.create_broadcast("e2e", "Привет от e2e", "custom", "all_users", ADMIN)
    await send_broadcast(bot=e2e.bot, broadcast_id=bid, user_ids=ids, message="Привет от e2e")
    await e2e.settle()
    assert any("Привет от e2e" in t for t in e2e.tg.texts(a.id))
    assert await e2e.val("SELECT is_reachable FROM users WHERE telegram_id=$1", blocks_now.id) is False

    ids2 = await database.get_users_by_segment("all_users")
    assert blocks_now.id not in ids2 and a.id in ids2, ids2


async def test_scheduled_broadcast_is_claimed_before_send_and_runs_once(e2e):
    """65e7dd04: two workers dispatch the same due 'once' schedule at the same
    time — the run is claimed first, every user gets the message exactly once."""
    from app.services import scheduled_broadcasts_worker as sbw
    users = [new_user() for _ in range(3)]
    for u in users:
        await e2e.register(u)
    await database.create_scheduled_broadcast(
        source_broadcast_id=None, title="e2e sched", message="Плановая рассылка e2e",
        segment="all_users", scheduled_at=utcnow() - timedelta(minutes=1), recurrence="once",
        created_by=ADMIN)
    due = await database.fetch_due_scheduled()
    assert len(due) == 1, due
    await asyncio.gather(sbw._dispatch_one(e2e.bot, due[0]), sbw._dispatch_one(e2e.bot, dict(due[0])))
    await e2e.settle()
    for u in users:
        got = [t for t in e2e.tg.texts(u.id) if "Плановая рассылка e2e" in t]
        assert len(got) == 1, (u.id, got)
    assert await database.fetch_due_scheduled() == [], "the 'once' schedule is still due after it ran"


async def test_sbp_topup_markup_is_charged_but_not_credited(e2e, monkeypatch):
    monkeypatch.setattr(config, "SBP_MARKUP_PERCENT", 10)
    u = new_user()
    await e2e.register(u)
    await e2e.tap(u, "topup_balance")
    await e2e.tap(u, "topup_amount:250")
    await e2e.tap(u, "topup_sbp:250")
    import platega_service
    p = await flows.latest_pending(e2e, u.id)
    charged = platega_service.apply_sbp_markup(25_000)
    assert p["price_kopecks"] == charged and p["credit_kopecks"] == 25_000, p
    res = await e2e.webhook(prov.platega_webhook(p["purchase_id"], charged / 100))
    assert res.body["status"] == "ok", res
    assert await e2e.balance(u.id) == 250.0, "the SBP markup was credited to the balance"
    assert (await e2e.payments(u.id))[-1]["amount"] == charged      # charged = revenue
