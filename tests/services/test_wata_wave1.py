"""WATA wave-1 hardening tests (audit 2026-09, P0-B + §3a).

Covers:
  1. Fail-closed webhook signature (pinned PEM, lazy fetch under lock,
     re-fetch once on mismatch, TransientPaymentError → HTTP 500).
  2. VPN purchases: missing/zero amount or non-RUB currency → reject +
     forced admin alert. Shop purchases keep the old behaviour.
  3. kind == "Refund" → forced admin alert, no revocation.
  4. HTTP 401 from WATA API → admin alert with cooldown.
  5. Payment link default expiry = 30 min (pending TTL).
  6. Declined → non-forced (cooldown) admin alert.
"""
import asyncio
import base64
import json
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import database
import wata_service
from app.services import admin_alerts
from app.services.payments import confirmation
from app.services.payments.confirmation import TransientPaymentError


# ── Key material ────────────────────────────────────────────────────

def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(priv, fmt=serialization.PublicFormat.SubjectPublicKeyInfo) -> str:
    return priv.public_key().public_bytes(
        serialization.Encoding.PEM, fmt,
    ).decode("ascii")


def _sign(priv, raw: bytes) -> str:
    return base64.b64encode(
        priv.sign(raw, padding.PKCS1v15(), hashes.SHA512()),
    ).decode("ascii")


KEY_A = _new_key()
KEY_B = _new_key()
PEM_A = _pem(KEY_A)
PEM_B = _pem(KEY_B)


def _signed(body: dict, priv=KEY_A):
    raw = json.dumps(body).encode("utf-8")
    return {"x-signature": _sign(priv, raw)}, raw, body


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_wata(monkeypatch):
    monkeypatch.setattr(wata_service, "WATA_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(wata_service, "_PINNED_PUBLIC_KEY_PEM", "")
    monkeypatch.setattr(wata_service, "_PUBLIC_KEY_PEM", None)
    monkeypatch.setattr(wata_service, "_PUBLIC_KEY_FETCHED_AT", None)
    monkeypatch.setattr(wata_service, "_KEY_LOCK", None)
    monkeypatch.setattr(wata_service, "_LAST_AUTH_ALERT_AT", None)
    monkeypatch.setattr(database, "DB_READY", True)
    yield


@pytest.fixture
def alerts(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", mock)
    return mock


@pytest.fixture
def fetch(monkeypatch):
    """Replace the network fetch of /public-key; returns the mock."""
    mock = AsyncMock(return_value=PEM_A)
    monkeypatch.setattr(wata_service, "_fetch_public_key_pem", mock)
    return mock


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(wata_service, "_PINNED_PUBLIC_KEY_PEM", PEM_A)


@pytest.fixture
def confirm(monkeypatch):
    mock = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(confirmation, "process_confirmed_payment", mock)
    return mock


def _lookup(monkeypatch, purchase_type="subscription", tariff="basic", price_kopecks=19900):
    purchase = {
        "purchase_id": "p1", "telegram_id": 42, "status": "pending",
        "purchase_type": purchase_type, "tariff": tariff,
        "price_kopecks": price_kopecks,
    }
    monkeypatch.setattr(
        confirmation, "lookup_pending_purchase",
        AsyncMock(return_value={"status": "ok", "purchase": purchase, "telegram_id": 42}),
    )
    return purchase


def _paid_body(**over):
    body = {
        "transactionId": "tx-1", "transactionStatus": "Paid", "kind": "Payment",
        "orderId": "p1", "amount": 199.0, "currency": "RUB",
    }
    body.update(over)
    return {k: v for k, v in body.items() if v is not None}


# ── 1. Fail-closed signature ───────────────────────────────────────

class TestPemNormalization:
    def test_escaped_newlines_are_unescaped(self):
        escaped = PEM_A.strip().replace("\n", "\\n")
        assert wata_service._normalize_pem(escaped) == PEM_A.strip()

    def test_surrounding_quotes_and_whitespace_stripped(self):
        assert wata_service._normalize_pem(f'  "{PEM_A.strip()}"  ') == PEM_A.strip()

    def test_empty(self):
        assert wata_service._normalize_pem("") == ""
        assert wata_service._normalize_pem(None) == ""


class TestSignature:
    async def test_pinned_key_verifies_without_network(self, pinned, fetch):
        headers, raw, _ = _signed({"a": 1})
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is True
        fetch.assert_not_called()

    async def test_pkcs1_pem_is_accepted(self, monkeypatch, fetch):
        monkeypatch.setattr(
            wata_service, "_PINNED_PUBLIC_KEY_PEM",
            _pem(KEY_A, serialization.PublicFormat.PKCS1),
        )
        headers, raw, _ = _signed({"a": 1})
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is True

    async def test_lazy_fetch_and_cache(self, fetch):
        headers, raw, _ = _signed({"a": 1})
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is True
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is True
        assert fetch.await_count == 1

    async def test_no_key_raises_transient(self, fetch):
        fetch.return_value = None
        headers, raw, _ = _signed({"a": 1})
        with pytest.raises(TransientPaymentError):
            await wata_service._verify_webhook_signature(raw, headers["x-signature"])

    async def test_fetch_failure_is_not_cached(self, fetch):
        fetch.side_effect = [None, PEM_A]
        headers, raw, _ = _signed({"a": 1})
        with pytest.raises(TransientPaymentError):
            await wata_service._verify_webhook_signature(raw, headers["x-signature"])
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is True
        assert fetch.await_count == 2

    async def test_malformed_fetched_key_is_not_cached(self, fetch):
        fetch.side_effect = ["not a pem", PEM_A]
        assert await wata_service._get_public_key() is None
        assert await wata_service._get_public_key() == PEM_A

    async def test_cryptography_unavailable_raises_transient(self, pinned, monkeypatch):
        headers, raw, _ = _signed({"a": 1})
        monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives", None)
        with pytest.raises(TransientPaymentError):
            await wata_service._verify_webhook_signature(raw, headers["x-signature"])

    async def test_concurrent_fetch_single_request(self, fetch):
        async def slow():
            await asyncio.sleep(0.05)
            return PEM_A
        fetch.side_effect = slow
        results = await asyncio.gather(*[wata_service._get_public_key() for _ in range(8)])
        assert all(r == PEM_A for r in results)
        assert fetch.await_count == 1

    async def test_mismatch_refetches_once_and_accepts_rotated_key(self, fetch, monkeypatch):
        monkeypatch.setattr(wata_service, "_PUBLIC_KEY_PEM", PEM_A)
        monkeypatch.setattr(wata_service, "_PUBLIC_KEY_FETCHED_AT", 0.0)
        fetch.return_value = PEM_B
        headers, raw, _ = _signed({"a": 1}, priv=KEY_B)
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is True
        assert fetch.await_count == 1
        assert wata_service._PUBLIC_KEY_PEM == PEM_B

    async def test_mismatch_after_refetch_rejected(self, fetch, monkeypatch):
        monkeypatch.setattr(wata_service, "_PUBLIC_KEY_PEM", PEM_A)
        monkeypatch.setattr(wata_service, "_PUBLIC_KEY_FETCHED_AT", 0.0)
        headers, raw, _ = _signed({"a": 1}, priv=KEY_B)
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is False
        assert fetch.await_count == 1

    async def test_mismatch_refetch_rate_limited(self, fetch, monkeypatch):
        """A second forged request right after a refetch must not hit WATA again."""
        monkeypatch.setattr(wata_service, "_PUBLIC_KEY_PEM", PEM_A)
        monkeypatch.setattr(wata_service, "_PUBLIC_KEY_FETCHED_AT", 0.0)
        headers, raw, _ = _signed({"a": 1}, priv=KEY_B)
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is False
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is False
        assert fetch.await_count == 1

    async def test_pinned_key_mismatch_no_refetch(self, pinned, fetch):
        headers, raw, _ = _signed({"a": 1}, priv=KEY_B)
        assert await wata_service._verify_webhook_signature(raw, headers["x-signature"]) is False
        fetch.assert_not_called()

    async def test_tampered_body_rejected(self, pinned):
        headers, raw, _ = _signed({"amount": 100})
        assert await wata_service._verify_webhook_signature(
            raw.replace(b"100", b"999"), headers["x-signature"],
        ) is False

    async def test_missing_signature_rejected(self, pinned):
        assert await wata_service._verify_webhook_signature(b"{}", "") is False


class TestWebhookSignatureOutcome:
    async def test_invalid_signature_raises_transient_subclass(self, pinned, alerts, confirm):
        headers, raw, body = _signed(_paid_body(), priv=KEY_B)
        with pytest.raises(TransientPaymentError) as ei:
            await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert isinstance(ei.value, wata_service.WataSignatureError)
        confirm.assert_not_called()

    async def test_unsigned_request_never_accepted_without_key(self, fetch, alerts, confirm):
        fetch.return_value = None
        _, raw, body = _signed(_paid_body())
        with pytest.raises(TransientPaymentError):
            await wata_service.process_webhook_data({}, raw, body, MagicMock())
        confirm.assert_not_called()

    async def test_no_key_raises_transient_from_process(self, fetch, alerts, confirm):
        fetch.return_value = None
        headers, raw, body = _signed(_paid_body())
        with pytest.raises(TransientPaymentError):
            await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        confirm.assert_not_called()

    def test_route_returns_500_on_invalid_signature(self, pinned, alerts, confirm, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import payment_webhook

        monkeypatch.setattr(payment_webhook, "_bot", MagicMock())
        monkeypatch.setattr(database, "log_payment_error", AsyncMock(), raising=False)
        app = FastAPI()
        app.include_router(payment_webhook.router)
        headers, raw, _ = _signed(_paid_body(), priv=KEY_B)
        resp = TestClient(app).post(
            "/webhooks/wata", content=raw,
            headers={"X-Signature": headers["x-signature"], "Content-Type": "application/json"},
        )
        assert resp.status_code == 500
        confirm.assert_not_called()


class TestWarmup:
    async def test_warmup_fetches_and_caches(self, fetch):
        assert await wata_service.warmup_public_key() is True
        assert wata_service._PUBLIC_KEY_PEM == PEM_A

    async def test_warmup_failure_does_not_raise(self, fetch):
        fetch.side_effect = RuntimeError("boom")
        assert await wata_service.warmup_public_key() is False

    async def test_warmup_pinned(self, pinned, fetch):
        assert await wata_service.warmup_public_key() is True
        fetch.assert_not_called()


# ── 2. Amount / currency (VPN only) ────────────────────────────────

class TestAmountCurrency:
    @pytest.mark.parametrize("amount", [None, 0, "0", "abc"])
    async def test_vpn_missing_or_zero_amount_rejected(self, pinned, alerts, confirm, monkeypatch, amount):
        _lookup(monkeypatch)
        headers, raw, body = _signed(_paid_body(amount=amount) if amount is not None
                                     else {k: v for k, v in _paid_body().items() if k != "amount"})
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result["status"] == "invalid_amount"
        confirm.assert_not_called()
        assert alerts.await_count == 1
        assert alerts.await_args.kwargs.get("force") is True

    @pytest.mark.parametrize("currency", ["USD", "EUR", None])
    async def test_vpn_non_rub_currency_rejected(self, pinned, alerts, confirm, monkeypatch, currency):
        _lookup(monkeypatch)
        headers, raw, body = _signed(_paid_body(currency=currency))
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result["status"] == "invalid_currency"
        confirm.assert_not_called()
        assert alerts.await_args.kwargs.get("force") is True

    async def test_vpn_valid_rub_passes_webhook_amount(self, pinned, alerts, confirm, monkeypatch):
        _lookup(monkeypatch)
        headers, raw, body = _signed(_paid_body(amount=199.0, currency="rub"))
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result == {"status": "ok"}
        assert confirm.await_args.kwargs["amount_rubles"] == 199.0
        assert confirm.await_args.kwargs["invoice_id"] == "tx-1"

    @pytest.mark.parametrize("purchase_type,tariff", [
        ("telegram_premium", "premium_3"),
        ("telegram_stars", "stars_50"),
        ("steam", "steam_500"),
        ("spotify", "spotify_1"),
        ("subscription", "apple_id_usa_25"),
        ("subscription", "steam_1000"),
        ("subscription", "spotify_3"),
    ])
    async def test_shop_keeps_old_behaviour(self, pinned, alerts, confirm, monkeypatch, purchase_type, tariff):
        _lookup(monkeypatch, purchase_type=purchase_type, tariff=tariff, price_kopecks=50000)
        body = {k: v for k, v in _paid_body().items() if k not in ("amount", "currency")}
        headers, raw, body = _signed(body)
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result == {"status": "ok"}
        # old behaviour: missing amount → expected price
        assert confirm.await_args.kwargs["amount_rubles"] == 500.0

    async def test_shop_foreign_currency_old_behaviour(self, pinned, alerts, confirm, monkeypatch):
        _lookup(monkeypatch, purchase_type="telegram_premium", tariff="premium_3")
        headers, raw, body = _signed(_paid_body(currency="USD", amount=5.0))
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result == {"status": "ok"}
        assert confirm.await_args.kwargs["amount_rubles"] == 5.0


# ── 3. Refund ──────────────────────────────────────────────────────

class TestRefund:
    async def test_refund_forced_alert_no_confirmation(self, pinned, alerts, confirm, monkeypatch):
        monkeypatch.setattr(
            database, "get_pending_purchase_any_status",
            AsyncMock(return_value={"telegram_id": 42, "purchase_type": "subscription", "tariff": "basic"}),
        )
        headers, raw, body = _signed(_paid_body(kind="Refund", amount=199.0))
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result["status"] == "refund_alerted"
        confirm.assert_not_called()
        assert alerts.await_count == 1
        assert alerts.await_args.kwargs.get("force") is True
        text = alerts.await_args.args[2]
        assert "p1" in text and "tx-1" in text and "42" in text

    async def test_refund_alert_survives_lookup_failure(self, pinned, alerts, confirm, monkeypatch):
        monkeypatch.setattr(
            database, "get_pending_purchase_any_status",
            AsyncMock(side_effect=RuntimeError("db down")),
        )
        headers, raw, body = _signed(_paid_body(kind="Refund"))
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result["status"] == "refund_alerted"
        assert alerts.await_count == 1


# ── 4. 401 alerts ──────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    calls: list = []
    response = _Resp(200)

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, content=None):
        type(self).calls.append(("POST", url, content))
        return type(self).response

    async def get(self, url, headers=None):
        type(self).calls.append(("GET", url, None))
        return type(self).response


@pytest.fixture
def http(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.response = _Resp(200)
    monkeypatch.setattr(wata_service.httpx, "AsyncClient", _FakeClient)
    return _FakeClient


@pytest.fixture
def admin_bot(monkeypatch):
    from app.api import payment_webhook
    bot = MagicMock()
    monkeypatch.setattr(payment_webhook, "_bot", bot)
    return bot


class TestUnauthorizedAlert:
    async def test_create_invoice_401_alerts_with_cooldown(self, http, alerts, admin_bot):
        http.response = _Resp(401, text="unauthorized")
        for _ in range(2):
            with pytest.raises(Exception, match="401"):
                await wata_service.create_invoice(199.0, "p1")
        assert alerts.await_count == 1
        assert alerts.await_args.args[0] is admin_bot

    async def test_check_link_status_401_returns_none_and_alerts(self, http, alerts, admin_bot):
        http.response = _Resp(401)
        assert await wata_service.check_link_status("link-1") is None
        assert alerts.await_count == 1

    async def test_check_transaction_401_returns_none_and_alerts(self, http, alerts, admin_bot):
        http.response = _Resp(401)
        assert await wata_service.check_transaction("tx-1") is None
        assert alerts.await_count == 1

    async def test_401_without_bot_only_logs(self, http, alerts, monkeypatch):
        from app.api import payment_webhook
        monkeypatch.setattr(payment_webhook, "_bot", None)
        http.response = _Resp(401)
        assert await wata_service.check_link_status("link-1") is None
        alerts.assert_not_called()

    async def test_500_does_not_alert(self, http, alerts, admin_bot):
        http.response = _Resp(500)
        assert await wata_service.check_link_status("link-1") is None
        alerts.assert_not_called()


# ── 5. Link expiry ─────────────────────────────────────────────────

class TestLinkExpiry:
    async def test_default_expiry_is_30_minutes(self, http):
        http.response = _Resp(200, {"id": "L1", "url": "https://pay/x"})
        before = datetime.now(timezone.utc)
        result = await wata_service.create_invoice(199.0, "p1")
        assert result == {"invoice_id": "L1", "payment_url": "https://pay/x"}
        method, url, content = http.calls[0]
        assert method == "POST" and url.endswith("/api/h2h/links")
        exp = datetime.strptime(
            json.loads(content)["expirationDateTime"], "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
        minutes = (exp - before).total_seconds() / 60
        assert 29 <= minutes <= 31


# ── 6. Declined ────────────────────────────────────────────────────

class TestDeclined:
    async def test_declined_non_forced_admin_alert(self, pinned, alerts, confirm, monkeypatch):
        monkeypatch.setattr(database, "get_pending_purchase_by_id", AsyncMock(return_value=None))
        headers, raw, body = _signed(_paid_body(transactionStatus="Declined", errorCode="TRA_2999"))
        result = await wata_service.process_webhook_data(headers, raw, body, MagicMock())
        assert result["status"] == "declined_notified"
        confirm.assert_not_called()
        assert alerts.await_count == 1
        assert alerts.await_args.kwargs.get("force", False) is False
        assert alerts.await_args.args[1] != "payment"  # own cooldown bucket
