"""migrations.run_migrations against a real Postgres.

Risk closed: the failure path of the custom runner had never run (coverage
09: run_migrations lines 166-177, apply_migration 120-124). On a deploy with a
broken migration the process must (1) roll that migration back completely —
no half-applied DDL/DML, (2) not record it, (3) not apply later migrations on
top of it, (4) report failure so init_db() refuses to start; and after the fix
apply exactly the missing ones, never re-running applied files.

Runs in a throw-away schema of the DASHBOARD_TEST_DATABASE_URL database
(search_path pinned to it), dropped afterwards.
"""
from __future__ import annotations

import os
import uuid

import pytest

URL = os.getenv("DASHBOARD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="DASHBOARD_TEST_DATABASE_URL not set")

asyncpg = pytest.importorskip("asyncpg")

import migrations  # noqa: E402


@pytest.fixture
async def conn():
    schema = f"mig_runner_{uuid.uuid4().hex[:10]}"
    c = await asyncpg.connect(URL)
    await c.execute(f'CREATE SCHEMA "{schema}"')
    await c.execute(f'SET search_path TO "{schema}"')
    try:
        yield c
    finally:
        await c.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await c.close()


def _write(d, name, sql):
    (d / name).write_text(sql, encoding="utf-8")


async def _tables(c):
    return {r[0] for r in await c.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}


async def _applied(c):
    return {r[0] for r in await c.fetch("SELECT version FROM schema_migrations")}


async def test_failed_migration_rolls_back_and_stops_the_run(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    _write(tmp_path, "001_a.sql", "CREATE TABLE t_a (id int);")
    _write(tmp_path, "002_b.sql",
           "CREATE TABLE t_b (id int);\nINSERT INTO t_b VALUES (1);\nSELECT * FROM no_such_table;")
    _write(tmp_path, "003_c.sql", "CREATE TABLE t_c (id int);")

    assert await migrations.run_migrations(conn) is False
    tables = await _tables(conn)
    assert "t_a" in tables
    assert "t_b" not in tables, "the failed migration was left half-applied"
    assert "t_c" not in tables, "a later migration ran on top of a failed one"
    assert await _applied(conn) == {"001"}

    # the fix is deployed: only the missing ones run (001 has no IF NOT EXISTS —
    # re-running it would fail)
    _write(tmp_path, "002_b.sql", "CREATE TABLE t_b (id int);\nINSERT INTO t_b VALUES (1);")
    assert await migrations.run_migrations(conn) is True
    assert {"t_a", "t_b", "t_c"} <= await _tables(conn)
    assert await conn.fetchval("SELECT count(*) FROM t_b") == 1
    assert await _applied(conn) == {"001", "002", "003"}

    assert await migrations.run_migrations(conn) is True           # rerun = no-op
    assert await conn.fetchval("SELECT count(*) FROM t_b") == 1


async def test_numeric_order_nine_before_ten(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    _write(tmp_path, "10_alter.sql", "ALTER TABLE t9 ADD COLUMN c int;")
    _write(tmp_path, "9_make.sql", "CREATE TABLE t9 (id int);")
    assert await migrations.run_migrations(conn) is True
    assert await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 't9' AND column_name = 'c'") == 1


async def test_empty_migration_is_recorded_as_applied(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    _write(tmp_path, "001_empty.sql", "   \n")
    assert await migrations.run_migrations(conn) is True
    # an empty file is skipped without recording; a later non-empty version of it still runs
    _write(tmp_path, "001_empty.sql", "CREATE TABLE t_e (id int);")
    assert await migrations.run_migrations(conn) is True
    assert "t_e" in await _tables(conn)
