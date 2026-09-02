"""
Regression: сегмент рассылки «expired_within_1y».

Любая подписка (триал/платная/gift/admin) истекла за последние 365 дней и
сейчас активной нет. Проверяем, что get_users_by_segment маршрутизирует ключ
в правильный SQL (UNION истории и текущего состояния, окно 365 дней, guard
неактивности), и что сегмент отдаётся дашбордом в списке.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from database import admin as db_admin


class _Conn:
    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, query, *args):
        self._captured["query"] = query
        self._captured["args"] = args
        return [{"telegram_id": 111}, {"telegram_id": 222}]


class _Pool:
    def __init__(self, captured):
        self._captured = captured

    def acquire(self):
        return _Conn(self._captured)


@pytest.mark.asyncio
async def test_expired_within_1y_routes_expected_sql():
    captured: dict = {}
    with patch.object(db_admin, "get_pool", AsyncMock(return_value=_Pool(captured))):
        ids = await db_admin.get_users_by_segment("expired_within_1y")

    assert ids == [111, 222]
    q = captured["query"]
    # Источник = UNION истории и текущего состояния.
    assert "subscription_history" in q
    assert "subscriptions" in q
    assert "UNION ALL" in q
    # Годовое окно.
    assert "365 days" in q
    # Guard неактивности «сейчас активной подписки нет».
    assert "NOT EXISTS" in q
    assert "expires_at >" in q


@pytest.mark.asyncio
async def test_expired_within_1y_listed_in_dashboard_segments():
    # Сегмент должен присутствовать в списке, отдаваемом дашборду
    # (endpoint считает count через get_users_by_segment — мокаем его).
    try:
        from app.api.dashboard.routes import broadcasts as br
    except BaseException as e:  # noqa: BLE001  # pyo3 PanicException — не Exception
        pytest.skip(f"dashboard broadcasts route import unavailable in env: {e}")

    async def _fake_count(key):
        return [1, 2, 3]

    with patch.object(br.database, "get_users_by_segment", AsyncMock(side_effect=_fake_count)):
        out = await br.broadcast_segments.__wrapped__() if hasattr(br.broadcast_segments, "__wrapped__") else await br.broadcast_segments()

    keys = {s["key"] for s in out}
    assert "expired_within_1y" in keys
    seg = next(s for s in out if s["key"] == "expired_within_1y")
    assert seg["group"] == "Истёкшие (любые)"
    assert seg["count"] == 3
