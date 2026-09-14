"""Last merged owner batch (refactor/audit-2026-09 @ 1cf6125b):

  439828ec  paid periods are CALENDAR months with an end-of-month clamp —
            the same date in the DB and in the panel (the owner example
            20 Oct + 1 month = 20 Nov is in test_02b_owner_example.py)
  6f866cc8  VIP removed; the largest single discount wins; a promo code that
            loses is neither stored on the purchase nor consumed
  5787729a  shutdown waits (≤ 20 s) for in-flight Telegram payment
            finalizations; still running → admin alert
"""
import asyncio
from datetime import datetime, timedelta

import pytest

import config
import database
import wata_service
from app.api import telegram_webhook
from tests.e2e import flows
from tests.e2e.world import ADMIN, UTC, new_user, utcnow
from tests.fakes import providers_http as prov
from tests.fakes import telegram as tgf


def _next(month: int, day: int) -> datetime:
    """The next future date with this month/day, 12:00 UTC."""
    now = utcnow()
    year = now.year if (now.month, now.day) < (month, day) else now.year + 1
    return datetime(year, month, day, 12, 0, tzinfo=UTC)


# ── calendar months with an end-of-month clamp ──────────────────────────

@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("until,period,expected", [
    ((1, 31), 30, "feb_end"),        # 31 Jan + 1 month → 28/29 Feb
    ((8, 31), 90, (11, 30)),         # 31 Aug + 3 months → 30 Nov
    ((3, 31), 30, (4, 30)),          # 31 Mar + 1 month → 30 Apr
])
async def test_calendar_month_end_of_month_clamp_db_and_panel(e2e, until, period, expected, flag):
    u = new_user()
    start = _next(*until)
    await flows.seed_active(e2e, u, "basic", start)
    e2e.provisioning(flag)
    pending = await flows.buy(e2e, u, "basic", period, "sbp")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.body["status"] == "ok", res
    await e2e.provisioning_tick()
    if expected == "feb_end":
        import calendar
        feb_days = calendar.monthrange(start.year, 2)[1]
        want = start.replace(month=2, day=feb_days)
    else:
        year = start.year + (1 if expected[0] < start.month else 0)
        want = start.replace(year=year, month=expected[0], day=expected[1])
    assert (await e2e.sub(u.id))["expires_at"] == want, "DB end date"
    assert abs((e2e.panel.premium_expire(u.id) - want).total_seconds()) <= 1, "panel expireAt"


# ── the largest single discount wins; a losing promo is not consumed ────

async def _enter_promo(e2e, u, code):
    await e2e.tap(u, "menu_buy_vpn")
    await e2e.tap(u, "enter_promo")
    await e2e.send(u, code)


async def test_bigger_personal_discount_beats_promo_and_promo_is_not_consumed(e2e):
    await database.create_promocode_atomic("E2E20", 20, 86400, 10, 1)
    u = new_user()
    await e2e.register(u)
    await database.create_user_discount(u.id, 30, utcnow() + timedelta(days=7), created_by=ADMIN)
    await _enter_promo(e2e, u, "E2E20")
    await e2e.tap(u, "tariff:basic")
    await e2e.tap(u, "period:basic:30")
    await e2e.tap(u, "pay:sbp")
    pending = await flows.latest_pending(e2e, u.id)
    assert pending["price_kopecks"] == round(19_900 * 0.70), pending["price_kopecks"]
    assert not pending["promo_code"], "a promo code that lost was stored on the purchase"
    before = await flows.snapshot(e2e, u.id)
    res = await e2e.webhook(prov.platega_webhook(pending["purchase_id"], pending["price_kopecks"] / 100))
    assert res.body["status"] == "ok", res
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await e2e.val("SELECT used_count FROM promo_codes WHERE code='E2E20'") == 0, "losing promo consumed"


async def test_bigger_promo_beats_personal_discount_and_is_consumed_once(e2e):
    await database.create_promocode_atomic("E2E50", 50, 86400, 10, 1)
    u = new_user()
    await e2e.register(u)
    await database.create_user_discount(u.id, 30, utcnow() + timedelta(days=7), created_by=ADMIN)
    await _enter_promo(e2e, u, "E2E50")
    await e2e.tap(u, "tariff:basic")
    await e2e.tap(u, "period:basic:30")
    await e2e.tap(u, "pay:sbp")
    pending = await flows.latest_pending(e2e, u.id)
    assert pending["price_kopecks"] == round(19_900 * 0.50), pending["price_kopecks"]
    assert (pending["promo_code"] or "").upper() == "E2E50"
    hook = prov.platega_webhook(pending["purchase_id"], pending["price_kopecks"] / 100)
    assert (await e2e.webhook(hook)).body["status"] == "ok"
    await e2e.webhook(hook)                                           # replay
    assert await e2e.val("SELECT used_count FROM promo_codes WHERE code='E2E50'") == 1


# ── shutdown drains in-flight Telegram payments ─────────────────────────

async def _slow_telegram_payment(e2e, monkeypatch):
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
    monkeypatch.setattr(telegram_webhook, "_HANDLER_TIMEOUT", 0.1)
    hdr = {"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET}
    payload = f"purchase:{pending['purchase_id']}"
    await e2e.http.post("/telegram/webhook", headers=hdr, json=tgf.pre_checkout(u, payload, 19_900))
    post = asyncio.ensure_future(e2e.http.post(
        "/telegram/webhook", headers=hdr, json=tgf.successful_payment(u, payload, 19_900)))
    for _ in range(100):                                  # the webhook answers after the timeout
        if post.done():
            break
        await e2e._real_sleep(0.05)
    assert post.done() and post.result().status_code == 200
    assert telegram_webhook._unfinished_payment_tasks(), "the payment is not tracked as in flight"
    return u, gate


async def test_shutdown_waits_for_an_in_flight_telegram_payment(e2e, monkeypatch):
    u, gate = await _slow_telegram_payment(e2e, monkeypatch)
    before_pays = len(await e2e.payments(u.id))

    async def release_soon():
        await e2e._real_sleep(0.3)
        gate.set()
    asyncio.ensure_future(release_soon())
    left = await telegram_webhook.drain_payment_tasks(timeout=5)
    assert left == 0, "shutdown stopped waiting before the payment finished"
    await e2e.settle()
    assert len(await e2e.payments(u.id)) == before_pays + 1, "the payment did not complete during the drain"


async def test_shutdown_alerts_when_a_payment_is_still_running(e2e, monkeypatch):
    u, gate = await _slow_telegram_payment(e2e, monkeypatch)
    mark = e2e.tg.mark()
    left = await telegram_webhook.drain_payment_tasks(timeout=0.2)
    assert left == 1
    await e2e.settle(timeout=1)
    assert e2e.admin_texts(mark), "no alert about a Telegram payment still running at shutdown"
    gate.set()                                            # let it finish before teardown
    for _ in range(100):
        if not telegram_webhook._unfinished_payment_tasks():
            break
        await e2e._real_sleep(0.05)
    await e2e.settle()
