"""
Platega wave-1 fixes (docs/audit/00_recon.md §3 P0-D, §3a; docs/providers/platega_api.md).

1. A recurring charge counts as processed only after access is granted
   (grant failure / webhook timeout / DB error -> 500 so Platega retries;
   a true duplicate -> 200 without a second extension).
2. Subscription callbacks (UpperCamel) arriving on the COMMON callback URL
   are processed, not dropped as "ignored".
3. CONFIRMED one-off callback without amount / with non-RUB currency is
   rejected for VPN purchases; shop purchases keep the old behaviour.
4. CHARGEBACKED -> forced admin alert, no automatic revoke.
5. Orphan charge -> forced admin alert; subscription id persistence at
   creation is mandatory.
6. intervalCount=1 is sent; user texts do not promise automatic retries.
7. _verify_auth helper.
"""
import asyncio
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

class FakeChargeStore:
    """In-memory stand-in for database.platega_subscriptions."""

    def __init__(self):
        self.charges = {}
        self.sub_row = {
            "subscription_id": SUB_ID,
            "telegram_id": TG,
            "tariff_type": "basic",
            "interval_days": 30,
            "amount_kopecks": 19900,
        }
        self.fail_writes = False
        self.update_subscription_status = AsyncMock()
        self.create_subscription = AsyncMock()

    def _maybe_fail(self):
        if self.fail_writes:
            raise ConnectionError("db down")

    async def get_subscription(self, subscription_id):
        return dict(self.sub_row) if self.sub_row else None

    async def record_charge(self, charge_id, subscription_id, telegram_id, amount_kopecks, status):
        self._maybe_fail()
        if charge_id in self.charges:
            return False
        self.charges[charge_id] = str(status).upper()
        return True

    async def claim_charge(self, charge_id, subscription_id, telegram_id, amount_kopecks,
                           stale_after_seconds=None):
        self._maybe_fail()
        st = self.charges.get(charge_id)
        if st is None:
            self.charges[charge_id] = "PROCESSING"
            return "claimed"
        if st == "PROCESSING":
            return "in_progress"
        return "done"

    async def confirm_charge(self, charge_id, subscription_id, amount_kopecks):
        self._maybe_fail()
        if self.charges.get(charge_id) == "PROCESSING":
            self.charges[charge_id] = "CONFIRMED"
            return True
        return False

    async def release_charge(self, charge_id):
        self._maybe_fail()
        if self.charges.get(charge_id) == "PROCESSING":
            del self.charges[charge_id]


class FakeResp:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _fake_httpx_client(sent):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(json)
            return FakeResp({
                "transactionId": SUB_ID,
                "redirect": "https://pay.platega.io/subscription/x",
            })

    return FakeClient


@pytest.fixture
def env(monkeypatch):
    import platega_service
    svc = importlib.reload(platega_service)
    import database.platega_subscriptions as psub_mod
    import app.services.admin_alerts as admin_alerts
    import app.services.payments.confirmation as conf

    store = FakeChargeStore()
    for name in ("get_subscription", "record_charge", "claim_charge", "confirm_charge",
                 "release_charge", "update_subscription_status", "create_subscription"):
        monkeypatch.setattr(psub_mod, name, getattr(store, name), raising=False)

    db = MagicMock()
    db.DB_READY = True
    db.grant_access = AsyncMock(return_value={"action": "renewal"})
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
        svc=svc, store=store, db=db, alerts=alerts, bot=bot,
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
# Task 1 — recurring charge must not be lost
# ---------------------------------------------------------------------------

async def test_grant_failure_is_not_marked_processed_and_retry_grants(env):
    env.db.grant_access.side_effect = [RuntimeError("remnawave down"), {"action": "renewal"}]

    with pytest.raises(env.svc.TransientPaymentError):
        await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)

    # Platega retry of the same charge must grant access, not be a "duplicate".
    result = await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    assert result["status"] == "ok"
    assert result["event"] == "charge_confirmed"
    assert env.db.grant_access.await_count == 2
    assert env.store.charges["ch-1"] == "CONFIRMED"


async def test_true_duplicate_returns_200_without_double_extension(env):
    first = await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    second = await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    assert first["event"] == "charge_confirmed"
    assert second["status"] == "duplicate"
    assert env.db.grant_access.await_count == 1


async def test_grant_failure_sends_forced_admin_alert(env):
    env.db.grant_access.side_effect = RuntimeError("remnawave down")
    with pytest.raises(env.svc.TransientPaymentError):
        await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    alerts = forced_alerts(env)
    assert alerts, "grant failure of a paid charge must force an admin alert"
    assert SUB_ID in alerts[0].message and "ch-1" in alerts[0].message


async def test_confirmed_charge_db_error_raises_transient(env):
    env.store.fail_writes = True
    with pytest.raises(env.svc.TransientPaymentError):
        await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    env.db.grant_access.assert_not_awaited()


async def test_canceled_charge_db_error_raises_transient(env):
    env.store.fail_writes = True
    with pytest.raises(env.svc.TransientPaymentError):
        await env.svc.process_subscription_webhook_data(
            GOOD_HEADERS, charge_body(status="CANCELED"), env.bot,
        )


async def test_charge_in_progress_returns_transient_not_duplicate(env):
    env.store.charges["ch-1"] = "PROCESSING"
    with pytest.raises(env.svc.TransientPaymentError):
        await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    env.db.grant_access.assert_not_awaited()


async def test_webhook_timeout_does_not_lose_charge(env):
    release = asyncio.Event()
    finished = []

    async def slow_grant(**kwargs):
        await release.wait()
        finished.append(kwargs)
        return {"action": "renewal"}

    env.db.grant_access.side_effect = slow_grant

    # The route wraps processing in asyncio.wait_for(..., 25s) — emulate the timeout.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot),
            timeout=0.05,
        )
    release.set()
    for _ in range(20):
        await asyncio.sleep(0.01)

    assert finished, "grant must run to completion even if the webhook timed out"
    assert env.store.charges["ch-1"] == "CONFIRMED"

    retry = await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    assert retry["status"] == "duplicate"
    assert len(finished) == 1


# DB layer: errors must raise, never be reported as "duplicate".

class _BoomPool:
    def acquire(self):
        raise ConnectionError("pool broken")


@pytest.fixture
def boom_db(monkeypatch):
    import database.platega_subscriptions as m
    monkeypatch.setattr(m._core, "DB_READY", True)
    monkeypatch.setattr(m, "get_pool", AsyncMock(return_value=_BoomPool()))
    return m


async def test_db_record_charge_raises_on_db_error(boom_db):
    with pytest.raises(ConnectionError):
        await boom_db.record_charge("c", "s", 1, 100, "CANCELED")


@pytest.mark.parametrize("call", [
    lambda m: m.claim_charge("c", "s", 1, 100),
    lambda m: m.confirm_charge("c", "s", 100),
    lambda m: m.release_charge("c"),
    lambda m: m.create_subscription("s", 1, 100, 30, "basic"),
])
async def test_db_charge_writes_raise_on_db_error(boom_db, call):
    with pytest.raises(ConnectionError):
        await call(boom_db)


async def test_db_writes_raise_when_db_not_ready(monkeypatch):
    import database.platega_subscriptions as m
    monkeypatch.setattr(m._core, "DB_READY", False)
    with pytest.raises(RuntimeError):
        await m.record_charge("c", "s", 1, 100, "CANCELED")
    with pytest.raises(RuntimeError):
        await m.claim_charge("c", "s", 1, 100)
    with pytest.raises(RuntimeError):
        await m.create_subscription("s", 1, 100, 30, "basic")


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeConn:
    """Returns queued fetchval results in order; records executed SQL."""

    def __init__(self, fetchval_results):
        self._results = list(fetchval_results)
        self.executed = []

    async def fetchval(self, sql, *args):
        self.executed.append(sql)
        return self._results.pop(0)

    async def execute(self, sql, *args):
        self.executed.append(sql)

    def transaction(self):
        return _FakeTx()


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.mark.parametrize("results, expected", [
    (["c"], "claimed"),                          # fresh insert
    ([None, "c"], "reclaimed"),                  # stale PROCESSING row taken over
    ([None, None, "PROCESSING"], "in_progress"),  # someone is granting right now
    ([None, None, "CONFIRMED"], "done"),          # already granted
    ([None, None, "CANCELED"], "done"),
])
async def test_db_claim_charge_outcomes(monkeypatch, results, expected):
    import database.platega_subscriptions as m
    conn = _FakeConn(results)
    monkeypatch.setattr(m._core, "DB_READY", True)
    monkeypatch.setattr(m, "get_pool", AsyncMock(return_value=_FakePool(conn)))
    assert await m.claim_charge("c", "s", 1, 100) == expected


async def test_db_confirm_charge_bumps_counters_only_once(monkeypatch):
    import database.platega_subscriptions as m
    monkeypatch.setattr(m._core, "DB_READY", True)

    conn = _FakeConn(["s"])
    monkeypatch.setattr(m, "get_pool", AsyncMock(return_value=_FakePool(conn)))
    assert await m.confirm_charge("c", "s", 100) is True
    assert any("charges_success" in sql for sql in conn.executed)

    conn2 = _FakeConn([None])
    monkeypatch.setattr(m, "get_pool", AsyncMock(return_value=_FakePool(conn2)))
    assert await m.confirm_charge("c", "s", 100) is False
    assert not any("charges_success" in sql for sql in conn2.executed)


# ---------------------------------------------------------------------------
# Task 2 — subscription callbacks on the common callback URL
# ---------------------------------------------------------------------------

async def test_common_url_subscription_charge_is_processed(env):
    result = await env.svc.process_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    assert result["event"] == "charge_confirmed"
    env.db.grant_access.assert_awaited_once()
    env.confirmed.assert_not_awaited()


async def test_common_url_subscription_status_event_is_processed(env):
    result = await env.svc.process_webhook_data(GOOD_HEADERS, status_body(), env.bot)
    assert result["event"] == "status"
    assert result["new_status"] == "Active"
    env.store.update_subscription_status.assert_awaited()


async def test_common_url_subscription_callback_requires_auth(env):
    result = await env.svc.process_webhook_data(BAD_HEADERS, charge_body(), env.bot)
    assert result["status"] == "unauthorized"
    env.db.grant_access.assert_not_awaited()


async def test_separate_subscription_route_still_works(env):
    result = await env.svc.process_subscription_webhook_data(GOOD_HEADERS, status_body(), env.bot)
    assert result["event"] == "status"
    bad = await env.svc.process_subscription_webhook_data(BAD_HEADERS, status_body(), env.bot)
    assert bad["status"] == "unauthorized"


async def test_common_url_one_off_callback_uses_one_off_path(env):
    result = await env.svc.process_webhook_data(GOOD_HEADERS, one_off_body(amount=199), env.bot)
    assert result["status"] == "ok"
    env.confirmed.assert_awaited_once()
    env.db.grant_access.assert_not_awaited()


# ---------------------------------------------------------------------------
# Task 3 — amount / currency of a CONFIRMED one-off callback
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
# Task 4 — CHARGEBACKED
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
# Task 5 — orphan charges / mandatory subscription persistence
# ---------------------------------------------------------------------------

async def test_orphan_charge_alerts_admin_and_returns_ok(env):
    env.store.sub_row = None
    result = await env.svc.process_subscription_webhook_data(GOOD_HEADERS, charge_body(), env.bot)
    assert result["status"] == "ok"
    assert result["event"] == "orphan_charge"
    alerts = forced_alerts(env)
    assert alerts and SUB_ID in alerts[0].message and "ch-1" in alerts[0].message
    env.db.grant_access.assert_not_awaited()


async def test_orphan_charge_without_telegram_id_alerts_admin(env):
    env.store.sub_row = None
    result = await env.svc.process_subscription_webhook_data(
        GOOD_HEADERS, charge_body(payload=False), env.bot,
    )
    assert result["status"] != "ok" or result.get("event") == "orphan_charge"
    alerts = forced_alerts(env)
    assert alerts and SUB_ID in alerts[0].message


async def _create_sub(env, **kwargs):
    return await env.svc.create_subscription(
        amount_rubles=199.0,
        interval=env.svc.SUBSCRIPTION_INTERVAL_MONTH,
        description="Atlas Secure VPN — test",
        telegram_id=TG,
        tariff_type="basic",
        period_days=30,
        **kwargs,
    )


async def test_create_subscription_db_failure_withholds_link_and_alerts(env):
    sent = []
    env.monkeypatch.setattr(env.svc.httpx, "AsyncClient", _fake_httpx_client(sent))
    env.store.create_subscription.side_effect = ConnectionError("db down")

    result = None
    with pytest.raises(RuntimeError):
        result = await _create_sub(env, bot=env.bot)
    assert result is None  # no redirect link handed out
    alerts = forced_alerts(env)
    assert alerts and SUB_ID in alerts[0].message


async def test_create_subscription_db_failure_alerts_via_webhook_bot(env):
    import app.api.payment_webhook as pw
    sent = []
    env.monkeypatch.setattr(env.svc.httpx, "AsyncClient", _fake_httpx_client(sent))
    env.monkeypatch.setattr(pw, "_bot", env.bot)
    env.store.create_subscription.side_effect = ConnectionError("db down")

    with pytest.raises(RuntimeError):
        await _create_sub(env)
    assert forced_alerts(env)


async def test_create_subscription_persists_and_returns_link(env):
    sent = []
    env.monkeypatch.setattr(env.svc.httpx, "AsyncClient", _fake_httpx_client(sent))
    result = await _create_sub(env, bot=env.bot)
    assert result["subscription_id"] == SUB_ID
    env.store.create_subscription.assert_awaited_once()


# ---------------------------------------------------------------------------
# Task 6 — intervalCount + honest user texts
# ---------------------------------------------------------------------------

async def test_create_subscription_sends_interval_count_1(env):
    sent = []
    env.monkeypatch.setattr(env.svc.httpx, "AsyncClient", _fake_httpx_client(sent))
    await _create_sub(env, bot=env.bot)
    details = sent[0]["paymentDetails"]
    assert details["interval"] == env.svc.SUBSCRIPTION_INTERVAL_MONTH
    assert details["intervalCount"] == 1


def sent_text(env) -> str:
    """Текст последнего сообщения юзеру.

    Сообщения идут через safe_send_message (правило проекта), а он передаёт
    текст ПОЗИЦИОННЫМ аргументом — поэтому kwargs["text"] здесь пусто.
    """
    call = env.bot.send_message.await_args
    return (call.kwargs.get("text") or call.args[1]).lower()


async def test_past_due_notification_does_not_promise_retry(env):
    await env.svc.process_subscription_webhook_data(
        GOOD_HEADERS, status_body("SUBSCRIPTION_PAST_DUE"), env.bot,
    )
    text = sent_text(env)
    assert "повторим" not in text
    assert "поддержк" in text


async def test_canceled_charge_notification_does_not_promise_retry(env):
    result = await env.svc.process_subscription_webhook_data(
        GOOD_HEADERS, charge_body(status="CANCELED"), env.bot,
    )
    assert result["event"] == "charge_canceled"
    text = sent_text(env)
    assert "попробуем" not in text
    assert "повторить через" not in text
    assert "поддержк" in text


# ---------------------------------------------------------------------------
# Task 7 — _verify_auth
# ---------------------------------------------------------------------------

def test_verify_auth(env):
    svc = env.svc
    assert svc._verify_auth(GOOD_HEADERS) is True
    assert svc._verify_auth({"X-MerchantId": MERCHANT, "X-Secret": SECRET}) is True
    assert svc._verify_auth(BAD_HEADERS) is False
    assert svc._verify_auth({}) is False
    env.monkeypatch.setattr(svc, "PLATEGA_SECRET", "")
    assert svc._verify_auth({"x-merchantid": MERCHANT, "x-secret": ""}) is False


# ---------------------------------------------------------------------------
# Все периоды тарифа + отмена подписки (PLATEGA_API.md §2.4, §6.4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("period_days, interval, count", [
    (30, 3, 1),     # месяц × 1
    (90, 3, 3),     # месяц × 3
    (180, 3, 6),    # месяц × 6
    (365, 4, 1),    # год (не 12 × 30 дней — тариф продаётся как 365)
    (730, 4, 2),    # 2 года (combo)
])
def test_every_sold_period_maps_to_a_plan(env, period_days, interval, count):
    assert env.svc.subscription_plan_for(period_days) == (interval, count)


@pytest.mark.parametrize("period", [7, 45, 1000, None, "x"])
def test_unsupported_period_has_no_plan(env, period):
    """Хендлер по None уводит юзера на разовую оплату, а не в тупик."""
    assert env.svc.subscription_plan_for(period) is None


async def test_quarterly_subscription_sends_three_months(env):
    sent = []
    env.monkeypatch.setattr(env.svc.httpx, "AsyncClient", _fake_httpx_client(sent))
    interval, count = env.svc.subscription_plan_for(90)

    await env.svc.create_subscription(
        amount_rubles=499.0, interval=interval, interval_count=count,
        description="Atlas Secure VPN — 3 месяца", telegram_id=TG,
        tariff_type="basic", period_days=90, bot=env.bot,
    )

    details = sent[0]["paymentDetails"]
    assert (details["interval"], details["intervalCount"]) == (3, 3)


async def test_interval_count_above_the_provider_limit_is_refused(env):
    """Доку §2.4: месяц ≤ 12, год ≤ 3. Ловим до похода в Platega —
    иначе юзер ждёт редиректа и получает 400."""
    with pytest.raises(ValueError):
        await _create_sub(env, interval_count=13)


async def test_cancel_subscription_calls_the_provider(env):
    calls = []

    class _Resp:
        status_code = 200
        text = '{"status":"cancelled"}'

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls.append(url)
            return _Resp()

    env.monkeypatch.setattr(env.svc.httpx, "AsyncClient", lambda *a, **k: _Client())

    assert await env.svc.cancel_subscription(SUB_ID) is True
    assert calls == [f"{env.svc.PLATEGA_API_URL}/subscription/{SUB_ID}/cancel"]


async def test_cancel_subscription_reports_failure(env):
    class _Resp:
        status_code = 500
        text = "boom"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            return _Resp()

    env.monkeypatch.setattr(env.svc.httpx, "AsyncClient", lambda *a, **k: _Client())

    assert await env.svc.cancel_subscription(SUB_ID) is False
