"""Различение причин отказа при финализации платежа.

Дефект: confirmation.py ловил любой ValueError и отвечал провайдеру
HTTP 200 already_processed. Под это правило попадали четыре разные причины
из finalize_purchase, и только одна означала законный повтор. Расхождение
суммы приводило к тому, что деньги списаны, товар не выдан, алерта нет.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_typed_exceptions_stay_value_errors():
    """Обратная совместимость: код выше по стеку ловит ValueError."""
    from database.subscriptions import (
        PaymentAlreadyProcessed,
        PaymentAmountMismatch,
        PurchaseInvalidStatus,
        PurchaseLocked,
    )
    for cls in (PaymentAlreadyProcessed, PaymentAmountMismatch,
                PurchaseInvalidStatus, PurchaseLocked):
        assert issubclass(cls, ValueError)


def test_rejection_causes_are_distinguishable():
    """Причины отказа не должны схлопываться в одну."""
    from database.subscriptions import (
        PaymentAlreadyProcessed,
        PaymentAmountMismatch,
        PurchaseInvalidStatus,
        PurchaseLocked,
    )
    assert not issubclass(PaymentAmountMismatch, PaymentAlreadyProcessed)
    assert not issubclass(PurchaseLocked, PaymentAlreadyProcessed)
    assert not issubclass(PurchaseInvalidStatus, PaymentAlreadyProcessed)


@pytest.mark.asyncio
async def test_amount_mismatch_alerts_admin_and_reports_error(monkeypatch):
    """Расхождение суммы: алерт админу и НЕ 'already_processed'."""
    from app.services.payments import confirmation
    from database.subscriptions import PaymentAmountMismatch

    alerts = []

    async def fake_alert(*args, **kwargs):
        alerts.append(kwargs or args)

    async def fake_lookup_tariff(purchase_id):
        return ("basic", 30)

    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(
        return_value={"telegram_id": 777, "purchase_type": "subscription",
                      "tariff": "basic", "price_kopecks": 49900}
    )
    fake_db.finalize_purchase = AsyncMock(
        side_effect=PaymentAmountMismatch("Payment amount mismatch: expected=499.00, actual=1.00")
    )

    # Выдача идёт через сервисный слой (subscription_service), а он держит
    # собственную ссылку на database. Подменяем в обоих модулях: иначе
    # обёртка пойдёт в настоящую базу, которой в тестах нет.
    monkeypatch.setattr(confirmation, "database", fake_db)
    from app.services.subscriptions import service as _subscription_service
    monkeypatch.setattr(_subscription_service, "database", fake_db)
    monkeypatch.setattr(confirmation, "_lookup_purchase_tariff", fake_lookup_tariff)
    monkeypatch.setattr(
        "app.services.admin_alerts.alert_payment_failure", fake_alert, raising=False
    )

    result = await confirmation.process_confirmed_payment(
        provider="platega", purchase_id="purchase_abc", amount_rubles=1.0,
        invoice_id="inv-1", telegram_id=777, bot=MagicMock(),
    )

    assert result["status"] != "already_processed", "потерянный платёж выдан за повтор"
    assert result["status"] == "error"
    assert result.get("reason") == "PaymentAmountMismatch"
    assert alerts, "админ обязан получить алерт о расхождении суммы"


@pytest.mark.asyncio
async def test_already_processed_still_returns_idempotent_status(monkeypatch):
    """Законный повтор вебхука по-прежнему отвечает already_processed."""
    from app.services.payments import confirmation
    from database.subscriptions import PaymentAlreadyProcessed

    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(
        return_value={"telegram_id": 777, "purchase_type": "subscription",
                      "tariff": "basic", "price_kopecks": 49900}
    )
    fake_db.finalize_purchase = AsyncMock(
        side_effect=PaymentAlreadyProcessed("Pending purchase already processed")
    )
    fake_db.get_subscription = AsyncMock(return_value=None)

    # Выдача идёт через сервисный слой (subscription_service), а он держит
    # собственную ссылку на database. Подменяем в обоих модулях: иначе
    # обёртка пойдёт в настоящую базу, которой в тестах нет.
    monkeypatch.setattr(confirmation, "database", fake_db)
    from app.services.subscriptions import service as _subscription_service
    monkeypatch.setattr(_subscription_service, "database", fake_db)

    result = await confirmation.process_confirmed_payment(
        provider="platega", purchase_id="purchase_abc", amount_rubles=499.0,
        invoice_id="inv-1", telegram_id=777, bot=MagicMock(),
    )

    assert result["status"] == "already_processed"
