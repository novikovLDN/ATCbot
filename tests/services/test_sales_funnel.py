"""Sales funnel (docs/audit/SCOPE.md «Воронка продаж») — hermetic rules.

Schedule = the owner-approved table; MSK send window; MSK daily cap boundary;
latest-due step; stop (claim refused) sends nothing; a step switched off in the
dashboard is skipped; keep-max: the text shows the discount checkout applies
(a bigger active one, with its deadline); a «still valid» step past its
deadline is skipped; worker loop survives errors, CancelledError propagates.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import tests.conftest  # noqa: F401  (env before config)
import database
import database.funnel as funnel_db
from app.i18n import LANGUAGES, get_text
from app.services import automated_notifications as autonotif
from app.services.sales_funnel import service as funnel
from app.utils.telegram_html import telegram_html_errors

UTC = timezone.utc
H, D = timedelta(hours=1), timedelta(days=1)


def msk(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=funnel.MSK).astimezone(UTC)


# ── the approved schedule ────────────────────────────────────────────────

def _table(chain):
    out = []
    for s in funnel.CHAINS[chain]:
        disc = (s.grant_percent, s.grant_for) if s.grant_percent else (
            ("keep", s.keep_percent, s.keep_until) if s.keep_percent else None)
        out.append((s.offset, disc, s.buttons))
    return out


def test_chain_start_schedule():
    assert _table("start") == [
        (1 * H, None, ("trial",)),
        (1 * D, None, ("trial",)),
        (3 * D, (20, 48 * H), ("trial", "buy")),
        (7 * D, (25, 72 * H), ("buy", "trial")),
        (30 * D, (30, 7 * D), ("buy", "trial")),
    ]


def test_chain_trial_schedule():
    assert _table("trial") == [
        (1 * D, ("keep", 30, 7 * D), ("buy",)),   # the −30 % 7 d given at the trial end
        (6 * D, ("keep", 30, 7 * D), ("buy",)),
        (14 * D, (25, 72 * H), ("buy",)),
        (30 * D, (30, 7 * D), ("buy",)),
        (90 * D, (40, 7 * D), ("buy",)),
    ]


def test_chain_paid_schedule():
    assert _table("paid") == [
        (6 * H, ("keep", 15, 72 * H), ("buy",)),  # the −15 % 72 h given at the end
        (1 * D, ("keep", 15, 72 * H), ("buy",)),
        (3 * D, (20, 72 * H), ("buy",)),
        (7 * D, (25, 72 * H), ("buy",)),
        (30 * D, (30, 7 * D), ("buy",)),
        (90 * D, (40, 7 * D), ("buy",)),
    ]


# ── time rules ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour, minute, expected", [
    (9, 59, False), (10, 0, True), (15, 30, True), (20, 59, True), (21, 0, False), (2, 0, False),
])
def test_send_window_is_10_to_21_msk(hour, minute, expected):
    assert funnel.in_send_window(msk(2026, 9, 14, hour, minute)) is expected


def test_daily_cap_counts_the_moscow_calendar_day():
    # 01:30 MSK on the 15th is 22:30 UTC on the 14th: the day began at 21:00 UTC on the 14th
    assert funnel.msk_day_start(msk(2026, 9, 15, 1, 30)) == datetime(2026, 9, 14, 21, 0, tzinfo=UTC)
    assert funnel.msk_day_start(msk(2026, 9, 15, 20, 59)) == datetime(2026, 9, 14, 21, 0, tzinfo=UTC)


def test_latest_due_step_wins_and_earlier_ones_are_skipped():
    a = datetime(2026, 9, 1, tzinfo=UTC)
    assert funnel.due_step("start", a, a + 59 * timedelta(minutes=1)) is None
    assert funnel.due_step("start", a, a + H).name == "1h"
    assert funnel.due_step("start", a, a + 8 * D).name == "7d"
    step = funnel.due_step("paid", a, a + 4 * D)
    assert step.name == "3d" and funnel.earlier_steps(step) == ("6h", "1d")


def test_lookback_is_the_cutoff_or_the_last_step_plus_grace():
    now = datetime(2026, 11, 1, tzinfo=UTC)
    cutoff = datetime(2026, 9, 14, tzinfo=UTC)
    assert funnel.lookback_start("start", cutoff, now) == now - 30 * D - funnel.LOOKBACK_GRACE
    assert funnel.lookback_start("paid", cutoff, now) == cutoff   # 90 d + grace reaches before the cut-off


@pytest.mark.parametrize("days, ru, en", [
    (1, "1 день", "1 day"), (2, "2 дня", "2 days"), (5, "5 дней", "5 days"), (6, "6 дней", "6 days"),
    (11, "11 дней", "11 days"), (21, "21 день", "21 days"), (22, "22 дня", "22 days"),
])
def test_days_left(days, ru, en):
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    deadline = now + days * D + timedelta(minutes=5)
    assert funnel.days_left_text("ru", deadline, now) == ru
    assert funnel.days_left_text("en", deadline, now) == en


# ── texts / buttons / dashboard ──────────────────────────────────────────

def test_every_step_has_ru_en_text_valid_html_and_a_dashboard_entry():
    params = {"percent": 25, "deadline": "17.09.2026 14:30 МСК", "days_left": "2 дня"}
    for step in funnel.ALL_STEPS:
        for lang in ("ru", "en"):
            text = LANGUAGES[lang][step.key]
            assert ("{percent}" in text) == step.has_discount, (lang, step.key)
            rendered = text.format(**params)
            assert telegram_html_errors(rendered) == [], (lang, step.key)
            if step.has_discount:
                assert "25%" in rendered and "17.09.2026 14:30 МСК" in rendered
        spec = autonotif.REGISTRY[step.key]
        assert spec.default_text_ru == LANGUAGES["ru"][step.key]
        assert spec.category == "reminder"
    assert sorted(k for k in autonotif.REGISTRY if k.startswith("funnel.")) == sorted(s.key for s in funnel.ALL_STEPS)


def test_buttons():
    kb = funnel.keyboard("ru", funnel.get_step("start", "1h"))
    assert [[b.callback_data for b in r] for r in kb.inline_keyboard] == [["activate_trial"]]
    kb = funnel.keyboard("ru", funnel.get_step("start", "3d"))
    assert [[b.callback_data for b in r] for r in kb.inline_keyboard] == [["activate_trial"], ["funnel_buy:start"]]
    kb = funnel.keyboard("ru", funnel.get_step("paid", "3d"))
    (btn,), = kb.inline_keyboard
    assert (btn.callback_data, btn.text) == ("funnel_buy:paid", get_text("ru", "funnel.btn_renew_discount"))
    (btn,), = funnel.keyboard("en", funnel.get_step("trial", "14d")).inline_keyboard
    assert (btn.callback_data, btn.text) == ("funnel_buy:trial", "🔥 Buy with discount")


# ── one candidate: claim → discount → send → record ─────────────────────

NOW = msk(2026, 9, 20, 12)
ANCHOR = NOW - 3 * D - H


class Env:
    def __init__(self, monkeypatch, *, claim=(1, "claimed"), enabled=True, personal=None, special=None,
                 send_ok=True):
        self.sent, self.finished, self.granted, self.logged, self.claims = [], [], [], [], []
        self.personal, self.special = personal, special

        async def fake_claim(*args, **kwargs):
            self.claims.append((args, kwargs))
            return claim

        async def fake_finish(claim_id, status, percent=None, expires=None):
            self.finished.append((claim_id, status, percent, expires))

        async def fake_create(**kw):
            self.granted.append(kw)
            cur = self.personal
            if cur is None or cur["discount_percent"] < kw["discount_percent"]:
                self.personal = {"discount_percent": kw["discount_percent"], "expires_at": kw["expires_at"]}
            return True

        async def fake_send(bot, tg, text, **kw):
            self.sent.append((tg, text, kw.get("reply_markup")))
            return SimpleNamespace(message_id=1) if send_ok else None

        async def fake_enabled(key):
            return enabled

        async def fake_text(key, *, params=None, language=None):
            return None   # i18n text

        async def fake_log(key, tg, *, status="sent", error=None):
            self.logged.append((key, status))

        async def fake_lang(tg):
            return "ru"

        async def fake_personal(tg):
            return self.personal

        async def fake_special(tg):
            return self.special

        from app.services import language_service
        from app.utils import telegram_safe
        monkeypatch.setattr(funnel_db, "claim", fake_claim)
        monkeypatch.setattr(funnel_db, "finish", fake_finish)
        monkeypatch.setattr(database, "create_user_discount", fake_create, raising=False)
        monkeypatch.setattr(database, "get_user_discount", fake_personal, raising=False)
        monkeypatch.setattr(database, "get_special_offer_info", fake_special, raising=False)
        monkeypatch.setattr(telegram_safe, "safe_send_message", fake_send)
        monkeypatch.setattr(autonotif, "is_notification_enabled", fake_enabled)
        monkeypatch.setattr(autonotif, "get_notification_text", fake_text)
        monkeypatch.setattr(autonotif, "log_notification_send", fake_log)
        monkeypatch.setattr(language_service, "resolve_user_language", fake_lang)


async def _process(chain="start", step="3d", anchor=ANCHOR):
    return await funnel.process_candidate(object(), chain, 42, anchor, step, now=NOW, lower=ANCHOR - D)


async def test_new_discount_is_granted_and_shown_with_its_msk_deadline(monkeypatch):
    env = Env(monkeypatch)
    assert await _process() == "sent"
    (grant,) = env.granted
    assert (grant["discount_percent"], grant["expires_at"], grant["keep_max"]) == (20, NOW + 48 * H, True)
    (_, text, markup), = env.sent
    assert "20%" in text and "22.09.2026 12:00 МСК" in text
    assert env.finished == [(1, "sent", 20, NOW + 48 * H)]
    assert env.logged == [("funnel.start_3d", "sent")]
    # the claim carries the stop check inputs: earlier steps skipped, MSK day, 6 h gap
    (args, kw), = env.claims
    assert args[:5] == (42, "start", ANCHOR, "3d", ("1h", "1d"))
    assert kw["day_start"] == funnel.msk_day_start(NOW) and kw["other_since"] == NOW - 6 * H


async def test_keep_max_a_bigger_active_discount_is_what_the_text_shows(monkeypatch):
    bigger_until = NOW + 5 * H
    env = Env(monkeypatch, personal={"discount_percent": 40, "expires_at": bigger_until})
    assert await _process() == "sent"
    (_, text, _), = env.sent
    assert "40%" in text and "20%" not in text and "20.09.2026 17:00 МСК" in text
    assert env.personal == {"discount_percent": 40, "expires_at": bigger_until}
    assert env.finished == [(1, "sent", 40, bigger_until)]


async def test_a_bigger_permanent_admin_discount_stays_and_nothing_is_granted(monkeypatch):
    env = Env(monkeypatch, personal={"discount_percent": 30, "expires_at": None})
    assert await _process() == "sent"
    assert env.granted == []                     # never touched: no write at all
    assert env.personal == {"discount_percent": 30, "expires_at": None}
    (_, text, _), = env.sent
    assert "30%" in text and "20%" not in text


@pytest.mark.parametrize("chain, step, anchor", [
    ("start", "3d", ANCHOR),                     # grant −20 %
    ("trial", "1d", NOW - D - H),                # keep −30 %
])
async def test_a_smaller_permanent_admin_discount_is_never_overwritten(monkeypatch, chain, step, anchor):
    """The funnel's keep_max upsert replaced a smaller PERMANENT admin discount
    with a 48 h one — after it ended the user had 0 % instead of −10 % forever.
    The step is skipped instead: nothing granted, nothing sent."""
    env = Env(monkeypatch, personal={"discount_percent": 10, "expires_at": None})
    assert await _process(chain, step, anchor) == "no_discount"
    assert env.granted == [] and env.sent == []
    assert env.personal == {"discount_percent": 10, "expires_at": None}
    assert env.finished == [(1, "skipped", None, None)]


async def test_special_offer_wins_a_tie_like_checkout(monkeypatch):
    offer_until = NOW + 60 * H
    env = Env(monkeypatch, special={"discount_percent": 15, "expires_at": offer_until})
    anchor = NOW - 6 * H - timedelta(minutes=3)
    assert await _process("paid", "6h", anchor) == "sent"
    assert env.granted == []                     # the −15 % window is there: nothing new
    (_, text, _), = env.sent
    assert "15%" in text and funnel.format_deadline("ru", offer_until) in text


async def test_paid_still_valid_step_never_opens_a_second_minus15_window(monkeypatch):
    """Paid +6 h / +1 d show the period's ONE −15 % window (owner: one window of
    72 h per period). Its 72 h ran out → skip; a personal 15 % would be a second."""
    import database.subscriptions as db_subs
    env = Env(monkeypatch)
    claims = []

    async def window_over(tg, period_end):
        claims.append((tg, period_end))
        return None
    monkeypatch.setattr(db_subs, "claim_special_offer", window_over)
    anchor = NOW - D - H
    assert await _process("paid", "1d", anchor) == "no_discount"
    assert env.granted == [] and env.sent == []
    assert claims == [(42, anchor)], "the window of THIS period (it ended at the anchor)"


async def test_paid_step_opens_the_periods_only_window_when_none_was_offered(monkeypatch):
    import database.subscriptions as db_subs
    env = Env(monkeypatch)
    until = NOW + 72 * H

    async def opened(tg, period_end):
        env.special = {"discount_percent": 15, "expires_at": until}
        return env.special
    monkeypatch.setattr(db_subs, "claim_special_offer", opened)
    assert await _process("paid", "6h", NOW - 6 * H - timedelta(minutes=3)) == "sent"
    assert env.granted == []
    (_, text, _), = env.sent
    assert "15%" in text and funnel.format_deadline("ru", until) in text


async def test_still_valid_reminder_grants_the_promised_discount_with_the_fixed_deadline(monkeypatch):
    env = Env(monkeypatch)                       # the trial-end −30 % never arrived
    anchor = NOW - D - H
    assert await _process("trial", "1d", anchor) == "sent"
    (grant,) = env.granted
    assert (grant["discount_percent"], grant["expires_at"]) == (30, anchor + 7 * D)
    (_, text, _), = env.sent
    assert "30%" in text and "5 дней" in text


async def test_still_valid_reminder_after_its_deadline_is_skipped(monkeypatch):
    env = Env(monkeypatch)
    anchor = NOW - 7 * D + H                     # the 30 % ends within the hour
    assert await _process("trial", "6d", anchor) == "no_discount"
    assert env.sent == [] and env.granted == []
    assert env.finished == [(1, "skipped", None, None)]


async def test_stopped_chain_sends_nothing(monkeypatch):
    env = Env(monkeypatch, claim=(None, "stopped"))
    assert await _process() == "stopped"
    assert env.sent == env.granted == env.finished == []


async def test_step_switched_off_in_the_dashboard_is_skipped(monkeypatch):
    env = Env(monkeypatch, enabled=False)
    assert await _process() == "disabled"
    assert env.sent == env.granted == []
    assert env.finished == [(1, "skipped", None, None)]
    assert env.logged == [("funnel.start_3d", "skipped_disabled")]


async def test_telegram_refusal_is_recorded_failed(monkeypatch):
    env = Env(monkeypatch, send_ok=False)
    assert await _process("start", "1h", NOW - 2 * H) == "failed"
    assert env.finished == [(1, "failed", None, None)] and env.logged == [("funnel.start_1h", "failed")]


@pytest.mark.parametrize("delivered", [True, False])
async def test_the_expiry_notice_is_recorded_for_the_6h_gap(monkeypatch, delivered):
    """The claim's «no funnel message within 6 h of another notification» reads
    automated_notification_sends; «subscription ended» recorded nothing there."""
    from app.services import language_service
    from app.services.notifications import special_offer
    from app.utils import telegram_safe
    logged = []

    async def fake_log(key, tg, *, status="sent", error=None):
        logged.append((key, tg, status))

    async def fake_notice(language, tg, **kw):
        return "ended", None

    async def fake_send(bot, tg, text, **kw):
        return SimpleNamespace(message_id=1) if delivered else None

    async def fake_lang(tg):
        return "ru"
    monkeypatch.setattr(autonotif, "log_notification_send", fake_log)
    monkeypatch.setattr(special_offer, "expired_notice", fake_notice)
    monkeypatch.setattr(telegram_safe, "safe_send_message", fake_send)
    monkeypatch.setattr(language_service, "resolve_user_language", fake_lang)
    assert await special_offer.notify_expired(object(), 42, has_bypass=False) is delivered
    assert logged == ([("subscription.expired", 42, "sent")] if delivered else [])


# ── pass / worker ────────────────────────────────────────────────────────

async def test_outside_the_window_only_the_cutoff_is_fixed(monkeypatch):
    calls = []

    async def fake_started(now):
        calls.append("cutoff")
        return now

    async def fake_due(*a, **k):
        calls.append("due")
        return []
    monkeypatch.setattr(funnel_db, "get_or_init_started_at", fake_started)
    monkeypatch.setattr(funnel_db, "fetch_due", fake_due)
    assert await funnel.run_pass(object(), now=msk(2026, 9, 14, 22)) == {"outside_window": 1}
    assert calls == ["cutoff"]
    await funnel.run_pass(object(), now=msk(2026, 9, 14, 12))
    assert calls == ["cutoff", "cutoff", "due", "due", "due"]


async def test_the_window_is_rechecked_before_every_send(monkeypatch):
    """The 10–21 MSK window was checked once per pass; a pass lasts up to 240 s,
    so sends at 21:0x happened. Now it is checked before every candidate."""
    async def fake_started(now):
        return now

    async def fake_due(chain, **k):
        return ([{"telegram_id": i, "anchor_at": NOW - 2 * H, "step": "1h"} for i in (1, 2, 3)]
                if chain == "start" else [])
    processed = []

    async def fake_process(bot, chain, tg, anchor, step, **kw):
        processed.append(tg)
        return "sent"
    answers = iter([True, True, False])          # pass start, 1st candidate, then 21:00
    monkeypatch.setattr(funnel, "in_send_window", lambda now: next(answers, False))
    monkeypatch.setattr(funnel_db, "get_or_init_started_at", fake_started)
    monkeypatch.setattr(funnel_db, "fetch_due", fake_due)
    monkeypatch.setattr(funnel, "process_candidate", fake_process)
    monkeypatch.setattr(funnel, "_new_pacer", lambda: None)
    counts = await funnel.run_pass(object(), now=msk(2026, 9, 14, 20, 59))
    assert processed == [1]
    assert counts == {"sent": 1, "outside_window": 1}


async def test_worker_survives_errors_and_propagates_cancel(monkeypatch):
    from app.workers import sales_funnel as worker
    passes, sleeps = [], []

    async def bad_pass(bot):
        passes.append(1)
        raise RuntimeError("db down")

    async def fake_sleep(delay):
        sleeps.append(delay)
        if len(sleeps) > 3:
            raise asyncio.CancelledError()
    monkeypatch.setattr(worker.service, "run_pass", bad_pass)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(database.core, "DB_READY", True)
    with pytest.raises(asyncio.CancelledError):
        await worker.sales_funnel_task(object())
    assert len(passes) == 3 and sleeps[1:] == [worker.INTERVAL_SECONDS] * 3


# ── old-base segments (one dashboard campaign) ──────────────────────────

async def test_old_base_segments_route_to_sql_bounded_by_the_funnel_start(monkeypatch):
    from database import admin as db_admin
    captured = []

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def fetch(self, query, *args):
            if "is_reachable = FALSE" in query:
                return []
            captured.append(query)
            return [{"telegram_id": 7}]

    class _Pool:
        def acquire(self):
            return _Conn()

    async def get_pool():
        return _Pool()
    monkeypatch.setattr(db_admin, "get_pool", get_pool)
    for seg in ("funnel_start_no_trial", "funnel_trial_ended_no_purchase", "funnel_paid_ended_no_renewal"):
        assert await db_admin.get_users_by_segment(seg) == [7]
    assert len(captured) == 3
    assert all("sales_funnel_started_at" in q for q in captured)
    assert "trial_used_at IS NULL" in captured[0]
    assert "is_bypass_only" in captured[1] and "is_bypass_only" in captured[2]

    from app.api.dashboard.routes import broadcasts
    broadcasts.reset_segment_counts_cache()
    monkeypatch.setattr(broadcasts.database, "get_users_by_segment", lambda key: _const([1, 2]))
    listed = {s["key"]: s for s in await broadcasts.segments_list()}
    for seg in ("funnel_start_no_trial", "funnel_trial_ended_no_purchase", "funnel_paid_ended_no_renewal"):
        assert listed[seg]["count"] == 2 and listed[seg]["group"] == "Воронка — старая база"


async def _const(v):
    return v
