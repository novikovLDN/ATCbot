"""
T6 — webhook P0 fixes (docs/audit/02_payment_core_plan.md §D, task T6).

1. finalize_purchase: concurrent duplicate webhooks → exactly one finalization,
   the other gets the idempotent "already processed" path (no PERMANENT alert).
2. process_confirmed_payment: TransientPaymentError propagates (→ HTTP 500).
3. payment_webhook: one status→HTTP map for every provider route.
4. not_found → 200 + forced admin alert + payment_errors (WATA alerts itself).
5. lookup_pending_purchase: provider mismatch → rejected + forced alert.
6. Balance top-up credits the EXPECTED amount, not the webhook amount.
"""
import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import subscriptions as subs
from app.api import payment_webhook
from app.services.payments import confirmation
from app.services.payments.confirmation import TransientPaymentError


# ── Fake asyncpg pool/connection with real lock semantics ───────────────


class FakeDB:
    """In-memory pending_purchases/payments with Postgres-like locking:
    session advisory locks, row locks held until the end of a transaction,
    SKIP LOCKED, and rollback on exception."""

    def __init__(self, rows):
        self.rows = {r["purchase_id"]: dict(r) for r in rows}
        self.payments = []
        self.adv_locks = {}
        self.row_locks = {}
        self.leaked_adv_locks = []
        self.after_first_pending_read = None

    def adv(self, key):
        return self.adv_locks.setdefault(key, asyncio.Lock())

    def row_lock(self, purchase_id):
        return self.row_locks.setdefault(purchase_id, asyncio.Lock())

    def pool(self):
        db = self

        class _Acquire:
            async def __aenter__(self):
                self.conn = FakeConn(db)
                return self.conn

            async def __aexit__(self, *exc):
                # asyncpg pool reset runs pg_advisory_unlock_all(); record
                # anything the code under test forgot to unlock itself.
                db.leaked_adv_locks.extend(self.conn.adv_held)
                self.conn.release_all()
                return False

        class _Pool:
            def acquire(self):
                return _Acquire()

        return _Pool()


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_tx = True
        self.conn.undo = []
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            for fn in reversed(self.conn.undo):
                fn()
        self.conn.in_tx = False
        self.conn.undo = []
        for lock in self.conn.row_locks_held:
            lock.release()
        self.conn.row_locks_held = []
        return False


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.in_tx = False
        self.undo = []
        self.row_locks_held = []
        self.adv_held = []

    def transaction(self):
        return _Tx(self)

    def release_all(self):
        for lock in self.adv_held:
            lock.release()
        self.adv_held = []
        for lock in self.row_locks_held:
            lock.release()
        self.row_locks_held = []

    async def _hold_row_lock(self, purchase_id):
        lock = self.db.row_lock(purchase_id)
        if lock in self.row_locks_held:
            return
        await lock.acquire()
        self.row_locks_held.append(lock)

    async def fetchrow(self, sql, *args):
        await asyncio.sleep(0)
        s = " ".join(sql.split())
        if "pg_advisory_unlock" in s:
            lock = self.db.adv(args)
            if lock in self.adv_held:
                self.adv_held.remove(lock)
                lock.release()
            return {"pg_advisory_unlock": True}
        if "pg_advisory_lock" in s:
            lock = self.db.adv(args)
            await lock.acquire()
            self.adv_held.append(lock)
            return {"pg_advisory_lock": None}
        if "FROM pending_purchases" in s:
            purchase_id = args[0]
            if "FOR UPDATE" in s:
                lock = self.db.row_lock(purchase_id)
                if "SKIP LOCKED" in s:
                    if lock.locked() and lock not in self.row_locks_held:
                        return None
                elif self.in_tx:
                    await self._hold_row_lock(purchase_id)
                else:  # autocommit statement: lock taken and released at once
                    await lock.acquire()
                    lock.release()
            row = self.db.rows.get(purchase_id)
            result = dict(row) if row else None
            hook, self.db.after_first_pending_read = self.db.after_first_pending_read, None
            if hook:
                hook(self.db)
            return result
        return None  # subscriptions etc.: no row

    async def execute(self, sql, *args):
        await asyncio.sleep(0)
        s = " ".join(sql.split())
        if s.startswith("UPDATE pending_purchases"):
            purchase_id = args[0]
            if self.in_tx:
                await self._hold_row_lock(purchase_id)
            row = self.db.rows.get(purchase_id)
            if row and row["status"] in ("pending", "expired"):
                prev = row["status"]
                row["status"] = "paid"
                self.undo.append(lambda: row.__setitem__("status", prev))
                return "UPDATE 1"
            return "UPDATE 0"
        if s.startswith("UPDATE payments"):
            return "UPDATE 1"
        return "OK"

    async def fetchval(self, sql, *args):
        await asyncio.sleep(0)
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO payments"):
            rec = {"telegram_id": args[0], "tariff": args[1], "amount": args[2]}
            self.db.payments.append(rec)
            self.undo.append(lambda: self.db.payments.remove(rec))
            return len(self.db.payments)
        return None


def _row(**overrides):
    row = {
        "purchase_id": "p-1",
        "telegram_id": 111,
        "status": "pending",
        "tariff": "basic",
        "period_days": 30,
        "purchase_type": "subscription",
        "price_kopecks": 19900,
        "promo_code": None,
        "country": None,
        "is_combo": False,
        "payment_provider": None,
    }
    row.update(overrides)
    return row


def _topup_row(**overrides):
    base = {"purchase_id": "p-topup", "tariff": None, "period_days": 0,
            "purchase_type": "balance_topup"}
    base.update(overrides)
    return _row(**base)


@pytest.fixture
def fake_db(monkeypatch):
    def make(rows):
        db = FakeDB(rows)
        pool = db.pool()
        monkeypatch.setattr(subs, "get_pool", AsyncMock(return_value=pool))
        db.increase_balance = AsyncMock(return_value=True)
        db.referral = AsyncMock(return_value={"success": False, "reason": "no_referrer"})
        monkeypatch.setattr("database.users.increase_balance", db.increase_balance)
        monkeypatch.setattr("database.users.process_referral_reward", db.referral)
        monkeypatch.setattr(subs, "set_combo_flag", AsyncMock())
        monkeypatch.setattr(subs, "set_bypass_only_flag", AsyncMock())

        async def _provision(*args, **kwargs):
            await asyncio.sleep(0.01)  # HTTP to the panel: lets the other webhook interleave
            return {"uuid": "uuid-1", "vless_url": "vless://x",
                    "subscription_type": "basic", "bypass_created_fresh": True}

        db.provision = AsyncMock(side_effect=_provision)
        monkeypatch.setattr("app.services.purchase_flow.provision_subscription", db.provision)
        db.grant = AsyncMock(return_value={
            "action": "new_issuance",
            "vless_url": "vless://x",
            "subscription_end": datetime.now(timezone.utc) + timedelta(days=30),
            "subscription_type": "basic",
        })
        monkeypatch.setattr(subs, "grant_access", db.grant)
        return db
    return make


def _split(results):
    ok = [r for r in results if isinstance(r, dict)]
    errs = [r for r in results if isinstance(r, BaseException)]
    return ok, errs


# ── 1. finalize_purchase: lock inside the idempotency decision ──────────


async def test_concurrent_topup_finalizes_exactly_once(fake_db):
    db = fake_db([_topup_row()])
    results = await asyncio.gather(
        subs.finalize_purchase("p-topup", "platega", 199.0, "inv-1"),
        subs.finalize_purchase("p-topup", "platega", 199.0, "inv-1"),
        return_exceptions=True,
    )
    ok, errs = _split(results)
    assert len(ok) == 1 and ok[0]["success"] is True
    assert len(errs) == 1
    assert isinstance(errs[0], ValueError) and "already processed" in str(errs[0])
    assert db.increase_balance.await_count == 1
    assert len(db.payments) == 1
    assert db.rows["p-topup"]["status"] == "paid"


async def test_concurrent_subscription_provisions_panel_once(fake_db):
    db = fake_db([_row()])
    results = await asyncio.gather(
        subs.finalize_purchase("p-1", "wata", 199.0, "tx-1"),
        subs.finalize_purchase("p-1", "wata", 199.0, "tx-1"),
        return_exceptions=True,
    )
    ok, errs = _split(results)
    assert len(ok) == 1
    assert len(errs) == 1
    assert isinstance(errs[0], ValueError) and "already processed" in str(errs[0])
    assert db.provision.await_count == 1  # Phase-1 panel HTTP ran once
    assert db.grant.await_count == 1
    assert len(db.payments) == 1


async def test_status_rechecked_under_row_lock_inside_transaction(fake_db):
    """Another writer (not holding the advisory lock) marks the row paid
    between the pre-read and the transaction → idempotent ValueError, not a
    generic 'Failed to mark pending purchase as paid' Exception."""
    db = fake_db([_topup_row()])

    def _flip(d):
        d.rows["p-topup"]["status"] = "paid"

    db.after_first_pending_read = _flip
    with pytest.raises(ValueError, match="already processed"):
        await subs.finalize_purchase("p-topup", "platega", 199.0, "inv-1")
    db.increase_balance.assert_not_awaited()
    assert db.payments == []


async def test_advisory_lock_released_on_error(fake_db):
    db = fake_db([_topup_row()])
    with pytest.raises(ValueError, match="(?i)amount mismatch"):
        await subs.finalize_purchase("p-topup", "platega", 100.0, "inv-1")
    assert db.leaked_adv_locks == []
    assert db.adv_locks, "finalize_purchase must take an advisory lock"
    assert all(not lock.locked() for lock in db.adv_locks.values())


async def test_advisory_lock_released_on_success(fake_db):
    db = fake_db([_topup_row()])
    await subs.finalize_purchase("p-topup", "platega", 199.0, "inv-1")
    assert db.leaked_adv_locks == []


async def test_concurrent_webhooks_no_permanent_alert(fake_db, monkeypatch):
    fake_db([_topup_row()])
    db_mod = confirmation.database
    monkeypatch.setattr(db_mod, "get_pending_purchase_by_id", AsyncMock(return_value=_topup_row()))
    monkeypatch.setattr(db_mod, "get_subscription", AsyncMock(return_value=None))
    monkeypatch.setattr(confirmation, "_send_confirmation", AsyncMock())
    alert = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.admin_alerts.alert_payment_failure", alert)

    results = await asyncio.gather(*[
        confirmation.process_confirmed_payment(
            provider="platega", purchase_id="p-topup", amount_rubles=199.0,
            invoice_id="inv-1", telegram_id=111, bot=MagicMock(),
        )
        for _ in range(2)
    ])
    assert sorted(r["status"] for r in results) == ["already_processed", "ok"]
    permanent = [c for c in alert.await_args_list if c.kwargs.get("is_transient") is False]
    assert permanent == []


# ── 2. TransientPaymentError propagates out of process_confirmed_payment ─


async def test_remnawave_sync_failure_raises_transient(monkeypatch):
    db_mod = confirmation.database
    monkeypatch.setattr(db_mod, "get_pending_purchase_by_id", AsyncMock(return_value=_row()))
    monkeypatch.setattr(db_mod, "finalize_purchase", AsyncMock(return_value={
        "success": True, "payment_id": 1,
        "remnawave_sync_failed": True, "remnawave_sync_error": "panel down",
    }))
    alert = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.admin_alerts.alert_payment_failure", alert)

    with pytest.raises(TransientPaymentError) as exc:
        await confirmation.process_confirmed_payment(
            provider="platega", purchase_id="p-1", amount_rubles=199.0,
            invoice_id="tx", telegram_id=111, bot=MagicMock(),
        )
    # P2-26: purchase_flow.sync_renewal_to_remnawave already sent the forced alert
    # (and scheduled the re-sync) — one incident, one alert: no second transient
    # alert here, and alerted=True keeps the webhook route from adding a third.
    assert exc.value.alerted is True
    alert.assert_not_awaited()


async def test_db_error_still_maps_to_transient(monkeypatch):
    import asyncpg
    db_mod = confirmation.database
    monkeypatch.setattr(db_mod, "get_pending_purchase_by_id", AsyncMock(return_value=_row()))
    monkeypatch.setattr(db_mod, "finalize_purchase",
                        AsyncMock(side_effect=asyncpg.PostgresError("conn lost")))
    monkeypatch.setattr("app.services.admin_alerts.alert_payment_failure", AsyncMock())
    with pytest.raises(TransientPaymentError):
        await confirmation.process_confirmed_payment(
            provider="wata", purchase_id="p-1", amount_rubles=199.0,
            invoice_id="tx", telegram_id=111, bot=MagicMock(),
        )


async def test_value_error_before_pending_lookup_is_not_name_error(monkeypatch):
    """ValueError raised before `pending` is assigned must not turn the
    replay-resync branch into NameError (→ bogus Transient/500)."""
    db_mod = confirmation.database
    monkeypatch.setattr(db_mod, "get_pending_purchase_by_id",
                        AsyncMock(side_effect=subs.PurchaseAlreadyProcessed("already processed")))
    monkeypatch.setattr(db_mod, "get_subscription", AsyncMock(return_value={
        "expires_at": datetime.now(timezone.utc) + timedelta(days=10),
        "subscription_type": "basic",
        "is_bypass_only": False,
    }))
    provision = AsyncMock(return_value={})
    monkeypatch.setattr("app.services.purchase_flow.provision_subscription", provision)

    result = await confirmation.process_confirmed_payment(
        provider="platega", purchase_id="p-1", amount_rubles=199.0,
        invoice_id="tx", telegram_id=111, bot=MagicMock(),
    )
    assert result == {"status": "already_processed"}
    assert provision.await_args.kwargs["period_days"] == 30


# ── 3. One status → HTTP map for every provider route ───────────────────

# Every {"status": ...} returned today by platega_service, wata_service,
# cryptobot_service and confirmation (grep `"status":`).
KNOWN_STATUSES = [
    "ok", "already_processed", "duplicate",
    "amount_mismatch", "provider_mismatch", "rejected",
    "invalid_amount", "invalid_currency",
    "not_found", "invalid_status", "error",
    "invalid", "ignored", "disabled",
    "refund_alerted", "declined_notified",
]

ROUTES = [
    ("/webhooks/platega", "platega_service", "process_webhook_data"),
    ("/platega/callback", "platega_service", "process_webhook_data"),
    ("/webhooks/platega-subscription", "platega_service", "process_subscription_webhook_data"),
    ("/platega/subscription-callback", "platega_service", "process_subscription_webhook_data"),
    ("/webhooks/cryptobot", "cryptobot_service", "process_webhook_data"),
    ("/webhooks/wata", "wata_service", "process_webhook_data"),
]


def test_status_map_covers_every_known_status():
    for status in KNOWN_STATUSES:
        assert payment_webhook._STATUS_HTTP[status] == 200, status
    # P1-4: auth failure of a (possibly paid) callback → 500 so the provider retries + alert
    assert payment_webhook._STATUS_HTTP["unauthorized"] == 500
    assert payment_webhook._STATUS_HTTP["transient_error"] == 500
    assert payment_webhook._STATUS_HTTP["timeout"] == 500


def _client(monkeypatch, module_name, func_name, impl, *, enabled=True, bot=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mod = importlib.import_module(module_name)
    monkeypatch.setattr(mod, "is_enabled", lambda: enabled)
    monkeypatch.setattr(mod, func_name, impl)
    monkeypatch.setattr(payment_webhook, "_bot", MagicMock() if bot else None)
    monkeypatch.setattr(payment_webhook, "_log_pe", AsyncMock())
    monkeypatch.setattr("app.services.admin_alerts.send_alert", AsyncMock(return_value=True))
    app = FastAPI()
    app.include_router(payment_webhook.router)
    return TestClient(app)


def _returning(value):
    async def impl(*args, **kwargs):
        return value
    return impl


def _raising(exc):
    async def impl(*args, **kwargs):
        raise exc
    return impl


@pytest.mark.parametrize("path,module_name,func_name", ROUTES)
@pytest.mark.parametrize("status", ["ok", "already_processed", "provider_mismatch",
                                    "not_found", "amount_mismatch", "error"])
def test_route_dict_result_status_code(monkeypatch, path, module_name, func_name, status):
    body = {"status": status, "purchase_id": "p-1"}
    client = _client(monkeypatch, module_name, func_name, _returning(body))
    resp = client.post(path, json={"x": 1})
    assert resp.status_code == 200
    assert resp.json() == body


@pytest.mark.parametrize("path,module_name,func_name", ROUTES)
def test_route_unknown_status_keeps_200(monkeypatch, path, module_name, func_name):
    client = _client(monkeypatch, module_name, func_name, _returning({"status": "brand_new"}))
    resp = client.post(path, json={"x": 1})
    assert resp.status_code == 200
    assert resp.json() == {"status": "brand_new"}


@pytest.mark.parametrize("path,module_name,func_name", ROUTES)
@pytest.mark.parametrize("exc,code,body", [
    (TransientPaymentError("db down"), 500, {"status": "transient_error"}),
    (asyncio.TimeoutError(), 500, {"status": "timeout"}),
    (ValueError("already processed"), 200, {"status": "already_processed"}),
    (RuntimeError("boom"), 500, {"status": "error"}),
])
def test_route_exception_mapping_unchanged(monkeypatch, path, module_name, func_name, exc, code, body):
    client = _client(monkeypatch, module_name, func_name, _raising(exc))
    resp = client.post(path, json={"x": 1})
    assert resp.status_code == code
    assert resp.json() == body


@pytest.mark.parametrize("path,module_name,func_name", ROUTES)
def test_route_invalid_json_is_400(monkeypatch, path, module_name, func_name):
    client = _client(monkeypatch, module_name, func_name, _returning({"status": "ok"}))
    resp = client.post(path, content=b"not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json() == {"status": "invalid"}


@pytest.mark.parametrize("path,module_name,func_name", ROUTES)
def test_route_disabled_is_200(monkeypatch, path, module_name, func_name):
    client = _client(monkeypatch, module_name, func_name, _returning({"status": "ok"}), enabled=False)
    resp = client.post(path, json={"x": 1})
    assert resp.status_code == 200
    assert resp.json() == {"status": "disabled"}


@pytest.mark.parametrize("path,module_name,func_name", ROUTES)
def test_route_bot_missing_is_500(monkeypatch, path, module_name, func_name):
    client = _client(monkeypatch, module_name, func_name, _returning({"status": "ok"}), bot=False)
    resp = client.post(path, json={"x": 1})
    assert resp.status_code == 500
    assert resp.json() == {"status": "error"}


def test_route_transient_from_confirmation_is_500(monkeypatch):
    """End-to-end: Remnawave sync failure inside process_confirmed_payment →
    TransientPaymentError → HTTP 500 (provider retries). Was 200."""
    db_mod = confirmation.database
    monkeypatch.setattr(db_mod, "get_pending_purchase_by_id", AsyncMock(return_value=_row()))
    monkeypatch.setattr(db_mod, "finalize_purchase", AsyncMock(return_value={
        "success": True, "payment_id": 1,
        "remnawave_sync_failed": True, "remnawave_sync_error": "panel down",
    }))
    monkeypatch.setattr("app.services.admin_alerts.alert_payment_failure", AsyncMock())

    async def impl(headers, body, bot):
        return await confirmation.process_confirmed_payment(
            provider="platega", purchase_id="p-1", amount_rubles=199.0,
            invoice_id="tx", telegram_id=111, bot=bot,
        )

    client = _client(monkeypatch, "platega_service", "process_webhook_data", impl)
    resp = client.post("/webhooks/platega", json={"x": 1})
    assert resp.status_code == 500
    assert resp.json() == {"status": "transient_error"}


# ── 4/5. lookup_pending_purchase: not_found alert, provider check ───────


@pytest.fixture
def lookup_env(monkeypatch):
    db_mod = confirmation.database
    env = MagicMock()
    env.payment_error = AsyncMock(return_value=1)
    env.alert = AsyncMock(return_value=True)
    env.set_row = lambda row: monkeypatch.setattr(
        db_mod, "get_pending_purchase_any_status", AsyncMock(return_value=row),
    )
    monkeypatch.setattr(db_mod, "log_payment_error", env.payment_error)
    monkeypatch.setattr("app.services.admin_alerts.send_alert", env.alert)
    monkeypatch.setattr(payment_webhook, "_bot", MagicMock())
    return env


@pytest.mark.parametrize("provider", ["platega", "cryptobot"])
async def test_not_found_forced_alert_and_payment_error(lookup_env, provider):
    lookup_env.set_row(None)
    result = await confirmation.lookup_pending_purchase(provider, "p-missing")
    assert result["status"] == "not_found"
    assert lookup_env.alert.await_count == 1
    assert lookup_env.alert.await_args.kwargs["force"] is True
    assert "p-missing" in lookup_env.alert.await_args.args[2]
    assert lookup_env.payment_error.await_count == 1
    kwargs = lookup_env.payment_error.await_args.kwargs
    assert kwargs["purchase_id"] == "p-missing" and kwargs["payment_provider"] == provider


async def test_not_found_wata_logs_but_does_not_double_alert(lookup_env):
    """wata_service already sends its own forced orphan alert on not_found."""
    lookup_env.set_row(None)
    result = await confirmation.lookup_pending_purchase("wata", "p-missing")
    assert result["status"] == "not_found"
    lookup_env.alert.assert_not_awaited()
    assert lookup_env.payment_error.await_count == 1


async def test_not_found_alert_failure_does_not_break_lookup(lookup_env):
    lookup_env.set_row(None)
    lookup_env.alert.side_effect = RuntimeError("telegram down")
    lookup_env.payment_error.side_effect = RuntimeError("db down")
    result = await confirmation.lookup_pending_purchase("platega", "p-missing")
    assert result["status"] == "not_found"


async def test_provider_mismatch_rejected_with_forced_alert(lookup_env):
    lookup_env.set_row(_row(payment_provider="wata"))
    result = await confirmation.lookup_pending_purchase("platega", "p-1")
    assert result["status"] == "provider_mismatch"
    assert lookup_env.alert.await_count == 1
    assert lookup_env.alert.await_args.kwargs["force"] is True
    assert lookup_env.payment_error.await_count == 1


@pytest.mark.parametrize("stored", [None, "", "platega"])
async def test_provider_null_or_same_is_accepted(lookup_env, stored):
    lookup_env.set_row(_row(payment_provider=stored))
    result = await confirmation.lookup_pending_purchase("platega", "p-1")
    assert result["status"] == "ok"
    assert result["telegram_id"] == 111
    lookup_env.alert.assert_not_awaited()


@pytest.mark.parametrize("purchase_type,tariff", [
    ("telegram_premium", "premium_3m"), ("telegram_stars", "stars_100"),
    ("steam", "steam_500"), ("spotify", "spotify_1m"), ("subscription", "apple_id_usa_10"),
])
async def test_provider_check_skips_shop_purchases(lookup_env, purchase_type, tariff):
    lookup_env.set_row(_row(purchase_type=purchase_type, tariff=tariff, payment_provider="wata"))
    result = await confirmation.lookup_pending_purchase("platega", "p-1")
    assert result["status"] == "ok"
    lookup_env.alert.assert_not_awaited()


async def test_already_paid_row_stays_already_processed(lookup_env):
    lookup_env.set_row(_row(status="paid", payment_provider="wata"))
    result = await confirmation.lookup_pending_purchase("platega", "p-1")
    assert result["status"] == "already_processed"


# ── 6. Balance top-up credits the expected amount ───────────────────────


async def test_topup_credits_expected_amount_on_overpayment(fake_db):
    db = fake_db([_topup_row(price_kopecks=19900)])
    result = await subs.finalize_purchase("p-topup", "wata", 203.06, "tx-1")
    assert db.increase_balance.await_args.kwargs["amount"] == pytest.approx(199.0)
    assert db.payments[0]["amount"] == 19900
    assert result["amount"] == pytest.approx(199.0)
    # owner 2026-09-14: referral cashback only for a purchase, never for a top-up
    db.referral.assert_not_awaited()


async def test_topup_small_underpayment_credits_received_amount(fake_db):
    db = fake_db([_topup_row(price_kopecks=19900)])
    await subs.finalize_purchase("p-topup", "wata", 198.60, "tx-1")
    assert db.increase_balance.await_args.kwargs["amount"] == pytest.approx(198.60)


async def test_topup_underpayment_beyond_tolerance_still_rejected(fake_db):
    db = fake_db([_topup_row(price_kopecks=19900)])
    with pytest.raises(ValueError, match="(?i)amount mismatch"):
        await subs.finalize_purchase("p-topup", "wata", 150.0, "tx-1")
    db.increase_balance.assert_not_awaited()


async def test_subscription_payment_amount_unchanged(fake_db):
    db = fake_db([_row(price_kopecks=19900)])
    await subs.finalize_purchase("p-1", "wata", 203.06, "tx-1")
    assert db.payments[0]["amount"] == 20306
