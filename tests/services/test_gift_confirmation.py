"""
Regression: платный подарок через ВНЕШНЮЮ оплату (platega/lava/cryptobot/wata)
должен доставлять покупателю share-ссылку.

Раньше confirmation.py не ветвил is_gift → покупатель получал обычное
«подписка активирована» и терял ссылку-подарок. Теперь есть
_handle_gift_confirmation (идемпотентный) → _send_gift_success.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payments import confirmation


def _result():
    return {
        "success": True, "payment_id": 555, "is_gift": True,
        "gift_code": "ABCD1234", "gift_tariff": "plus", "gift_period_days": 90,
    }


@pytest.mark.asyncio
async def test_gift_confirmation_sends_share_link():
    send = AsyncMock()
    with patch.object(confirmation.database, "mark_payment_notification_sent", AsyncMock(return_value=True)), \
         patch("app.services.language_service.resolve_user_language", AsyncMock(return_value="ru")), \
         patch("app.handlers.callbacks.gift._send_gift_success", send), \
         patch("app.handlers.callbacks.payments_callbacks.delete_invoice_message_for_purchase", AsyncMock()):
        await confirmation._handle_gift_confirmation(
            provider="wata", bot=object(), telegram_id=42,
            payment_id=555, purchase_id="p1", result=_result(),
        )
    send.assert_awaited_once()
    kw = send.await_args.kwargs
    assert kw["gift_code"] == "ABCD1234"
    assert kw["tariff"] == "plus"
    assert kw["period_days"] == 90
    assert kw["telegram_id"] == 42


@pytest.mark.asyncio
async def test_gift_confirmation_idempotent_skip():
    # Повторный вебхук: mark_payment_notification_sent=False → НЕ слать второй раз.
    send = AsyncMock()
    with patch.object(confirmation.database, "mark_payment_notification_sent", AsyncMock(return_value=False)), \
         patch("app.handlers.callbacks.gift._send_gift_success", send):
        await confirmation._handle_gift_confirmation(
            provider="wata", bot=object(), telegram_id=42,
            payment_id=555, purchase_id="p1", result=_result(),
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_gift_confirmation_no_code_noop():
    send = AsyncMock()
    mark = AsyncMock(return_value=True)
    with patch.object(confirmation.database, "mark_payment_notification_sent", mark), \
         patch("app.handlers.callbacks.gift._send_gift_success", send):
        await confirmation._handle_gift_confirmation(
            provider="wata", bot=object(), telegram_id=42,
            payment_id=555, purchase_id="p1", result={"is_gift": True},  # нет gift_code
        )
    send.assert_not_awaited()
    mark.assert_not_awaited()  # выходим до idempotency-флага
