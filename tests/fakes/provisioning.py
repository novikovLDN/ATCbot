"""In-memory stand-ins for the provisioning outbox and its DB helpers.

Shared by tests/services/test_provisioning_apply.py (T4) and
tests/services/test_provisioning_worker.py (T5).

  FakeConn  — the caller's billing connection (is_in_transaction)
  FakeJobs  — database.provisioning_jobs with the SQL's semantics: idempotent
              insert, per-user FIFO claim with lease (an expired 'running'
              lease is claimable again), one-shot bypass plan, status
              transitions, get_by_key
  FakeDB    — subscriptions cache helpers + payment_errors logger, via the
              real `database.*` names
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import database
import database.provisioning_jobs as pj

from tests.fakes.panel import FakePanel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FakeConn:
    def __init__(self, in_tx: bool = True):
        self.in_tx = in_tx
        self.calls = []

    def is_in_transaction(self) -> bool:
        return self.in_tx


class FakeJobs:
    """In-memory database.provisioning_jobs with the SQL's semantics."""

    def __init__(self, panel: FakePanel):
        self.panel = panel
        self.rows = {}
        self._ids = itertools.count(1)

    async def insert_job(self, conn, *, key, telegram_id, source, ent, premium_until, context=None):
        conn.calls.append(("insert_job", key, conn.is_in_transaction()))
        for row in self.rows.values():
            if row["idempotency_key"] == key:
                return row["id"]
        now = utcnow()
        job_id = next(self._ids)
        self.rows[job_id] = {
            "id": job_id, "idempotency_key": key, "telegram_id": telegram_id,
            "source": source, "tariff_key": ent.tariff_key, "premium_until": premium_until,
            "bypass_add_bytes": int(ent.bypass_bytes), "bypass_base_bytes": None,
            "bypass_target_bytes": None, "status": "pending", "attempts": 0,
            "next_attempt_at": now, "lease_until": None, "last_error": None,
            "context": dict(context or {}), "created_at": now, "updated_at": now, "done_at": None,
        }
        return job_id

    async def claim(self, job_id=None, *, lease_s=120):
        now = utcnow()

        def is_open(r):
            return r["status"] in ("pending", "running")

        cands = []
        for r in self.rows.values():
            if job_id is not None and r["id"] != job_id:
                continue
            if not (r["status"] == "pending" or (r["status"] == "running" and r["lease_until"] < now)):
                continue
            if job_id is None and r["next_attempt_at"] > now:
                continue
            if any(e["telegram_id"] == r["telegram_id"] and e["id"] < r["id"] and is_open(e)
                   for e in self.rows.values()):
                continue
            cands.append(r)
        if not cands:
            return None
        r = min(cands, key=lambda x: (x["next_attempt_at"], x["id"]))
        r.update(status="running", lease_until=now + timedelta(seconds=lease_s),
                 attempts=r["attempts"] + 1, updated_at=now)
        return dict(r)

    async def save_bypass_plan(self, job_id, base, target):
        self.panel.calls.append(("save_bypass_plan", job_id, base, target))
        r = self.rows[job_id]
        if r["bypass_target_bytes"] is None:
            r.update(bypass_base_bytes=base, bypass_target_bytes=target)
        return dict(r)

    def _finish(self, job_id, **fields):
        r = self.rows[job_id]
        if r["status"] not in ("pending", "running"):
            return False
        r.update(lease_until=None, updated_at=utcnow(), **fields)
        return True

    async def mark_done(self, job_id):
        return self._finish(job_id, status="done", done_at=utcnow())

    async def mark_retry(self, job_id, err, next_at):
        assert next_at.tzinfo is not None, "UTC contract: aware next_at"
        return self._finish(job_id, status="pending", last_error=err, next_attempt_at=next_at)

    async def mark_dead(self, job_id, err):
        return self._finish(job_id, status="dead", last_error=err)

    async def get_by_key(self, key):
        for r in self.rows.values():
            if r["idempotency_key"] == key:
                return dict(r)
        return None

    def install(self, monkeypatch) -> "FakeJobs":
        for name in ("insert_job", "claim", "save_bypass_plan", "mark_done", "mark_retry",
                     "mark_dead", "get_by_key"):
            monkeypatch.setattr(pj, name, getattr(self, name))
        return self

    def job(self, job_id):
        return self.rows[job_id]

    def make_due(self, job_id):
        """Simulate the backoff elapsing: the job is due now."""
        self.rows[job_id]["next_attempt_at"] = utcnow() - timedelta(seconds=1)


class FakeDB:
    """subscriptions cache columns + payment_errors, via the real helper names."""

    def __init__(self):
        self.subs = {}
        self.cache = defaultdict(dict)
        self.payment_errors = []

    async def get_subscription_any(self, tg):
        return self.subs.get(tg)

    async def get_remnawave_premium_uuid(self, tg):
        return self.cache[tg].get("premium_uuid")

    async def get_remnawave_premium_id(self, tg):
        return self.cache[tg].get("premium_id")

    async def set_remnawave_premium_uuid_and_url(self, tg, uuid, sub_url, *, short_uuid=None, mark_migrated=True):
        self.cache[tg].update(premium_uuid=uuid, premium_url=sub_url, premium_short=short_uuid)

    async def set_remnawave_premium_id(self, tg, numeric_id):
        self.cache[tg]["premium_id"] = int(numeric_id)

    async def set_remnawave_bypass_cache(self, tg, uuid, sub_url, short_uuid):
        c = self.cache[tg]
        for col, val in (("bypass_uuid", uuid), ("bypass_url", sub_url), ("bypass_short", short_uuid)):
            if val is not None:  # COALESCE like the real UPDATE
                c[col] = val

    async def set_remnawave_id(self, tg, numeric_id):
        self.cache[tg]["bypass_id"] = int(numeric_id)

    async def log_payment_error(self, **kw):
        self.payment_errors.append(kw)
        return len(self.payment_errors)

    def install(self, monkeypatch) -> "FakeDB":
        for name in (
            "get_subscription_any", "get_remnawave_premium_uuid", "get_remnawave_premium_id",
            "set_remnawave_premium_uuid_and_url", "set_remnawave_premium_id",
            "set_remnawave_bypass_cache", "set_remnawave_id", "log_payment_error",
        ):
            monkeypatch.setattr(database, name, getattr(self, name))
        return self
