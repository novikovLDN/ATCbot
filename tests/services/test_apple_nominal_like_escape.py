"""Разбивка по Apple-номиналам не должна ловить чужие тарифы.

Дефект. Отбор шёл условием `tariff LIKE 'apple_id_%'`. В SQL символ `_` —
одиночный wildcard, поэтому шаблон совпадает и с 'appleXidY…'. Дальше
tariff разбирался по '_' позиционно, с обращением к parts[2]/parts[3] без
всякой проверки формы: любой посторонний тариф попал бы в отчёт Apple с
мусорным регионом и номиналом.

Сейчас таких тарифов нет — дефект латентный. Ровно поэтому он и опасен:
проявится молча, в цифрах, а не падением.
"""
import pytest

import database.analytics as analytics_mod


class _FakeConn:
    """Соединение, которое запоминает SQL и отдаёт заранее заданные строки
    на запрос про apple-номиналы."""

    def __init__(self, apple_rows):
        self.sql: list[str] = []
        self._apple_rows = apple_rows

    async def fetch(self, query, *args):
        self.sql.append(query)
        if "apple" in query:
            return self._apple_rows
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


def _install(monkeypatch, apple_rows):
    conn = _FakeConn(apple_rows)
    pool = _FakePool(conn)

    async def _get_pool():
        return pool

    monkeypatch.setattr(analytics_mod, "get_pool", _get_pool)
    return conn


def _apple_sql(conn) -> str:
    hits = [q for q in conn.sql if "apple" in q]
    assert len(hits) == 1, "запрос по apple-номиналам должен быть ровно один"
    return hits[0]


@pytest.mark.asyncio
async def test_underscore_is_escaped(monkeypatch):
    """Без ESCAPE `_` остаётся wildcard'ом и шаблон ловит лишнее."""
    conn = _install(monkeypatch, [])
    await analytics_mod.get_payments_breakdown(24)

    sql = _apple_sql(conn)
    assert r"LIKE 'apple\_id\_%'" in sql
    assert r"ESCAPE '\'" in sql
    assert "LIKE 'apple_id_%'" not in sql


@pytest.mark.asyncio
async def test_wellformed_tariff_is_parsed(monkeypatch):
    """Штатная форма apple_id_<регион>_<номинал> разбирается как раньше."""
    conn = _install(monkeypatch, [
        {"tariff": "apple_id_turkey_500", "c": 3, "rev": 120000},
    ])
    out = await analytics_mod.get_payments_breakdown(24)
    assert conn.sql, "запросы не выполнялись"

    assert out["by_apple_nominal"] == [
        {"region": "turkey", "nominal": 500, "count": 3, "revenue_rubles": 1200.0},
    ]


@pytest.mark.asyncio
async def test_unexpected_shape_is_not_guessed(monkeypatch):
    """Чужой тариф, если он всё же доедет до разбора, не должен получить
    выдуманный регион с номиналом — иначе деньги молча припишутся
    существующей строке отчёта."""
    _install(monkeypatch, [
        {"tariff": "apple_id_usa", "c": 1, "rev": 50000},
        {"tariff": "apple_id_usa_500_extra", "c": 1, "rev": 10000},
    ])
    out = await analytics_mod.get_payments_breakdown(24)

    rows = out["by_apple_nominal"]
    assert len(rows) == 2
    for row in rows:
        assert row["nominal"] == 0
        # Регион = сам тариф: странная строка должна быть видна админу,
        # а не спрятана под правдоподобным 'usa'.
        assert row["region"].startswith("apple_id_usa")
    # Деньги не потеряны — строки остались в отчёте.
    assert sum(r["revenue_rubles"] for r in rows) == 600.0
