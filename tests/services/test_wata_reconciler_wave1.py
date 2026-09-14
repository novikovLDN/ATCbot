"""
Wave 1 — WATA reconciler + provider tagging on VPN invoices.

Covers:
  * update_pending_purchase_invoice_id(..., provider=...) writes payment_provider
    (backward compatible without the kwarg);
  * reconciler scans only WATA / NULL-provider rows and never expires a row
    whose provider is not known to be WATA;
  * paid-detection via the documented GET /api/h2h/v2/transactions/?orderId=
    (429 = unknown, never "not paid"), with amount/currency verification
    for VPN purchases (shop keeps the old behaviour);
  * VPN call sites pass provider=... and Platega telegram_id (AST guard).
"""
import ast
import pathlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers import wata_reconciler as wr

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── helpers ─────────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, fetch_rows=None, execute_result="UPDATE 1"):
        self.fetch_rows = fetch_rows or []
        self.execute_result = execute_result
        self.fetch_calls = []
        self.execute_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self.fetch_rows

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return self.execute_result


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def _acq(self):
        yield self.conn

    def acquire(self):
        return self._acq()


class _FakeResponse:
    def __init__(self, status_code, payload=None, json_exc=None):
        self.status_code = status_code
        self._payload = payload
        self._json_exc = json_exc
        self.text = ""

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._payload


def _fake_client_factory(response=None, exc=None, calls=None):
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, headers=None, params=None):
            if calls is not None:
                calls.append({"url": url, "params": params, "headers": headers})
            if exc:
                raise exc
            return response

    return _Client


def _row(**over):
    base = {
        "purchase_id": "purchase_abc",
        "telegram_id": 42,
        "invoice_id": "link-uuid-1",
        "price_kopecks": 19900,
        "payment_provider": "wata",
        "purchase_type": "subscription",
        "tariff": "basic",
    }
    base.update(over)
    return base


def _paid_tx(order_id="purchase_abc", amount=199.0, currency="RUB", tx_id="tx-1"):
    return {
        "id": tx_id,
        "orderId": order_id,
        "status": "Paid",
        "kind": "Payment",
        "amount": amount,
        "currency": currency,
    }


@pytest.fixture(autouse=True)
def _reset_alert_dedupe():
    wr._MISMATCH_ALERTED.clear()
    wr._LAST_LOOKUP_AT.clear()
    yield
    wr._MISMATCH_ALERTED.clear()
    wr._LAST_LOOKUP_AT.clear()


@pytest.fixture
def wata_enabled():
    import wata_service
    with patch.object(wata_service, "is_enabled", return_value=True), \
         patch.object(wata_service, "WATA_ACCESS_TOKEN", "tok"):
        yield wata_service


# ── Task 1: update_pending_purchase_invoice_id(provider=...) ─────────────

@pytest.mark.asyncio
async def test_update_invoice_id_sets_payment_provider_when_given():
    import database.subscriptions as subs
    conn = _FakeConn()
    with patch.object(subs, "get_pool", AsyncMock(return_value=_FakePool(conn))):
        ok = await subs.update_pending_purchase_invoice_id("p1", "inv1", provider="wata")
    assert ok is True
    sql, args = conn.execute_calls[0]
    assert "payment_provider" in sql
    assert "inv1" in args and "p1" in args and "wata" in args
    assert "status = 'pending'" in sql


@pytest.mark.asyncio
async def test_update_invoice_id_backward_compatible_without_provider():
    import database.subscriptions as subs
    conn = _FakeConn()
    with patch.object(subs, "get_pool", AsyncMock(return_value=_FakePool(conn))):
        ok = await subs.update_pending_purchase_invoice_id("p1", "inv1")
    assert ok is True
    sql, args = conn.execute_calls[0]
    assert "payment_provider" not in sql
    assert len(args) == 3


@pytest.mark.asyncio
async def test_update_invoice_id_returns_false_when_not_pending():
    import database.subscriptions as subs
    conn = _FakeConn(execute_result="UPDATE 0")
    with patch.object(subs, "get_pool", AsyncMock(return_value=_FakePool(conn))):
        ok = await subs.update_pending_purchase_invoice_id("p1", "inv1", provider="platega")
    assert ok is False


# ── Task 4: documented transaction lookup ────────────────────────────────

@pytest.mark.asyncio
async def test_lookup_uses_documented_v2_transactions_endpoint(wata_enabled):
    calls = []
    resp = _FakeResponse(200, {"items": [_paid_tx()], "totalCount": 1})
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(resp, calls=calls)):
        outcome, tx = await wr.lookup_paid_transaction("purchase_abc")
    assert outcome == wr.LOOKUP_PAID
    assert tx["id"] == "tx-1"
    assert calls[0]["url"].endswith("/api/h2h/v2/transactions/")
    assert calls[0]["params"]["orderId"] == "purchase_abc"


@pytest.mark.asyncio
async def test_lookup_429_is_rate_limited_not_unpaid(wata_enabled):
    resp = _FakeResponse(429, None)
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(resp)):
        outcome, tx = await wr.lookup_paid_transaction("purchase_abc")
    assert outcome == wr.LOOKUP_RATE_LIMITED
    assert tx is None


@pytest.mark.asyncio
async def test_lookup_network_error_is_unknown(wata_enabled):
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(exc=OSError("boom"))):
        outcome, _ = await wr.lookup_paid_transaction("purchase_abc")
    assert outcome == wr.LOOKUP_UNKNOWN


@pytest.mark.asyncio
async def test_lookup_5xx_and_bad_json_are_unknown(wata_enabled):
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(_FakeResponse(502))):
        assert (await wr.lookup_paid_transaction("p-502"))[0] == wr.LOOKUP_UNKNOWN
    bad = _FakeResponse(200, json_exc=ValueError("no json"))
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(bad)):
        assert (await wr.lookup_paid_transaction("p-badjson"))[0] == wr.LOOKUP_UNKNOWN


@pytest.mark.asyncio
async def test_lookup_local_throttle_30s_per_order(wata_enabled):
    calls = []
    resp = _FakeResponse(200, {"items": []})
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(resp, calls=calls)):
        first = await wr.lookup_paid_transaction("purchase_abc")
        second = await wr.lookup_paid_transaction("purchase_abc")
    assert first[0] == wr.LOOKUP_NOT_PAID
    assert second[0] == wr.LOOKUP_UNKNOWN  # retry later, never "not paid"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lookup_ignores_paid_tx_of_other_order(wata_enabled):
    resp = _FakeResponse(200, {"items": [_paid_tx(order_id="someone_else")]})
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(resp)):
        outcome, tx = await wr.lookup_paid_transaction("purchase_abc")
    assert outcome == wr.LOOKUP_NOT_PAID
    assert tx is None


@pytest.mark.asyncio
async def test_lookup_only_pending_or_declined_is_not_paid(wata_enabled):
    items = [
        {**_paid_tx(), "status": "Declined"},
        {**_paid_tx(), "status": "Pending"},
    ]
    with patch.object(wr.httpx, "AsyncClient", _fake_client_factory(_FakeResponse(200, {"items": items}))):
        outcome, _ = await wr.lookup_paid_transaction("purchase_abc")
    assert outcome == wr.LOOKUP_NOT_PAID


def test_find_paid_transaction_ignores_refund_and_link_object():
    # Link object per docs: status Opened|Closed — never "paid".
    assert wr._find_paid_transaction({"id": "l", "status": "Closed", "orderId": "p"}) is None
    refund = {**_paid_tx(), "kind": "Refund"}
    assert wr._find_paid_transaction({"items": [refund]}, order_id="purchase_abc") is None
    # Legacy shape (transactions[] inside link) still understood.
    assert wr._find_paid_transaction({"transactions": [_paid_tx()]})["id"] == "tx-1"


# ── verification: VPN strict, shop legacy ───────────────────────────────

@pytest.mark.asyncio
async def test_vpn_paid_matching_amount_finalizes_with_expected_amount():
    finalize = AsyncMock(return_value={"status": "ok"})
    with patch.object(wr, "lookup_paid_transaction",
                      AsyncMock(return_value=(wr.LOOKUP_PAID, _paid_tx(amount=199.4)))), \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize):
        outcome = await wr._check_and_finalize(object(), _row())
    assert outcome == "finalized"
    kw = finalize.await_args.kwargs
    assert kw["provider"] == "wata"
    assert kw["purchase_id"] == "purchase_abc"
    assert kw["amount_rubles"] == pytest.approx(199.0)
    assert kw["invoice_id"] == "tx-1"
    assert kw["telegram_id"] == 42


@pytest.mark.asyncio
async def test_vpn_amount_mismatch_blocks_finalize_and_alerts_once():
    finalize = AsyncMock()
    alert = AsyncMock(return_value=True)
    with patch.object(wr, "lookup_paid_transaction",
                      AsyncMock(return_value=(wr.LOOKUP_PAID, _paid_tx(amount=10.0)))), \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize), \
         patch("app.services.admin_alerts.send_alert", alert):
        first = await wr._check_and_finalize(object(), _row())
        second = await wr._check_and_finalize(object(), _row())
    assert first == second == "mismatch"
    finalize.assert_not_awaited()
    assert alert.await_count == 1


@pytest.mark.asyncio
async def test_vpn_currency_mismatch_blocks_finalize():
    finalize = AsyncMock()
    with patch.object(wr, "lookup_paid_transaction",
                      AsyncMock(return_value=(wr.LOOKUP_PAID, _paid_tx(currency="USD")))), \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize), \
         patch("app.services.admin_alerts.send_alert", AsyncMock()):
        outcome = await wr._check_and_finalize(object(), _row())
    assert outcome == "mismatch"
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_shop_purchase_keeps_old_behaviour_no_amount_check():
    finalize = AsyncMock(return_value={"status": "ok"})
    row = _row(payment_provider=None, purchase_type="spotify", tariff="spotify_1m")
    with patch.object(wr, "lookup_paid_transaction",
                      AsyncMock(return_value=(wr.LOOKUP_PAID, _paid_tx(amount=10.0, currency=None)))), \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize):
        outcome = await wr._check_and_finalize(object(), row)
    assert outcome == "finalized"
    assert finalize.await_args.kwargs["amount_rubles"] == pytest.approx(10.0)


def test_is_shop_purchase_classification():
    assert wr._is_shop_purchase({"purchase_type": "telegram_premium"})
    assert wr._is_shop_purchase({"purchase_type": "apple_id", "tariff": "apple_id_usa_10"})
    assert wr._is_shop_purchase({"purchase_type": None, "tariff": "steam_500"})
    assert not wr._is_shop_purchase({"purchase_type": "subscription", "tariff": "plus"})
    assert not wr._is_shop_purchase({"purchase_type": "proxy", "tariff": "proxy"})
    assert not wr._is_shop_purchase({"purchase_type": "balance_topup"})


# ── Task 3: provider-scoped scan / expiry rule ───────────────────────────

@pytest.mark.asyncio
async def test_scan_sql_is_scoped_to_wata_or_null_provider():
    conn = _FakeConn(fetch_rows=[])
    import database
    with patch.object(database, "get_pool", AsyncMock(return_value=_FakePool(conn))):
        await wr._reconcile_iteration(object())
    sql, _ = conn.fetch_calls[0]
    norm = " ".join(sql.split())
    assert "payment_provider = 'wata'" in norm
    assert "payment_provider IS NULL" in norm


@pytest.mark.asyncio
async def test_non_wata_provider_row_is_never_touched():
    lookup = AsyncMock()
    link = AsyncMock()
    import wata_service
    with patch.object(wr, "lookup_paid_transaction", lookup), \
         patch.object(wata_service, "check_link_status", link):
        outcome = await wr._check_and_finalize(object(), _row(payment_provider="platega"))
    assert outcome == "not_wata"
    lookup.assert_not_awaited()
    link.assert_not_awaited()


@pytest.mark.asyncio
async def test_wata_row_unpaid_with_404_link_is_expired():
    conn = _FakeConn()
    import database
    import wata_service
    with patch.object(wr, "lookup_paid_transaction", AsyncMock(return_value=(wr.LOOKUP_NOT_PAID, None))), \
         patch.object(wata_service, "check_link_status",
                      AsyncMock(return_value={"_http": 404, "_link_id": "link-uuid-1"})), \
         patch.object(database, "get_pool", AsyncMock(return_value=_FakePool(conn))):
        outcome = await wr._check_and_finalize(object(), _row(payment_provider="wata"))
    assert outcome == "expired_stale"
    assert any("expired" in sql for sql, _ in conn.execute_calls)


@pytest.mark.asyncio
async def test_null_provider_row_is_never_expired_on_404():
    conn = _FakeConn()
    link = AsyncMock(return_value={"_http": 404, "_link_id": "x"})
    import database
    import wata_service
    with patch.object(wr, "lookup_paid_transaction", AsyncMock(return_value=(wr.LOOKUP_NOT_PAID, None))), \
         patch.object(wata_service, "check_link_status", link), \
         patch.object(database, "get_pool", AsyncMock(return_value=_FakePool(conn))):
        outcome = await wr._check_and_finalize(object(), _row(payment_provider=None))
    assert outcome == "not_paid"
    link.assert_not_awaited()
    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_null_provider_row_still_finalized_when_wata_has_paid_tx():
    """Shop/legacy WATA rows (payment_provider NULL) must keep being recovered."""
    finalize = AsyncMock(return_value={"status": "ok"})
    row = _row(payment_provider=None, purchase_type="apple_id", tariff="apple_id_usa_10")
    with patch.object(wr, "lookup_paid_transaction", AsyncMock(return_value=(wr.LOOKUP_PAID, _paid_tx()))), \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize):
        outcome = await wr._check_and_finalize(object(), row)
    assert outcome == "finalized"


@pytest.mark.asyncio
async def test_unknown_lookup_never_expires_even_wata_row():
    link = AsyncMock(return_value={"_http": 404})
    import wata_service
    with patch.object(wr, "lookup_paid_transaction", AsyncMock(return_value=(wr.LOOKUP_UNKNOWN, None))), \
         patch.object(wata_service, "check_link_status", link):
        outcome = await wr._check_and_finalize(object(), _row())
    assert outcome == "unknown"
    link.assert_not_awaited()


@pytest.mark.asyncio
async def test_iteration_stops_batch_on_rate_limit():
    rows = [_row(purchase_id="a"), _row(purchase_id="b"), _row(purchase_id="c")]
    conn = _FakeConn(fetch_rows=rows)
    lookup = AsyncMock(return_value=(wr.LOOKUP_RATE_LIMITED, None))
    import database
    with patch.object(database, "get_pool", AsyncMock(return_value=_FakePool(conn))), \
         patch.object(wr, "lookup_paid_transaction", lookup), \
         patch.object(wr.asyncio, "sleep", AsyncMock()):
        await wr._reconcile_iteration(object())
    assert lookup.await_count == 1


# ── fast poll reuses the documented lookup ───────────────────────────────

@pytest.mark.asyncio
async def test_fast_poll_finalizes_via_order_lookup():
    from app.handlers.callbacks import payments_callbacks as pc
    finalize = AsyncMock(return_value={"status": "ok"})
    pending = {**_row(), "status": "pending", "provider_invoice_id": "link-uuid-1"}
    with patch.object(pc.database, "get_pending_purchase_any_status", AsyncMock(return_value=pending)), \
         patch.object(wr, "lookup_paid_transaction",
                      AsyncMock(return_value=(wr.LOOKUP_PAID, _paid_tx()))) as lookup, \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize), \
         patch.object(pc.asyncio, "sleep", AsyncMock()):
        await pc._poll_wata_invoice(
            object(), telegram_id=42, purchase_id="purchase_abc", invoice_id="link-uuid-1",
        )
    lookup.assert_awaited_with("purchase_abc")
    finalize.assert_awaited_once()
    assert finalize.await_args.kwargs["amount_rubles"] == pytest.approx(199.0)


@pytest.mark.asyncio
async def test_fast_poll_does_not_finalize_on_vpn_mismatch():
    from app.handlers.callbacks import payments_callbacks as pc
    finalize = AsyncMock()
    pending = {**_row(), "status": "pending"}
    with patch.object(pc.database, "get_pending_purchase_any_status", AsyncMock(return_value=pending)), \
         patch.object(wr, "lookup_paid_transaction",
                      AsyncMock(return_value=(wr.LOOKUP_PAID, _paid_tx(amount=1.0)))), \
         patch("app.services.payments.confirmation.process_confirmed_payment", finalize), \
         patch("app.services.admin_alerts.send_alert", AsyncMock()), \
         patch.object(pc, "_WATA_POLL_MAX_ATTEMPTS", 2), \
         patch.object(pc.asyncio, "sleep", AsyncMock()):
        await pc._poll_wata_invoice(
            object(), telegram_id=42, purchase_id="purchase_abc", invoice_id="link-uuid-1",
        )
    finalize.assert_not_awaited()


# ── Tasks 2 & 5: VPN call sites (static guard) ───────────────────────────

_VPN_FILES = [
    "app/handlers/proxy.py",
    "app/handlers/game.py",
    "app/handlers/traffic.py",
    "app/handlers/callbacks/payments_callbacks.py",
    "app/handlers/callbacks/gift.py",
]


def _calls(path, attr):
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == attr:
            yield node


@pytest.mark.parametrize("path", _VPN_FILES)
def test_vpn_invoice_saves_pass_provider(path):
    allowed = {"wata", "platega", "cryptobot"}
    found = list(_calls(path, "update_pending_purchase_invoice_id"))
    assert found, path
    for call in found:
        kw = {k.arg: k.value for k in call.keywords}
        assert "provider" in kw, f"{path}:{call.lineno} missing provider="
        assert isinstance(kw["provider"], ast.Constant) and kw["provider"].value in allowed, \
            f"{path}:{call.lineno}"


@pytest.mark.parametrize("path", _VPN_FILES)
def test_vpn_platega_transactions_pass_telegram_id(path):
    for call in _calls(path, "create_transaction"):
        kw = {k.arg for k in call.keywords}
        assert "telegram_id" in kw, f"{path}:{call.lineno} missing telegram_id="
