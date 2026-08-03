"""Оплаченный «ручной» товар не должен теряться молча.

ЧТО ПРОИСХОДИТ

    Steam, Spotify, Apple ID, прокси, Telegram Premium и звёзды не
    выдаются автоматически. Вебхук провайдера помечает покупку
    оплаченной и отправляет два сообщения: подтверждение покупателю и
    карточку заказа админу с кнопкой выдачи.

ЧТО ЛОМАЛОСЬ

    Отправка стояла в try, а любое исключение просто писалось в лог, и
    функция возвращала status=ok. Telegram отдал 5xx или таймаут — и
    покупатель не увидел подтверждения, а админ не узнал о заказе.

    Повторный вебхук не спасал: mark_pending_purchase_paid вернёт False,
    и ветка отправки будет пропущена как дубль. Заказ оставался виден
    только прямым запросом в pending_purchases, и никакого сигнала
    никуда не приходило — деньги получены, товар не выдан.

ЧТО ПРОВЕРЯЕМ

    Что при сбое уведомления поднимается алерт админу со всем, что нужно
    для ручной выдачи: тип товара, покупатель, purchase_id, сумма.
"""
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payments import confirmation


PENDING = {
    "telegram_id": 777,
    "purchase_type": "steam",
    "tariff": "steam_1000",
    "price_kopecks": 100000,
    "status": "pending",
}


@pytest.mark.asyncio
async def test_failed_notification_raises_an_alert():
    alert = AsyncMock(return_value=True)

    with patch("database.get_pending_purchase_by_id", new=AsyncMock(return_value=PENDING)), \
         patch("database.mark_pending_purchase_paid", new=AsyncMock(return_value=True)), \
         patch("app.handlers.payments.steam_purchase.send_steam_success",
               new=AsyncMock(side_effect=RuntimeError("Telegram 502"))), \
         patch("app.services.admin_alerts.send_alert", new=alert):
        result = await confirmation.process_confirmed_payment(
            bot=object(), provider="platega", purchase_id="p-1",
            telegram_id=777, amount_rubles=1000.0, invoice_id="inv-1",
        )

    assert result["status"] == "ok", "оплата состоялась — это не ошибка вебхука"
    assert result.get("notification_failed") is True, (
        "провал уведомления не виден вызывающему"
    )
    alert.assert_awaited_once()

    text = alert.await_args.args[2]
    for needle in ("steam", "777", "p-1", "1000"):
        assert needle in text, f"в алерте нет {needle!r} — заказ не найти"
    assert alert.await_args.kwargs.get("force") is True, (
        "алерт может быть проглочен кулдауном"
    )


@pytest.mark.asyncio
async def test_successful_notification_raises_nothing():
    alert = AsyncMock(return_value=True)

    with patch("database.get_pending_purchase_by_id", new=AsyncMock(return_value=PENDING)), \
         patch("database.mark_pending_purchase_paid", new=AsyncMock(return_value=True)), \
         patch("app.handlers.payments.steam_purchase.send_steam_success", new=AsyncMock()), \
         patch("app.services.admin_alerts.send_alert", new=alert):
        result = await confirmation.process_confirmed_payment(
            bot=object(), provider="platega", purchase_id="p-2",
            telegram_id=777, amount_rubles=1000.0, invoice_id="inv-2",
        )

    assert result["status"] == "ok"
    assert "notification_failed" not in result
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_failure_is_logged_as_critical(caplog):
    """Последний рубеж: не ушло ни уведомление, ни алерт."""
    with patch("database.get_pending_purchase_by_id", new=AsyncMock(return_value=PENDING)), \
         patch("database.mark_pending_purchase_paid", new=AsyncMock(return_value=True)), \
         patch("app.handlers.payments.steam_purchase.send_steam_success",
               new=AsyncMock(side_effect=RuntimeError("Telegram 502"))), \
         patch("app.services.admin_alerts.send_alert",
               new=AsyncMock(side_effect=RuntimeError("bot is down"))):
        result = await confirmation.process_confirmed_payment(
            bot=object(), provider="platega", purchase_id="p-3",
            telegram_id=777, amount_rubles=1000.0, invoice_id="inv-3",
        )

    assert result["status"] == "ok"
    assert "ORDER_LOST" in caplog.text, "потерянный заказ не помечен в логах"


def test_notification_failure_does_not_swallow_the_reason():
    """Пустой лог без трейса не даёт понять, что именно упало."""
    src = inspect.getsource(confirmation.process_confirmed_payment)
    block = src[src.index("except Exception as notif_err:"):]
    block = block[:2500]
    assert "exc_info=True" in block, "причина сбоя теряется"
