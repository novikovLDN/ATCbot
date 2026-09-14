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
    """Tests for validate_payment_amount(actual_rubles, expected_rubles, tolerance=1.0).

    Contract (unchanged since the service layer was extracted in 5ce26527):
    compares two ruble amounts, returns True within tolerance, raises
    PaymentAmountMismatchError otherwise.
    """

    @pytest.mark.asyncio
    async def test_amount_matches(self):
        """Exactly matching amounts pass."""
        assert await validate_payment_amount(1000.0, 1000.0) is True

    @pytest.mark.asyncio
    async def test_amount_within_default_tolerance(self):
        """Difference of exactly 1 RUB (default tolerance) still passes."""
        assert await validate_payment_amount(999.0, 1000.0) is True

    @pytest.mark.asyncio
    async def test_amount_mismatch(self):
        """Difference above tolerance raises PaymentAmountMismatchError."""
        with pytest.raises(PaymentAmountMismatchError, match="expected=1500.00 RUB, actual=1000.00 RUB"):
            await validate_payment_amount(1000.0, 1500.0)

    @pytest.mark.asyncio
    async def test_balance_topup_amount(self):
        """Custom tolerance: 0.5 RUB diff passes with tolerance=1, fails with 0.1."""
        assert await validate_payment_amount(500.0, 500.5) is True
        with pytest.raises(PaymentAmountMismatchError):
            await validate_payment_amount(500.0, 500.5, tolerance=0.1)


class _FakeConn:
    def __init__(self, payment_row):
        self.payment_row = payment_row
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        return self.payment_row


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, payment_row):
        self.conn = _FakeConn(payment_row)

    def acquire(self):
        return _FakeAcquire(self.conn)


class TestCheckPaymentIdempotency:
    """Tests for check_payment_idempotency(purchase_id, telegram_id) -> (bool, sub|None).

    Already processed == pending_purchase.status == 'paid' AND the latest
    payments row for that purchase_id is 'approved'.
    """

    @pytest.mark.asyncio
    async def test_payment_not_processed(self):
        """No pending purchase → (False, None), no payments lookup."""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value=None)
            mock_db.get_pool = AsyncMock()
            result = await check_payment_idempotency("purchase_123", 12345)
            assert result == (False, None)
            mock_db.get_pending_purchase.assert_awaited_once_with(
                "purchase_123", 12345, check_expiry=False
            )
            mock_db.get_pool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_payment_already_processed(self):
        """Paid purchase + approved payment → (True, existing subscription)."""
        subscription = {"telegram_id": 12345, "status": "active"}
        pool = _FakePool({"id": 123, "status": "approved"})
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value={"status": "paid"})
            mock_db.get_pool = AsyncMock(return_value=pool)
            mock_db.get_subscription = AsyncMock(return_value=subscription)
            result = await check_payment_idempotency("purchase_123", 12345)
            assert result == (True, subscription)
            mock_db.get_subscription.assert_awaited_once_with(12345)
        query, args = pool.conn.queries[0]
        assert "FROM payments WHERE purchase_id = $1" in query
        assert args == ("purchase_123",)

    @pytest.mark.asyncio
    async def test_payment_pending(self):
        """Pending purchase still 'pending' → not processed, payments not queried."""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value={"status": "pending"})
            mock_db.get_pool = AsyncMock()
            result = await check_payment_idempotency("purchase_123", 12345)
            assert result == (False, None)
            mock_db.get_pool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_payment_rejected(self):
        """Purchase 'paid' but latest payment 'rejected' → not processed (can retry)."""
        pool = _FakePool({"id": 123, "status": "rejected"})
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_pending_purchase = AsyncMock(return_value={"status": "paid"})
            mock_db.get_pool = AsyncMock(return_value=pool)
            mock_db.get_subscription = AsyncMock()
            result = await check_payment_idempotency("purchase_123", 12345)
            assert result == (False, None)
            mock_db.get_subscription.assert_not_awaited()
