"""Уведомление реферера о кешбэке — единая точка входа.

Дефект: кешбэк начисляет finalize_purchase внутри транзакции и возвращает
словарь referral_reward, а сообщение шлёт вызывающий код. Вебхуки
CryptoBot/Lava/Платеги этот словарь не читали вовсе — деньги рефереру
начислялись, уведомления он не получал никогда. Остальные три пути оплаты
слали его каждый по-своему, с копипастой форматирования периода.

Теперь все пути ходят через notify_referral_cashback.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestFormatSubscriptionPeriod:
    """Срок подписки в тексте уведомления. Врать нельзя: 45 дней — это
    не «1 месяц», иначе реферер видит неверные данные о чужой покупке."""

    def test_known_periods(self):
        from app.handlers.notifications import format_subscription_period
        assert format_subscription_period(30) == "1 месяц"
        assert format_subscription_period(90) == "3 месяца"
        assert format_subscription_period(180) == "6 месяцев"
        assert format_subscription_period(365) == "12 месяцев"

    def test_multiples_of_thirty(self):
        from app.handlers.notifications import format_subscription_period
        assert format_subscription_period(60) == "2 месяца"
        assert format_subscription_period(150) == "5 месяцев"

    def test_non_round_period_stays_in_days(self):
        from app.handlers.notifications import format_subscription_period
        assert format_subscription_period(45) == "45 дней"
        assert format_subscription_period(7) == "7 дней"

    def test_missing_period_is_none(self):
        from app.handlers.notifications import format_subscription_period
        assert format_subscription_period(None) is None
        assert format_subscription_period(0) is None


REWARD = {
    "success": True,
    "referrer_id": 111,
    "reward_amount": 49.9,
    "percent": 10,
    "paid_referrals_count": 3,
    "referrals_needed": 2,
}


@pytest.mark.asyncio
async def test_notify_passes_reward_fields_through(monkeypatch):
    from app.handlers import notifications

    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "send_referral_cashback_notification", sender)

    ok = await notifications.notify_referral_cashback(
        MagicMock(), REWARD, referred_id=222, purchase_amount=499.0,
        action_type="purchase", period_days=90, context="test",
    )

    assert ok is True
    kwargs = sender.await_args.kwargs
    assert kwargs["referrer_id"] == 111
    assert kwargs["cashback_amount"] == 49.9
    assert kwargs["cashback_percent"] == 10
    assert kwargs["subscription_period"] == "3 месяца"
    assert kwargs["action_type"] == "purchase"


@pytest.mark.asyncio
async def test_notify_skips_when_no_reward(monkeypatch):
    """Кешбэка не было — вызывающему коду не нужна своя проверка."""
    from app.handlers import notifications

    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "send_referral_cashback_notification", sender)

    assert await notifications.notify_referral_cashback(
        MagicMock(), None, referred_id=222, purchase_amount=499.0,
    ) is False
    assert await notifications.notify_referral_cashback(
        MagicMock(), {"success": False, "reason": "no_referrer"},
        referred_id=222, purchase_amount=499.0,
    ) is False
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_never_raises(monkeypatch):
    """Платёж уже закоммичен — падение уведомления не должно его ронять."""
    from app.handlers import notifications

    sender = AsyncMock(side_effect=RuntimeError("telegram down"))
    monkeypatch.setattr(notifications, "send_referral_cashback_notification", sender)

    assert await notifications.notify_referral_cashback(
        MagicMock(), REWARD, referred_id=222, purchase_amount=499.0,
    ) is False


@pytest.mark.asyncio
async def test_webhook_payment_notifies_referrer(monkeypatch):
    """Главный регресс: вебхук обязан уведомить реферера."""
    from app.services.payments import confirmation
    from app.handlers import notifications

    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(
        return_value={"telegram_id": 777, "purchase_type": "subscription",
                      "tariff": "basic", "price_kopecks": 49900}
    )
    fake_db.finalize_purchase = AsyncMock(return_value={
        "success": True, "payment_id": 5, "expires_at": None,
        "subscription_type": "basic", "period_days": 30,
        "referral_reward": REWARD,
    })
    monkeypatch.setattr(confirmation, "database", fake_db)
    monkeypatch.setattr(confirmation, "_send_confirmation", AsyncMock())
    monkeypatch.setattr(
        "app.services.site_sync.is_enabled", lambda: False, raising=False,
    )

    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "send_referral_cashback_notification", sender)

    result = await confirmation.process_confirmed_payment(
        provider="cryptobot", purchase_id="p-ref-1", amount_rubles=499.0,
        invoice_id="inv-ref-1", telegram_id=777, bot=MagicMock(),
    )

    assert result["status"] == "ok"
    sender.assert_awaited_once()
    assert sender.await_args.kwargs["referrer_id"] == 111
