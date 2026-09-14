"""T15 — trial activation service + provisioning outbox (entry point "trial").
Spec: docs/audit/02_payment_core_plan.md §A flow 9, task T15; owner decision:
trial = 3 days premium + TRIAL_BYPASS_MB (500 MB) bypass.

Sections
  1. Flag OFF: callback_activate_trial makes exactly the pre-T15 calls, in the
     same order, with the same texts (these tests were run green against the
     handler BEFORE the refactor, then after it).
  2. Flag ON, service + handler: one tx (advisory lock + users row lock,
     availability re-check, mark trial used, grant_access(defer_panel=True,
     _caller_holds_transaction=True), enqueue "trial:{tg}") → commit → run_now.
     Hermetic world: FakePanel / FakeJobs / FakeDB + a fake connection whose
     users writes become visible only at commit and whose advisory lock is a
     real asyncio.Lock, so concurrent clicks really race.
  3. The 3 former calls of the missing trial_service.activate_trial, on the T8
     harness. Traffic pack `bypass_*` (webhook + Telegram): owner rule 2026-09-14 —
     the GB + a one-time 3-day premium gift (0 bytes, not the trial's 500 MB),
     regardless of the "trial" flag, never breaks the purchase. Balance and
     Telegram FSM bypass-only: flag on → trial when available; off → none.
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
import database.subscriptions as db_subs
from app.api import payment_webhook
from app.handlers.callbacks import subscription as sub_mod
from app.handlers.payments import payments_messages as pm
from app.i18n import get_text as i18n_get_text
from app.services import admin_alerts, provisioning, sub_aggregator, user_subscription_links
from app.services.trials import bypass_activation_delay
from app.services.trials import service as trial_service
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeDB, FakeJobs
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import make_pending, text_prefix
from tests.services.test_payment_core_t8 import (  # noqa: F401 — env / outbox are pytest fixtures
    _close,
    _flag,
    assert_no_legacy_panel_writes,
    env,
    outbox,
    seed_active,
    sent_text,
)

TG = 7015
HTG = h.TG  # user id of the T8 harness
GIB = 1024 ** 3
MIB = 1024 ** 2
TRIAL_BYTES = config.TRIAL_BYPASS_MB * MIB
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt is not None else None


def _norm(sql: str) -> str:
    return " ".join(sql.split())


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "off")
    monkeypatch.delenv(EP_VAR, raising=False)


def test_trial_entitlement_is_500mb_and_3_days():
    assert config.TRIAL_BYPASS_MB == 500
    assert trial_service.TRIAL_DURATION == timedelta(days=3)


# ═══ 1. flag OFF: callback_activate_trial is exactly the pre-T15 handler ═══

class HandlerEnv:
    """Records every external call of callback_activate_trial, in order.
    patch_db=False leaves grant_access / mark_trial_used / is_eligible to a world."""

    def __init__(self, monkeypatch, *, eligible=True, grant_result=None, grant_exc=None,
                 mark_ok=True, sub_url="https://panel.test/sub/prem", patch_db=True):
        self.log = []
        self.grant_result = grant_result
        self.kb = object()
        log = self.log

        async def ensure_db_ready_callback(cb):
            log.append(("ensure_db_ready",))
            return True

        async def resolve_user_language(tg):
            log.append(("resolve_user_language", tg))
            return "ru"

        def check_rate_limit(tg, action):
            log.append(("check_rate_limit", tg, action))
            return True, None

        async def is_eligible_for_trial(tg):
            log.append(("is_eligible_for_trial", tg))
            return eligible

        async def grant_access(**kw):
            log.append(("grant_access", kw))
            if grant_exc is not None:
                raise grant_exc
            return self.grant_result

        async def mark_trial_used(tg, trial_expires_at):
            log.append(("mark_trial_used", tg, trial_expires_at))
            return mark_ok

        async def activate_referral_and_notify(bot, tg):
            log.append(("referral_task", bot, tg))

        def schedule_bypass_activated_notification(bot, tg):
            log.append(("schedule_bypass_activated", bot, tg))

        async def get_url(tg):
            log.append(("get_user_primary_subscription_url", tg))
            return sub_url

        def keyboard(language, subscription_type=None, **kw):
            log.append(("get_payment_success_keyboard", language, subscription_type, kw))
            return self.kb

        monkeypatch.setattr(database, "DB_READY", True, raising=False)
        # not degraded → no trial.degradation_notice suffix on the success text
        monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True, raising=False)
        monkeypatch.setattr(sub_mod, "ensure_db_ready_callback", ensure_db_ready_callback)
        monkeypatch.setattr(sub_mod, "resolve_user_language", resolve_user_language)
        monkeypatch.setattr(sub_mod, "check_rate_limit", check_rate_limit)
        if patch_db:
            monkeypatch.setattr(database, "is_eligible_for_trial", is_eligible_for_trial)
            monkeypatch.setattr(database, "grant_access", grant_access)
            monkeypatch.setattr(database, "mark_trial_used", mark_trial_used)
        monkeypatch.setattr(sub_mod, "_activate_referral_and_notify", activate_referral_and_notify)
        monkeypatch.setattr(bypass_activation_delay, "schedule_bypass_activated_notification",
                            schedule_bypass_activated_notification)
        monkeypatch.setattr(user_subscription_links, "get_user_primary_subscription_url", get_url)
        import app.handlers.common.keyboards as kb_mod
        monkeypatch.setattr(kb_mod, "get_payment_success_keyboard", keyboard)

        self.bot = object()
        self.callback, self.placeholder = self.make_callback()

    def make_callback(self):
        placeholder = SimpleNamespace(edit_text=AsyncMock(side_effect=self._rec("placeholder.edit_text")))
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=TG), bot=self.bot,
            answer=AsyncMock(side_effect=self._rec("callback.answer")),
            message=SimpleNamespace(
                answer=AsyncMock(side_effect=self._rec("message.answer", ret=placeholder)),
                delete=AsyncMock(side_effect=self._rec("message.delete")),
            ),
        )
        return callback, placeholder

    def _rec(self, name, ret=None):
        async def f(*a, **kw):
            self.log.append((name, a, kw))
            return ret
        return f

    async def run(self, callback=None):
        await sub_mod.callback_activate_trial(callback or self.callback, SimpleNamespace())
        await self.settle()

    @staticmethod
    async def settle():
        for _ in range(3):
            await asyncio.sleep(0)  # let the referral create_task run

    def names(self):
        return [e[0] for e in self.log]


def _legacy_grant_result(end):
    return {"uuid": "0123456789abcdef", "vless_url": "vless://x", "subscription_end": end}


def _success_text(end, sub_url="https://panel.test/sub/prem"):
    return i18n_get_text("ru", "trial.activated", expires_date=end.strftime("%d.%m.%Y"), sub_url=sub_url)


async def test_flag_off_handler_calls_and_texts_identical_to_pre_t15(monkeypatch):
    end = utcnow() + timedelta(days=3)
    env_ = HandlerEnv(monkeypatch, grant_result=_legacy_grant_result(end))
    before = utcnow()
    await env_.run()
    after = utcnow()

    assert env_.names() == [
        "ensure_db_ready",
        "resolve_user_language",
        "check_rate_limit",
        "is_eligible_for_trial",
        "callback.answer",
        "message.answer",                       # «⏳ Активирую…» placeholder
        "grant_access",
        "mark_trial_used",
        "schedule_bypass_activated",
        "get_user_primary_subscription_url",
        "get_payment_success_keyboard",
        "placeholder.edit_text",
        "message.delete",
        "referral_task",                        # create_task body runs at the next loop tick
    ]
    grant = env_.log[6][1]
    assert grant == {"telegram_id": TG, "duration": timedelta(days=3), "source": "trial",
                     "admin_telegram_id": None}
    _, tg, trial_expires_at = env_.log[7]
    assert tg == TG and before + timedelta(days=3) <= trial_expires_at <= after + timedelta(days=3)
    assert env_.log[8][1:] == (env_.bot, TG)
    assert env_.log[10][1:] == ("ru", "basic", {})
    assert env_.log[-1][1:] == (env_.bot, TG)

    assert env_.log[4] == ("callback.answer", (), {})
    assert env_.log[5] == ("message.answer", (i18n_get_text("ru", "trial.activating"),), {"parse_mode": "HTML"})
    assert env_.log[11] == ("placeholder.edit_text", (_success_text(end),),
                            {"parse_mode": "HTML", "reply_markup": env_.kb})


async def test_flag_off_handler_ineligible_user_gets_toast_and_nothing_else(monkeypatch):
    env_ = HandlerEnv(monkeypatch, eligible=False)
    await env_.run()
    assert env_.names() == ["ensure_db_ready", "resolve_user_language", "check_rate_limit",
                            "is_eligible_for_trial", "callback.answer"]
    assert env_.log[-1] == ("callback.answer", (i18n_get_text("ru", "main.trial_not_available"),),
                            {"show_alert": True})


@pytest.mark.parametrize("grant_result,grant_exc", [
    ({"uuid": None, "vless_url": None, "subscription_end": None}, None),  # pending activation
    (None, RuntimeError("panel boom")),
], ids=["no_uuid", "grant_raises"])
async def test_flag_off_handler_grant_failure_shows_error_and_does_not_mark_trial(
        monkeypatch, grant_result, grant_exc):
    env_ = HandlerEnv(monkeypatch, grant_result=grant_result, grant_exc=grant_exc)
    await env_.run()
    assert "mark_trial_used" not in env_.names()
    assert "referral_task" not in env_.names()
    assert env_.log[-1] == ("message.answer", (i18n_get_text("ru", "main.trial_activation_error"),),
                            {"parse_mode": "HTML"})


async def test_flag_off_handler_mark_failure_still_succeeds(monkeypatch):
    end = utcnow() + timedelta(days=3)
    env_ = HandlerEnv(monkeypatch, grant_result=_legacy_grant_result(end), mark_ok=False)
    await env_.run()
    assert "placeholder.edit_text" in env_.names() and "referral_task" in env_.names()


async def test_flag_off_activate_trial_is_the_handler_sequence(monkeypatch):
    end = utcnow() + timedelta(days=3)
    env_ = HandlerEnv(monkeypatch, grant_result=_legacy_grant_result(end))
    assert await trial_service.activate_trial(TG) is True
    assert env_.names() == ["is_eligible_for_trial", "grant_access", "mark_trial_used"]
    env_.log.clear()
    ineligible = HandlerEnv(monkeypatch, eligible=False)
    assert await trial_service.activate_trial(TG) is False
    assert ineligible.names() == ["is_eligible_for_trial"]


# ═══ 2. flag ON: service + handler (hermetic world) ═══════════════════════

class World:
    def __init__(self, jobs: FakeJobs, db: FakeDB):
        self.jobs = jobs
        self.db = db
        self.users = {}        # committed: tg -> {"trial_used_at": naive|None, "trial_expires_at": naive|None}
        self.grant_calls = []
        self.locks = {}        # advisory xact locks: key -> asyncio.Lock
        self.lock_keys = []
        self.fail_grant = False


class _Tx:
    """conn.transaction(): users writes applied at commit; jobs/subs rolled back on error;
    advisory locks released at the end (like pg_advisory_xact_lock)."""

    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        w = self.conn.world
        self.jobs_before = set(w.jobs.rows)
        self.subs_before = copy.deepcopy(w.db.subs)
        self.conn.in_tx = True
        self.conn.pending_users = {}
        return self

    async def __aexit__(self, exc_type, exc, tb):
        w = self.conn.world
        self.conn.in_tx = False
        if exc_type is None:
            for tg, vals in self.conn.pending_users.items():
                w.users.setdefault(tg, {}).update(vals)
        else:
            for job_id in set(w.jobs.rows) - self.jobs_before:
                del w.jobs.rows[job_id]
            w.db.subs = self.subs_before
        self.conn.pending_users = {}
        for lock in self.conn.held:
            lock.release()
        self.conn.held = []
        return False


class TxConn:
    def __init__(self, world: World):
        self.world = world
        self.in_tx = False
        self.calls = []
        self.held = []
        self.pending_users = {}

    def is_in_transaction(self) -> bool:
        return self.in_tx

    def transaction(self):
        return _Tx(self)

    def _user(self, tg):
        committed = self.world.users.get(tg)
        if committed is None:
            return None
        return {**committed, **self.pending_users.get(tg, {})}

    async def execute(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("execute", s, args))
        if s.startswith("SELECT pg_advisory_xact_lock"):
            assert self.in_tx, "advisory xact lock outside a transaction"
            lock = self.world.locks.setdefault(args[0], asyncio.Lock())
            await lock.acquire()
            self.held.append(lock)
            self.world.lock_keys.append(args[0])
            return "SELECT 1"
        if s.startswith("UPDATE users SET trial_used_at"):
            assert self.in_tx and "trial_used_at IS NULL" in s
            trial_expires_at, tg = args
            assert trial_expires_at.tzinfo is None, "UTC contract: naive in the DB"
            user = self._user(tg)
            if user is None or user.get("trial_used_at") is not None:
                return "UPDATE 0"
            self.pending_users[tg] = {"trial_used_at": naive(utcnow()), "trial_expires_at": trial_expires_at}
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {s}")

    async def fetchrow(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchrow", s, args))
        if "FROM users u LEFT JOIN subscriptions" in s:
            assert self.in_tx and "FOR UPDATE OF u" in s
            user = self._user(args[0])
            if user is None:
                return None
            sub = self.world.db.subs.get(args[0]) or {}
            return {
                "trial_used_at": user.get("trial_used_at"), "status": sub.get("status"),
                "expires_at": naive(sub.get("expires_at")), "source": sub.get("source"),
                "is_bypass_only": bool(sub.get("is_bypass_only")),
            }
        raise AssertionError(f"unexpected fetchrow: {s}")

    async def fetchval(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchval", s, args))
        if "FROM provisioning_jobs" in s:
            for r in self.world.jobs.rows.values():
                if r["idempotency_key"] == args[0]:
                    return r["id"]
            return None
        raise AssertionError(f"unexpected fetchval: {s}")


class FakePool:
    def __init__(self, world: World):
        self.world = world

    def acquire(self):
        conn = TxConn(self.world)

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _Acq()


@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


@pytest.fixture
def jobs(monkeypatch, panel):
    return FakeJobs(panel).install(monkeypatch)


@pytest.fixture
def fdb(monkeypatch):
    return FakeDB().install(monkeypatch)


@pytest.fixture
def alerts(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", mock)
    return mock


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "trial")
    assert trial_service.outbox_on()


@pytest.fixture
def world(monkeypatch, jobs, fdb, alerts):
    w = World(jobs, fdb)
    pool = FakePool(w)
    monkeypatch.setattr(database, "DB_READY", True, raising=False)
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda tg: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)

    async def get_pool():
        return pool

    async def grant_access(*, telegram_id, duration, source, admin_telegram_id=None,
                           admin_grant_days=None, conn=None, _caller_holds_transaction=False,
                           tariff="basic", defer_panel=False, **kw):
        w.grant_calls.append({
            "tg": telegram_id, "duration": duration, "source": source, "tariff": tariff,
            "admin_telegram_id": admin_telegram_id, "defer": defer_panel,
            "holds": _caller_holds_transaction,
            "in_tx": conn.is_in_transaction() if conn is not None else None, "extra": kw,
        })
        await asyncio.sleep(0)  # a real grant_access awaits the DB: let other clicks run
        if w.fail_grant:
            raise RuntimeError("db boom")
        now = utcnow()
        sub = w.db.subs.get(telegram_id)
        # real rule: renewal only for an active row WITH a uuid (bypass-only rows have none)
        if sub and sub["status"] == "active" and sub["expires_at"] > now and sub.get("uuid"):
            sub["expires_at"] = sub["expires_at"] + duration
            return {"uuid": sub["uuid"], "vless_url": None, "subscription_end": sub["expires_at"],
                    "action": "renewal", "deferred": True}
        end = now + duration
        w.db.subs[telegram_id] = {
            "telegram_id": telegram_id, "uuid": None, "status": "active", "expires_at": end,
            "subscription_type": tariff, "source": source, "is_bypass_only": False,
            "activation_status": "pending" if defer_panel else "active",
        }
        return {"uuid": None, "vless_url": None, "subscription_end": end,
                "action": "pending_activation", "deferred": True}

    async def complete_activation(tg, *, vpn_key, vpn_key_plus, uuid=None, conn=None):
        sub = w.db.subs.get(tg)
        if not sub or sub.get("activation_status") != "pending":
            return False
        sub.update(activation_status="active", vpn_key=vpn_key, vpn_key_plus=vpn_key_plus)
        return True

    async def is_eligible_for_trial(tg):  # committed state only, like the real query
        return (w.users.get(tg) or {}).get("trial_used_at") is None

    monkeypatch.setattr(database, "get_pool", get_pool)
    monkeypatch.setattr(database, "grant_access", grant_access)
    monkeypatch.setattr(db_subs, "grant_access", grant_access)
    monkeypatch.setattr(database, "complete_activation", complete_activation)
    monkeypatch.setattr(database, "is_eligible_for_trial", is_eligible_for_trial)
    monkeypatch.setattr(database, "mark_trial_used",
                        AsyncMock(side_effect=AssertionError("legacy mark_trial_used under the flag")))
    yield w
    assert provisioning._USER_LOCKS == {}, "user locks must be released"


def only_job(w: World):
    assert len(w.jobs.rows) == 1, w.jobs.rows
    return next(iter(w.jobs.rows.values()))


def ceil_s(dt):
    return provisioning._ceil_second(dt)


def assert_trial_grant_call(call):
    assert call["defer"] is True and call["holds"] is True and call["in_tx"] is True
    assert call["source"] == "trial" and call["duration"] == timedelta(days=3)
    assert call["tariff"] == "basic" and call["admin_telegram_id"] is None


async def test_activate_trial_new_user_3_days_premium_and_500mb_once(flag_on, world, panel, alerts):
    world.users[TG] = {"trial_used_at": None}
    before = utcnow()
    assert await trial_service.activate_trial(TG) is True

    job = only_job(world)
    assert (job["idempotency_key"], job["source"], job["tariff_key"], job["status"]) == \
        (f"trial:{TG}", "trial", "trial", "done")
    assert job["bypass_add_bytes"] == TRIAL_BYTES
    [call] = world.grant_calls
    assert_trial_grant_call(call)
    end = world.db.subs[TG]["expires_at"]
    assert job["premium_until"] == end and _close(end, before + timedelta(days=3))
    assert panel.premium_expire(TG) == ceil_s(end)
    assert panel.bypass_limit(TG) == TRIAL_BYTES
    assert world.db.subs[TG]["activation_status"] == "active"
    user = world.users[TG]
    assert user["trial_used_at"] is not None
    assert _close(user["trial_expires_at"].replace(tzinfo=timezone.utc), before + timedelta(days=3))
    assert world.lock_keys == [f"trial:{TG}"]
    alerts.assert_not_awaited()

    # second call: already used → False, nothing new
    assert await trial_service.activate_trial(TG) is False
    assert len(world.jobs.rows) == 1 and len(world.grant_calls) == 1
    assert panel.bypass_limit(TG) == TRIAL_BYTES


async def test_activate_trial_bypass_only_buyer_gets_trial_on_top_of_existing_gb(flag_on, world, panel):
    """ensure_bypass_only_subscription's row (active, +10y, no uuid) is not an active subscription."""
    world.users[TG] = {"trial_used_at": None}
    world.db.subs[TG] = {"telegram_id": TG, "uuid": None, "status": "active",
                         "expires_at": utcnow() + timedelta(days=3650), "source": "bypass_only",
                         "is_bypass_only": True, "activation_status": "active"}
    panel.seed_bypass(TG, 3 * GIB)
    assert await trial_service.activate_trial(TG) is True
    assert panel.bypass_limit(TG) == 3 * GIB + TRIAL_BYTES
    assert _close(panel.premium_expire(TG), utcnow() + timedelta(days=3))
    assert world.db.subs[TG]["source"] == "trial"


async def test_activate_trial_already_used_returns_false_no_job(flag_on, world, panel, alerts):
    world.users[TG] = {"trial_used_at": naive(utcnow() - timedelta(days=30))}
    assert await trial_service.activate_trial(TG) is False
    assert world.jobs.rows == {} and world.grant_calls == []
    assert panel.calls == [] and alerts.await_count == 0


@pytest.mark.parametrize("sub", [
    {"uuid": "u-1", "status": "active", "source": "payment", "days": 20},
    {"uuid": "u-1", "status": "expired", "source": "payment", "days": -20},  # paid history
    {"uuid": "u-1", "status": "active", "source": "admin", "days": 5},
], ids=["active_paid", "paid_history", "active_admin_grant"])
async def test_activate_trial_not_available_for_subscribers(flag_on, world, sub):
    world.users[TG] = {"trial_used_at": None}
    world.db.subs[TG] = {"telegram_id": TG, "uuid": sub["uuid"], "status": sub["status"],
                         "expires_at": utcnow() + timedelta(days=sub["days"]),
                         "source": sub["source"], "is_bypass_only": False, "activation_status": "active"}
    assert await trial_service.activate_trial(TG) is False
    assert world.jobs.rows == {} and world.grant_calls == []
    assert world.users[TG]["trial_used_at"] is None


async def test_activate_trial_unknown_user_returns_false(flag_on, world):
    assert await trial_service.activate_trial(TG) is False
    assert world.jobs.rows == {}


async def test_concurrent_activations_grant_one_trial(flag_on, world, panel):
    world.users[TG] = {"trial_used_at": None}
    results = await asyncio.gather(*(trial_service.activate_trial(TG) for _ in range(3)))
    assert sorted(results) == [False, False, True]
    only_job(world)
    assert len(world.grant_calls) == 1
    assert panel.bypass_limit(TG) == TRIAL_BYTES
    assert world.lock_keys == [f"trial:{TG}"] * 3  # every call went through the lock


async def test_manually_reset_trial_never_enqueues_a_second_trial_job(flag_on, world, panel):
    world.users[TG] = {"trial_used_at": None}
    assert await trial_service.activate_trial(TG) is True
    world.users[TG]["trial_used_at"] = None  # admin reset by hand in SQL
    world.db.subs[TG]["expires_at"] = utcnow() - timedelta(days=1)
    world.db.subs[TG]["source"] = "admin"
    assert await trial_service.activate_trial(TG) is False
    assert len(world.jobs.rows) == 1 and len(world.grant_calls) == 1


async def test_grant_failure_commits_nothing_and_safely_alerts(flag_on, world, alerts):
    world.users[TG] = {"trial_used_at": None}
    world.fail_grant = True
    with pytest.raises(RuntimeError):
        await trial_service.activate_trial(TG)
    assert world.jobs.rows == {} and world.users[TG]["trial_used_at"] is None

    bot = object()
    assert await trial_service.activate_trial_safely(TG, bot=bot, where="traffic_pack:x:p1") is False
    alerts.assert_awaited_once()
    assert alerts.await_args.args[0] is bot and alerts.await_args.kwargs["force"] is True
    assert "Trial activation after purchase FAILED" in alerts.await_args.args[2]
    assert "traffic_pack:x:p1" in alerts.await_args.args[2]
    assert world.users[TG]["trial_used_at"] is None


async def test_flag_on_handler_success(flag_on, world, panel, monkeypatch):
    world.users[TG] = {"trial_used_at": None}
    env_ = HandlerEnv(monkeypatch, patch_db=False)
    await env_.run()

    job = only_job(world)
    assert job["status"] == "done" and panel.bypass_limit(TG) == TRIAL_BYTES
    assert_trial_grant_call(world.grant_calls[0])
    end = world.db.subs[TG]["expires_at"]
    names = env_.names()
    assert names[:6] == ["ensure_db_ready", "resolve_user_language", "check_rate_limit",
                         "is_eligible_for_trial", "callback.answer", "message.answer"] or \
        names[:5] == ["ensure_db_ready", "resolve_user_language", "check_rate_limit",
                      "callback.answer", "message.answer"]
    assert names[-6:] == ["schedule_bypass_activated", "get_user_primary_subscription_url",
                          "get_payment_success_keyboard", "placeholder.edit_text", "message.delete",
                          "referral_task"]
    edit = [e for e in env_.log if e[0] == "placeholder.edit_text"][0]
    assert edit == ("placeholder.edit_text", (_success_text(end),), {"parse_mode": "HTML", "reply_markup": env_.kb})


async def test_flag_on_handler_double_click_one_trial(flag_on, world, panel, monkeypatch):
    world.users[TG] = {"trial_used_at": None}
    env_ = HandlerEnv(monkeypatch, patch_db=False)
    (cb1, ph1), (cb2, ph2) = env_.make_callback(), env_.make_callback()
    await asyncio.gather(
        sub_mod.callback_activate_trial(cb1, SimpleNamespace()),
        sub_mod.callback_activate_trial(cb2, SimpleNamespace()),
    )
    await env_.settle()

    only_job(world)
    assert len(world.grant_calls) == 1
    assert panel.bypass_limit(TG) == TRIAL_BYTES
    end = world.db.subs[TG]["expires_at"]
    texts = sorted(ph.edit_text.await_args.args[0] for ph in (ph1, ph2))
    assert texts == sorted([_success_text(end), i18n_get_text("ru", "main.trial_not_available")])
    assert env_.names().count("referral_task") == 1
    assert env_.names().count("schedule_bypass_activated") == 1


async def test_flag_on_handler_panel_down_job_pending_alert_user_gets_trial_text(
        flag_on, world, panel, alerts, monkeypatch):
    world.users[TG] = {"trial_used_at": None}
    panel.mode = "down"
    env_ = HandlerEnv(monkeypatch, patch_db=False)
    await env_.run()

    job = only_job(world)
    assert (job["status"], job["attempts"]) == ("pending", 1)
    alerts.assert_awaited_once()
    assert alerts.await_args.args[0] is env_.bot and alerts.await_args.kwargs["force"] is True
    assert world.db.payment_errors and world.db.payment_errors[0]["purchase_id"] == f"trial:{TG}"
    end = world.db.subs[TG]["expires_at"]
    edit = [e for e in env_.log if e[0] == "placeholder.edit_text"][0]
    assert edit[1] == (_success_text(end, sub_url=""),)
    # queued job: no lazy entity provisioning outside the outbox
    assert "get_user_primary_subscription_url" not in env_.names()
    assert world.users[TG]["trial_used_at"] is not None
    assert world.db.subs[TG]["activation_status"] == "pending"

    panel.mode = "ok"
    assert await provisioning.run_now(job["id"]) is True
    assert panel.bypass_limit(TG) == TRIAL_BYTES
    assert panel.premium_expire(TG) == ceil_s(end)
    assert world.db.subs[TG]["activation_status"] == "active"


# ═══ 3. the 3 former calls of the missing activate_trial (T8 harness) ═════

def install_trial_state(w, monkeypatch, *, trial_used=False):
    """Teach the harness connection the trial SQL (users row + trial CAS + job lookup)."""
    w.trial = {"used_at": naive(utcnow()) if trial_used else None, "expires_at": None}
    real_fetchrow, real_fetchval, real_execute = w.conn.fetchrow, w.conn.fetchval, w.conn.execute

    async def fetchrow(sql, *args):
        s = _norm(sql).lower()
        if "from users u left join subscriptions" in s:
            sub = w.sub or {}
            return {"trial_used_at": w.trial["used_at"], "status": sub.get("status"),
                    "expires_at": sub.get("expires_at"), "source": sub.get("source"),
                    "is_bypass_only": bool(sub.get("is_bypass_only"))}
        return await real_fetchrow(sql, *args)

    async def fetchval(sql, *args):
        s = _norm(sql).lower()
        if "from provisioning_jobs" in s:
            return next((r["id"] for r in w.jobs.rows.values() if r["idempotency_key"] == args[0]), None)
        return await real_fetchval(sql, *args)

    async def execute(sql, *args):
        s = _norm(sql).lower()
        if s.startswith("update users set trial_used_at"):
            if w.trial["used_at"] is not None:
                return "UPDATE 0"
            w.trial.update(used_at=naive(utcnow()), expires_at=args[0])
            return "UPDATE 1"
        return await real_execute(sql, *args)

    monkeypatch.setattr(w.conn, "fetchrow", fetchrow)
    monkeypatch.setattr(w.conn, "fetchval", fetchval)
    monkeypatch.setattr(w.conn, "execute", execute)
    return w


def spy_safely(monkeypatch):
    mock = AsyncMock(side_effect=trial_service.activate_trial_safely)
    monkeypatch.setattr(trial_service, "activate_trial_safely", mock)
    return mock


def jobs_by_key(w):
    return {r["idempotency_key"]: r for r in w.jobs.rows.values()}


def pack_pending(gb=15):
    return make_pending(tariff=f"bypass_{gb}gb", period_days=0, purchase_type="traffic_pack", price_rub=89)


async def run_tg(w, monkeypatch, pending: dict, *, fsm: dict | None = None):
    """Telegram-native successful_payment (payments_messages.process_successful_payment);
    unlike the harness runner, message.bot can send (traffic-pack confirmation)."""
    w.pending = pending

    async def get_pending(pid, tg, check_expiry=True):
        return dict(w.pending) if w.pending and w.pending["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase", get_pending)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pm, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pm, "clear_promo_session", AsyncMock())
    state = h._fsm(pending["telegram_id"])
    if fsm:
        await state.update_data(**fsm)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    message = MagicMock()
    message.bot = bot
    message.from_user.id = pending["telegram_id"]
    message.message_id = 1
    message.answer = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency="RUB", total_amount=pending["price_kopecks"],
        invoice_payload=f"purchase:{pending['purchase_id']}",
        telegram_payment_charge_id="tg-charge-1",
    )
    await pm.process_successful_payment(message, state)
    await w.drain_background()
    return message, bot


async def run_balance(w, monkeypatch, *, bypass_only_gb=5, tariff="basic", period_days=30):
    """callback_pay_balance with the (never set in production) FSM key bypass_only_gb."""
    import app.services.automated_notifications as autonotif
    from app.handlers.callbacks import payments_callbacks as pc
    from app.handlers.common.states import PurchaseState

    price_rub = config.TARIFFS[tariff][period_days]["price"]
    state = h._fsm(HTG)
    await state.set_state(PurchaseState.choose_payment_method)
    await state.update_data(tariff_type=tariff, period_days=period_days,
                            final_price_kopecks=price_rub * 100, bypass_only_gb=bypass_only_gb)

    async def _balance(_tg): return w.balance_kopecks / 100
    monkeypatch.setattr(database, "get_user_balance", _balance, raising=False)
    monkeypatch.setattr(pc, "check_rate_limit", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pc, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pc, "get_promo_session", AsyncMock(return_value=None))
    monkeypatch.setattr(pc, "clear_promo_session", AsyncMock())
    monkeypatch.setattr(autonotif, "is_notification_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(autonotif, "get_notification_text", AsyncMock(return_value=None))
    monkeypatch.setattr(autonotif, "log_notification_send", AsyncMock())
    callback = MagicMock()
    callback.from_user.id = HTG
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.delete = AsyncMock()
    await pc.callback_pay_balance(callback, state)
    await w.drain_background()
    return callback


@pytest.fixture
def trial_on(outbox, monkeypatch):
    """webhook + telegram + trial on, T8 harness with the trial SQL."""
    _flag(monkeypatch, "on", "webhook,telegram,trial")
    return install_trial_state(outbox, monkeypatch)


def assert_pack_plus_gift(w, gb, before):
    """Owner rule 2026-09-14 (SCOPE «Только обход» без подписки): exactly the
    pack GB + the 3-day premium gift — 0 bytes, never the trial's 500 MB."""
    by_key = jobs_by_key(w)
    assert set(by_key) == {"purchase:pid-1", f"trial:{HTG}"}
    pack, gift = by_key["purchase:pid-1"], by_key[f"trial:{HTG}"]
    assert (pack["tariff_key"], pack["bypass_add_bytes"], pack["premium_until"], pack["status"]) == \
        ("pack", gb * GIB, None, "done")
    assert (gift["tariff_key"], gift["source"], gift["bypass_add_bytes"], gift["status"]) == \
        ("trial", "trial", 0, "done")
    assert pack["id"] < gift["id"]
    assert w.panel2.bypass_limit(HTG) == gb * GIB   # exactly the GB bought
    assert _close(w.panel2.premium_expire(HTG), before + timedelta(days=3))
    [g] = w.grant_calls
    assert (g["source"], g["duration"], g["defer_panel"], g["caller_holds_tx"]) == \
        ("trial", timedelta(days=3), True, True)
    assert w.trial["used_at"] is not None
    assert w.sub["activation_status"] == "active"
    assert_no_legacy_panel_writes(w)


def spy_gift(monkeypatch):
    mock = AsyncMock(side_effect=trial_service.grant_bypass_purchase_gift)
    monkeypatch.setattr(trial_service, "grant_bypass_purchase_gift", mock)
    return mock


def gift_line(w) -> str:
    return text_prefix(w, "bypass.gift_premium_granted")


async def test_webhook_bypass_pack_buyer_gets_gift_and_pack_gb_two_jobs(trial_on, monkeypatch):
    w = trial_on
    spy = spy_gift(monkeypatch)
    before = h.utcnow()
    out = await h.run_webhook(w, monkeypatch, pack_pending(15))

    assert out["result"] == {"status": "ok"}
    assert_pack_plus_gift(w, 15, before)
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["where"] == "traffic_pack:platega:pid-1"
    assert spy.await_args.kwargs["via_outbox"] is True
    text = sent_text(out["bot"])
    assert text.startswith(text_prefix(w, "bypass.purchase_success"))
    assert gift_line(w) in text
    assert trial_service.format_gift_until(w.sub["expires_at"]) + " МСК" in text
    assert "задерживается" not in text


async def test_telegram_bypass_pack_buyer_gets_gift_and_pack_gb_two_jobs(trial_on, monkeypatch):
    w = trial_on
    before = h.utcnow()
    _message, bot = await run_tg(w, monkeypatch, pack_pending(15))

    assert_pack_plus_gift(w, 15, before)
    assert jobs_by_key(w)["purchase:pid-1"]["context"]["provider"] == "telegram_payment"
    text = bot.send_message.call_args.args[1]
    assert gift_line(w) in text


async def test_webhook_pack_trial_already_used_only_pack_job(outbox, monkeypatch):
    w = outbox
    _flag(monkeypatch, "on", "webhook,telegram,trial")
    install_trial_state(w, monkeypatch, trial_used=True)
    out = await h.run_webhook(w, monkeypatch, pack_pending(15))
    assert set(jobs_by_key(w)) == {"purchase:pid-1"}
    assert w.panel2.bypass_limit(HTG) == 15 * GIB and w.grant_calls == []
    assert "bypass.gift_premium_granted" not in w.i18n_keys
    assert gift_line(w) not in sent_text(out["bot"])


async def test_webhook_pack_active_subscriber_gets_no_trial(trial_on, monkeypatch):
    w = trial_on
    seed_active(w, bypass_gb=3)
    await h.run_webhook(w, monkeypatch, pack_pending(15))
    assert set(jobs_by_key(w)) == {"purchase:pid-1"}
    assert w.panel2.bypass_limit(HTG) == 18 * GIB
    assert w.trial["used_at"] is None and w.grant_calls == []


async def test_webhook_pack_gift_failure_never_breaks_the_purchase(trial_on, monkeypatch):
    w = trial_on
    monkeypatch.setattr(trial_service, "_attempt_gift", AsyncMock(side_effect=RuntimeError("tx failed")))
    retry = MagicMock()
    monkeypatch.setattr(trial_service, "_schedule_gift_retry", retry)
    out = await h.run_webhook(w, monkeypatch, pack_pending(15))

    assert out["result"] == {"status": "ok"}
    assert set(jobs_by_key(w)) == {"purchase:pid-1"}
    assert w.panel2.bypass_limit(HTG) == 15 * GIB
    gift_alerts = [c for c in w.alerts.await_args_list
                   if "Bypass-purchase gift (3 days premium) FAILED" in c.args[2]]
    assert len(gift_alerts) == 1 and gift_alerts[0].kwargs["force"] is True
    assert gift_alerts[0].args[0] is out["bot"]
    retry.assert_called_once()
    assert retry.call_args.kwargs["via_outbox"] is True
    text = sent_text(out["bot"])
    assert text.startswith(text_prefix(w, "bypass.purchase_success"))
    assert gift_line(w) not in text


async def test_webhook_pack_panel_down_gift_job_pending_user_still_told(trial_on, monkeypatch):
    w = trial_on
    w.panel2.mode = "down"
    out = await h.run_webhook(w, monkeypatch, pack_pending(15))
    assert out["result"] == {"status": "ok"}
    by_key = jobs_by_key(w)
    assert by_key["purchase:pid-1"]["status"] == "pending"
    # the gift job is queued behind the pack job (per-user FIFO), then the worker applies both
    assert by_key[f"trial:{HTG}"]["status"] == "pending"
    assert w.trial["used_at"] is not None
    text = sent_text(out["bot"])
    assert gift_line(w) in text
    assert i18n_get_text("ru", "bypass.activation_delayed") in text
    assert any(c.kwargs.get("force") for c in w.alerts.await_args_list)
    w.panel2.mode = "ok"
    for job in sorted(by_key.values(), key=lambda r: r["id"]):
        assert await provisioning.run_now(job["id"]) is True
    assert w.panel2.bypass_limit(HTG) == 15 * GIB     # the GB bought, no trial 500 MB
    assert w.panel2.premium_expire(HTG) is not None


async def test_telegram_fsm_bypass_only_calls_trial_under_flag_but_paid_user_is_not_eligible(
        trial_on, monkeypatch):
    """payments_messages call site: reached only with FSM bypass_only_gb (set nowhere in
    the bot). The buyer has just paid for a subscription → trial not available."""
    w = trial_on
    spy = spy_safely(monkeypatch)
    message, _bot = await run_tg(w, monkeypatch, make_pending(), fsm={"bypass_only_gb": 5})
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["where"] == "telegram:pid-1"
    assert spy.await_args.args[0] == HTG
    assert set(jobs_by_key(w)) == {"purchase:pid-1"}
    assert w.trial["used_at"] is None
    assert message.answer.await_count == 1  # the purchase success message


async def test_telegram_fsm_bypass_only_trial_failure_alerts_and_keeps_purchase(trial_on, monkeypatch):
    w = trial_on
    monkeypatch.setattr(trial_service, "activate_trial", AsyncMock(side_effect=RuntimeError("boom")))
    message, bot = await run_tg(w, monkeypatch, make_pending(), fsm={"bypass_only_gb": 5})
    assert jobs_by_key(w)["purchase:pid-1"]["status"] == "done"
    assert message.answer.await_count == 1
    trial_alerts = [c for c in w.alerts.await_args_list if "Trial activation after purchase FAILED" in c.args[2]]
    assert len(trial_alerts) == 1 and trial_alerts[0].args[0] is bot


@pytest.fixture
def balance_trial_on(env, monkeypatch):
    """Legacy balance branch (flag "balance" off) — the only place the balance call
    site lives — with the "trial" entry point on."""
    _flag(monkeypatch, "on", "trial")
    env.alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", env.alerts)
    env.seed_active_subscription(HTG)
    return install_trial_state(env, monkeypatch)


async def test_balance_bypass_only_calls_trial_under_flag_but_paid_user_is_not_eligible(
        balance_trial_on, monkeypatch):
    w = balance_trial_on
    spy = spy_safely(monkeypatch)
    callback = await run_balance(w, monkeypatch, bypass_only_gb=5)
    spy.assert_awaited_once()
    assert spy.await_args.kwargs["where"].startswith("balance:")
    assert w.jobs.rows == {} and w.trial["used_at"] is None
    assert not [c for c in w.grant_calls if c["source"] == "trial"]
    assert callback.message.answer.await_count >= 1
    w.db.set_bypass_only_flag.assert_awaited_once_with(HTG, True)


async def test_balance_bypass_only_trial_failure_alerts_and_keeps_purchase(balance_trial_on, monkeypatch):
    w = balance_trial_on
    monkeypatch.setattr(trial_service, "activate_trial", AsyncMock(side_effect=RuntimeError("boom")))
    callback = await run_balance(w, monkeypatch, bypass_only_gb=5)
    success = [c for c in callback.message.answer.await_args_list
               if "errors.payment_processing" not in str(c)]
    assert success, "the purchase success message is still sent"
    assert "errors.payment_processing" not in w.i18n_keys
    w.alerts.assert_awaited_once()
    assert w.alerts.await_args.kwargs["force"] is True


# ── the «Только обход» gift does not depend on the "trial" flag ───────
# (owner, 2026-09-14). The balance / Telegram FSM call sites below it still do.

async def test_trial_flag_off_webhook_pack_still_grants_the_gift_via_outbox(outbox, monkeypatch):
    w = outbox  # webhook + telegram on, "trial" off → the pack job exists → the gift job too
    install_trial_state(w, monkeypatch)
    spy = spy_safely(monkeypatch)
    before = h.utcnow()
    out = await h.run_webhook(w, monkeypatch, pack_pending(15))
    spy.assert_not_awaited()               # the gift is not the post-purchase trial hook
    assert_pack_plus_gift(w, 15, before)
    assert gift_line(w) in sent_text(out["bot"])


async def test_all_flags_off_legacy_webhook_pack_grants_exact_gb_and_the_gift(env, monkeypatch):
    w = env  # everything off: legacy pack delivery, then the legacy gift grant
    w.jobs = SimpleNamespace(rows={})
    install_trial_state(w, monkeypatch)
    before = h.utcnow()
    out = await h.run_webhook(w, monkeypatch, pack_pending(15))
    assert out["result"]["status"] == "ok"
    [g] = w.grant_calls
    assert (g["source"], g["duration"], g["defer_panel"], g["caller_holds_tx"]) == \
        ("trial", timedelta(days=3), False, False)
    assert w.panel.added_bytes(HTG) == 15 * GIB          # the GB bought — no trial 500 MB
    assert _close(h.aware(w.panel.premium_expire[HTG]), before + timedelta(days=3))
    assert w.trial["used_at"] is not None
    text = sent_text(out["bot"])
    assert gift_line(w) in text and "задерживается" not in text


async def test_flag_off_balance_and_telegram_bypass_only_do_not_activate_trial(env, monkeypatch):
    w = env
    w.seed_active_subscription(HTG)
    spy = spy_safely(monkeypatch)
    await run_balance(w, monkeypatch, bypass_only_gb=5)
    await run_tg(w, monkeypatch, make_pending(purchase_id="pid-2"), fsm={"bypass_only_gb": 5})
    spy.assert_not_awaited()
    assert not [c for c in w.grant_calls if c["source"] == "trial"]
