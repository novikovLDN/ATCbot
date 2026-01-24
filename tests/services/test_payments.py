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
        payload_info = type('obj', (object,), {
            'amount_rubles': 1000.0,
            'payment_type': 'subscription',
        })()
        
        with patch('app.services.payments.service.subscription_service') as mock_sub:
            mock_sub.calculate_price = AsyncMock(return_value=1000.0)
            await validate_payment_amount(payload_info, 1000)
            # Should not raise
    
    @pytest.mark.asyncio
    async def test_amount_mismatch(self):
        """Mismatched amounts should raise exception"""
        payload_info = type('obj', (object,), {
            'amount_rubles': 1000.0,
            'payment_type': 'subscription',
            'tariff': 'basic',
            'period_days': 30,
        })()
        
        with patch('app.services.payments.service.subscription_service') as mock_sub:
            mock_sub.calculate_price = AsyncMock(return_value=1500.0)
            with pytest.raises(PaymentAmountMismatchError):
                await validate_payment_amount(payload_info, 1000)
    
    @pytest.mark.asyncio
    async def test_balance_topup_amount(self):
        """Balance topup should validate against payload amount"""
        payload_info = type('obj', (object,), {
            'amount_rubles': 500.0,
            'payment_type': 'balance_topup',
        })()
        
        await validate_payment_amount(payload_info, 500)
        # Should not raise


class TestCheckPaymentIdempotency:
    """Tests for check_payment_idempotency function"""
    
    @pytest.mark.asyncio
    async def test_payment_not_processed(self):
        """Payment not yet processed should pass"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_payment_by_provider_id = AsyncMock(return_value=None)
            result = await check_payment_idempotency("provider_123", "invoice_456")
            assert result is False
    
    @pytest.mark.asyncio
    async def test_payment_already_processed(self):
        """Already processed payment should raise exception"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_payment_by_provider_id = AsyncMock(return_value={
                "status": "approved",
                "payment_id": 123,
            })
            with pytest.raises(PaymentAlreadyProcessedError):
                await check_payment_idempotency("provider_123", "invoice_456")
    
    @pytest.mark.asyncio
    async def test_payment_pending(self):
        """Pending payment should raise exception"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_payment_by_provider_id = AsyncMock(return_value={
                "status": "pending",
                "payment_id": 123,
            })
            with pytest.raises(PaymentAlreadyProcessedError):
                await check_payment_idempotency("provider_123", "invoice_456")
    
    @pytest.mark.asyncio
    async def test_payment_rejected(self):
        """Rejected payment should pass (can retry)"""
        with patch('app.services.payments.service.database') as mock_db:
            mock_db.get_payment_by_provider_id = AsyncMock(return_value={
                "status": "rejected",
                "payment_id": 123,
            })
            result = await check_payment_idempotency("provider_123", "invoice_456")
            assert result is False
