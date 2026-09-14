#!/usr/bin/env python3
"""Migration Integrity gate — boot the real DB init path against Postgres.

Production boots through ``database.init_db()`` (main.py), which:
  1. probes connectivity, creates the pool,
  2. runs ``migrations.run_migrations_safe`` (migrations/NNN_*.sql, recorded in
     ``schema_migrations`` by the numeric prefix),
  3. recreates the pool, then runs the inline legacy DDL in database/core.py
     (CREATE/ALTER ... IF NOT EXISTS, promo code seed),
  4. checks the required tables and sets DB_READY.

This script replays exactly that, so a migration (or inline DDL) that fails on
an empty database fails CI instead of the next Railway deploy.

Stages (run both, in order, against the same database):

  --stage empty   The database must be empty. Static checks on migrations/,
                  boot #1, then structural checks: every version recorded,
                  every table/index a migration creates exists. Writes a
                  schema snapshot to --snapshot.
  --stage rerun   Boot #2 on the already-migrated database (what every
                  redeploy does). Must succeed, record no new versions and
                  leave the schema identical to the snapshot. Then re-executes
                  every migration file inside a rolled-back transaction and
                  reports files that are not re-runnable (informational: the
                  runner never re-applies a recorded version).

Usage:
  DATABASE_URL=postgresql://user:pass@localhost:5432/db \\
      python scripts/ci/migration_integrity.py --stage empty --snapshot /tmp/s.json
  DATABASE_URL=... python scripts/ci/migration_integrity.py --stage rerun --snapshot /tmp/s.json

Exit code 0 = pass, 1 = failure (details on stderr and in $GITHUB_STEP_SUMMARY).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# Version prefixes that are already duplicated in history and cannot be
# renamed (renaming would change the recorded version on prod). The runner
# keys schema_migrations by the numeric prefix, so on a DB where one of the
# two files was already recorded, the other one is skipped forever. For 006
# this is harmless: 006_broadcast_discounts' table is also created by the
# inline DDL in database/core.py. Any NEW duplicate fails the gate.
ALLOWED_DUPLICATE_VERSIONS = {"006"}

FILE_RE = re.compile(r"^(\d+)_(.+)\.sql$")
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?\"?(\w+)\"?", re.I
)
DROP_TABLE_RE = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?\"?(\w+)\"?", re.I)
RENAME_TABLE_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?\"?(\w+)\"?\s+RENAME\s+TO\s+\"?(\w+)\"?",
    re.I,
)
CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?\s+ON\s",
    re.I,
)
DROP_INDEX_RE = re.compile(
    r"DROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?(?:public\.)?\"?(\w+)\"?", re.I
)
RENAME_INDEX_RE = re.compile(
    r"ALTER\s+INDEX\s+(?:IF\s+EXISTS\s+)?(?:public\.)?\"?(\w+)\"?\s+RENAME\s+TO\s+\"?(\w+)\"?", re.I
)

SUMMARY: list[str] = []
log = logging.getLogger("migration_integrity")


def summary(line: str = "") -> None:
    SUMMARY.append(line)


def flush_summary() -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and SUMMARY:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(SUMMARY) + "\n\n")


def fail(msg: str) -> None:
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"FAIL: {msg}", file=sys.stderr)
    summary(f"- :x: {msg}")


def ok(msg: str) -> None:
    print(f"OK: {msg}")
    summary(f"- :white_check_mark: {msg}")


def warn(msg: str) -> None:
    print(f"::warning::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"WARN: {msg}")
    summary(f"- :warning: {msg}")


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def static_checks() -> tuple[bool, list[tuple[str, Path]]]:
    """File naming, duplicate versions, ordering. Returns (ok, files)."""
    good = True
    files: list[tuple[str, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = FILE_RE.match(path.name)
        if not m:
            fail(f"{path.name}: does not match NNN_name.sql — migrations.py would silently skip it")
            good = False
            continue
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            warn(f"{path.name}: empty file (runner records it without executing anything)")
        if re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", _strip_sql_comments(text), re.I | re.M):
            warn(
                f"{path.name}: contains explicit BEGIN/COMMIT — migrations.py already wraps each file in a "
                "transaction; an inner COMMIT ends it early, so a later failure is no longer rolled back atomically"
            )
        files.append((m.group(1), path))

    by_version: dict[str, list[str]] = defaultdict(list)
    by_int: dict[int, set[str]] = defaultdict(set)
    for version, path in files:
        by_version[version].append(path.name)
        by_int[int(version)].add(version)
    for version, names in sorted(by_version.items()):
        if len(names) > 1:
            if version in ALLOWED_DUPLICATE_VERSIONS:
                warn(f"known duplicate version {version}: {', '.join(names)} (allow-listed)")
            else:
                fail(
                    f"duplicate migration version {version}: {', '.join(names)} — schema_migrations is keyed "
                    "by the numeric prefix, so on an existing DB only one of them would ever run. Renumber the new file."
                )
                good = False
    for number, spellings in sorted(by_int.items()):
        if len(spellings) > 1:
            fail(f"version {number} spelled differently ({sorted(spellings)}) — recorded as distinct versions")
            good = False
    if good:
        ok(f"{len(files)} migration files, {len(by_version)} distinct versions, naming valid")
    return good, files


def expected_objects(files: list[tuple[str, Path]]) -> tuple[set[str], set[str]]:
    """Tables and indexes that should exist after all migrations, in numeric order."""
    tables: set[str] = set()
    indexes: set[str] = set()
    for _, path in sorted(files, key=lambda f: (int(f[0]), f[1].name)):
        sql = _strip_sql_comments(path.read_text(encoding="utf-8"))
        # Apply statements in textual order so CREATE→DROP→CREATE resolves correctly.
        events: list[tuple[int, str, tuple[str, ...]]] = []
        for rx, kind in (
            (CREATE_TABLE_RE, "ct"),
            (DROP_TABLE_RE, "dt"),
            (RENAME_TABLE_RE, "rt"),
            (CREATE_INDEX_RE, "ci"),
            (DROP_INDEX_RE, "di"),
            (RENAME_INDEX_RE, "ri"),
        ):
            for m in rx.finditer(sql):
                events.append((m.start(), kind, tuple(g.lower() for g in m.groups())))
        for _, kind, groups in sorted(events):
            if kind == "ct":
                tables.add(groups[0])
            elif kind == "dt":
                tables.discard(groups[0])
            elif kind == "rt":
                if groups[0] in tables:
                    tables.discard(groups[0])
                tables.add(groups[1])
            elif kind == "ci":
                indexes.add(groups[0])
            elif kind == "di":
                indexes.discard(groups[0])
            elif kind == "ri":
                indexes.discard(groups[0])
                indexes.add(groups[1])
    # Temp/backup tables created and dropped within DO blocks are fine to miss;
    # anything named *_tmp / *_old / *_backup is not asserted.
    tables = {t for t in tables if not re.search(r"(_tmp|_old|_backup|_new)$", t)}
    return tables, indexes


def bootstrap_env(database_url: str) -> None:
    """Configure config.py exactly as a LOCAL boot would, before importing it."""
    # config.py refuses to start when a bare DATABASE_URL is present
    # ("Direct usage of DATABASE_URL is FORBIDDEN") — drop it after reading.
    for var in ("DATABASE_URL", "BOT_TOKEN", "ADMIN_TELEGRAM_ID", "TG_PROVIDER_TOKEN"):
        os.environ.pop(var, None)
    os.environ["APP_ENV"] = "local"
    os.environ["LOCAL_DATABASE_URL"] = database_url
    os.environ.setdefault("LOCAL_BOT_TOKEN", "0000000000:ci-fake-token")
    os.environ.setdefault("LOCAL_ADMIN_TELEGRAM_ID", "1")
    os.environ.setdefault("LOCAL_WEBHOOK_URL", "https://ci.invalid/webhook/telegram")
    os.environ.setdefault("LOCAL_WEBHOOK_SECRET", "ci-secret")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


async def schema_snapshot(conn) -> dict:
    cols = await conn.fetch(
        """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns WHERE table_schema = 'public'
        ORDER BY table_name, column_name
        """
    )
    idx = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname"
    )
    cons = await conn.fetch(
        """
        SELECT conrelid::regclass::text AS tbl, conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint WHERE connamespace = 'public'::regnamespace
        ORDER BY 1, 2
        """
    )
    return {
        "columns": [list(map(str, r.values())) for r in cols],
        "indexes": [list(map(str, r.values())) for r in idx],
        "constraints": [list(map(str, r.values())) for r in cons],
    }


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


async def boot(label: str) -> bool:
    """Run database.init_db() exactly as main.py does. Returns success."""
    import database  # noqa: WPS433 — env must be set first

    database.DB_READY = False  # proxy propagates to database.core
    try:
        success = await database.init_db()
    except Exception as e:  # main.py would log and go degraded; CI must fail
        log.exception("init_db raised")
        fail(f"{label}: database.init_db() raised {type(e).__name__}: {e}")
        return False
    if not success or not database.DB_READY:
        fail(f"{label}: database.init_db() returned {success!r}, DB_READY={database.DB_READY!r} (see log above)")
        return False
    ok(f"{label}: database.init_db() succeeded (migrations + inline DDL + required tables)")
    return True


async def close_pool() -> None:
    import database

    await database.close_pool()


async def stage_empty(database_url: str, snapshot_path: Path) -> bool:
    import asyncpg

    good, files = static_checks()

    conn = await asyncpg.connect(database_url)
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
        server_version = await conn.fetchval("SHOW server_version")
    finally:
        await conn.close()
    summary(f"Postgres server {server_version}")
    if n:
        fail(f"database is not empty ({n} tables in public) — the gate must start from an empty DB")
        return False
    ok("database is empty before boot")

    if not await boot("boot #1 (empty DB)"):
        await close_pool()
        return False
    await close_pool()

    conn = await asyncpg.connect(database_url)
    try:
        recorded = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        expected_versions = {v for v, _ in files}
        missing = sorted(expected_versions - recorded, key=int)
        extra = sorted(recorded - expected_versions)
        if missing:
            fail(f"versions not recorded in schema_migrations: {missing}")
            good = False
        if extra:
            warn(f"schema_migrations has versions without files: {extra}")
        if not missing:
            ok(f"all {len(expected_versions)} versions recorded in schema_migrations (latest {max(expected_versions, key=int)})")

        tables, indexes = expected_objects(files)
        have_tables = {
            r["table_name"].lower()
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        have_indexes = {
            r["indexname"].lower()
            for r in await conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        }
        missing_t = sorted(tables - have_tables)
        missing_i = sorted(indexes - have_indexes)
        if missing_t:
            fail(f"tables created by migrations but absent after boot: {missing_t}")
            good = False
        else:
            ok(f"{len(tables)} tables created by migrations are present ({len(have_tables)} total)")
        if missing_i:
            fail(f"indexes created by migrations but absent after boot: {missing_i}")
            good = False
        else:
            ok(f"{len(indexes)} indexes created by migrations are present")

        snap = await schema_snapshot(conn)
    finally:
        await conn.close()

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps({"versions": sorted(recorded), "schema": snap}, indent=1))
    ok(f"schema snapshot {_digest(snap)} written ({len(snap['columns'])} columns, {len(snap['indexes'])} indexes)")
    return good


async def stage_rerun(database_url: str, snapshot_path: Path, strict_reapply: bool) -> bool:
    import asyncpg

    if not snapshot_path.exists():
        fail(f"snapshot {snapshot_path} not found — run --stage empty first")
        return False
    before = json.loads(snapshot_path.read_text())
    good = True

    if not await boot("boot #2 (already migrated DB, idempotency)"):
        await close_pool()
        return False
    await close_pool()

    conn = await asyncpg.connect(database_url)
    try:
        recorded = sorted(r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations"))
        if recorded != before["versions"]:
            fail(f"boot #2 changed schema_migrations: {sorted(set(recorded) ^ set(before['versions']))}")
            good = False
        else:
            ok("boot #2 recorded no new migration versions")
        after = await schema_snapshot(conn)
        if after != before["schema"]:
            for key in ("columns", "indexes", "constraints"):
                a = {json.dumps(x) for x in before["schema"][key]}
                b = {json.dumps(x) for x in after[key]}
                if a != b:
                    fail(f"boot #2 changed {key}: -{sorted(a - b)[:5]} +{sorted(b - a)[:5]}")
            good = False
        else:
            ok(f"schema identical after boot #2 ({_digest(after)})")

        # Informational: can each file be executed again? The runner never
        # re-applies a recorded version, but migrations/README.md promises
        # idempotent files, and a hand re-run during an incident relies on it.
        non_rerunnable: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: (int(p.name.split("_")[0]), p.name)):
            sql = path.read_text(encoding="utf-8")
            if not sql.strip():
                continue
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute(sql)
            except Exception as e:
                non_rerunnable.append(f"{path.name}: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
            finally:
                await tr.rollback()
        if non_rerunnable:
            msg = f"{len(non_rerunnable)} migration file(s) are not re-runnable on a migrated DB"
            (fail if strict_reapply else warn)(msg)
            for line in non_rerunnable:
                print(f"    {line}")
                summary(f"  - `{line}`")
            if strict_reapply:
                good = False
        else:
            ok("every migration file re-executes cleanly on the migrated DB (rolled back)")
    finally:
        await conn.close()
    return good


async def stage_diagnose(database_url: str) -> bool:
    """Continue-on-error scan: apply every file in runner order on a scratch
    database created next to DATABASE_URL, and list ALL failing files (the real
    runner stops at the first one). Diagnostic only — run it when `empty`
    fails; later failures may be cascades of the first."""
    import asyncpg
    from urllib.parse import urlsplit, urlunsplit

    import migrations  # noqa: WPS433

    parts = urlsplit(database_url)
    base_db = parts.path.lstrip("/") or "postgres"
    diag_db = f"{base_db}_diag"
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    diag_url = urlunsplit(parts._replace(path=f"/{diag_db}"))

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{diag_db}"')
        await admin.execute(f'CREATE DATABASE "{diag_db}"')
    finally:
        await admin.close()

    failures: list[str] = []
    files = migrations.get_migration_files()  # exact runner order
    conn = await asyncpg.connect(diag_url)
    try:
        for version, path in files:
            sql = path.read_text(encoding="utf-8")
            if not sql.strip():
                continue
            try:
                async with conn.transaction():
                    await conn.execute(sql)
            except Exception as e:
                first = str(e).splitlines()[0][:200]
                failures.append(f"{path.name}: {type(e).__name__}: {first}")
    finally:
        await conn.close()
        admin = await asyncpg.connect(admin_url)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{diag_db}"')
        finally:
            await admin.close()

    summary(f"Applied {len(files)} files in runner order on scratch DB `{diag_db}`, continuing past errors.")
    if not failures:
        ok("every migration file applies on an empty DB when run in isolation")
        return True
    fail(f"{len(failures)} migration file(s) fail on an empty DB (the first one is the root cause; later ones may cascade)")
    for line in failures:
        print(f"    {line}")
        summary(f"  - `{line}`")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=["empty", "rerun", "diagnose"], required=True)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "schema_snapshot.json",
    )
    parser.add_argument("--strict-reapply", action="store_true", help="fail (not warn) on non-re-runnable files")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL (or --database-url) is required")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    bootstrap_env(args.database_url)
    summary(f"### Migration Integrity — stage `{args.stage}`")
    try:
        if args.stage == "empty":
            passed = asyncio.run(stage_empty(args.database_url, args.snapshot))
        elif args.stage == "diagnose":
            passed = asyncio.run(stage_diagnose(args.database_url))
        else:
            passed = asyncio.run(stage_rerun(args.database_url, args.snapshot, args.strict_reapply))
    finally:
        flush_summary()
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
