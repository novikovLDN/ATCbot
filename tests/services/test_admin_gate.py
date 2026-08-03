"""Админский раздел закрыт одной проверкой на входе, а не 193 вручную.

Дефект: проверка «я админ» была написана руками 193 раза в виде
`if callback.from_user.id != config.ADMIN_TELEGRAM_ID: return` в двадцати
пяти модулях, плюс существовала в четырёх разных реализациях
(app/utils/security.is_admin, admin_auth.is_admin, require_admin, декоратор
admin_only). Достаточно один раз забыть строчку в новом обработчике, чтобы
админская операция стала доступна кому угодно, — и найти такую дыру глазами
нельзя.

Теперь весь админский роутер закрыт middleware, а ответ на вопрос «кто
админ» живёт в одном месте. Ручные проверки в обработчиках оставлены как
второй рубеж: массовая замена 193 мест — источник регрессий, у каждого свой
хвост (где-то return, где-то answer с текстом, где-то очистка FSM).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


def _event(telegram_id):
    ev = MagicMock()
    ev.from_user = MagicMock()
    ev.from_user.id = telegram_id
    return ev


@pytest.fixture
def gate():
    from app.handlers.admin import _require_admin
    return _require_admin


@pytest.mark.asyncio
async def test_admin_passes_through(gate, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 777, raising=False)
    handler = AsyncMock(return_value="ok")
    assert await gate(handler, _event(777), {}) == "ok"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_stranger_is_blocked(gate, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", 777, raising=False)
    handler = AsyncMock(return_value="ok")
    assert await gate(handler, _event(12345), {}) is None
    assert not handler.called, "посторонний дошёл до админского обработчика"


@pytest.mark.asyncio
async def test_event_without_user_is_blocked(gate):
    """Событие без from_user — не повод пускать: fail closed."""
    ev = MagicMock()
    ev.from_user = None
    handler = AsyncMock(return_value="ok")
    assert await gate(handler, ev, {}) is None
    assert not handler.called


def test_gate_is_registered_on_both_event_types():
    """Middleware обязан висеть и на сообщениях, и на колбэках — иначе
    половина админских операций останется без проверки."""
    from app.handlers.admin import router

    for observer, label in ((router.message, "message"), (router.callback_query, "callback_query")):
        names = [getattr(m, "__name__", "") for m in observer.middleware]
        assert "_require_admin" in names, f"нет проверки админа на {label}"


def test_single_source_of_truth_for_is_admin():
    """У security.is_admin не должно быть собственного сравнения с
    ADMIN_TELEGRAM_ID: иначе две реализации однажды разойдутся."""
    import inspect

    from app.utils import security

    src = inspect.getsource(security.is_admin)
    assert "admin_auth" in src, "проверка снова отвязалась от единого источника"
    assert "config.ADMIN_TELEGRAM_ID" not in src, "вернулось собственное сравнение"


def test_require_admin_delegates_too():
    import inspect

    from app.utils import security

    src = inspect.getsource(security.require_admin)
    assert "is_admin(" in src
    assert "config.ADMIN_TELEGRAM_ID" not in src
