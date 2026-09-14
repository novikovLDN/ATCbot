"""T12 — auto-renewal through the provisioning outbox (entry point "autorenew").

Spec: docs/audit/02_payment_core_plan.md §A flow 4, §G0; task T12.

Flag ON (USE_NEW_PROVISIONING=on, "autorenew" enabled):
  * tariff = the subscription's real one: tariff_key(subscription_type, is_combo)
    (legacy biz_* → plus); period = what the legacy code derives from the last payment;
  * price: basic / plus / biz → exactly today's number (same base price, same
    personal discount; VIP was removed 2026-09-14); combo_* → COMBO_TARIFFS price
    with the same discount;
  * one savepoint per user: debit → grant_access(defer_panel) → payments → enqueue;
    post-commit provisioning.run_now; no legacy sync / renew_remnawave_user_bg.
Flag OFF / shadow / entry point not listed → the legacy path, unchanged (the T0
characterization tests in test_payment_core_characterization.py pin it too).

Real: auto_renewal.process_auto_renewals, provisioning (enqueue / run_now / apply),
provisioning_worker.run_tick, tariffs, provisioning_flags.
Fake: asyncpg (TxConn below: the harness FakeConn + transaction depth per task, the
selection's WHERE clause and FOR UPDATE SKIP LOCKED on the one subscription row;
no rollback emulation), grant_access (harness contract fake), Remnawave
(tests/fakes/panel.FakePanel), outbox (tests/fakes/provisioning.FakeJobs).
"""
from __future__ import annotations

import asyncio
import uuid as uuid_lib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import auto_renewal
import config
import database
from app.services import admin_alerts, provisioning, purchase_flow, remnawave_service, tariffs
from app.workers import provisioning_worker as worker
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeDB, FakeJobs
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import TG

GIB = 1024 ** 3
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"
SEED_BYPASS = 3 * GIB
PERSONAL_DISCOUNT = 15


# ── fake asyncpg with transactions + the selection query ───────────────

class _Tx:
    def __init__(self, conn: "TxConn"):
        self.conn = conn

    async def __aenter__(self):
        task = asyncio.current_task()
        self.conn.depth[task] = self.conn.depth.get(task, 0) + 1
        return self

    async def __aexit__(self, exc_type, *_exc):
        task = asyncio.current_task()
        self.conn.depth[task] -= 1
        if self.conn.depth[task] == 0:
            del self.conn.depth[task]
            if self.conn.row_lock is task:
                self.conn.row_lock = None
            if exc_type is None:
                self.conn.commits += 1
        return False


class TxConn(h.FakeConn):
    """Harness FakeConn + transaction depth (per task), the auto-renewal selection
    (WHERE + FOR UPDATE SKIP LOCKED on the single subscription row) and the
    last_auto_renewal_at guard UPDATE."""

    def __init__(self, world):
        super().__init__(world)
        self.depth = {}
        self.row_lock = None
        self.commits = 0
        self.skipped_locked = 0

    def is_in_transaction(self) -> bool:
        return self.depth.get(asyncio.current_task(), 0) > 0

    def transaction(self):
        return _Tx(self)

    async def fetch(self, sql, *args):
        s = self._norm(sql)
        self.calls.append(("fetch", s, args))
        await asyncio.sleep(0)  # let a concurrent iteration interleave
        sub = self.w.sub
        if "from subscriptions s" not in s or not sub:
            return []
        threshold, now = args[0], args[1]
        exp, last = sub["expires_at"], sub.get("last_auto_renewal_at")
        due = (
            sub.get("status") == "active" and sub.get("auto_renew") and sub.get("uuid")
            and now < exp <= threshold
            and (last is None or last < exp - timedelta(hours=12))
        )
        if not due:
            return []
        if self.row_lock is not None and self.row_lock is not asyncio.current_task():
            self.skipped_locked += 1  # SKIP LOCKED
            return []
        self.row_lock = asyncio.current_task()
        return [{**sub, "language": "ru", "balance": self.w.balance_kopecks}]

    async def execute(self, sql, *args):
        s = self._norm(sql)
        if s.startswith("update subscriptions set last_auto_renewal_at"):
            self.calls.append(("execute", s, args))
            await asyncio.sleep(0)
            sub = self.w.sub
            exp, last = sub["expires_at"], sub.get("last_auto_renewal_at")
            if (sub.get("status") == "active" and sub.get("auto_renew")
                    and (last is None or last < exp - timedelta(hours=12))):
                sub["last_auto_renewal_at"] = args[0]
                return "UPDATE 1"
            return "UPDATE 0"
        return await super().execute(sql, *args)


# ── world ──────────────────────────────────────────────────────────────

def build(monkeypatch, *, flag="on", entrypoints="", sub_type="basic", is_combo=False,
          last_payment="basic_30", balance_rub=10_000, discount=None):
    """Fresh world: one due subscription (expires in 2 h), premium + 3 GB bypass."""
    w = h.install(monkeypatch)
    w.conn = TxConn(w)
    w.pool = h.FakePool(w.conn)
    panel = FakePanel().install(monkeypatch)
    jobs = FakeJobs(panel).install(monkeypatch)
    FakeDB().install(monkeypatch)

    async def get_subscription_any(tg):
        return dict(w.sub) if w.sub else None
    monkeypatch.setattr(database, "get_subscription_any", get_subscription_any)

    expires = h.utcnow() + timedelta(hours=2)
    w.sub = {
        "telegram_id": TG, "status": "active", "uuid": str(uuid_lib.uuid4()),
        "vpn_key": f"https://panel.test/sub/prem-{TG}", "expires_at": h.naive(expires),
        "subscription_type": sub_type, "activation_status": "active", "auto_renew": True,
        "is_bypass_only": False, "is_combo": is_combo, "last_auto_renewal_at": None,
    }
    w.balance_kopecks = balance_rub * 100
    panel.seed_premium(TG, expires)
    panel.seed_bypass(TG, SEED_BYPASS)

    monkeypatch.setenv(MODE_VAR, flag)
    if entrypoints:
        monkeypatch.setenv(EP_VAR, entrypoints)
    else:
        monkeypatch.delenv(EP_VAR, raising=False)

    async def _decrease(*, telegram_id, amount, source, description, conn):
        kop = round(amount * 100)
        if w.balance_kopecks < kop:
            return False
        w.balance_kopecks -= kop
        return True

    env = SimpleNamespace(
        w=w, panel=panel, jobs=jobs, premium_before=panel.premium_expire(TG),
        decrease=AsyncMock(side_effect=_decrease),
        sent=AsyncMock(return_value=MagicMock()),
        alerts=AsyncMock(return_value=True),
        legacy_sync=AsyncMock(),
        renew_bg=MagicMock(),
        bot=MagicMock(),
    )
    monkeypatch.setattr(database, "get_last_subscription_payment",
                        AsyncMock(return_value={"tariff": last_payment}), raising=False)
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(
        return_value={"discount_percent": PERSONAL_DISCOUNT} if discount == "personal" else None),
        raising=False)
    monkeypatch.setattr(database, "decrease_balance", env.decrease, raising=False)
    monkeypatch.setattr(database, "increase_balance", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(auto_renewal, "acquire_connection", lambda _pool, _name: h._Acquire(w.conn))
    monkeypatch.setattr(auto_renewal, "safe_send_message", env.sent)
    monkeypatch.setattr(auto_renewal, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(admin_alerts, "send_alert", env.alerts)
    monkeypatch.setattr(purchase_flow, "sync_renewal_to_remnawave", env.legacy_sync)
    monkeypatch.setattr(remnawave_service, "renew_remnawave_user_bg", env.renew_bg)
    return env


async def run(env):
    await auto_renewal.process_auto_renewals(env.bot)
    await env.w.drain_background()


def expected_amount(base_price: int, discount) -> float:
    """The discount rule auto_renewal applies (personal %; VIP was removed)."""
    if discount == "personal":
        return round(base_price * (1 - PERSONAL_DISCOUNT / 100), 2)
    return float(base_price)


def charged(env) -> float:
    assert env.decrease.await_count == 1
    return env.decrease.await_args.kwargs["amount"]


def only_job(env) -> dict:
    assert len(env.jobs.rows) == 1
    return next(iter(env.jobs.rows.values()))


def sent_text(env) -> str:
    return env.sent.await_args.args[2]


def assert_no_legacy_panel_path(env) -> None:
    env.legacy_sync.assert_not_awaited()
    env.renew_bg.assert_not_called()


def assert_premium_extended_to_sub(env) -> None:
    new_end = h.aware(env.w.sub["expires_at"])
    assert env.panel.premium_expire(TG) > env.premium_before
    assert env.panel.premium_expire(TG) >= new_end.replace(microsecond=0)


# ── flag ON: basic / plus keep today's price ───────────────────────────

@pytest.mark.parametrize("discount", [None, "personal"])
@pytest.mark.parametrize("sub_type,last_payment,period", [
    ("basic", "basic_30", 30),
    ("plus", "plus_90", 90),
])
async def test_on_basic_plus_charge_todays_price_add_10gb_and_extend_premium(
        monkeypatch, sub_type, last_payment, period, discount):
    off = build(monkeypatch, flag="off", sub_type=sub_type, last_payment=last_payment,
                discount=discount)
    await run(off)
    amount_off = charged(off)

    env = build(monkeypatch, sub_type=sub_type, last_payment=last_payment, discount=discount)
    await run(env)

    base = config.TARIFFS[sub_type][period]["price"]
    assert charged(env) == amount_off == expected_amount(base, discount)
    assert env.panel.bypass_limit(TG) == SEED_BYPASS + config.TRAFFIC_LIMITS[sub_type][period]
    assert_premium_extended_to_sub(env)
    grant = env.w.grant_calls[-1]
    assert grant["defer_panel"] is True and grant["caller_holds_tx"] is True
    assert grant["tariff"] == sub_type and grant["duration"] == timedelta(days=period)
    [(tg, tariff_str, amount_kop)] = env.w.conn.payment_inserts()
    assert (tg, tariff_str, amount_kop) == (TG, last_payment, round(charged(env) * 100))
    job = only_job(env)
    assert job["status"] == "done" and job["source"] == "autorenew"
    assert job["idempotency_key"] == "autorenew:501"
    assert job["tariff_key"] == sub_type
    assert job["premium_until"] == h.aware(env.w.sub["expires_at"])
    assert_no_legacy_panel_path(env)
    assert "purchase.auto_renewal_success" in env.w.i18n_keys
    assert f"{charged(env):.2f}" in sent_text(env)


async def test_on_outbox_job_is_written_inside_the_billing_transaction(monkeypatch):
    env = build(monkeypatch)

    await run(env)

    kinds = [c[0] if c[0] != "fetchval" or "insert into payments" not in c[1] else "payment"
             for c in env.w.conn.calls]
    assert ("insert_job", "autorenew:501", True) in env.w.conn.calls
    assert kinds.index("payment") < kinds.index("insert_job")
    assert env.w.conn.commits >= 1


# ── flag ON: combo at the combo price and combo GB ─────────────────────

@pytest.mark.parametrize("discount", [None, "personal"])
@pytest.mark.parametrize("sub_type,last_payment,period", [
    ("basic", "basic_30", 30),
    ("plus", "plus_90", 90),
])
async def test_on_combo_charges_combo_price_and_adds_combo_gb(
        monkeypatch, sub_type, last_payment, period, discount):
    key = f"combo_{sub_type}"
    env = build(monkeypatch, sub_type=sub_type, is_combo=True, last_payment=last_payment,
                discount=discount)

    await run(env)

    combo = config.COMBO_TARIFFS[key][period]
    assert tariffs.renewal_price_rub(key, period) == combo["price"]
    assert charged(env) == expected_amount(combo["price"], discount)
    assert env.panel.bypass_limit(TG) == SEED_BYPASS + combo["gb"] * GIB
    assert_premium_extended_to_sub(env)
    grant = env.w.grant_calls[-1]
    assert grant["tariff"] == sub_type and grant["defer_panel"] is True
    # combo purchases record payments.tariff as "<base tier>_<days>" (finalize_purchase)
    [(_, tariff_str, amount_kop)] = env.w.conn.payment_inserts()
    assert tariff_str == f"{sub_type}_{period}"
    assert amount_kop == round(charged(env) * 100)
    job = only_job(env)
    assert job["status"] == "done" and job["tariff_key"] == key
    assert job["bypass_add_bytes"] == combo["gb"] * GIB
    assert_no_legacy_panel_path(env)
    text = sent_text(env)
    assert f"Комбо {sub_type.capitalize()}" in text
    assert f"{charged(env):.2f}" in text


async def test_combo_basic_30d_is_329_in_the_current_config():
    """Guard for the owner decision's example number (read from config, not assumed)."""
    assert config.COMBO_TARIFFS["combo_basic"][30]["price"] == tariffs.renewal_price_rub("combo_basic", 30)
    assert config.COMBO_TARIFFS["combo_basic"][30]["gb"] == 75


# ── legacy business tariffs renew as plain Plus (owner 2026-09-14) ─────

async def test_legacy_biz_renews_as_plus_at_the_plus_price(monkeypatch):
    off = build(monkeypatch, flag="off", sub_type="biz_starter", last_payment="biz_starter_30")
    await run(off)
    amount_off = charged(off)

    env = build(monkeypatch, sub_type="biz_starter", last_payment="biz_starter_30")
    await run(env)

    # price: the current Plus price on both paths (the old "biz falls back to basic 199 ₽" rule is gone)
    assert charged(env) == amount_off == config.TARIFFS["plus"][30]["price"]
    grant = env.w.grant_calls[-1]
    assert grant["tariff"] == "plus" and grant["duration"] == timedelta(days=30)
    assert env.w.sub["subscription_type"] == "plus"
    job = only_job(env)
    assert job["tariff_key"] == "plus" and job["status"] == "done"
    assert env.panel.bypass_limit(TG) == SEED_BYPASS + config.TRAFFIC_LIMITS["plus"][30]
    assert_premium_extended_to_sub(env)
    assert_no_legacy_panel_path(env)


# ── flag ON: insufficient balance for the combo price ──────────────────

async def test_on_combo_insufficient_balance_no_debit_no_job(monkeypatch):
    basic = config.TARIFFS["basic"][30]["price"]
    combo = config.COMBO_TARIFFS["combo_basic"][30]["price"]
    balance = (basic + combo) // 2
    assert basic <= balance < combo  # enough for the old basic price, not for combo
    env = build(monkeypatch, is_combo=True, balance_rub=balance)

    await run(env)

    env.decrease.assert_not_awaited()
    assert env.w.balance_kopecks == balance * 100
    assert env.jobs.rows == {}
    assert env.w.grant_calls == []
    assert env.w.conn.payment_inserts() == []
    env.sent.assert_not_awaited()
    assert env.panel.bypass_limit(TG) == SEED_BYPASS
    assert env.panel.premium_expire(TG) == env.premium_before
    assert_no_legacy_panel_path(env)


# ── flag ON: panel down → billed once, job queued, worker completes ────

async def test_on_panel_down_bills_once_queues_job_alerts_and_worker_completes(monkeypatch):
    env = build(monkeypatch)
    start_balance = env.w.balance_kopecks
    env.panel.mode = "down"

    await run(env)

    price = config.TARIFFS["basic"][30]["price"]
    assert charged(env) == price
    assert env.w.balance_kopecks == start_balance - price * 100
    assert len(env.w.conn.payment_inserts()) == 1
    job = only_job(env)
    assert job["status"] == "pending" and job["attempts"] == 1 and job["last_error"]
    assert env.alerts.await_count >= 1
    assert env.alerts.await_args_list[0].kwargs.get("force") is True
    assert env.panel.bypass_limit(TG) == SEED_BYPASS
    assert env.sent.await_count == 1  # the DB renewal is committed → the user is told
    assert_no_legacy_panel_path(env)

    env.panel.mode = "ok"
    env.jobs.make_due(job["id"])
    stats = await worker.run_tick(env.bot)

    assert stats.done == 1
    assert env.jobs.job(job["id"])["status"] == "done"
    assert env.panel.bypass_limit(TG) == SEED_BYPASS + config.TRAFFIC_LIMITS["basic"][30]
    assert_premium_extended_to_sub(env)
    assert env.decrease.await_count == 1
    assert len(env.jobs.rows) == 1
    assert provisioning._USER_LOCKS == {}


# ── flag ON: two worker iterations on the same subscription ────────────

@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "concurrent"])
async def test_on_two_iterations_renew_once(monkeypatch, concurrent):
    env = build(monkeypatch, is_combo=True)

    if concurrent:
        await asyncio.gather(run(env), run(env))
        assert env.w.conn.skipped_locked == 1  # the second iteration hit SKIP LOCKED
    else:
        await run(env)
        await run(env)

    combo = config.COMBO_TARIFFS["combo_basic"][30]
    assert charged(env) == combo["price"]
    assert len(env.w.grant_calls) == 1
    assert len(env.w.conn.payment_inserts()) == 1
    assert only_job(env)["status"] == "done"
    assert env.panel.bypass_limit(TG) == SEED_BYPASS + combo["gb"] * GIB
    assert env.sent.await_count == 1


# ── flag OFF / shadow / entry point not listed → legacy path unchanged ─

@pytest.mark.parametrize("flag,entrypoints", [
    ("off", ""), ("shadow", ""), ("on", "webhook,balance"),
], ids=["off", "shadow", "on_other_entrypoints"])
async def test_flag_not_on_for_autorenew_keeps_legacy_path(monkeypatch, flag, entrypoints):
    env = build(monkeypatch, flag=flag, entrypoints=entrypoints, is_combo=True)
    enqueue = AsyncMock(side_effect=AssertionError("outbox used with the flag off"))
    monkeypatch.setattr(provisioning, "enqueue", enqueue)

    await run(env)

    # legacy: combo users are billed the basic price of the last payment (T0 xfail pins the target)
    assert charged(env) == config.TARIFFS["basic"][30]["price"]
    grant = env.w.grant_calls[-1]
    # M-AUTORENEW-SYNC-IN-TX (docs/audit/03_payment_matrix.md): the legacy grant is
    # DB-only inside the batch transaction; the premium sync runs after commit (Phase B).
    assert grant["defer_panel"] is False and grant["caller_holds_tx"] is True
    assert grant["tariff"] == "basic"
    env.legacy_sync.assert_awaited_once()
    env.renew_bg.assert_called_once()
    enqueue.assert_not_awaited()
    assert env.jobs.rows == {}
    assert env.w.conn.payment_inserts()[0][1] == "basic_30"
