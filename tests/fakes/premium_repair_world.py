"""A seeded world for the premium expireAt repair (app/services/premium_repair
and its dashboard job): the 3.4.3 HTTP panel fake, an in-memory bot DB for
load_repair_inputs / record_premium_repair (their SQL is covered on real
Postgres in tests/db/test_premium_repair_db.py), a fake clock, mocked alerts.

Candidates (premium expireAt > now + 5y), all `would_fix`:
  301  a year bought a month ago; DB row leaked to +10y → shortened too
  302  no payments, no DB row → tomorrow
  303  one old month (past) → tomorrow; bypass-only DB row keeps its placeholder
  304  a month paid 10 days ago, but the bot accounts a later sane date (gift/balance)
Not candidates: a sane premium (305), a bypass entity (306), foreign usernames.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services import admin_alerts, premium_repair, remnawave_api
from app.services.tariffs import extend_expiry
from database import reconciliation as recon
from tests.fakes.remnawave_http import GIB, FakeRemnawaveHTTP

FIVE_Y = timedelta(days=365 * 5)


def pay(pid, tariff, when):
    """A payments row as asyncpg returns it (naive UTC)."""
    return {"id": pid, "tariff": tariff, "effective_at": when.astimezone(timezone.utc).replace(tzinfo=None)}


def sub(exp, *, status="active", source="payment", bypass_only=False):
    return {"expires_at": exp, "status": status, "source": source, "is_bypass_only": bypass_only}


class FakeDB:
    """In-memory stand-in for load_repair_inputs / record_premium_repair."""

    def __init__(self):
        self.subs: dict = {}
        self.pays: dict = {}
        self.records: list = []

    async def load(self, ids):
        out = {}
        for tg in ids:
            s = self.subs.get(tg)
            e = recon._count_payments(self.pays.get(tg, []))
            e.update(db_row=s is not None, db_expires_at=s["expires_at"] if s else None,
                     db_status=s["status"] if s else None, db_source=s["source"] if s else None,
                     db_is_bypass_only=s["is_bypass_only"] if s else False,
                     admin_grant_days=s.get("admin_grant_days", 0) if s else 0)
            out[tg] = e
        return out

    async def record(self, tg, **kw):
        self.records.append((tg, kw))
        s = self.subs.get(tg)
        shortened = False
        if (kw["shorten_db"] and s and not s["is_bypass_only"] and s["source"] != "bypass_only"
                and s["expires_at"] > kw["now"] + FIVE_Y and s["expires_at"] > kw["new_expires_at"]):
            s["expires_at"] = kw["new_expires_at"]
            shortened = True
        return {"log_id": len(self.records), "db_shortened": shortened}


class MemKV:
    """In-memory app_settings for premium_repair_job._store (JSON round trip like the DB)."""

    def __init__(self):
        self.data: dict = {}

    async def load(self, key):
        v = self.data.get(key)
        return None if v is None else json.loads(json.dumps(v))

    async def save(self, key, value):
        self.data[key] = json.loads(json.dumps(value))


def install_world(monkeypatch) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    far = now + timedelta(days=3650)
    http = FakeRemnawaveHTTP().install(monkeypatch)
    db = FakeDB()
    monkeypatch.setattr(recon, "load_repair_inputs", db.load)
    monkeypatch.setattr(recon, "record_premium_repair", db.record)
    monkeypatch.setattr(recon, "get_pool", AsyncMock(return_value=object()))
    clock = {"t": 1000.0}

    async def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(premium_repair, "_clock", lambda: clock["t"])
    monkeypatch.setattr(premium_repair, "_sleep", fake_sleep)
    patch_times: list = []
    real_update = remnawave_api.update_user

    async def timed_update(user_id, **fields):
        patch_times.append(clock["t"])
        return await real_update(user_id, **fields)

    monkeypatch.setattr(remnawave_api, "update_user", timed_update)
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", alerts)

    paid_301 = now - timedelta(days=30)
    http.seed_premium(301, far)
    http.seed_bypass(301, 5 * GIB)
    db.pays[301] = [pay(11, "plus_365", paid_301)]
    db.subs[301] = sub(far)
    http.seed_premium(302, far)
    http.seed_premium(303, far)
    http.seed_bypass(303, GIB)
    db.pays[303] = [pay(31, "basic_30", now - timedelta(days=700))]
    db.subs[303] = sub(far, source="bypass_only", bypass_only=True)
    http.seed_premium(304, far)
    db.pays[304] = [pay(41, "basic_30", now - timedelta(days=10))]
    db.subs[304] = sub(now + timedelta(days=200))
    http.seed_premium(305, now + timedelta(days=400))
    http.seed_bypass(306, GIB)
    http.seed("tg_abc_premium", tg=None, limit=0, expire_at=far)
    http.seed("tg_307_premium_old", tg=307, limit=0, expire_at=far)
    http.seed("vip_308", tg=308, limit=0, expire_at=far)
    return SimpleNamespace(http=http, db=db, now=now, far=far, clock=clock, patch_times=patch_times,
                           alerts=alerts, target_301=extend_expiry(paid_301, 365))


class FakePool:
    """database.core.get_pool() for the CLI's `SELECT 1` probe (it never calls init_db)."""

    class _Conn:
        async def fetchval(self, sql):
            return 1

    class _Ctx:
        async def __aenter__(self):
            return FakePool._Conn()

        async def __aexit__(self, *exc):
            return False

    def acquire(self):
        return FakePool._Ctx()


def patches(http) -> list:
    return [r for r in http.requests if r[0] == "PATCH"]
