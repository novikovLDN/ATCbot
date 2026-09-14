"""P1: the farm notifications worker wrote a stale copy of the whole farm back.

farm_notifications_iteration read every user's farm_plots once, sent Telegram
messages, then saved the WHOLE saved copy (save_farm_plots, no lock, no
re-read). If the user harvested a ready plot in between (harvest_plot_atomic:
+balance, plot reset to empty), the stale write put the ripe plant back —
the user harvested and got paid a second time. The same overwrite erased a
paid storm shield or a newly bought plot.

Now the worker only sets its flags (status / notified_*) under the user's lock
on plots that are still the same planting (plot_id + plant_type + planted_at)
and still growing/ready.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import database
import database.core as db_core
import database.farm as db_farm
from app.workers import farm_notifications as fn

TG = 9090
NOW = datetime.now(timezone.utc)
PLANTED = (NOW - timedelta(days=4)).isoformat()


def _ripe_plot(pid):
    return {
        "plot_id": pid, "status": "growing", "plant_type": "tomato", "planted_at": PLANTED,
        "ready_at": (NOW - timedelta(hours=1)).isoformat(),
        "dead_at": (NOW + timedelta(days=2)).isoformat(),
        "notified_ready": False, "notified_12h": False, "notified_dead": False,
        "storm_shielded": False,
    }


def _empty_plot(pid):
    return {"plot_id": pid, "status": "empty", "plant_type": None, "planted_at": None,
            "ready_at": None, "dead_at": None, "notified_ready": False,
            "notified_12h": False, "notified_dead": False, "storm_shielded": False}


class _Store:
    """users.farm_plots of one user, shared by the fake pool and save_farm_plots."""

    def __init__(self, plots):
        self.plots = json.loads(json.dumps(plots))


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, store):
        self.store = store

    def transaction(self):
        return _Tx()

    async def execute(self, sql, *args):
        if "UPDATE users SET farm_plots" in sql:
            self.store.plots = json.loads(args[0])
        return "OK"

    async def fetchrow(self, sql, *args):
        assert "farm_plots" in sql and "FOR UPDATE" in sql
        return {"farm_plots": json.dumps(self.store.plots)}


class _Pool:
    def __init__(self, store):
        self.store = store

    def acquire(self):
        conn = _Conn(self.store)

        class _A:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False
        return _A()


@pytest.fixture
def world(monkeypatch):
    stale = [_ripe_plot(0), _ripe_plot(1)]
    store = _Store(stale)
    monkeypatch.setattr(db_core, "DB_READY", True)

    async def get_pool():
        return _Pool(store)

    async def save_farm_plots(tg, plots):          # what the real one does, unlocked
        store.plots = json.loads(json.dumps(plots))

    async def get_users_with_active_farm():
        # the read the worker does at the start of its iteration...
        rows = [{"telegram_id": TG, "farm_plots": json.dumps(stale), "farm_plot_count": 2}]
        # ...then the user harvests plot 0 before the worker writes back
        store.plots[0] = _empty_plot(0)
        return rows

    monkeypatch.setattr(db_farm, "get_pool", get_pool)
    monkeypatch.setattr(database, "save_farm_plots", save_farm_plots)
    monkeypatch.setattr(database, "get_users_with_active_farm", get_users_with_active_farm)
    return store


async def test_harvested_plot_is_not_resurrected(world):
    bot = AsyncMock()
    await fn.farm_notifications_iteration(bot)

    assert world.plots[0]["status"] == "empty", "the harvested plant must not come back"
    assert world.plots[1]["status"] == "ready" and world.plots[1]["notified_ready"] is True
    assert bot.send_message.await_count == 2        # notifications unchanged
