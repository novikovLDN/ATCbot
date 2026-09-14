"""provisioning_jobs — outbox for premium/bypass provisioning (migration 082).

Spec: docs/audit/02_payment_core_plan.md §A.2, §B.

  insert_job       — in the caller's billing transaction (outbox): billing
                     without a job, or a job without billing, is impossible.
                     Idempotent by idempotency_key.
  claim            — short tx, FOR UPDATE SKIP LOCKED, per-user FIFO, lease.
  save_bypass_plan — one-shot (base, target) for the bypass CAS; committed
                     BEFORE the panel PATCH so a crash after PATCH is detected
                     on retry (current limit == target → already applied).
  mark_done / mark_retry / mark_dead, get_by_key.

Every DB error RAISES (DB not ready → RuntimeError): a swallowed error here
would look like "no job" and lose or duplicate provisioning.
Timestamps: aware UTC in the API, naive UTC in the DB (_to_db_utc/_from_db_utc).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

import database.core as _core
from database.core import _from_db_utc, _to_db_utc, get_pool

STATUSES = ("pending", "running", "done", "dead", "shadow")
MAX_ERROR_LEN = 2000
DEFAULT_LEASE_S = 120

_DT_COLUMNS = ("premium_until", "next_attempt_at", "lease_until", "created_at", "updated_at", "done_at")
_NOW = "(NOW() AT TIME ZONE 'UTC')"


async def _require_pool(op: str):
    if not _core.DB_READY:
        raise RuntimeError(f"provisioning_jobs.{op}: DB not ready")
    pool = await get_pool()
    if pool is None:
        raise RuntimeError(f"provisioning_jobs.{op}: pool is None")
    return pool


def _to_job(record) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    job = dict(record)
    for col in _DT_COLUMNS:
        if col in job:
            job[col] = _from_db_utc(job[col])
    ctx = job.get("context")
    if isinstance(ctx, str):
        job["context"] = json.loads(ctx) if ctx else {}
    elif ctx is None and "context" in job:
        job["context"] = {}
    return job


async def insert_job(
    conn,
    *,
    key: str,
    telegram_id: int,
    source: str,
    ent,
    premium_until: Optional[datetime],
    context: Optional[Dict[str, Any]] = None,
) -> int:
    """Enqueue a job inside the caller's transaction. Returns the job id —
    the existing one if `key` was already enqueued (ON CONFLICT DO NOTHING).

    `ent` is an app.services.tariffs.Entitlement; `premium_until` is the
    absolute aware-UTC premium target (required when ent.premium_days > 0).
    """
    if ent.premium_days > 0 and premium_until is None:
        raise ValueError(f"provisioning_jobs.insert_job: premium_until required for {ent.tariff_key!r}")
    # None = premium untouched (packs, bypass gifts); otherwise raises on naive / non-UTC.
    until_db = _to_db_utc(premium_until) if premium_until is not None else None
    job_id = await conn.fetchval(
        """
        INSERT INTO provisioning_jobs
            (idempotency_key, telegram_id, source, tariff_key,
             premium_until, bypass_add_bytes, context)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        key, int(telegram_id), source, ent.tariff_key,
        until_db, int(ent.bypass_bytes), json.dumps(context or {}),
    )
    if job_id is None:
        job_id = await conn.fetchval(
            "SELECT id FROM provisioning_jobs WHERE idempotency_key = $1", key,
        )
    return int(job_id)


async def claim(job_id: Optional[int] = None, *, lease_s: float = DEFAULT_LEASE_S) -> Optional[Dict[str, Any]]:
    """Take one due job (or the given `job_id`) and lease it for `lease_s`.

    Claimable: 'pending', or 'running' whose lease expired (crashed worker).
    Skipped when an earlier open job of the same user exists (per-user FIFO).
    Without job_id only jobs with next_attempt_at <= now are due; an explicit
    job_id (run_now fast path) ignores the backoff time but not lease/FIFO.
    Increments attempts. Returns the job row or None.
    """
    pool = await _require_pool("claim")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                WITH picked AS (
                    SELECT j.id
                      FROM provisioning_jobs j
                     WHERE ($1::bigint IS NULL OR j.id = $1)
                       AND (j.status = 'pending'
                            OR (j.status = 'running' AND j.lease_until < {_NOW}))
                       AND ($1::bigint IS NOT NULL OR j.next_attempt_at <= {_NOW})
                       AND NOT EXISTS (
                           SELECT 1 FROM provisioning_jobs e
                            WHERE e.telegram_id = j.telegram_id
                              AND e.id < j.id
                              AND e.status IN ('pending', 'running')
                       )
                     ORDER BY j.next_attempt_at, j.id
                     LIMIT 1
                       FOR UPDATE SKIP LOCKED
                )
                UPDATE provisioning_jobs p
                   SET status = 'running',
                       lease_until = {_NOW} + make_interval(secs => $2::double precision),
                       attempts = attempts + 1,
                       updated_at = {_NOW}
                  FROM picked
                 WHERE p.id = picked.id
                RETURNING p.*
                """,
                job_id, float(lease_s),
            )
    return _to_job(row)


async def save_bypass_plan(job_id: int, base: int, target: int) -> Dict[str, Any]:
    """Persist the bypass CAS plan once. If a plan already exists it is NOT
    overwritten — the stored row (with the original plan) is returned.
    Raises LookupError if the job does not exist."""
    for name, value in (("base", base), ("target", target)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"provisioning_jobs.save_bypass_plan: bad {name}={value!r}")
    if target < base:
        raise ValueError(f"provisioning_jobs.save_bypass_plan: target {target} < base {base}")
    pool = await _require_pool("save_bypass_plan")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE provisioning_jobs
               SET bypass_base_bytes = $2,
                   bypass_target_bytes = $3,
                   updated_at = {_NOW}
             WHERE id = $1 AND bypass_target_bytes IS NULL
            RETURNING *
            """,
            int(job_id), base, target,
        )
        if row is None:
            row = await conn.fetchrow("SELECT * FROM provisioning_jobs WHERE id = $1", int(job_id))
    if row is None:
        raise LookupError(f"provisioning_jobs.save_bypass_plan: job {job_id} not found")
    return _to_job(row)


async def _finish(op: str, sql: str, *args) -> bool:
    pool = await _require_pool(op)
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args) is not None


async def mark_done(job_id: int) -> bool:
    """Open job → 'done'. False if the job was already final."""
    return await _finish(
        "mark_done",
        f"""
        UPDATE provisioning_jobs
           SET status = 'done', done_at = {_NOW}, lease_until = NULL, updated_at = {_NOW}
         WHERE id = $1 AND status IN ('pending', 'running')
        RETURNING id
        """,
        int(job_id),
    )


async def mark_retry(job_id: int, err: str, next_at: datetime) -> bool:
    """Open job → 'pending' again, due at aware-UTC `next_at`."""
    return await _finish(
        "mark_retry",
        f"""
        UPDATE provisioning_jobs
           SET status = 'pending', last_error = $2, next_attempt_at = $3,
               lease_until = NULL, updated_at = {_NOW}
         WHERE id = $1 AND status IN ('pending', 'running')
        RETURNING id
        """,
        int(job_id), str(err)[:MAX_ERROR_LEN], _to_db_utc(next_at),
    )


async def mark_dead(job_id: int, err: str) -> bool:
    """Open job → 'dead' (needs manual action; the caller alerts)."""
    return await _finish(
        "mark_dead",
        f"""
        UPDATE provisioning_jobs
           SET status = 'dead', last_error = $2, lease_until = NULL, updated_at = {_NOW}
         WHERE id = $1 AND status IN ('pending', 'running')
        RETURNING id
        """,
        int(job_id), str(err)[:MAX_ERROR_LEN],
    )


async def get_by_key(key: str) -> Optional[Dict[str, Any]]:
    pool = await _require_pool("get_by_key")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM provisioning_jobs WHERE idempotency_key = $1", key,
        )
    return _to_job(row)
