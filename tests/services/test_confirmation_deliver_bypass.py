"""
Regression tests for confirmation._deliver_bypass_gb.

Ловят баг: combo/renewal-платёж продлевал срок, но НЕ начислял ГБ обхода,
если у юзера ещё нет bypass entity (top-up-only путь молча возвращал False).
_deliver_bypass_gb должен создавать entity в этом случае (как traffic-pack).

Три ветки:
  1. entity есть по кешу → top-up.
  2. entity нет по кешу, но есть в панели → self-heal + top-up.
  3. entity нет нигде → create fresh + персист кеша.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payments import confirmation

GB = 1024 ** 3


@pytest.mark.asyncio
async def test_deliver_topup_when_entity_exists():
    add = AsyncMock(return_value=True)
    create = AsyncMock()
    # Патчим через модуль импортируемых имён внутри функции.
    with patch("app.services.remnawave_bypass.add_bypass_traffic", add), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", create), \
         patch("app.services.remnawave_api.get_bypass_entity_safe", AsyncMock()):
        ok = await confirmation._deliver_bypass_gb(42, 75 * GB)
    assert ok is True
    add.assert_awaited_once_with(42, extra_bytes=75 * GB)
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_self_heal_then_topup():
    # Первый top-up False (кеш пуст), панель отдаёт entity → второй top-up True.
    add = AsyncMock(side_effect=[False, True])
    create = AsyncMock()
    get_safe = AsyncMock(return_value={"id": 7, "username": "42"})
    with patch("app.services.remnawave_bypass.add_bypass_traffic", add), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", create), \
         patch("app.services.remnawave_api.get_bypass_entity_safe", get_safe):
        ok = await confirmation._deliver_bypass_gb(42, 75 * GB)
    assert ok is True
    assert add.await_count == 2
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_creates_when_no_entity_anywhere():
    # THE BUG: renewal/combo без bypass entity → должны СОЗДАТЬ, а не терять ГБ.
    add = AsyncMock(return_value=False)
    get_safe = AsyncMock(return_value=None)
    create = AsyncMock(return_value=SimpleNamespace(
        ok=True,
        panel_uuid="uuid-x",
        subscription_url="https://sub/x",
        short_uuid="short-x",
        panel_id=99,
    ))
    set_cache = AsyncMock()
    set_id = AsyncMock()
    with patch("app.services.remnawave_bypass.add_bypass_traffic", add), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", create), \
         patch("app.services.remnawave_api.get_bypass_entity_safe", get_safe), \
         patch.object(confirmation.database, "set_remnawave_bypass_cache", set_cache), \
         patch.object(confirmation.database, "set_remnawave_id", set_id):
        ok = await confirmation._deliver_bypass_gb(42, 75 * GB)
    assert ok is True
    create.assert_awaited_once_with(42, traffic_limit_bytes=75 * GB)
    set_cache.assert_awaited_once()
    set_id.assert_awaited_once_with(42, 99)


@pytest.mark.asyncio
async def test_deliver_returns_false_when_create_fails():
    add = AsyncMock(return_value=False)
    get_safe = AsyncMock(return_value=None)
    create = AsyncMock(return_value=SimpleNamespace(
        ok=False, panel_uuid=None, subscription_url=None, short_uuid=None, panel_id=None,
    ))
    with patch("app.services.remnawave_bypass.add_bypass_traffic", add), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", create), \
         patch("app.services.remnawave_api.get_bypass_entity_safe", get_safe):
        ok = await confirmation._deliver_bypass_gb(42, 75 * GB)
    assert ok is False


@pytest.mark.asyncio
async def test_deliver_noop_on_zero_bytes():
    add = AsyncMock()
    with patch("app.services.remnawave_bypass.add_bypass_traffic", add):
        ok = await confirmation._deliver_bypass_gb(42, 0)
    assert ok is False
    add.assert_not_awaited()
