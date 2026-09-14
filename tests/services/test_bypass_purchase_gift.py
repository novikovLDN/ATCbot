"""Owner rule 2026-09-14 (docs/audit/SCOPE.md, «Покупка ГБ с экрана «🌐 Только обход
блокировок» без подписки»): GB + a ONE-TIME 3-day premium gift, regardless of the
trial flag, without the trial's 500 MB.

Hermetic unit tests of app.services.trials.service's gift (eligibility, the legacy
claim / release / background retry, alerts, texts), the purchase screen and the
GB-aware trial reminders. The purchase paths end to end: test_payment_core_t15
(section 3), the payment matrix (section 2b) and tests/e2e/test_17_bypass_only_gift.py
(real Postgres).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
import trial_notifications
from app.i18n import get_text
from app.services import admin_alerts, tariffs
from app.services.trials import service as ts

UTC = timezone.utc
TG = 7123
BOT = MagicMock(name="bot")


def utcnow() -> datetime:
    return datetime.now(UTC)


def naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


# ── a users row + a subscriptions row; the advisory lock is a real asyncio.Lock
#    held until the transaction ends, writes become visible at commit ─────────

class GiftDB:
    def __init__(self):
        self.user = {"trial_used_at": None, "trial_expires_at": None}
        self.sub = None
        self.job = False
        self.lock = asyncio.Lock()


class _Conn:
    def __init__(self, db: GiftDB):
        self.db = db
        self.staged: dict = {}
        self.locked = False

    def transaction(self):
        return _Tx(self)

    def _user(self) -> dict:
        return {**self.db.user, **self.staged}

    async def execute(self, sql, *args):
        s = " ".join(sql.split()).lower()
        if "pg_advisory_xact_lock" in s:
            await self.db.lock.acquire()
            self.locked = True
            return "SELECT 1"
        if s.startswith("update users set trial_used_at = current_timestamp"):
            if self._user()["trial_used_at"] is not None:
                return "UPDATE 0"
            self.staged.update(trial_used_at=naive(utcnow()), trial_expires_at=args[0])
            return "UPDATE 1"
        if s.startswith("update users set trial_used_at = null"):   # claim release (autocommit)
            if self.db.user["trial_expires_at"] == args[1]:
                self.db.user.update(trial_used_at=None, trial_expires_at=None)
                return "UPDATE 1"
            return "UPDATE 0"
        raise AssertionError(f"unexpected SQL: {sql}")

    async def fetchrow(self, sql, *args):
        sub = self.db.sub or {}
        return {"trial_used_at": self._user()["trial_used_at"], "status": sub.get("status"),
                "expires_at": sub.get("expires_at"), "source": sub.get("source"),
                "is_bypass_only": bool(sub.get("is_bypass_only"))}

    async def fetchval(self, sql, *args):
        assert "from provisioning_jobs" in " ".join(sql.split()).lower()
        return 1 if self.db.job else None


class _Tx:
    def __init__(self, conn: _Conn):
        self.c = conn

    async def __aenter__(self):
        self.c.staged = {}
        return self

    async def __aexit__(self, exc_type, *_exc):
        if exc_type is None:
            self.c.db.user.update(self.c.staged)
        self.c.staged = {}
        if self.c.locked:
            self.c.locked = False
            self.c.db.lock.release()
        return False


class _Acq:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return _Conn(self.db)

    async def __aexit__(self, *_exc):
        return False


class _Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        return _Acq(self.db)


def _grant_ok(**_kw):
    return {"subscription_end": utcnow() + timedelta(days=3), "uuid": "u-1",
            "vless_url": "https://panel.test/sub/prem", "action": "new_issuance"}


@pytest.fixture
def gdb(monkeypatch):
    db = GiftDB()

    async def get_pool():
        return _Pool(db)
    monkeypatch.setattr(database, "get_pool", get_pool)
    db.grant = AsyncMock(side_effect=_grant_ok)
    monkeypatch.setattr(database, "grant_access", db.grant)
    db.pointer = AsyncMock(return_value="bypass-panel-uuid")
    monkeypatch.setattr(database, "get_remnawave_uuid", db.pointer)
    db.sub_any = AsyncMock(return_value=None)
    monkeypatch.setattr(database, "get_subscription_any", db.sub_any)
    db.alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", db.alerts)
    return db


@pytest.fixture(autouse=True)
async def _no_leftover_retries():
    yield
    tasks = list(ts._gift_retry_tasks.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    ts._gift_retry_tasks.clear()


def _alert_titles(db) -> list:
    return [c.args[2].splitlines()[0] for c in db.alerts.await_args_list]


# ── entitlement, date text, eligibility ─────────────────────────────────

def test_gift_entitlement_is_3_days_basic_premium_and_zero_gb():
    ent = tariffs.for_bypass_purchase_gift()
    assert (ent.tariff_key, ent.premium_days, ent.premium_tier, ent.bypass_bytes) == ("trial", 3, "basic", 0)
    # the trial itself is unchanged: 3 days + TRIAL_BYPASS_MB
    assert tariffs.for_trial().bypass_bytes == config.TRIAL_BYPASS_MB * 1024 ** 2


@pytest.mark.parametrize("dt,text", [
    (datetime(2026, 9, 17, 10, 36, 15, tzinfo=UTC), "17.09 13:36"),
    (datetime(2026, 9, 17, 22, 5, tzinfo=UTC), "18.09 01:05"),        # MSK is already tomorrow
    (datetime(2026, 9, 17, 10, 36, 15), "17.09 13:36"),               # naive = UTC (DB contract)
])
def test_gift_end_is_shown_in_moscow_time(dt, text):
    assert ts.format_gift_until(dt) == text


NOW = utcnow()


@pytest.mark.parametrize("row,reason", [
    (None, "no_user"),
    ({"trial_used_at": naive(NOW), "status": None, "expires_at": None, "is_bypass_only": False}, "trial_used"),
    ({"trial_used_at": None, "status": "active", "expires_at": naive(NOW + timedelta(days=9)),
      "is_bypass_only": False}, "active_subscription"),                      # paid / granted premium
    ({"trial_used_at": None, "status": "active", "expires_at": naive(NOW + timedelta(days=3650)),
      "is_bypass_only": True}, None),                                        # bypass-only placeholder
    ({"trial_used_at": None, "status": "expired", "expires_at": naive(NOW - timedelta(days=5)),
      "is_bypass_only": False}, None),                                       # premium ended
    ({"trial_used_at": None, "status": "active", "expires_at": naive(NOW - timedelta(minutes=1)),
      "is_bypass_only": False}, None),                                       # ended, not cleaned yet
    ({"trial_used_at": None, "status": None, "expires_at": None, "is_bypass_only": False}, None),
], ids=["no-user", "trial-used", "active-premium", "bypass-only", "expired", "ended-not-cleaned", "new"])
def test_gift_eligibility(row, reason):
    assert ts._gift_unavailable_reason(row, NOW) == reason


async def test_screen_availability_is_the_grant_rule(gdb, monkeypatch):
    assert await ts.is_bypass_gift_available(TG) is True
    gdb.sub = {"status": "active", "expires_at": naive(utcnow() + timedelta(days=3650)), "is_bypass_only": True}
    assert await ts.is_bypass_gift_available(TG) is True
    gdb.sub = {"status": "active", "expires_at": naive(utcnow() + timedelta(days=9)), "is_bypass_only": False}
    assert await ts.is_bypass_gift_available(TG) is False
    gdb.sub = None
    gdb.user["trial_used_at"] = naive(utcnow())
    assert await ts.is_bypass_gift_available(TG) is False

    async def broken():
        raise OSError("db down")
    monkeypatch.setattr(database, "get_pool", broken)
    assert await ts.is_bypass_gift_available(TG) is False     # never promise on a DB error


# ── legacy path (both flags off) ─────────────────────────────────────────

async def test_legacy_gift_claims_the_trial_then_grants_3_days(gdb):
    t0 = utcnow()
    grant = await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="traffic_pack:platega:p1", via_outbox=False)

    assert grant is not None
    assert t0 + timedelta(days=3) <= grant.subscription_end <= utcnow() + timedelta(days=3)
    gdb.grant.assert_awaited_once_with(telegram_id=TG, duration=timedelta(days=3), source="trial",
                                       admin_telegram_id=None)
    assert gdb.user["trial_used_at"] is not None
    end = gdb.user["trial_expires_at"].replace(tzinfo=UTC)
    assert t0 + timedelta(days=3) <= end <= utcnow() + timedelta(days=3)
    gdb.alerts.assert_not_awaited()


async def test_legacy_gift_is_one_time_even_for_concurrent_purchases(gdb):
    results = await asyncio.gather(*[
        ts.grant_bypass_purchase_gift(TG, bot=BOT, where=f"p{i}", via_outbox=False) for i in range(3)
    ])
    assert sum(r is not None for r in results) == 1
    gdb.grant.assert_awaited_once()
    assert await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="later", via_outbox=False) is None
    gdb.grant.assert_awaited_once()


@pytest.mark.parametrize("state", ["trial_used", "active_premium", "job_exists"])
async def test_legacy_gift_not_granted_when_not_eligible(gdb, state):
    if state == "trial_used":
        gdb.user["trial_used_at"] = naive(utcnow())
    elif state == "active_premium":
        gdb.sub = {"status": "active", "expires_at": naive(utcnow() + timedelta(days=9)), "is_bypass_only": False}
    else:
        gdb.job = True          # a trial:{tg} job exists (trial_used_at reset by hand)
    before = dict(gdb.user)
    assert await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="w", via_outbox=False) is None
    gdb.grant.assert_not_awaited()
    assert gdb.user == before
    gdb.alerts.assert_not_awaited()


async def test_legacy_panel_failure_releases_the_claim_alerts_and_retry_grants_later(gdb, monkeypatch):
    from app.services import language_service
    from app.utils import telegram_safe
    gate = asyncio.Event()

    async def gated_sleep(_seconds):
        await gate.wait()
    monkeypatch.setattr(ts, "_gift_retry_sleep", gated_sleep)
    sent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(telegram_safe, "safe_send_message", sent)
    monkeypatch.setattr(language_service, "resolve_user_language", AsyncMock(return_value="ru"))
    gdb.grant.side_effect = RuntimeError("panel down")

    assert await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="traffic_pack:wata:p1", via_outbox=False) is None
    assert gdb.user == {"trial_used_at": None, "trial_expires_at": None}      # claim released
    assert _alert_titles(gdb) == ["Bypass-purchase gift (3 days premium) FAILED"]
    assert gdb.alerts.await_args.kwargs["force"] is True and gdb.alerts.await_args.args[0] is BOT
    assert "traffic_pack:wata:p1" in gdb.alerts.await_args.args[2]
    task = ts._gift_retry_tasks[TG]

    gdb.grant.side_effect = _grant_ok      # the panel is back
    gate.set()
    await task
    assert gdb.grant.await_count == 2
    assert gdb.user["trial_used_at"] is not None
    sent.assert_awaited_once()
    assert sent.await_args.args[:2] == (BOT, TG)
    text = sent.await_args.args[2]
    assert text.startswith(get_text("ru", "bypass.gift_premium_granted").split("{", 1)[0])
    assert _alert_titles(gdb) == ["Bypass-purchase gift (3 days premium) FAILED"]   # no more alerts


async def test_legacy_retry_gives_up_with_a_final_forced_alert(gdb, monkeypatch):
    monkeypatch.setattr(ts, "_gift_retry_sleep", AsyncMock())
    gdb.grant.side_effect = RuntimeError("panel down")
    assert await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="w", via_outbox=False) is None
    await ts._gift_retry_tasks[TG]
    assert gdb.grant.await_count == 1 + len(ts.GIFT_RETRY_DELAYS_S)
    assert _alert_titles(gdb) == ["Bypass-purchase gift (3 days premium) FAILED", "Bypass-purchase gift: GAVE UP"]
    assert all(c.kwargs["force"] for c in gdb.alerts.await_args_list)
    assert gdb.user["trial_used_at"] is None            # nothing half-granted


async def test_legacy_retry_stops_if_the_user_is_no_longer_eligible(gdb, monkeypatch):
    gate = asyncio.Event()

    async def gated_sleep(_seconds):
        await gate.wait()
    monkeypatch.setattr(ts, "_gift_retry_sleep", gated_sleep)
    gdb.grant.side_effect = RuntimeError("panel down")
    assert await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="w", via_outbox=False) is None
    gdb.user["trial_used_at"] = naive(utcnow())      # e.g. the user took the menu trial meanwhile
    gdb.grant.side_effect = _grant_ok
    gate.set()
    await ts._gift_retry_tasks[TG]
    assert gdb.grant.await_count == 1
    assert _alert_titles(gdb)[-1] == "Bypass-purchase gift: retry stopped"


async def test_legacy_gift_never_creates_a_trial_size_bypass_entity(gdb, monkeypatch):
    """GB not delivered (no bypass pointer): the legacy grant would create a bypass
    entity with TRIAL_BYPASS_MB — refused, claim released, alert + retry."""
    gdb.pointer.return_value = None
    retry = MagicMock()
    monkeypatch.setattr(ts, "_schedule_gift_retry", retry)
    assert await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="w", via_outbox=False) is None
    gdb.grant.assert_not_awaited()
    assert gdb.user["trial_used_at"] is None
    assert _alert_titles(gdb) == ["Bypass-purchase gift (3 days premium) FAILED"]
    retry.assert_called_once()
    assert retry.call_args.kwargs["via_outbox"] is False


async def test_legacy_panel_sync_failure_after_the_db_grant_keeps_the_gift(gdb, monkeypatch):
    """Renewal branch of grant_access: DB extended, panel sync failed (purchase_flow
    alerts and re-syncs itself) — the gift stands, the claim is kept, no retry."""
    end = utcnow() + timedelta(days=3)
    gdb.grant.side_effect = RuntimeError("renewal sync failed")
    gdb.sub_any.return_value = {"source": "trial", "status": "active", "is_bypass_only": False,
                                "expires_at": end}
    retry = MagicMock()
    monkeypatch.setattr(ts, "_schedule_gift_retry", retry)
    grant = await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="w", via_outbox=False)
    assert grant is not None and grant.subscription_end == end
    assert gdb.user["trial_used_at"] is not None
    retry.assert_not_called()
    gdb.alerts.assert_not_awaited()


async def test_gift_never_raises_even_when_alerting_fails(gdb, monkeypatch):
    monkeypatch.setattr(ts, "_attempt_gift", AsyncMock(side_effect=RuntimeError("boom")))
    gdb.alerts.side_effect = RuntimeError("telegram down")
    monkeypatch.setattr(ts, "_schedule_gift_retry", MagicMock())
    assert await ts.grant_bypass_purchase_gift(TG, bot=BOT, where="w", via_outbox=True) is None


# ── the purchase screen promises the gift only to users who get it ──────

@pytest.mark.parametrize("available", [True, False])
async def test_screen_promises_the_gift_only_when_the_grant_would_give_it(monkeypatch, available):
    from app.handlers import traffic
    monkeypatch.setattr(traffic, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(traffic, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(database, "get_user_traffic_discount", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ts, "is_bypass_gift_available", AsyncMock(return_value=available))
    monkeypatch.setattr(ts, "is_trial_available", AsyncMock(side_effect=AssertionError("menu trial rule")))
    cb = MagicMock()
    cb.from_user.id = TG
    cb.answer = AsyncMock()
    cb.message.delete = AsyncMock()
    cb.bot.send_message = AsyncMock()

    await traffic.callback_buy_bypass_only(cb)

    text = cb.bot.send_message.await_args.args[1]
    assert (get_text("ru", "bypass.buy_title_trial") in text) is available


# ── trial reminders: GB-aware text for users who bought GB ───────────────

class _ValPool:
    def __init__(self, value):
        self.value = value

    def acquire(self):
        pool = self

        class _A:
            async def __aenter__(self):
                conn = MagicMock()
                conn.fetchval = AsyncMock(return_value=pool.value)
                return conn

            async def __aexit__(self, *_exc):
                return False
        return _A()


@pytest.mark.parametrize("key", ["trial.reminder_24h", "trial.reminder_3h", "trial.notification_71h"])
@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_reminder_for_a_user_with_purchased_gb_never_says_vpn_stops(key, lang):
    text = await trial_notifications._gb_reminder_text(_ValPool(True), TG, key, lang)
    assert text == get_text(lang, key + "_gb")
    low = text.lower()
    assert ("гб" in low) if lang == "ru" else ("gb" in low)
    for false_claim in ("vpn перестанет", "vpn будет отключ", "vpn will stop", "vpn will be disabled",
                        "всё вернётся к блокировкам", "back to blocks"):
        assert false_claim not in low
    assert await trial_notifications._gb_reminder_text(_ValPool(False), TG, key, lang) is None


async def test_reminder_without_a_gb_variant_is_unchanged():
    assert await trial_notifications._gb_reminder_text(_ValPool(True), TG, "trial.expired", "ru") is None
