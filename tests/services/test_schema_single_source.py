"""Схема БД описана в одном месте — в каталоге migrations/.

Дефект: схема управлялась двумя механизмами сразу. Помимо migrations/*.sql,
init_db выполнял ~630 строк императивного DDL прямо из кода, и делал это при
КАЖДОМ старте бота. Каждый CREATE TABLE / ALTER TABLE просит у Postgres
ACCESS EXCLUSIVE на свою таблицу: одна висящая idle-in-transaction сессия
или работающий autovacuum — и ALTER встаёт в очередь, а за ним встают все
читающие запросы к users, subscriptions, payments. Спасал lock_timeout=5s,
после которого ошибка молча проглатывалась через `except Exception: pass` —
на девственной базе колонка могла не создаться, и никто бы не узнал.

Теперь блок выполняется только при LEGACY_SCHEMA_BOOTSTRAP=1 (аварийный
бутстрап для локальной разработки), а всё, что жило только в нём, перенесено
в migrations/073_schema_source_of_truth.sql.

Сам DDL с тех пор уехал из database/core.py в database/legacy_schema.py.
Читаем оба файла: если оставить один core.py, тест начнёт проходить впустую —
искать в нём будет нечего, а колонка, добавленная только в код, снова
проедет незамеченной.
"""
import re
from pathlib import Path

CORE = Path("database/core.py")
LEGACY = Path("database/legacy_schema.py")
MIGRATIONS = Path("migrations")


def _core_source() -> str:
    return CORE.read_text(encoding="utf-8") + "\n" + LEGACY.read_text(encoding="utf-8")


def _all_migrations_sql() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS.glob("*.sql"))
    )


def _tables(sql: str) -> set:
    return {
        m.group(1).lower()
        for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.I)
    }


def _columns(sql: str) -> set:
    """Колонки и из ALTER, и объявленные прямо в CREATE TABLE."""
    out = set()
    for m in re.finditer(
        r"ALTER TABLE\s+(?:IF EXISTS\s+)?(\w+)\s+ADD COLUMN(?:\s+IF NOT EXISTS)?\s+(\w+)",
        sql, re.I,
    ):
        out.add((m.group(1).lower(), m.group(2).lower()))
    for m in re.finditer(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\s*\)\s*;", sql, re.I | re.S,
    ):
        table = m.group(1).lower()
        for line in m.group(2).split("\n"):
            line = line.strip().strip(",")
            if not line or line.startswith("--"):
                continue
            first = line.split()[0].lower()
            if first in ("primary", "unique", "foreign", "constraint", "check"):
                continue
            out.add((table, first))
    return out


def test_legacy_ddl_is_disabled_by_default():
    src = _core_source()
    assert "LEGACY_SCHEMA_BOOTSTRAP" in src, "легаси-DDL снова выполняется безусловно"
    assert 'os.getenv("LEGACY_SCHEMA_BOOTSTRAP"' in src
    assert "if _legacy_bootstrap:" in src


def test_every_table_from_code_exists_in_migrations():
    missing = sorted(_tables(_core_source()) - _tables(_all_migrations_sql()))
    assert not missing, (
        f"таблицы есть только в коде — на боевой базе их не создаст никто: {missing}"
    )


def test_every_column_from_code_exists_in_migrations():
    missing = sorted(_columns(_core_source()) - _columns(_all_migrations_sql()))
    assert not missing, (
        "колонки есть только в коде, миграции их не создадут: "
        f"{missing[:20]}"
    )


def test_bootstrap_migration_is_idempotent():
    """Она проедет и по боевой базе, где объекты давно созданы кодом."""
    raw = (MIGRATIONS / "073_schema_source_of_truth.sql").read_text(encoding="utf-8")
    # Комментарии не выполняются — из них и берутся ложные срабатывания.
    sql = "\n".join(
        line for line in raw.split("\n") if not line.strip().startswith("--")
    )
    creates = re.findall(r"CREATE TABLE(?!\s+IF NOT EXISTS)", sql, re.I)
    alters = re.findall(r"ADD COLUMN(?!\s+IF NOT EXISTS)", sql, re.I)
    assert not creates, "CREATE TABLE без IF NOT EXISTS упадёт на боевой базе"
    assert not alters, "ADD COLUMN без IF NOT EXISTS упадёт на боевой базе"
