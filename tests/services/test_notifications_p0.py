"""P0 notification fixes N-01…N-07 (docs/notifications/bugs-and-risks.md).

Hermetic: fake asyncpg connections, no Postgres, no HTTP, no Telegram.
Each test names the bug it pins; all of them fail on the pre-fix code.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
import database.subscriptions as db_subs
from app import i18n
from app.services.notifications import service as notification_service
from tests.services import payment_core_harness as h
from tests.services.test_grant_access_deferred import (
    PRE_PROVISIONED,
    TG,
    Recorder,
    SubsConn,
    _FrozenDatetime,
    active_row,
    expired_row,
    strict_no_panel,
)


def _norm(sql: str) -> str:
    return " ".join(sql.split()).lower()


# ═══════════════════════════════════════════════════════════════════════
# grant_access helpers (N-01, N-04, N-07)
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def frozen(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(db_subs, "datetime", _FrozenDatetime)
    monkeypatch.setattr(config, "VPN_ENABLED", True)
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(db_subs, "_notify_watchdog_expires_at", r.watchdog_hook)
    monkeypatch.setattr(db_subs, "_log_vpn_lifecycle_audit_async", r.audit_hook)
    strict_no_panel(monkeypatch, r)  # defer_panel=True: zero panel / HTTP calls
    return r


async def _grant(row, **kw):
    conn = SubsConn(row, in_tx=True)
    kw.setdefault("source", "payment")
    result = await db_subs.grant_access(
        telegram_id=TG, duration=timedelta(days=30), conn=conn,
        _caller_holds_transaction=True, defer_panel=True, **kw,
    )
    return result, conn


def _sub_writes(conn):
    """(sql, args) of every INSERT/UPDATE on subscriptions."""
    return [
        (s, a) for kind, s, a in conn.calls
        if kind == "execute" and (s.startswith("update subscriptions") or s.startswith("insert into subscriptions ("))
    ]


def _row(tariff="basic", **extra):
    row = active_row(tariff=tariff)
    row.update(extra)
    return row


GRANT_SCENARIOS = {
    "renewal_same_tariff": (lambda: _row("basic"), {"tariff": "basic"}),
    "renewal_basic_to_plus": (lambda: _row("basic"), {"tariff": "plus"}),
    "renewal_plus_to_basic": (lambda: _row("plus"), {"tariff": "basic"}),
    "new_issuance_pending": (expired_row, {}),
    "new_issuance_preprovisioned": (lambda: None, {"pre_provisioned_uuid": PRE_PROVISIONED}),
}


# ── N-01: reminder_7d_sent / reminder_1d_sent reset on every grant ─────

@pytest.mark.parametrize("name", sorted(GRANT_SCENARIOS))
async def test_n01_every_grant_branch_resets_7d_and_1d_flags(frozen, name):
    seed, kw = GRANT_SCENARIOS[name]
    _result, conn = await _grant(seed(), **kw)
    writes = _sub_writes(conn)
    assert writes, "grant_access wrote nothing to subscriptions"
    for sql, _args in writes:
        for flag in ("reminder_7d_sent = false", "reminder_1d_sent = false",
                     "reminder_3d_sent = false", "reminder_3h_sent = false"):
            assert flag in sql, f"{name}: {flag!r} missing in {sql[:120]}…"


def test_n01_second_period_gets_7d_and_1d_again():
    """After the reset the 2nd paid period is scheduled like the 1st one."""
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    reset_row = {
        "source": "payment", "subscription_type": "basic", "admin_grant_days": None,
        "last_action_type": "renewal", "reminder_7d_sent": False, "reminder_1d_sent": False,
        "reminder_3d_sent": False, "reminder_3h_sent": False,
    }
    d7 = notification_service.should_send_reminder({**reset_row, "expires_at": now + timedelta(days=7)}, now)
    d1 = notification_service.should_send_reminder({**reset_row, "expires_at": now + timedelta(hours=24)}, now)
    assert d7.should_send and d7.reminder_type.value == "reminder_7d"
    assert d1.should_send and d1.reminder_type.value == "reminder_1d"


# ── N-04: admin_grant_days cleared by every PAID grant ─────────────────

def _clears_admin_grant_days(sql: str, args: tuple) -> bool:
    if "admin_grant_days = null" in sql:
        return True
    if "admin_grant_days = case when $6 then null else admin_grant_days end" in sql:
        return args[5] is True
    if "admin_grant_days = $" in sql:  # new issuance writes the (None) argument
        idx = int(sql.split("admin_grant_days = $", 1)[1].split(",", 1)[0].strip()) - 1
        return args[idx] is None
    return False


@pytest.mark.parametrize("source", ["payment", "auto_renew", "gift"])
@pytest.mark.parametrize("name", sorted(GRANT_SCENARIOS))
async def test_n04_paid_grant_clears_admin_grant_days(frozen, name, source):
    if source != "payment" and name in ("renewal_basic_to_plus", "renewal_plus_to_basic"):
        pytest.skip("tariff switch branches are payment-only")
    seed, kw = GRANT_SCENARIOS[name]
    row = seed()
    if row is not None:
        row["admin_grant_days"] = 7
    _result, conn = await _grant(row, source=source, **kw)
    (sql, args), = [w for w in _sub_writes(conn)]
    assert _clears_admin_grant_days(sql, args), f"{name}/{source}: admin_grant_days survives: {sql[:160]}"


async def test_n04_admin_renewal_keeps_admin_grant_days(frozen):
    _result, conn = await _grant(_row(admin_grant_days=7), source="admin", admin_telegram_id=1, admin_grant_days=7)
    (sql, args), = _sub_writes(conn)
    assert not _clears_admin_grant_days(sql, args)


def test_n04_paid_user_after_admin_grant_gets_paid_reminders():
    """With admin_grant_days cleared the paid schedule applies (not the '24h free' one)."""
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    paid = {"source": "payment", "admin_grant_days": None, "last_action_type": "renewal",
            "expires_at": now + timedelta(days=3)}
    decision = notification_service.should_send_reminder(paid, now)
    assert decision.should_send and decision.reminder_type.value == "reminder_3d"


# ── N-07: broadcast "trial key" never turns a paid sub into source='trial' ──

@pytest.mark.parametrize("current_source", ["payment", "auto_renew", "gift", "admin"])
async def test_n07_trial_gift_keeps_source_of_active_paid_subscription(frozen, current_source):
    row = _row("plus", source=current_source)
    result, conn = await _grant(row, source="trial")
    (sql, args), = _sub_writes(conn)
    assert sql.startswith("update subscriptions")
    assert args[1] == current_source           # source = $2
    assert args[4] == "plus"                    # subscription_type = COALESCE($5, …): no Plus→Basic
    assert result["action"] == "renewal"
    # the paid reminders keep working for this subscription
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    decision = notification_service.should_send_reminder(
        {"source": args[1], "admin_grant_days": None, "last_action_type": "trial",
         "expires_at": now + timedelta(days=7)}, now)
    assert current_source == "admin" or decision.should_send


@pytest.mark.parametrize("current_source,is_bypass_only", [("trial", False), ("bypass_only", True)])
async def test_n07_trial_gift_on_trial_or_bypass_only_row_unchanged(frozen, current_source, is_bypass_only):
    row = _row("basic", source=current_source, is_bypass_only=is_bypass_only)
    _result, conn = await _grant(row, source="trial")
    # (a trial row also gets its trial end moved + trial flags reset — #4)
    (sql, args), = [w for w in _sub_writes(conn) if "expires_at = $1" in w[0]]
    assert args[1] == "trial"


# ── #3 / #4: day grants (admin / promo link / game) only move the date ──

@pytest.mark.parametrize("grant", ["admin", "game_strike", "game_dice"])
@pytest.mark.parametrize("current_source", ["payment", "auto_renew", "gift"])
async def test_day_grant_on_a_paid_subscription_keeps_it_paid(frozen, grant, current_source):
    row = _row("plus", source=current_source)
    kw = {"admin_telegram_id": 1, "admin_grant_days": 7} if grant == "admin" else {}
    result, conn = await _grant(row, source=grant, tariff="basic", **kw)
    (sql, args), = [w for w in _sub_writes(conn) if "expires_at = $1" in w[0]]
    assert args[1] == current_source, "the row stays paid: its reminders, −15 % and end message"
    assert args[4] == "plus", "a day grant never flips the tariff"
    assert not _clears_admin_grant_days(sql, args) and "admin_grant_days = case" in sql
    assert result["action"] == "renewal"
    assert not [c for c in conn.calls if "update users set trial_expires_at" in c[1]]
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    decision = notification_service.should_send_reminder(
        {"source": args[1], "admin_grant_days": None, "last_action_type": "admin_grant",
         "expires_at": now + timedelta(days=7)}, now)
    assert decision.should_send and decision.reminder_type.value == "reminder_7d"


async def test_admin_plus_days_on_basic_upgrade_the_tariff_and_keep_the_source(frozen):
    row = _row("basic", source="payment")
    _result, conn = await _grant(row, source="admin", tariff="plus", admin_telegram_id=1, admin_grant_days=7)
    (sql, args), = [w for w in _sub_writes(conn) if "expires_at = $1" in w[0]]
    assert (args[1], args[4]) == ("payment", "plus")


@pytest.mark.parametrize("grant", ["admin", "game_strike", "game_dice", "trial"])
async def test_day_grant_during_a_trial_extends_the_trial(frozen, grant):
    """#4: +days during a trial turned it into source='game_*' / 'admin' — no trial
    reminders, no «пробный завершён −30 %», paid «продлите» to a trial user instead."""
    row = _row("basic", source="trial")
    result, conn = await _grant(row, source=grant)
    (sql, args), = [w for w in _sub_writes(conn) if "expires_at = $1" in w[0]]
    assert args[1] == "trial"
    trial_move = [c for c in conn.calls if c[1].startswith("update users set trial_expires_at")]
    assert len(trial_move) == 1 and trial_move[0][2][0] == args[0], "trial end = the new end"
    flags = [c for c in conn.calls if c[1].startswith("update subscriptions set trial_notif_24h_sent = false")]
    assert flags, "the trial reminders go out again for the new end"


async def test_day_grant_on_a_bypass_only_row_is_the_grant(frozen):
    row = _row("basic", source="bypass_only", is_bypass_only=True)
    _result, conn = await _grant(row, source="game_dice")
    (sql, args), = [w for w in _sub_writes(conn) if "expires_at = $1" in w[0]]
    assert args[1] == "game_dice"


async def test_paid_period_during_a_trial_still_ends_the_trial(frozen):
    row = _row("basic", source="trial")
    _result, conn = await _grant(row, source="payment", tariff="basic", tariff_period_days=30)
    (sql, args), = [w for w in _sub_writes(conn) if "expires_at = $1" in w[0]]
    assert args[1] == "payment"
    assert not [c for c in conn.calls if c[1].startswith("update subscriptions set trial_notif_24h_sent")]


# ═══════════════════════════════════════════════════════════════════════
# N-02: is_reachable is restored; auto-renewal does not depend on it
# ═══════════════════════════════════════════════════════════════════════

class _RecConn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((_norm(sql), args))
        return "UPDATE 1"


class _RecPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return h._Acquire(self.conn)


async def test_n02_mark_user_reachable_sets_true(monkeypatch):
    import database.core as core
    import database.users as db_users
    conn = _RecConn()

    async def _pool():
        return _RecPool(conn)
    monkeypatch.setattr(core, "DB_READY", True)
    monkeypatch.setattr(db_users, "get_pool", _pool)
    await database.mark_user_reachable(TG)
    assert conn.calls == [(
        "update users set is_reachable = true where telegram_id = $1 and is_reachable = false", (TG,),
    )]


async def test_n02_last_seen_middleware_restores_reachability(monkeypatch):
    from aiogram.types import CallbackQuery, User
    from app.core.last_seen_middleware import LastSeenMiddleware

    touch = AsyncMock()
    reach = AsyncMock()
    monkeypatch.setattr(database, "touch_last_seen", touch)
    monkeypatch.setattr(database, "mark_user_reachable", reach, raising=False)
    event = CallbackQuery.model_construct(
        id="1", from_user=User(id=TG, is_bot=False, first_name="t"), chat_instance="c",
    )
    handler = AsyncMock(return_value="ok")
    assert await LastSeenMiddleware()(handler, event, {}) == "ok"
    for _ in range(3):
        await asyncio.sleep(0)
    touch.assert_awaited_once_with(TG)
    reach.assert_awaited_once_with(TG)


async def test_n02_auto_renewal_selection_ignores_is_reachable(monkeypatch):
    w = h.install(monkeypatch)
    w.seed_active_subscription(days_left=0)
    w.sub["expires_at"] = h.naive(h.utcnow() + timedelta(hours=2))
    out = await h.run_auto_renewal(w, monkeypatch, last_payment_tariff="basic_30")
    selects = [s for kind, s, _a in w.conn.calls if kind == "fetch" and "from subscriptions" in s]
    assert selects, "auto-renewal did not select due subscriptions"
    assert all("is_reachable" not in s for s in selects)
    out["decrease_balance"].assert_awaited_once()  # the user was billed and renewed


# ═══════════════════════════════════════════════════════════════════════
# N-03: the RU dashboard override is used only when i18n would answer in RU
# ═══════════════════════════════════════════════════════════════════════

RU_OVERRIDE = "RU OVERRIDE FROM DASHBOARD"


@pytest.fixture
def ru_override(monkeypatch):
    from app.services.automated_notifications import helper
    cache = {
        key: {"is_enabled": True, "text": RU_OVERRIDE, "trigger_config": {}}
        for key in ("subscription.reminder_7d", "subscription.reminder_3d", "subscription.reminder_1d",
                    "subscription.reminder_3h", "trial.reminder_24h", "trial.reminder_3h",
                    "trial.notification_71h")
    }
    monkeypatch.setattr(helper, "_CACHE", cache)
    monkeypatch.setattr(helper, "_CACHE_TS", time.monotonic() + 3600)
    return helper


@pytest.mark.parametrize("language,expected", [
    ("en", None), ("ru", RU_OVERRIDE), ("kk", RU_OVERRIDE), (None, RU_OVERRIDE),
])
async def test_n03_override_only_for_russian_rendering(ru_override, language, expected):
    assert await ru_override.get_notification_text("subscription.reminder_7d", language=language) == expected


def _patch_reminders(monkeypatch, language):
    import reminders
    from app.services import automated_notifications as an
    sent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(reminders, "safe_send_message", sent)
    monkeypatch.setattr(reminders, "resolve_user_language", AsyncMock(return_value=language))
    monkeypatch.setattr(notification_service, "mark_reminder_sent", AsyncMock())
    monkeypatch.setattr(reminders, "_claim_reminder", AsyncMock(return_value=True))
    monkeypatch.setattr(an, "log_notification_send", AsyncMock())
    monkeypatch.setattr(an, "get_trigger_config", AsyncMock(return_value={}))
    monkeypatch.setattr(database, "_log_audit_event_atomic_standalone", AsyncMock(), raising=False)
    now = datetime.now(timezone.utc)
    sub = {"telegram_id": TG, "source": "payment", "subscription_type": "basic",
           "admin_grant_days": None, "last_action_type": "purchase",
           "expires_at": now + timedelta(days=7)}
    monkeypatch.setattr(database, "get_subscriptions_for_reminders", AsyncMock(return_value=[sub]))
    return reminders, sent


@pytest.mark.parametrize("language", ["en", "ru"])
async def test_n03_paid_reminder_text_matches_user_language(ru_override, monkeypatch, language):
    reminders, sent = _patch_reminders(monkeypatch, language)
    await reminders.send_smart_reminders(MagicMock())
    text = sent.await_args.args[2]
    expected = i18n.get_text("en", "reminder.paid_7d") if language == "en" else RU_OVERRIDE
    assert text == expected


@pytest.mark.parametrize("language", ["en", "ru"])
async def test_n03_trial_24h_text_matches_user_language(ru_override, monkeypatch, language):
    import trial_notifications as tn
    from app.services import automated_notifications as an
    sent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(tn, "safe_send_message", sent)
    monkeypatch.setattr(tn, "resolve_user_language", AsyncMock(return_value=language))
    monkeypatch.setattr(an, "log_notification_send", AsyncMock())
    monkeypatch.setattr(an, "get_trigger_config", AsyncMock(return_value={}))
    now = datetime.now(timezone.utc)
    row = {
        "telegram_id": TG,
        "trial_expires_at": h.naive(now + timedelta(hours=24)),
        "subscription_expires_at": h.naive(now + timedelta(hours=24)),
        "paid_subscription_expires_at": None,
        "trial_notif_24h_sent": False, "trial_notif_3h_sent": False,
        "trial_notif_bypass_activated_sent": True,
    }
    await tn._process_single_trial_notification(MagicMock(), _RecPool(_RecConn()), row, now)
    text = sent.await_args.args[2]
    expected = i18n.get_text("en", "trial.reminder_24h") if language == "en" else RU_OVERRIDE
    assert text == expected


# ═══════════════════════════════════════════════════════════════════════
# N-05: "trial ended −30%" is delivered exactly once, and the 30% is real
# ═══════════════════════════════════════════════════════════════════════

class _ExpiryConn:
    """fast_expiry_cleanup + trial completion queries against one expired row."""

    def __init__(self, *, source: str, bypass: bool = False):
        now = datetime.now(timezone.utc)
        self.row = {"id": 1, "telegram_id": TG, "uuid": "u-1", "vpn_key": "k",
                    "expires_at": h.naive(now - timedelta(minutes=1)), "status": "active", "source": source}
        self.bypass = bypass
        self.trial_completed_sent = False
        self.batches = [[dict(self.row)]]
        self.calls = []

    def transaction(self):
        return h._Tx()

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", _norm(sql), args))
        return self.batches.pop(0) if self.batches else []

    async def fetchrow(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchrow", s, args))
        if s.startswith("select uuid, expires_at, status from subscriptions"):
            return {k: self.row[k] for k in ("uuid", "expires_at", "status")}
        if "trial_completed_sent" in s and "from users" in s:
            return {"trial_used_at": h.naive(datetime.now(timezone.utc) - timedelta(days=3)),
                    "trial_completed_sent": self.trial_completed_sent}
        return None

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", _norm(sql), args))
        return "rw-uuid" if self.bypass else None

    async def execute(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("execute", s, args))
        if s.startswith("update users set trial_completed_sent = true"):
            if self.trial_completed_sent:
                return "UPDATE 0"
            self.trial_completed_sent = True
            return "UPDATE 1"
        return "UPDATE 1"


def _patch_expiry(monkeypatch, conn, *, existing_discount=None):
    import fast_expiry_cleanup as fec
    import trial_notifications as tn
    from app.core import feature_flags

    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *a, **k):
        if delay == fec.CLEANUP_INTERVAL_SECONDS:
            raise asyncio.CancelledError  # stop after one iteration
        await real_sleep(0)

    sent = AsyncMock(return_value=MagicMock())
    create_discount = AsyncMock(return_value=True)
    monkeypatch.setattr(fec.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(fec.random, "uniform", lambda *_a: 1.0)
    monkeypatch.setattr(feature_flags, "get_feature_flags",
                        lambda: SimpleNamespace(background_workers_enabled=True))
    monkeypatch.setattr(database, "DB_READY", True, raising=False)
    monkeypatch.setattr(database, "get_pool", AsyncMock(return_value=_RecPool(conn)))
    monkeypatch.setattr(fec, "acquire_connection", lambda _pool, _name: h._Acquire(conn))
    monkeypatch.setattr(database, "get_active_paid_subscription", AsyncMock(return_value=None))
    monkeypatch.setattr(database, "_log_vpn_lifecycle_audit_async", AsyncMock(), raising=False)
    monkeypatch.setattr(database, "_log_audit_event_atomic", AsyncMock(), raising=False)
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(return_value=existing_discount), raising=False)
    monkeypatch.setattr(database, "create_user_discount", create_discount, raising=False)
    for mod in (fec, tn):
        monkeypatch.setattr(mod, "safe_send_message", sent)
        monkeypatch.setattr(mod, "resolve_user_language", AsyncMock(return_value="ru"))
    try:
        from app.services import remnawave_service
        monkeypatch.setattr(remnawave_service, "extend_remnawave_for_bypass_bg", lambda *_a, **_k: None)
    except Exception:  # pragma: no cover
        pass
    return fec, sent, create_discount


async def _run_one_expiry_iteration(fec):
    with pytest.raises(asyncio.CancelledError):
        await fec.fast_expiry_cleanup_task(bot=MagicMock())


def _texts(sent) -> list:
    return [c.args[2] for c in sent.await_args_list]


@pytest.mark.parametrize("bypass", [False, True])
async def test_n05_fast_expiry_delivers_trial_expired_exactly_once(monkeypatch, bypass):
    conn = _ExpiryConn(source="trial", bypass=bypass)
    fec, sent, create_discount = _patch_expiry(monkeypatch, conn)

    await _run_one_expiry_iteration(fec)

    expired_text = i18n.get_text("ru", "trial.expired")
    assert _texts(sent).count(expired_text) == 1
    assert conn.trial_completed_sent is True
    # the promised 30% is a real personal discount with a limited window
    kw = create_discount.await_args.kwargs
    assert kw["telegram_id"] == TG and kw["discount_percent"] == 30
    window = kw["expires_at"] - datetime.now(timezone.utc)
    assert timedelta(days=6) < window <= timedelta(days=7)

    # a second claim (trial_notifications worker, restart…) sends nothing
    import trial_notifications as tn
    assert await tn.claim_trial_expired_notice(TG, conn) is False


async def test_trial_end_with_bypass_is_one_message_without_the_15_percent(monkeypatch):
    """#1: the trial row with a bypass entity becomes bypass-only → the user got
    «пробный завершён −30 %» AND «основная подписка закончилась −15 %»."""
    from app.services.notifications import special_offer
    conn = _ExpiryConn(source="trial", bypass=True)
    fec, sent, _discount = _patch_expiry(monkeypatch, conn)
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(special_offer, "notify_expired", notify)

    await _run_one_expiry_iteration(fec)

    assert _texts(sent) == [i18n.get_text("ru", "trial.expired")]
    notify.assert_not_awaited()


async def test_n05_trial_expired_text_promises_what_is_applied():
    for lang in ("ru", "en"):
        text = i18n.get_text(lang, "trial.expired")
        assert "30%" in text
        assert "YABX30" not in text.upper()  # no promo code that does not exist in code
        assert "7" in text                   # the discount window is stated


async def test_n05_paid_subscription_expiry_sends_no_trial_message(monkeypatch):
    conn = _ExpiryConn(source="payment")
    fec, sent, create_discount = _patch_expiry(monkeypatch, conn)
    await _run_one_expiry_iteration(fec)
    assert i18n.get_text("ru", "trial.expired") not in _texts(sent)
    create_discount.assert_not_awaited()


async def test_n05_existing_bigger_discount_is_not_downgraded(monkeypatch):
    conn = _ExpiryConn(source="trial")
    fec, sent, create_discount = _patch_expiry(monkeypatch, conn, existing_discount={"discount_percent": 50})
    await _run_one_expiry_iteration(fec)
    create_discount.assert_not_awaited()
    assert _texts(sent).count(i18n.get_text("ru", "trial.expired")) == 1


# ═══════════════════════════════════════════════════════════════════════
# N-06: auto-renewal success shows the amount actually charged
# ═══════════════════════════════════════════════════════════════════════

async def test_n06_auto_renewal_message_shows_charged_amount(monkeypatch):
    w = h.install(monkeypatch)
    w.seed_active_subscription(days_left=0)
    w.sub["expires_at"] = h.naive(h.utcnow() + timedelta(hours=2))

    out = await h.run_auto_renewal(w, monkeypatch, last_payment_tariff="basic_30")

    price = config.TARIFFS["basic"][30]["price"]
    charged = out["decrease_balance"].await_args.kwargs["amount"]
    assert charged == price
    args = out["safe_send_message"].await_args.args
    text = args[2]
    assert f": {float(price):.2f} ₽" in text
    assert ": 0.00 ₽" not in text
