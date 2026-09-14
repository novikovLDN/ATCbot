"""E2E fixtures — real PostgreSQL 16, everything else of the bot real too.

Runs only when E2E_DATABASE_URL points at a DISPOSABLE Postgres database: the
session DROPs its public schema, boots it with database.init_db() (migrations
+ inline DDL, exactly like main.py) and every test TRUNCATEs all tables and
restores the rows init_db seeded. Without the variable every test
here is skipped, so the hermetic suite stays hermetic.

    E2E_DATABASE_URL=postgresql://… pytest tests/e2e -q

See docs/audit/07_e2e.md.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

URL = os.getenv("E2E_DATABASE_URL")
_HERE = Path(__file__).parent.resolve()

if URL:
    # Production (Railway container) and CI run with TZ=UTC. The code writes
    # naive-UTC datetimes (_to_db_utc) into TIMESTAMPTZ columns and asyncpg
    # reads a naive value as LOCAL time — on a non-UTC machine every date would
    # shift by the local offset (docs/audit/07_e2e.md, finding E2E-TZ).
    import time as _time
    os.environ["TZ"] = "UTC"
    _time.tzset()


def pytest_collection_modifyitems(config, items):
    if URL:
        return
    skip = pytest.mark.skip(reason="E2E_DATABASE_URL not set (real-Postgres e2e suite)")
    for item in items:
        if _HERE in Path(str(item.fspath)).resolve().parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def e2e_db():
    """Boot the schema once per session (init_db = migrations + inline DDL)
    and remember what init_db seeded, to restore it after each TRUNCATE."""
    if not URL:
        pytest.skip("E2E_DATABASE_URL not set")
    import database.core as core

    original = core.DATABASE_URL
    core.DATABASE_URL = URL

    async def boot():
        import asyncpg
        # Production (Railway) and CI Postgres run with TimeZone=UTC; a local
        # server inherits the machine zone. Pin UTC so SQL NOW() into naive
        # "UTC" columns behaves as in production (see docs/audit/07_e2e.md).
        # The database is disposable: start every session from an EMPTY schema,
        # so init_db() really runs every migration + the inline DDL (the
        # production boot on a fresh database) and the seed snapshot is pristine.
        conn = await asyncpg.connect(URL)
        try:
            db = await conn.fetchval("SELECT current_database()")
            await conn.execute(f"ALTER DATABASE \"{db}\" SET timezone TO 'UTC'")
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        finally:
            await conn.close()
        core.DB_READY = False
        core._pool = None
        ok = await core.init_db()
        assert ok, "database.init_db() failed on the e2e database"
        pool = core._pool
        tables = [r[0] for r in await pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'")]
        seeds = {}
        for t in tables:
            if await pool.fetchval(f'SELECT count(*) FROM "{t}"'):
                seeds[t] = await pool.fetchval(f'SELECT json_agg(x)::text FROM "{t}" x')
        await pool.close()
        core._pool = None
        core.DB_READY = False
        return tables, seeds

    tables, seeds = asyncio.run(boot())
    yield SimpleNamespace(url=URL, tables=tables, seeds=seeds)
    core.DATABASE_URL = original
    core._pool = None
    core.DB_READY = False


async def reset_db(pool, tables, seeds) -> None:
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
        for t, rows in seeds.items():
            await conn.execute(f'INSERT INTO "{t}" SELECT * FROM json_populate_recordset(NULL::"{t}", $1::json)', rows)
            seq = await conn.fetchval("SELECT pg_get_serial_sequence($1, 'id')", f'public."{t}"') \
                if await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name='id'", t) else None
            if seq:
                await conn.execute(f"SELECT setval('{seq}', GREATEST((SELECT max(id) FROM \"{t}\"), 1))")


@pytest.fixture
async def e2e(e2e_db, monkeypatch):
    import database
    import database.core as core
    from tests.e2e.world import World

    import asyncpg

    # Pool created the way init_db() creates it (main.py boot), not through
    # get_pool()'s lazy path.
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "20")
    pool = await asyncpg.create_pool(e2e_db.url, **core._get_pool_config())
    core._pool = pool
    core.DB_READY = True
    assert await database.get_pool() is pool
    await reset_db(pool, e2e_db.tables, e2e_db.seeds)
    w = World(pool, monkeypatch)
    w.setup()
    try:
        yield w
    finally:
        await w.close()
        await pool.close()
        core._pool = None
        core.DB_READY = False
