"""
Smoke tests for payment webhook signature/auth verification.

Tests cover:
- CryptoBot HMAC-SHA256 signature verification
- Platega header-based authentication
- Rejection of invalid/missing credentials
- Health endpoint DB_READY checks
"""
import hashlib
import hmac
import importlib
import json
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_CRYPTOBOT_TOKEN = "test-cryptobot-token-12345"
FAKE_PLATEGA_MERCHANT_ID = "test-merchant-001"
FAKE_PLATEGA_SECRET = "test-platega-secret-xyz"


def _compute_cryptobot_signature(raw_body: bytes, api_token: str) -> str:
    """Reproduce CryptoBot HMAC-SHA256 signature algorithm."""
    secret = hashlib.sha256(api_token.encode("utf-8")).digest()
    return hmac.new(secret, raw_body, hashlib.sha256).hexdigest()


def _make_mock_config(**overrides):
    """Create a mock config module with sensible defaults."""
    cfg = MagicMock()
    cfg.CRYPTOBOT_API_TOKEN = overrides.get("CRYPTOBOT_API_TOKEN", FAKE_CRYPTOBOT_TOKEN)
    cfg.CRYPTOBOT_API_URL = overrides.get("CRYPTOBOT_API_URL", "https://pay.crypt.bot/api")
    cfg.PLATEGA_MERCHANT_ID = overrides.get("PLATEGA_MERCHANT_ID", FAKE_PLATEGA_MERCHANT_ID)
    cfg.PLATEGA_SECRET = overrides.get("PLATEGA_SECRET", FAKE_PLATEGA_SECRET)
    cfg.PLATEGA_API_URL = overrides.get("PLATEGA_API_URL", "https://api.platega.io")
    cfg.SBP_MARKUP_PERCENT = 11
    cfg.VALID_SUBSCRIPTION_TYPES = ["basic", "plus"]
    cfg.is_biz_tariff = lambda t: False
    return cfg


def _make_mock_database(db_ready: bool = True):
    """Create a mock database module."""
    db = MagicMock()
    db.DB_READY = db_ready
    db.get_pending_purchase_by_id = AsyncMock(return_value=None)
    db.finalize_purchase = AsyncMock(return_value=None)
    return db


def _load_cryptobot_service(config_mock=None, db_mock=None):
    """Load cryptobot_service with mocked heavy dependencies."""
    cfg = config_mock or _make_mock_config()
    db = db_mock or _make_mock_database()

    # Pre-inject mocks so import chain doesn't pull real modules
    saved = {}
    for mod_name, mock_obj in [("config", cfg), ("database", db), ("vpn_utils", MagicMock())]:
        saved[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = mock_obj

    try:
        if "cryptobot_service" in sys.modules:
            mod = importlib.reload(sys.modules["cryptobot_service"])
        else:
            mod = importlib.import_module("cryptobot_service")
        return mod
    finally:
        # Restore originals to avoid leaking mocks
        for mod_name, orig in saved.items():
            if orig is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = orig


def _load_platega_service(config_mock=None, db_mock=None):
    """Load platega_service with mocked heavy dependencies."""
    cfg = config_mock or _make_mock_config()
    db = db_mock or _make_mock_database()

    saved = {}
    for mod_name, mock_obj in [("config", cfg), ("database", db), ("vpn_utils", MagicMock())]:
        saved[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = mock_obj

    try:
        if "platega_service" in sys.modules:
            mod = importlib.reload(sys.modules["platega_service"])
        else:
            mod = importlib.import_module("platega_service")
        return mod
    finally:
        for mod_name, orig in saved.items():
            if orig is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = orig


# ---------------------------------------------------------------------------
# CryptoBot signature verification
# ---------------------------------------------------------------------------

class TestCryptobotSignatureVerification:
    """CryptoBot webhook signature (HMAC-SHA256 of body, keyed by SHA256(token))."""

    def test_valid_signature_accepted(self):
        svc = _load_cryptobot_service()
        body = b'{"update_type":"invoice_paid","payload":{}}'
        sig = _compute_cryptobot_signature(body, FAKE_CRYPTOBOT_TOKEN)
        assert svc.verify_webhook_signature(body, sig) is True

    def test_wrong_signature_rejected(self):
        svc = _load_cryptobot_service()
        body = b'{"update_type":"invoice_paid"}'
        assert svc.verify_webhook_signature(body, "deadbeef" * 8) is False

    def test_empty_signature_rejected(self):
        svc = _load_cryptobot_service()
        body = b'{"update_type":"invoice_paid"}'
        assert svc.verify_webhook_signature(body, "") is False

    def test_tampered_body_rejected(self):
        svc = _load_cryptobot_service()
        original = b'{"amount":"100"}'
        tampered = b'{"amount":"999"}'
        sig = _compute_cryptobot_signature(original, FAKE_CRYPTOBOT_TOKEN)
        assert svc.verify_webhook_signature(tampered, sig) is False

    def test_empty_body_valid_signature(self):
        svc = _load_cryptobot_service()
        body = b""
        sig = _compute_cryptobot_signature(body, FAKE_CRYPTOBOT_TOKEN)
        assert svc.verify_webhook_signature(body, sig) is True

    def test_no_token_configured_rejects_all(self):
        cfg = _make_mock_config(CRYPTOBOT_API_TOKEN="")
        svc = _load_cryptobot_service(config_mock=cfg)
        body = b'{"test": true}'
        sig = _compute_cryptobot_signature(body, "any-token")
        assert svc.verify_webhook_signature(body, sig) is False


# ---------------------------------------------------------------------------
# Platega header-based authentication
# ---------------------------------------------------------------------------

class TestPlategaWebhookAuth:
    """Platega auth: X-MerchantId + X-Secret header comparison via hmac.compare_digest."""

    @pytest.mark.asyncio
    async def test_valid_auth_headers_accepted(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_platega_service(db_mock=db_mock)
        # Re-inject db mock after load (module caches reference)
        svc.database = db_mock

        headers = {"x-merchantid": FAKE_PLATEGA_MERCHANT_ID, "x-secret": FAKE_PLATEGA_SECRET}
        body = {
            "id": "txn-001",
            "status": "confirmed",
            "payload": json.dumps({"purchase_id": "p-123"}),
            "paymentDetails": {"amount": 100},
        }
        result = await svc.process_webhook_data(headers, body, MagicMock())
        assert result["status"] != "unauthorized"

    @pytest.mark.asyncio
    async def test_wrong_merchant_id_rejected(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_platega_service(db_mock=db_mock)
        svc.database = db_mock

        headers = {"x-merchantid": "wrong-merchant", "x-secret": FAKE_PLATEGA_SECRET}
        body = {"id": "txn-001", "status": "confirmed"}
        result = await svc.process_webhook_data(headers, body, MagicMock())
        assert result["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_wrong_secret_rejected(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_platega_service(db_mock=db_mock)
        svc.database = db_mock

        headers = {"x-merchantid": FAKE_PLATEGA_MERCHANT_ID, "x-secret": "wrong-secret"}
        body = {"id": "txn-001", "status": "confirmed"}
        result = await svc.process_webhook_data(headers, body, MagicMock())
        assert result["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_missing_headers_rejected(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_platega_service(db_mock=db_mock)
        svc.database = db_mock

        headers = {}
        body = {"id": "txn-001", "status": "confirmed"}
        result = await svc.process_webhook_data(headers, body, MagicMock())
        assert result["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_db_not_ready_returns_degraded(self):
        db_mock = _make_mock_database(db_ready=False)
        svc = _load_platega_service(db_mock=db_mock)
        svc.database = db_mock

        headers = {"x-merchantid": FAKE_PLATEGA_MERCHANT_ID, "x-secret": FAKE_PLATEGA_SECRET}
        body = {"id": "txn-001", "status": "confirmed"}
        result = await svc.process_webhook_data(headers, body, MagicMock())
        assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_case_insensitive_headers(self):
        """Headers should be matched case-insensitively (e.g. X-MerchantId vs x-merchantid)."""
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_platega_service(db_mock=db_mock)
        svc.database = db_mock

        headers = {"X-MerchantId": FAKE_PLATEGA_MERCHANT_ID, "X-Secret": FAKE_PLATEGA_SECRET}
        body = {
            "id": "txn-002",
            "status": "completed",
            "payload": json.dumps({"purchase_id": "p-456"}),
            "paymentDetails": {"amount": 200},
        }
        result = await svc.process_webhook_data(headers, body, MagicMock())
        assert result["status"] != "unauthorized"
