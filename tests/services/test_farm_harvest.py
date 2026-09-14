"""Farm harvest — commission + anti-double-credit (harvest_plot_atomic).

Locks two things the economy nerf depends on:
  1. Harvest pays FARM_HARVEST_PAYOUT_FACTOR × reward (market commission), and
     early-harvest pays half of that.
  2. A second harvest of the same plot credits NOTHING — the status
     compare-and-set inside the advisory-locked transaction makes it
     idempotent, closing the double-tap money-duplication hole.
"""
import json

import pytest

import database.farm as farm
from app.handlers.game import farm_harvest_payout, FARM_HARVEST_PAYOUT_FACTOR

REWARDS = {"oak": 5300, "tomato": 400}


def _plot(status, plant="oak", plot_id=0):
    return {
        "plot_id": plot_id, "status": status, "plant_type": plant,
        "planted_at": None, "ready_at": None, "dead_at": None,
        "notified_ready": False, "notified_12h": False, "notified_dead": False,
        "water_used_at": None, "fertilizer_used_at": None, "storm_shielded": False,
    }


class _FakeConn:
    """Interprets the handful of SQL statements harvest_plot_atomic issues,
    mutating an in-memory user store so credits/resets are observable."""
    def __init__(self, store):
        self.store = store  # {"farm_plots": list, "balance": int}
        self.txns = []

    async def execute(self, query, *args):
        q = " ".join(query.split())
        if "pg_advisory_xact_lock" in q:
            return "SELECT 1"
        if q.startswith("UPDATE users SET farm_plots"):
            self.store["farm_plots"] = json.loads(args[0])
            return "UPDATE 1"
        if q.startswith("UPDATE users SET balance = balance +"):
            self.store["balance"] += args[0]
            return "UPDATE 1"
        if "INSERT INTO balance_transactions" in q:
            self.txns.append(args)
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, query, *args):
        if "farm_plots" in query and "FROM users" in query:
            return {"farm_plots": self.store["farm_plots"]}
        return None

    def transaction(self):
        conn = self

        class _Txn:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Txn()


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


@pytest.fixture
def store_and_pool(monkeypatch):
    store = {"farm_plots": [_plot("ready")], "balance": 0}
    conn = _FakeConn(store)
    monkeypatch.setattr(farm._core, "DB_READY", True, raising=False)

    async def _get_pool():
        return _FakePool(conn)
    monkeypatch.setattr(farm, "get_pool", _get_pool)
    return store, conn


def test_payout_helper_applies_commission():
    assert farm_harvest_payout(5300) == int(5300 * FARM_HARVEST_PAYOUT_FACTOR)
    assert farm_harvest_payout(400) == int(400 * FARM_HARVEST_PAYOUT_FACTOR)
    # default nerf is 50%
    assert farm_harvest_payout(5300) == 2650


async def test_ready_harvest_pays_commissioned_amount(store_and_pool):
    store, _ = store_and_pool
    ok, reason, payout = await farm.harvest_plot_atomic(
        123, 0, REWARDS, FARM_HARVEST_PAYOUT_FACTOR, mode="ready",
    )
    assert ok and reason == "ok"
    assert payout == 2650                    # 5300 * 0.5
    assert store["balance"] == 2650
    assert store["farm_plots"][0]["status"] == "empty"


async def test_double_harvest_credits_only_once(store_and_pool):
    store, _ = store_and_pool
    ok1, _, p1 = await farm.harvest_plot_atomic(123, 0, REWARDS, FARM_HARVEST_PAYOUT_FACTOR, mode="ready")
    ok2, reason2, p2 = await farm.harvest_plot_atomic(123, 0, REWARDS, FARM_HARVEST_PAYOUT_FACTOR, mode="ready")
    assert ok1 is True and p1 == 2650
    assert ok2 is False and reason2 == "wrong_status" and p2 == 0
    assert store["balance"] == 2650          # NOT 5300 — no double credit


async def test_early_harvest_pays_half_of_commissioned(store_and_pool, monkeypatch):
    store, _ = store_and_pool
    store["farm_plots"] = [_plot("growing")]
    ok, reason, payout = await farm.harvest_plot_atomic(
        123, 0, REWARDS, FARM_HARVEST_PAYOUT_FACTOR, mode="early",
    )
    assert ok and reason == "ok"
    assert payout == 1325                     # (5300*0.5)//2
    assert store["balance"] == 1325
    assert store["farm_plots"][0]["status"] == "empty"


async def test_ready_mode_rejects_non_ready_plot(store_and_pool):
    store, _ = store_and_pool
    store["farm_plots"] = [_plot("growing")]
    ok, reason, payout = await farm.harvest_plot_atomic(
        123, 0, REWARDS, FARM_HARVEST_PAYOUT_FACTOR, mode="ready",
    )
    assert ok is False and reason == "wrong_status" and payout == 0
    assert store["balance"] == 0
