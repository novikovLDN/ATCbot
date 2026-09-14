"""T13 — admin grants via the provisioning outbox (entrypoint "admin").

Spec: docs/audit/02_payment_core_plan.md §A flow 6, §G0 (day grants = premium only,
0 GB), task T13.

Real code: the dashboard grant endpoints (the bot admin panel was removed) →
database.admin_grant_access_atomic / admin_grant_access_minutes_atomic →
provisioning.enqueue / run_now → provisioning.apply, and the T5 worker tick.
Fakes: T0 harness World (asyncpg, grant_access contract fake with defer_panel),
tests/fakes/panel.FakePanel (Remnawave), tests/fakes/provisioning.FakeJobs
(outbox). Legacy panel entry points are sentinels that must stay uncalled.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
from app.api import payment_webhook
from app.services import admin_alerts, provisioning, purchase_flow, remnawave_service, tariffs
from app.services.tariffs import Entitlement, TariffConfigError
from app.workers import provisioning_worker as worker
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeJobs
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG

GIB = 1024 ** 3
BOT = object()
ADMIN = config.ADMIN_TELEGRAM_ID
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "admin")
    w = h.install(monkeypatch)
    w.conn.is_in_transaction = lambda: True  # harness conn = the caller's open tx
    panel = FakePanel().install(monkeypatch)
    jobs = FakeJobs(panel).install(monkeypatch)

    async def complete_activation(tg, *, vpn_key, vpn_key_plus=None, uuid=None, conn=None):
        if not w.sub or w.sub.get("activation_status") != "pending":
            return False
        w.sub.update(activation_status="active", vpn_key=vpn_key, vpn_key_plus=vpn_key_plus, uuid=uuid)
        return True

    monkeypatch.setattr(database, "complete_activation", complete_activation)
    monkeypatch.setattr(database, "get_remnawave_premium_id", AsyncMock(return_value=None))
    payment_errors = AsyncMock(return_value=1)
    monkeypatch.setattr(database, "log_payment_error", payment_errors)
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", alerts)
    monkeypatch.setattr(payment_webhook, "_bot", BOT)

    legacy = SimpleNamespace(
        renew_bg=MagicMock(), add_bypass=AsyncMock(return_value=True),
        provision=AsyncMock(side_effect=AssertionError("Phase-1 provisioning must not run")),
        sync=AsyncMock(side_effect=AssertionError("legacy renewal sync must not run")),
    )
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", legacy.renew_bg)
    monkeypatch.setattr(remnawave_service, "add_bypass_traffic", legacy.add_bypass)
    monkeypatch.setattr(purchase_flow, "provision_subscription", legacy.provision)
    monkeypatch.setattr(purchase_flow, "sync_renewal_to_remnawave", legacy.sync)
    yield SimpleNamespace(w=w, panel=panel, jobs=jobs, alerts=alerts, legacy=legacy,
                          payment_errors=payment_errors)
    assert provisioning._USER_LOCKS == {}, "user locks must be released"


def seed_active(e, *, bypass_gb=3, days_left=10, tariff="basic"):
    e.w.seed_active_subscription(TG, tariff=tariff, bypass_gb=bypass_gb, days_left=days_left)
    e.panel.seed_premium(TG, h.aware(e.w.sub["expires_at"]))
    e.panel.seed_bypass(TG, bypass_gb * GIB)


def only_job(e):
    assert len(e.jobs.rows) == 1
    return next(iter(e.jobs.rows.values()))


def assert_no_legacy_panel_work(e):
    e.legacy.renew_bg.assert_not_called()
    e.legacy.add_bypass.assert_not_awaited()
    e.legacy.provision.assert_not_awaited()
    e.legacy.sync.assert_not_awaited()
    assert e.w.panel.calls == []  # the harness (legacy) panel is untouched
    assert e.w.background == []


def assert_admin_job(job, *, premium_until):
    assert job["idempotency_key"].startswith("admin:") and len(job["idempotency_key"]) > len("admin:")
    assert (job["source"], job["tariff_key"], job["bypass_add_bytes"]) == ("admin", "grant", 0)
    assert job["premium_until"] == premium_until
    assert job["context"]["admin_id"] == ADMIN


def assert_premium_at(e, until):
    """Panel expireAt = the exact target, rounded up to the second (panel precision)."""
    got = e.panel.premium_expire(TG)
    assert until.replace(microsecond=0) <= got <= until + timedelta(seconds=1)


# ── flex handler runner (days / hours / minutes) ─────────────────────────

def _yield_mock():
    async def _yield(*_a, **_k):
        await asyncio.sleep(0)
    return AsyncMock(side_effect=_yield)


async def dashboard_grant(w, monkeypatch, *, days=None, minutes=None, tariff="basic", tg=TG):
    """The admin grant entry point: dashboard POST /users/{tg}/grant(-minutes)."""
    from app.api.dashboard.routes import users as routes_mod
    monkeypatch.setattr(routes_mod.bus, "publish", MagicMock())
    admin = {"sub": str(ADMIN)}
    if minutes is not None:
        resp = await routes_mod.user_grant_minutes(
            telegram_id=tg, body=routes_mod.GrantMinutesRequest(minutes=minutes), admin=admin)
    else:
        resp = await routes_mod.user_grant(
            telegram_id=tg, body=routes_mod.GrantRequest(days=days, tariff=tariff), admin=admin)
    await w.drain_background()
    return resp


# ═══════════════════════════════════════════════════════════════════════
# tariffs.for_grant_duration (pure)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tariff,duration,days,tier", [
    ("basic", timedelta(days=7), 7, "basic"),
    ("plus", timedelta(minutes=10), 1, "plus"),
    ("basic", timedelta(hours=23, minutes=59), 1, "basic"),
    ("basic", timedelta(hours=25), 2, "basic"),
    ("basic", timedelta(days=1, microseconds=1), 2, "basic"),
    ("combo_plus", timedelta(days=1), 1, "plus"),
])
def test_for_grant_duration_premium_only_zero_gb(tariff, duration, days, tier):
    assert tariffs.for_grant_duration(tariff, duration) == Entitlement("grant", days, tier, 0)


def test_for_grant_duration_whole_days_equals_for_grant():
    assert tariffs.for_grant_duration("plus", timedelta(days=30)) == tariffs.for_grant("plus", 30)


@pytest.mark.parametrize("tariff,duration", [
    ("basic", timedelta(0)), ("basic", timedelta(minutes=-5)), ("basic", 7), ("basic", None),
    ("gold", timedelta(days=1)),
])
def test_for_grant_duration_rejects_invalid(tariff, duration):
    with pytest.raises(TariffConfigError):
        tariffs.for_grant_duration(tariff, duration)


# ═══════════════════════════════════════════════════════════════════════
# Flag ON: premium only, 0 GB
# ═══════════════════════════════════════════════════════════════════════

async def test_admin_grant_7d_new_user_premium_7d_zero_gb(env, monkeypatch):
    e = env
    t0 = h.utcnow()

    resp = await dashboard_grant(e.w, monkeypatch, days=7)

    assert resp["ok"] is True
    job = only_job(e)
    premium_until = h.aware(e.w.sub["expires_at"])
    assert timedelta(days=7) <= premium_until - t0 < timedelta(days=7, seconds=5)
    assert_admin_job(job, premium_until=premium_until)
    assert job["status"] == "done" and job["context"]["reason"] == "admin_grant_days"
    assert_premium_at(e, premium_until)
    assert e.panel.bypass_limit(TG) is None          # 0 GB: no bypass entity created
    assert e.w.sub["activation_status"] == "active" and e.w.sub["vpn_key"]
    grant = e.w.grant_calls[0]
    assert (grant["source"], grant["defer_panel"], grant["caller_holds_tx"], grant["pre_provisioned"]) == \
        ("admin", True, True, None)
    assert grant["duration"] == timedelta(days=7)
    assert_no_legacy_panel_work(e)
    e.alerts.assert_not_awaited()


async def test_admin_grant_7d_renewal_extends_premium_zero_gb(env, monkeypatch):
    e = env
    seed_active(e, bypass_gb=3, days_left=10)
    before = h.aware(e.w.sub["expires_at"])

    resp = await dashboard_grant(e.w, monkeypatch, days=7, tariff="plus")

    assert resp["ok"] is True
    job = only_job(e)
    assert job["premium_until"] == before + timedelta(days=7)
    assert job["context"]["premium_tier"] == "plus"
    assert_admin_job(job, premium_until=before + timedelta(days=7))
    assert job["status"] == "done"
    assert_premium_at(e, before + timedelta(days=7))
    assert e.panel.bypass_limit(TG) == 3 * GIB       # GB untouched
    assert_no_legacy_panel_work(e)


async def test_admin_grant_300_minutes_exact_premium_until_zero_gb(env, monkeypatch):
    e = env
    t0 = h.utcnow()

    resp = await dashboard_grant(e.w, monkeypatch, minutes=300)

    assert resp["ok"] is True
    grant = e.w.grant_calls[0]
    assert grant["duration"] == timedelta(minutes=300)   # not rounded to a day
    job = only_job(e)
    premium_until = h.aware(e.w.sub["expires_at"])
    assert timedelta(minutes=300) <= premium_until - t0 < timedelta(minutes=300, seconds=5)
    assert_admin_job(job, premium_until=premium_until)
    assert job["context"]["reason"] == "admin_grant_minutes"
    assert job["context"]["duration_s"] == 300 * 60
    assert job["status"] == "done"
    assert_premium_at(e, premium_until)
    assert e.panel.bypass_limit(TG) is None
    assert_no_legacy_panel_work(e)


async def test_admin_grant_minutes_atomic_returns_key_after_run_now(env):
    e = env

    expires_at, vpn_key = await database.admin_grant_access_minutes_atomic(TG, 45, ADMIN)

    assert expires_at == h.aware(e.w.sub["expires_at"])
    assert vpn_key and vpn_key == e.w.sub["vpn_key"]   # completed by run_now
    assert only_job(e)["status"] == "done"


async def test_two_grants_get_distinct_keys_and_two_extensions(env):
    """Separate admin actions are separate grants (uuid4 key per action)."""
    e = env
    seed_active(e)
    before = h.aware(e.w.sub["expires_at"])

    await database.admin_grant_access_atomic(TG, 7, ADMIN)
    await database.admin_grant_access_atomic(TG, 7, ADMIN)

    keys = {r["idempotency_key"] for r in e.jobs.rows.values()}
    assert len(keys) == 2
    assert all(r["status"] == "done" for r in e.jobs.rows.values())
    assert_premium_at(e, before + timedelta(days=14))
    assert e.panel.bypass_limit(TG) == 3 * GIB


# ═══════════════════════════════════════════════════════════════════════
# Panel down: the grant is committed, provisioning is queued
# ═══════════════════════════════════════════════════════════════════════

async def test_panel_down_grant_committed_job_pending_alert_then_worker(env, monkeypatch):
    e = env
    e.panel.mode = "down"

    resp = await dashboard_grant(e.w, monkeypatch, days=7)

    assert resp["ok"] is True
    assert len(e.w.grant_calls) == 1
    assert e.w.sub["activation_status"] == "pending"
    job = only_job(e)
    assert (job["status"], job["attempts"]) == ("pending", 1)
    assert e.alerts.await_count == 1 and e.alerts.await_args.kwargs.get("force") is True
    assert e.alerts.await_args.args[0] is BOT
    assert e.payment_errors.await_args.kwargs["purchase_id"] == job["idempotency_key"]
    assert e.panel.premium_expire(TG) is None

    e.panel.mode = "ok"
    e.jobs.make_due(job["id"])
    s = await worker.run_tick(BOT)

    assert (s.processed, s.done) == (1, 1)
    assert job["status"] == "done"
    assert_premium_at(e, job["premium_until"])
    assert e.panel.bypass_limit(TG) is None
    assert e.w.sub["activation_status"] == "active" and e.w.sub["vpn_key"]
    assert len(e.w.grant_calls) == 1
    assert_no_legacy_panel_work(e)


# ═══════════════════════════════════════════════════════════════════════
# Dashboard endpoints: same semantics
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def routes(monkeypatch):
    from app.api.dashboard.routes import users as routes_mod
    publish = MagicMock()
    monkeypatch.setattr(routes_mod.bus, "publish", publish)
    return SimpleNamespace(mod=routes_mod, publish=publish)


async def test_dashboard_grant_days_premium_only_zero_gb(env, routes):
    e = env
    seed_active(e, bypass_gb=3)
    before = h.aware(e.w.sub["expires_at"])

    resp = await routes.mod.user_grant(
        telegram_id=TG, body=routes.mod.GrantRequest(days=7, tariff="plus"), admin={"sub": str(ADMIN)},
    )

    assert resp["ok"] is True and resp["vpn_key"]
    assert resp["expires_at"] == (before + timedelta(days=7)).isoformat()
    job = only_job(e)
    assert_admin_job(job, premium_until=before + timedelta(days=7))
    assert job["status"] == "done"
    assert_premium_at(e, before + timedelta(days=7))
    assert e.panel.bypass_limit(TG) == 3 * GIB
    routes.publish.assert_called_once()
    assert_no_legacy_panel_work(e)


async def test_dashboard_grant_minutes_exact_premium_zero_gb(env, routes):
    e = env
    t0 = h.utcnow()

    resp = await routes.mod.user_grant_minutes(
        telegram_id=TG, body=routes.mod.GrantMinutesRequest(minutes=90), admin={"sub": str(ADMIN)},
    )

    premium_until = h.aware(e.w.sub["expires_at"])
    assert timedelta(minutes=90) <= premium_until - t0 < timedelta(minutes=90, seconds=5)
    assert resp["ok"] is True and resp["expires_at"] == premium_until.isoformat()
    assert resp["vpn_key"] == e.w.sub["vpn_key"]
    assert_admin_job(only_job(e), premium_until=premium_until)
    assert_premium_at(e, premium_until)
    assert e.panel.bypass_limit(TG) is None
    assert_no_legacy_panel_work(e)


async def test_dashboard_grant_panel_down_still_ok_job_queued(env, routes):
    e = env
    e.panel.mode = "down"

    resp = await routes.mod.user_grant(
        telegram_id=TG, body=routes.mod.GrantRequest(days=7), admin={"sub": str(ADMIN)},
    )

    assert resp["ok"] is True and resp["vpn_key"] == ""  # pending until the worker applies
    assert only_job(e)["status"] == "pending"
    assert e.alerts.await_count == 1


# ═══════════════════════════════════════════════════════════════════════
# Flag not ON → legacy path
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode,entrypoints", [("off", "admin"), ("on", "balance,webhook"), ("shadow", "")],
                         ids=["flag_off", "admin_not_listed", "shadow"])
async def test_flag_not_on_for_admin_keeps_legacy_path(monkeypatch, mode, entrypoints):
    monkeypatch.setenv(MODE_VAR, mode)
    monkeypatch.setenv(EP_VAR, entrypoints)
    w = h.install(monkeypatch)
    jobs = FakeJobs(FakePanel()).install(monkeypatch)
    enqueue = AsyncMock(side_effect=AssertionError("outbox must not be used"))
    monkeypatch.setattr(provisioning, "enqueue", enqueue)
    renew_bg = MagicMock()
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", renew_bg)

    resp = await dashboard_grant(w, monkeypatch, days=7)

    assert resp["ok"] is True
    assert jobs.rows == {}
    enqueue.assert_not_awaited()
    grant = w.grant_calls[0]
    assert grant["defer_panel"] is False and grant["pre_provisioned"] is not None  # Phase 1 ran
