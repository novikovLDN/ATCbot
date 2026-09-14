"""Scenario 17 — «🌐 Только обход блокировок» without a subscription (owner rule
2026-09-14, docs/audit/SCOPE.md): the buyer gets EXACTLY the GB bought plus a
ONE-TIME 3-day premium gift (regardless of the trial flag, no trial 500 MB).
When the gift ends the GB keep working; later purchases are assigned correctly.

Every step goes through the real bot UI (the screen, the pack, the payment
button), a signed provider webhook, the real DB and the real workers; USE_NEW_PROVISIONING
off and on.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

import fast_expiry_cleanup
import trial_notifications
from app.i18n import get_text
from app.services.trials import service as ts
from tests.e2e import flows
from tests.e2e.world import GIB, aware, naive, new_user, utcnow
from tests.fakes import providers_http as prov

GIFT_PROMISE = get_text("ru", "bypass.buy_title_trial")
GIFT_LINE = get_text("ru", "bypass.gift_premium_granted").split("{", 1)[0]
BYPASS_WORKS = get_text("ru", "traffic.subscription_expired_bypass_active").split("\n", 1)[0]
TRIAL_ENDED = get_text("ru", "trial.expired")
FALSE_CLAIMS = ("VPN перестанет", "VPN будет отключ", "вернётся к блокировкам")
FLAGS = pytest.mark.parametrize("flag", ["off", "on"])


# ── helpers ─────────────────────────────────────────────────────────────

async def open_bypass_screen(e2e, u) -> str:
    mark = e2e.tg.mark()
    await e2e.tap(u, "buy_bypass_only")
    texts = e2e.user_texts(u.id, mark)
    assert texts, "the «Только обход» screen was not shown"
    return texts[-1]


async def choose_pack(e2e, u, gb: int, method: str):
    await e2e.tap(u, f"buy_bypass_pack:{gb}")
    last = await e2e.val("SELECT COALESCE(max(id), 0) FROM pending_purchases WHERE telegram_id=$1", u.id)
    await e2e.tap(u, f"bypass_pay_{method}:{gb}")
    p = await flows.latest_pending(e2e, u.id)
    assert p is not None and p["id"] > last, f"bypass_pay_{method} created no purchase"
    assert (p["tariff"], p["purchase_type"]) == (f"bypass_{gb}gb", "traffic_pack")
    return p


def paid_hook(p, method: str):
    amount = p["price_kopecks"] / 100
    if method == "sbp":
        return prov.platega_webhook(p["purchase_id"], amount)
    return prov.wata_webhook(p["purchase_id"], amount)


async def buy_bypass(e2e, u, gb: int, *, flag: str, method: str = "sbp"):
    """Screen → pack → payment button → the provider says «paid». Returns
    (screen text, pending purchase, Telegram mark before the webhook)."""
    screen = await open_bypass_screen(e2e, u)
    p = await choose_pack(e2e, u, gb, method)
    mark = e2e.tg.mark()
    res = await e2e.webhook(paid_hook(p, method))
    assert (res.status, res.body["status"]) == (200, "ok"), res
    if flag == "on":
        await e2e.provisioning_tick()
    return screen, p, mark


async def assert_gift_state(e2e, u, t0, *, gb: int, flag: str) -> dict:
    """DB + panel after a bypass-only purchase that got the gift."""
    sub = await e2e.sub(u.id)
    assert (sub["status"], sub["source"], sub["is_bypass_only"]) == ("active", "trial", False), sub
    assert (sub["subscription_type"] or "basic") == "basic"
    lo, hi = t0 + timedelta(days=3), utcnow() + timedelta(days=3)
    assert lo - timedelta(seconds=5) <= sub["expires_at"] <= hi + timedelta(seconds=5), sub["expires_at"]
    assert sub["uuid"], "premium key not written"
    # #6: the trial worker must not announce «500 МБ в подарок» to the gift
    assert sub["trial_notif_bypass_activated_sent"] is True
    assert sub["remnawave_uuid"], "bypass pointer missing — expiry would disable the bypass entity"
    user = await e2e.row("SELECT trial_used_at, trial_expires_at FROM users WHERE telegram_id=$1", u.id)
    assert user["trial_used_at"] is not None
    assert abs((aware(user["trial_expires_at"]) - sub["expires_at"]).total_seconds()) < 5
    assert e2e.panel.bypass_limit(u.id) == gb * GIB, "exactly the GB bought (no trial 500 MB)"
    prem = e2e.panel.premium(u.id)
    assert prem is not None and prem["status"] == "ACTIVE"
    assert abs((prem["expireAt"] - sub["expires_at"]).total_seconds()) <= 1.5, (prem["expireAt"], sub["expires_at"])
    assert [x["tariff"] for x in await e2e.payments(u.id)] == [f"bypass_{gb}gb"]
    if flag == "on":
        jobs = {j["idempotency_key"]: j for j in await e2e.jobs(u.id)}
        assert jobs[f"trial:{u.id}"]["bypass_add_bytes"] == 0
        assert all(j["status"] == "done" for j in jobs.values()), jobs
    return sub


async def expire_gift(e2e, u) -> None:
    """3 days later: the DB date is in the past and the panel expired the
    premium entity by itself (Remnawave sets EXPIRED at expireAt)."""
    past = utcnow() - timedelta(minutes=5)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id, naive(past))
    await e2e.pool.execute("UPDATE users SET trial_expires_at=$2 WHERE telegram_id=$1", u.id, naive(past))
    ent = e2e.panel.premium(u.id)
    ent["expireAt"] = past
    ent["status"] = "EXPIRED"


async def run_expiry_workers(e2e) -> None:
    await e2e.run_worker_iterations(fast_expiry_cleanup, fast_expiry_cleanup.fast_expiry_cleanup_task)
    await trial_notifications.expire_trial_subscriptions(e2e.bot)
    await e2e.settle()


# ── 1. the purchase ─────────────────────────────────────────────────────

@FLAGS
@pytest.mark.parametrize("method", ["sbp", "wata"])
async def test_new_user_gets_exact_gb_and_a_3_day_gift_once(e2e, flag, method):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag)
    t0 = utcnow()
    screen, p, mark = await buy_bypass(e2e, u, 15, flag=flag, method=method)

    assert GIFT_PROMISE in screen
    sub = await assert_gift_state(e2e, u, t0, gb=15, flag=flag)
    msgs = [t for t in e2e.user_texts(u.id, mark) if GIFT_LINE in t]
    assert len(msgs) == 1, e2e.user_texts(u.id, mark)
    assert "+15" in msgs[0]
    assert ts.format_gift_until(sub["expires_at"]) + " МСК" in msgs[0]
    assert e2e.admin_texts(mark) == []

    # the same webhook again: nothing twice (GB, gift, messages)
    jobs = len(await e2e.jobs(u.id))
    m2 = e2e.tg.mark()
    again = await e2e.webhook(paid_hook(p, method))
    assert again.body["status"] == "already_processed"
    await e2e.provisioning_tick()
    assert e2e.panel.bypass_limit(u.id) == 15 * GIB
    assert (await e2e.sub(u.id))["expires_at"] == sub["expires_at"]
    assert len(await e2e.jobs(u.id)) == jobs
    assert not [t for t in e2e.user_texts(u.id, m2) if GIFT_LINE in t]


@FLAGS
@pytest.mark.parametrize("state", ["trial_used_long_ago", "gift_active"])
async def test_trial_or_gift_already_used_gives_only_the_gb(e2e, flag, state):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag)
    if state == "trial_used_long_ago":
        await e2e.pool.execute(
            "UPDATE users SET trial_used_at = NOW() - interval '60 days', "
            "trial_expires_at = NOW() - interval '57 days' WHERE telegram_id=$1", u.id)
        start_gb = 0
    else:
        await buy_bypass(e2e, u, 15, flag=flag)
        start_gb = 15
    before = await e2e.sub(u.id)
    premium_before = e2e.panel.premium_expire(u.id)

    screen, _p, mark = await buy_bypass(e2e, u, 50, flag=flag)

    assert GIFT_PROMISE not in screen
    assert e2e.panel.bypass_limit(u.id) == (start_gb + 50) * GIB
    assert e2e.panel.premium_expire(u.id) == premium_before
    after = await e2e.sub(u.id)
    if state == "gift_active":
        assert (after["expires_at"], after["source"], after["is_bypass_only"]) == \
            (before["expires_at"], "trial", False)
    else:
        assert after["is_bypass_only"] is True and e2e.panel.premium(u.id) is None
    texts = e2e.user_texts(u.id, mark)
    assert texts and not [t for t in texts if GIFT_LINE in t]
    if flag == "on":
        keys = [j["idempotency_key"] for j in await e2e.jobs(u.id)]
        assert keys.count(f"trial:{u.id}") == (1 if state == "gift_active" else 0)


@FLAGS
async def test_active_paid_subscriber_buying_on_that_screen_gets_only_gb(e2e, flag):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20), flag="on")
    e2e.provisioning(flag)
    before = await e2e.sub(u.id)
    premium, limit = e2e.panel.premium_expire(u.id), e2e.panel.bypass_limit(u.id)

    screen, _p, mark = await buy_bypass(e2e, u, 15, flag=flag)

    assert GIFT_PROMISE not in screen
    after = await e2e.sub(u.id)
    assert (after["expires_at"], after["source"], after["is_bypass_only"]) == \
        (before["expires_at"], before["source"], False)
    assert e2e.panel.premium_expire(u.id) == premium
    assert e2e.panel.bypass_limit(u.id) == limit + 15 * GIB
    assert (await e2e.row("SELECT trial_used_at FROM users WHERE telegram_id=$1", u.id))["trial_used_at"] is None
    assert not [t for t in e2e.user_texts(u.id, mark) if GIFT_LINE in t]


# ── 2. the full lifecycle ───────────────────────────────────────────────

@FLAGS
async def test_lifecycle_gift_ends_gb_stay_then_basic_then_more_gb_then_no_second_gift(e2e, flag):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag)
    t0 = utcnow()
    await buy_bypass(e2e, u, 15, flag=flag)
    await assert_gift_state(e2e, u, t0, gb=15, flag=flag)

    # 3 days pass: premium ends, the bot's expiry workers run
    await expire_gift(e2e, u)
    mark = e2e.tg.mark()
    await run_expiry_workers(e2e)
    sub = await e2e.sub(u.id)
    assert (sub["status"], sub["is_bypass_only"], sub["source"], sub["uuid"]) == \
        ("active", True, "bypass_only", None), sub
    byp = e2e.panel.bypass(u.id)
    assert (byp["status"], byp["trafficLimitBytes"]) == ("ACTIVE", 15 * GIB), "the GB must keep working"
    texts = e2e.user_texts(u.id, mark)
    # #1: the gift IS the trial — its end is ONE message, «пробный завершён»
    assert texts == [TRIAL_ENDED], texts
    assert not [t for t in texts if BYPASS_WORKS in t], texts
    assert not [t for t in texts for claim in FALSE_CLAIMS if claim in t], texts
    # a second worker round changes nothing
    m2 = e2e.tg.mark()
    await run_expiry_workers(e2e)
    assert e2e.user_texts(u.id, m2) == [] and e2e.panel.bypass(u.id)["status"] == "ACTIVE"

    # buy Basic 1 month: premium from NOW by a calendar month, +10 GB, a normal paid row
    before = await flows.snapshot(e2e, u.id)
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.body["status"] == "ok", res
    if flag == "on":
        await e2e.provisioning_tick()
    sub = await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert sub["source"] == "payment" and sub["expires_at"] < utcnow() + timedelta(days=32), \
        "a paid row, never the 10-year bypass-only placeholder"
    assert e2e.panel.bypass_limit(u.id) == 25 * GIB
    assert e2e.panel.premium(u.id)["status"] == "ACTIVE"

    # more GB later from the same screen: only GB, premium untouched
    screen, _p, mark = await buy_bypass(e2e, u, 50, flag=flag)
    assert GIFT_PROMISE not in screen
    after = await e2e.sub(u.id)
    assert (after["expires_at"], after["source"], after["is_bypass_only"]) == (sub["expires_at"], "payment", False)
    assert e2e.panel.bypass_limit(u.id) == 75 * GIB
    assert not [t for t in e2e.user_texts(u.id, mark) if GIFT_LINE in t]


@FLAGS
async def test_gift_ended_then_second_gb_purchase_gives_only_gb(e2e, flag):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag)
    await buy_bypass(e2e, u, 15, flag=flag)
    await expire_gift(e2e, u)
    await run_expiry_workers(e2e)
    premium = e2e.panel.premium_expire(u.id)

    screen, _p, mark = await buy_bypass(e2e, u, 50, flag=flag)

    assert GIFT_PROMISE not in screen
    sub = await e2e.sub(u.id)
    assert (sub["is_bypass_only"], sub["source"]) == (True, "bypass_only")
    assert e2e.panel.bypass_limit(u.id) == 65 * GIB
    assert e2e.panel.premium_expire(u.id) == premium
    assert not [t for t in e2e.user_texts(u.id, mark) if GIFT_LINE in t]
    if flag == "on":
        assert [j["idempotency_key"] for j in await e2e.jobs(u.id)].count(f"trial:{u.id}") == 1


@FLAGS
@pytest.mark.parametrize("tariff", ["basic", "plus", "combo_basic"])
async def test_paid_purchase_while_the_gift_is_active_extends_from_the_gift_end(e2e, flag, tariff):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag)
    await buy_bypass(e2e, u, 15, flag=flag)
    before = await flows.snapshot(e2e, u.id)
    assert before.active, "the gift is an active premium"

    pending = await flows.buy(e2e, u, tariff, 30, "sbp")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.body["status"] == "ok", res
    if flag == "on":
        await e2e.provisioning_tick()

    sub = await flows.check_purchase(e2e, u.id, before, tariff, 30)   # gift end + 1 calendar month
    assert sub["source"] == "payment"
    assert bool(sub["is_combo"]) is tariff.startswith("combo_")


@FLAGS
async def test_gb_bought_right_after_the_premium_ended_before_cleanup_gets_the_gift(e2e, flag):
    """The premium ended a minute ago and the cleanup did not run yet (row still
    active, past date, old key set). Before the fix the GB purchase turned it into
    a bypass-only row that KEPT the key: every later grant raised «Invalid renewal»."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=3), flag="on")
    past = utcnow() - timedelta(minutes=1)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id, naive(past))
    ent = e2e.panel.premium(u.id)
    ent["expireAt"], ent["status"] = past, "EXPIRED"
    e2e.provisioning(flag)
    limit = e2e.panel.bypass_limit(u.id)
    t0 = utcnow()

    screen, _p, mark = await buy_bypass(e2e, u, 15, flag=flag)

    assert GIFT_PROMISE in screen
    sub = await e2e.sub(u.id)
    assert (sub["status"], sub["source"], sub["is_bypass_only"]) == ("active", "trial", False), sub
    assert t0 + timedelta(days=3) - timedelta(seconds=5) <= sub["expires_at"] <= utcnow() + timedelta(days=3, seconds=5)
    assert e2e.panel.bypass_limit(u.id) == limit + 15 * GIB
    prem = e2e.panel.premium(u.id)
    assert prem["status"] == "ACTIVE" and abs((prem["expireAt"] - sub["expires_at"]).total_seconds()) <= 1.5
    assert [t for t in e2e.user_texts(u.id, mark) if GIFT_LINE in t]
    assert "Bypass-purchase gift" not in "\n".join(e2e.admin_texts(mark))

    before = await flows.snapshot(e2e, u.id)
    pending = await flows.buy(e2e, u, "plus", 90, "sbp")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.body["status"] == "ok", res
    if flag == "on":
        await e2e.provisioning_tick()
    await flows.check_purchase(e2e, u.id, before, "plus", 90)      # from the gift end


@FLAGS
@pytest.mark.parametrize("tariff,period", [("basic", 30), ("plus", 90), ("combo_basic", 30)])
async def test_paid_purchase_on_a_legacy_bypass_only_row_with_a_stale_key(e2e, flag, tariff, period):
    """Rows the OLD ensure_bypass_only_subscription left in production (GB bought
    after the premium ended, before the cleanup): bypass-only, 10-year placeholder,
    the old premium key still set. Before the fix: Basic → «Invalid renewal» (paid,
    not credited), Plus → premium until placeholder + 3 months (~10 years)."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=3), flag="on")
    await e2e.pool.execute(
        "UPDATE subscriptions SET is_bypass_only=TRUE, source='bypass_only', "
        "expires_at = (NOW() AT TIME ZONE 'UTC') + interval '3650 days' WHERE telegram_id=$1", u.id)
    ent = e2e.panel.premium(u.id)
    ent["expireAt"], ent["status"] = utcnow() - timedelta(days=1), "EXPIRED"
    assert (await e2e.sub(u.id))["uuid"], "the stale key is the point of this test"
    e2e.provisioning(flag)
    before = await flows.snapshot(e2e, u.id)
    assert not before.active

    pending = await flows.buy(e2e, u, tariff, period, "sbp")
    mark = e2e.tg.mark()
    res = await flows.pay(e2e, u, pending, "sbp")
    assert (res.status, res.body["status"]) == (200, "ok"), res
    if flag == "on":
        await e2e.provisioning_tick()

    sub = await flows.check_purchase(e2e, u.id, before, tariff, period)   # from NOW
    assert sub["expires_at"] < utcnow() + timedelta(days=100), "never the 10-year placeholder"
    assert e2e.admin_texts(mark) == []


# ── 3. the panel fails during the gift ──────────────────────────────────

@FLAGS
async def test_panel_failure_during_the_gift_alerts_and_the_gift_arrives_later(e2e, flag):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning(flag)
    gate = asyncio.Event()

    async def gated_retry_sleep(_seconds):
        await gate.wait()
    e2e.mp.setattr(ts, "_gift_retry_sleep", gated_retry_sleep)
    e2e.panel.fail("POST", lambda _r, b: str(b.get("username", "")).endswith("_premium"))
    await open_bypass_screen(e2e, u)
    p = await choose_pack(e2e, u, 15, "sbp")
    t0 = utcnow()
    mark = e2e.tg.mark()

    res = await e2e.webhook(paid_hook(p, "sbp"), settle=False)
    assert (res.status, res.body["status"]) == (200, "ok"), res
    for task in ts._gift_retry_tasks.values():
        task._e2e_daemon = True          # parked on the gate, not a lingering task
    await e2e.settle()

    assert e2e.panel.bypass_limit(u.id) == 15 * GIB, "the GB are never held back by the gift"
    admin = "\n".join(e2e.admin_texts(mark))
    user_text = "\n".join(e2e.user_texts(u.id, mark))
    if flag == "off":
        assert "Bypass-purchase gift (3 days premium) FAILED" in admin
        assert (await e2e.row("SELECT trial_used_at FROM users WHERE telegram_id=$1", u.id))["trial_used_at"] is None
        assert GIFT_LINE not in user_text          # not promised while it is not granted
    else:
        assert admin, "the outbox must alert the admin about the stuck gift job"
        assert GIFT_LINE in user_text              # granted in the DB, the panel side is queued

    e2e.panel.clear_failures()
    m2 = e2e.tg.mark()
    if flag == "off":
        gate.set()
        task = ts._gift_retry_tasks.get(u.id)
        if task is not None:
            await task
        await e2e.settle()
        assert GIFT_LINE in "\n".join(e2e.user_texts(u.id, m2)), "the user is told when the retry grants it"
    else:
        await e2e.pool.execute(
            "UPDATE provisioning_jobs SET next_attempt_at = (NOW() AT TIME ZONE 'UTC') "
            "WHERE telegram_id=$1 AND status='pending'", u.id)
        await e2e.provisioning_tick()
    await assert_gift_state(e2e, u, t0, gb=15, flag=flag)


# ── 4. notifications during the gift ────────────────────────────────────

@pytest.mark.parametrize("left,flag_col", [(timedelta(hours=24), "trial_notif_24h_sent"),
                                           (timedelta(hours=3), "trial_notif_3h_sent")])
async def test_gift_reminders_say_the_gb_keep_working(e2e, left, flag_col):
    u = new_user()
    await e2e.start_user(u)
    await buy_bypass(e2e, u, 15, flag="off")
    end = utcnow() + left - timedelta(minutes=5)
    await e2e.pool.execute(
        "UPDATE subscriptions SET expires_at=$2, trial_notif_bypass_activated_sent=TRUE WHERE telegram_id=$1",
        u.id, naive(end))
    await e2e.pool.execute(
        "UPDATE users SET trial_expires_at=$2, trial_used_at=$3 WHERE telegram_id=$1",
        u.id, naive(end), naive(end - timedelta(hours=72)))
    mark = e2e.tg.mark()
    await trial_notifications.process_trial_notifications(e2e.bot)
    await e2e.settle()
    texts = e2e.user_texts(u.id, mark)
    assert len(texts) == 1, texts
    assert "ГБ" in texts[0]
    assert not [claim for claim in FALSE_CLAIMS if claim in texts[0]], texts[0]
    assert (await e2e.sub(u.id))[flag_col] is True
