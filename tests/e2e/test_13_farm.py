"""Farm — money moves of the in-bot game (merged fix fa5ccdfc, N2–N4 in
docs/audit/06_bug_hunt.md): a double tap on «Собрать» credits once, a double
tap on «Купить грядку» charges once, one storm pass applies once (auto-harvest
paid once, shield one-shot) and neither a rerun of the same storm nor the next
worker pass changes anything.
"""
import asyncio
import json
from datetime import timedelta

import database
from app.handlers.game import FARM_PLOT_PRICE_KOPECKS, PLANT_TYPES, farm_harvest_payout
from app.workers import farm_notifications
from tests.e2e.world import naive, new_user, utcnow
from tests.fakes import telegram as tgf


def _plot(plot_id: int, status: str, plant: str = "greens", **extra):
    now = utcnow()
    return {
        "plot_id": plot_id, "status": status, "plant_type": plant if status != "empty" else None,
        "planted_at": (now - timedelta(days=3)).isoformat() if status != "empty" else None,
        "ready_at": (now - timedelta(minutes=5) if status == "ready" else now + timedelta(days=2)).isoformat()
        if status != "empty" else None,
        "dead_at": None, "notified_ready": False, "notified_12h": False, "notified_dead": False,
        "water_used_at": None, "fertilizer_used_at": None, **extra,
    }


async def _seed_farm(e2e, u, plots, *, balance_kopecks=0):
    await e2e.register(u)
    await e2e.pool.execute(
        "UPDATE users SET farm_plots=$2::jsonb, farm_plot_count=$3, balance=$4 WHERE telegram_id=$1",
        u.id, json.dumps(plots), len(plots), balance_kopecks)


async def _plots(e2e, tg):
    raw = await e2e.val("SELECT farm_plots FROM users WHERE telegram_id=$1", tg)
    return json.loads(raw) if isinstance(raw, str) else raw


async def _tx(e2e, tg, source):
    return await e2e.rows(
        "SELECT amount FROM balance_transactions WHERE user_id=$1 AND source=$2 ORDER BY id", tg, source)


async def _double_tap(e2e, u, data):
    await asyncio.gather(e2e._post_update(tgf.callback(u, data), settle=False),
                         e2e._post_update(tgf.callback(u, data), settle=False))
    await e2e.settle()


async def test_double_tap_harvest_credits_once(e2e):
    u = new_user()
    await _seed_farm(e2e, u, [_plot(0, "ready", "greens")])
    await _double_tap(e2e, u, "farm_harvest_0")

    credits = await _tx(e2e, u.id, "farm_harvest")
    assert len(credits) == 1, credits
    amount = int(credits[0]["amount"])
    listed = PLANT_TYPES["greens"]["reward"]
    assert amount in (farm_harvest_payout(listed), farm_harvest_payout(listed * 100)), amount
    assert int(await e2e.val("SELECT balance FROM users WHERE telegram_id=$1", u.id)) == amount
    assert (await _plots(e2e, u.id))[0]["status"] == "empty"


async def test_double_tap_buy_plot_charges_once(e2e):
    u = new_user()
    await _seed_farm(e2e, u, [_plot(0, "empty")], balance_kopecks=100_000)
    await _double_tap(e2e, u, "farm_buy_plot:1")

    assert await e2e.val("SELECT farm_plot_count FROM users WHERE telegram_id=$1", u.id) == 2
    assert int(await e2e.val("SELECT balance FROM users WHERE telegram_id=$1", u.id)) == 100_000 - FARM_PLOT_PRICE_KOPECKS
    assert len(await _tx(e2e, u.id, "farm_buy_plot")) == 1
    assert len(await _plots(e2e, u.id)) == 2


async def test_storm_pass_applies_once(e2e):
    """Offline user: unshielded growing plot → auto-harvest at 50 % (paid once);
    shielded plot survives, shield consumed. Online user: plot dies, nothing paid.
    A rerun of the same storm and the next worker pass change nothing."""
    offline, online = new_user(), new_user()
    await _seed_farm(e2e, offline, [_plot(0, "growing", "potato"), _plot(1, "growing", "tomato", storm_shielded=True)])
    await _seed_farm(e2e, online, [_plot(0, "growing", "potato")])
    storm_id = await e2e.val("SELECT id FROM farm_storms WHERE executed_at IS NULL ORDER BY scheduled_at LIMIT 1")
    assert storm_id, "init_db seeds a pending storm"
    announced = utcnow() - timedelta(hours=3)
    await e2e.pool.execute("UPDATE farm_storms SET announced_at=$2, scheduled_at=$3 WHERE id=$1",
                           storm_id, naive(announced), naive(utcnow() - timedelta(minutes=1)))
    await e2e.pool.execute("UPDATE users SET last_seen_at=$2 WHERE telegram_id=$1", offline.id,
                           naive(announced - timedelta(hours=1)))
    await e2e.pool.execute("UPDATE users SET last_seen_at=$2 WHERE telegram_id=$1", online.id, naive(utcnow()))

    await farm_notifications.farm_storm_iteration(e2e.bot)
    await e2e.settle()

    assert await e2e.val("SELECT executed_at FROM farm_storms WHERE id=$1", storm_id) is not None
    off = await _plots(e2e, offline.id)
    assert off[0]["status"] == "empty", off[0]
    assert off[1]["status"] == "growing" and off[1].get("storm_shielded") is False, off[1]
    assert off[1].get("storm_survived") == storm_id
    paid = await _tx(e2e, offline.id, "farm_storm_auto_harvest")
    assert len(paid) == 1 and int(paid[0]["amount"]) > 0, paid
    on = await _plots(e2e, online.id)
    assert on[0]["status"] == "dead" and await _tx(e2e, online.id, "farm_storm_auto_harvest") == []
    nxt = await e2e.val("SELECT count(*) FROM farm_storms WHERE executed_at IS NULL")
    assert nxt == 1, "the next storm was not scheduled (or scheduled twice)"

    # rerun of the SAME storm after a worker timeout (N3): the survivor is not killed
    snapshot = await _plots(e2e, offline.id)
    await database.execute_storm_for_user(offline.id, snapshot, naive(utcnow()), announced,
                                          {k: v["reward"] for k, v in PLANT_TYPES.items()}, storm_id=storm_id)
    assert await _plots(e2e, offline.id) == snapshot

    # the next worker pass: the new storm is days away → nothing moves
    before_balance = await e2e.val("SELECT balance FROM users WHERE telegram_id=$1", offline.id)
    await farm_notifications.farm_storm_iteration(e2e.bot)
    await e2e.settle()
    assert await e2e.val("SELECT balance FROM users WHERE telegram_id=$1", offline.id) == before_balance
    assert len(await _tx(e2e, offline.id, "farm_storm_auto_harvest")) == 1
    assert await _plots(e2e, offline.id) == snapshot
