"""Комбо через внешнего провайдера: отказ панели больше не съедает пакет ГБ.

БОЕВОЙ ДЕФЕКТ
    Человек платит за комбо через Platega / CryptoBot / Lava. Покупка
    проводится, подписка выдаётся, а на начислении гигабайтов панель
    Remnawave моргает. _send_confirmation бросает TransientPaymentError —
    расчёт был на то, что вебхук ответит 5xx и провайдер повторит запрос.
    Не работало ничего из этого:

    1. Исключение ловилось на два уровня выше (`except Exception` в конце
       цепочки process_confirmed_payment), вебхук отвечал 200, и повтора не
       было. «webhook will retry» никогда не было правдой.
    2. Даже случись повтор — покупка уже в статусе 'paid', и повторный
       вебхук останавливался на lookup_pending_purchase, который такие
       строки не отдаёт вовсе: провайдер получал 200 not_found.
    3. А ветка PaymentAlreadyProcessed, которая ловит повтор-гонку,
       пересинхронизировала только премиум-подписку. Про гигабайты она не
       знала.

    Итог: оплаченные ГБ терялись навсегда, оставалась строка в логе.

ЧЕГО СТОИТ ПОЧИНКА
    add_bypass_traffic ПРИБАВЛЯЕТ трафик. Включённый повтор без защиты —
    это раздача вторых пакетов, а она не видна: на лишние гигабайты не
    жалуются. Поэтому у начисления есть ключ идемпотентности (purchase_id,
    traffic_purchases.purchase_id, миграция 075), и здесь проверяется, что
    повтор доначисляет недостающее и НЕ выдаёт второе.

    Тесты на сам ключ — в tests/services/test_combo_traffic.py.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config


COMBO_GB = config.COMBO_TARIFFS["combo_plus"][30]["gb"]

COMBO_PENDING = {
    "telegram_id": 777,
    "purchase_id": "p-combo-1",
    "purchase_type": "subscription",
    "tariff": "plus",
    "period_days": 30,
    "price_kopecks": 49900,
    "is_combo": True,
    "status": "pending",
}

COMBO_FINALIZED = {
    "success": True,
    "payment_id": 11,
    "expires_at": None,
    "subscription_type": "plus",
    "period_days": 30,
    "is_combo": True,
}


@pytest.fixture
def panel(monkeypatch):
    """Панель Remnawave и учёт покупок трафика — как в жизни.

    add_bypass_traffic прибавляет гигабайты (total_bytes), запись покупки
    занимает ключ идемпотентности (keys), проверка ключа читает его же.
    Второй пакет здесь видно как удвоенный объём.
    """
    import database

    state = SimpleNamespace(ok=True, total_bytes=0, keys=set(), grants=0)

    async def _add(telegram_id, extra_bytes, **kwargs):
        state.grants += 1
        if not state.ok:
            return False
        state.total_bytes += extra_bytes
        return True

    async def _record(telegram_id, gb, price, purchase_id=None):
        if purchase_id is not None:
            state.keys.add(purchase_id)
        return 1

    async def _already(purchase_id):
        return purchase_id in state.keys

    async def _flag(telegram_id, value=True):
        return None

    monkeypatch.setattr(
        "app.services.remnawave_service.add_bypass_traffic", _add, raising=False
    )
    monkeypatch.setattr(database, "record_traffic_purchase", _record)
    # raising=False: проверка ключа появилась вместе с починкой, и без неё
    # эти тесты должны падать на СВОЁМ утверждении (200 вместо 500,
    # not_found вместо повтора), а не на отсутствующем атрибуте.
    monkeypatch.setattr(database, "combo_traffic_already_granted", _already, raising=False)
    monkeypatch.setattr(database, "set_combo_flag", _flag)
    return state


def _patch_confirmation_db(monkeypatch, *, pending=None, paid=None, finalize=None):
    """Заменить базу на всём пути подтверждения оплаты.

    Сервисный слой держит собственную ссылку на database — патчим в обоих
    модулях, иначе finalize_purchase пойдёт в настоящую базу.
    """
    from app.services.payments import confirmation
    from app.services.subscriptions import service as subscription_service

    fake_db = MagicMock()
    fake_db.get_pending_purchase_by_id = AsyncMock(return_value=pending)
    fake_db.get_paid_purchase_by_id = AsyncMock(return_value=paid)
    fake_db.get_subscription = AsyncMock(return_value=None)
    if isinstance(finalize, Exception):
        fake_db.finalize_purchase = AsyncMock(side_effect=finalize)
    else:
        fake_db.finalize_purchase = AsyncMock(return_value=finalize)
    monkeypatch.setattr(confirmation, "database", fake_db)
    monkeypatch.setattr(subscription_service, "database", fake_db)
    return fake_db


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch):
    """Уведомления, синк на сайт и алерты в этих тестах не участвуют."""
    async def _language(_tg):
        return "ru"

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.language_service.resolve_user_language", _language, raising=False
    )
    monkeypatch.setattr("app.services.site_sync.is_enabled", lambda: False, raising=False)
    monkeypatch.setattr(
        "app.handlers.notifications.notify_referral_cashback", _noop, raising=False
    )
    monkeypatch.setattr(
        "app.services.remnawave_service.renew_remnawave_user_bg",
        lambda *a, **k: None, raising=False,
    )


# ─────────────────────────────────────────────────────────────────────
# 1. Отказ начисления доводится до 5xx
# ─────────────────────────────────────────────────────────────────────

async def test_failed_grant_raises_instead_of_reporting_success(monkeypatch, panel):
    """process_confirmed_payment обязан выпустить сигнал на повтор наружу.

    Раньше он гасился на два уровня выше: сначала веткой «уведомление не
    ушло», потом общим `except Exception` — провайдеру уходило 200.
    """
    from app.services.payments import confirmation

    _patch_confirmation_db(monkeypatch, pending=COMBO_PENDING, finalize=COMBO_FINALIZED)
    panel.ok = False

    with pytest.raises(confirmation.TransientPaymentError):
        await confirmation.process_confirmed_payment(
            provider="platega", purchase_id="p-combo-1", amount_rubles=499.0,
            invoice_id="inv-1", telegram_id=777, bot=MagicMock(),
        )


def test_webhook_answers_5xx_when_the_panel_refuses(monkeypatch, panel):
    """Сквозь весь путь: HTTP-запрос от Platega → 500.

    Проверяется именно ответ, а не место броска: перехватов по дороге
    несколько, и достаточно одного лишнего, чтобы провайдер снова получил
    200 и не повторил.
    """
    import database
    import platega_service
    from app.api import payment_webhook

    _patch_confirmation_db(monkeypatch, pending=COMBO_PENDING, finalize=COMBO_FINALIZED)
    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(platega_service, "PLATEGA_MERCHANT_ID", "merchant", raising=False)
    monkeypatch.setattr(platega_service, "PLATEGA_SECRET", "secret", raising=False)
    monkeypatch.setattr(payment_webhook, "_bot", MagicMock())
    panel.ok = False

    app = FastAPI()
    app.include_router(payment_webhook.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/webhooks/platega",
        headers={"X-MerchantId": "merchant", "X-Secret": "secret"},
        json={
            "id": "tx-1",
            "status": "CONFIRMED",
            "payload": json.dumps({"purchase_id": "p-combo-1"}),
            "paymentDetails": {"amount": 499.0},
        },
    )

    assert response.status_code == 500, (
        "провайдер получил 200 и не повторит запрос — оплаченные ГБ потеряны"
    )


async def test_delivery_failure_does_not_skip_the_site_sync(monkeypatch, panel):
    """Сигнал на повтор поднимается ПОСЛЕ шагов, которые к выдаче не относятся.

    Повторный вебхук останавливается на lookup_pending_purchase и до синка
    на сайт не доходит никогда — значит, у этого прогона он единственный.
    Бросок сразу из ветки отказа тихо выкидывал бы его.
    """
    from app.services.payments import confirmation

    _patch_confirmation_db(monkeypatch, pending=COMBO_PENDING, finalize=COMBO_FINALIZED)
    panel.ok = False

    scheduled = []

    def _capture(coro):
        # Корутину не исполняем — снаружи нет ни базы, ни сети; важно, что
        # её вообще создали и поставили в очередь.
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(confirmation.asyncio, "ensure_future", _capture)
    monkeypatch.setattr("app.services.site_sync.is_enabled", lambda: True, raising=False)

    with pytest.raises(confirmation.TransientPaymentError):
        await confirmation.process_confirmed_payment(
            provider="lava", purchase_id="p-combo-1", amount_rubles=499.0,
            invoice_id="inv-2", telegram_id=777, bot=MagicMock(),
        )

    assert scheduled, "синк на сайт пропущен: повтор вебхука до него не дойдёт"


def test_webhook_answers_200_when_the_traffic_was_granted(monkeypatch, panel):
    """Обратная сторона: успешная выдача не должна просить о повторе."""
    import database
    import platega_service
    from app.api import payment_webhook

    _patch_confirmation_db(monkeypatch, pending=COMBO_PENDING, finalize=COMBO_FINALIZED)
    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(platega_service, "PLATEGA_MERCHANT_ID", "merchant", raising=False)
    monkeypatch.setattr(platega_service, "PLATEGA_SECRET", "secret", raising=False)
    monkeypatch.setattr(payment_webhook, "_bot", MagicMock())

    app = FastAPI()
    app.include_router(payment_webhook.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/webhooks/platega",
        headers={"X-MerchantId": "merchant", "X-Secret": "secret"},
        json={
            "id": "tx-2",
            "status": "CONFIRMED",
            "payload": json.dumps({"purchase_id": "p-combo-1"}),
            "paymentDetails": {"amount": 499.0},
        },
    )

    assert response.status_code == 200
    assert panel.total_bytes == COMBO_GB * 1024 ** 3


# ─────────────────────────────────────────────────────────────────────
# 2. Повторный вебхук доначисляет то, что не доехало
# ─────────────────────────────────────────────────────────────────────

async def test_replay_of_a_finalized_purchase_tops_up_the_traffic(monkeypatch, panel):
    """Второй заход провайдера по уже проведённой покупке.

    Это и есть настоящий повтор: первый вебхук успел провести покупку
    (status='paid') и не успел выдать гигабайты. Раньше повтор упирался в
    lookup_pending_purchase — тот отдаёт только 'pending'/'expired', и всё
    останавливалось на «purchase not found» с ответом 200.
    """
    from app.services.payments import confirmation

    paid = dict(COMBO_PENDING, status="paid")
    _patch_confirmation_db(monkeypatch, pending=None, paid=paid)

    result = await confirmation.lookup_pending_purchase("platega", "p-combo-1")

    assert result["status"] == "already_processed"
    assert panel.total_bytes == COMBO_GB * 1024 ** 3, (
        "повтор не доначислил гигабайты, не доехавшие с первого раза"
    )


async def test_replay_does_not_grant_a_second_package(monkeypatch, panel):
    """Провайдеры шлют дубли и без всяких отказов.

    Пакет уже выдан — повтор обязан пройти мимо панели. Иначе включённый
    повтор превращается в бесплатную раздачу, которой никто не заметит.
    """
    from app.services.payments import confirmation

    paid = dict(COMBO_PENDING, status="paid")
    _patch_confirmation_db(monkeypatch, pending=None, paid=paid)

    await confirmation.lookup_pending_purchase("platega", "p-combo-1")
    await confirmation.lookup_pending_purchase("platega", "p-combo-1")
    await confirmation.lookup_pending_purchase("platega", "p-combo-1")

    assert panel.total_bytes == COMBO_GB * 1024 ** 3, "выдан второй пакет"
    assert panel.grants == 1, "панель дёргали повторно, хотя пакет уже выдан"


async def test_replay_asks_for_another_retry_while_the_panel_is_down(monkeypatch, panel):
    """Если и на повторе не вышло — просим повторить ещё раз, а не молчим."""
    from app.services.payments import confirmation

    paid = dict(COMBO_PENDING, status="paid")
    _patch_confirmation_db(monkeypatch, pending=None, paid=paid)
    panel.ok = False

    with pytest.raises(confirmation.TransientPaymentError):
        await confirmation.lookup_pending_purchase("platega", "p-combo-1")


async def test_replay_ignores_purchases_without_combo(monkeypatch, panel):
    """Обычная подписка гигабайтов не получает — ни с первого раза, ни с повтора."""
    from app.services.payments import confirmation

    paid = dict(COMBO_PENDING, status="paid", is_combo=False)
    _patch_confirmation_db(monkeypatch, pending=None, paid=paid)

    result = await confirmation.lookup_pending_purchase("platega", "p-combo-1")

    assert result["status"] == "already_processed"
    assert panel.grants == 0


async def test_unknown_purchase_is_still_not_found(monkeypatch, panel):
    """Платёж по несуществующей покупке остаётся ошибкой, а не повтором."""
    from app.services.payments import confirmation

    _patch_confirmation_db(monkeypatch, pending=None, paid=None)

    result = await confirmation.lookup_pending_purchase("platega", "p-nope")

    assert result["status"] == "not_found"
    assert panel.grants == 0


async def test_concurrent_webhook_replay_tops_up_the_traffic(monkeypatch, panel):
    """Вторая ветка повтора: гонка двух вебхуков.

    Оба прошли lookup до того, как первый закоммитил покупку, поэтому
    второй доходит до finalize_purchase и получает PaymentAlreadyProcessed.
    Эта ветка умела ресинкать премиум-подписку и ничего не знала про
    гигабайты — повтор был бесполезен ровно там, где он и нужен.
    """
    from app.services.payments import confirmation
    from database.subscriptions import PaymentAlreadyProcessed

    _patch_confirmation_db(
        monkeypatch,
        pending=COMBO_PENDING,
        finalize=PaymentAlreadyProcessed("already"),
    )

    result = await confirmation.process_confirmed_payment(
        provider="cryptobot", purchase_id="p-combo-1", amount_rubles=499.0,
        invoice_id="inv-3", telegram_id=777, bot=MagicMock(),
    )

    assert result["status"] == "already_processed"
    assert panel.total_bytes == COMBO_GB * 1024 ** 3, (
        "повтор-гонка не доначислил комбо-гигабайты"
    )
