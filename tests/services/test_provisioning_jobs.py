"""T2 — database.provisioning_jobs (outbox) + migration 082.

No Postgres here: a fake asyncpg connection records SQL and returns queued
results, so the tests pin the SQL *semantics* we can check statically
(ON CONFLICT path, one-shot bypass plan, SKIP LOCKED + per-user FIFO,
UTC conversion). Real-Postgres behaviour is covered by the CI migration gate.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import database.provisioning_jobs as pj
from app.services.tariffs import Entitlement

GIB = 1024 ** 3
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "082_provisioning_jobs.sql"


class _FakeTx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        self._conn.tx_depth += 1
        self._conn.tx_count += 1
        return self

    async def __aexit__(self, *a):
        self._conn.tx_depth -= 1
        return False


class FakeConn:
    """Queued results per method; records (method, sql, args, in_tx)."""

    def __init__(self, *, fetchval=(), fetchrow=()):
        self._fetchval = list(fetchval)
        self._fetchrow = list(fetchrow)
        self.calls = []
        self.tx_depth = 0
        self.tx_count = 0

    def _record(self, method, sql, args):
        self.calls.append((method, " ".join(sql.split()), args, self.tx_depth > 0))

    async def fetchval(self, sql, *args):
        self._record("fetchval", sql, args)
        return self._fetchval.pop(0)

    async def fetchrow(self, sql, *args):
        self._record("fetchrow", sql, args)
        return self._fetchrow.pop(0)

    async def execute(self, sql, *args):
        self._record("execute", sql, args)
        return "UPDATE 1"

    def transaction(self):
        return _FakeTx(self)


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.fixture
def db(monkeypatch):
    """Install a FakeConn behind database.provisioning_jobs.get_pool."""
    def _install(conn):
        monkeypatch.setattr(pj._core, "DB_READY", True)
        monkeypatch.setattr(pj, "get_pool", AsyncMock(return_value=FakePool(conn)))
        return conn
    return _install


def _job_row(**over):
    row = {
        "id": 7,
        "idempotency_key": "purchase:1",
        "telegram_id": 42,
        "source": "webhook",
        "tariff_key": "basic",
        "premium_until": datetime(2026, 10, 13, 12, 0),
        "bypass_add_bytes": 10 * GIB,
        "bypass_base_bytes": None,
        "bypass_target_bytes": None,
        "status": "running",
        "attempts": 1,
        "next_attempt_at": datetime(2026, 9, 13, 12, 0),
        "lease_until": datetime(2026, 9, 13, 12, 2),
        "last_error": None,
        "context": '{"purchase_id": "1"}',
        "created_at": datetime(2026, 9, 13, 12, 0),
        "updated_at": datetime(2026, 9, 13, 12, 0),
        "done_at": None,
    }
    row.update(over)
    return row


ENT = Entitlement("basic", 30, "basic", 10 * GIB)
UNTIL = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


# ── insert_job ─────────────────────────────────────────────────────────

async def test_insert_job_new_row_returns_id():
    conn = FakeConn(fetchval=[11])
    job_id = await pj.insert_job(
        conn, key="purchase:1", telegram_id=42, source="webhook",
        ent=ENT, premium_until=UNTIL, context={"purchase_id": "1"},
    )
    assert job_id == 11
    assert len(conn.calls) == 1
    _, sql, args, _ = conn.calls[0]
    assert "INSERT INTO provisioning_jobs" in sql
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert "RETURNING id" in sql
    assert "::jsonb" in sql
    assert args[0] == "purchase:1"
    assert args[1] == 42
    assert "webhook" in args and "basic" in args
    assert 10 * GIB in args
    # UTC contract: aware UTC in → naive UTC to asyncpg
    naive = [a for a in args if isinstance(a, datetime)]
    assert naive == [datetime(2026, 10, 13, 12, 0)]
    assert naive[0].tzinfo is None
    assert json.loads([a for a in args if isinstance(a, str) and a.startswith("{")][0]) == {
        "purchase_id": "1",
    }


async def test_insert_job_conflict_returns_existing_id():
    conn = FakeConn(fetchval=[None, 5])
    job_id = await pj.insert_job(
        conn, key="purchase:1", telegram_id=42, source="webhook",
        ent=ENT, premium_until=UNTIL, context=None,
    )
    assert job_id == 5
    _, sql2, args2, _ = conn.calls[1]
    assert sql2.startswith("SELECT id FROM provisioning_jobs")
    assert "idempotency_key = $1" in sql2
    assert args2 == ("purchase:1",)


async def test_insert_job_premium_untouched_and_empty_context():
    conn = FakeConn(fetchval=[1])
    await pj.insert_job(
        conn, key="pack:9", telegram_id=42, source="webhook",
        ent=Entitlement("pack", 0, None, 15 * GIB), premium_until=None, context=None,
    )
    _, _, args, _ = conn.calls[0]
    assert None in args          # premium_until NULL
    assert "{}" in args          # context defaults to empty object


async def test_insert_job_rejects_naive_datetime():
    conn = FakeConn(fetchval=[1])
    with pytest.raises(ValueError):
        await pj.insert_job(
            conn, key="k", telegram_id=1, source="s", ent=ENT,
            premium_until=datetime(2026, 1, 1), context=None,
        )
    assert conn.calls == []


async def test_insert_job_rejects_premium_without_until():
    conn = FakeConn(fetchval=[1])
    with pytest.raises(ValueError):
        await pj.insert_job(
            conn, key="k", telegram_id=1, source="s", ent=ENT,
            premium_until=None, context=None,
        )


# ── claim ──────────────────────────────────────────────────────────────

async def test_claim_sql_semantics(db):
    conn = db(FakeConn(fetchrow=[_job_row()]))
    job = await pj.claim(lease_s=90)
    assert job["id"] == 7
    assert conn.tx_count == 1
    method, sql, args, in_tx = conn.calls[0]
    assert in_tx, "claim must run inside a short transaction"
    assert "FOR UPDATE SKIP LOCKED" in sql
    # per-user FIFO: no earlier open job of the same user
    assert re.search(
        r"NOT EXISTS \( SELECT 1 FROM provisioning_jobs e WHERE e\.telegram_id = j\.telegram_id "
        r"AND e\.id < j\.id AND e\.status IN \('pending', ?'running'\)", sql,
    ), sql
    # pending, or running with an expired lease
    assert "j.status = 'pending'" in sql
    assert "j.status = 'running' AND j.lease_until <" in sql
    assert "next_attempt_at <=" in sql
    assert "status = 'running'" in sql and "lease_until =" in sql
    assert "attempts = attempts + 1" in sql
    assert args == (None, 90)


async def test_claim_by_id_passes_job_id(db):
    conn = db(FakeConn(fetchrow=[_job_row(id=3)]))
    job = await pj.claim(3)
    assert job["id"] == 3
    _, sql, args, _ = conn.calls[0]
    assert args == (3, 120)
    assert "$1::bigint IS NULL OR j.id = $1" in sql


async def test_claim_nothing_due_returns_none(db):
    db(FakeConn(fetchrow=[None]))
    assert await pj.claim() is None


async def test_claim_row_is_utc_aware_and_context_decoded(db):
    db(FakeConn(fetchrow=[_job_row()]))
    job = await pj.claim()
    for col in ("premium_until", "next_attempt_at", "lease_until", "created_at", "updated_at"):
        assert job[col].tzinfo == timezone.utc, col
    assert job["premium_until"] == UNTIL
    assert job["done_at"] is None
    assert job["context"] == {"purchase_id": "1"}


async def test_claim_context_already_dict(db):
    db(FakeConn(fetchrow=[_job_row(context={"a": 1})]))
    assert (await pj.claim())["context"] == {"a": 1}


# ── save_bypass_plan ───────────────────────────────────────────────────

async def test_save_bypass_plan_first_time(db):
    conn = db(FakeConn(fetchrow=[_job_row(bypass_base_bytes=3 * GIB, bypass_target_bytes=13 * GIB)]))
    job = await pj.save_bypass_plan(7, 3 * GIB, 13 * GIB)
    assert (job["bypass_base_bytes"], job["bypass_target_bytes"]) == (3 * GIB, 13 * GIB)
    _, sql, args, _ = conn.calls[0]
    assert sql.startswith("UPDATE provisioning_jobs")
    assert "WHERE id = $1 AND bypass_target_bytes IS NULL" in sql
    assert "RETURNING *" in sql
    assert args == (7, 3 * GIB, 13 * GIB)
    assert len(conn.calls) == 1


async def test_save_bypass_plan_only_once_returns_existing(db):
    existing = _job_row(bypass_base_bytes=3 * GIB, bypass_target_bytes=13 * GIB)
    conn = db(FakeConn(fetchrow=[None, existing]))
    job = await pj.save_bypass_plan(7, 20 * GIB, 30 * GIB)
    # the stored plan wins — a second call never overwrites it
    assert (job["bypass_base_bytes"], job["bypass_target_bytes"]) == (3 * GIB, 13 * GIB)
    _, sql2, args2, _ = conn.calls[1]
    assert sql2.startswith("SELECT * FROM provisioning_jobs WHERE id = $1")
    assert args2 == (7,)


async def test_save_bypass_plan_missing_job_raises(db):
    db(FakeConn(fetchrow=[None, None]))
    with pytest.raises(LookupError):
        await pj.save_bypass_plan(404, 0, GIB)


@pytest.mark.parametrize("base, target", [(-1, 5), (5, 4), (None, 5)])
async def test_save_bypass_plan_rejects_bad_plan(db, base, target):
    conn = db(FakeConn())
    with pytest.raises(ValueError):
        await pj.save_bypass_plan(7, base, target)
    assert conn.calls == []


# ── status transitions ─────────────────────────────────────────────────

async def test_mark_done(db):
    conn = db(FakeConn(fetchval=[7]))
    assert await pj.mark_done(7) is True
    _, sql, args, _ = conn.calls[0]
    assert "status = 'done'" in sql and "done_at =" in sql and "lease_until = NULL" in sql
    assert "status IN ('pending', 'running')" in sql
    assert args == (7,)


async def test_mark_done_already_final(db):
    db(FakeConn(fetchval=[None]))
    assert await pj.mark_done(7) is False


async def test_mark_retry_utc_and_truncation(db):
    conn = db(FakeConn(fetchval=[7]))
    next_at = datetime(2026, 9, 13, 12, 30, tzinfo=timezone.utc)
    assert await pj.mark_retry(7, "x" * 5000, next_at) is True
    _, sql, args, _ = conn.calls[0]
    assert "status = 'pending'" in sql and "next_attempt_at = $3" in sql
    assert "lease_until = NULL" in sql
    assert args[0] == 7
    assert len(args[1]) == pj.MAX_ERROR_LEN
    assert args[2] == datetime(2026, 9, 13, 12, 30) and args[2].tzinfo is None


async def test_mark_retry_rejects_naive(db):
    db(FakeConn(fetchval=[7]))
    with pytest.raises(ValueError):
        await pj.mark_retry(7, "err", datetime(2026, 9, 13) + timedelta(minutes=1))


async def test_mark_dead(db):
    conn = db(FakeConn(fetchval=[7]))
    assert await pj.mark_dead(7, "conflict") is True
    _, sql, args, _ = conn.calls[0]
    assert "status = 'dead'" in sql and "lease_until = NULL" in sql
    assert "status IN ('pending', 'running')" in sql
    assert args == (7, "conflict")


# ── get_by_key ─────────────────────────────────────────────────────────

async def test_get_by_key(db):
    conn = db(FakeConn(fetchrow=[_job_row(status="done", done_at=datetime(2026, 9, 13, 12, 1))]))
    job = await pj.get_by_key("purchase:1")
    assert job["status"] == "done"
    assert job["done_at"].tzinfo == timezone.utc
    assert conn.calls[0][2] == ("purchase:1",)


async def test_get_by_key_missing(db):
    db(FakeConn(fetchrow=[None]))
    assert await pj.get_by_key("nope") is None


# ── DB failures raise (never look like "no job") ───────────────────────

async def test_raises_when_db_not_ready(monkeypatch):
    monkeypatch.setattr(pj._core, "DB_READY", False)
    with pytest.raises(RuntimeError):
        await pj.claim()
    with pytest.raises(RuntimeError):
        await pj.get_by_key("k")


async def test_raises_when_pool_none(monkeypatch):
    monkeypatch.setattr(pj._core, "DB_READY", True)
    monkeypatch.setattr(pj, "get_pool", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError):
        await pj.mark_done(1)


# ── migration 082 sanity ───────────────────────────────────────────────

def _statements():
    sql = re.sub(r"--[^\n]*", "", MIGRATION.read_text())
    return [" ".join(s.split()) for s in sql.split(";") if s.strip()]


def test_migration_number_is_unique():
    assert [p.name for p in (ROOT / "migrations").glob("082_*.sql")] == [MIGRATION.name]


def test_migration_is_idempotent_and_additive():
    stmts = _statements()
    assert stmts, "empty migration"
    for s in stmts:
        upper = s.upper()
        assert not upper.startswith(("DROP", "TRUNCATE", "DELETE", "UPDATE")), s
        if upper.startswith("CREATE"):
            assert re.match(r"CREATE (UNIQUE )?(TABLE|INDEX) IF NOT EXISTS ", upper), s
        elif upper.startswith("ALTER TABLE"):
            clauses = re.findall(r"\bADD (?:COLUMN )?(?:IF NOT EXISTS )?", upper)
            assert clauses and all("IF NOT EXISTS" in c for c in clauses), s
            assert " DROP " not in upper, s
        else:
            pytest.fail(f"unexpected statement: {s}")


def test_migration_schema_matches_plan():
    text = " ".join(MIGRATION.read_text().split())
    for col in (
        "idempotency_key TEXT NOT NULL UNIQUE",
        "telegram_id BIGINT NOT NULL",
        "premium_until TIMESTAMP NULL",
        "bypass_add_bytes BIGINT NOT NULL DEFAULT 0 CHECK (bypass_add_bytes >= 0)",
        "bypass_base_bytes BIGINT NULL",
        "bypass_target_bytes BIGINT NULL",
        "CHECK (status IN ('pending','running','done','dead','shadow'))",
        "next_attempt_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')",
        "context JSONB NOT NULL DEFAULT '{}'::jsonb",
    ):
        assert col in text, col
    assert "TIMESTAMPTZ" not in text.upper(), "UTC contract: TIMESTAMP WITHOUT TIME ZONE"
    assert (
        "ALTER TABLE platega_subscriptions ADD COLUMN IF NOT EXISTS is_combo BOOLEAN NOT NULL DEFAULT FALSE"
        in text
    )


def test_statuses_in_code_match_migration():
    text = MIGRATION.read_text()
    for status in pj.STATUSES:
        assert f"'{status}'" in text
