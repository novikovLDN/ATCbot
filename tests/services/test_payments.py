"""
Unit tests for payment service layer.

Tests focus on business logic:
- Payment payload verification
- Amount validation
- Idempotency checks
- Edge cases
"""
import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock
from app.services.payments.service import (
    verify_payment_payload,
    validate_payment_amount,
    check_payment_idempotency,
)
from app.services.payments.exceptions import (
    InvalidPaymentPayloadError,
    PaymentAmountMismatchError,
    PaymentAlreadyProcessedError,
)


class TestVerifyPaymentPayload:
    """Tests for verify_payment_payload function"""

    @pytest.mark.asyncio
    async def test_valid_purchase_payload(self):
        """Valid purchase payload should be parsed correctly"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value={
                "tariff": "basic",
                "price_kopecks": 100000,
                "promo_code": None,
            })

            result = await verify_payment_payload("purchase:123", 12345)

            assert result.payload_type == "purchase"
            assert result.purchase_id == "123"
            assert result.telegram_id == 12345
            assert result.tariff == "basic"
            assert result.amount == 1000.0

    @pytest.mark.asyncio
    async def test_balance_topup_payload(self):
        """Balance topup payload should be parsed correctly"""
        result = await verify_payment_payload("balance_topup_12345_500", 12345)

        assert result.payload_type == "balance_topup"
        assert result.telegram_id == 12345
        assert result.amount == 500.0

    @pytest.mark.asyncio
    async def test_invalid_payload_format(self):
        """Invalid payload format should raise exception"""
        with pytest.raises(InvalidPaymentPayloadError):
            await verify_payment_payload("invalid_format", 12345)

    @pytest.mark.asyncio
    async def test_empty_payload(self):
        """Empty payload should raise exception"""
        with pytest.raises(InvalidPaymentPayloadError):
            await verify_payment_payload("", 12345)

    @pytest.mark.asyncio
    async def test_telegram_id_mismatch(self):
        """Telegram ID mismatch should raise exception"""
        with pytest.raises(InvalidPaymentPayloadError):
            await verify_payment_payload("balance_topup_12345_500", 99999)

    @pytest.mark.asyncio
    async def test_pending_purchase_not_found(self):
        """Missing pending purchase should raise exception"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value=None)

            with pytest.raises(InvalidPaymentPayloadError):
                await verify_payment_payload("purchase:123", 12345)


class TestValidatePaymentAmount:
    """Tests for validate_payment_amount function"""

    @pytest.mark.asyncio
    async def test_amount_matches(self):
        """Matching amounts should pass validation"""
        result = await validate_payment_amount(1000.0, 1000.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_amount_mismatch(self):
        """Mismatched amounts should raise exception"""
        with pytest.raises(PaymentAmountMismatchError):
            await validate_payment_amount(1000.0, 1500.0)

    @pytest.mark.asyncio
    async def test_balance_topup_amount(self):
        """Amounts within tolerance should pass"""
        result = await validate_payment_amount(500.0, 500.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_amount_within_tolerance(self):
        """Amounts within tolerance (0.01 RUB) should pass"""
        result = await validate_payment_amount(500.005, 500.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_amount_outside_tolerance(self):
        """Amounts outside tolerance should raise exception"""
        with pytest.raises(PaymentAmountMismatchError):
            await validate_payment_amount(500.02, 500.0)


class TestCheckPaymentIdempotency:
    """Tests for check_payment_idempotency function"""

    @pytest.mark.asyncio
    async def test_payment_not_processed(self):
        """Payment not yet processed should return (False, None)"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value=None)
            is_processed, data = await check_payment_idempotency("purchase_123", 12345)
            assert is_processed is False
            assert data is None

    @pytest.mark.asyncio
    async def test_payment_already_processed(self):
        """Already processed payment should return (True, subscription)"""
        from contextlib import asynccontextmanager

        mock_subscription = {
            "status": "active",
            "expires_at": datetime(2024, 2, 15),
            "vpn_key": "vless://test",
        }
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": 123, "status": "approved"})

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value={
                "status": "paid",
                "tariff": "basic",
                "price_kopecks": 14900,
            })
            mock_pool = AsyncMock()
            mock_pool.acquire = mock_acquire
            mock_db.get_pool = AsyncMock(return_value=mock_pool)
            mock_db.get_subscription = AsyncMock(return_value=mock_subscription)

            is_processed, data = await check_payment_idempotency("purchase_123", 12345)
            assert is_processed is True
            assert data == mock_subscription

    @pytest.mark.asyncio
    async def test_payment_pending(self):
        """Pending purchase should return (False, None)"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value={
                "status": "pending",
                "tariff": "basic",
                "price_kopecks": 14900,
            })
            is_processed, data = await check_payment_idempotency("purchase_123", 12345)
            assert is_processed is False
            assert data is None

    @pytest.mark.asyncio
    async def test_payment_expired(self):
        """Expired purchase should return (False, None)"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value={
                "status": "expired",
                "tariff": "basic",
                "price_kopecks": 14900,
            })
            is_processed, data = await check_payment_idempotency("purchase_123", 12345)
            assert is_processed is False
            assert data is None
