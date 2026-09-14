"""
Platega wave-1 fixes (docs/audit/00_recon.md §3a; docs/providers/platega_api.md).

Recurring SBP subscriptions (paymentMethod=6) are REMOVED (owner decision).
1. Safety net: subscription callbacks (UpperCamel) on the COMMON callback URL
   and on /webhooks/platega-subscription are auth-checked as before, then only
   logged + payment_errors + FORCED admin alert, HTTP 200. Nothing is granted,
   enqueued or written to platega_subscriptions.
2. CONFIRMED one-off callback without amount / with non-RUB currency is
   rejected for VPN purchases; shop purchases keep the old behaviour.
3. CHARGEBACKED -> forced admin alert, no automatic revoke.
4. _verify_auth helper.
"""
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

MERCHANT = "merchant-test"
SECRET = "secret-test"
GOOD_HEADERS = {"x-merchantid": MERCHANT, "x-secret": SECRET}
BAD_HEADERS = {"x-merchantid": MERCHANT, "x-secret": "nope"}
SUB_ID = "sub-111"
TG = 424242
PURCHASE_ID = "p-1"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class RecordingConn:
    """asyncpg stand-in for database.platega_subscriptions: records every SQL."""

    def __init__(self, row):
        self.row = row
        self.sql = []

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        return self.row

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return [self.row] if self.row else []

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        return None

    async def execute(self, sql, *args):
        self.sql.append(sql)


class RecordingPool:
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


@pytest.fixture
def env(monkeypatch):
    import platega_service
    svc = importlib.reload(platega_service)
    import database.platega_subscriptions as psub_mod
    import app.services.admin_alerts as admin_alerts
    import app.services.payments.confirmation as conf

    from app.services import provisioning

    sub_conn = RecordingConn({
        "subscription_id": SUB_ID, "telegram_id": TG, "tariff_type": "basic",
        "interval_days": 30, "amount_kopecks": 19900, "status": "Active",
    })
    monkeypatch.setattr(psub_mod._core, "DB_READY", True)
    monkeypatch.setattr(psub_mod, "get_pool", AsyncMock(return_value=RecordingPool(sub_conn)))
    enqueue = AsyncMock()
    monkeypatch.setattr(provisioning, "enqueue", enqueue)

    db = MagicMock()
    db.DB_READY = True
    db.grant_access = AsyncMock(return_value={"action": "renewal"})
    db.log_payment_error = AsyncMock(return_value=1)
    monkeypatch.setattr(svc, "database", db)
    monkeypatch.setattr(svc, "PLATEGA_MERCHANT_ID", MERCHANT)
    monkeypatch.setattr(svc, "PLATEGA_SECRET", SECRET)

    alerts = []

    async def fake_send_alert(bot, category, message, *, force=False):
        alerts.append(SimpleNamespace(category=category, message=message, force=force))
        return True

    monkeypatch.setattr(admin_alerts, "send_alert", fake_send_alert)

    pending = {
        "purchase_id": PURCHASE_ID,
        "telegram_id": TG,
        "purchase_type": "subscription",
        "tariff": "basic",
        "price_kopecks": 19900,
        "status": "pending",
    }
    lookup = AsyncMock(side_effect=lambda provider, pid: {
        "status": "ok", "purchase": pending, "telegram_id": TG,
    })
    confirmed = AsyncMock(return_value={"status": "ok", "purchase_id": PURCHASE_ID})
    monkeypatch.setattr(conf, "lookup_pending_purchase", lookup)
    monkeypatch.setattr(conf, "process_confirmed_payment", confirmed)

    bot = MagicMock()
    bot.send_message = AsyncMock()
    return SimpleNamespace(
        svc=svc, db=db, alerts=alerts, bot=bot, sub_conn=sub_conn, enqueue=enqueue,
        pending=pending, confirmed=confirmed, monkeypatch=monkeypatch,
    )


def charge_body(charge_id="ch-1", status="CONFIRMED", amount=199.0, payload=True):
    return {
        "Id": charge_id,
        "Amount": amount,
        "Currency": "RUB",
        "Status": status,
        "PaymentMethod": 6,
        "Payload": json.dumps({"telegram_id": TG, "tariff": "basic", "days": 30}) if payload else None,
        "SubscriptionId": SUB_ID,
        "NextChargeAt": "2026-10-13T09:10:00Z",
    }


def status_body(status="SUBSCRIPTION_ACTIVATED"):
    return {
        "Id": SUB_ID,
        "Amount": 199.0,
        "Currency": "RUB",
        "Status": status,
        "PaymentMethod": 6,
        "Payload": None,
        "SubscriptionId": SUB_ID,
        "NextChargeAt": "2026-10-13T09:10:00Z",
    }


def one_off_body(amount=None, currency="RUB", status="CONFIRMED", tx="tx-1"):
    body = {
        "id": tx,
        "status": status,
        "paymentMethod": 2,
        "payload": json.dumps({"purchase_id": PURCHASE_ID}),
    }
    if amount is not None:
        body["amount"] = amount
    if currency is not None:
        body["currency"] = currency
    return body


def forced_alerts(env):
    return [a for a in env.alerts if a.force]


# ---------------------------------------------------------------------------
# Task 1 — recurring removed: alert-only safety net for stray callbacks
# ---------------------------------------------------------------------------

INSTRUCTION = "Рекуррентные подписки отключены. Отмените подписку в кабинете Platega"


def assert_safety_net(env, result, *, charge_id, status):
    # 200-mapped result, nothing credited
    assert result["status"] == "ok"
    assert result["event"] == "recurring_disabled"
    from app.api import payment_webhook
    assert payment_webhook._STATUS_HTTP[result["status"]] == 200
    # forced alert with the ids, status, amount and the instruction
    alerts = forced_alerts(env)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.category == "payment"
    assert SUB_ID in a.message and charge_id in a.message and status in a.message
    assert "199.00" in a.message
    assert INSTRUCTION in a.message and "POST /subscription/{id}/cancel" in a.message
    # payment_errors row
    env.db.log_payment_error.assert_awaited_once()
    kw = env.db.log_payment_error.await_args.kwargs
    assert kw["payment_provider"] == "platega" and kw["error_code"] == status
    assert kw["telegram_id"] == TG
    # NO grant / NO enqueue / NO one-off finalization / NO other database.* call
    env.db.grant_access.assert_not_awaited()
    env.enqueue.assert_not_awaited()
    env.confirmed.assert_not_awaited()
    assert [c[0] for c in env.db.method_calls] == ["log_payment_error"]
    # NO DB writes to platega_subscriptions / charges (reads only, if any)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in env.sub_conn.sql), env.sub_conn.sql
    env.bot.send_message.assert_not_awaited()  # user is not notified; admin decides


@pytest.mark.parametrize("entry", ["common", "dedicated"])
@pytest.mark.parametrize("status", ["CONFIRMED", "CANCELED"])
async def test_subscription_charge_callback_is_alert_only(env, entry, status):
    fn = env.svc.process_webhook_data if entry == "common" else env.svc.process_subscription_webhook_data
    result = await fn(GOOD_HEADERS, charge_body(status=status), env.bot)
    assert_safety_net(env, result, charge_id="ch-1", status=status)


async def test_confirmed_charge_alert_says_money_taken_access_not_granted(env):
    await env.svc.process_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    assert "доступ НЕ выдан" in forced_alerts(env)[0].message


@pytest.mark.parametrize("entry", ["common", "dedicated"])
@pytest.mark.parametrize("status", [
    "SUBSCRIPTION_ACTIVATED", "SUBSCRIPTION_PAST_DUE",
    "SUBSCRIPTION_CANCELLED", "SUBSCRIPTION_FAILED",
])
async def test_subscription_status_callback_is_alert_only(env, entry, status):
    fn = env.svc.process_webhook_data if entry == "common" else env.svc.process_subscription_webhook_data
    result = await fn(GOOD_HEADERS, status_body(status), env.bot)
    assert_safety_net(env, result, charge_id=SUB_ID, status=status)


async def test_telegram_id_falls_back_to_read_only_subscription_row(env):
    result = await env.svc.process_subscription_webhook_data(
        GOOD_HEADERS, charge_body(payload=False), env.bot,
    )
    assert_safety_net(env, result, charge_id="ch-1", status="CONFIRMED")
    assert env.sub_conn.sql  # looked up (SELECT only, asserted above)


async def test_status_only_body_without_subscription_id_still_alerts(env):
    body = {"Id": "x-1", "Status": "SUBSCRIPTION_CANCELLED", "Amount": 199.0}
    result = await env.svc.process_webhook_data(GOOD_HEADERS, body, env.bot)
    assert result["event"] == "recurring_disabled"
    assert forced_alerts(env) and "x-1" in forced_alerts(env)[0].message
    env.db.grant_access.assert_not_awaited()


@pytest.mark.parametrize("entry", ["common", "dedicated"])
async def test_subscription_callback_bad_auth_is_rejected(env, entry):
    fn = env.svc.process_webhook_data if entry == "common" else env.svc.process_subscription_webhook_data
    result = await fn(BAD_HEADERS, charge_body(), env.bot)
    assert result["status"] == "unauthorized"
    assert not env.alerts
    env.db.log_payment_error.assert_not_awaited()
    env.db.grant_access.assert_not_awaited()


@pytest.mark.parametrize("entry", ["common", "dedicated"])
async def test_subscription_callback_db_not_ready_is_500_as_before(env, entry):
    env.db.DB_READY = False
    fn = env.svc.process_webhook_data if entry == "common" else env.svc.process_subscription_webhook_data
    with pytest.raises(env.svc.TransientPaymentError):
        await fn(GOOD_HEADERS, charge_body(), env.bot)
    assert not env.alerts


@pytest.mark.parametrize("path", [
    "/webhooks/platega", "/platega/callback",
    "/webhooks/platega-subscription", "/platega/subscription-callback",
])
def test_http_routes_answer_200_and_alert(env, path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import payment_webhook

    env.monkeypatch.setattr(payment_webhook, "_bot", env.bot)
    app = FastAPI()
    app.include_router(payment_webhook.router)
    client = TestClient(app)

    resp = client.post(path, json=charge_body(), headers={"X-MerchantId": MERCHANT, "X-Secret": SECRET})
    assert resp.status_code == 200
    assert resp.json()["event"] == "recurring_disabled"
    assert len(forced_alerts(env)) == 1
    env.db.grant_access.assert_not_awaited()

    bad = client.post(path, json=charge_body(), headers={"X-MerchantId": MERCHANT, "X-Secret": "nope"})
    # P1-4: bad credentials → 500 (Platega retries) + one more forced alert
    assert bad.status_code == 500 and bad.json()["status"] == "unauthorized"
    assert len(forced_alerts(env)) == 2
    assert "unauthorized" in forced_alerts(env)[1].message


def test_recurring_code_is_gone(env):
    import database.platega_subscriptions as psub_mod
    from app.services import provisioning_flags
    for name in ("create_subscription", "check_subscription_status", "is_subscription_visible_to",
                 "_process_confirmed_charge", "_apply_confirmed_charge",
                 "_apply_confirmed_charge_outbox", "SUBSCRIPTION_INTERVAL_MONTH"):
        assert not hasattr(env.svc, name), name
    for name in ("create_subscription", "update_subscription_status", "record_charge",
                 "claim_charge", "confirm_charge", "release_charge"):
        assert not hasattr(psub_mod, name), name
    assert "platega_recurring" not in provisioning_flags.ALL_ENTRYPOINTS


async def test_common_url_one_off_callback_uses_one_off_path(env):
    result = await env.svc.process_webhook_data(GOOD_HEADERS, one_off_body(amount=199), env.bot)
    assert result["status"] == "ok"
    env.confirmed.assert_awaited_once()
    env.db.grant_access.assert_not_awaited()


# ---------------------------------------------------------------------------
# Task 2 — amount / currency of a CONFIRMED one-off callback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("amount", [None, 0])
async def test_vpn_purchase_missing_amount_is_rejected(env, amount):
    result = await env.svc.process_webhook_data(GOOD_HEADERS, one_off_body(amount=amount), env.bot)
    assert result["status"] == "rejected"
    env.confirmed.assert_not_awaited()
    alerts = forced_alerts(env)
    assert alerts and PURCHASE_ID in alerts[0].message


async def test_vpn_purchase_non_rub_currency_is_rejected(env):
    result = await env.svc.process_webhook_data(
        GOOD_HEADERS, one_off_body(amount=199, currency="USD"), env.bot,
    )
    assert result["status"] == "rejected"
    env.confirmed.assert_not_awaited()
    assert forced_alerts(env)


async def test_vpn_purchase_currency_inside_payment_details_is_checked(env):
    body = one_off_body(amount=None, currency=None)
    body["paymentDetails"] = {"amount": 199, "currency": "USD"}
    result = await env.svc.process_webhook_data(GOOD_HEADERS, body, env.bot)
    assert result["status"] == "rejected"
    env.confirmed.assert_not_awaited()


async def test_vpn_purchase_valid_amount_is_processed(env):
    result = await env.svc.process_webhook_data(GOOD_HEADERS, one_off_body(amount=199), env.bot)
    assert result["status"] == "ok"
    assert env.confirmed.await_args.kwargs["amount_rubles"] == 199.0
    assert not forced_alerts(env)


@pytest.mark.parametrize("purchase_type, tariff", [
    ("telegram_premium", "premium_3m"),
    ("telegram_stars", "stars_100"),
    ("steam", ""),
    ("spotify", ""),
    ("subscription", "apple_id_usa_10"),
    ("subscription", "steam_500"),
    ("subscription", "spotify_1m"),
])
@pytest.mark.parametrize("amount, currency", [(None, "RUB"), (0, None), (None, "USD")])
async def test_shop_purchase_keeps_old_amount_behaviour(env, purchase_type, tariff, amount, currency):
    env.pending["purchase_type"] = purchase_type
    env.pending["tariff"] = tariff
    result = await env.svc.process_webhook_data(
        GOOD_HEADERS, one_off_body(amount=amount, currency=currency), env.bot,
    )
    assert result["status"] == "ok"
    # Old behaviour: missing amount is replaced by the stored price.
    assert env.confirmed.await_args.kwargs["amount_rubles"] == 199.0
    assert not env.alerts


# ---------------------------------------------------------------------------
# Task 3 — CHARGEBACKED
# ---------------------------------------------------------------------------

async def test_chargeback_alerts_admin_and_does_not_revoke(env):
    body = one_off_body(amount=199, status="CHARGEBACKED", tx="tx-9")
    result = await env.svc.process_webhook_data(GOOD_HEADERS, body, env.bot)
    assert result["status"] != "ignored"
    alerts = forced_alerts(env)
    assert alerts
    msg = alerts[0].message
    assert PURCHASE_ID in msg and "tx-9" in msg and "199" in msg
    env.confirmed.assert_not_awaited()
    env.db.grant_access.assert_not_awaited()
    assert not any("revoke" in call[0] for call in env.db.method_calls)


# ---------------------------------------------------------------------------
# Task 4 — _verify_auth
# ---------------------------------------------------------------------------

def test_verify_auth(env):
    svc = env.svc
    assert svc._verify_auth(GOOD_HEADERS) is True
    assert svc._verify_auth({"X-MerchantId": MERCHANT, "X-Secret": SECRET}) is True
    assert svc._verify_auth(BAD_HEADERS) is False
    assert svc._verify_auth({}) is False
    env.monkeypatch.setattr(svc, "PLATEGA_SECRET", "")
    assert svc._verify_auth({"x-merchantid": MERCHANT, "x-secret": ""}) is False
