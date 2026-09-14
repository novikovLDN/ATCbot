"""Farm (N2 / N3 / N4, docs/audit/06_bug_hunt.md §3).

N4: buying a plot, planting, watering and fertilizing did read → check → debit →
save the WHOLE stale copy, with no lock. A double tap on "buy plot" charged twice
for one plot; two concurrent actions overwrote each other (a planting or a
watering was lost). Now every action is one locked read-modify-write
(pg_advisory_xact_lock + FOR UPDATE, re-check on the fresh row).

N2: the storm computed the new plots from the list read before the loop and
wrote it back — a plot harvested (paid) in between was auto-harvested and paid
again. Now it recomputes from the locked fresh row.

N3: a storm interrupted by the worker timeout was executed again 30 min later and
killed the plots its first pass had saved with a PAID shield. Now a plot that
survived storm N carries the marker `storm_survived = N` and is skipped by a rerun.
"""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import database
import database.core as db_core
import database.farm as db_farm
from app.handlers import game
from app.workers import farm_notifications as fn

TG = 7070
NOW = datetime.now(timezone.utc)
PRICE = game.FARM_PLOT_PRICE_KOPECKS
READY_AT = NOW + timedelta(days=3)


def _empty(pid):
    return {"plot_id": pid, "status": "empty", "plant_type": None, "planted_at": None,
            "ready_at": None, "dead_at": None, "notified_ready": False, "notified_12h": False,
            "notified_dead": False, "water_used_at": None, "fertilizer_used_at": None}


def _growing(pid, *, shielded=False, ready_at=READY_AT):
    return {**_empty(pid), "status": "growing", "plant_type": "tomato",
            "planted_at": (NOW - timedelta(days=1)).isoformat(),
            "ready_at": ready_at.isoformat(), "dead_at": (ready_at + timedelta(hours=24)).isoformat(),
            "storm_shielded": shielded}


class Store:
    """One user's row: farm_plots, farm_plot_count, balance."""

    def __init__(self, plots, *, count=1, balance=0):
        self.plots = copy.deepcopy(plots)
        self.count = count
        self.balance = balance
        self.debits: list = []
        self.lock = asyncio.Lock()      # pg_advisory_xact_lock(telegram_id)


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self.conn.holds:
            self.conn.holds = False
            self.conn.store.lock.release()
        return False


class _Conn:
    def __init__(self, store):
        self.store = store
        self.holds = False

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *args):
        await asyncio.sleep(0)
        q = " ".join(sql.split())
        s = self.store
        if "pg_advisory_xact_lock" in q:
            await s.lock.acquire()
            self.holds = True
        elif q.startswith("UPDATE users SET farm_plots = $1::jsonb, farm_plot_count = $2"):
            s.plots, s.count = json.loads(args[0]), args[1]
        elif q.startswith("UPDATE users SET farm_plots"):
            s.plots = json.loads(args[0])
        elif q.startswith("UPDATE users SET balance = balance -"):
            s.balance -= args[0]
            s.debits.append(args[0])
        elif q.startswith("UPDATE users SET balance = balance +"):
            s.balance += args[0]
        return "OK"

    async def fetchrow(self, sql, *args):
        await asyncio.sleep(0)
        assert "FOR UPDATE" in sql
        s = self.store
        return {"farm_plots": json.dumps(s.plots), "farm_plot_count": s.count, "balance": s.balance}


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
def make_world(monkeypatch):
    def _make(plots, **kw):
        store = Store(plots, **kw)
        monkeypatch.setattr(db_core, "DB_READY", True)

        async def get_pool():
            return _Pool(store)
        monkeypatch.setattr(db_farm, "get_pool", get_pool)

        # the unlocked helpers the handlers used before (what the real ones do)
        async def get_farm_data(_tg):
            await asyncio.sleep(0)
            return copy.deepcopy(store.plots), store.count, store.balance

        async def save_farm_plots(_tg, plots):
            await asyncio.sleep(0)
            store.plots = copy.deepcopy(plots)

        async def update_farm_plot_count(_tg, n):
            store.count = n

        async def decrease_balance(*, telegram_id, amount, source, description=None, conn=None):
            await asyncio.sleep(0)
            k = round(amount * 100)
            if store.balance < k:
                return False
            store.balance -= k
            store.debits.append(k)
            return True

        monkeypatch.setattr(database, "get_farm_data", get_farm_data)
        monkeypatch.setattr(database, "save_farm_plots", save_farm_plots)
        monkeypatch.setattr(database, "update_farm_plot_count", update_farm_plot_count)
        monkeypatch.setattr(database, "decrease_balance", decrease_balance)
        monkeypatch.setattr(database, "get_pool", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(game, "ensure_db_ready_callback", AsyncMock(return_value=True))
        monkeypatch.setattr(game, "resolve_user_language", AsyncMock(return_value="ru"))
        monkeypatch.setattr(game, "_get_imminent_storm", AsyncMock(return_value=None))
        return store
    return _make


def _cb(data):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = TG
    cb.answer = AsyncMock()
    return cb


@pytest.fixture
def no_render(monkeypatch):
    monkeypatch.setattr(game, "_render_farm", AsyncMock())


# ── N4: buy / plant / water / fertilize ─────────────────────────────────


async def test_double_tap_buy_plot_charges_once(make_world, no_render):
    store = make_world([_empty(0)], count=1, balance=2 * PRICE)

    await asyncio.gather(
        game.callback_farm_buy_plot(_cb("farm_buy_plot:1"), MagicMock()),
        game.callback_farm_buy_plot(_cb("farm_buy_plot:1"), MagicMock()),
    )

    assert store.debits == [PRICE], "two taps on the same screen = ONE plot, ONE charge"
    assert store.count == 2 and [p["plot_id"] for p in store.plots] == [0, 1]
    assert store.balance == PRICE


async def test_buy_plot_legacy_button_still_buys(make_world, no_render):
    store = make_world([_empty(0)], count=1, balance=PRICE)

    await game.callback_farm_buy_plot(_cb("farm_buy_plot"), MagicMock())

    assert store.debits == [PRICE] and store.count == 2 and len(store.plots) == 2


async def test_buy_plot_during_planting_keeps_the_seedling(make_world, no_render):
    store = make_world([_empty(0)], count=1, balance=PRICE)

    await asyncio.gather(
        game.callback_farm_plant(_cb("farm_plant_0_tomato"), MagicMock()),
        game.callback_farm_buy_plot(_cb("farm_buy_plot:1"), MagicMock()),
    )

    assert store.plots[0]["status"] == "growing" and store.plots[0]["plant_type"] == "tomato"
    assert store.count == 2 and len(store.plots) == 2 and store.debits == [PRICE]


async def test_double_tap_water_applies_once(make_world, no_render):
    store = make_world([_growing(0)])

    await asyncio.gather(
        game.callback_farm_water(_cb("farm_water_0"), MagicMock()),
        game.callback_farm_water(_cb("farm_water_0"), MagicMock()),
    )

    assert datetime.fromisoformat(store.plots[0]["ready_at"]) == READY_AT - timedelta(hours=6)
    assert store.plots[0]["water_used_at"] is not None


async def test_concurrent_water_and_fertilize_both_apply(make_world, no_render):
    store = make_world([_growing(0)])

    await asyncio.gather(
        game.callback_farm_water(_cb("farm_water_0"), MagicMock()),
        game.callback_farm_fert(_cb("farm_fert_0"), MagicMock()),
    )

    assert datetime.fromisoformat(store.plots[0]["ready_at"]) == READY_AT - timedelta(hours=8)
    assert store.plots[0]["water_used_at"] and store.plots[0]["fertilizer_used_at"]


async def test_double_tap_plant_plants_once(make_world, no_render):
    store = make_world([_empty(0)])

    await asyncio.gather(
        game.callback_farm_plant(_cb("farm_plant_0_tomato"), MagicMock()),
        game.callback_farm_plant(_cb("farm_plant_0_oak"), MagicMock()),
    )

    assert store.plots[0]["status"] == "growing"
    assert store.plots[0]["plant_type"] in ("tomato", "oak")
    assert store.debits == []


async def test_farm_screen_status_sync_does_not_resurrect_a_harvested_plot(make_world, monkeypatch):
    """_render_farm synced growing→ready by saving its whole (stale) copy: a plot
    harvested in between came back ripe and could be harvested (paid) again."""
    store = make_world([_growing(0, ready_at=NOW - timedelta(hours=1))])
    stale = copy.deepcopy(store.plots)

    async def get_farm_data(_tg):
        store.plots[0] = _empty(0)          # the user harvests right after our read
        return copy.deepcopy(stale), 1, 0
    monkeypatch.setattr(database, "get_farm_data", get_farm_data)
    monkeypatch.setattr(game, "safe_edit_text", AsyncMock())

    await game._render_farm(_cb("game_farm"), MagicMock())

    assert store.plots[0]["status"] == "empty", "the harvested plant must not come back"


# ── N2: storm from the current state ───────────────────────────────────


async def test_storm_uses_the_current_plots_not_the_stale_list(make_world):
    store = make_world([_empty(0)])                 # already harvested (paid)
    stale_list = [_growing(0)]

    result = await database.execute_storm_for_user(
        TG, stale_list, None, NOW - timedelta(hours=1), {"tomato": 200})

    assert result["autoharv"] == 0 and result["killed"] == 0
    assert store.balance == 0, "a harvested plot must not be auto-harvested (paid) again"
    assert store.plots == [_empty(0)]


# ── N3: an interrupted storm is not applied twice ───────────────────────


async def test_interrupted_storm_does_not_kill_shielded_plots_on_rerun(make_world, monkeypatch):
    store = make_world([_growing(0, shielded=True), _growing(1)])
    storm = {"id": 7, "scheduled_at": NOW - timedelta(minutes=5),
             "announced_at": NOW - timedelta(hours=24), "executed_at": None}
    online = (NOW + timedelta(minutes=1)).replace(tzinfo=None)

    async def list_users():
        if any(p["status"] == "growing" for p in store.plots):
            return [{"telegram_id": TG, "farm_plots": copy.deepcopy(store.plots), "last_seen_at": online}]
        return []

    mark = AsyncMock(side_effect=[asyncio.TimeoutError(), True])
    monkeypatch.setattr(database, "get_pending_storm", AsyncMock(return_value=storm))
    monkeypatch.setattr(database, "list_users_with_growing_plots", list_users)
    monkeypatch.setattr(database, "mark_storm_executed", mark)
    monkeypatch.setattr(database, "schedule_next_storm", AsyncMock(return_value=8))
    bot = AsyncMock()

    with pytest.raises(asyncio.TimeoutError):       # first pass interrupted before "executed"
        await fn.farm_storm_iteration(bot)
    assert store.plots[0]["status"] == "growing" and store.plots[1]["status"] == "dead"

    await fn.farm_storm_iteration(bot)              # the rerun 30 minutes later

    assert store.plots[0]["status"] == "growing", "the paid shield already saved this plot"
    assert bot.send_message.await_count == 1, "the storm wrap-up is sent once"
