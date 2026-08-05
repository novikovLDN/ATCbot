"""Экран «Сверка» не должен выкачивать всю панель на каждое обновление.

Дефект: find_over_issuance_candidates звала remnawave_api.get_all_users()
без кэша. get_all_users листает Remnawave страницами по 1000; на проде это
~358k сущностей, то есть сотни HTTP-запросов и десятки секунд. Роут дашборда
дёргает функцию на каждое открытие экрана, поэтому пара нажатий F5 подряд
превращалась в шторм запросов к панели (вплоть до rate-limit) и залипание
воркера FastAPI.

Отдельно проверяем, что неудачный скан не кэшируется: закэшированное
«панель недоступна» на 10 минут спрятало бы реальные данные, а закэшированный
пустой список выглядел бы как «всё чисто».
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest


def _panel_user(tg_id: int, years: int = 10):
    expires = datetime.now(timezone.utc) + timedelta(days=365 * years)
    return {
        "username": f"tg_{tg_id}_premium",
        "expireAt": expires.isoformat().replace("+00:00", "Z"),
        "uuid": f"panel-{tg_id}",
        "status": "ACTIVE",
    }


class _FakeConn:
    async def fetch(self, *a, **kw):
        return []


class _Acquire:
    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def acquire(self):
        return _Acquire()


@pytest.fixture(autouse=True)
def _clean_cache():
    import database.reconciliation as rec
    rec.invalidate_panel_scan_cache()
    yield
    rec.invalidate_panel_scan_cache()


def _install(monkeypatch, get_all_users):
    """Подменяем пул и панель, возвращаем модуль со списком кандидатов.

    get_pool патчим именно в reconciliation_candidates: после разрезания
    database/reconciliation.py на модули там живёт find_over_issuance_candidates,
    а фасад держит только реэкспорт — подмена атрибута на фасаде до реальной
    функции не доехала бы.
    """
    import database.reconciliation_candidates as rec
    from app.services import remnawave_api
    monkeypatch.setattr(rec, "get_pool", AsyncMock(return_value=_FakePool()))
    monkeypatch.setattr(remnawave_api, "get_all_users", get_all_users)
    return rec


@pytest.mark.asyncio
async def test_second_open_of_the_screen_does_not_rescan_the_panel(monkeypatch):
    scan = AsyncMock(return_value=[_panel_user(1), _panel_user(2)])
    rec = _install(monkeypatch, scan)

    first = await rec.find_over_issuance_candidates()
    second = await rec.find_over_issuance_candidates()

    assert scan.await_count == 1, "второй заход обязан читать кэш"
    assert len(first) == len(second) == 2


@pytest.mark.asyncio
async def test_limit_does_not_truncate_the_cached_list(monkeypatch):
    """limit режет только ответ, иначе следующий запрос получил бы обрезок."""
    scan = AsyncMock(return_value=[_panel_user(i) for i in range(5)])
    rec = _install(monkeypatch, scan)

    short = await rec.find_over_issuance_candidates(limit=1)
    full = await rec.find_over_issuance_candidates(limit=5)

    assert len(short) == 1
    assert len(full) == 5
    assert scan.await_count == 1


@pytest.mark.asyncio
async def test_force_refresh_goes_to_the_panel(monkeypatch):
    scan = AsyncMock(return_value=[_panel_user(1)])
    rec = _install(monkeypatch, scan)

    await rec.find_over_issuance_candidates()
    await rec.find_over_issuance_candidates(force_refresh=True)

    assert scan.await_count == 2


@pytest.mark.asyncio
async def test_unreachable_panel_is_not_cached(monkeypatch):
    """None от get_all_users — это «не смогли прочитать», а не результат."""
    scan = AsyncMock(return_value=None)
    rec = _install(monkeypatch, scan)

    rows = await rec.find_over_issuance_candidates()
    assert rows and rows[0]["panel_unreachable"] is True

    scan.return_value = [_panel_user(1)]
    rows2 = await rec.find_over_issuance_candidates()
    assert scan.await_count == 2
    assert rows2[0]["telegram_id"] == 1


@pytest.mark.asyncio
async def test_expired_ttl_triggers_a_fresh_scan(monkeypatch):
    scan = AsyncMock(return_value=[_panel_user(1)])
    rec = _install(monkeypatch, scan)

    # Кэш — глобал reconciliation_panel, а не фасада: состарить его можно
    # только там, где он реально лежит.
    import database.reconciliation_panel as panel

    await rec.find_over_issuance_candidates()
    stored_at, rows = panel._panel_scan_cache
    panel._panel_scan_cache = (stored_at - panel._PANEL_SCAN_TTL_SECONDS - 1, rows)
    await rec.find_over_issuance_candidates()

    assert scan.await_count == 2
