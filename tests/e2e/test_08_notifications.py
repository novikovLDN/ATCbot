"""Scenario 8 — notification workers, one real tick each, and no duplicate on
the second tick: paid reminders 7d / 1d, trial 24h / 3h, expiry cleanup (+ the
−15 % offer once per ended paid period, merged fix 377062c2).
"""
from datetime import timedelta

import pytest

import fast_expiry_cleanup
import reminders
import trial_notifications
from tests.e2e import flows
from tests.e2e.world import new_user, utcnow


async def _tick_reminders(e2e):
    await reminders.send_smart_reminders(e2e.bot)
    await e2e.settle()


@pytest.mark.parametrize("left,flag", [(timedelta(days=7), "reminder_7d_sent"),
                                       (timedelta(hours=24), "reminder_1d_sent")])
async def test_paid_reminder_sent_once(e2e, left, flag):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + left - timedelta(minutes=10))
    mark = e2e.tg.mark()
    await _tick_reminders(e2e)
    first = e2e.user_texts(u.id, mark)
    assert len(first) == 1, first
    assert (await e2e.sub(u.id))[flag] is True
    # a later tick (after the 30-min restart guard too) sends nothing again
    await e2e.pool.execute(
        "UPDATE subscriptions SET last_reminder_at = last_reminder_at - interval '2 hours' WHERE telegram_id=$1",
        u.id)
    mark2 = e2e.tg.mark()
    await _tick_reminders(e2e)
    assert e2e.user_texts(u.id, mark2) == []


async def test_no_reminder_far_from_expiry_or_for_unreachable_user(e2e):
    far, blocked = new_user(), new_user()
    await flows.seed_active(e2e, far, "basic", utcnow() + timedelta(days=15))
    await flows.seed_active(e2e, blocked, "basic", utcnow() + timedelta(days=7) - timedelta(minutes=10))
    await e2e.pool.execute("UPDATE users SET is_reachable = FALSE WHERE telegram_id=$1", blocked.id)
    mark = e2e.tg.mark()
    await _tick_reminders(e2e)
    assert e2e.user_texts(far.id, mark) == [] and e2e.user_texts(blocked.id, mark) == []


async def _trial_user(e2e, left: timedelta):
    u = new_user()
    await e2e.start_user(u)
    await e2e.tap(u, "activate_trial")
    end = utcnow() + left
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id,
                           end.replace(tzinfo=None))
    await e2e.pool.execute(
        "UPDATE users SET trial_expires_at=$2, trial_used_at=$3 WHERE telegram_id=$1", u.id,
        end.replace(tzinfo=None), (end - timedelta(hours=72)).replace(tzinfo=None))
    await e2e.pool.execute("UPDATE subscriptions SET trial_notif_bypass_activated_sent=TRUE WHERE telegram_id=$1", u.id)
    return u


@pytest.mark.parametrize("left,flag", [(timedelta(hours=24), "trial_notif_24h_sent"),
                                       (timedelta(hours=3), "trial_notif_3h_sent")])
async def test_trial_reminder_sent_once(e2e, left, flag):
    u = await _trial_user(e2e, left - timedelta(minutes=5))
    mark = e2e.tg.mark()
    await trial_notifications.process_trial_notifications(e2e.bot)
    await e2e.settle()
    assert len(e2e.user_texts(u.id, mark)) == 1, e2e.user_texts(u.id, mark)
    assert (await e2e.sub(u.id))[flag] is True
    mark2 = e2e.tg.mark()
    await trial_notifications.process_trial_notifications(e2e.bot)
    await e2e.settle()
    assert e2e.user_texts(u.id, mark2) == []


async def test_expired_paid_subscription_cleanup_once_with_one_offer(e2e):
    """Premium ends → the row becomes bypass-only (remaining GB keep working),
    the user is told, the −15 % offer is granted once; a second tick does nothing."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=1), flag="on")
    await _expire_and_check(e2e, u)


@pytest.mark.xfail(strict=True, reason="E2E-CACHE (medium, open): the first legacy (flag-off) purchase "
                   "leaves subscriptions.remnawave_uuid NULL, so expiry cleanup marks the row 'expired' "
                   "instead of bypass-only and sends no notice (the bypass entity still works in the panel)")
async def test_expired_after_first_legacy_purchase_becomes_bypass_only(e2e):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=1))
    await _expire_and_check(e2e, u)


async def _expire_and_check(e2e, u):
    past = utcnow() - timedelta(minutes=5)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id,
                           past.replace(tzinfo=None))
    limit = e2e.panel.bypass_limit(u.id)

    mark = e2e.tg.mark()
    await e2e.run_worker_iterations(fast_expiry_cleanup, fast_expiry_cleanup.fast_expiry_cleanup_task)
    sub = await e2e.sub(u.id)
    assert sub["uuid"] is None, "expired premium key not revoked"
    assert sub["is_bypass_only"] is True and sub["status"] == "active"
    assert e2e.panel.bypass_limit(u.id) == limit, "remaining bypass GB must survive premium expiry"
    offer_at = await e2e.val("SELECT special_offer_created_at FROM users WHERE telegram_id=$1", u.id)
    assert offer_at is not None, "−15 % offer not granted on a paid expiry"
    assert len(e2e.user_texts(u.id, mark)) == 1, "user not told that premium ended (bypass GB remain)"

    mark2 = e2e.tg.mark()
    await e2e.run_worker_iterations(fast_expiry_cleanup, fast_expiry_cleanup.fast_expiry_cleanup_task)
    assert e2e.user_texts(u.id, mark2) == [], "expiry processed twice"
    assert await e2e.val("SELECT special_offer_created_at FROM users WHERE telegram_id=$1", u.id) == offer_at
    assert e2e.admin_texts(mark) == []


async def test_cleanup_does_not_touch_an_active_subscription(e2e):
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=3))
    before = await e2e.sub(u.id)
    await e2e.run_worker_iterations(fast_expiry_cleanup, fast_expiry_cleanup.fast_expiry_cleanup_task)
    after = await e2e.sub(u.id)
    assert (after["uuid"], after["expires_at"], after["is_bypass_only"]) == (
        before["uuid"], before["expires_at"], before["is_bypass_only"])
