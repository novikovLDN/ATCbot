"""Перевыпуск ключа не должен держать соединение пула во время похода в панель.

Дефект: reissue_vpn_key_atomic брала соединение из пула, вешала session-level
pg_advisory_lock и внутри этого блока делала DELETE + preflight + POST к
Remnawave — до нескольких секунд на пользователя. Массовый перевыпуск или пара
админов одновременно выедали пул, и деградировали пользовательские хендлеры.
Правило проекта прямо противоположное (см. POOL_STABILITY в
app/services/activation/service.py и fast_expiry_cleanup.py).

Второй сюжет: сняв session-lock, мы потеряли защиту от параллельного
перевыпуска на время сетевой фазы. Взамен под xact-локом сверяется uuid — если
строку успел переписать другой перевыпуск, наш UPDATE не применяется, а свежая
панельная сущность удаляется, чтобы не остаться сиротой.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _Tracker:
    def __init__(self):
        self.held = 0          # сколько соединений пула занято прямо сейчас
        self.max_held = 0
        self.held_during_panel_call = None
        self.acquires = 0
        self.executed = []


class _FakeTransaction:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, tracker, rows):
        self._tracker = tracker
        self._rows = rows

    def transaction(self):
        return _FakeTransaction(self)

    async def execute(self, sql, *args):
        self._tracker.executed.append(sql.strip())
        return "OK"

    async def fetchrow(self, sql, *args):
        return self._rows.pop(0) if self._rows else None

    async def fetchval(self, sql, *args):
        return None

    async def fetch(self, sql, *args):
        return []


class _Acquire:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        t = self._pool.tracker
        t.acquires += 1
        t.held += 1
        t.max_held = max(t.max_held, t.held)
        return _FakeConn(t, self._pool.rows)

    async def __aexit__(self, exc_type, exc, tb):
        self._pool.tracker.held -= 1
        return False


class _FakePool:
    def __init__(self, tracker, rows):
        self.tracker = tracker
        self.rows = rows

    def acquire(self):
        return _Acquire(self)


def _install(monkeypatch, rows):
    """Подменить пул и всё, что ходит наружу, вернув трекер занятости пула.

    Подменяем атрибуты database.subscription_reissue — модуля, где
    reissue_vpn_key_atomic ОПРЕДЕЛЕНА. Через database.subscriptions она
    по-прежнему доступна, но патч по имени пакета-фасада не подействует:
    функция берёт get_pool из своего пространства имён. Тест при этом
    продолжит проходить, проверяя не тот код, — поэтому патчим по месту.
    """
    import database.subscription_reissue as subs
    from app.services import remnawave_premium, remnawave_api

    tracker = _Tracker()
    pool = _FakePool(tracker, rows)
    monkeypatch.setattr(subs, "get_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(subs, "_log_subscription_history_atomic", AsyncMock(return_value=None))
    monkeypatch.setattr(subs, "_log_audit_event_atomic", AsyncMock(return_value=None))
    monkeypatch.setattr(subs, "_log_vpn_lifecycle_audit_async", AsyncMock(return_value=None))

    async def fake_reissue(telegram_id, **kwargs):
        # Ключевая проверка: в момент HTTP к панели соединений быть не должно.
        tracker.held_during_panel_call = tracker.held
        return SimpleNamespace(
            ok=True, panel_uuid="panel-new", subscription_url="https://rmnw/sub/new",
            short_uuid="sn", status=201, error=None,
        )

    monkeypatch.setattr(remnawave_premium, "reissue_premium_user_entity", fake_reissue)
    deleted = AsyncMock(return_value=True)
    monkeypatch.setattr(remnawave_api, "delete_user", deleted)
    return subs, tracker, deleted


def _active_row(uuid="old-uuid"):
    return {
        "telegram_id": 42,
        "uuid": uuid,
        "vpn_key": "vless://old",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=10),
        "subscription_type": "basic",
    }


@pytest.mark.asyncio
async def test_panel_call_happens_without_a_pool_connection(monkeypatch):
    """Во время запроса к Remnawave соединение пула не удерживается."""
    rows = [_active_row(), {"telegram_id": 42, "uuid": "old-uuid"}]
    subs, tracker, _ = _install(monkeypatch, rows)

    new_key, old_key = await subs.reissue_vpn_key_atomic(42, admin_telegram_id=1)

    assert new_key == "https://rmnw/sub/new"
    assert old_key == "vless://old"
    assert tracker.held_during_panel_call == 0, "соединение пула удерживалось во время HTTP"
    assert tracker.max_held == 1, "фазы должны брать соединение по очереди"
    assert tracker.acquires == 2, "ожидались две короткие фазы работы с базой"


@pytest.mark.asyncio
async def test_no_session_level_advisory_lock_left(monkeypatch):
    """Session-lock снят: он охватывал сетевую фазу и держал соединение."""
    rows = [_active_row(), {"telegram_id": 42, "uuid": "old-uuid"}]
    subs, tracker, _ = _install(monkeypatch, rows)

    await subs.reissue_vpn_key_atomic(42, admin_telegram_id=1)

    statements = " ".join(tracker.executed)
    assert "pg_advisory_lock" not in statements
    assert "pg_advisory_unlock" not in statements
    assert "pg_advisory_xact_lock" in statements, "мутацию базы всё ещё нужно сериализовать"


@pytest.mark.asyncio
async def test_concurrent_reissue_is_detected_and_leaves_no_orphan(monkeypatch):
    """Пока мы ходили в панель, ключ перевыпустил кто-то ещё.

    Применять свой UPDATE поверх чужого нельзя: человек получит ключ, который
    через секунду затрут, а чужая панельная сущность останется сиротой.
    Отказываемся и удаляем СВОЮ свежесозданную сущность.
    """
    rows = [_active_row(), {"telegram_id": 42, "uuid": "someone-elses-uuid"}]
    subs, _, deleted = _install(monkeypatch, rows)

    with pytest.raises(Exception, match="Concurrent reissue"):
        await subs.reissue_vpn_key_atomic(42, admin_telegram_id=1)

    deleted.assert_awaited_once_with("panel-new")
