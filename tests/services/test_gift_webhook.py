"""Доставка подарочной подписки при оплате через вебхук.

Дефект: process_confirmed_payment не знал про purchase_type='gift'.
finalize_purchase подарок создавал (gift_code лежал в базе), но покупателю
уходило общее подтверждение подписки — «Оплата получена! Тариф: Basic,
До: N/A» с кнопками подключения VPN, потому что у подарка нет expires_at.
Ссылку на подарок человек не получал никогда.

Оплата картой в Telegram (payments_messages.py) подарок обрабатывала
правильно — расходились только вебхуки CryptoBot / Lava / Платеги.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


GIFT_PENDING = {
    "telegram_id": 777,
    "purchase_type": "gift",
    "tariff": "plus",
    "price_kopecks": 99900,
}

GIFT_RESULT = {
    "success": True,
    "payment_id": 42,
    "is_gift": True,
    "gift_code": "GIFT-XYZ-123",
    "gift_tariff": "plus",
    "gift_period_days": 90,
    "expires_at": None,
}


def _patch_common(monkeypatch, confirmation, finalize_result):
    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(return_value=GIFT_PENDING)
    fake_db.finalize_purchase = AsyncMock(return_value=finalize_result)
    monkeypatch.setattr(confirmation, "database", fake_db)
    return fake_db


@pytest.mark.asyncio
async def test_gift_webhook_sends_gift_link_not_subscription_text(monkeypatch):
    """Главный регресс: покупатель получает ссылку на подарок."""
    from app.services.payments import confirmation

    _patch_common(monkeypatch, confirmation, GIFT_RESULT)

    sent = {}

    async def fake_send_gift_success(*, bot, telegram_id, language, gift_code,
                                     tariff, period_days):
        sent.update(
            telegram_id=telegram_id, gift_code=gift_code,
            tariff=tariff, period_days=period_days,
        )

    async def fake_language(_tg):
        return "ru"

    generic = AsyncMock()
    monkeypatch.setattr(
        "app.handlers.callbacks.gift._send_gift_success",
        fake_send_gift_success, raising=False,
    )
    monkeypatch.setattr(
        "app.services.language_service.resolve_user_language",
        fake_language, raising=False,
    )
    monkeypatch.setattr(confirmation, "_send_confirmation", generic)

    result = await confirmation.process_confirmed_payment(
        provider="cryptobot", purchase_id="p-gift-1", amount_rubles=999.0,
        invoice_id="inv-gift-1", telegram_id=777, bot=MagicMock(),
    )

    assert result["status"] == "ok"
    assert sent["gift_code"] == "GIFT-XYZ-123", "код подарка не доставлен покупателю"
    assert sent["tariff"] == "plus"
    assert sent["period_days"] == 90
    assert not generic.called, "подарок ушёл в общее подтверждение подписки"


@pytest.mark.asyncio
async def test_gift_webhook_does_not_sync_purchase_to_site(monkeypatch):
    """Подписка покупателя не менялась — синхронизировать на сайт нечего."""
    from app.services.payments import confirmation

    _patch_common(monkeypatch, confirmation, GIFT_RESULT)

    synced = []

    async def fake_full_sync(*args, **kwargs):
        synced.append(args)

    async def fake_send_gift_success(**kwargs):
        return None

    async def fake_language(_tg):
        return "ru"

    monkeypatch.setattr(
        "app.handlers.callbacks.gift._send_gift_success",
        fake_send_gift_success, raising=False,
    )
    monkeypatch.setattr(
        "app.services.language_service.resolve_user_language",
        fake_language, raising=False,
    )
    monkeypatch.setattr(
        "app.services.site_sync.is_enabled", lambda: True, raising=False,
    )
    monkeypatch.setattr(
        "app.services.site_sync.full_sync_after_payment",
        fake_full_sync, raising=False,
    )

    await confirmation.process_confirmed_payment(
        provider="lava", purchase_id="p-gift-2", amount_rubles=999.0,
        invoice_id="inv-gift-2", telegram_id=777, bot=MagicMock(),
    )

    assert not synced, "подарок не должен уезжать на сайт как купленный тариф"


@pytest.mark.asyncio
async def test_regular_subscription_still_uses_generic_confirmation(monkeypatch):
    """Обычная подписка не должна пострадать от ветки подарка."""
    from app.services.payments import confirmation

    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(
        return_value={"telegram_id": 777, "purchase_type": "subscription",
                      "tariff": "basic", "price_kopecks": 49900}
    )
    fake_db.finalize_purchase = AsyncMock(return_value={
        "success": True, "payment_id": 7, "expires_at": None,
        "subscription_type": "basic",
    })
    monkeypatch.setattr(confirmation, "database", fake_db)

    generic = AsyncMock()
    monkeypatch.setattr(confirmation, "_send_confirmation", generic)
    monkeypatch.setattr(
        "app.services.site_sync.is_enabled", lambda: False, raising=False,
    )

    result = await confirmation.process_confirmed_payment(
        provider="platega", purchase_id="p-sub-1", amount_rubles=499.0,
        invoice_id="inv-sub-1", telegram_id=777, bot=MagicMock(),
    )

    assert result["status"] == "ok"
    generic.assert_awaited_once()
