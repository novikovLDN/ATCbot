"""
Payment matrix — ONE authoritative suite for every paid / granted access path
(docs/audit/03_payment_matrix.md). Owner request: "корректность платежей …
алерты админу обязательно".

Dimensions (generated programmatically):
  entry   webhook (platega / wata / cryptobot / WATA reconciler), telegram card,
          telegram stars, balance, auto-renewal, admin grant, gift activation,
          trial, traffic pack, balance top-up
  tariff  basic / plus × 30/90/180/365, combo_basic / combo_plus × 30/90/180/365/730,
          traffic packs (standard + extended), legacy biz_* row (auto-renewal → Plus)
  state   new, active (premium T+10d, bypass 3 GB), expired (T-5d, bypass 0),
          bypass_only (bypass 5 GB, no premium), trial (T+2d, bypass 500 MB)
  flag    USE_NEW_PROVISIONING off / on (only the cell's entry point enabled)

Every cell is checked by ONE helper (`check`) against the owner rules
(docs/audit/SCOPE.md):
  * DB subscriptions.expires_at == max(now, current_expires_at) + period
    (current only counts while the subscription is really active — bypass-only
    and expired rows start from now), and the panel premium expireAt equals it
    to the second (legacy PATCH floors to the second, the outbox ceils);
  * bypass limit == previous + expected (basic/plus +10 GB, combo = combo GB only,
    pack +N, day grants 0, trial 500 MB); premium untouched where no days are due;
  * exactly one committed payments row where money moved (0 for grants);
  * subscription_type / is_combo / is_bypass_only as purchased.
Plus idempotency (replay → no change), panel-down behaviour (money kept, job
pending or legacy retry, forced admin alert), amount / provider mismatch and
balance top-up amount.

Flag-off cells that are KNOWN legacy bugs are xfail(strict=True) with the bug id
(T0 list in docs/audit/02_payment_core_plan.md); flag-on cells must pass.

What is REAL: every entry point's production code down to the panel client,
including database.subscriptions.grant_access (not the harness contract fake),
finalize_purchase / finalize_balance_purchase / admin / gift / auto-renewal /
trial / grant_outbox, confirmation, provisioning (enqueue / run_now / apply /
worker tick) and the legacy Remnawave layer (purchase_flow, remnawave_bypass,
remnawave_service).
What is FAKED: asyncpg (`MatrixConn`: one subscriptions row, pending purchase,
gift, payments, balance — with transaction ROLLBACK semantics per connection),
the panel (legacy: payment_core_harness.FakePanel; outbox: tests/fakes/panel),
the outbox table (tests/fakes/provisioning.FakeJobs) and Telegram.
"""
from __future__ import annotations

import asyncio
import copy
import functools
import itertools
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
import database
import database.subscriptions as db_subs
from app.api import payment_webhook
from app.services import (
    admin_alerts, provisioning, purchase_flow, remnawave_bypass, remnawave_service,
)
from app.services.payments import confirmation
from app.services.payments.confirmation import TransientPaymentError
from app.workers import provisioning_worker
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeJobs
from tests.services import payment_core_harness as h
from tests.services.payment_core_harness import GB, OTHER_TG, TG

MB = 1024 ** 2
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"
REAL_GRANT_ACCESS = db_subs.grant_access
REAL_CONSUME_PROMO = db_subs._consume_promo_in_transaction
REAL_SLEEP = asyncio.sleep
BOT = MagicMock(name="bot")

legacy_bug = functools.partial(pytest.mark.xfail, strict=True, raises=AssertionError)


@pytest.fixture(autouse=True)
async def _isolate_legacy_resync():
    """purchase_flow keeps process-level state (forced-alert budget, pending
    background re-syncs): fresh per cell, nothing left running afterwards."""
    purchase_flow._forced_alert_times.clear()
    yield
    tasks = list(purchase_flow._resync_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    purchase_flow._resync_tasks.clear()
    purchase_flow._forced_alert_times.clear()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ═══════════════════════════════════════════════════════════════════════
# Fake asyncpg with per-connection transactions (rollback restores state)
# ═══════════════════════════════════════════════════════════════════════

class _Tx:
    def __init__(self, conn: "MatrixConn"):
        self.conn = conn

    async def __aenter__(self):
        self.conn.undo.append({})
        return self

    async def __aexit__(self, exc_type, *_exc):
        level = self.conn.undo.pop()
        if exc_type is not None:
            for key, old in level.items():
                self.conn.w.restore(key, old)
        elif self.conn.undo:
            parent = self.conn.undo[-1]
            for key, old in level.items():
                parent.setdefault(key, old)
        return False


class MatrixConn(h.FakeConn):
    """One asyncpg connection. Each pool.acquire() gets its own, so a write on a
    connection WITHOUT a transaction autocommits even while another connection's
    transaction is later rolled back — exactly like Postgres."""

    def __init__(self, world):
        super().__init__(world)
        self.undo: list = []

    def is_in_transaction(self) -> bool:
        return bool(self.undo)

    def transaction(self):
        return _Tx(self)

    def _touch(self, key: str) -> None:
        if self.undo and key not in self.undo[-1]:
            self.undo[-1][key] = self.w.snapshot_key(key)

    # ── reads ───────────────────────────────────────────────────────────
    async def fetchrow(self, sql, *args):
        s = self._norm(sql)
        w = self.w
        if "trial_used_at" in s and "from users u" in s:
            self.calls.append(("fetchrow", s, args))
            sub = w.sub or {}
            return {"trial_used_at": w.trial_used_at, "status": sub.get("status"),
                    "expires_at": sub.get("expires_at"), "source": sub.get("source"),
                    "is_bypass_only": bool(sub.get("is_bypass_only"))}
        if "from provisioning_jobs" in s:
            self.calls.append(("fetchrow", s, args))
            job = w.job_by_key(args[0])
            return {"id": job["id"], "premium_until": job["premium_until"]} if job else None
        if "from payments where purchase_id" in s:
            self.calls.append(("fetchrow", s, args))
            rows = [p for p in w.payments if p.get("purchase_id") == args[0]]
            return dict(rows[-1]) if rows else None
        if "from payments where telegram_id = $1 and status = 'approved'" in s:
            # the user's newest approved payment; with `tariff ~ $2` only those whose
            # tariff matches the pattern the production query passes (Postgres `~`)
            self.calls.append(("fetchrow", s, args))
            rows = [p for p in w.payment_history + w.payments
                    if p.get("telegram_id") == args[0] and p.get("status", "approved") == "approved"]
            if "tariff ~ $2" in s:
                rows = [p for p in rows if re.search(args[1], str(p.get("tariff") or ""))]
            return dict(rows[-1]) if rows else None
        if "promo_codes" in s:
            self.calls.append(("fetchrow", s, args))
            return self._promo(s, args)
        return await super().fetchrow(sql, *args)

    def _promo(self, s: str, args: tuple):
        """promo_codes: one row (w.promo) — active lookup, conditional consume
        (by id or by code) and the unconditional count of a paid purchase."""
        p = self.w.promo
        if p is None:
            return None
        live = p["is_active"] and (p["expires_at"] is None or p["expires_at"] > utcnow())
        free = p["max_uses"] is None or p["used_count"] < p["max_uses"]
        if s.startswith("select"):
            return dict(p) if str(args[0]).upper() == p["code"] and live and free else None
        if s.startswith("update promo_codes"):
            if "where id = $1" in s:
                ok = args[0] == p["id"] and free
            elif "case when" in s:                  # paid purchase: counted up to the cap
                if str(args[0]).upper() != p["code"]:
                    return None
                self._touch("promo")
                if free:
                    p["used_count"] += 1
                return dict(p)
            elif "used_count < max_uses" in s:
                ok = str(args[0]).upper() == p["code"] and live and free
            else:                                   # unconditional: counted even over the limit
                ok = str(args[0]).upper() == p["code"]
            if not ok:
                return None
            self._touch("promo")
            p["used_count"] += 1
            return dict(p)
        return None

    async def fetchval(self, sql, *args):
        s = self._norm(sql)
        w = self.w
        if "insert into payments" in s:
            self.calls.append(("fetchval", s, args))
            self._touch("payments")
            row = _parse_insert(s, args)
            row["id"] = next(w.payment_ids)
            w.payments.append(row)
            return row["id"]
        if "from provisioning_jobs" in s:
            self.calls.append(("fetchval", s, args))
            job = w.job_by_key(args[0])
            return job["id"] if job else None
        if "insert into traffic_purchases" in s:
            w.traffic_rows.append(args)
        if "information_schema.columns" in s and "promo_codes" in s:
            return None                             # no deleted_at column → plain active query
        return await super().fetchval(sql, *args)

    async def execute(self, sql, *args):
        s = self._norm(sql)
        w = self.w
        if s.startswith("update users set balance"):
            self._touch("balance")
        elif s.startswith("update pending_purchases set status = 'paid'"):
            self.calls.append(("execute", s, args))
            if not w.pending or w.pending.get("status") not in ("pending", "expired"):
                return "UPDATE 0"
            self._touch("pending")
            w.pending["status"] = "paid"
            if len(args) > 1:
                w.pending["payment_provider"] = args[1]
            return "UPDATE 1"
        elif s.startswith("update payments set status = 'approved'"):
            self._touch("payments")
            for p in w.payments:
                if p["id"] == args[0]:
                    p["status"] = "approved"
        elif s.startswith("update gift_subscriptions set status = 'activated'"):
            self._touch("gift")
            w.gift["status"] = "activated"
        elif s.startswith("update users set trial_used_at"):
            self.calls.append(("execute", s, args))
            if w.trial_used_at is not None:
                return "UPDATE 0"
            self._touch("trial")
            w.trial_used_at = utcnow()
            return "UPDATE 1"
        elif s.startswith("update subscriptions set last_auto_renewal_at"):
            self.calls.append(("execute", s, args))
            self._touch("sub")
            w.sub["last_auto_renewal_at"] = args[0]
            return "UPDATE 1"
        elif "subscriptions" in s.split(" where ")[0] and (
                s.startswith("update subscriptions") or s.startswith("insert into subscriptions")):
            self._touch("sub")
            _apply_subscription_write(w, s, args)
        return await super().execute(sql, *args)


def _parse_insert(s: str, args: tuple) -> dict:
    """{column: value} of an `INSERT INTO t (cols) VALUES (...)` statement."""
    cols = [c.strip() for c in s.split("(", 1)[1].split(")", 1)[0].split(",")]
    vals = [v.strip() for v in s.split("values", 1)[1].split("(", 1)[1].rsplit(")", 1)[0].split(",")]
    row = {}
    for col, val in zip(cols, vals):
        m = re.fullmatch(r"\$(\d+)", val)
        row[col] = args[int(m.group(1)) - 1] if m else val.strip("'")
    return row


def _apply_subscription_write(w, s: str, args: tuple) -> None:
    """The subscriptions statements of grant_access / balance outbox."""
    base = dict(w.sub or {})
    if s.startswith("insert into subscriptions (") and "vpn_key_plus" in s.split("values")[0]:
        tg, uuid, key, key_plus, end, src, _days, start, act, stype, _country = args
        base.update(telegram_id=tg, status="active", expires_at=end, activation_status=act,
                    subscription_type=stype or base.get("subscription_type"), source=src,
                    is_bypass_only=False, activated_at=start)
        base["uuid"] = uuid or base.get("uuid")
        base["vpn_key"] = key or base.get("vpn_key")
        base["vpn_key_plus"] = key_plus or base.get("vpn_key_plus")
        w.sub = base
    elif s.startswith("insert into subscriptions ("):
        tg, end, src, _days, start, _country, stype = args
        base.update(telegram_id=tg, uuid=None, vpn_key=None, status="active", expires_at=end,
                    activation_status="pending", subscription_type=stype or base.get("subscription_type"),
                    source=src, is_bypass_only=False, activated_at=start)
        w.sub = base
    elif s.startswith("update subscriptions set is_combo = true"):
        w.sub["is_combo"] = True
    elif s.startswith("update subscriptions set expires_at = $1"):
        w.sub["expires_at"] = args[0]
        w.sub["status"] = "active"
        w.sub["activation_status"] = "active"
        if "is_bypass_only = false" in s:
            w.sub["is_bypass_only"] = False
        if "subscription_type = 'plus'" in s:
            w.sub["subscription_type"] = "plus"
        elif "subscription_type = 'basic'" in s:
            w.sub["subscription_type"] = "basic"
        elif "coalesce($5, subscription_type)" in s:
            if args[4]:
                w.sub["subscription_type"] = args[4]
            w.sub["uuid"] = args[3]
            w.sub["is_bypass_only"] = False
        w.sub["source"] = args[1]


class _Acq:
    """pool.acquire(): awaitable (grant_access standalone) and async CM."""

    def __init__(self, conn):
        self.conn = conn

    def __await__(self):
        async def _get():
            return self.conn
        return _get().__await__()

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class MatrixPool:
    def __init__(self, world):
        self.w = world

    def acquire(self):
        conn = MatrixConn(self.w)
        self.w.conns.append(conn)
        return _Acq(conn)

    async def release(self, conn):
        return None


# ═══════════════════════════════════════════════════════════════════════
# World
# ═══════════════════════════════════════════════════════════════════════

class MatrixWorld(h.World):
    """harness World + committed payments rows, trial flag, notification flags."""

    def snapshot_key(self, key):
        return copy.deepcopy({
            "sub": self.sub, "pending": self.pending, "gift": self.gift,
            "balance": self.balance_kopecks, "payments": self.payments,
            "trial": self.trial_used_at, "promo": self.promo,
        }[key])

    def restore(self, key, old):
        attr = {"sub": "sub", "pending": "pending", "gift": "gift", "balance": "balance_kopecks",
                "payments": "payments", "trial": "trial_used_at", "promo": "promo"}[key]
        setattr(self, attr, old)

    def job_by_key(self, key):
        for row in self.jobs.rows.values():
            if row["idempotency_key"] == key:
                return row
        return None

    def jobs_list(self):
        return list(self.jobs.rows.values())

    # panel view — the panel the cell's mode actually writes to
    def premium_until(self) -> Optional[datetime]:
        if self.on:
            return self.panel2.premium_expire(TG)
        return aware(self.panel.premium_expire.get(TG))

    def bypass_bytes(self) -> int:
        if self.on:
            return int(self.panel2.bypass_limit(TG) or 0)
        return self.panel.bypass_bytes(TG)

    def forced_alerts(self) -> list:
        return [c for c in self.alerts if c["force"]]


def _legacy_writers() -> dict:
    return {
        "purchase_flow.provision_subscription": (purchase_flow, "provision_subscription"),
        "purchase_flow.sync_renewal_to_remnawave": (purchase_flow, "sync_renewal_to_remnawave"),
        "remnawave_service.add_bypass_traffic": (remnawave_service, "add_bypass_traffic"),
        "remnawave_bypass.add_bypass_traffic": (remnawave_bypass, "add_bypass_traffic"),
        "remnawave_service.renew_remnawave_user_bg": (remnawave_service, "renew_remnawave_user_bg"),
        "confirmation._deliver_bypass_gb": (confirmation, "_deliver_bypass_gb"),
    }


def build(monkeypatch, *, flag: str, entrypoint: str) -> MatrixWorld:
    """Fresh world: flag `on` enables ONLY `entrypoint`; `off` = legacy path."""
    monkeypatch.setattr(h, "World", MatrixWorld)
    w = h.install(monkeypatch)
    assert isinstance(w, MatrixWorld)
    w.on = flag == "on"
    w.payments, w.payment_ids, w.traffic_rows = [], itertools.count(501), []
    w.conns, w.trial_used_at, w.notified, w.promo = [], None, set(), None
    w.alerts, w.payment_errors, w.legacy_calls = [], [], []
    w.payment_history = []      # approved payments that exist before the cell runs (oldest first)
    w.pool = MatrixPool(w)
    w.conn = MatrixConn(w)
    w.panel_down = False

    # the REAL grant_access (the harness installs a contract fake)
    monkeypatch.setattr(db_subs, "grant_access", REAL_GRANT_ACCESS)
    monkeypatch.setattr(database, "grant_access", REAL_GRANT_ACCESS)
    monkeypatch.setattr(db_subs, "_notify_watchdog_expires_at", lambda *a, **k: None)
    monkeypatch.setattr(db_subs, "_log_vpn_lifecycle_audit_async", AsyncMock())

    async def fast_sleep(_delay=0, *a, **k):
        await REAL_SLEEP(0)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    # subscriptions readers: aware datetimes like the real _normalize_subscription_row
    def _aware_sub():
        if not w.sub:
            return None
        sub = dict(w.sub)
        sub["expires_at"] = aware(sub.get("expires_at"))
        return sub

    async def get_subscription_any(_tg):
        return _aware_sub()

    async def get_subscription(_tg):
        sub = _aware_sub()
        if sub and sub.get("status") == "active" and sub["expires_at"] and sub["expires_at"] > utcnow():
            return sub
        return None

    async def set_combo_flag(_tg, is_combo=True):
        if w.sub:
            w.sub["is_combo"] = bool(is_combo)

    async def set_bypass_only_flag(_tg, is_bypass_only=True):
        if w.sub:
            w.sub["is_bypass_only"] = bool(is_bypass_only)

    async def mark_notified(payment_id, *_a, **_k):
        if payment_id in w.notified:
            return False
        w.notified.add(payment_id)
        return True

    async def is_notified(payment_id, *_a, **_k):
        return payment_id in w.notified

    async def log_payment_error(**kw):
        w.payment_errors.append(kw)
        return len(w.payment_errors)

    async def complete_activation(_tg, *, vpn_key, vpn_key_plus, uuid=None, conn=None):
        if not w.sub or w.sub.get("activation_status") != "pending":
            return False
        w.sub.update(activation_status="active", vpn_key=vpn_key,
                     vpn_key_plus=vpn_key_plus or w.sub.get("vpn_key_plus"),
                     uuid=uuid or w.sub.get("uuid"))
        return True

    async def send_alert(bot, category, message, *, force=False):
        w.alerts.append({"category": category, "text": message, "force": force, "bot": bot})
        return True

    async def send_admin_notification(**kw):
        w.alerts.append({"category": "admin_notification", "text": kw.get("message"), "force": True,
                         "bot": kw.get("bot")})
        return True

    for mod in (database, db_subs):
        monkeypatch.setattr(mod, "set_combo_flag", set_combo_flag, raising=False)
        monkeypatch.setattr(mod, "set_bypass_only_flag", set_bypass_only_flag, raising=False)
    for name, fn in {
        "get_subscription_any": get_subscription_any, "get_subscription": get_subscription,
        "mark_payment_notification_sent": mark_notified, "is_payment_notification_sent": is_notified,
        "log_payment_error": log_payment_error, "complete_activation": complete_activation,
        "get_remnawave_premium_id": AsyncMock(return_value=None),
    }.items():
        monkeypatch.setattr(database, name, fn, raising=False)
    monkeypatch.setattr(admin_alerts, "send_alert", send_alert)
    import admin_notifications
    monkeypatch.setattr(admin_notifications, "send_admin_notification", send_admin_notification)
    from app.services.payments import verify_delivery
    monkeypatch.setattr(verify_delivery, "_send_admin_alert", AsyncMock())
    monkeypatch.setattr(payment_webhook, "_bot", BOT)
    provisioning.reset_alert_state()

    # legacy background re-sync (purchase_flow): waits until the test "recovers" the panel
    w.resync_gate = asyncio.Event()

    async def gated_sleep(_seconds):
        await w.resync_gate.wait()
    monkeypatch.setattr(purchase_flow, "_resync_sleep", gated_sleep)

    # legacy panel with a "down" switch (payment_core_harness.FakePanel has none)
    _install_downable_legacy_panel(w, monkeypatch)

    # outbox side
    monkeypatch.setenv(MODE_VAR, "off")
    monkeypatch.delenv(EP_VAR, raising=False)
    w.panel2 = FakePanel()
    w.jobs = FakeJobs(w.panel2).install(monkeypatch)
    if w.on:
        monkeypatch.setenv(MODE_VAR, "on")
        monkeypatch.setenv(EP_VAR, entrypoint)
        w.panel2.install(monkeypatch)
        for label, (mod, name) in _legacy_writers().items():
            monkeypatch.setattr(mod, name, _forbidden(w, label), raising=False)
    return w


def _forbidden(w, label):
    def _record(*_a, **_k):
        w.legacy_calls.append(label)
        raise AssertionError(f"legacy panel writer {label} used under the outbox flag")

    if label.endswith("_bg"):
        return _record

    async def _async(*a, **k):
        _record(*a, **k)
    return _async


def _install_downable_legacy_panel(w, monkeypatch) -> None:
    from app.services import remnawave_api, remnawave_premium
    from app.services.remnawave_premium import PremiumCreateResult
    panel = w.panel

    def gate(name, down_value):
        real = getattr(panel, name)

        async def _call(*a, **k):
            if w.panel_down:
                panel.calls.append((f"{name}:down",))
                return down_value() if callable(down_value) else down_value
            return await real(*a, **k)
        return _call

    for name, down in (
        ("find_user_by_username", None), ("get_bypass_entity_safe", None), ("get_user", None),
        ("create_user", None), ("update_user", None), ("assign_user_to_squad", False),
    ):
        monkeypatch.setattr(remnawave_api, name, gate(name, down))
    monkeypatch.setattr(remnawave_premium, "create_premium_user_entity", gate(
        "create_premium_user_entity",
        lambda: PremiumCreateResult(False, None, False, None, 0, "timeout")))
    monkeypatch.setattr(remnawave_premium, "renew_premium_user", gate("renew_premium_user", False))


def set_panel_down(w, down: bool) -> None:
    if w.on:
        w.panel2.mode = "down" if down else "ok"
    else:
        w.panel_down = down


# ═══════════════════════════════════════════════════════════════════════
# States
# ═══════════════════════════════════════════════════════════════════════

STATES = ("new", "active", "expired", "bypass_only", "trial")
STATE_SHAPE = {
    # state: (premium expires offset, bypass bytes, has premium entity, is_bypass_only)
    "active": (timedelta(days=10), 3 * GB, True, False),
    "expired": (timedelta(days=-5), 0, True, False),
    "bypass_only": (timedelta(days=3650), 5 * GB, False, True),
    "trial": (timedelta(days=2), 500 * MB, True, False),
}


def seed(w: MatrixWorld, state: str, *, sub_type: str = "basic", is_combo: bool = False) -> None:
    if state == "new":
        w.sub = None
        return
    offset, bypass, has_premium, bypass_only = STATE_SHAPE[state]
    now = utcnow().replace(microsecond=0)
    expires = now + offset
    w.sub = {
        "telegram_id": TG,
        "status": "expired" if state == "expired" else "active",
        "uuid": None if bypass_only else "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "vpn_key": None if bypass_only else f"https://panel.test/sub/prem-{TG}",
        "vpn_key_plus": f"https://panel.test/sub/bypass-{TG}",
        "expires_at": h.naive(expires), "subscription_type": sub_type,
        "activation_status": "active", "auto_renew": True, "is_bypass_only": bypass_only,
        "is_combo": is_combo,
        "source": {"trial": "trial", "bypass_only": "bypass_only"}.get(state, "payment"),
        "activated_at": h.naive(now - timedelta(days=20)), "last_auto_renewal_at": None,
    }
    if state == "trial":
        w.trial_used_at = now - timedelta(days=1)
    # legacy panel
    ent = w.panel.new_bypass_entity(TG, bypass)
    w.panel._seeded_bytes[TG] = bypass
    w.bypass_uuid[TG], w.bypass_id[TG] = ent["uuid"], ent["id"]
    # outbox panel
    w.panel2.seed_bypass(TG, bypass)
    if has_premium:
        w.panel.premium_expire[TG] = expires
        w.premium_uuid[TG] = f"prem-{TG}"
        w.panel2.seed_premium(TG, expires)


# ═══════════════════════════════════════════════════════════════════════
# Expectations + the ONE assertion helper
# ═══════════════════════════════════════════════════════════════════════

MONTHS_OF_PERIOD = {30: 1, 90: 3, 180: 6, 365: 12, 730: 24}


@dataclass(frozen=True)
class Months:
    """Owner decision 2026-09-14: a paid tariff period is CALENDAR months.
    `base + Months(n)` moves `base` n calendar months (same time of day), clamped
    to the last day of a shorter month. An oracle of its own — not app.services.tariffs."""
    n: int

    def __radd__(self, base):
        idx = base.month - 1 + self.n
        year, month = base.year + idx // 12, idx % 12 + 1
        first_of_next = base.replace(year=year + month // 12, month=month % 12 + 1, day=1)
        last_day = (first_of_next - timedelta(days=1)).day
        return base.replace(year=year, month=month, day=min(base.day, last_day))

    def __str__(self):
        return f"{self.n} calendar month(s)"


def paid_period(period: int) -> Months:
    """Premium of a paid catalog period (30/90/180/365/730 d key) — calendar months."""
    return Months(MONTHS_OF_PERIOD[period])


@dataclass(frozen=True)
class Expect:
    premium: Optional[timedelta | Months]  # None → premium untouched (DB and panel)
    bypass_add: Optional[int]             # bytes added to the bypass limit (None → not checked)
    payments: int                         # committed payments rows created
    sub_type: Optional[str] = None        # subscriptions.subscription_type after
    is_combo: Optional[bool] = None       # subscriptions.is_combo after (None → not checked)


@dataclass
class Before:
    t0: datetime
    db_expires: Optional[datetime]
    active: bool                          # a really active premium subscription
    premium: Optional[datetime]
    bypass: int
    payments: int


def snap(w: MatrixWorld) -> Before:
    sub = w.sub or {}
    exp = aware(sub.get("expires_at"))
    t0 = utcnow()
    active = bool(sub and sub.get("status") == "active" and sub.get("uuid") and exp and exp > t0
                  and not sub.get("is_bypass_only"))
    return Before(t0=t0, db_expires=exp, active=active, premium=w.premium_until(),
                  bypass=w.bypass_bytes(), payments=len(w.payments))


def check(w: MatrixWorld, before: Before, exp: Expect) -> None:
    """Every owner rule for one cell; lists ALL violations at once."""
    t1 = utcnow()
    bad = []
    sub = w.sub or {}
    db_end = aware(sub.get("expires_at"))
    panel_end = w.premium_until()
    if exp.premium is not None:
        lo = (max(before.db_expires, before.t0) if before.active else before.t0) + exp.premium
        hi = (max(before.db_expires, t1) if before.active else t1) + exp.premium
        if db_end is None or not (lo - timedelta(microseconds=1) <= db_end <= hi):
            bad.append(f"DB expires_at={db_end} not in [{lo}, {hi}] (max(now, current)+{exp.premium})")
        if panel_end is None:
            bad.append("panel premium entity missing")
        elif db_end is not None and abs((panel_end - db_end).total_seconds()) >= 1:
            bad.append(f"panel expireAt={panel_end} != DB expires_at={db_end} (to the second)")
        if sub.get("is_bypass_only"):
            bad.append("is_bypass_only still TRUE after a premium grant")
    else:
        if db_end != before.db_expires:
            bad.append(f"DB expires_at changed {before.db_expires} → {db_end} (premium must stay)")
        if panel_end != before.premium:
            bad.append(f"panel expireAt changed {before.premium} → {panel_end} (premium must stay)")
    added = w.bypass_bytes() - before.bypass
    if exp.bypass_add is not None and added != exp.bypass_add:
        bad.append(f"bypass +{added / GB:g} GB, expected +{exp.bypass_add / GB:g} GB")
    committed = [p for p in w.payments[before.payments:]]
    if len(committed) != exp.payments:
        bad.append(f"{len(committed)} committed payments rows, expected {exp.payments}")
    if exp.sub_type is not None and sub.get("subscription_type") != exp.sub_type:
        bad.append(f"subscription_type={sub.get('subscription_type')!r}, expected {exp.sub_type!r}")
    if exp.is_combo is not None and bool(sub.get("is_combo")) != exp.is_combo:
        bad.append(f"is_combo={sub.get('is_combo')!r}, expected {exp.is_combo}")
    if w.on and w.legacy_calls:
        bad.append(f"legacy panel writers used under the flag: {w.legacy_calls}")
    assert not bad, "owner rules violated:\n  " + "\n  ".join(bad)


# ═══════════════════════════════════════════════════════════════════════
# Tariff catalog (from config, not from app.services.tariffs)
# ═══════════════════════════════════════════════════════════════════════

BASE_PERIODS = (30, 90, 180, 365)
COMBO_PERIODS = tuple(sorted(config.COMBO_TARIFFS["combo_basic"]))
PURCHASE_TARIFFS = (
    [(t, p) for t in ("basic", "plus") for p in BASE_PERIODS]
    + [(t, p) for t in ("combo_basic", "combo_plus") for p in COMBO_PERIODS]
)
PACK_SIZES = (15, 50, 200, 300, 8000)


def tier_of(tariff: str) -> str:
    return tariff.removeprefix("combo_")


def purchase_gb(tariff: str, period: int) -> int:
    if tariff.startswith("combo_"):
        return int(config.COMBO_TARIFFS[tariff][period]["gb"]) * GB
    return int(config.TRAFFIC_LIMITS[tariff][period])


def purchase_price(tariff: str, period: int) -> int:
    if tariff.startswith("combo_"):
        return int(config.COMBO_TARIFFS[tariff][period]["price"])
    return int(config.TARIFFS[tariff][period]["price"])


def purchase_expect(tariff: str, period: int) -> Expect:
    return Expect(premium=paid_period(period), bypass_add=purchase_gb(tariff, period), payments=1,
                  sub_type=tier_of(tariff), is_combo=tariff.startswith("combo_"))


def pending_for(tariff: str, period: int, *, purchase_id: str = "pid-1", provider=None) -> dict:
    p = h.make_pending(purchase_id=purchase_id, tariff=tier_of(tariff), period_days=period,
                       is_combo=tariff.startswith("combo_"), price_rub=purchase_price(tariff, period))
    p["payment_provider"] = provider
    return p


def pack_pending(gb: int, *, purchase_id: str = "pack-1") -> dict:
    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED[gb]
    return h.make_pending(purchase_id=purchase_id, tariff=f"traffic_{gb}gb", period_days=0,
                          purchase_type="traffic_pack", price_rub=pack["price"])


# ═══════════════════════════════════════════════════════════════════════
# Runners (one per entry point)
# ═══════════════════════════════════════════════════════════════════════

WEBHOOK_PROVIDERS = ("platega", "wata", "cryptobot", "wata_reconciler")


async def run_webhook(w, monkeypatch, pending: dict, *, provider="platega", amount=None) -> dict:
    """External provider (or the WATA reconciler) → confirmation.process_confirmed_payment."""
    from app.handlers.callbacks import payments_callbacks
    w.pending = pending

    async def by_id(pid, check_expiry=False):
        return dict(w.pending) if w.pending and w.pending["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase_by_id", by_id)
    monkeypatch.setattr(payments_callbacks, "delete_invoice_message_for_purchase", AsyncMock())
    bot = MagicMock()
    bot.send_message = AsyncMock()
    amount = pending["price_kopecks"] / 100 if amount is None else amount
    out = {"bot": bot, "transient": None, "result": None}
    try:
        if provider == "wata_reconciler":
            from app.workers import wata_reconciler
            monkeypatch.setattr(wata_reconciler, "resolve_wata_payment", AsyncMock(return_value={
                "outcome": wata_reconciler.LOOKUP_PAID, "amount": amount, "tx_id": "tx-1"}))
            out["result"] = await wata_reconciler._check_and_finalize(bot, {
                "purchase_id": pending["purchase_id"], "invoice_id": "inv-1",
                "telegram_id": pending["telegram_id"], "payment_provider": "wata"})
        else:
            out["result"] = await confirmation.process_confirmed_payment(
                provider=provider, purchase_id=pending["purchase_id"], amount_rubles=amount,
                invoice_id="inv-1", telegram_id=pending["telegram_id"], bot=bot)
    except TransientPaymentError as e:
        out["transient"] = e
    await w.drain_background()
    return out


async def run_telegram(w, monkeypatch, pending: dict, *, stars=False, fsm_combo_gb=0, charge_kopecks=None):
    """Telegram-native successful_payment (card RUB or Stars XTR). `charge_kopecks`
    overrides the charged amount (default: the pending price)."""
    charged = pending["price_kopecks"] if charge_kopecks is None else charge_kopecks
    from app.handlers.payments import payments_messages as pm
    w.pending = pending

    async def get_pending(pid, tg, check_expiry=True):
        return dict(w.pending) if w.pending and w.pending["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase", get_pending)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pm, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pm, "clear_promo_session", AsyncMock())
    state = h._fsm(pending["telegram_id"])
    if fsm_combo_gb:
        await state.update_data(combo_bypass_gb=fsm_combo_gb)
    message = MagicMock()
    message.from_user.id = pending["telegram_id"]
    message.message_id = 1
    message.answer = AsyncMock()
    message.bot = BOT
    message.successful_payment = SimpleNamespace(
        currency="XTR" if stars else "RUB",
        total_amount=charged // 100 if stars else charged,
        invoice_payload=f"purchase:{pending['purchase_id']}",
        telegram_payment_charge_id="tg-charge-1",
    )
    await pm.process_successful_payment(message, state)
    await w.drain_background()
    return message


async def run_balance(w, monkeypatch, tariff: str, period: int):
    combo_gb = purchase_gb(tariff, period) // GB if tariff.startswith("combo_") else 0
    return await h.run_balance_purchase(w, monkeypatch, tariff=tier_of(tariff), period_days=period,
                                        combo_gb=combo_gb)


async def run_autorenew(w, monkeypatch, *, last_payment: str, expire_mid_run: bool = False,
                        newer: tuple = ()):
    """`last_payment`: the tariff of the user's last subscription payment (payments
    history, served by MatrixConn — the payment lookup is NOT stubbed); `newer`:
    approved non-subscription payments made after it (top-up, gift, GB pack …).
    `expire_mid_run`: the subscription is selected as due, then expires
    before its grant_access (R-EXPIRY-RACE / P1-9)."""
    import auto_renewal
    w.fetch_batches = [[{**w.sub, "telegram_id": TG, "language": "ru", "balance": w.balance_kopecks}]]
    w.payment_history = [{"telegram_id": TG, "tariff": t, "status": "approved", "amount": 0}
                         for t in (last_payment, *newer)]

    async def _decrease(*, telegram_id, amount, source, description, conn):
        kop = round(amount * 100)
        if w.balance_kopecks < kop:
            return False
        if isinstance(conn, MatrixConn):
            conn._touch("balance")          # a real debit is a write on the batch connection
        w.balance_kopecks -= kop
        return True
    w.debit = AsyncMock(side_effect=_decrease)

    async def _discount(*_a, **_k):     # runs after the selection, before the grant
        if expire_mid_run:
            w.sub["expires_at"] = h.naive(utcnow() - timedelta(seconds=1))
        return None
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(side_effect=_discount), raising=False)
    monkeypatch.setattr(database, "decrease_balance", w.debit, raising=False)
    monkeypatch.setattr(database, "increase_balance", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(auto_renewal, "acquire_connection", lambda _pool, _name: w.pool.acquire())
    monkeypatch.setattr(auto_renewal, "safe_send_message", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(auto_renewal, "resolve_user_language", AsyncMock(return_value="ru"))
    await auto_renewal.process_auto_renewals(BOT)
    await w.drain_background()


async def run_admin_grant(w, monkeypatch, *, days=7, tariff="basic"):
    """Admin «grant N days» → dashboard POST /users/{tg}/grant → admin_grant_access_atomic
    (the bot admin panel was removed; the dashboard is the only admin grant entry)."""
    from app.api.dashboard.routes import users as routes_mod
    monkeypatch.setattr(routes_mod.bus, "publish", MagicMock())
    resp = await routes_mod.user_grant(
        telegram_id=TG, body=routes_mod.GrantRequest(days=days, tariff=tariff),
        admin={"sub": str(config.ADMIN_TELEGRAM_ID)},
    )
    await w.drain_background()
    return resp


async def run_gift(w, monkeypatch, *, tariff="basic", period=30, code="GIFT1234"):
    """/start gift_<code> → start.cmd_start → database.activate_gift_subscription
    (+ the handler's legacy post-activation renew_remnawave_user_bg)."""
    real_activate = database.activate_gift_subscription
    results = []

    async def spy(*a, **k):
        res = await real_activate(*a, **k)
        results.append(res)
        return res
    monkeypatch.setattr(database, "activate_gift_subscription", spy)
    await h.run_gift_activation(w, monkeypatch, tariff=tariff, period_days=period, code=code)
    assert results, "gift activation was not reached"
    return results[-1]


async def run_trial(w, monkeypatch):
    from app.services.trials import service as trial_service

    async def eligible(_tg):
        return w.trial_used_at is None and not (
            w.sub and w.sub.get("status") == "active" and not w.sub.get("is_bypass_only"))

    async def mark_used(_tg, _exp):
        w.trial_used_at = utcnow()
        return True
    monkeypatch.setattr(database, "is_eligible_for_trial", eligible, raising=False)
    monkeypatch.setattr(database, "mark_trial_used", mark_used, raising=False)
    ok = await trial_service.activate_trial(TG, bot=BOT)
    await w.drain_background()
    return ok


ENTRY_FLAG = {
    "platega": "webhook", "wata": "webhook", "cryptobot": "webhook", "wata_reconciler": "webhook",
    "telegram": "telegram", "stars": "telegram", "balance": "balance", "autorenew": "autorenew",
    "admin": "admin", "gift": "gift", "grants": "grants", "trial": "trial",
}


async def run_purchase(w, monkeypatch, entry: str, tariff: str, period: int):
    if entry in WEBHOOK_PROVIDERS:
        return await run_webhook(w, monkeypatch, pending_for(tariff, period), provider=entry)
    if entry in ("telegram", "stars"):
        combo = purchase_gb(tariff, period) // GB if tariff.startswith("combo_") else 0
        return await run_telegram(w, monkeypatch, pending_for(tariff, period),
                                  stars=entry == "stars", fsm_combo_gb=combo)
    if entry == "balance":
        return await run_balance(w, monkeypatch, tariff, period)
    raise AssertionError(entry)


# ═══════════════════════════════════════════════════════════════════════
# KNOWN legacy bugs (flag off) — xfail(strict) with the bug id
# ═══════════════════════════════════════════════════════════════════════

def legacy_known_bug(entry: str, tariff: str, state: str) -> Optional[str]:
    """Bug id of a flag-off cell that violates the owner rules today (None = must pass).
    Ids: docs/audit/02_payment_core_plan.md «Находки T0» (T0-*) and new matrix
    findings (M-*), both listed in docs/audit/03_payment_matrix.md. All of them are
    GB / tariff-label bugs of the legacy path that the provisioning outbox fixes."""
    combo = tariff.startswith("combo_")
    if entry in ("telegram", "stars") and state == "new":
        return ("T0-TG-COMBO-NEW: Telegram combo new = combo GB twice (150 instead of 75)" if combo
                else "T0-TG-NEW-20: Telegram basic/plus new = 20 GB instead of 10")
    if entry == "balance":
        # T0-BAL-COMBO-RENEW is fixed (no base-10 GB background top-up for Combo).
        if combo:
            return "T0-BAL-COMBO-NEW: balance combo new = combo GB + 20" if state == "new" else None
        if state == "new":
            return "T0-BAL-NEW-20: balance basic/plus new = 20 GB instead of 10"
    if entry == "admin" and state == "new":
        return "T0-ADMIN-GB: admin day grant to a new user creates a 10 GB bypass (owner rule: 0 GB)"
    if entry == "gift" and state == "new":
        return "T0-GIFT-20: gift to a new user = 20 GB instead of 10"
    if entry == "autorenew" and combo:
        return "T0-AUTORENEW-COMBO: combo renews at basic price with 10 GB (rule: combo price + combo GB)"
    if entry == "autorenew" and tariff == "plus":
        return "T0-AUTORENEW-PLUS: plus subscription renews as basic (grant_access without tariff)"
    if entry == "trial" and state in ("expired", "bypass_only"):
        return "M-TRIAL-NO-MB: trial for a user with an existing bypass entity adds 0 MB"
    return None


def cell_params(entries, tariffs, states, *, rule=legacy_known_bug):
    out = []
    for entry, (tariff, period), state, flag in itertools.product(entries, tariffs, states, ("off", "on")):
        bug = rule(entry, tariff, state) if flag == "off" else None
        marks = [legacy_bug(reason=bug)] if bug else []
        out.append(pytest.param(entry, tariff, period, state, flag, marks=marks,
                                id=f"{entry}-{tariff}_{period}-{state}-{flag}"))
    return out


# ═══════════════════════════════════════════════════════════════════════
# 1. Purchases: webhook / telegram / stars / balance × tariff × state × flag
# ═══════════════════════════════════════════════════════════════════════

REDUCED = [("basic", 30), ("plus", 365), ("combo_basic", 30), ("combo_plus", 90)]

PURCHASE_CELLS = (
    cell_params(("platega", "telegram", "balance"), PURCHASE_TARIFFS, STATES)
    + cell_params(("wata", "cryptobot", "wata_reconciler", "stars"), REDUCED, STATES)
)


@pytest.mark.parametrize("entry,tariff,period,state,flag", PURCHASE_CELLS)
async def test_purchase_cell(monkeypatch, entry, tariff, period, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, state)
    before = snap(w)

    out = await run_purchase(w, monkeypatch, entry, tariff, period)

    if isinstance(out, dict):
        assert out["transient"] is None, f"webhook answered 5xx: {out['transient']}"
    check(w, before, purchase_expect(tariff, period))


# ═══════════════════════════════════════════════════════════════════════
# 2. Traffic packs: +N GB, premium untouched
# ═══════════════════════════════════════════════════════════════════════

def pack_params():
    out = []
    for entry, gb, state, flag in itertools.product(("platega", "telegram", "stars"), PACK_SIZES,
                                                     STATES, ("off", "on")):
        bug = legacy_known_bug(f"pack:{entry}", f"traffic_{gb}gb", state) if flag == "off" else None
        out.append(pytest.param(entry, gb, state, flag,
                                marks=[legacy_bug(reason=bug)] if bug else [],
                                id=f"pack-{entry}-{gb}gb-{state}-{flag}"))
    return out


@pytest.mark.parametrize("entry,gb,state,flag", pack_params())
async def test_traffic_pack_cell(monkeypatch, entry, gb, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, state)
    before = snap(w)
    pending = pack_pending(gb)

    if entry == "platega":
        out = await run_webhook(w, monkeypatch, pending)
        assert out["transient"] is None
    else:
        await run_telegram(w, monkeypatch, pending, stars=entry == "stars")

    check(w, before, Expect(premium=None, bypass_add=gb * GB, payments=1))


# ═══════════════════════════════════════════════════════════════════════
# 2b. «🌐 Только обход блокировок» (tariff bypass_{N}gb) × user state
#     Owner rule 2026-09-14 (SCOPE): no active premium and the trial never used
#     → EXACTLY the GB bought + a one-time 3-day premium gift (no trial 500 MB),
#     whatever the "trial" flag; otherwise only the GB, premium untouched.
# ═══════════════════════════════════════════════════════════════════════

BYPASS_ENTRIES = ("platega", "wata", "wata_reconciler", "telegram")
BYPASS_SIZES = (15, 300)
BYPASS_STATES = STATES + ("trial_used",)          # trial_used: no row, trial taken long ago
GIFT_STATES = ("new", "expired", "bypass_only")


def bypass_pending(gb: int, *, purchase_id: str = "bypass-1") -> dict:
    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED[gb]
    return h.make_pending(purchase_id=purchase_id, tariff=f"bypass_{gb}gb", period_days=0,
                          purchase_type="traffic_pack", price_rub=pack["price"])


def seed_bypass_state(w: MatrixWorld, state: str) -> None:
    if state == "trial_used":
        seed(w, "new")
        w.trial_used_at = utcnow() - timedelta(days=60)
    else:
        seed(w, state)


def install_bypass_only_row(w: MatrixWorld, monkeypatch) -> None:
    """database.ensure_bypass_only_subscription on the one-row world (its SQL runs
    on real Postgres in tests/e2e/test_17_bypass_only_gift.py): an active premium
    row is kept, anything else becomes the bypass-only placeholder row."""
    async def ensure(tg, conn=None):
        if isinstance(conn, MatrixConn):
            conn._touch("sub")
        now = utcnow()
        sub = w.sub
        far = h.naive(now + timedelta(days=3650))
        if sub:
            exp = aware(sub.get("expires_at"))
            if exp and exp > now and not sub.get("is_bypass_only"):
                return True
            ended = sub.get("status") == "expired" or (exp is not None and exp < now)
            sub.update(is_bypass_only=True, status="active", expires_at=far,
                       uuid=None, vpn_key=None, vpn_key_plus=None)
            if ended:
                sub["source"] = "bypass_only"
        else:
            w.sub = {"telegram_id": tg, "status": "active", "subscription_type": "basic",
                     "is_bypass_only": True, "expires_at": far, "source": "bypass_only",
                     "uuid": None, "activation_status": "active", "is_combo": False}
        return True
    monkeypatch.setattr(db_subs, "ensure_bypass_only_subscription", ensure)
    monkeypatch.setattr(database, "ensure_bypass_only_subscription", ensure)


async def run_bypass_purchase(w, monkeypatch, entry: str, gb: int, *, purchase_id="bypass-1", pending=None):
    """`pending` given → deliver that (already paid) purchase again: a replay."""
    pending = pending if pending is not None else bypass_pending(gb, purchase_id=purchase_id)
    if entry == "telegram":
        return await run_telegram(w, monkeypatch, pending)
    out = await run_webhook(w, monkeypatch, pending, provider=entry)
    assert out["transient"] is None, f"webhook answered 5xx: {out['transient']}"
    return out


def check_bypass_only_cell(w: MatrixWorld, before: Before, gb: int, *, gift: bool, trial_before) -> None:
    if gift:
        check(w, before, Expect(premium=timedelta(days=3), bypass_add=gb * GB, payments=1, sub_type="basic"))
        assert (w.sub["source"], w.trial_used_at is not None) == ("trial", True), w.sub
        return
    bad = []
    if w.premium_until() != before.premium:
        bad.append(f"panel premium changed {before.premium} → {w.premium_until()} (no gift due)")
    added = w.bypass_bytes() - before.bypass
    if added != gb * GB:
        bad.append(f"bypass +{added / GB:g} GB, expected +{gb} GB")
    if len(w.payments) - before.payments != 1:
        bad.append(f"{len(w.payments) - before.payments} payments rows, expected 1")
    sub = w.sub or {}
    if before.active:
        if aware(sub.get("expires_at")) != before.db_expires or sub.get("is_bypass_only"):
            bad.append(f"active premium row changed: {sub}")
    elif not sub.get("is_bypass_only"):
        bad.append(f"no premium due → the row must be bypass-only: {sub}")
    if w.trial_used_at != trial_before:
        bad.append("trial_used_at changed without a gift")
    if w.on and w.legacy_calls:
        bad.append(f"legacy panel writers used under the flag: {w.legacy_calls}")
    assert not bad, "owner rules violated:\n  " + "\n  ".join(bad)


def bypass_only_params():
    return [pytest.param(entry, gb, state, flag, id=f"bypass-{entry}-{gb}gb-{state}-{flag}")
            for entry, gb, state, flag in itertools.product(BYPASS_ENTRIES, BYPASS_SIZES, BYPASS_STATES,
                                                            ("off", "on"))]


@pytest.mark.parametrize("entry,gb,state,flag", bypass_only_params())
async def test_bypass_only_purchase_cell(monkeypatch, entry, gb, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])   # "trial" flag stays OFF
    install_bypass_only_row(w, monkeypatch)
    seed_bypass_state(w, state)
    before, trial_before = snap(w), w.trial_used_at

    await run_bypass_purchase(w, monkeypatch, entry, gb)

    check_bypass_only_cell(w, before, gb, gift=state in GIFT_STATES, trial_before=trial_before)
    forced = [a["text"].splitlines()[0] for a in w.forced_alerts()]
    assert not forced, f"unexpected forced alerts: {forced}"


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", ["platega", "telegram"])
async def test_bypass_only_replay_grants_nothing_twice(monkeypatch, entry, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    install_bypass_only_row(w, monkeypatch)
    await run_bypass_purchase(w, monkeypatch, entry, 15)
    state = (copy.deepcopy(w.sub), w.trial_used_at, w.bypass_bytes(), w.premium_until(),
             len(w.payments), len(w.jobs_list()))

    await run_bypass_purchase(w, monkeypatch, entry, 15, pending=dict(w.pending))   # the same paid purchase again

    assert (w.sub, w.trial_used_at, w.bypass_bytes(), w.premium_until(), len(w.payments),
            len(w.jobs_list())) == state
    assert w.bypass_bytes() == 15 * GB


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("gift", ["active", "ended"])
@pytest.mark.parametrize("tariff,period", [("basic", 30), ("plus", 90), ("combo_basic", 30)])
async def test_paid_purchase_after_the_bypass_only_gift(monkeypatch, tariff, period, gift, flag):
    """Owner: «после он может продлить всё что угодно, главное чтобы не было
    проблем с назначением». Gift active → from the gift end; gift ended (the
    expiry workers made the row bypass-only) → from now; calendar months; the
    tariff's GB on top of the remaining; a normal paid row."""
    w = build(monkeypatch, flag=flag, entrypoint="webhook")
    install_bypass_only_row(w, monkeypatch)
    await run_bypass_purchase(w, monkeypatch, "platega", 15)
    assert w.sub["source"] == "trial"
    if gift == "ended":                     # what fast_expiry_cleanup / trial expiry leave behind
        past = utcnow() - timedelta(minutes=5)
        w.sub.update(uuid=None, vpn_key=None, is_bypass_only=True, source="bypass_only",
                     expires_at=h.naive(utcnow() + timedelta(days=3650)))
        if w.on:
            w.panel2.premium[TG]["expireAt"] = past
        else:
            w.panel.premium_expire[TG] = past
    before = snap(w)
    assert before.active is (gift == "active")

    out = await run_webhook(w, monkeypatch, pending_for(tariff, period))

    assert out["transient"] is None
    check(w, before, purchase_expect(tariff, period))
    assert w.sub["source"] == "payment" and not w.sub.get("is_bypass_only")
    assert w.bypass_bytes() == 15 * GB + purchase_gb(tariff, period)


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("tariff,period", [("basic", 30), ("plus", 90), ("combo_basic", 30)])
async def test_paid_purchase_on_a_bypass_only_row_with_a_stale_key(monkeypatch, tariff, period, flag):
    """A bypass-only row that still carries the ended premium's key (what the old
    ensure_bypass_only_subscription left behind): grant_access must count from
    now, never from the 10-year placeholder (was: Basic → «Invalid renewal»,
    Plus → placeholder + months), and the row becomes a normal paid row."""
    w = build(monkeypatch, flag=flag, entrypoint="webhook")
    seed(w, "bypass_only")
    w.sub.update(uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", vpn_key=f"https://panel.test/sub/prem-{TG}")
    past = utcnow() - timedelta(days=1)
    w.panel.premium_expire[TG] = past
    w.premium_uuid[TG] = f"prem-{TG}"
    w.panel2.seed_premium(TG, past)
    before = snap(w)
    assert before.active is False

    out = await run_webhook(w, monkeypatch, pending_for(tariff, period))

    assert out["transient"] is None, out["transient"]
    check(w, before, purchase_expect(tariff, period))
    assert not w.forced_alerts(), [a["text"][:120] for a in w.forced_alerts()]


# ═══════════════════════════════════════════════════════════════════════
# 3. Auto-renewal (active subscriptions only — the worker selects nothing else)
# ═══════════════════════════════════════════════════════════════════════

AUTORENEW_TARIFFS = (
    [(t, p) for t in ("basic", "plus") for p in BASE_PERIODS]
    + [(t, p) for t in ("combo_basic", "combo_plus") for p in BASE_PERIODS]
)


def autorenew_expect(tariff: str, period: int) -> Expect:
    return purchase_expect(tariff, period)


@pytest.mark.parametrize("entry,tariff,period,state,flag",
                         cell_params(("autorenew",), AUTORENEW_TARIFFS, ("active", "trial")))
async def test_autorenew_cell(monkeypatch, entry, tariff, period, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint="autorenew")
    is_combo = tariff.startswith("combo_")
    seed(w, state, sub_type=tier_of(tariff), is_combo=is_combo)
    before = snap(w)

    await run_autorenew(w, monkeypatch, last_payment=f"{tier_of(tariff)}_{period}")

    check(w, before, autorenew_expect(tariff, period))


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_legacy_biz_row_renews_as_plus(monkeypatch, flag):
    """Business tariffs were removed (owner 2026-09-14). A legacy row with
    subscription_type=biz_* and a last payment "biz_team_30" auto-renews as
    plain Plus: premium +30 d, +10 GB, one payment, charged today's Plus
    price (no more "biz falls back to basic 199 ₽"), and it reads as plus."""
    from app.services.tariffs import normalize_tier

    w = build(monkeypatch, flag=flag, entrypoint="autorenew")
    seed(w, "active", sub_type="biz_team")
    before = snap(w)
    balance_before = w.balance_kopecks

    await run_autorenew(w, monkeypatch, last_payment="biz_team_30")

    check(w, before, Expect(premium=paid_period(30), bypass_add=10 * GB, payments=1))
    assert balance_before - w.balance_kopecks == config.TARIFFS["plus"][30]["price"] * 100
    if flag == "on":
        assert normalize_tier(w.sub["subscription_type"]) == "plus"
    else:
        # Known legacy bug, same as every Plus row on this path: T0-AUTORENEW-PLUS
        # (grant_access without tariff writes 'basic'). Pinned so a fix is noticed.
        assert w.sub["subscription_type"] == "basic"


# ═══════════════════════════════════════════════════════════════════════
# 4. Grants without money: admin days, gift activation, trial
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("entry,tariff,period,state,flag",
                         cell_params(("admin",), [("basic", 7), ("plus", 7)], STATES))
async def test_admin_grant_cell(monkeypatch, entry, tariff, period, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint="admin")
    seed(w, state)
    before = snap(w)

    await run_admin_grant(w, monkeypatch, days=period, tariff=tariff)

    check(w, before, Expect(premium=timedelta(days=period), bypass_add=0, payments=0, sub_type=tariff))


@pytest.mark.parametrize("entry,tariff,period,state,flag",
                         cell_params(("gift",), [(t, p) for t in ("basic", "plus") for p in BASE_PERIODS],
                                     STATES))
async def test_gift_cell(monkeypatch, entry, tariff, period, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint="gift")
    seed(w, state)
    before = snap(w)

    res = await run_gift(w, monkeypatch, tariff=tariff, period=period)

    assert res["success"] is True, res
    assert w.gift["status"] == "activated"
    # a gift is a paid purchase by the gifter: the recipient gets the tariff's entitlement
    check(w, before, Expect(premium=paid_period(period), bypass_add=purchase_gb(tariff, period),
                            payments=0, sub_type=tariff))


@pytest.mark.parametrize("entry,tariff,period,state,flag",
                         cell_params(("trial",), [("trial", 3)], ("new", "expired", "bypass_only")))
async def test_trial_cell(monkeypatch, entry, tariff, period, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint="trial")
    seed(w, state)
    if state == "expired":
        w.sub["source"] = "admin"       # an expired non-paid subscription may still take a trial
    before = snap(w)

    assert await run_trial(w, monkeypatch) is True

    check(w, before, Expect(premium=timedelta(days=3), bypass_add=config.TRIAL_BYPASS_MB * MB,
                            payments=0))


# ═══════════════════════════════════════════════════════════════════════
# 5. OWNER'S EXAMPLE — the panel is down while an ACTIVE user renews
#    ("подписка до 20 окт., купил месяц, а в панели всё ещё 20 окт.")
# ═══════════════════════════════════════════════════════════════════════
#
# Phase 1 (panel down): billing is committed exactly once (or not at all),
# the DB is extended exactly once, and a FORCED admin alert went out.
# Phase 2 (panel back): legacy — the background re-sync (purchase_flow) and a
# provider retry; outbox — the worker tick. Then EVERY owner rule holds:
# panel expireAt == DB expires_at == old + period (never twice), GB exactly once
# (legacy: or a forced alert naming the missing GB — the documented legacy
# compromise, add_bypass_traffic is not idempotent).

RENEWAL_ENTRIES = ("platega", "telegram", "balance", "autorenew", "admin", "gift")


async def run_entry(w, monkeypatch, entry: str):
    """One default action per entry point (basic 30 d / admin 7 d)."""
    if entry in ("platega", "telegram", "balance"):
        return await run_purchase(w, monkeypatch, entry, "basic", 30)
    if entry == "autorenew":
        return await run_autorenew(w, monkeypatch, last_payment="basic_30")
    if entry == "admin":
        return await run_admin_grant(w, monkeypatch, days=7)
    if entry == "gift":
        return await run_gift(w, monkeypatch, tariff="basic", period=30)
    raise AssertionError(entry)


def entry_expect(entry: str, flag: str) -> Expect:
    if entry == "admin":
        # legacy admin grant adds GB (T0-ADMIN-GB, xfail in §4) → GB only checked with the flag
        return Expect(premium=timedelta(days=7), bypass_add=0 if flag == "on" else None, payments=0)
    if entry == "gift":
        return Expect(premium=paid_period(30), bypass_add=10 * GB, payments=0)
    return Expect(premium=paid_period(30), bypass_add=10 * GB, payments=1, sub_type="basic")


async def recover_panel(w) -> None:
    """Panel back: outbox → worker tick; legacy → release the background re-sync."""
    set_panel_down(w, False)
    if w.on:
        for job in w.jobs_list():
            if job["status"] == "pending":
                w.jobs.make_due(job["id"])
        await provisioning_worker.run_tick(BOT)
    else:
        w.resync_gate.set()
        tasks = list(purchase_flow._resync_tasks.values())
        if tasks:
            await asyncio.gather(*tasks)
    await w.drain_background()


def gb_or_alert(w, before: Before, expected: Optional[int]) -> Optional[int]:
    """Legacy GB delivery is not retried: GB either arrived, or a forced alert
    naming the missing GB went out (then the cell expects +0)."""
    if expected is None or w.on:
        return expected
    if w.bypass_bytes() - before.bypass == expected:
        return expected
    gb_alerts = [a for a in w.forced_alerts() if "bypass" in a["text"].lower() or " gb" in a["text"].lower()]
    assert gb_alerts, "legacy GB not delivered and no forced alert about it"
    return 0


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", RENEWAL_ENTRIES)
async def test_owner_example_renewal_while_panel_down(monkeypatch, entry, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG.get(entry, entry))
    seed(w, "active")
    before = snap(w)
    exp = entry_expect(entry, flag)
    set_panel_down(w, True)

    out = await run_entry(w, monkeypatch, entry)

    # phase 1 — money / DB / alert, panel still behind
    assert aware(w.sub["expires_at"]) == before.db_expires + exp.premium, "DB extended != exactly once"
    assert len(w.payments) - before.payments == exp.payments, "billing not committed exactly once"
    assert w.premium_until() == before.premium, "panel moved while down?"
    assert w.forced_alerts(), f"no forced admin alert: {w.alerts}"
    assert w.payment_errors, "no payment_errors row"
    if entry == "platega":
        assert (out["transient"] is not None) != w.on, "legacy → 5xx (retry), outbox → 200"

    # phase 2 — panel back (+ the provider retries the webhook)
    await recover_panel(w)
    if entry == "platega":
        again = await run_webhook(w, monkeypatch, dict(w.pending))
        assert again["transient"] is None and again["result"] == {"status": "already_processed"}

    check(w, before, Expect(premium=exp.premium, bypass_add=gb_or_alert(w, before, exp.bypass_add),
                            payments=exp.payments, sub_type=exp.sub_type))


# ═══════════════════════════════════════════════════════════════════════
# 5b. OWNER'S EXAMPLE — calendar months (owner decision 2026-09-14)
#     «подписка до 20 окт., купил месяц → до 20 ноя.»; 31 янв. + 1 мес. → 28/29 фев.
# ═══════════════════════════════════════════════════════════════════════

def _future(month: int, day: int):
    """The next <month>/<day> 09:30:00 UTC at least 3 days ahead."""
    now = utcnow()
    at = now.replace(month=month, day=day, hour=9, minute=30, second=0, microsecond=0)
    while at < now + timedelta(days=3):
        at = at.replace(year=at.year + 1)
    return at


CALENDAR_CASES = [
    pytest.param((10, 20), 30, lambda s: s.replace(month=11, day=20), id="oct20_plus_1m_is_nov20"),
    pytest.param((1, 31), 30, lambda s: s.replace(month=3, day=1) - timedelta(days=1),
                 id="jan31_plus_1m_is_end_of_feb"),
    pytest.param((8, 31), 180, lambda s: s.replace(year=s.year + 1, month=3, day=1) - timedelta(days=1),
                 id="aug31_plus_6m_is_end_of_feb"),
]


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", ["platega", "telegram", "balance", "autorenew", "gift"])
@pytest.mark.parametrize("start_md,period,expected", CALENDAR_CASES)
async def test_owner_example_paid_month_is_a_calendar_month(monkeypatch, entry, flag, start_md,
                                                            period, expected):
    """Active until Oct 20 → buy 1 month → DB AND panel until Nov 20 (not Nov 19),
    the same datetime on both sides; flag off and on; end-of-month clamps."""
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, "active")
    start = _future(*start_md)
    w.sub["expires_at"] = h.naive(start)
    w.panel.premium_expire[TG] = start
    w.panel2.seed_premium(TG, start)

    if entry == "autorenew":
        await run_autorenew(w, monkeypatch, last_payment=f"basic_{period}")
    elif entry == "gift":
        res = await run_gift(w, monkeypatch, tariff="basic", period=period)
        assert res["success"] is True, res
    else:
        out = await run_purchase(w, monkeypatch, entry, "basic", period)
        if isinstance(out, dict):
            assert out["transient"] is None, out["transient"]

    want = expected(start)
    assert aware(w.sub["expires_at"]) == want, f"DB {w.sub['expires_at']} != {want}"
    assert w.premium_until() is not None
    assert abs((w.premium_until() - want).total_seconds()) < 1, f"panel {w.premium_until()} != {want}"


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", ["platega", "telegram", "balance"])
async def test_new_purchase_while_panel_down_keeps_money_and_alerts(monkeypatch, entry, flag):
    """New user, panel down. Outbox: billing committed, activation pending, job +
    forced alert, the worker completes it. Legacy: nothing is granted or billed
    (webhook: pending stays pending → a retry / the WATA reconciler / the admin
    completes it; balance: no debit; Telegram: the charge is kept by Telegram and
    the admin gets a forced alert — Telegram never retries, residual risk R-TG-NEW)."""
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, "new")
    before = snap(w)
    balance_before = w.balance_kopecks
    set_panel_down(w, True)

    await run_purchase(w, monkeypatch, entry, "basic", 30)

    assert w.forced_alerts(), f"no forced admin alert: {w.alerts}"
    if w.on:
        assert len(w.payments) == 1 and w.sub["activation_status"] == "pending"
        assert [j["status"] for j in w.jobs_list()] == ["pending"]
        await recover_panel(w)
        check(w, before, purchase_expect("basic", 30))
        assert w.sub["activation_status"] == "active"
        return
    assert w.payments == [] and (w.sub is None or w.sub.get("uuid") is None)
    assert w.balance_kopecks == balance_before, "legacy balance purchase must not debit"
    if entry == "platega":
        assert w.pending["status"] == "pending", "money kept: the purchase stays retryable"
        await recover_panel(w)
        again = await run_webhook(w, monkeypatch, dict(w.pending))
        assert again["result"] == {"status": "ok"}
        check(w, before, purchase_expect("basic", 30))


# ═══════════════════════════════════════════════════════════════════════
# 6. Idempotency — the same webhook / charge / click again changes nothing
# ═══════════════════════════════════════════════════════════════════════

def frozen(w) -> tuple:
    """Observable state; the panel premium to the second (a legacy replay re-syncs
    the panel to the DB value, which may differ from Phase 1's by microseconds)."""
    premium = w.premium_until()
    return (dict(w.sub or {}).get("expires_at"),
            premium.replace(microsecond=0) if premium else None,
            w.bypass_bytes(), len(w.payments), len(w.jobs.rows))


def replay_params():
    out = []
    for entry, tariff, state, flag in itertools.product(
            ("platega", "wata", "cryptobot", "wata_reconciler", "telegram", "stars"),
            ("basic", "combo_basic"), ("new", "active", "expired"), ("off", "on")):
        out.append(pytest.param(entry, tariff, state, flag, id=f"{entry}-{tariff}-{state}-{flag}"))
    return out


@pytest.mark.parametrize("entry,tariff,state,flag", replay_params())
async def test_replay_purchase_changes_nothing(monkeypatch, entry, tariff, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, state)
    await run_purchase(w, monkeypatch, entry, tariff, 30)
    after_first = frozen(w)
    alerts_first = len(w.forced_alerts())

    if entry in WEBHOOK_PROVIDERS:
        out = await run_webhook(w, monkeypatch, dict(w.pending), provider=entry)
    else:   # Telegram re-delivers the same successful_payment update
        combo = purchase_gb(tariff, 30) // GB if tariff.startswith("combo_") else 0
        out = await run_telegram(w, monkeypatch, dict(w.pending), stars=entry == "stars",
                                 fsm_combo_gb=combo)

    assert frozen(w) == after_first
    assert len(w.forced_alerts()) == alerts_first, "a replay must not alert"
    if entry in WEBHOOK_PROVIDERS and entry != "wata_reconciler":
        assert out["result"] == {"status": "already_processed"} and out["transient"] is None


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("state", ["new", "active"])
async def test_replay_gift_code_grants_once(monkeypatch, state, flag):
    w = build(monkeypatch, flag=flag, entrypoint="gift")
    seed(w, state)
    await run_gift(w, monkeypatch)
    after_first = frozen(w)
    w.gift["status"] = "activated"          # the committed state the second click sees

    res = await database.activate_gift_subscription("GIFT1234", TG)

    assert res == {"success": False, "error": "already_activated"}
    assert frozen(w) == after_first


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_replay_trial_activates_once(monkeypatch, flag):
    w = build(monkeypatch, flag=flag, entrypoint="trial")
    assert await run_trial(w, monkeypatch) is True
    after_first = frozen(w)

    assert await run_trial(w, monkeypatch) is False
    assert frozen(w) == after_first


# ═══════════════════════════════════════════════════════════════════════
# 7. Amount / provider mismatch, balance top-up amount
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("provider", ["platega", "wata", "cryptobot", "wata_reconciler"])
async def test_underpayment_is_rejected_with_forced_alert(monkeypatch, provider, flag):
    w = build(monkeypatch, flag=flag, entrypoint="webhook")
    seed(w, "active")
    before = snap(w)
    pending = pending_for("basic", 30)

    out = await run_webhook(w, monkeypatch, pending, provider=provider, amount=199.0 - 5)

    if provider != "wata_reconciler":
        assert out["result"]["status"] == "amount_mismatch"
    assert out["transient"] is None
    assert w.pending["status"] == "pending"
    assert w.forced_alerts(), "underpayment must alert the admin"
    check(w, before, Expect(premium=None, bypass_add=0, payments=0))


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("paid", [199.0 - 0.4, 199.0, 199.0 * 1.02], ids=["tolerance", "exact", "fee"])
async def test_amount_within_tolerance_or_overpaid_is_accepted(monkeypatch, paid, flag):
    w = build(monkeypatch, flag=flag, entrypoint="webhook")
    seed(w, "active")
    before = snap(w)

    out = await run_webhook(w, monkeypatch, pending_for("basic", 30), amount=paid)

    assert out["result"] == {"status": "ok"}
    check(w, before, purchase_expect("basic", 30))


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_telegram_underpayment_is_rejected_with_forced_alert(monkeypatch, flag):
    w = build(monkeypatch, flag=flag, entrypoint="telegram")
    seed(w, "active")
    before = snap(w)
    pending = pending_for("basic", 30)

    await run_telegram(w, monkeypatch, pending, charge_kopecks=pending["price_kopecks"] - 10_000)

    assert w.pending["status"] == "pending"
    assert w.forced_alerts() and w.payment_errors[-1]["stage"] == "telegram_payment_rejected"
    check(w, before, Expect(premium=None, bypass_add=0, payments=0))


@pytest.mark.parametrize("provider", ["platega", "wata", "cryptobot"])
async def test_provider_mismatch_is_rejected_with_forced_alert(monkeypatch, provider):
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    other = {"platega": "wata", "wata": "cryptobot", "cryptobot": "platega"}[provider]
    row = {**pending_for("basic", 30), "payment_provider": other}
    monkeypatch.setattr(database, "get_pending_purchase_any_status", AsyncMock(return_value=row))

    res = await confirmation.lookup_pending_purchase(provider, row["purchase_id"])

    assert res["status"] == "provider_mismatch"
    assert w.forced_alerts() and w.payment_errors[-1]["stage"] == "webhook_provider_mismatch"


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("provider", ["platega", "wata", "cryptobot"])
@pytest.mark.parametrize("paid,credited", [(203.06, 199.0), (199.0, 199.0), (198.6, 198.6), (150.0, None)],
                         ids=["fee", "exact", "tolerance", "underpaid"])
async def test_balance_topup_credits_the_expected_amount(monkeypatch, provider, paid, credited, flag):
    w = build(monkeypatch, flag=flag, entrypoint="webhook")
    increase = AsyncMock(return_value=True)
    monkeypatch.setattr("database.users.increase_balance", increase)
    pending = h.make_pending(purchase_id="topup-1", tariff=None, period_days=0,
                             purchase_type="balance_topup", price_rub=199)

    out = await run_webhook(w, monkeypatch, pending, provider=provider, amount=paid)

    if credited is None:
        assert out["result"]["status"] == "amount_mismatch" and w.forced_alerts()
        increase.assert_not_awaited()
        assert w.payments == []
    else:
        assert out["result"] == {"status": "ok"}
        assert increase.await_args.kwargs["amount"] == credited
        assert [p["amount"] for p in w.payments] == [round(credited * 100)]
    assert w.jobs.rows == {}


# ═══════════════════════════════════════════════════════════════════════
# 7b. Promo codes (P0-1): a PAID purchase is always honoured at the price paid,
#     even when the promo was exhausted / expired between invoice and payment
# ═══════════════════════════════════════════════════════════════════════

PROMO_CASES = {
    # case: (used_count, max_uses, expires_at offset, is_active)
    "valid": (0, 10, timedelta(days=1), True),
    "exhausted_after_invoice": (5, 5, timedelta(days=1), True),
    "expired_after_invoice": (0, 10, timedelta(hours=-1), True),
}
PROMO_PRICE_KOPECKS = 9950          # basic 30 d with a 50 % promo


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", ["platega", "wata", "telegram"])
@pytest.mark.parametrize("case", list(PROMO_CASES))
async def test_promo_purchase_is_honoured_at_the_paid_price(monkeypatch, case, entry, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, "active")
    monkeypatch.setattr(db_subs, "_consume_promo_in_transaction", REAL_CONSUME_PROMO)
    used, max_uses, offset, active = PROMO_CASES[case]
    w.promo = {"id": 7, "code": "SALE50", "discount_percent": 50, "used_count": used,
               "max_uses": max_uses, "expires_at": utcnow() + offset, "is_active": active}
    before = snap(w)
    pending = {**pending_for("basic", 30), "promo_code": "SALE50", "price_kopecks": PROMO_PRICE_KOPECKS}

    if entry == "telegram":
        await run_telegram(w, monkeypatch, pending)
    else:
        out = await run_webhook(w, monkeypatch, pending, provider=entry)
        assert out["result"] == {"status": "ok"}, out

    check(w, before, purchase_expect("basic", 30))
    assert w.pending["status"] == "paid"
    assert [p["amount"] for p in w.payments] == [PROMO_PRICE_KOPECKS]
    # counted up to the cap: CHECK promocodes_used_not_exceed_max forbids more
    # (docs/audit/07_e2e.md, E2E-PROMO-CAP); the over-cap sale is in the alert
    expected_used = used + 1 if max_uses is None or used < max_uses else used
    assert w.promo["used_count"] == expected_used, "every discounted sale is counted up to the cap"
    promo_alerts = [a for a in w.alerts if "SALE50" in (a["text"] or "")]
    if case == "valid":
        assert promo_alerts == []
    else:
        assert len(promo_alerts) == 1, [a["text"] for a in w.alerts]
        assert "pid-1" in promo_alerts[0]["text"] and str(TG) in promo_alerts[0]["text"]
        assert not any("PERMANENT" in a["text"] for a in w.alerts)


async def test_unexpected_finalize_value_error_is_a_permanent_alert_not_a_duplicate(monkeypatch):
    """P0-1: only PurchaseAlreadyProcessed is the idempotent duplicate; any
    other ValueError from finalize (unknown period, bad date, …) is a paid
    purchase that was NOT credited → PERMANENT forced alert + payment_errors."""
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    monkeypatch.setattr(database, "finalize_purchase", AsyncMock(
        side_effect=ValueError("Invalid subscription purchase: tariff=basic, period_days=31")))

    out = await run_webhook(w, monkeypatch, pending_for("basic", 30))

    assert out["result"]["status"] == "error" and out["transient"] is None
    assert payment_webhook._STATUS_HTTP[out["result"]["status"]] == 200
    assert any("PERMANENT" in a["text"] for a in w.forced_alerts())
    assert w.payment_errors and w.payment_errors[-1]["stage"] == "finalize_rejected"


async def test_duplicate_finalize_is_already_processed_without_alert(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    monkeypatch.setattr(database, "finalize_purchase", AsyncMock(
        side_effect=db_subs.PurchaseAlreadyProcessed("Pending purchase already processed: pid-1")))

    out = await run_webhook(w, monkeypatch, pending_for("basic", 30))

    assert out["result"] == {"status": "already_processed"}
    assert w.forced_alerts() == [] and w.payment_errors == []


# ═══════════════════════════════════════════════════════════════════════
# 8. Alert coverage — every failure branch of the money paths reaches the
#    admin (forced alert, or payment_errors + the provisioning digest)
# ═══════════════════════════════════════════════════════════════════════

async def test_alert_webhook_permanent_error_is_forced(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    monkeypatch.setattr(database, "finalize_purchase", AsyncMock(side_effect=Exception("boom")))

    out = await run_webhook(w, monkeypatch, pending_for("basic", 30))

    assert out["result"] == {"status": "error"}
    assert any("PERMANENT" in a["text"] for a in w.forced_alerts())


async def test_alert_confirmation_purchase_missing_is_forced(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    w.pending = {**pending_for("basic", 30), "telegram_id": OTHER_TG}   # row of another user
    monkeypatch.setattr(database, "get_pending_purchase_by_id", AsyncMock(return_value=dict(w.pending)))

    res = await confirmation.process_confirmed_payment(
        provider="platega", purchase_id="pid-1", amount_rubles=199.0, invoice_id="inv-1",
        telegram_id=TG, bot=BOT)

    assert res["status"] == "error" and w.payments == []
    assert w.forced_alerts() and w.payment_errors[-1]["stage"] == "confirm_purchase_not_found"


async def test_alert_delivery_unexpected_error_after_commit_is_forced(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    seed(w, "active")
    monkeypatch.setattr(confirmation, "_deliver_bypass_gb", AsyncMock(side_effect=KeyError("bug")))

    out = await run_webhook(w, monkeypatch, pending_for("basic", 30))

    assert out["result"] == {"status": "ok"} and len(w.payments) == 1
    assert any("KeyError" in a["text"] for a in w.forced_alerts())


@pytest.mark.parametrize("stars", [False, True], ids=["card", "stars"])
async def test_alert_telegram_finalization_failure_is_forced(monkeypatch, stars):
    w = build(monkeypatch, flag="off", entrypoint="telegram")
    monkeypatch.setattr(database, "finalize_purchase", AsyncMock(side_effect=Exception("db down")))

    message = await run_telegram(w, monkeypatch, pending_for("basic", 30), stars=stars)

    assert message.answer.await_count >= 1
    assert w.forced_alerts() and w.payment_errors[-1]["stage"] == "telegram_finalization_failed"


async def test_alert_telegram_paid_but_purchase_missing_is_forced(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="telegram")
    pending = pending_for("basic", 30)
    await run_telegram(w, monkeypatch, pending)   # installs the patches
    w.alerts.clear()
    w.pending = None

    message = MagicMock()
    message.from_user.id, message.message_id, message.bot = TG, 2, BOT
    message.answer = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency="RUB", total_amount=19900, invoice_payload="purchase:gone-1",
        telegram_payment_charge_id="tg-charge-2")
    from app.handlers.payments import payments_messages as pm
    await pm.process_successful_payment(message, h._fsm(TG))

    assert w.forced_alerts() and w.payment_errors[-1]["stage"] == "telegram_purchase_not_found"


async def test_alert_balance_unexpected_error_is_forced(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="balance")
    monkeypatch.setattr(database, "finalize_balance_purchase", AsyncMock(side_effect=RuntimeError("tx")))

    await run_balance(w, monkeypatch, "basic", 30)

    assert any("balance" in a["text"] for a in w.forced_alerts())


async def test_no_alert_for_user_side_insufficient_balance(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="balance")
    monkeypatch.setattr(database, "finalize_balance_purchase",
                        AsyncMock(side_effect=ValueError("Insufficient balance")))

    await run_balance(w, monkeypatch, "basic", 30)

    assert w.forced_alerts() == []


async def test_balance_double_tap_duplicate_is_answered_without_alert(monkeypatch):
    """P1-2: the second tap of a double tap is refused by finalize_balance_purchase
    (nothing debited) → the user sees «payment is already being processed», the
    admin is not alerted (no money moved)."""
    w = build(monkeypatch, flag="off", entrypoint="balance")
    dup_cls = getattr(database, "DuplicateBalancePurchase", ValueError)
    monkeypatch.setattr(database, "finalize_balance_purchase", AsyncMock(
        side_effect=dup_cls("duplicate balance purchase")))

    callback = await run_balance(w, monkeypatch, "basic", 30)

    assert w.forced_alerts() == []
    answered = [c.args[0] for c in callback.answer.await_args_list if c.args]
    assert w.real_get_text("ru", "errors.session_expired_processing") in answered


async def test_alert_autorenew_per_user_error_is_forced(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="autorenew")
    seed(w, "active")
    monkeypatch.setattr(database, "grant_access", AsyncMock(side_effect=RuntimeError("bug")))

    await run_autorenew(w, monkeypatch, last_payment="basic_30")

    assert any("Auto-renewal processing error" in a["text"] for a in w.forced_alerts())


async def test_autorenew_expiry_mid_run_does_not_debit_without_renewal(monkeypatch):
    """P1-9 (regression 213e2620, flag off): the subscription expires between the
    batch selection and its grant_access → grant_access raises INVARIANT_VIOLATION
    (new issuance inside the caller's transaction). The user's savepoint rolls the
    debit back: no money taken without a renewal, forced alert to the admin."""
    w = build(monkeypatch, flag="off", entrypoint="autorenew")
    seed(w, "active")
    w.balance_kopecks = 100_000
    payments_before = len(w.payments)

    await run_autorenew(w, monkeypatch, last_payment="basic_30", expire_mid_run=True)

    assert w.debit.await_count <= 1
    assert w.balance_kopecks == 100_000, "balance debited without a renewal"
    assert len(w.payments) == payments_before
    assert any("Auto-renewal processing error" in a["text"] for a in w.forced_alerts())


SYNC_FAILED_RENEWAL = {
    "success": True, "payment_id": 501, "is_renewal": True, "subscription_type": "basic",
    "period_days": 30, "remnawave_sync_failed": True, "remnawave_sync_error": "panel down",
}


async def test_webhook_renewal_sync_failure_is_one_alert(monkeypatch):
    """P2-26: a webhook renewal whose premium sync failed — purchase_flow already
    sent the forced alert and scheduled the re-sync. confirmation must not add a
    transient alert, verify_premium_delivery, or (via alerted=True) a webhook-route
    alert for the same incident."""
    from app.services.payments import verify_delivery
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    seed(w, "active")
    monkeypatch.setattr(database, "finalize_purchase", AsyncMock(return_value={
        **SYNC_FAILED_RENEWAL, "expires_at": utcnow() + timedelta(days=40)}))
    monkeypatch.setattr(confirmation, "_deliver_bypass_gb", AsyncMock(return_value=True))
    verify_premium, verify_bypass = AsyncMock(), AsyncMock()
    monkeypatch.setattr(verify_delivery, "verify_premium_delivery", verify_premium)
    monkeypatch.setattr(verify_delivery, "verify_bypass_delivery", verify_bypass)

    out = await run_webhook(w, monkeypatch, pending_for("basic", 30))

    assert out["transient"] is not None, "still 5xx: the provider retries"
    assert getattr(out["transient"], "alerted", False) is True
    assert w.alerts == [], [a["text"] for a in w.alerts]
    verify_premium.assert_not_called()


async def test_webhook_first_purchase_premium_has_one_verifier(monkeypatch):
    """P2-27: a returning user (bypass entity kept) buys again via a webhook — a
    first purchase. The legacy delayed check already verifies premium, so
    verify_premium_delivery must not verify it a second time (one premium
    mismatch gave two alerts). The GB of this purchase are still verified."""
    from app.services.payments import verify_delivery
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    seed(w, "expired")
    monkeypatch.setattr(database, "finalize_purchase", AsyncMock(return_value={
        "success": True, "payment_id": 501, "is_renewal": False, "subscription_type": "basic",
        "period_days": 30, "bypass_created_fresh": False, "expires_at": utcnow() + timedelta(days=30)}))
    monkeypatch.setattr(confirmation, "_deliver_bypass_gb", AsyncMock(return_value=True))
    legacy_check, verify_premium, verify_bypass = MagicMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(verify_delivery, "schedule_legacy_check", legacy_check)
    monkeypatch.setattr(verify_delivery, "verify_premium_delivery", verify_premium)
    monkeypatch.setattr(verify_delivery, "verify_bypass_delivery", verify_bypass)

    out = await run_webhook(w, monkeypatch, pending_for("basic", 30))

    assert out["result"] == {"status": "ok"}
    legacy_check.assert_called_once()
    verify_premium.assert_not_called()
    verify_bypass.assert_called_once()


async def test_alert_legacy_resync_that_gives_up_is_forced_again(monkeypatch):
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    seed(w, "active")
    set_panel_down(w, True)
    await run_webhook(w, monkeypatch, pending_for("basic", 30))
    first = len(w.forced_alerts())

    w.resync_gate.set()                      # every re-sync attempt runs — the panel stays down
    await asyncio.gather(*purchase_flow._resync_tasks.values())

    final = [a for a in w.forced_alerts()[first:] if "gave up" in a["text"]]
    assert final, [a["text"] for a in w.alerts]
    assert w.payment_errors[-1]["error_code"] == "renewal_sync_gave_up"


async def test_legacy_resync_never_lowers_a_later_db_date(monkeypatch):
    """A re-sync after a failed renewal targets the CURRENT DB date: a second
    renewal that already moved DB + panel forward is never undone."""
    w = build(monkeypatch, flag="off", entrypoint="webhook")
    seed(w, "active")
    before = snap(w)
    set_panel_down(w, True)
    await run_webhook(w, monkeypatch, pending_for("basic", 30, purchase_id="pid-1"))
    set_panel_down(w, False)
    await run_webhook(w, monkeypatch, pending_for("basic", 30, purchase_id="pid-2"))
    assert aware(w.sub["expires_at"]) == before.db_expires + Months(1) + Months(1)

    w.resync_gate.set()
    await asyncio.gather(*purchase_flow._resync_tasks.values())

    assert abs((w.premium_until() - aware(w.sub["expires_at"])).total_seconds()) < 1


async def test_alert_legacy_bypass_topup_not_applied_is_forced(monkeypatch):
    """renew_remnawave_user (legacy GB top-up of Telegram / balance / auto-renewal /
    admin / gift) ignored a failed PATCH and logged success (M-RENEW-GB-SILENT)."""
    w = build(monkeypatch, flag="off", entrypoint="balance")
    seed(w, "active")
    from app.services import remnawave_api
    real_update = remnawave_api.update_user

    async def refuse_traffic(ref, **fields):
        if "trafficLimitBytes" in fields:
            return None
        return await real_update(ref, **fields)
    monkeypatch.setattr(remnawave_api, "update_user", refuse_traffic)

    await remnawave_service.renew_remnawave_user(TG, "basic", utcnow() + timedelta(days=30), period_days=30)

    assert any("Bypass GB NOT delivered" in a["text"] for a in w.forced_alerts())
    assert w.payment_errors[-1]["stage"] == "bypass_topup"


# ═══════════════════════════════════════════════════════════════════════
# 11. Referral cashback (owner 2026-09-14, N17): ONLY for a purchase — any
#     purchase however it was paid (provider, Telegram / Stars, balance,
#     auto-renewal from balance; subscription, combo, GB pack, gift, shop) —
#     NEVER for a balance top-up. Base = the amount paid; once per purchase.
#     process_referral_reward itself (tiers, floor, duplicate guard) is the
#     harness mock here — its own tests are test_referral_reward_*.py.
# ═══════════════════════════════════════════════════════════════════════

def _cashback(w) -> list:
    return [c.kwargs for c in w.db.process_referral_reward.await_args_list]


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("provider", ["platega", "wata", "cryptobot"])
async def test_cashback_balance_topup_gives_none(monkeypatch, provider, flag):
    w = build(monkeypatch, flag=flag, entrypoint="webhook")
    monkeypatch.setattr("database.users.increase_balance", AsyncMock(return_value=True))
    pending = h.make_pending(purchase_id="topup-1", tariff=None, period_days=0,
                             purchase_type="balance_topup", price_rub=199)

    out = await run_webhook(w, monkeypatch, pending, provider=provider)

    assert out["result"] == {"status": "ok"}
    assert _cashback(w) == [], "a balance top-up earns no cashback (the purchase paid from it does)"


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", ["platega", "wata", "cryptobot", "telegram", "stars", "balance"])
@pytest.mark.parametrize("tariff", ["basic", "combo_basic"])
async def test_cashback_subscription_purchase_once_on_the_amount_paid(monkeypatch, entry, tariff, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, "active", sub_type=tier_of(tariff), is_combo=tariff.startswith("combo_"))

    await run_purchase(w, monkeypatch, entry, tariff, 30)

    calls = _cashback(w)
    assert len(calls) == 1, calls
    assert calls[0]["buyer_id"] == TG
    assert calls[0]["amount_rubles"] == pytest.approx(purchase_price(tariff, 30))
    if entry == "balance":
        assert calls[0]["purchase_id"].startswith("balance_purchase_")
    else:
        assert calls[0]["purchase_id"] == "pid-1"


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("tariff", ["basic", "combo_plus"])
async def test_cashback_auto_renewal_from_balance_once(monkeypatch, tariff, flag):
    w = build(monkeypatch, flag=flag, entrypoint="autorenew")
    seed(w, "active", sub_type=tier_of(tariff), is_combo=tariff.startswith("combo_"))

    await run_autorenew(w, monkeypatch, last_payment=f"{tier_of(tariff)}_30")

    calls = _cashback(w)
    assert len(calls) == 1, calls
    assert calls[0]["buyer_id"] == TG and calls[0]["purchase_id"].startswith("autorenew_")
    paid = [p["amount"] for p in w.payments]
    assert len(paid) == 1 and calls[0]["amount_rubles"] == pytest.approx(paid[0] / 100)


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", ["platega", "telegram", "stars"])
async def test_cashback_traffic_pack_once(monkeypatch, entry, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, "active")
    pending = pack_pending(PACK_SIZES[0])

    if entry == "platega":
        await run_webhook(w, monkeypatch, pending)
    else:
        await run_telegram(w, monkeypatch, pending, stars=entry == "stars")

    calls = _cashback(w)
    assert len(calls) == 1, calls
    assert calls[0]["purchase_id"] == "pack-1"
    assert calls[0]["amount_rubles"] == pytest.approx(pending["price_kopecks"] / 100)


@pytest.mark.parametrize("entry", ["platega", "telegram"])
async def test_cashback_gift_purchase_once(monkeypatch, entry):
    from app.handlers.callbacks import gift as gift_mod
    w = build(monkeypatch, flag="off", entrypoint=ENTRY_FLAG[entry])
    monkeypatch.setattr(gift_mod, "_send_gift_success", AsyncMock())
    pending = h.make_pending(purchase_id="gift-buy-1", tariff="basic", period_days=30, purchase_type="gift")

    if entry == "platega":
        await run_webhook(w, monkeypatch, pending)
    else:
        await run_telegram(w, monkeypatch, pending)

    calls = _cashback(w)
    assert len(calls) == 1, calls
    assert calls[0]["purchase_id"] == "gift-buy-1" and calls[0]["amount_rubles"] == pytest.approx(199.0)


@pytest.mark.parametrize("entry", ["platega", "telegram"])
async def test_cashback_shop_purchase_once_and_not_on_replay(monkeypatch, entry):
    """Shop order (🔒 files untouched): the cashback is accrued by the shared core
    right after it marks the order paid; a re-delivered webhook adds nothing."""
    import database.users as db_users
    from app.handlers.payments import telegram_premium
    w = build(monkeypatch, flag="off", entrypoint=ENTRY_FLAG[entry])
    monkeypatch.setattr(db_users, "get_pool", AsyncMock(return_value=w.pool))
    monkeypatch.setattr(database, "mark_pending_purchase_paid", AsyncMock(side_effect=[True, False]))
    monkeypatch.setattr(telegram_premium, "send_premium_success", AsyncMock())
    pending = h.make_pending(purchase_id="shop-1", tariff="premium_3m", period_days=0,
                             purchase_type="telegram_premium", price_rub=1499)

    if entry == "platega":
        await run_webhook(w, monkeypatch, pending)
        await run_webhook(w, monkeypatch, dict(pending))
    else:
        await run_telegram(w, monkeypatch, pending)

    calls = _cashback(w)
    assert len(calls) == 1, calls
    assert calls[0]["buyer_id"] == TG and calls[0]["purchase_id"] == "shop-1"
    assert calls[0]["amount_rubles"] == pytest.approx(1499.0)


@pytest.mark.parametrize("flag", ["off", "on"])
@pytest.mark.parametrize("entry", ["platega", "wata", "telegram", "stars"])
async def test_cashback_replay_gives_no_second(monkeypatch, entry, flag):
    w = build(monkeypatch, flag=flag, entrypoint=ENTRY_FLAG[entry])
    seed(w, "active")
    await run_purchase(w, monkeypatch, entry, "basic", 30)

    if entry in WEBHOOK_PROVIDERS:
        await run_webhook(w, monkeypatch, dict(w.pending), provider=entry)
    else:
        await run_telegram(w, monkeypatch, dict(w.pending), stars=entry == "stars")

    assert len(_cashback(w)) == 1


# ═══════════════════════════════════════════════════════════════════════
# 12. Auto-renewal renews the last SUBSCRIPTION, not the last payment
#     (P1, code walkthrough 2026-09-14): the tariff / period came from the last
#     approved payment of ANY kind — a top-up, gift, GB pack or farm shield was
#     parsed as «basic, 30 days». Flag off: Plus 90 renewed as Basic 30; flag on:
#     the subscription's tier, but the period / price of that payment.
#     (flag off + plus is the known T0-AUTORENEW-PLUS bug, not repeated here)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("latest", ["balance_topup", "gift_basic_30", "traffic_15gb", "farm_storm_shield"])
@pytest.mark.parametrize("flag,tariff", [("off", "basic"), ("on", "basic"), ("on", "plus")])
async def test_autorenew_renews_the_last_subscription_not_the_last_payment(monkeypatch, flag, tariff, latest):
    w = build(monkeypatch, flag=flag, entrypoint="autorenew")
    seed(w, "active", sub_type=tariff)
    before = snap(w)

    await run_autorenew(w, monkeypatch, last_payment=f"{tariff}_90", newer=(latest,))

    check(w, before, autorenew_expect(tariff, 90))
    assert [p["amount"] for p in w.payments] == [round(config.TARIFFS[tariff][90]["price"] * 100)], \
        "billed at today's price of the subscription's own tariff / period"
