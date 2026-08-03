"""«Доход сегодня» и дневной график обязаны резать сутки одинаково.

Дефект. Тайл «Доход сегодня» на главной считается от полуночи по Москве:
фронт присылает since=mskTodayStartIso() в /payments/revenue. Дневной
график рядом (get_daily_timeseries) резал сутки по UTC. Покупки с 00:00
до 03:00 МСК у тайла попадали в сегодня, а у графика — во вчера: две
цифры про один и тот же день на одном экране не сходились, и понять,
какая правильная, было нельзя.

Второй дефект, тут же. Выручка — это только внешние поступления. Строки
с payment_provider='balance' (покупка с баланса, автопродление с
баланса) — внутреннее движение уже посчитанных денег; без фильтра один
рубль попадал в отчёты два-три раза.

Третий дефект. Карточка пользователя запрашивала из pending_purchases
колонки amount_kopecks и paid_at, которых в этой таблице нет вовсе, —
на живой базе запрос падал UndefinedColumnError и карточка не
открывалась ни в боте, ни в дашборде.
"""
import inspect
import re

import pytest

import database.admin as admin_mod
import database.analytics as analytics_mod


class _FakeConn:
    """Соединение, которое запоминает SQL и отдаёт пустые результаты."""

    def __init__(self):
        self.sql: list[str] = []

    async def fetch(self, query, *args):
        self.sql.append(query)
        return []

    async def fetchrow(self, query, *args):
        self.sql.append(query)
        return None

    async def fetchval(self, query, *args):
        self.sql.append(query)
        return None


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture()
def conn(monkeypatch):
    c = _FakeConn()
    pool = _FakePool(c)

    async def _get_pool():
        return pool

    monkeypatch.setattr(admin_mod, "get_pool", _get_pool)
    monkeypatch.setattr(analytics_mod, "get_pool", _get_pool)
    return c


@pytest.mark.asyncio
async def test_daily_chart_cuts_days_in_moscow(conn):
    """Каждая серия графика режет сутки по Москве — как и тайл «сегодня».

    Проверяем ВСЕ DATE_TRUNC('day', ...) в запросе: достаточно одной
    серии, оставшейся в UTC, чтобы точки на одном X разъехались.
    """
    await admin_mod.get_daily_timeseries(30)
    sql = "\n".join(conn.sql)
    buckets = re.findall(r"DATE_TRUNC\(\s*'day',(.*?)\)::date", sql, re.S)
    assert buckets, "не нашли ни одной посуточной группировки"
    for expr in buckets:
        assert "Europe/Moscow" in expr, f"сутки режутся не по Москве: {expr.strip()}"


@pytest.mark.asyncio
async def test_daily_chart_reports_its_timezone(conn):
    """Ответ явно называет часовой пояс — чтобы на фронте не гадать."""
    out = await admin_mod.get_daily_timeseries(7)
    assert out["tz"] == "Europe/Moscow"


@pytest.mark.asyncio
async def test_tile_and_chart_share_revenue_definition(conn):
    """Тайл и график берут деньги из одной таблицы с одним фильтром."""
    await admin_mod.get_daily_timeseries(30)
    chart_sql = "\n".join(conn.sql)
    conn.sql.clear()
    await analytics_mod.get_revenue_for_period(24)
    tile_sql = "\n".join(conn.sql)

    for name, sql in (("график", chart_sql), ("тайл", tile_sql)):
        assert "FROM pending_purchases" in sql, f"{name}: не тот источник выручки"
        assert "status = 'paid'" in sql, f"{name}: считаются неоплаченные корзины"
        assert "COALESCE(payment_provider, '') <> 'balance'" in sql, (
            f"{name}: покупки с баланса удваивают выручку"
        )


@pytest.mark.asyncio
async def test_user_card_asks_only_for_existing_columns(conn):
    """В pending_purchases нет ни amount_kopecks, ни paid_at.

    Запрос с ними падал на живой базе — карточка пользователя не
    открывалась. Здесь ловим текст запроса: фейковая база ошибку не
    поднимет, а прод поднимет.
    """
    await admin_mod.get_user_extended_stats(1)
    sql = "\n".join(conn.sql)
    assert "FROM pending_purchases" in sql
    assert "amount_kopecks" not in sql
    assert "MIN(paid_at)" not in sql and "MAX(paid_at)" not in sql
    assert "SUM(price_kopecks)" in sql


def test_hourly_chart_stays_in_moscow_too():
    """Часовой график уже жил в МСК — следим, чтобы не «починили» обратно."""
    src = inspect.getsource(admin_mod.get_hourly_timeseries)
    assert "Europe/Moscow" in src
