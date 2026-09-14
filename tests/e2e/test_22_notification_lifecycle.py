"""Scenario 22 — the subscription lifecycle, message by message, on the real DB
+ the fake panel + the real workers, with time moved by rewriting the dates
(docs/notifications/matrix.md; owner rules 2026-09-14):

  trial → trial reminder → bought during the trial (no trial message after) →
  paid reminders 7d / 3d / 1d / 3h → renewal (flags reset, the next period
  gets them again) → expiry (ONE message: GB left / VPN off, −15 % once);
  trial end = one message; days from an admin / a game during paid and during
  a trial; a gifted subscription ends with a message + −15 %; auto-renewal
  aware reminders; a renewal inside a reminders pass; the traffic notices.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import auto_renewal
import database
import fast_expiry_cleanup
import reminders
import trial_notifications
from app.i18n import get_text
from app.workers import traffic_monitor
from tests.e2e import flows
from tests.e2e.world import ADMIN, GIB, MB, naive, new_user, utcnow

T = lambda key, **kw: get_text("ru", key, **kw)          # noqa: E731
HEAD = lambda key, **kw: T(key, **kw).split("\n", 1)[0]  # noqa: E731
PERIOD_FLAGS = ("reminder_7d_sent", "reminder_3d_sent", "reminder_1d_sent", "reminder_3h_sent")
TRIAL_ENDED = T("trial.expired")


# ── helpers ────────────────────────────────────────────────────────────

async def reminders_pass(e2e):
    # the 30-min restart guard (last_reminder_at) is not what these tests are about
    await e2e.pool.execute("UPDATE subscriptions SET last_reminder_at = last_reminder_at - interval '2 hours' "
                           "WHERE last_reminder_at IS NOT NULL")
    await reminders.send_smart_reminders(e2e.bot)
    await e2e.settle()


async def expiry_pass(e2e):
    await e2e.run_worker_iterations(fast_expiry_cleanup, fast_expiry_cleanup.fast_expiry_cleanup_task)
    await trial_notifications.expire_trial_subscriptions(e2e.bot)
    await e2e.settle()


async def trial_pass(e2e):
    await trial_notifications.process_trial_notifications(e2e.bot)
    await e2e.settle()


async def end_premium(e2e, u):
    """The premium ended 5 minutes ago; the panel expired it by itself."""
    past = utcnow() - timedelta(minutes=5)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id, naive(past))
    ent = e2e.panel.premium(u.id)
    if ent is not None:
        ent["expireAt"], ent["status"] = past, "EXPIRED"


def use_bypass(e2e, u, used_bytes: int):
    e2e.panel.bypass(u.id)["userTraffic"]["usedTrafficBytes"] = int(used_bytes)


async def flags(e2e, u):
    sub = await e2e.sub(u.id)
    return {f: sub[f] for f in PERIOD_FLAGS}


async def trial_user(e2e):
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning("on")
    await e2e.tap(u, "activate_trial")
    await e2e.provisioning_tick()
    e2e.provisioning("off")
    await e2e.pool.execute("UPDATE subscriptions SET trial_notif_bypass_activated_sent=TRUE WHERE telegram_id=$1", u.id)
    return u


async def move_trial_end(e2e, u, left: timedelta):
    end = utcnow() + left
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id, naive(end))
    await e2e.pool.execute("UPDATE users SET trial_expires_at=$2, trial_used_at=$3 WHERE telegram_id=$1",
                           u.id, naive(end), naive(end - timedelta(hours=72)))


# ── 1. the whole journey ───────────────────────────────────────────────

async def test_trial_bought_during_the_trial_then_every_paid_message_once(e2e):
    u = await trial_user(e2e)
    await move_trial_end(e2e, u, timedelta(hours=24) - timedelta(minutes=5))
    m = e2e.tg.mark()
    await trial_pass(e2e)
    assert [HEAD("trial.reminder_24h")] == [t.split("\n", 1)[0] for t in e2e.user_texts(u.id, m)]

    # bought Basic during the trial: from now on only paid messages
    before = await flows.snapshot(e2e, u.id)
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert (await flows.pay(e2e, u, pending, "sbp")).body["status"] == "ok"
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    m = e2e.tg.mark()
    await trial_pass(e2e)
    await expiry_pass(e2e)
    assert not [t for t in e2e.user_texts(u.id, m) if "Пробный" in t or TRIAL_ENDED in t], e2e.user_texts(u.id, m)

    # the paid reminders, each exactly once
    for left, flag, head in [
        (timedelta(days=7), "reminder_7d_sent", HEAD("reminder.paid_7d")),
        (timedelta(days=3), "reminder_3d_sent", HEAD("reminder.paid_3d")),
        (timedelta(hours=24), "reminder_1d_sent", HEAD("reminder.paid_1d_gb", remaining="X")),
        (timedelta(hours=3), "reminder_3h_sent", HEAD("reminder.paid_3h_special_gb", remaining="X", deadline="X")),
    ]:
        await e2e.set_expiry(u.id, utcnow() + left - timedelta(minutes=10))
        m = e2e.tg.mark()
        await reminders_pass(e2e)
        await reminders_pass(e2e)
        texts = e2e.user_texts(u.id, m)
        assert len(texts) == 1 and texts[0].split("\n", 1)[0] == head, (flag, texts)
        assert (await e2e.sub(u.id))[flag] is True
        if flag in ("reminder_1d_sent", "reminder_3h_sent"):
            assert "10.5 ГБ" in texts[0], "GB left are named (#9): 500 MB + 10 GB"
    offer_at = await e2e.val("SELECT special_offer_created_at FROM users WHERE telegram_id=$1", u.id)
    assert offer_at is not None, "the 3 h reminder opened the −15 % window"

    # renewal → a new period: every flag reset, the reminders come again
    before = await flows.snapshot(e2e, u.id)
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert (await flows.pay(e2e, u, pending, "sbp")).body["status"] == "ok"
    await flows.check_purchase(e2e, u.id, before, "basic", 30)
    assert await flags(e2e, u) == {f: False for f in PERIOD_FLAGS}
    await e2e.set_expiry(u.id, utcnow() + timedelta(days=7) - timedelta(minutes=10))
    m = e2e.tg.mark()
    await reminders_pass(e2e)
    assert [t.split("\n", 1)[0] for t in e2e.user_texts(u.id, m)] == [HEAD("reminder.paid_7d")]

    # expiry: ONE message — the GB keep working, the amount is named
    await end_premium(e2e, u)
    m = e2e.tg.mark()
    await expiry_pass(e2e)
    await expiry_pass(e2e)
    texts = e2e.user_texts(u.id, m)
    assert len(texts) == 1 and texts[0].startswith(HEAD("subscription.expired_gb_left", remaining="X")), texts
    assert "20.5 ГБ" in texts[0]
    sub = await e2e.sub(u.id)
    assert (sub["is_bypass_only"], sub["uuid"]) == (True, None)


async def test_paid_expiry_at_zero_gb_says_the_vpn_is_off(e2e):
    """#2: «ГБ на месте» at 0 GB was false."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=1), flag="on")
    use_bypass(e2e, u, e2e.panel.bypass_limit(u.id))
    await end_premium(e2e, u)
    m = e2e.tg.mark()
    await expiry_pass(e2e)
    (text,) = e2e.user_texts(u.id, m)
    assert text.startswith(HEAD("subscription.expired_gb_spent"))
    assert "продолжает работать" not in text
    kb = e2e.tg.since(m, u.id)[0].callback_data()
    assert "buy_traffic" in kb and "menu_buy_vpn" in kb
    assert await e2e.val("SELECT special_offer_created_at FROM users WHERE telegram_id=$1", u.id) is not None


async def test_trial_end_is_one_message(e2e):
    u = await trial_user(e2e)
    await move_trial_end(e2e, u, -timedelta(minutes=5))
    ent = e2e.panel.premium(u.id)
    ent["expireAt"], ent["status"] = utcnow() - timedelta(minutes=5), "EXPIRED"
    m = e2e.tg.mark()
    await expiry_pass(e2e)
    await expiry_pass(e2e)
    assert e2e.user_texts(u.id, m) == [TRIAL_ENDED]
    assert e2e.panel.bypass(u.id)["status"] == "ACTIVE" and e2e.panel.bypass_limit(u.id) == 500 * MB


# ── 2. auto-renewal ────────────────────────────────────────────────────

@pytest.mark.parametrize("balance,key", [(5000, "reminder.paid_autorenew_ok"), (50, "reminder.paid_autorenew_topup")])
async def test_autorenew_reminder_says_what_will_happen(e2e, balance, key):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=7) - timedelta(minutes=10))
    await e2e.pool.execute("UPDATE subscriptions SET auto_renew=TRUE WHERE telegram_id=$1", u.id)
    await e2e.pool.execute("UPDATE users SET balance=$2 WHERE telegram_id=$1", u.id, balance * 100)
    m = e2e.tg.mark()
    await reminders_pass(e2e)
    (text,) = e2e.user_texts(u.id, m)
    assert text.startswith(T(key, date="X", amount="X", balance="X", missing="X", deadline="X").split("X", 1)[0])
    assert T("reminder.paid_7d") != text


# ── 3. every renewal path re-arms the paid reminders (owner, point 3) ──

async def _seven_days_reminder_once(e2e, u):
    assert await flags(e2e, u) == {f: False for f in PERIOD_FLAGS}, "a renewal resets every reminder flag"
    await e2e.set_expiry(u.id, utcnow() + timedelta(days=7) - timedelta(minutes=10))
    m = e2e.tg.mark()
    await reminders_pass(e2e)
    await reminders_pass(e2e)
    texts = e2e.user_texts(u.id, m)
    assert len(texts) == 1 and texts[0].split("\n", 1)[0] == HEAD("reminder.paid_7d"), texts


async def test_paid_reminders_come_again_after_every_kind_of_renewal(e2e):
    u, buyer = new_user(), new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=7) - timedelta(minutes=10))   # webhook
    await reminders_pass(e2e)
    assert (await e2e.sub(u.id))["reminder_7d_sent"] is True

    # 1) a provider webhook
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert (await flows.pay(e2e, u, pending, "sbp")).body["status"] == "ok"
    await _seven_days_reminder_once(e2e, u)

    # 2) the balance
    await e2e.pool.execute("UPDATE users SET balance=$2 WHERE telegram_id=$1", u.id, 100_000)
    await e2e.pool.execute("UPDATE payments SET created_at = created_at - interval '2 minutes' WHERE telegram_id=$1", u.id)
    await flows.buy(e2e, u, "basic", 30, "balance")
    await _seven_days_reminder_once(e2e, u)

    # 3) auto-renewal
    await e2e.set_expiry(u.id, utcnow() + timedelta(hours=3))
    await e2e.pool.execute("UPDATE subscriptions SET auto_renew=TRUE, last_auto_renewal_at=NULL WHERE telegram_id=$1", u.id)
    before = (await e2e.sub(u.id))["expires_at"]
    await auto_renewal.process_auto_renewals(e2e.bot)
    await e2e.settle()
    assert (await e2e.sub(u.id))["expires_at"] > before + timedelta(days=27)
    await e2e.pool.execute("UPDATE subscriptions SET auto_renew=FALSE WHERE telegram_id=$1", u.id)
    await _seven_days_reminder_once(e2e, u)

    # 4) a gift activated on top
    await e2e.register(buyer)
    gift = await e2e.create_purchase(buyer, "basic", 30, provider="platega", purchase_type="gift")
    assert (await flows.pay(e2e, buyer, gift, "sbp")).body["status"] == "ok"
    code = await e2e.val("SELECT gift_code FROM gift_subscriptions WHERE buyer_telegram_id=$1", buyer.id)
    await e2e.send(u, f"/start gift_{code}")
    await _seven_days_reminder_once(e2e, u)

    # 5) days from an admin — the subscription stays paid (#3)
    await database.admin_grant_access_atomic(u.id, 7, ADMIN, "basic")
    await e2e.settle()
    assert (await e2e.sub(u.id))["source"] in ("payment", "auto_renew", "gift")
    await _seven_days_reminder_once(e2e, u)


# ── 4. day grants ──────────────────────────────────────────────────────

async def test_game_days_during_a_trial_extend_the_trial(e2e):
    """#4: +days during a trial turned it into a game row — no trial reminders,
    never «пробный завершён», paid «продлите» instead."""
    u = await trial_user(e2e)
    old_end = (await e2e.sub(u.id))["expires_at"]
    res = await database.grant_access(telegram_id=u.id, duration=timedelta(days=3), source="game_dice", tariff="basic")
    await e2e.settle()
    sub = await e2e.sub(u.id)
    assert sub["source"] == "trial" and sub["expires_at"] == res["subscription_end"] > old_end
    trial_end = await e2e.val("SELECT trial_expires_at FROM users WHERE telegram_id=$1", u.id)
    assert abs((trial_end - naive(res["subscription_end"])).total_seconds()) < 1
    # the trial reminders go to the new end; no paid reminder for a trial
    await move_trial_end(e2e, u, timedelta(hours=24) - timedelta(minutes=5))
    m = e2e.tg.mark()
    await trial_pass(e2e)
    await reminders_pass(e2e)
    assert [t.split("\n", 1)[0] for t in e2e.user_texts(u.id, m)] == [HEAD("trial.reminder_24h")]


async def test_free_days_end_with_their_own_message(e2e):
    """#18: admin days without a subscription ended in silence."""
    u = new_user()
    await e2e.register(u)
    await database.admin_grant_access_atomic(u.id, 14, ADMIN, "basic")
    await e2e.settle()
    await e2e.set_expiry(u.id, utcnow() + timedelta(hours=24) - timedelta(minutes=10))
    m = e2e.tg.mark()
    await reminders_pass(e2e)
    (text,) = e2e.user_texts(u.id, m)
    assert text.split("\n", 1)[0] == HEAD("reminder.admin_7days_24h", price="X")
    assert "199₽" not in text and "{price}" not in text

    await end_premium(e2e, u)
    m = e2e.tg.mark()
    await expiry_pass(e2e)
    texts = e2e.user_texts(u.id, m)
    assert len(texts) == 1, texts


async def test_gifted_subscription_end_is_told_with_the_offer(e2e):
    """#19: a gift ended in silence and without −15 %."""
    buyer, u = new_user(), new_user()
    await e2e.register(buyer)
    gift = await e2e.create_purchase(buyer, "basic", 30, provider="platega", purchase_type="gift")
    assert (await flows.pay(e2e, buyer, gift, "sbp")).body["status"] == "ok"
    code = await e2e.val("SELECT gift_code FROM gift_subscriptions WHERE buyer_telegram_id=$1", buyer.id)
    await e2e.start_user(u, f"gift_{code}")
    assert (await e2e.sub(u.id))["source"] == "gift"
    await end_premium(e2e, u)
    m = e2e.tg.mark()
    await expiry_pass(e2e)
    texts = e2e.user_texts(u.id, m)
    assert len(texts) == 1, texts
    assert await e2e.val("SELECT special_offer_created_at FROM users WHERE telegram_id=$1", u.id) is not None


# ── 5. a renewal inside a reminders pass (#16) ─────────────────────────

async def test_renewal_inside_the_pass_sends_no_old_date_reminder(e2e, monkeypatch):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=7) - timedelta(minutes=10))
    snapshot = await database.get_subscriptions_for_reminders()          # the pass read the rows …
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")                # … the user renewed meanwhile
    assert (await flows.pay(e2e, u, pending, "sbp")).body["status"] == "ok"

    async def stale():
        return snapshot
    monkeypatch.setattr(database, "get_subscriptions_for_reminders", stale)
    m = e2e.tg.mark()
    await reminders.send_smart_reminders(e2e.bot)
    await e2e.settle()
    assert e2e.user_texts(u.id, m) == []
    assert (await e2e.sub(u.id))["reminder_7d_sent"] is False, "the new period keeps its reminder"


# ── 6. traffic notices ─────────────────────────────────────────────────

async def test_traffic_one_notice_for_the_lowest_threshold_zero_by_state_and_re_armed(e2e):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=10), flag="on")   # 10 GB
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)            # baseline: 10 GB left
    m = e2e.tg.mark()
    use_bypass(e2e, u, 8 * GIB)                                         # a sharp drop: 2 GB left
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)
    (text,) = e2e.user_texts(u.id, m)
    assert text.split("\n", 1)[0] == HEAD("traffic.left_warn", remaining="2 ГБ")

    # used up: premium active → «основные серверы работают» (after the 3 h gap)
    await e2e.pool.execute("UPDATE users SET traffic_notice_last_at = traffic_notice_last_at - interval '4 hours' "
                           "WHERE telegram_id=$1", u.id)
    use_bypass(e2e, u, 10 * GIB)
    m = e2e.tg.mark()
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)
    assert [t.split("\n", 1)[0] for t in e2e.user_texts(u.id, m)] == [HEAD("traffic.zero_premium")]

    # more GB: the thresholds below the new amount come again (a new baseline first)
    e2e.panel.bypass(u.id)["trafficLimitBytes"] = 20 * GIB                # a 10 GB pack
    await e2e.pool.execute("UPDATE users SET traffic_notice_last_at = traffic_notice_last_at - interval '4 hours' "
                           "WHERE telegram_id=$1", u.id)
    m = e2e.tg.mark()
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)            # baseline: 10 GB left
    use_bypass(e2e, u, 16 * GIB)                                        # 4 GB left
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)
    assert [t.split("\n", 1)[0] for t in e2e.user_texts(u.id, m)] == [HEAD("traffic.left_warn", remaining="4 ГБ")]


async def test_traffic_zero_without_premium_says_access_is_off(e2e):
    """#7: «Atlas Fast работает без ограничений» went to users WITHOUT premium."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=1), flag="on")
    await end_premium(e2e, u)
    await expiry_pass(e2e)                                              # → bypass-only
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)            # baseline
    use_bypass(e2e, u, e2e.panel.bypass_limit(u.id))
    m = e2e.tg.mark()
    await traffic_monitor.traffic_monitor_iteration(e2e.bot)
    (text,) = e2e.user_texts(u.id, m)
    assert text.split("\n", 1)[0] == HEAD("traffic.zero_no_premium") and "Atlas Fast" not in text
