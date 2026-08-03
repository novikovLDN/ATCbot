"""Финализация покупки идёт только через сервисный слой.

Дефект: часть кода звала database.finalize_purchase напрямую, в обход
app.services.subscriptions.service.finalize_purchase. Обход дорог не сам по
себе — мимо сервиса проходят его гарантии:

  • сервис проверяет, что result непустой и success=True, и превращает
    «тихую неудачу» в PaymentFinalizationError; прямой вызов возвращал
    словарь, который вызывающий мог не проверить;
  • сервис отличает доменные ошибки БД (PaymentAlreadyProcessed,
    PaymentAmountMismatch, PurchaseInvalidStatus, PurchaseLocked) от прочих
    и пробрасывает их как есть — на каждую вызывающий отвечает по-своему;
  • сервис пишет единый лог финализации.

Повторная оплата и повторный вебхук — штатная ситуация у Telegram, поэтому
два разных пути финализации означают два разных поведения на одном и том же
событии.

Второй дефект того же корня: экран профиля звал
database.check_and_disable_expired_subscription напрямую. Внутри этой
функции сетевой вызов в Remnawave; его таймаут ронял весь обработчик, и
человек, нажав «Профиль», не получал ничего. Обёртка
subscription_service.disable_if_expired сбой гасит.
"""
import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers.payments import goods_delivery as goods
from app.services.subscriptions import service as subscription_service


GIFT_PENDING = {
    "purchase_type": "gift",
    "tariff": "plus",
    "period_days": 90,
    "price_kopecks": 99900,
}

GIFT_RESULT = {
    "success": True,
    "payment_id": 42,
    "is_gift": True,
    "gift_code": "GIFT-XYZ-123",
    "gift_tariff": "plus",
    "gift_period_days": 90,
}

TRAFFIC_PENDING = {
    "purchase_type": "traffic_pack",
    "tariff": "traffic_50",
    "period_days": 0,
    "price_kopecks": 19900,
}


def _paid(pending: dict) -> goods.PaidPurchase:
    message = MagicMock()
    message.answer = AsyncMock()
    message.bot = MagicMock()
    state = MagicMock()
    state.clear = AsyncMock()
    return goods.PaidPurchase(
        message=message,
        state=state,
        telegram_id=777,
        language="ru",
        purchase_id="p-1",
        pending_purchase=pending,
        payment_amount_rubles=999.0,
        is_stars_payment=False,
        start_time=0.0,
    )


def _patch_db(monkeypatch, finalize_result):
    """Подменяем database в обоих модулях: и в вызывающем, и в сервисе.

    Сервисная обёртка держит собственную ссылку на database — если подменить
    только в goods_delivery, обёртка пойдёт в настоящую базу, которой в
    тестах нет.
    """
    fake_db = MagicMock()
    fake_db.finalize_purchase = AsyncMock(return_value=finalize_result)
    monkeypatch.setattr(goods, "database", fake_db)
    monkeypatch.setattr(subscription_service, "database", fake_db)
    return fake_db


@pytest.mark.asyncio
async def test_gift_delivery_goes_through_service(monkeypatch):
    """Подарок финализируется сервисом, а не прямым вызовом БД."""
    fake_db = _patch_db(monkeypatch, GIFT_RESULT)

    called = {}
    real_finalize = subscription_service.finalize_purchase

    async def spy(**kwargs):
        called.update(kwargs)
        return await real_finalize(**kwargs)

    monkeypatch.setattr(goods, "subscription_service", MagicMock(finalize_purchase=spy))

    sent = {}

    async def fake_send_gift_success(*, bot, telegram_id, language, gift_code,
                                     tariff, period_days):
        sent.update(gift_code=gift_code, telegram_id=telegram_id)

    import app.handlers.callbacks.gift as gift_module
    monkeypatch.setattr(gift_module, "_send_gift_success", fake_send_gift_success)

    assert await goods.deliver_gift(_paid(GIFT_PENDING)) is True
    assert called["purchase_id"] == "p-1", "сервис вызван не для той покупки"
    assert sent["gift_code"] == "GIFT-XYZ-123"
    assert fake_db.finalize_purchase.await_count == 1


@pytest.mark.asyncio
async def test_gift_delivery_survives_service_error(monkeypatch):
    """Тихая неудача БД превращается сервисом в исключение — и не молчит.

    Прямой вызов возвращал бы {"success": False}, и без явной проверки
    покупатель увидел бы экран успеха без подарка.
    """
    _patch_db(monkeypatch, {"success": False})

    paid = _paid(GIFT_PENDING)
    assert await goods.deliver_gift(paid) is True
    paid.message.answer.assert_awaited()  # пользователю сказали про ошибку


@pytest.mark.asyncio
async def test_traffic_pack_goes_through_service(monkeypatch):
    """Пакет трафика тоже финализируется сервисом."""
    fake_db = _patch_db(
        monkeypatch,
        {"success": True, "payment_id": 7, "is_traffic_pack": True, "traffic_gb": 50},
    )
    fake_db.get_remnawave_uuid = AsyncMock(return_value=None)
    fake_db.clear_remnawave_uuid = AsyncMock()
    fake_db.ensure_bypass_only_subscription = AsyncMock()

    assert await goods.deliver_traffic_pack(_paid(TRAFFIC_PENDING)) is True
    assert fake_db.finalize_purchase.await_count == 1


def _direct_db_finalize_calls(path: Path) -> list[int]:
    """Строки, где вызывается database.finalize_purchase напрямую."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "finalize_purchase"
            and isinstance(f.value, ast.Name)
            and f.value.id == "database"
        ):
            hits.append(node.lineno)
    return hits


def test_only_the_service_talks_to_database_finalize():
    """Единственный прямой вызов database.finalize_purchase — внутри обёртки.

    Сторож против отката: добавить обход сервиса легко и незаметно, а цена
    — расхождение поведения на повторной оплате.
    """
    allowed = Path("app/services/subscriptions/service.py")
    offenders = {}
    for path in Path("app").rglob("*.py"):
        if path == allowed:
            continue
        lines = _direct_db_finalize_calls(path)
        if lines:
            offenders[str(path)] = lines
    assert not offenders, (
        f"прямой вызов database.finalize_purchase в обход сервиса: {offenders}"
    )


@pytest.mark.asyncio
async def test_disable_if_expired_swallows_remnawave_failure(monkeypatch):
    """Сбой отключения истёкшей подписки не должен ронять экран профиля."""
    fake_db = MagicMock()
    fake_db.check_and_disable_expired_subscription = AsyncMock(
        side_effect=TimeoutError("Remnawave не ответил")
    )
    monkeypatch.setattr(subscription_service, "database", fake_db)

    assert await subscription_service.disable_if_expired(777) is False


@pytest.mark.asyncio
async def test_disable_if_expired_returns_db_result(monkeypatch):
    """В обычном случае обёртка ничего не меняет — возвращает ответ БД."""
    fake_db = MagicMock()
    fake_db.check_and_disable_expired_subscription = AsyncMock(return_value=True)
    monkeypatch.setattr(subscription_service, "database", fake_db)

    assert await subscription_service.disable_if_expired(777) is True


def test_profile_screen_uses_the_wrapper():
    """Экран профиля не лезет в БД мимо сервиса."""
    src = Path("app/handlers/callbacks/subscription.py").read_text(encoding="utf-8")
    assert "database.check_and_disable_expired_subscription" not in src
    assert "subscription_service.disable_if_expired" in src
