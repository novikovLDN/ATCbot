"""Scenario 22 — the sales funnel end to end (docs/audit/SCOPE.md «Воронка продаж»).

Real bot + real Postgres. Time travel = every timestamp of the user (and the
funnel cut-off) moves back by Δ, the same as the clock moving forward by Δ: the
code under test keeps using the real clock. The MSK send window is forced open
except where it is the subject.

* chain start: +1 h / +1 d trial messages, +3 d −20 % 48 h (checkout price),
  the button does not extend the discount, +7 d −25 %, a purchase stops it;
* chain trial: the existing «trial ended −30 %» first, then +1 d and +6 d show
  that same −30 % with its deadline, +14 d −25 %, a purchase stops it;
* chain paid: the expiry worker's −15 % first, then +6 h / +1 d show it, +3 d
  −20 % (checkout price), a renewal stops it;
* no retro-sends for events before the cut-off; an overlapping pass / a crash
  after the claim never duplicates; ≤ 1 per MSK day; window closed → nothing;
  unreachable users skipped; a step switched off in the dashboard is skipped;
  old-base segments hold exactly the pre-cutoff users.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

import database
import fast_expiry_cleanup
from app.services import automated_notifications as autonotif
from app.services.automated_notifications import helper as autonotif_helper
from app.services.sales_funnel import service as funnel
from database import funnel as funnel_db
from tests.e2e import flows
from tests.e2e.test_21_trial_end_bypass_only import end_trial, run_workers, trial_user
from tests.e2e.world import aware, naive, new_user, utcnow

H, D, M = timedelta(hours=1), timedelta(days=1), timedelta(minutes=1)

_SHIFT = [
    ("users", ["created_at", "trial_used_at", "trial_expires_at", "special_offer_created_at",
               "captcha_passed_at"]),
    ("subscriptions", ["expires_at", "activated_at"]),
    ("subscription_history", ["start_date", "end_date", "created_at"]),
    ("payments", ["created_at", "paid_at"]),
    ("user_discounts", ["expires_at", "created_at"]),
    ("funnel_messages", ["anchor_at", "sent_at", "discount_expires_at"]),
    ("automated_notification_sends", ["sent_at"]),
]


async def travel(e2e, delta: timedelta, *users) -> None:
    """The clock moves forward by `delta` for these users (and the cut-off)."""
    for u in users:
        for table, cols in _SHIFT:
            sets = ", ".join(f"{c} = {c} - $2::interval" for c in cols)
            await e2e.pool.execute(f"UPDATE {table} SET {sets} WHERE telegram_id = $1", u.id, delta)  # noqa: S608
    cutoff = await e2e.val("SELECT value FROM app_settings WHERE key = $1", funnel_db.STARTED_AT_KEY)
    if cutoff:
        moved = datetime.fromisoformat(cutoff) - delta
        await e2e.pool.execute("UPDATE app_settings SET value = $2 WHERE key = $1",
                               funnel_db.STARTED_AT_KEY, moved.isoformat())


@pytest.fixture
def window(e2e):
    state = {"open": True}
    e2e.mp.setattr(funnel, "in_send_window", lambda now: state["open"])
    autonotif_helper.touch_cache()
    yield state
    autonotif_helper.touch_cache()


async def run(e2e):
    counts = await funnel.run_pass(e2e.bot)
    await e2e.settle()
    return counts


async def go_live(e2e):
    """The funnel's first pass fixes the cut-off at «now»; the test then says
    it was deployed 10 minutes ago (the seeded events below happen «now»)."""
    await run(e2e)
    cutoff = await funnel_db.get_or_init_started_at(utcnow())
    assert utcnow() - cutoff < timedelta(seconds=30)
    await e2e.pool.execute("UPDATE app_settings SET value = $2 WHERE key = $1",
                           funnel_db.STARTED_AT_KEY, (cutoff - 10 * M).isoformat())


async def rows(e2e, u):
    return await e2e.rows(
        "SELECT chain, step, status, discount_percent, discount_expires_at FROM funnel_messages "
        "WHERE telegram_id=$1 ORDER BY id", u.id)


async def sent_steps(e2e, u):
    return [(r["chain"], r["step"]) for r in await rows(e2e, u) if r["status"] == "sent"]


async def discount(e2e, u):
    r = await e2e.row("SELECT discount_percent, expires_at FROM user_discounts WHERE telegram_id=$1", u.id)
    return (r["discount_percent"], aware(r["expires_at"])) if r else None


def only_message(e2e, u, mark):
    msgs = [s for s in e2e.tg.since(mark, u.id) if s.method == "SendMessage"]
    assert len(msgs) == 1, [m.text for m in msgs]
    return msgs[0]


# ── chain 1: /start without trial and subscription ──────────────────────

async def test_chain_start_end_to_end(e2e, window):
    await go_live(e2e)
    u = new_user()
    await e2e.start_user(u)

    mark = e2e.tg.mark()
    await run(e2e)
    assert e2e.tg.since(mark, u.id) == [] and await rows(e2e, u) == []   # not +1 h yet

    await travel(e2e, H + M, u)
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert msg.text.startswith("👋 <b>Вы в одном шаге") and msg.callback_data() == ["activate_trial"]
    mark = e2e.tg.mark()
    await run(e2e)                                                          # the same step never twice
    assert e2e.tg.since(mark, u.id) == []

    await travel(e2e, D, u)
    mark = e2e.tg.mark()
    await run(e2e)
    assert "Что даёт Atlas Secure" in only_message(e2e, u, mark).text

    await travel(e2e, 2 * D, u)                                             # +3 d: trial or −20 % for 48 h
    mark = e2e.tg.mark()
    before = utcnow()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    pct, until = await discount(e2e, u)
    assert pct == 20 and before + 48 * H - M <= until <= utcnow() + 48 * H
    assert "20%" in msg.text and funnel.format_deadline("ru", until) in msg.text
    assert msg.callback_data() == ["activate_trial", "funnel_buy:start"]

    # the button opens the purchase screen and never extends the discount
    mark = e2e.tg.mark()
    await e2e.tap(u, "funnel_buy:start")
    texts = e2e.user_texts(u.id, mark)
    assert any("20%" in t and funnel.format_deadline("ru", until) in t for t in texts), texts
    assert e2e.tg.with_button(u.id, "tariff:basic") is not None
    await e2e.tap(u, "funnel_buy:start")
    assert await discount(e2e, u) == (20, until)

    # the discount in the text = what checkout charges
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert pending["price_kopecks"] == 19900 - 19900 * 20 // 100

    await travel(e2e, 4 * D, u)                                             # +7 d: −25 % for 72 h
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert (await discount(e2e, u))[0] == 25 and "25%" in msg.text

    # a purchase stops the chain
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    res = await flows.pay(e2e, u, pending, "sbp")
    assert res.status == 200
    await travel(e2e, 24 * D, u)
    mark = e2e.tg.mark()
    await run(e2e)
    assert [s for s in e2e.tg.since(mark, u.id) if "Скидка" in (s.text or "")] == []
    assert await sent_steps(e2e, u) == [("start", "1h"), ("start", "1d"), ("start", "3d"), ("start", "7d")]


async def test_a_permanent_admin_discount_survives_the_chain(e2e, window):
    """A smaller PERMANENT admin discount is never replaced by a funnel one
    (−10 % forever would become −20 % for 48 h, then 0 %): the step is skipped."""
    await go_live(e2e)
    u = new_user()
    await e2e.start_user(u)
    assert await database.create_user_discount(u.id, 10, None, 1)          # admin, no expiry
    await travel(e2e, 3 * D + M, u)                                         # +3 d: −20 % 48 h is due
    mark = e2e.tg.mark()
    await run(e2e)
    assert [s for s in e2e.tg.since(mark, u.id) if s.method == "SendMessage"] == []
    assert await discount(e2e, u) == (10, None)
    assert [(r["step"], r["status"]) for r in await rows(e2e, u)] == [("1h", "skipped"), ("1d", "skipped"),
                                                                     ("3d", "skipped")]


async def test_purchase_between_batch_and_send_stops_the_chain(e2e, window):
    await go_live(e2e)
    u = new_user()
    await e2e.start_user(u)
    await travel(e2e, H + M, u)
    now = utcnow()
    cutoff = await funnel_db.get_or_init_started_at(now)
    lower = funnel.lookback_start("start", cutoff, now)
    due = await funnel_db.fetch_due("start", now=now, lower=lower,
                                    steps=[(s.name, s.offset.total_seconds()) for s in funnel.CHAINS["start"]],
                                    day_start=funnel.msk_day_start(now), limit=10)
    (cand,) = [c for c in due if c["telegram_id"] == u.id]
    await e2e.tap(u, "activate_trial")                                      # the trial starts meanwhile
    mark = e2e.tg.mark()
    outcome = await funnel.process_candidate(e2e.bot, "start", u.id, cand["anchor_at"], cand["step"],
                                             now=now, lower=lower)
    assert outcome == "stopped" and await rows(e2e, u) == []
    assert not [s for s in e2e.tg.since(mark, u.id) if "Вы в одном шаге" in (s.text or "")]


# ── chain 2: the trial ended, no purchase ───────────────────────────────

async def test_chain_trial_end_to_end(e2e, window):
    await go_live(e2e)
    u = await trial_user(e2e, "off")
    await end_trial(e2e, u)
    await run_workers(e2e)                                                  # existing: «trial ended», −30 % 7 d
    pct, until = await discount(e2e, u)
    assert pct == 30

    await travel(e2e, D, u)
    until -= D
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert "30%" in msg.text and funnel.format_deadline("ru", until) in msg.text and "5 дней" in msg.text
    assert msg.callback_data() == ["funnel_buy:trial"]
    assert await discount(e2e, u) == (30, until)                            # same discount, same deadline

    await travel(e2e, 5 * D, u)
    until -= 5 * D
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert "скоро сгорит" in msg.text and funnel.format_deadline("ru", until) in msg.text
    assert await discount(e2e, u) == (30, until)

    await travel(e2e, 8 * D, u)                                             # +14 d: −25 % 72 h
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert (await discount(e2e, u))[0] == 25 and "25%" in msg.text
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert pending["price_kopecks"] == 19900 - 19900 * 25 // 100
    assert (await flows.pay(e2e, u, pending, "sbp")).status == 200

    await travel(e2e, 16 * D, u)
    mark = e2e.tg.mark()
    await run(e2e)
    assert await sent_steps(e2e, u) == [("trial", "1d"), ("trial", "6d"), ("trial", "14d")]


# ── chain 3: a paid subscription ended, not renewed ─────────────────────

async def paid_ended(e2e):
    """A paid subscriber whose subscription ended a minute ago (history = DB end),
    expired by the real worker (its «subscription ended −15 %» message)."""
    u = new_user()
    await flows.seed_active(e2e, u, "basic", utcnow() + timedelta(days=2))
    end = utcnow() - M
    await e2e.set_expiry(u.id, end)
    await e2e.pool.execute("UPDATE subscription_history SET end_date=$2 WHERE telegram_id=$1", u.id, naive(end))
    ent = e2e.panel.premium(u.id)
    if ent is not None:
        ent["status"] = "EXPIRED"
    await e2e.run_worker_iterations(fast_expiry_cleanup, fast_expiry_cleanup.fast_expiry_cleanup_task)
    offer = await database.get_special_offer_info(u.id)
    assert offer is not None and offer["discount_percent"] == 15
    return u, offer["expires_at"]


async def test_chain_paid_end_to_end(e2e, window):
    await go_live(e2e)
    u, offer_until = await paid_ended(e2e)
    mark = e2e.tg.mark()
    await run(e2e)
    assert await rows(e2e, u) == [] and e2e.tg.since(mark, u.id) == []

    await travel(e2e, 6 * H + M, u)
    offer_until -= 6 * H + M
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert "15%" in msg.text and funnel.format_deadline("ru", offer_until) in msg.text
    assert msg.callback_data() == ["funnel_buy:paid"]
    assert await discount(e2e, u) is None                                   # the −15 % window, nothing new

    await travel(e2e, D, u)
    mark = e2e.tg.mark()
    await run(e2e)
    assert "Скидка 15% на продление" in only_message(e2e, u, mark).text

    await travel(e2e, 2 * D, u)                                             # +3 d: −20 % 72 h
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert (await discount(e2e, u))[0] == 20 and "20%" in msg.text
    pending = await flows.buy(e2e, u, "basic", 30, "sbp")
    assert pending["price_kopecks"] == 19900 - 19900 * 20 // 100
    assert (await flows.pay(e2e, u, pending, "sbp")).status == 200          # renewed → the chain stops

    await travel(e2e, 5 * D, u)
    await run(e2e)
    assert await sent_steps(e2e, u) == [("paid", "6h"), ("paid", "1d"), ("paid", "3d")]


# ── global rules ─────────────────────────────────────────────────────────

async def test_no_retro_sends_for_events_before_the_cutoff(e2e, window):
    old_start, old_trial = new_user(), None
    await e2e.start_user(old_start)
    old_trial = await trial_user(e2e, "off")
    await end_trial(e2e, old_trial)
    await run_workers(e2e)
    old_paid, _ = await paid_ended(e2e)
    olds = (old_start, old_trial, old_paid)
    for u in olds:                                                          # all three events: 2 days ago
        for table, cols in _SHIFT:
            sets = ", ".join(f"{c} = {c} - interval '2 days'" for c in cols)
            await e2e.pool.execute(f"UPDATE {table} SET {sets} WHERE telegram_id=$1", u.id)  # noqa: S608

    await go_live(e2e)                                                      # the funnel is deployed now
    for _ in range(3):
        await travel(e2e, 2 * D, *olds)
        await run(e2e)
    for u in olds:
        assert await rows(e2e, u) == []

    # …they are the old base: one dashboard campaign by segments
    seg = {s: set(await database.get_users_by_segment(s)) for s in (
        "funnel_start_no_trial", "funnel_trial_ended_no_purchase", "funnel_paid_ended_no_renewal")}
    assert old_start.id in seg["funnel_start_no_trial"]
    assert old_trial.id in seg["funnel_trial_ended_no_purchase"]
    assert old_paid.id in seg["funnel_paid_ended_no_renewal"]
    new = new_user()
    await e2e.start_user(new)                                               # after the cut-off: the funnel's
    assert new.id not in set(await database.get_users_by_segment("funnel_start_no_trial"))


async def test_overlapping_passes_and_a_crash_after_the_claim_never_duplicate(e2e, window):
    await go_live(e2e)
    a, b = new_user(), new_user()
    await e2e.start_user(a)
    await e2e.start_user(b)
    await travel(e2e, H + M, a, b)
    mark = e2e.tg.mark()
    await asyncio.gather(funnel.run_pass(e2e.bot), funnel.run_pass(e2e.bot))
    await e2e.settle()
    assert len([s for s in e2e.tg.since(mark, a.id) if s.method == "SendMessage"]) == 1
    assert len([s for s in e2e.tg.since(mark, b.id) if s.method == "SendMessage"]) == 1

    # a restart between the claim and the send: the claimed row stays, no resend
    c = new_user()
    await e2e.start_user(c)
    await travel(e2e, H + M, c)
    anchor = await e2e.val("SELECT created_at FROM users WHERE telegram_id=$1", c.id)
    await e2e.pool.execute(
        "INSERT INTO funnel_messages (telegram_id, chain, anchor_at, step, status, sent_at) "
        "VALUES ($1, 'start', $2, '1h', 'claimed', NOW() - interval '1 day')", c.id, anchor)
    mark = e2e.tg.mark()
    await run(e2e)
    assert e2e.tg.since(mark, c.id) == []


async def test_one_per_moscow_day_window_unreachable_and_switched_off(e2e, window):
    await go_live(e2e)
    u = new_user()
    await e2e.start_user(u)
    await travel(e2e, H + M, u)

    # window closed → nothing, not even a claim
    window["open"] = False
    await run(e2e)
    assert await rows(e2e, u) == []
    window["open"] = True

    # another funnel message today → wait for tomorrow (MSK)
    await e2e.pool.execute(
        "INSERT INTO funnel_messages (telegram_id, chain, anchor_at, step, status, sent_at) "
        "VALUES ($1, 'paid', NOW() - interval '30 days', '30d', 'sent', NOW())", u.id)
    await run(e2e)
    assert await sent_steps(e2e, u) == [("paid", "30d")]
    await e2e.pool.execute("UPDATE funnel_messages SET sent_at=$2 WHERE telegram_id=$1 AND chain='paid'",
                           u.id, funnel.msk_day_start(utcnow()) - M)
    # …and another lifecycle notification in the last 6 h also waits
    await autonotif.log_notification_send("trial.reminder_24h", u.id, status="sent")
    await run(e2e)
    assert await sent_steps(e2e, u) == [("paid", "30d")]
    await e2e.pool.execute("UPDATE automated_notification_sends SET sent_at = sent_at - interval '7 hours' "
                           "WHERE telegram_id=$1", u.id)
    await run(e2e)
    assert await sent_steps(e2e, u) == [("paid", "30d"), ("start", "1h")]

    # unreachable → skipped
    v = new_user()
    await e2e.start_user(v)
    await e2e.pool.execute("UPDATE users SET is_reachable = FALSE WHERE telegram_id=$1", v.id)
    await travel(e2e, H + M, v)
    await run(e2e)
    assert await rows(e2e, v) == []

    # a step switched off in the dashboard is skipped, the chain goes on
    await autonotif.sync_registry_to_db()
    assert await autonotif_helper.update_notification("funnel.start_1d", is_enabled=False)
    await travel(e2e, D, u)
    mark = e2e.tg.mark()
    await run(e2e)
    assert e2e.tg.since(mark, u.id) == []
    assert [(r["step"], r["status"]) for r in await rows(e2e, u) if r["chain"] == "start"] == \
        [("1h", "sent"), ("1d", "skipped")]
    await travel(e2e, 2 * D, u)
    await run(e2e)
    assert ("start", "3d") in await sent_steps(e2e, u)


async def test_english_user_gets_english_text(e2e, window):
    await go_live(e2e)
    u = new_user(lang="en")
    await e2e.start_user(u)
    await e2e.pool.execute("UPDATE users SET language='en' WHERE telegram_id=$1", u.id)
    await travel(e2e, 3 * D + M, u)
    mark = e2e.tg.mark()
    await run(e2e)
    msg = only_message(e2e, u, mark)
    assert msg.text.startswith("🎁 <b>Two ways to start</b>") and "20% off" in msg.text
    assert "Moscow time" in msg.text
