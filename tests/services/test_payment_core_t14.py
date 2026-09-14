"""T14 — gift activation (/start gift_<code>) through the provisioning outbox
(entry point "gift"). Spec: docs/audit/02_payment_core_plan.md §A flow 7, T14,
"Находки T0" item 2.

Flag ON: activate_gift_subscription, in ONE tx under the gift row lock:
mark the gift activated → grant_access(defer_panel=True, _caller_holds_transaction=True)
→ provisioning.enqueue("gift:{code}", tariffs.for_purchase(tariff, period)) → commit
→ run_now against FakePanel. No Phase-1 HTTP, no /start renew_remnawave_user_bg.
The recipient gets the gifted tariff: premium for the period + 10 GB, once.

Flag OFF (T0 harness, tests/services/payment_core_harness.py): legacy path.
Behaviour fix (no flag): a gift to an already-active recipient extends the
premium expireAt in the panel — the renewal_xray_sync_after_commit returned by
grant_access inside the gift transaction is executed after commit, exactly as
finalize_purchase does.

Hermetic: FakePanel / FakeJobs / FakeDB (tests/fakes), a fake pool whose
connection emulates the gift_subscriptions statements with a real row lock
(asyncio.Lock held until the tx ends) and rollback, a fake grant_access.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
import database.admin as db_admin
import database.provisioning_jobs as pj
import database.subscriptions as db_subs
from app.api import payment_webhook
from app.handlers.user import start as start_mod
from app.i18n import get_text as i18n_get_text
from app.services import (
    admin_alerts, provisioning, provisioning_flags, purchase_flow, remnawave_service, sub_aggregator,
)
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeDB, FakeJobs
from tests.services import payment_core_harness as h

GIB = 1024 ** 3
TG = 6161
TG2 = 6162
GIFTER = 6100
CODE = "GIFT7777"
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def ceil_s(dt: datetime) -> datetime:
    return provisioning._ceil_second(dt)


# ── fake DB world ──────────────────────────────────────────────────────

class World:
    def __init__(self, jobs: FakeJobs, db: FakeDB):
        self.jobs = jobs
        self.db = db
        self.gift = None
        self.gift_lock = asyncio.Lock()   # the gift row lock (SELECT … FOR UPDATE)
        self.grant_calls = []
        self.fail_grant = False

    def seed_gift(self, *, code=CODE, tariff="basic", period_days=90, status="paid",
                  buyer=GIFTER, expires_in=timedelta(days=30)):
        self.gift = {
            "id": 1, "gift_code": code, "buyer_telegram_id": buyer, "tariff": tariff,
            "period_days": period_days, "price_kopecks": 100, "purchase_id": "gp-1",
            "status": status, "activated_by": None, "activated_at": None,
            "created_at": naive(utcnow()), "expires_at": naive(utcnow() + expires_in),
        }


class _Tx:
    """conn.transaction(): releases the row lock at the end; rolls back on error."""

    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        w = self.conn.world
        self.jobs_before = set(w.jobs.rows)
        self.subs_before = copy.deepcopy(w.db.subs)
        self.gift_before = copy.deepcopy(w.gift)
        self.conn.in_tx = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        w = self.conn.world
        self.conn.in_tx = False
        if exc_type is not None:
            for job_id in set(w.jobs.rows) - self.jobs_before:
                del w.jobs.rows[job_id]
            w.db.subs = self.subs_before
            w.gift = self.gift_before
        if self.conn.holds_lock:
            self.conn.holds_lock = False
            w.gift_lock.release()
        return False


class TxConn:
    def __init__(self, world: World):
        self.world = world
        self.in_tx = False
        self.holds_lock = False
        self.calls = []

    def is_in_transaction(self) -> bool:
        return self.in_tx

    def transaction(self):
        return _Tx(self)

    async def fetchrow(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchrow", s, args))
        w = self.world
        if s.startswith("SELECT * FROM gift_subscriptions WHERE gift_code = $1"):
            if s.endswith("FOR UPDATE"):
                assert self.in_tx, "FOR UPDATE outside a transaction"
                if not self.holds_lock:
                    await w.gift_lock.acquire()
                    self.holds_lock = True
            g = w.gift
            return dict(g) if g and g["gift_code"] == args[0] else None
        raise AssertionError(f"unexpected fetchrow: {s}")

    async def execute(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("execute", s, args))
        w = self.world
        if s.startswith("UPDATE gift_subscriptions SET status = 'expired'"):
            w.gift["status"] = "expired"
            return "UPDATE 1"
        if s.startswith("UPDATE gift_subscriptions SET status = 'activated'"):
            assert self.in_tx and self.holds_lock, "gift marked activated without the row lock"
            w.gift.update(status="activated", activated_by=args[0], activated_at=args[1])
            return "UPDATE 1"
        if s.startswith("UPDATE users SET"):
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {s}")


class FakePool:
    def __init__(self, world: World):
        self.world = world
        self.conns = []

    def acquire(self):
        conn = TxConn(self.world)
        self.conns.append(conn)

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _Acq()


# ── fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "off")
    monkeypatch.delenv(EP_VAR, raising=False)
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda *a, **k: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "gift")
    assert provisioning_flags.is_on("gift")


@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


@pytest.fixture
def jobs(monkeypatch, panel):
    return FakeJobs(panel).install(monkeypatch)


@pytest.fixture
def db(monkeypatch):
    return FakeDB().install(monkeypatch)


@pytest.fixture
def alerts(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", mock)
    return mock


@pytest.fixture
def legacy(monkeypatch):
    """Legacy panel calls that must NOT run on the outbox path."""
    env = SimpleNamespace(renew_bg=MagicMock(), provision=AsyncMock(), sync=AsyncMock())
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", env.renew_bg)
    monkeypatch.setattr(purchase_flow, "provision_subscription", env.provision)
    monkeypatch.setattr(purchase_flow, "sync_renewal_to_remnawave", env.sync)
    return env


@pytest.fixture
def world(monkeypatch, jobs, db, alerts, legacy):
    w = World(jobs, db)
    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(config, "VPN_ENABLED", True)  # Phase 1 would run on the legacy path
    pool = w.pool = FakePool(w)

    async def get_pool():
        return pool

    async def grant_access(*, telegram_id, duration, source, conn=None,
                           _caller_holds_transaction=False, tariff="basic", defer_panel=False,
                           pre_provisioned_uuid=None, **kw):
        w.grant_calls.append({
            "tg": telegram_id, "days": duration.days, "source": source, "tariff": tariff,
            "defer": defer_panel, "holds": _caller_holds_transaction,
            "in_tx": conn.is_in_transaction() if conn is not None else None,
            "pre": pre_provisioned_uuid, "extra": kw,
        })
        await asyncio.sleep(0)  # let a racing activation interleave
        if w.fail_grant:
            raise RuntimeError("db boom")
        now = utcnow()
        sub = w.db.subs.get(telegram_id)
        if sub and sub["status"] == "active" and sub["expires_at"] > now:
            end = sub["expires_at"] + duration
            sub.update(expires_at=end, subscription_type=tariff)
            return {"subscription_end": end, "action": "renewal", "deferred": True}
        end = now + duration
        w.db.subs[telegram_id] = {
            "telegram_id": telegram_id, "uuid": None, "status": "active", "expires_at": end,
            "subscription_type": tariff, "activation_status": "pending",
        }
        return {"subscription_end": end, "action": "pending_activation", "deferred": True}

    async def get_subscription(tg):
        sub = w.db.subs.get(tg)
        return dict(sub) if sub and sub["status"] == "active" and sub["expires_at"] > utcnow() else None

    async def complete_activation(tg, *, vpn_key, vpn_key_plus, uuid=None, conn=None):
        sub = w.db.subs.get(tg)
        if not sub or sub.get("activation_status") != "pending":
            return False
        sub.update(activation_status="active", vpn_key=vpn_key, vpn_key_plus=vpn_key_plus)
        return True

    monkeypatch.setattr(database, "get_pool", get_pool)
    monkeypatch.setattr(db_admin, "get_pool", get_pool)
    monkeypatch.setattr(database, "grant_access", grant_access)
    monkeypatch.setattr(db_subs, "grant_access", grant_access)
    monkeypatch.setattr(database, "get_subscription", get_subscription)
    monkeypatch.setattr(database, "complete_activation", complete_activation)
    return w


@pytest.fixture
def start_env(monkeypatch):
    monkeypatch.setattr(database, "get_user", AsyncMock(return_value={"language": "ru", "referral_code": "R1"}))
    monkeypatch.setattr(start_mod, "safe_resolve_username", lambda *a, **k: "user")
    monkeypatch.setattr(start_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(start_mod, "get_main_menu_keyboard", AsyncMock(return_value=None))


def seed_active(world: World, panel: FakePanel, tg=TG, *, days_left=10, bypass_gb=None):
    end = (utcnow() + timedelta(days=days_left)).replace(microsecond=0)
    world.db.subs[tg] = {
        "telegram_id": tg, "uuid": None, "status": "active", "expires_at": end,
        "subscription_type": "basic", "activation_status": "active",
    }
    panel.seed_premium(tg, end)
    if bypass_gb is not None:
        panel.seed_bypass(tg, bypass_gb * GIB)
    return end


async def start_gift(code=CODE, tg=TG):
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"), text=f"/start gift_{code}",
        from_user=SimpleNamespace(id=tg, language_code="ru", username="user", first_name="U"),
        answer=AsyncMock(),
    )
    await start_mod.cmd_start(message, SimpleNamespace(clear=AsyncMock()))
    return message


def sent(message) -> str:
    return message.answer.await_args.args[0]


def activated_text(tariff_name: str, period_days: int) -> str:
    months = period_days // 30
    if months == 1:
        period = i18n_get_text("ru", "buy.period_text_1", "1 месяц")
    elif months in (2, 3, 4):
        period = i18n_get_text("ru", "buy.period_text_2_4", "{months} месяца", months=months)
    else:
        period = i18n_get_text("ru", "buy.period_text_5_plus", "{months} месяцев", months=months)
    return i18n_get_text("ru", "gift.activated", tariff_name=tariff_name, period=period)


def only_job(world: World):
    assert len(world.jobs.rows) == 1, world.jobs.rows
    return next(iter(world.jobs.rows.values()))


def assert_outbox_grant(call, *, days, tariff="basic", tg=TG):
    assert call["defer"] is True and call["holds"] is True and call["in_tx"] is True
    assert call["source"] == "gift" and call["days"] == days and call["tariff"] == tariff
    assert call["tg"] == tg and call["pre"] is None


def assert_no_legacy_panel_calls(world: World, legacy):
    legacy.renew_bg.assert_not_called()
    legacy.provision.assert_not_awaited()
    legacy.sync.assert_not_awaited()
    phase1 = [c for conn in world.pool.conns for c in conn.calls if "FROM subscriptions" in c[1]]
    assert phase1 == []


# ── flag ON ────────────────────────────────────────────────────────────

async def test_gift_basic_90d_new_recipient_premium_90d_and_10gb_once(
        flag_on, world, panel, start_env, legacy):
    world.seed_gift(tariff="basic", period_days=90)
    message = await start_gift()

    assert sent(message) == activated_text("Basic", 90)
    job = only_job(world)
    assert job["idempotency_key"] == f"gift:{CODE}" and job["source"] == "gift"
    assert job["tariff_key"] == "basic" and job["bypass_add_bytes"] == 10 * GIB
    assert job["context"]["code"] == CODE and job["context"]["gifter"] == GIFTER
    assert job["context"]["recipient"] == TG and job["context"]["premium_days"] == 90
    assert job["status"] == "done"
    assert_outbox_grant(world.grant_calls[0], days=90)
    sub = world.db.subs[TG]
    assert abs(sub["expires_at"] - (utcnow() + timedelta(days=90))) < timedelta(seconds=10)
    assert job["premium_until"] == sub["expires_at"]
    assert panel.premium_expire(TG) == ceil_s(sub["expires_at"])
    assert panel.bypass_limit(TG) == 10 * GIB
    assert sub["activation_status"] == "active"
    assert world.gift["status"] == "activated" and world.gift["activated_by"] == TG
    assert_no_legacy_panel_calls(world, legacy)


async def test_gift_to_active_recipient_extends_premium_90d_in_panel_plus_10gb(
        flag_on, world, panel, start_env, legacy):
    end0 = seed_active(world, panel, bypass_gb=3)
    world.seed_gift(tariff="basic", period_days=90)
    message = await start_gift()

    assert sent(message) == activated_text("Basic", 90)
    job = only_job(world)
    assert job["status"] == "done" and job["premium_until"] == end0 + timedelta(days=90)
    assert panel.premium_expire(TG) == ceil_s(end0 + timedelta(days=90))
    assert panel.bypass_limit(TG) == 13 * GIB
    assert_outbox_grant(world.grant_calls[0], days=90)
    assert_no_legacy_panel_calls(world, legacy)


@pytest.mark.parametrize("tariff,tariff_key", [("plus", "plus"), ("biz_starter", "plus")])
async def test_gift_plus_and_legacy_biz_get_plus_entitlement(
        flag_on, world, panel, start_env, tariff, tariff_key):
    """Gifts are sold as basic/plus only (callbacks/gift.py); a legacy biz row renews as Plus."""
    world.seed_gift(tariff=tariff, period_days=30)
    await start_gift()
    job = only_job(world)
    assert job["tariff_key"] == tariff_key and job["context"]["premium_tier"] == "plus"
    assert job["bypass_add_bytes"] == 10 * GIB
    assert world.grant_calls[0]["tariff"] == tariff  # grant_access keeps the gift's tariff
    assert panel.bypass_limit(TG) == 10 * GIB


async def test_same_code_twice_grants_once(flag_on, world, panel, start_env):
    world.seed_gift()
    first = await start_gift()
    second = await start_gift()

    assert sent(first) == activated_text("Basic", 90)
    assert sent(second) == i18n_get_text("ru", "gift.error_already_activated")
    assert len(world.jobs.rows) == 1 and len(world.grant_calls) == 1
    assert panel.bypass_limit(TG) == 10 * GIB


async def test_two_users_racing_on_one_code_get_one_grant(flag_on, world, panel):
    """Both pass the unlocked pre-check; the row lock lets only the first one grant."""
    world.seed_gift()
    r1, r2 = await asyncio.gather(
        database.activate_gift_subscription(CODE, TG),
        database.activate_gift_subscription(CODE, TG2),
    )
    results = sorted([r1, r2], key=lambda r: not r["success"])
    assert results[0]["success"] is True and results[0]["outbox"] is True
    assert results[1] == {"success": False, "error": "already_activated"}
    assert len(world.grant_calls) == 1 and len(world.jobs.rows) == 1
    winner = world.grant_calls[0]["tg"]
    assert world.gift["activated_by"] == winner
    assert only_job(world)["telegram_id"] == winner
    loser = TG2 if winner == TG else TG
    assert panel.bypass_limit(winner) == 10 * GIB and panel.bypass_limit(loser) is None


async def test_same_user_double_click_concurrent_grants_once(flag_on, world, panel):
    world.seed_gift()
    r1, r2 = await asyncio.gather(
        database.activate_gift_subscription(CODE, TG),
        database.activate_gift_subscription(CODE, TG),
    )
    assert sorted([r1["success"], r2["success"]]) == [False, True]
    assert len(world.grant_calls) == 1 and len(world.jobs.rows) == 1
    assert panel.bypass_limit(TG) == 10 * GIB


@pytest.mark.parametrize("flag", ["on", "off"])
@pytest.mark.parametrize("case,error_key", [
    ("not_found", "gift.error_not_found"),
    ("activated", "gift.error_already_activated"),
    ("expired", "gift.error_expired"),
    ("self", "gift.error_self_activation"),
    ("pending", "gift.error_invalid"),
])
async def test_invalid_used_expired_code_no_job_same_texts(
        world, panel, start_env, legacy, monkeypatch, flag, case, error_key):
    if flag == "on":
        monkeypatch.setenv(MODE_VAR, "on")
        monkeypatch.setenv(EP_VAR, "gift")
    if case == "activated":
        world.seed_gift(status="activated")
    elif case == "expired":
        world.seed_gift(expires_in=timedelta(days=-1))
    elif case == "self":
        world.seed_gift(buyer=TG)
    elif case == "pending":
        world.seed_gift(status="pending")
    message = await start_gift()

    assert sent(message) == i18n_get_text("ru", error_key)
    assert world.jobs.rows == {} and world.grant_calls == []
    assert panel.premium_expire(TG) is None and panel.bypass_limit(TG) is None
    if case == "expired":
        assert world.gift["status"] == "expired"


async def test_panel_down_job_pending_alert_user_success_text(
        flag_on, world, panel, start_env, alerts, monkeypatch):
    bot = object()
    monkeypatch.setattr(payment_webhook, "_bot", bot)
    panel.mode = "down"
    world.seed_gift()
    message = await start_gift()

    assert sent(message) == activated_text("Basic", 90)
    job = only_job(world)
    assert job["status"] == "pending" and job["attempts"] == 1
    assert world.gift["status"] == "activated"  # activation is durable
    alerts.assert_awaited_once()
    assert alerts.await_args.args[0] is bot and alerts.await_args.kwargs["force"] is True
    assert world.db.payment_errors[0]["purchase_id"] == f"gift:{CODE}"

    panel.mode = "ok"  # the worker's retry finishes the job, exactly once
    assert await provisioning.run_now(job["id"]) is True
    assert panel.premium_expire(TG) == ceil_s(world.db.subs[TG]["expires_at"])
    assert panel.bypass_limit(TG) == 10 * GIB
    assert world.db.subs[TG]["activation_status"] == "active"


async def test_tx_failure_keeps_gift_paid_and_enqueues_nothing(
        flag_on, world, panel, start_env, monkeypatch):
    world.seed_gift()

    async def boom(*a, **k):
        raise RuntimeError("insert failed")
    monkeypatch.setattr(pj, "insert_job", boom)
    message = await start_gift()

    assert sent(message) == i18n_get_text("ru", "gift.error_invalid")
    assert world.gift["status"] == "paid" and world.jobs.rows == {}
    assert TG not in world.db.subs
    # the code is still redeemable once the DB recovers
    monkeypatch.setattr(pj, "insert_job", world.jobs.insert_job)
    message = await start_gift()
    assert sent(message) == activated_text("Basic", 90)
    assert panel.bypass_limit(TG) == 10 * GIB


# ── flag OFF: legacy path (T0 harness) ─────────────────────────────────

async def test_flag_off_gift_to_active_recipient_extends_premium_in_panel(monkeypatch):
    """Behaviour fix. Before it: DB expires_at +3 months, panel premium expireAt unchanged."""
    from app.services.tariffs import extend_expiry
    w = h.install(monkeypatch)
    w.seed_active_subscription(days_left=10)
    premium_before = w.panel.premium_expire[h.TG]

    await h.run_gift_activation(w, monkeypatch, period_days=90)

    db_end = h.aware(w.sub["expires_at"])
    # a paid gift period is calendar months (owner decision 2026-09-14)
    assert abs(db_end - extend_expiry(premium_before, 90)) < timedelta(seconds=5)
    assert w.panel.premium_expire[h.TG] == db_end
    assert w.panel.added_gb(h.TG) == 10  # legacy /start renew_remnawave_user_bg


async def test_flag_off_uses_legacy_path_and_enqueues_nothing(monkeypatch):
    enqueue = AsyncMock(side_effect=AssertionError("outbox used with the flag off"))
    run_now = AsyncMock(side_effect=AssertionError("outbox used with the flag off"))
    monkeypatch.setattr(provisioning, "enqueue", enqueue)
    monkeypatch.setattr(provisioning, "run_now", run_now)
    w = h.install(monkeypatch)

    message = await h.run_gift_activation(w, monkeypatch, period_days=90)

    assert message.answer.await_count == 1
    assert message.answer.await_args.args[0].startswith(h.text_prefix(w, "gift.activated"))
    call = w.grant_calls[0]
    assert call["defer_panel"] is False and call["caller_holds_tx"] is True
    assert call["pre_provisioned"] and call["pre_provisioned"]["uuid"]  # Phase 1 ran
    assert w.panel.added_gb(h.TG) == 20  # unchanged legacy number (T0 xfail: target 10)
    enqueue.assert_not_awaited()
    run_now.assert_not_awaited()
    assert message.answer.await_count == 1
