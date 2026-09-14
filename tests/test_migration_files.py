"""Migration files: every version number is used once.

Risk closed: the runner (migrations.py) records applied migrations by the
NUMBER only (`schema_migrations.version = "083"`). A new file that reuses an
existing number is applied on an empty DB (CI is green) but silently SKIPPED
on production, where that number is already recorded — the schema change
never happens and the code that needs it fails at runtime.

Known historical pair: 006_add_subscription_fields.sql +
006_broadcast_discounts.sql (the second added 2026-03 when 006 was long
applied). It is harmless only because database/core.py creates
broadcast_discounts in its inline DDL anyway. Do not add to this set.
"""
from __future__ import annotations

import re
from collections import Counter

import migrations

KNOWN_DUPLICATES = {"006"}


def test_migration_versions_are_unique():
    versions = Counter(int(v) for v, _ in migrations.get_migration_files())
    dup = {f"{v:03d}" for v, n in versions.items() if n > 1}
    assert dup == KNOWN_DUPLICATES, (
        f"migration number(s) used twice: {sorted(dup - KNOWN_DUPLICATES)} — the second file "
        "would be skipped on production (schema_migrations stores only the number)")


def test_known_duplicate_is_covered_by_inline_ddl():
    core = (migrations.MIGRATIONS_DIR.parent / "database" / "core.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS broadcast_discounts" in core


def test_every_sql_file_is_picked_up_by_the_runner():
    pattern = re.compile(r"^(\d+)_(.+)\.sql$")
    ignored = [p.name for p in migrations.MIGRATIONS_DIR.glob("*.sql") if not pattern.match(p.name)]
    assert ignored == [], f"SQL files the runner silently ignores: {ignored}"


def test_runner_orders_by_number_not_by_text():
    nums = [int(v) for v, _ in migrations.get_migration_files()]
    assert nums == sorted(nums)
