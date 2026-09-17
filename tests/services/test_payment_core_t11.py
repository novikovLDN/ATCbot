"""T11 — Platega recurring charges via the provisioning outbox (flag
USE_NEW_PROVISIONING=on, entrypoint "platega_recurring").
Spec: docs/audit/02_payment_core_plan.md §A flow 5, §B, task T11.

Flag on, CONFIRMED charge: claim_charge → ONE transaction
  confirm_charge(conn) + grant_access(defer_panel) + provisioning.enqueue
→ commit → provisioning.run_now.

Fakes: tests/fakes/panel.FakePanel (Remnawave), tests/fakes/provisioning
FakeJobs/FakeDB (outbox + subscription cache), an in-memory charge store and a
connection whose transaction() rolls every fake table back on an exception.
Flag off: tests/services/test_platega_wave1.py (unchanged) + the routing tests
below; the T0 xfail test test_platega_recurring_confirmed_charge_basic_adds_10gb
runs with the flag off and stays xfail.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
import database.platega_subscriptions as psub
import platega_service
from app.api import payment_webhook
from app.services import admin_alerts, provisioning, sub_aggregator
from app.services import provisioning_flags as pf
from app.services.payments.confirmation import TransientPaymentError
from app.workers import provisioning_worker
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeDB, FakeJobs

GIB = 1024 ** 3
TG = 515151
SUB_ID = "psub-t11"
MERCHANT = "merchant-t11"
SECRET = "secret-t11"
HEADERS = {"x-merchantid": MERCHANT, "x-secret": SECRET}
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"
LEGACY_UUID = "11111111-2222-4333-8444-555555555555"


# ── fakes ──────────────────────────────────────────────────────────────

class ChargeStore:
    """database.platega_subscriptions with the SQL's semantics."""

    def __init__(self):
        self.charges = {}
        self.counters = {"charges_success": 0, "total_amount_kopecks": 0}
        self.sub_row = {
            "subscription_id": SUB_ID, "telegram_id": TG, "tariff_type": "basic",
            "interval_days": 30, "amount_kopecks": 19900, "is_combo": False,
        }
        self.stale = False                 # a PROCESSING row is older than the stale timeout
        self.parallel_commit_on_reclaim = False
        self.confirm_conns = []
        self.update_subscription_status = AsyncMock()

    async def get_subscription(self, subscription_id):
        return dict(self.sub_row) if self.sub_row else None

    async def record_charge(self, charge_id, subscription_id, telegram_id, amount_kopecks, status):
        if charge_id in self.charges:
            return False
        self.charges[charge_id] = str(status).upper()
        return True

    async def claim_charge(self, charge_id, subscription_id, telegram_id, amount_kopecks,
                           stale_after_seconds=240.0):
        st = self.charges.get(charge_id)
        if st is None:
            self.charges[charge_id] = "PROCESSING"
            return "claimed"
        if st == "PROCESSING" and self.stale:
            self.stale = False
            if self.parallel_commit_on_reclaim:
                # The "crashed" attempt was only slow: it commits right after our re-claim.
                self.charges[charge_id] = "CONFIRMED"
                self.counters["charges_success"] += 1
            return "reclaimed"
        return "in_progress" if st == "PROCESSING" else "done"

    async def confirm_charge(self, charge_id, subscription_id, amount_kopecks, conn=None):
        self.confirm_conns.append(conn)
        if conn is not None:
            assert conn.is_in_transaction(), "confirm_charge(conn=…) must run in the caller's tx"
        if self.charges.get(charge_id) != "PROCESSING":
            return False
        self.charges[charge_id] = "CONFIRMED"
        self.counters["charges_success"] += 1
        self.counters["total_amount_kopecks"] += int(amount_kopecks)
        return True

    async def release_charge(self, charge_id):
        if self.charges.get(charge_id) == "PROCESSING":
            del self.charges[charge_id]

    def install(self, monkeypatch):
        for name in ("get_subscription", "record_charge", "claim_charge", "confirm_charge",
                     "release_charge", "update_subscription_status"):
            monkeypatch.setattr(psub, name, getattr(self, name))
        return self


class TxConn:
    """asyncpg-like connection: transaction() snapshots every fake table and
    restores it when the block raises (rollback)."""

    def __init__(self, world):
        self.world = world
        self.in_tx = False
        self.calls = []

    def is_in_transaction(self):
        return self.in_tx

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                assert not conn.in_tx
                conn.in_tx = True
                self.snap = conn.world.snapshot()
                return self

            async def __aexit__(self, exc_type, exc, tb):
                conn.in_tx = False
                if exc_type is not None:
                    conn.world.restore(self.snap)
                    conn.world.rollbacks += 1
                else:
                    conn.world.commits += 1
                return False

        return _Tx()


class Pool:
    def __init__(self, world):
        self.world = world

    def acquire(self):
        world = self.world

        class _Acq:
            async def __aenter__(self):
                self.conn = TxConn(world)
                world.conns.append(self.conn)
                return self.conn

            async def __aexit__(self, *exc):
                return False

        return _Acq()


class World:
    def __init__(self, monkeypatch):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.panel = FakePanel().install(monkeypatch)
        self.jobs = FakeJobs(self.panel).install(monkeypatch)
        self.db = FakeDB().install(monkeypatch)
        self.store = ChargeStore().install(monkeypatch)
        self.conns = []
        self.commits = 0
        self.rollbacks = 0
        self.grant_calls = []
        self.fail_grant = 0
        self.fail_enqueue = 0
        self.alerts = []
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()

        async def get_pool():
            return Pool(self)

        monkeypatch.setattr(database, "get_pool", get_pool)
        monkeypatch.setattr(database, "grant_access", self.grant_access)
        monkeypatch.setattr(database, "DB_READY", True, raising=False)

        real_insert = self.jobs.insert_job

        async def insert_job(conn, **kw):
            if self.fail_enqueue:
                self.fail_enqueue -= 1
                raise ConnectionError("outbox insert failed")
            return await real_insert(conn, **kw)

        import database.provisioning_jobs as pj
        monkeypatch.setattr(pj, "insert_job", insert_job)

        async def send_alert(bot, category, message, *, force=False):
            self.alerts.append(SimpleNamespace(category=category, message=message, force=force))
            return True

        monkeypatch.setattr(admin_alerts, "send_alert", send_alert)
        monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda *_a, **_k: None)
        monkeypatch.setattr(payment_webhook, "_bot", None)
        monkeypatch.setattr(platega_service, "PLATEGA_MERCHANT_ID", MERCHANT)
        monkeypatch.setattr(platega_service, "PLATEGA_SECRET", SECRET)

    # existing paying user: active sub, premium until +10 d, bypass 3 GB
    def seed_active_user(self, *, bypass_gb=3, days_left=10):
        self.expires_before = self.now + timedelta(days=days_left)
        self.db.subs[TG] = {
            "telegram_id": TG, "status": "active", "uuid": LEGACY_UUID,
            "expires_at": self.expires_before, "subscription_type": "basic",
            "activation_status": "active",
        }
        self.panel.seed_premium(TG, self.expires_before)
        self.panel.seed_bypass(TG, bypass_gb * GIB)

    def snapshot(self):
        return copy.deepcopy((self.store.charges, self.store.counters, self.db.subs, self.jobs.rows))

    def restore(self, snap):
        for target, saved in zip(
            (self.store.charges, self.store.counters, self.db.subs, self.jobs.rows), snap,
        ):
            target.clear()
            target.update(saved)

    async def grant_access(self, *, telegram_id, duration, source, admin_telegram_id=None,
                           conn=None, _caller_holds_transaction=False, tariff="basic",
                           defer_panel=False, **_kw):
        self.grant_calls.append({
            "telegram_id": telegram_id, "duration": duration, "source": source, "conn": conn,
            "in_tx": bool(conn is not None and conn.is_in_transaction()),
            "caller_holds_tx": _caller_holds_transaction, "defer_panel": defer_panel,
            "tariff": tariff,
            # платный период каталога → календарные месяцы в grant_access
            "tariff_period_days": _kw.get("tariff_period_days"),
        })
        if self.fail_grant:
            self.fail_grant -= 1
            raise RuntimeError("grant_access failed")
        sub = self.db.subs[telegram_id]
        end = max(sub["expires_at"], datetime.now(timezone.utc)) + duration
        sub.update(expires_at=end, subscription_type=tariff)
        result = {"uuid": sub["uuid"], "subscription_end": end, "action": "renewal"}
        if defer_panel:
            result["deferred"] = True
        return result

    def job(self, charge_id="ch-1"):
        rows = [r for r in self.jobs.rows.values() if r["idempotency_key"] == f"platega_charge:{charge_id}"]
        assert len(rows) <= 1
        return rows[0] if rows else None

    def added_gb(self):
        return (self.panel.bypass_limit(TG) - 3 * GIB) / GIB

    def forced_alerts(self):
        return [a for a in self.alerts if a.force]


def charge_body(charge_id="ch-1", amount=199.0, status="CONFIRMED"):
    return {
        "Id": charge_id, "Amount": amount, "Currency": "RUB", "Status": status,
        "PaymentMethod": 6, "SubscriptionId": SUB_ID,
        "Payload": json.dumps({"telegram_id": TG, "tariff": "basic", "days": 30}),
        "NextChargeAt": "2026-10-13T09:10:00Z",
    }


async def webhook(w, **kw):
    return await platega_service.process_subscription_webhook_data(HEADERS, charge_body(**kw), w.bot)


@pytest.fixture(autouse=True)
def _flag_env(monkeypatch):
    monkeypatch.delenv(MODE_VAR, raising=False)
    monkeypatch.delenv(EP_VAR, raising=False)
    pf._warned.clear()


@pytest.fixture
def w(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "platega_recurring")
    world = World(monkeypatch)
    world.seed_active_user()
    return world


# ── flag on: happy paths ───────────────────────────────────────────────

async def test_confirmed_basic_charge_adds_30_days_and_10gb(w):
    result = await webhook(w)

    assert result == {"status": "ok", "event": "charge_confirmed",
                      "subscription_id": SUB_ID, "charge_id": "ch-1"}
    target = w.expires_before + timedelta(days=30)
    assert w.db.subs[TG]["expires_at"] == target
    assert w.panel.premium_expire(TG) == target
    assert w.added_gb() == 10
    job = w.job()
    assert job["status"] == "done"
    assert job["tariff_key"] == "basic" and job["source"] == "platega_recurring"
    assert job["premium_until"] == target
    assert {"subscription_id": SUB_ID, "charge_id": "ch-1", "amount_kopecks": 19900}.items() <= job["context"].items()
    assert w.store.charges["ch-1"] == "CONFIRMED"
    assert w.store.counters == {"charges_success": 1, "total_amount_kopecks": 19900}
    assert not w.alerts
    w.store.update_subscription_status.assert_awaited_once()
    assert w.bot.send_message.await_count == 1


async def test_grant_confirm_and_enqueue_share_one_transaction(w):
    await webhook(w)

    (call,) = w.grant_calls
    assert call["in_tx"] and call["caller_holds_tx"] and call["defer_panel"]
    assert call["source"] == "platega_recurring"
    assert call["duration"] == timedelta(days=30) and call["tariff"] == "basic"
    tx_conn = call["conn"]
    assert w.store.confirm_conns == [tx_conn]
    assert ("insert_job", "platega_charge:ch-1", True) in tx_conn.calls
    assert w.commits == 1 and w.rollbacks == 0


async def test_combo_subscription_adds_combo_gb(w):
    w.store.sub_row.update(is_combo=True, amount_kopecks=32900)

    result = await webhook(w, amount=329.0)

    assert result["event"] == "charge_confirmed"
    assert w.added_gb() == config.COMBO_TARIFFS["combo_basic"][30]["gb"] == 75
    assert w.job()["tariff_key"] == "combo_basic"
    assert w.grant_calls[0]["tariff"] == "basic"
    assert w.panel.premium_expire(TG) == w.expires_before + timedelta(days=30)
    assert not w.alerts


@pytest.mark.parametrize("is_combo", [None, False, "missing"])
async def test_old_subscription_without_is_combo_is_treated_as_non_combo(w, is_combo):
    """§G3 open: old combo subscriptions cannot be told apart — they get basic GB."""
    if is_combo == "missing":
        del w.store.sub_row["is_combo"]  # migration 082 not applied
    else:
        w.store.sub_row["is_combo"] = is_combo

    await webhook(w)

    assert w.added_gb() == 10
    assert w.job()["tariff_key"] == "basic"


async def test_legacy_biz_subscription_renews_as_plus(w):
    w.store.sub_row["tariff_type"] = "biz_starter"

    result = await webhook(w)

    assert result["event"] == "charge_confirmed"
    assert w.job()["tariff_key"] == "plus"
    assert w.added_gb() == 10
    assert w.grant_calls[0]["tariff"] == "biz_starter"  # subscription_type untouched, as today


# ── idempotency ────────────────────────────────────────────────────────

async def test_duplicate_charge_is_a_noop(w):
    first = await webhook(w)
    second = await webhook(w)

    assert first["event"] == "charge_confirmed"
    assert second == {"status": "duplicate"}
    assert len(w.grant_calls) == 1
    assert len(w.jobs.rows) == 1
    assert w.added_gb() == 10
    assert w.db.subs[TG]["expires_at"] == w.expires_before + timedelta(days=30)
    assert w.store.counters["charges_success"] == 1


@pytest.mark.parametrize("fail", ["grant", "enqueue"])
async def test_failure_inside_tx_releases_claim_then_retry_grants_once(w, fail):
    setattr(w, f"fail_{fail}", 1)

    with pytest.raises(TransientPaymentError):
        await webhook(w)

    # Rolled back: no extension, no job, no confirmed charge, claim released.
    assert w.rollbacks == 1 and w.commits == 0
    assert w.db.subs[TG]["expires_at"] == w.expires_before
    assert w.jobs.rows == {}
    assert "ch-1" not in w.store.charges
    assert w.store.counters["charges_success"] == 0
    assert w.panel.bypass_limit(TG) == 3 * GIB
    (alert,) = w.forced_alerts()
    assert "access NOT granted" in alert.message and "ch-1" in alert.message

    retry = await webhook(w)

    assert retry["event"] == "charge_confirmed"
    assert w.db.subs[TG]["expires_at"] == w.expires_before + timedelta(days=30)
    assert w.panel.premium_expire(TG) == w.expires_before + timedelta(days=30)
    assert w.added_gb() == 10
    assert w.store.counters["charges_success"] == 1


async def test_crash_after_commit_then_platega_retry_is_duplicate_and_worker_grants_gb_once(
        w, monkeypatch):
    real_run_now = provisioning.run_now

    async def process_killed(job_id, **_kw):
        raise RuntimeError("process killed right after commit")

    monkeypatch.setattr(provisioning, "run_now", process_killed)
    with pytest.raises(RuntimeError):
        await webhook(w)

    # Committed together: charge confirmed, DB extended, job waiting; panel untouched.
    assert w.store.charges["ch-1"] == "CONFIRMED"
    assert w.db.subs[TG]["expires_at"] == w.expires_before + timedelta(days=30)
    assert w.job()["status"] == "pending"
    assert w.panel.bypass_limit(TG) == 3 * GIB
    assert w.panel.premium_expire(TG) == w.expires_before

    monkeypatch.setattr(provisioning, "run_now", real_run_now)
    retry = await webhook(w)
    assert retry == {"status": "duplicate"}
    assert len(w.grant_calls) == 1

    stats = await provisioning_worker.run_tick(w.bot)
    assert stats.done == 1
    assert w.job()["status"] == "done"
    assert w.added_gb() == 10
    assert w.panel.premium_expire(TG) == w.expires_before + timedelta(days=30)
    assert await provisioning_worker.run_tick(w.bot) == provisioning_worker.TickStats()
    assert w.added_gb() == 10


async def test_stale_reclaim_after_crash_before_commit_grants_once(w):
    """Attempt A claimed and died before commit (nothing committed): the retry re-claims."""
    w.store.charges["ch-1"] = "PROCESSING"
    w.store.stale = True

    result = await webhook(w)

    assert result["event"] == "charge_confirmed"
    assert len(w.grant_calls) == 1
    assert w.added_gb() == 10
    assert w.db.subs[TG]["expires_at"] == w.expires_before + timedelta(days=30)


async def test_stale_reclaim_racing_a_slow_attempt_does_not_double_extend(w):
    """Attempt A was slow, not dead: it commits after our re-claim → our tx sees
    the charge CONFIRMED, rolls back and answers duplicate."""
    w.store.charges["ch-1"] = "PROCESSING"
    w.store.stale = True
    w.store.parallel_commit_on_reclaim = True

    result = await webhook(w)

    assert result == {"status": "duplicate"}
    assert w.grant_calls == []
    assert w.jobs.rows == {}
    assert w.db.subs[TG]["expires_at"] == w.expires_before
    assert w.rollbacks == 1
    assert w.store.counters["charges_success"] == 1


# ── panel down / amount ────────────────────────────────────────────────

async def test_panel_down_returns_200_job_pending_alert_and_worker_finishes(w):
    w.panel.mode = "down"

    result = await webhook(w)

    assert result["event"] == "charge_confirmed"
    assert w.store.charges["ch-1"] == "CONFIRMED"
    assert w.db.subs[TG]["expires_at"] == w.expires_before + timedelta(days=30)
    job = w.job()
    assert job["status"] == "pending" and job["last_error"]
    (alert,) = w.forced_alerts()
    assert "platega_charge:ch-1" in alert.message and "RETRY" in alert.message
    assert w.panel.bypass_limit(TG) == 3 * GIB

    w.panel.mode = "ok"
    w.jobs.make_due(job["id"])
    stats = await provisioning_worker.run_tick(w.bot)

    assert stats.done == 1
    assert w.added_gb() == 10
    assert w.panel.premium_expire(TG) == w.expires_before + timedelta(days=30)


async def test_amount_mismatch_alerts_but_still_grants(w):
    result = await webhook(w, amount=150.0)

    assert result["event"] == "charge_confirmed"
    assert w.added_gb() == 10
    (alert,) = w.forced_alerts()
    assert "differs" in alert.message and "150.00" in alert.message and "199.00" in alert.message


# ── flag off / shadow: legacy path untouched ───────────────────────────

# USE_NEW_PROVISIONING defaults to "on" since the outbox became the normal
# path (DEFAULT_MODE in provisioning_flags), so an unset flag now means the
# outbox — the tests above cover it. The legacy path is left for the explicit
# kill-switch and for an entry-point list that leaves platega_recurring out.
@pytest.mark.parametrize("mode, entrypoints", [
    ("off", None),
    ("shadow", None),
    ("on", "webhook,telegram"),
])
async def test_flag_not_on_uses_the_legacy_path(w, monkeypatch, mode, entrypoints):
    monkeypatch.delenv(MODE_VAR, raising=False)
    monkeypatch.delenv(EP_VAR, raising=False)
    if mode:
        monkeypatch.setenv(MODE_VAR, mode)
    if entrypoints:
        monkeypatch.setenv(EP_VAR, entrypoints)

    result = await webhook(w)

    assert result["event"] == "charge_confirmed"
    (call,) = w.grant_calls
    assert call["conn"] is None and not call["defer_panel"] and not call["caller_holds_tx"]
    assert call["source"] == "platega_subscription"
    assert w.store.confirm_conns == [None]
    assert w.jobs.rows == {} and w.conns == []


# ── DB layer ───────────────────────────────────────────────────────────

class _RecConn:
    def __init__(self, fetchval_results=()):
        self.results = list(fetchval_results)
        self.sql = []

    async def fetchval(self, sql, *args):
        self.sql.append(" ".join(sql.split()))
        return self.results.pop(0)

    async def execute(self, sql, *args):
        self.sql.append(" ".join(sql.split()))


class _RecPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


async def test_db_confirm_charge_with_conn_runs_on_the_callers_connection(monkeypatch):
    monkeypatch.setattr(psub._core, "DB_READY", True)
    monkeypatch.setattr(psub, "get_pool", AsyncMock(side_effect=AssertionError("must not acquire")))

    conn = _RecConn(["c"])
    assert await psub.confirm_charge("c", "s", 100, conn=conn) is True
    assert any("SET status = 'CONFIRMED'" in s for s in conn.sql)
    assert any("charges_success" in s for s in conn.sql)

    conn2 = _RecConn([None])
    assert await psub.confirm_charge("c", "s", 100, conn=conn2) is False
    assert not any("charges_success" in s for s in conn2.sql)


@pytest.fixture
def rec_db(monkeypatch):
    monkeypatch.setattr(psub._core, "DB_READY", True)
    monkeypatch.setattr(psub, "_IS_COMBO_COLUMN_EXISTS", False)

    def use(conn):
        monkeypatch.setattr(psub, "get_pool", AsyncMock(return_value=_RecPool(conn)))
        return conn
    return use


async def test_db_create_subscription_non_combo_keeps_the_old_insert_without_probe(rec_db):
    conn = rec_db(_RecConn())
    await psub.create_subscription("s", 1, 19900, 30, "basic")
    (insert,) = conn.sql
    assert insert.startswith("INSERT INTO platega_subscriptions") and "is_combo" not in insert


async def test_db_create_subscription_combo_writes_is_combo_when_column_exists(rec_db):
    conn = rec_db(_RecConn([True]))
    await psub.create_subscription("s", 1, 32900, 30, "basic", is_combo=True)
    probe, insert = conn.sql
    assert "information_schema.columns" in probe and "'is_combo'" in probe
    assert "is_combo)" in insert and "TRUE)" in insert

    # positive probe is cached
    conn2 = rec_db(_RecConn())
    await psub.create_subscription("s2", 1, 32900, 30, "basic", is_combo=True)
    (insert2,) = conn2.sql
    assert "is_combo)" in insert2


async def test_db_create_subscription_combo_without_column_falls_back_and_logs(rec_db, caplog):
    conn = rec_db(_RecConn([False, False]))
    await psub.create_subscription("s", 1, 32900, 30, "basic", is_combo=True)
    probe, insert = conn.sql
    assert "is_combo" not in insert
    assert "PLATEGA_SUB_IS_COMBO_NOT_PERSISTED" in caplog.text

    # a negative probe is NOT cached (migration may be applied later)
    conn2 = rec_db(_RecConn([False]))
    await psub.create_subscription("s2", 1, 32900, 30, "basic", is_combo=True)
    assert "information_schema.columns" in conn2.sql[0]


@pytest.mark.parametrize("is_combo", [True, False])
async def test_service_create_subscription_passes_is_combo_to_db(monkeypatch, is_combo):
    from tests.services.test_platega_wave1 import _fake_httpx_client

    monkeypatch.setattr(platega_service, "PLATEGA_MERCHANT_ID", MERCHANT)
    monkeypatch.setattr(platega_service, "PLATEGA_SECRET", SECRET)
    monkeypatch.setattr(platega_service.httpx, "AsyncClient", _fake_httpx_client([]))
    create = AsyncMock()
    monkeypatch.setattr(psub, "create_subscription", create)

    await platega_service.create_subscription(
        amount_rubles=329.0, interval=platega_service.SUBSCRIPTION_INTERVAL_MONTH,
        description="t", telegram_id=TG, tariff_type="basic", period_days=30,
        bot=MagicMock(), is_combo=is_combo,
    )

    assert create.await_args.kwargs["is_combo"] is is_combo


# ── Найдено в ревью PR 798 ─────────────────────────────────────────────

async def test_charge_extends_by_calendar_months(w):
    """Платный период каталога продлевается КАЛЕНДАРНЫМИ месяцами
    (решение владельца 2026-09-14). Рекуррент считал голые 30 дней —
    единственный платный путь в репозитории, который так делал."""
    await webhook(w)

    (call,) = w.grant_calls
    assert call.get("tariff_period_days") == 30, "продление в днях вместо календарных месяцев"


async def test_amount_mismatch_alerts_on_the_emergency_path_too(w, monkeypatch):
    """Сверка суммы обязательна на обоих путях выдачи: при выключенном
    флаге списание на любую сумму раньше проходило без единого слова."""
    monkeypatch.setenv(MODE_VAR, "off")

    await webhook(w, amount=1.0)

    alerts = w.forced_alerts()
    assert any("differs" in a.message for a in alerts), [a.message for a in alerts]
