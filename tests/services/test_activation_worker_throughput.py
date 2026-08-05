"""Воркер активации обязан разгребать очередь, а не 30 записей за виток.

Дефект: на каждую обработанную подписку безусловно делался
await asyncio.sleep(0.5) при бюджете итерации 15 с и выборке 50 строк. Это
давало ~30 активаций за виток и потолок ~360 в час независимо от того, как
быстро отвечает панель.

Почему важно: всплеск покупок (рассылка со скидкой) или восстановление после
недоступности панели копит сотни pending-подписок. Полтора часа люди ждут
оплаченный ключ, а каждая неудачная попытка ещё и жжёт одну из пяти.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

import activation_worker as aw
import config
import database
from app.services.activation import service as activation_service


class _FakeConn:
    """Соединение, которого хватает воркеру: проверка статуса и счётчик."""

    async def fetchrow(self, query, *args):
        return {
            "activation_status": "active",
            "uuid": f"uuid-{args[0] if args else 0}",
            "subscription_type": "basic",
        }

    async def fetchval(self, query, *args):
        return 0


class _FakeAcquire:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *exc):
        return False


def _pending(count):
    now = time.time()
    return [
        SimpleNamespace(
            telegram_id=1000 + i,
            subscription_id=i,
            activation_attempts=0,
            expires_at=None,
            amount_rubles=199,
            subscription_type="basic",
            period_days=30,
            is_combo=False,
        )
        for i in range(count)
    ]


@pytest.fixture
def worker_env(monkeypatch):
    """Полностью синтетическое окружение: ни БД, ни панели, ни Telegram."""
    sent = []

    monkeypatch.setattr(database, "DB_READY", True)
    monkeypatch.setattr(config, "VPN_ENABLED", True)

    async def _get_pool():
        return object()

    monkeypatch.setattr(database, "get_pool", _get_pool)
    monkeypatch.setattr(aw, "acquire_connection", _FakeAcquire)
    monkeypatch.setattr(activation_service, "is_subscription_expired", lambda _e: False)

    async def _no_notifications(*args, **kwargs):
        return []

    monkeypatch.setattr(activation_service, "get_pending_for_notification", _no_notifications)

    async def _attempt(subscription_id, telegram_id, current_attempts, pool):
        return SimpleNamespace(uuid=f"uuid-{subscription_id}", attempts=current_attempts + 1)

    monkeypatch.setattr(activation_service, "attempt_activation", _attempt)

    async def _lang(_tg):
        return "ru"

    monkeypatch.setattr(aw, "resolve_user_language", _lang)

    async def _send(bot, chat_id, text, **kwargs):
        sent.append(chat_id)
        return True

    monkeypatch.setattr(aw, "safe_send_message", _send)
    # Паузу за отправку сообщения обнуляем: тест меряет НЕ её, а то, что
    # воркер не платит фиксированный штраф за каждую запись очереди.
    monkeypatch.setattr(aw, "NOTIFICATION_PAUSE_SECONDS", 0.0)
    return sent


async def test_whole_batch_is_processed_in_one_iteration(monkeypatch, worker_env):
    """120 подписок разбираются за виток целиком.

    Со старым sleep(0.5) виток упирался в бюджет 15 с примерно на тридцатой
    записи, и остальные 90 ждали следующего тика через 5 минут.
    """
    batch = _pending(120)

    async def _get_pending(**kwargs):
        return batch

    monkeypatch.setattr(activation_service, "get_pending_subscriptions", _get_pending)

    started = time.monotonic()
    processed, outcome, error_type = await aw.process_pending_activations(bot=object())
    elapsed = time.monotonic() - started

    assert processed == 120, "очередь разобрана не полностью"
    assert outcome == "success"
    assert error_type is None, "успешный виток не должен нести тип ошибки"
    assert elapsed < 2.0, f"виток занял {elapsed:.1f}s — где-то остался фиксированный sleep"
    assert len(worker_env) == 120, "не всем отправлено уведомление об активации"


async def test_no_fixed_pause_per_subscription(monkeypatch, worker_env):
    """Пауза платится только за реально отправленное сообщение.

    Проверяем напрямую: считаем длительности всех asyncio.sleep внутри витка
    при отключённых уведомлениях — их суммарно должно быть около нуля.
    """
    batch = _pending(40)

    async def _get_pending(**kwargs):
        return batch

    monkeypatch.setattr(activation_service, "get_pending_subscriptions", _get_pending)

    # Сообщения не уходят — значит и паузы Telegram быть не должно.
    async def _send_fails(*args, **kwargs):
        return None

    monkeypatch.setattr(aw, "safe_send_message", _send_fails)
    monkeypatch.setattr(aw, "NOTIFICATION_PAUSE_SECONDS", 0.5)

    delays = []
    real_sleep = asyncio.sleep

    async def _tracking_sleep(seconds, *args, **kwargs):
        delays.append(seconds)
        return await real_sleep(0, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", _tracking_sleep)
    try:
        processed, _, _ = await aw.process_pending_activations(bot=object())
    finally:
        monkeypatch.undo()

    assert processed == 40
    assert sum(delays) == 0, f"воркер спит без причины: {sum(delays)}s на 40 записей"


def test_iteration_budget_fits_under_the_hard_timeout():
    """Бюджет витка должен оставаться меньше внешнего wait_for(120).

    Иначе мягкий выход с логом «time limit reached» никогда не сработает, и
    итерацию будет рубить жёсткая отмена задачи — без понятной причины в логах.
    """
    assert aw.MAX_ITERATION_SECONDS >= 60, "бюджет витка снова душит очередь"
    assert aw.MAX_ITERATION_SECONDS < 120


def test_batch_limit_is_configurable():
    """Размер выборки не должен быть зашит числом: очередь бывает разной."""
    assert aw.ACTIVATION_BATCH_LIMIT >= 200
