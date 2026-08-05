"""Оплаченная плёнка от шторма: применение или возврат денег.

Дефект: если плёнка не применилась (грядку успели собрать, она уже накрыта,
статус больше не growing, шторм уже прошёл), finalize_purchase всё равно
записывал платёж и возвращал success=True. Комментарий в коде обещал, что
«логика возврата может быть запущена отдельно» — такой логики не
существовало. Плюс ветки farm_effect не было в обработчике вебхуков, и
покупатель получал общий текст подписки «Тариф: Basic, До: N/A».

Сценарий: человек жмёт «Накрыть» за 10 минут до шторма, платит через СБП
спустя 15 минут — шторм уже отработал, грядка мертва, деньги списаны.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_webhook_tells_user_about_refund(monkeypatch):
    """Не применилась + возврат → человек видит сумму возврата."""
    from app.services.payments import confirmation

    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(
        return_value={"telegram_id": 777, "purchase_type": "farm_effect",
                      "tariff": "farm_storm_shield", "price_kopecks": 5000}
    )
    fake_db.finalize_purchase = AsyncMock(return_value={
        "success": True, "payment_id": 9, "expires_at": None,
        "is_farm_effect": True, "farm_plot_id": 2,
        "farm_shield_applied": False,
        "farm_shield_reason": "plot_not_growing",
        "farm_shield_refund_kopecks": 5000,
    })
    # Выдача идёт через сервисный слой, а он держит собственную ссылку на
    # database. Подменяем в обоих модулях: иначе обёртка пойдёт в настоящую
    # базу, которой в тестах нет.
    monkeypatch.setattr(confirmation, "database", fake_db)
    from app.services.subscriptions import service as _subscription_service
    monkeypatch.setattr(_subscription_service, "database", fake_db)
    monkeypatch.setattr(
        "app.services.site_sync.is_enabled", lambda: False, raising=False,
    )

    bot = MagicMock()
    bot.send_message = AsyncMock()

    result = await confirmation.process_confirmed_payment(
        provider="platega", purchase_id="p-shield-1", amount_rubles=50.0,
        invoice_id="inv-shield-1", telegram_id=777, bot=bot,
    )

    assert result["status"] == "ok"
    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.args[1]
    assert "50.00 ₽" in text, "сумма возврата не названа"
    assert "баланс" in text.lower()
    assert "До: N/A" not in text, "покупателю ушёл текст подписки"


@pytest.mark.asyncio
async def test_webhook_confirms_applied_shield(monkeypatch):
    from app.services.payments import confirmation

    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(
        return_value={"telegram_id": 777, "purchase_type": "farm_effect",
                      "tariff": "farm_storm_shield", "price_kopecks": 5000}
    )
    fake_db.finalize_purchase = AsyncMock(return_value={
        "success": True, "payment_id": 9, "expires_at": None,
        "is_farm_effect": True, "farm_plot_id": 0,
        "farm_shield_applied": True, "farm_shield_reason": None,
        "farm_shield_refund_kopecks": 0,
    })
    # Выдача идёт через сервисный слой, а он держит собственную ссылку на
    # database. Подменяем в обоих модулях: иначе обёртка пойдёт в настоящую
    # базу, которой в тестах нет.
    monkeypatch.setattr(confirmation, "database", fake_db)
    from app.services.subscriptions import service as _subscription_service
    monkeypatch.setattr(_subscription_service, "database", fake_db)
    monkeypatch.setattr(
        "app.services.site_sync.is_enabled", lambda: False, raising=False,
    )

    bot = MagicMock()
    bot.send_message = AsyncMock()

    await confirmation.process_confirmed_payment(
        provider="lava", purchase_id="p-shield-2", amount_rubles=50.0,
        invoice_id="inv-shield-2", telegram_id=777, bot=bot,
    )

    text = bot.send_message.await_args.args[1]
    assert "Плёнка установлена" in text
    assert "Грядка 1" in text, "нумерация грядок для человека начинается с 1"


def test_finalize_refunds_when_shield_not_applied():
    """Возврат обязан идти в той же транзакции, что и запись платежа —
    иначе при сбое останется списание без компенсации."""
    from pathlib import Path

    # Ветка farm_effect живёт в _finalize_purchase_locked, а та переехала
    # в database/purchase_finalization.py при разбивке subscriptions.py.
    # В фасаде теперь только реэкспорты — index() там упадёт.
    src = Path("database/purchase_finalization.py").read_text(encoding="utf-8")
    start = src.index("if is_farm_effect:")
    block = src[start:start + 6000]
    assert "farm_shield_refund" in block, "нет возврата на баланс"
    assert "UPDATE users SET balance = balance + $1" in block
    assert "farm_shield_refund_kopecks" in block, (
        "вызывающий код не узнает о возврате и не скажет пользователю"
    )
