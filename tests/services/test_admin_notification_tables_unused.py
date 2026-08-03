"""Таблицы admin_notification_templates и admin_notification_log — пустышки.

ЧТО ЕСТЬ

    Миграция 036 создаёт обе таблицы. Ни одной вставки, ни одного чтения:
    их имена не встречаются больше нигде — ни в .py, ни в других .sql, ни в
    дашборде (.ts/.tsx). Таблицы всегда пусты.

    Опасность не в месте на диске, а в чтении схемы: админ видит
    admin_notification_log и считает, что история админских рассылок
    пишется, — и строит на ней отчёт. Реальный лог отправок ведётся в
    automated_notification_sends.

ПОЧЕМУ НЕ УДАЛЕНЫ

    DROP TABLE — миграция схемы на проде, это решение владельца, а не правка
    кода. Вместо удаления в саму миграцию 036 положено предупреждение, чтобы
    следующий читатель не искал потребителей зря.

ЧТО СТОРОЖИТ ТЕСТ

    Ровно два состояния: либо потребителей по-прежнему нет и предупреждение
    на месте, либо кто-то начал таблицы использовать — тогда предупреждение
    в миграции стало враньём и его надо снять.
"""
from pathlib import Path

import pytest

MIGRATION = Path("migrations/036_notification_overhaul.sql")
DEAD_TABLES = ["admin_notification_templates", "admin_notification_log"]
LIVE_NEIGHBOURS = ["cashback_promotions", "user_cashback_multipliers"]
SKIP_DIRS = (".venv", "graphify-out", "__pycache__", "node_modules", "docs/", "tests/")
SUFFIXES = (".py", ".sql", ".ts", ".tsx")


def _consumers(table: str):
    hits = []
    for path in Path(".").rglob("*"):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        s = str(path)
        if any(x in s for x in SKIP_DIRS) or path == MIGRATION:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if table in text:
            hits.append(s)
    return hits


@pytest.mark.parametrize("table", DEAD_TABLES)
def test_table_has_no_consumers(table):
    hits = _consumers(table)
    assert not hits, (
        f"таблица {table} где-то используется ({hits}) — значит предупреждение "
        f"в {MIGRATION} устарело и его надо убрать"
    )


@pytest.mark.parametrize("table", DEAD_TABLES)
def test_migration_warns_about_dead_table(table):
    src = MIGRATION.read_text(encoding="utf-8")
    assert "НИКЕМ НЕ ИСПОЛЬЗУЮТСЯ" in src, (
        f"из {MIGRATION} убрали предупреждение — следующий читатель снова "
        f"пойдёт искать несуществующих потребителей"
    )
    assert f"CREATE TABLE IF NOT EXISTS {table}" in src


@pytest.mark.parametrize("table", LIVE_NEIGHBOURS)
def test_live_tables_from_same_migration_are_used(table):
    """Соседние таблицы миграции 036 живые — предупреждение не про них."""
    assert _consumers(table), f"{table} внезапно осталась без потребителей"
