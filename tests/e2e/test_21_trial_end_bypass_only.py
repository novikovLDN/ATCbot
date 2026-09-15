"""Scenario 21 — the trial ends while the user's row is ALREADY bypass-only.

Production: "Trial cleanup skipped: user has active paid subscription …
paid_expires_at=2036-09-…" — the 10-year placeholder of a bypass-only row
counted as a paid subscription. When the ended trial row was turned into
bypass-only by a path that does not send "trial ended" (a screen open →
check_and_disable_expired_subscription; GB bought right after the end →
ensure_bypass_only_subscription), the trial worker skipped the user for good.

Now: "trial ended" exactly once, the row and the bypass entity untouched (the
GB keep working), never «VPN stopped». A real paid subscriber is still skipped.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import database
import fast_expiry_cleanup
import trial_notifications
from app.i18n import get_text
from tests.e2e import flows
from tests.e2e.test_17_bypass_only_gift import buy_bypass
from tests.e2e.world import GIB, naive, new_user, utcnow

MB = 1024 * 1024
TRIAL_ENDED = get_text("ru", "trial.expired").split("\n", 1)[0]
FALSE_CLAIMS = ("VPN перестанет", "VPN будет отключ", "вернётся к блокировкам")
FLAGS = pytest.mark.parametrize("flag", ["off", "on"])


async def trial_user(e2e, flag):
    """A trial activated as production does now (new provisioning core on: the
    500 MB bypass and its pointer are written at activation; the legacy path
    writes them lazily from a screen). `flag` is the mode for what follows."""
    u = new_user()
    await e2e.start_user(u)
    e2e.provisioning("on")
    await e2e.tap(u, "activate_trial")
    await e2e.provisioning_tick()
    sub = await e2e.sub(u.id)
    assert sub["source"] == "trial" and sub["remnawave_uuid"], sub
    assert e2e.panel.bypass_limit(u.id) == 500 * MB
    e2e.provisioning(flag)
    return u


async def end_trial(e2e, u):
    """The trial ended 5 minutes ago; the panel expired the premium itself."""
    past = utcnow() - timedelta(minutes=5)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1", u.id, naive(past))
    await e2e.pool.execute("UPDATE users SET trial_expires_at=$2 WHERE telegram_id=$1", u.id, naive(past))
    ent = e2e.panel.premium(u.id)
    ent["expireAt"], ent["status"] = past, "EXPIRED"


async def run_workers(e2e):
    await trial_notifications.expire_trial_subscriptions(e2e.bot)
    await e2e.run_worker_iterations(fast_expiry_cleanup, fast_expiry_cleanup.fast_expiry_cleanup_task)
    await e2e.settle()


async def completed_sent(e2e, u) -> bool:
    return await e2e.val("SELECT trial_completed_sent FROM users WHERE telegram_id=$1", u.id)


async def assert_told_once_bypass_kept(e2e, u, mark, *, limit):
    texts = e2e.user_texts(u.id, mark)
    assert len([t for t in texts if TRIAL_ENDED in t]) == 1, texts
    assert not [t for t in texts for claim in FALSE_CLAIMS if claim in t], texts
    assert await completed_sent(e2e, u) is True
    sub = await e2e.sub(u.id)
    assert (sub["status"], sub["is_bypass_only"], sub["source"], sub["uuid"]) == \
        ("active", True, "bypass_only", None), sub
    byp = e2e.panel.bypass(u.id)
    assert (byp["status"], byp["trafficLimitBytes"]) == ("ACTIVE", limit), "the GB must keep working"

    m2 = e2e.tg.mark()
    await run_workers(e2e)
    assert e2e.user_texts(u.id, m2) == [], "trial ended is sent once"
    assert e2e.panel.bypass(u.id)["status"] == "ACTIVE"


@FLAGS
async def test_row_turned_bypass_only_by_a_screen_open(e2e, flag):
    u = await trial_user(e2e, flag)
    await end_trial(e2e, u)
    mark = e2e.tg.mark()
    # the user opens the bot before the cleanup: the lazy expiry check
    assert await database.check_and_disable_expired_subscription(u.id) is True
    await e2e.settle()
    assert (await e2e.sub(u.id))["is_bypass_only"] is True
    # #1: this path now sends «пробный завершён» itself (claimed once) — not
    # «основная подписка закончилась»; the workers below add nothing
    assert await completed_sent(e2e, u) is True

    await run_workers(e2e)
    await assert_told_once_bypass_kept(e2e, u, mark, limit=500 * MB)


@FLAGS
async def test_row_turned_bypass_only_by_gb_bought_right_after_the_end(e2e, flag):
    u = await trial_user(e2e, flag)
    await end_trial(e2e, u)
    _screen, _p, mark = await buy_bypass(e2e, u, 15, flag=flag)
    sub = await e2e.sub(u.id)
    assert (sub["is_bypass_only"], sub["source"]) == (True, "bypass_only"), sub
    assert await completed_sent(e2e, u) is False

    await run_workers(e2e)
    await assert_told_once_bypass_kept(e2e, u, mark, limit=500 * MB + 15 * GIB)


def count_paid_closes(monkeypatch) -> list:
    """Calls of the worker's «trial ended under a paid subscription» close."""
    calls = []
    real = trial_notifications._close_trial_for_paid

    async def spy(conn, telegram_id, *a, **kw):
        calls.append(telegram_id)
        return await real(conn, telegram_id, *a, **kw)
    monkeypatch.setattr(trial_notifications, "_close_trial_for_paid", spy)
    return calls


async def test_real_paid_subscriber_whose_trial_just_ended_is_skipped(e2e, monkeypatch):
    """Nothing expired, no notice — the trial is marked completed once and the
    worker never selects the user again (production 2026-09-15: ~12 such users
    re-checked and logged on every pass for 24 h)."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=20))
    await e2e.pool.execute(
        "UPDATE users SET trial_used_at = NOW() - interval '73 hours', "
        "trial_expires_at = (NOW() AT TIME ZONE 'UTC') - interval '1 hour', "
        "trial_completed_sent = FALSE WHERE telegram_id=$1", u.id)
    before = await e2e.sub(u.id)
    premium = e2e.panel.premium_expire(u.id)
    closes = count_paid_closes(monkeypatch)
    mark = e2e.tg.mark()

    await run_workers(e2e)

    assert e2e.user_texts(u.id, mark) == []
    assert await completed_sent(e2e, u) is True
    assert closes == [u.id]
    after = await e2e.sub(u.id)
    assert (after["expires_at"], after["source"], after["is_bypass_only"], after["uuid"]) == \
        (before["expires_at"], "payment", False, before["uuid"])
    assert e2e.panel.premium_expire(u.id) == premium and e2e.panel.premium(u.id)["status"] == "ACTIVE"

    await run_workers(e2e)                             # next passes: not selected any more
    assert closes == [u.id]
    assert e2e.user_texts(u.id, mark) == []


async def test_purchase_during_the_trial_is_never_selected_by_the_trial_worker(e2e, monkeypatch):
    """A purchase during the trial already closed and completed it: the worker
    has nothing to do for this user — no re-check, no notice."""
    u = await trial_user(e2e, "on")
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=30))
    await e2e.pool.execute(
        "UPDATE users SET trial_expires_at = (NOW() AT TIME ZONE 'UTC') - interval '1 hour', "
        "trial_completed_sent = TRUE WHERE telegram_id=$1", u.id)
    closes = count_paid_closes(monkeypatch)
    mark = e2e.tg.mark()
    await run_workers(e2e)
    assert closes == [] and e2e.user_texts(u.id, mark) == []


async def test_a_new_trial_starts_with_a_fresh_completed_flag(e2e):
    """A stale trial_completed_sent (e.g. a trial reset by hand) must not hide
    the new trial's end: starting a trial resets the flag."""
    u = new_user()
    await e2e.start_user(u)
    await e2e.pool.execute("UPDATE users SET trial_completed_sent = TRUE WHERE telegram_id=$1", u.id)
    e2e.provisioning("on")
    await e2e.tap(u, "activate_trial")
    await e2e.provisioning_tick()
    assert (await e2e.sub(u.id))["source"] == "trial"
    assert await completed_sent(e2e, u) is False
