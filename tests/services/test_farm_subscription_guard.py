"""Ферма доступна только действующим подписчикам.

Дефект: меню игр, боулинг и кубики проверяли подписку, а вся фермерская
ветка — нет. Старое сообщение с инлайн-клавиатурой живёт в чате вечно:
подписка истекла или была возвращена, а кнопки «Собрать» по-прежнему
работают, и неплательщик продолжает начислять себе реальный баланс.

Проверка вынесена в middleware роутера фермы — одна точка входа на все
полтора десятка farm_*-обработчиков, включая будущие.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


def _event(telegram_id: int = 555):
    ev = MagicMock()
    ev.from_user = MagicMock()
    ev.from_user.id = telegram_id
    ev.answer = AsyncMock()
    return ev


@pytest.fixture
def guard():
    from app.handlers.farm import require_active_subscription
    return require_active_subscription


@pytest.mark.asyncio
async def test_blocks_user_without_active_subscription(guard, monkeypatch):
    import app.handlers.farm as farm

    monkeypatch.setattr(farm.database, "get_subscription", AsyncMock(return_value=None))
    handler = AsyncMock()
    ev = _event()

    result = await guard(handler, ev, {})

    assert result is None
    assert not handler.called, "неплательщик дошёл до обработчика фермы"
    ev.answer.assert_awaited_once()
    assert ev.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_lets_active_subscriber_through(guard, monkeypatch):
    import app.handlers.farm as farm

    monkeypatch.setattr(
        farm.database, "get_subscription",
        AsyncMock(return_value={"telegram_id": 555, "status": "active"}),
    )
    handler = AsyncMock(return_value="ok")
    ev = _event()

    assert await guard(handler, ev, {}) == "ok"
    handler.assert_awaited_once()
    ev.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_db_error_does_not_lock_out_paying_user(guard, monkeypatch):
    """Сбой базы не должен превращаться в отказ плательщику: пропускаем
    дальше, обработчик сам покажет ошибку через ensure_db_ready_callback."""
    import app.handlers.farm as farm

    monkeypatch.setattr(
        farm.database, "get_subscription",
        AsyncMock(side_effect=RuntimeError("pool is down")),
    )
    handler = AsyncMock(return_value="ok")

    assert await guard(handler, _event(), {}) == "ok"
    handler.assert_awaited_once()


def test_guard_is_registered_on_farm_router():
    """Middleware обязан висеть на роутере — иначе он просто не вызовется."""
    import app.handlers.farm as farm

    registered = list(farm.router.callback_query.middleware)
    names = [getattr(m, "__name__", "") for m in registered]
    assert "require_active_subscription" in names, (
        f"guard не зарегистрирован в роутере фермы, есть только: {names}"
    )
