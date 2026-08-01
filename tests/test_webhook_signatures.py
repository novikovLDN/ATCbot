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
FAKE_LAVA_JWT = "test-lava-jwt-token"
FAKE_LAVA_SHOP_ID = "test-lava-shop-uuid"
FAKE_LAVA_SIGN_KEY = "test-lava-sign-key-abc"


def _compute_lava_signature(raw_body: bytes, sign_key: str) -> str:
    """Reproduce Lava HMAC-SHA256 webhook signature algorithm."""
    return hmac.new(sign_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


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
    cfg.LAVA_JWT_TOKEN = overrides.get("LAVA_JWT_TOKEN", FAKE_LAVA_JWT)
    cfg.LAVA_SHOP_ID = overrides.get("LAVA_SHOP_ID", FAKE_LAVA_SHOP_ID)
    cfg.LAVA_SIGN_KEY = overrides.get("LAVA_SIGN_KEY", FAKE_LAVA_SIGN_KEY)
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


def _load_lava_service(config_mock=None, db_mock=None):
    """Load lava_service with mocked heavy dependencies."""
    cfg = config_mock or _make_mock_config()
    db = db_mock or _make_mock_database()

    saved = {}
    for mod_name, mock_obj in [("config", cfg), ("database", db), ("vpn_utils", MagicMock())]:
        saved[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = mock_obj

    try:
        if "lava_service" in sys.modules:
            mod = importlib.reload(sys.modules["lava_service"])
        else:
            mod = importlib.import_module("lava_service")
        return mod
    finally:
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


# ---------------------------------------------------------------------------
# Lava webhook signature verification
# ---------------------------------------------------------------------------


class TestLavaWebhookAuth:
    """Lava auth: HMAC-SHA256 подпись сырого тела в заголовке Authorization.

    До этих тестов подпись не проверялась вовсе: функция _verify_webhook_signature
    была объявлена и ни разу не вызывалась, поэтому любой POST на публичный
    /webhooks/lava с известным purchase_id активировал подписку без оплаты.
    """

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_lava_service(db_mock=db_mock)
        svc.database = db_mock

        body = {"order_id": "purchase_abc123", "status": "success", "amount": 1599}
        raw = json.dumps(body).encode("utf-8")
        headers = {"authorization": _compute_lava_signature(raw, FAKE_LAVA_SIGN_KEY)}

        result = await svc.process_webhook_data(headers, body, MagicMock(), raw)
        assert result["status"] != "unauthorized"

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_lava_service(db_mock=db_mock)
        svc.database = db_mock

        body = {"order_id": "purchase_abc123", "status": "success", "amount": 1599}
        raw = json.dumps(body).encode("utf-8")

        result = await svc.process_webhook_data({}, body, MagicMock(), raw)
        assert result["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_wrong_signature_rejected(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_lava_service(db_mock=db_mock)
        svc.database = db_mock

        body = {"order_id": "purchase_abc123", "status": "success", "amount": 1599}
        raw = json.dumps(body).encode("utf-8")
        headers = {"authorization": "0" * 64}

        result = await svc.process_webhook_data(headers, body, MagicMock(), raw)
        assert result["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_tampered_body_rejected(self):
        """Подпись считается от сырого тела: подмена суммы после подписи должна ломать проверку."""
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_lava_service(db_mock=db_mock)
        svc.database = db_mock

        original = {"order_id": "purchase_abc123", "status": "success", "amount": 10}
        signature = _compute_lava_signature(json.dumps(original).encode("utf-8"), FAKE_LAVA_SIGN_KEY)

        tampered = {"order_id": "purchase_abc123", "status": "success", "amount": 9999}
        tampered_raw = json.dumps(tampered).encode("utf-8")

        result = await svc.process_webhook_data(
            {"authorization": signature}, tampered, MagicMock(), tampered_raw
        )
        assert result["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_unconfigured_sign_key_rejects_instead_of_allowing(self):
        """Ненастроенный ключ обязан закрывать вебхук, а не открывать его настежь."""
        cfg = _make_mock_config(LAVA_SIGN_KEY="")
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_lava_service(config_mock=cfg, db_mock=db_mock)
        svc.database = db_mock

        body = {"order_id": "purchase_abc123", "status": "success", "amount": 1599}
        raw = json.dumps(body).encode("utf-8")

        result = await svc.process_webhook_data(
            {"authorization": "anything"}, body, MagicMock(), raw
        )
        assert result["status"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_case_insensitive_authorization_header(self):
        db_mock = _make_mock_database(db_ready=True)
        svc = _load_lava_service(db_mock=db_mock)
        svc.database = db_mock

        body = {"order_id": "purchase_xyz", "status": "success", "amount": 500}
        raw = json.dumps(body).encode("utf-8")
        headers = {"Authorization": _compute_lava_signature(raw, FAKE_LAVA_SIGN_KEY)}

        result = await svc.process_webhook_data(headers, body, MagicMock(), raw)
        assert result["status"] != "unauthorized"
