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
from unittest.mock import patch, AsyncMock, MagicMock
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
    """Сверка суммы платежа с ожидаемой ценой.

    Сигнатура давно изменилась: функция принимает две суммы и допуск,
    а не объект payload. Прежние тесты собирали фиктивный объект и
    падали на несовпадении интерфейса, ничего не проверяя.
    """

    @pytest.mark.asyncio
    async def test_exact_match_passes(self):
        assert await validate_payment_amount(1000.0, 1000.0) is True

    @pytest.mark.asyncio
    async def test_difference_within_tolerance_passes(self):
        """Допуск в рубль закрывает округления на стороне провайдера."""
        assert await validate_payment_amount(999.5, 1000.0) is True

    @pytest.mark.asyncio
    async def test_underpayment_raises(self):
        """Недоплата — единственное, ради чего эта проверка существует."""
        with pytest.raises(PaymentAmountMismatchError):
            await validate_payment_amount(500.0, 1000.0)

    @pytest.mark.asyncio
    async def test_overpayment_also_raises(self):
        """Переплата тоже расхождение: обычно это признак подмены суммы."""
        with pytest.raises(PaymentAmountMismatchError):
            await validate_payment_amount(1500.0, 1000.0)

    @pytest.mark.asyncio
    async def test_custom_tolerance_respected(self):
        assert await validate_payment_amount(1005.0, 1000.0, tolerance=10.0) is True
        with pytest.raises(PaymentAmountMismatchError):
            await validate_payment_amount(1005.0, 1000.0, tolerance=1.0)

    @pytest.mark.asyncio
    async def test_error_message_contains_both_amounts(self):
        """Сообщение попадает в алерт админу — по нему разбирают инцидент."""
        with pytest.raises(PaymentAmountMismatchError) as exc:
            await validate_payment_amount(500.0, 1000.0)
        text = str(exc.value)
        assert "500" in text and "1000" in text


class TestCheckPaymentIdempotency:
    """Защита от повторной обработки платежа.

    Возвращает пару (уже_обработан, данные_подписки). Прежние тесты
    вызывали функцию с идентификаторами провайдера вместо purchase_id
    и telegram_id и мокали несуществующую функцию базы.
    """

    @pytest.mark.asyncio
    async def test_unknown_purchase_is_not_processed(self, monkeypatch):
        from app.services.payments import service as svc
        fake_db = MagicMock()
        fake_db.get_pending_purchase = AsyncMock(return_value=None)
        monkeypatch.setattr(svc, "database", fake_db)

        processed, data = await check_payment_idempotency("purchase_abc", 777)
        assert processed is False
        assert data is None

    @pytest.mark.asyncio
    async def test_pending_purchase_is_not_processed(self, monkeypatch):
        """Покупка ждёт оплаты — обрабатывать её можно и нужно."""
        from app.services.payments import service as svc
        fake_db = MagicMock()
        fake_db.get_pending_purchase = AsyncMock(
            return_value={"status": "pending", "telegram_id": 777}
        )
        monkeypatch.setattr(svc, "database", fake_db)

        processed, _ = await check_payment_idempotency("purchase_abc", 777)
        assert processed is False

