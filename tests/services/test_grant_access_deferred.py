"""T7 — deferred panel work: grant_access(defer_panel), complete_activation,
the provisioning completion hook and the activation_worker open-job filter.
Spec: docs/audit/02_payment_core_plan.md §A.5, §B.6, task T7.

Hermetic (no Postgres, no HTTP):
* ``SubsConn`` — a fake asyncpg connection that keeps ONE subscriptions row and
  records every statement (normalized SQL + args);
* the clock inside ``database.subscriptions`` is frozen, so the
  ``defer_panel=False`` goldens (returned dict + every SQL statement with its
  args + watchdog / audit / panel calls) are byte-exact. They were captured from
  the pre-T7 implementation: any drift in the default path fails here;
* deferred tests replace EVERY coroutine of purchase_flow / remnawave_* (and
  the httpx transport) with stubs that record and raise — zero panel calls.
"""
from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timedelta, timezone

import pytest

import config
import database
import database.subscriptions as db_subs
from app.services import (
    provisioning, purchase_flow, remnawave_api, remnawave_bypass, remnawave_premium, remnawave_service,
)
from app.services.activation import service as activation_service
from app.services.tariffs import for_grant, for_pack, for_purchase
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeConn as JobConn
from tests.fakes.provisioning import FakeDB, FakeJobs

FIXED_NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
TG = 777_001
DUR = timedelta(days=30)
OLD_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
NEW_UUID = "11111111-2222-4333-8444-555555555555"
OLD_KEY = "https://panel.test/sub/old"
OLD_KEY_PLUS = "https://panel.test/sub/old-bypass"
PREMIUM_URL = "https://panel.test/sub/prem"
BYPASS_URL = "https://panel.test/sub/bypass"
PROVISION_RESULT = {
    "uuid": NEW_UUID, "vless_url": PREMIUM_URL, "vless_url_plus": BYPASS_URL,
    "subscription_type": "basic", "bypass_created_fresh": True,
}
PRE_PROVISIONED = {"uuid": NEW_UUID, "vless_url": PREMIUM_URL, "vless_url_plus": BYPASS_URL,
                   "subscription_type": "basic"}


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is not None else FIXED_NOW.replace(tzinfo=None)


def naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _norm(sql: str) -> str:
    return " ".join(sql.split()).lower()


# ── fake asyncpg with one subscriptions row ────────────────────────────

class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class SubsConn:
    """Emulates the statements grant_access / complete_activation issue."""

    def __init__(self, sub=None, *, in_tx=False):
        self.sub = dict(sub) if sub else None
        self.in_tx = in_tx
        self.calls = []

    def is_in_transaction(self):
        return self.in_tx

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchrow", s, args))
        if "from users" in s:
            return {"trial_expires_at": None}
        if "from subscriptions" in s:
            return dict(self.sub) if self.sub else None
        return None

    async def fetchval(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchval", s, args))
        if s.startswith("select id from subscriptions"):
            return 1
        return None

    async def execute(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("execute", s, args))
        base = dict(self.sub or {})
        if s.startswith("insert into subscriptions (") and "vpn_key_plus" in s.split("values")[0]:
            tg, uuid, key, key_plus, end, _src, _days, _start, act, stype, _country = args
            base.update(telegram_id=tg, status="active", expires_at=end, activation_status=act,
                        subscription_type=stype, is_bypass_only=False)
            base["uuid"] = uuid or base.get("uuid")
            base["vpn_key"] = key or base.get("vpn_key")
            base["vpn_key_plus"] = key_plus or base.get("vpn_key_plus")
            self.sub = base
            return "INSERT 0 1"
        if s.startswith("insert into subscriptions ("):
            tg, end, _src, _days, _start, _country, stype = args
            base.update(telegram_id=tg, uuid=None, vpn_key=None, status="active", expires_at=end,
                        activation_status="pending", subscription_type=stype, is_bypass_only=False)
            self.sub = base
            return "INSERT 0 1"
        if s.startswith("update subscriptions set activation_status = 'active'"):
            # complete_activation: WHERE telegram_id = $1 AND activation_status = 'pending'
            assert "where telegram_id = $1 and activation_status = 'pending'" in s
            if not self.sub or self.sub.get("activation_status") != "pending":
                return "UPDATE 0"
            tg, key, key_plus, uuid = args
            self.sub["activation_status"] = "active"
            self.sub["last_activation_error"] = None
            for col, val in (("vpn_key", key), ("vpn_key_plus", key_plus), ("uuid", uuid)):
                if val is not None:  # COALESCE($n, col)
                    self.sub[col] = val
            return "UPDATE 1"
        if s.startswith("update subscriptions"):
            self.sub["expires_at"] = args[0]
            if "subscription_type = 'plus'" in s:
                self.sub["subscription_type"] = "plus"
            elif "subscription_type = 'basic'" in s:
                self.sub["subscription_type"] = "basic"
            elif "coalesce($5, subscription_type)" in s and args[4]:
                self.sub["subscription_type"] = args[4]
            self.sub["activation_status"] = "active"
            return "UPDATE 1"
        return "INSERT 0 1"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _Acq()

    async def release(self, conn):
        return None


def active_row(*, tariff="basic", days_left=10):
    return {
        "telegram_id": TG, "uuid": OLD_UUID, "vpn_key": OLD_KEY, "vpn_key_plus": OLD_KEY_PLUS,
        "expires_at": naive(FIXED_NOW + timedelta(days=days_left)), "status": "active",
        "subscription_type": tariff, "activation_status": "active",
        "activated_at": naive(FIXED_NOW - timedelta(days=20)), "is_bypass_only": False,
    }


def expired_row():
    row = active_row()
    row.update(expires_at=naive(FIXED_NOW - timedelta(days=5)), status="expired")
    return row


# ── recorders / panel stubs ────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.watchdog = []
        self.audit = []
        self.panel = []
        self.forbidden = []

    def watchdog_hook(self, telegram_id, **kw):
        self.watchdog.append((telegram_id, kw))

    async def audit_hook(self, *a, **kw):
        self.audit.append((a, kw))


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(db_subs, "datetime", _FrozenDatetime)
    monkeypatch.setattr(config, "VPN_ENABLED", True)
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(db_subs, "_notify_watchdog_expires_at", r.watchdog_hook)
    monkeypatch.setattr(db_subs, "_log_vpn_lifecycle_audit_async", r.audit_hook)
    return r


def legacy_panel(monkeypatch, r: Recorder) -> None:
    """Today's panel surface of grant_access, recording its calls."""
    async def provision(telegram_id, **kw):
        r.panel.append(("provision_subscription", telegram_id, kw))
        return dict(PROVISION_RESULT)

    async def sync(payload):
        r.panel.append(("sync_renewal_to_remnawave", dict(payload)))

    monkeypatch.setattr(purchase_flow, "provision_subscription", provision)
    monkeypatch.setattr(purchase_flow, "sync_renewal_to_remnawave", sync)


def strict_no_panel(monkeypatch, r: Recorder) -> None:
    """Every panel coroutine (and the HTTP transport) records and raises."""
    def forbid(name):
        async def _stub(*_a, **_k):
            r.forbidden.append(name)
            raise AssertionError(f"panel/HTTP call in defer_panel mode: {name}")
        return _stub

    for mod in (purchase_flow, remnawave_api, remnawave_premium, remnawave_bypass, remnawave_service):
        for name, obj in list(vars(mod).items()):
            if inspect.iscoroutinefunction(obj) and getattr(obj, "__module__", "") == mod.__name__:
                monkeypatch.setattr(mod, name, forbid(f"{mod.__name__}.{name}"))
    try:
        import httpx
        monkeypatch.setattr(httpx.AsyncClient, "send", forbid("httpx.AsyncClient.send"))
    except ImportError:  # pragma: no cover
        pass
    try:
        import aiohttp
        monkeypatch.setattr(aiohttp.ClientSession, "_request", forbid("aiohttp.ClientSession._request"))
    except ImportError:  # pragma: no cover
        pass


async def grant(conn, **kw):
    kw.setdefault("source", "payment")
    return await db_subs.grant_access(telegram_id=TG, duration=DUR, conn=conn, **kw)


def snapshot(result, conn, r: Recorder) -> str:
    blob = repr((sorted(result.items()), conn.calls, r.watchdog, r.audit, r.panel))
    return hashlib.sha256(blob.encode()).hexdigest()


# ── defer_panel=False: byte-identical to the pre-T7 implementation ─────

SCENARIOS = {
    "new_standalone": (None, {}),
    "new_expired_standalone": (expired_row, {}),
    "new_preprovisioned_in_tx": (None, {"_caller_holds_transaction": True,
                                        "pre_provisioned_uuid": PRE_PROVISIONED}),
    "pending_vpn_disabled": (None, {"_vpn_enabled": False}),
    "renewal_standalone": (active_row, {}),
    "renewal_in_tx": (active_row, {"_caller_holds_transaction": True}),
    "admin_renewal_standalone": (active_row, {"source": "admin", "admin_telegram_id": 1,
                                              "admin_grant_days": 30}),
    "upgrade_standalone": (lambda: active_row(tariff="basic"), {"tariff": "plus"}),
    "downgrade_in_tx": (lambda: active_row(tariff="plus"), {"tariff": "basic",
                                                            "_caller_holds_transaction": True}),
}

# sha256 of (returned dict, SQL + args, watchdog, audit, panel calls), captured
# from the pre-T7 grant_access (commit dad10749) with the clock frozen at FIXED_NOW.
# Re-pinned for the notification P0 fixes (docs/notifications/bugs-and-risks.md):
# the ONLY change per scenario is the subscriptions UPDATE/UPSERT text —
# "reminder_7d_sent = FALSE, reminder_1d_sent = FALSE" (N-01), admin_grant_days
# cleared on paid grants (N-04: "= NULL" in the tariff-switch branches, "CASE WHEN
# $6 …" + one bool arg in the renewal branch). Returned dicts, watchdog, audit
# and panel calls are byte-identical to the pre-fix run (verified by diffing the
# full call dumps).
# Re-pinned for M-BYPASS-STALE-KEY (docs/audit/03_payment_matrix.md §4): the ONLY
# change is "is_bypass_only = FALSE" appended to the tariff-switch UPDATEs
# (upgrade_standalone, downgrade_in_tx) — a paid Basic↔Plus switch on a
# bypass-only row must leave a normal paid row. Other scenarios are unchanged.
GOLDEN = {
    "admin_renewal_standalone": "b037660ab87d8642ccd39965c9bd4b3dca09d5375561cc7b1822fdc24b5d8060",
    "downgrade_in_tx": "a5cec62b43e816aaa713476ffe03ea8fd27975a6f2c0901afc51a8d262a55dff",
    "new_expired_standalone": "35f3d0dd879b3412b275165115ce8ffe54f7de1b55006a6639c93db2c0ca590f",
    "new_preprovisioned_in_tx": "4e6b6f7f8c362fe37b5db8cebe4f57543f3d31403da3b780820099c19c264d6e",
    "new_standalone": "c873284a7573c5d05c011437f1cb8f59c3e73dbf1eca0eb4fbe2eec815d2bebe",
    "pending_vpn_disabled": "239c159d080e07f4fb97610097eb51002fcf1d48f5f6bf128382c3a47d6b3858",
    "renewal_in_tx": "0ad3e5e0a18123be982d049e8e386b152c914d7efe118250c1fde88599015a0c",
    "renewal_standalone": "9dfe96c32cd30e343d54f1beb600c1b87b35f035f3a6a6f46082fb34983cc74a",
    "upgrade_standalone": "5490cfd5811237ea78e2108dea216bbfef195ce7e306e52df1309f5352b72939",
}


async def run_scenario(monkeypatch, r: Recorder, name: str, **extra):
    seed, kw = SCENARIOS[name]
    kw = dict(kw)
    if not kw.pop("_vpn_enabled", True):
        monkeypatch.setattr(config, "VPN_ENABLED", False)
    conn = SubsConn(seed() if seed else None, in_tx=kw.get("_caller_holds_transaction", False))
    result = await grant(conn, **kw, **extra)
    return result, conn


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_default_path_matches_pre_t7_golden(monkeypatch, rec, name):
    legacy_panel(monkeypatch, rec)
    result, conn = await run_scenario(monkeypatch, rec, name)
    assert "deferred" not in result
    digest = snapshot(result, conn, rec)
    assert GOLDEN.get(name) == digest, f"{name}: {digest}"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_explicit_defer_false_equals_omitted(monkeypatch, rec, name):
    legacy_panel(monkeypatch, rec)
    result, conn = await run_scenario(monkeypatch, rec, name, defer_panel=False)
    assert snapshot(result, conn, rec) == GOLDEN[name]


async def test_default_new_issuance_still_provisions_inline(monkeypatch, rec):
    legacy_panel(monkeypatch, rec)
    result, conn = await run_scenario(monkeypatch, rec, "new_standalone")
    assert rec.panel == [("provision_subscription", TG, {
        "tariff": "basic", "subscription_end": FIXED_NOW + DUR, "period_days": 30, "is_trial": False,
    })]
    assert result["action"] == "new_issuance" and result["uuid"] == NEW_UUID
    assert conn.sub["activation_status"] == "active" and conn.sub["vpn_key"] == PREMIUM_URL


async def test_default_renewal_still_syncs_inline_or_returns_payload(monkeypatch, rec):
    legacy_panel(monkeypatch, rec)
    await run_scenario(monkeypatch, rec, "renewal_standalone")
    end = FIXED_NOW + timedelta(days=10) + DUR
    assert rec.panel == [("sync_renewal_to_remnawave", {
        "telegram_id": TG, "uuid": OLD_UUID, "subscription_end": end, "tariff": "basic", "period_days": 30,
    })]
    rec.panel.clear()
    result, _ = await run_scenario(monkeypatch, rec, "renewal_in_tx")
    assert rec.panel == []
    assert result["renewal_xray_sync_after_commit"]["subscription_end"] == end


# ── defer_panel=True: DB only, zero panel / HTTP ───────────────────────

async def test_deferred_new_issuance_is_pending_with_zero_http(monkeypatch, rec):
    strict_no_panel(monkeypatch, rec)
    conn = SubsConn(None)
    result = await grant(conn, defer_panel=True)
    assert result == {"uuid": None, "vless_url": None, "subscription_end": FIXED_NOW + DUR,
                      "action": "pending_activation", "deferred": True}
    assert conn.sub["activation_status"] == "pending" and conn.sub["status"] == "active"
    assert conn.sub["uuid"] is None and conn.sub["vpn_key"] is None
    assert conn.sub["expires_at"] == naive(FIXED_NOW + DUR)
    assert rec.forbidden == []


async def test_deferred_new_issuance_same_sql_as_existing_pending_branch(monkeypatch, rec):
    """defer_panel reuses the pending_activation branch (VPN_ENABLED=False) verbatim."""
    legacy_panel(monkeypatch, rec)
    ref, ref_conn = await run_scenario(monkeypatch, rec, "pending_vpn_disabled")
    monkeypatch.setattr(config, "VPN_ENABLED", True)
    strict_no_panel(monkeypatch, rec)
    conn = SubsConn(None)
    result = await grant(conn, defer_panel=True)
    assert result == {**ref, "deferred": True}
    assert conn.calls == ref_conn.calls
    assert rec.forbidden == []


async def test_deferred_new_issuance_inside_callers_tx_needs_no_pre_provisioned_uuid(monkeypatch, rec):
    strict_no_panel(monkeypatch, rec)
    conn = SubsConn(None, in_tx=True)
    result = await grant(conn, defer_panel=True, _caller_holds_transaction=True)  # no INVARIANT raise
    assert result["action"] == "pending_activation" and result["deferred"] is True
    assert conn.sub["activation_status"] == "pending"
    assert rec.forbidden == []


async def test_deferred_new_issuance_for_expired_subscription(monkeypatch, rec):
    strict_no_panel(monkeypatch, rec)
    conn = SubsConn(expired_row())
    result = await grant(conn, defer_panel=True, tariff="plus")
    assert result["action"] == "pending_activation" and result["subscription_end"] == FIXED_NOW + DUR
    assert conn.sub["activation_status"] == "pending" and conn.sub["subscription_type"] == "plus"
    assert "old_uuid_to_remove_after_commit" not in result
    assert rec.forbidden == []


async def test_deferred_with_vpn_disabled_is_still_pending(monkeypatch, rec):
    monkeypatch.setattr(config, "VPN_ENABLED", False)
    strict_no_panel(monkeypatch, rec)
    result = await grant(SubsConn(None), defer_panel=True)
    assert result["action"] == "pending_activation" and result["deferred"] is True
    assert rec.forbidden == []


async def test_deferred_with_pre_provisioned_uuid_keeps_it(monkeypatch, rec):
    """A pre-provisioned UUID is DB-only work already: it is written, never dropped."""
    strict_no_panel(monkeypatch, rec)
    conn = SubsConn(None, in_tx=True)
    result = await grant(conn, defer_panel=True, _caller_holds_transaction=True,
                         pre_provisioned_uuid=PRE_PROVISIONED)
    assert result["action"] == "new_issuance" and result["uuid"] == NEW_UUID and result["deferred"] is True
    assert conn.sub["uuid"] == NEW_UUID and conn.sub["activation_status"] == "active"
    assert rec.forbidden == []


@pytest.mark.parametrize("name", ["renewal_standalone", "renewal_in_tx", "admin_renewal_standalone",
                                  "upgrade_standalone", "downgrade_in_tx"])
async def test_deferred_renewal_is_db_only_same_formula_no_sync_payload(monkeypatch, rec, name):
    legacy_panel(monkeypatch, rec)
    ref, ref_conn = await run_scenario(monkeypatch, rec, name)
    ref.pop("renewal_xray_sync_after_commit", None)
    rec.panel.clear()
    strict_no_panel(monkeypatch, rec)

    result, conn = await run_scenario(monkeypatch, rec, name, defer_panel=True)

    assert result == {**ref, "deferred": True}
    assert "renewal_xray_sync_after_commit" not in result
    assert result["action"] == "renewal"
    # unchanged formula: max(now, current) + duration
    assert result["subscription_end"] == FIXED_NOW + timedelta(days=10) + DUR
    assert conn.sub["expires_at"] == naive(FIXED_NOW + timedelta(days=10) + DUR)
    assert conn.calls == ref_conn.calls  # identical DB work
    assert rec.forbidden == [] and rec.panel == []


# ── complete_activation ────────────────────────────────────────────────

def pending_row():
    row = active_row()
    row.update(uuid=None, vpn_key=None, vpn_key_plus=None, activation_status="pending")
    return row


def test_complete_activation_is_exported():
    assert database.complete_activation is db_subs.complete_activation


async def test_complete_activation_activates_pending_once():
    conn = SubsConn(pending_row())
    assert await db_subs.complete_activation(
        TG, vpn_key=PREMIUM_URL, vpn_key_plus=BYPASS_URL, uuid=NEW_UUID, conn=conn) is True
    assert conn.sub["activation_status"] == "active"
    assert (conn.sub["vpn_key"], conn.sub["vpn_key_plus"], conn.sub["uuid"]) == (PREMIUM_URL, BYPASS_URL, NEW_UUID)
    # idempotent: second call changes nothing
    assert await db_subs.complete_activation(
        TG, vpn_key="https://other", vpn_key_plus=None, uuid=None, conn=conn) is False
    assert conn.sub["vpn_key"] == PREMIUM_URL
    ((_, sql, args),) = [c for c in conn.calls[:1]]
    assert "where telegram_id = $1 and activation_status = 'pending'" in sql
    assert args == (TG, PREMIUM_URL, BYPASS_URL, NEW_UUID)


async def test_complete_activation_ignores_active_row():
    conn = SubsConn(active_row())
    assert await db_subs.complete_activation(TG, vpn_key=PREMIUM_URL, vpn_key_plus=BYPASS_URL, conn=conn) is False
    assert conn.sub["vpn_key"] == OLD_KEY and conn.sub["uuid"] == OLD_UUID


async def test_complete_activation_missing_row_is_false():
    assert await db_subs.complete_activation(TG, vpn_key=PREMIUM_URL, vpn_key_plus=None, conn=SubsConn(None)) is False


async def test_complete_activation_none_fields_keep_existing_values():
    row = pending_row()
    row.update(uuid=OLD_UUID, vpn_key_plus=OLD_KEY_PLUS)
    conn = SubsConn(row)
    assert await db_subs.complete_activation(TG, vpn_key=PREMIUM_URL, vpn_key_plus=None, conn=conn) is True
    assert (conn.sub["vpn_key"], conn.sub["vpn_key_plus"], conn.sub["uuid"]) == (PREMIUM_URL, OLD_KEY_PLUS, OLD_UUID)


async def test_complete_activation_without_conn_uses_pool(monkeypatch):
    conn = SubsConn(pending_row())

    async def get_pool():
        return _Pool(conn)
    monkeypatch.setattr(db_subs, "get_pool", get_pool)
    assert await db_subs.complete_activation(TG, vpn_key=PREMIUM_URL, vpn_key_plus=None) is True
    assert conn.sub["activation_status"] == "active"


# ── provisioning completes a pending activation ────────────────────────

GIB = 1024 ** 3
UNTIL = FIXED_NOW + timedelta(days=30)
LEGACY_UUID = "22222222-3333-4444-8555-666666666666"


class ActivationDB(FakeDB):
    """T4 FakeDB + complete_activation with the SQL's semantics."""

    def __init__(self):
        super().__init__()
        self.completions = []
        self.fail_completion = 0

    async def complete_activation(self, telegram_id, *, vpn_key, vpn_key_plus, uuid=None, conn=None):
        self.completions.append({"tg": telegram_id, "vpn_key": vpn_key,
                                 "vpn_key_plus": vpn_key_plus, "uuid": uuid})
        if self.fail_completion:
            self.fail_completion -= 1
            raise ConnectionError("db down")
        sub = self.subs.get(telegram_id)
        if not sub or sub.get("activation_status") != "pending":
            return False
        sub["activation_status"] = "active"
        for col, val in (("vpn_key", vpn_key), ("vpn_key_plus", vpn_key_plus), ("uuid", uuid)):
            if val is not None:
                sub[col] = val
        return True

    def install(self, monkeypatch):
        super().install(monkeypatch)
        monkeypatch.setattr(database, "complete_activation", self.complete_activation)
        return self


@pytest.fixture
def prov(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import payment_webhook
    from app.services import admin_alerts, sub_aggregator
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda tg: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)
    monkeypatch.setattr(admin_alerts, "send_alert", AsyncMock(return_value=True))
    panel = FakePanel().install(monkeypatch)
    jobs = FakeJobs(panel).install(monkeypatch)
    db = ActivationDB().install(monkeypatch)
    return panel, jobs, db


def seed_sub(db, *, status="pending", uuid=None):
    db.subs[TG] = {"telegram_id": TG, "uuid": uuid, "vpn_key": None, "vpn_key_plus": None,
                   "status": "active", "expires_at": UNTIL, "activation_status": status}


async def enqueue(key, ent, until=UNTIL):
    return await provisioning.enqueue(JobConn(), key=key, telegram_id=TG, ent=ent,
                                      premium_until=until, source="test")


async def test_apply_completes_pending_activation_with_premium_and_bypass_urls(prov):
    panel, jobs, db = prov
    seed_sub(db)
    job_id = await enqueue("purchase:1", for_purchase("basic", 30))
    assert await provisioning.run_now(job_id) is True
    prem, byp = panel.premium[TG], panel.bypass[TG]
    assert db.completions == [{"tg": TG, "vpn_key": prem["subscriptionUrl"],
                               "vpn_key_plus": byp["subscriptionUrl"], "uuid": prem["vlessUuid"]}]
    assert db.subs[TG]["activation_status"] == "active"
    assert jobs.job(job_id)["status"] == "done"


async def test_apply_present_premium_keeps_legacy_uuid(prov):
    panel, jobs, db = prov
    seed_sub(db, uuid=LEGACY_UUID)
    panel.seed_premium(TG, FIXED_NOW + timedelta(days=5))
    job_id = await enqueue("purchase:1", for_grant("basic", 30))
    assert await provisioning.run_now(job_id) is True
    assert db.completions == [{"tg": TG, "vpn_key": panel.premium[TG]["subscriptionUrl"],
                               "vpn_key_plus": None, "uuid": LEGACY_UUID}]


async def test_apply_does_not_touch_active_subscription(prov):
    panel, jobs, db = prov
    seed_sub(db, status="active")
    job_id = await enqueue("purchase:1", for_purchase("basic", 30))
    assert await provisioning.run_now(job_id) is True
    assert db.completions == []


async def test_completion_is_idempotent_across_jobs(prov):
    panel, jobs, db = prov
    seed_sub(db)
    assert await provisioning.run_now(await enqueue("purchase:1", for_purchase("basic", 30))) is True
    assert await provisioning.run_now(await enqueue("purchase:2", for_purchase("basic", 30))) is True
    assert len(db.completions) == 1


async def test_pack_job_without_premium_does_not_complete(prov):
    panel, jobs, db = prov
    seed_sub(db)
    job_id = await enqueue("pack:1", for_pack(15), until=None)
    assert await provisioning.run_now(job_id) is True
    assert db.completions == [] and db.subs[TG]["activation_status"] == "pending"


async def test_bypass_conflict_still_completes_premium_activation(prov):
    panel, jobs, db = prov
    seed_sub(db)
    panel.seed_bypass(TG, 3 * GIB)
    panel.mode = "conflict"
    job_id = await enqueue("purchase:1", for_purchase("basic", 30))
    assert await provisioning.run_now(job_id) is False
    assert jobs.job(job_id)["status"] == "dead"
    assert db.completions == [{"tg": TG, "vpn_key": panel.premium[TG]["subscriptionUrl"],
                               "vpn_key_plus": None, "uuid": panel.premium[TG]["vlessUuid"]}]
    assert db.subs[TG]["activation_status"] == "active"


async def test_completion_db_failure_retries_the_job(prov):
    panel, jobs, db = prov
    seed_sub(db)
    db.fail_completion = 1
    job_id = await enqueue("purchase:1", for_purchase("basic", 30))
    assert await provisioning.run_now(job_id) is False
    assert jobs.job(job_id)["status"] == "pending" and db.subs[TG]["activation_status"] == "pending"
    assert await provisioning.run_now(job_id) is True
    assert db.subs[TG]["activation_status"] == "active"
    assert panel.bypass_limit(TG) == 10 * GIB  # retry did not add the GB twice


# ── activation_worker skips users with an open provisioning job ────────

LEGACY_PENDING_SQL = (
    "select s.telegram_id, s.id, s.activation_attempts, s.last_activation_error, s.expires_at, "
    "s.activated_at, s.subscription_type, lp.price_kopecks, lp.period_days from subscriptions s "
    "left join lateral ( select pp.price_kopecks, pp.period_days from pending_purchases pp "
    "where pp.telegram_id = s.telegram_id and pp.status = 'paid' order by pp.created_at desc limit 1 "
    ") lp on true where s.activation_status = 'pending' and s.activation_attempts < $1 "
    "order by s.id asc limit $2"
)
# P2-9: a user whose outbox job went DEAD belongs to the admin (the dead alert
# carries the retry SQL) — the activation worker must not provision the legacy
# way on top (+10 GB, and a manual job retry would add them again).
OPEN_JOB_PREDICATE = (
    "and not exists ( select 1 from provisioning_jobs pj where pj.telegram_id = s.telegram_id "
    "and pj.status in ('pending','running','dead') )"
)


class ActConn:
    def __init__(self, regclass):
        self.regclass = regclass
        self.probes = 0
        self.fetches = []

    async def fetchval(self, sql, *args):
        assert _norm(sql) == "select to_regclass('public.provisioning_jobs')"
        self.probes += 1
        if isinstance(self.regclass, Exception):
            raise self.regclass
        return self.regclass

    async def fetch(self, sql, *args):
        self.fetches.append((_norm(sql), args))
        return [{"telegram_id": TG, "id": 5, "activation_attempts": 0, "last_activation_error": None,
                 "expires_at": naive(UNTIL), "activated_at": None, "subscription_type": "basic",
                 "price_kopecks": 19900, "period_days": 30}]


@pytest.fixture
def fresh_probe(monkeypatch):
    monkeypatch.setattr(activation_service, "_PROVISIONING_JOBS_TABLE_EXISTS", None)


async def test_worker_query_skips_open_jobs_when_table_exists(fresh_probe):
    conn = ActConn("provisioning_jobs")
    rows = await activation_service.get_pending_subscriptions(max_attempts=5, limit=50, conn=conn)
    await activation_service.get_pending_subscriptions(max_attempts=5, limit=50, conn=conn)
    assert [r.telegram_id for r in rows] == [TG]
    sql, args = conn.fetches[0]
    assert OPEN_JOB_PREDICATE in sql
    assert sql == LEGACY_PENDING_SQL.replace(
        "and s.activation_attempts < $1 ", f"and s.activation_attempts < $1 {OPEN_JOB_PREDICATE} ")
    assert args == (5, 50)
    assert conn.probes == 1  # probed once, cached


async def test_worker_query_unchanged_when_table_missing(fresh_probe):
    conn = ActConn(None)
    rows = await activation_service.get_pending_subscriptions(max_attempts=5, limit=50, conn=conn)
    await activation_service.get_pending_subscriptions(max_attempts=5, limit=50, conn=conn)
    assert [r.telegram_id for r in rows] == [TG]
    assert [s for s, _ in conn.fetches] == [LEGACY_PENDING_SQL, LEGACY_PENDING_SQL]
    assert conn.probes == 1


async def test_worker_probe_error_falls_back_to_legacy_query_and_reprobes(fresh_probe):
    conn = ActConn(RuntimeError("boom"))
    await activation_service.get_pending_subscriptions(max_attempts=5, limit=50, conn=conn)
    assert conn.fetches[0][0] == LEGACY_PENDING_SQL
    conn.regclass = "provisioning_jobs"
    await activation_service.get_pending_subscriptions(max_attempts=5, limit=50, conn=conn)
    assert OPEN_JOB_PREDICATE in conn.fetches[1][0]
    assert conn.probes == 2


# ── T0 harness models defer_panel ──────────────────────────────────────

async def test_harness_fake_grant_access_defers_panel(monkeypatch):
    from tests.services import payment_core_harness as h
    w = h.install(monkeypatch)
    w.seed_active_subscription(h.TG, bypass_gb=3, days_left=10)
    before = w.sub["expires_at"]
    res = await w.grant_access(telegram_id=h.TG, duration=DUR, source="payment", defer_panel=True)
    assert res["deferred"] is True and res["action"] == "renewal"
    assert "renewal_xray_sync_after_commit" not in res
    assert w.sub["expires_at"] == h.naive(h.aware(before) + DUR)
    assert not w.panel.touched

    w.sub = None
    res = await w.grant_access(telegram_id=h.TG, duration=DUR, source="payment",
                               _caller_holds_transaction=True, defer_panel=True)
    assert res["action"] == "pending_activation" and res["deferred"] is True
    assert w.sub["activation_status"] == "pending"
    assert not w.panel.touched
