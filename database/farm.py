"""
Farm storm mechanic — schedule, announce, execute.

The Storm hits every 7–10 days (random).  24h before it lands every user
with a growing plot gets a push.  Each plot can be shielded for 10/20/30
RUB (tiered by plant reward); unshielded growing plots die at execution
time — unless the user was OFFLINE during the warning window, in which
case the plot is auto-harvested at 50% reward to the user's balance.
"""
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


# Interval between storms (days, uniform random)
STORM_MIN_INTERVAL_DAYS = 7
STORM_MAX_INTERVAL_DAYS = 10

# Warning window before execution (hours)
STORM_ANNOUNCE_BEFORE_HOURS = 24


# ──────────────────────────────────────────────────────────────────────
# Storm schedule
# ──────────────────────────────────────────────────────────────────────

async def get_pending_storm() -> Optional[Dict[str, Any]]:
    """Return the next unexecuted storm or None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, scheduled_at, announced_at, executed_at
               FROM farm_storms
               WHERE executed_at IS NULL
               ORDER BY scheduled_at ASC
               LIMIT 1"""
        )
        return dict(row) if row else None


async def mark_storm_announced(storm_id: int) -> bool:
    """Stamp announced_at on the storm row.  Idempotent (re-stamp is no-op)."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE farm_storms
               SET announced_at = CURRENT_TIMESTAMP
               WHERE id = $1 AND announced_at IS NULL""",
            storm_id,
        )
        return result.endswith(" 1")


async def mark_storm_executed(
    storm_id: int,
    killed: int,
    shielded: int,
    auto_harvested: int,
    auto_harvested_rub: int,
) -> bool:
    """Stamp executed_at + counters.  Caller must also schedule the next storm."""
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE farm_storms
               SET executed_at = CURRENT_TIMESTAMP,
                   killed_count = $2,
                   shielded_count = $3,
                   auto_harvested_count = $4,
                   auto_harvested_rub = $5
               WHERE id = $1 AND executed_at IS NULL""",
            storm_id, killed, shielded, auto_harvested, auto_harvested_rub,
        )
        return result.endswith(" 1")


async def schedule_next_storm(now: Optional[datetime] = None) -> Optional[int]:
    """Schedule next storm 7–10 days out.  Returns the new storm id.

    Guarded by the partial UNIQUE INDEX on (1) WHERE executed_at IS NULL:
    if a pending storm already exists, INSERT silently returns 0 rows and
    we return None.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    days = random.uniform(STORM_MIN_INTERVAL_DAYS, STORM_MAX_INTERVAL_DAYS)
    scheduled_at = _to_db_utc(now + timedelta(days=days))
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO farm_storms (scheduled_at) VALUES ($1)
                   RETURNING id""",
                scheduled_at,
            )
            return row["id"] if row else None
        except Exception as e:
            # Partial unique index — pending storm already present.
            logger.info("schedule_next_storm: cannot insert (%s)", type(e).__name__)
            return None


# ──────────────────────────────────────────────────────────────────────
# Plot operations during storm
# ──────────────────────────────────────────────────────────────────────

async def list_users_with_growing_plots() -> List[Dict[str, Any]]:
    """Users with at least one growing plot — for the announcement push
    and for execution iteration.  Returns telegram_id, farm_plots, last_seen_at."""
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, farm_plots, last_seen_at
               FROM users
               WHERE farm_plots IS NOT NULL
                 AND jsonb_typeof(farm_plots) = 'array'
                 AND EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements(farm_plots) p
                     WHERE p->>'status' = 'growing'
                 )"""
        )
        result = []
        for row in rows:
            plots = row["farm_plots"]
            if isinstance(plots, str):
                plots = json.loads(plots)
            result.append({
                "telegram_id": row["telegram_id"],
                "farm_plots": plots,
                "last_seen_at": row["last_seen_at"],
            })
        return result


async def apply_storm_shield_atomic(
    telegram_id: int,
    plot_id: int,
    cost_kopecks: int,
    *,
    deduct_balance: bool,
    conn=None,
) -> Tuple[bool, str]:
    """Set storm_shielded=true on one plot atomically.

    If deduct_balance=True, also deducts cost_kopecks from balance under the
    same advisory lock (path for purchases paid via balance).  Otherwise
    just flips the flag (path for purchases paid via Lava/Платега, where
    the balance is not used).

    If `conn` is passed, runs on the caller's connection without acquiring
    a new one — caller owns the transaction and the advisory lock.

    Returns (success, reason).  reason values:
        "ok", "plot_not_growing", "plot_not_found", "insufficient_balance",
        "already_shielded", "user_not_found", "db_not_ready"
    """
    if not _core.DB_READY:
        return False, "db_not_ready"

    async def _do(c):
        row = await c.fetchrow(
            "SELECT farm_plots, balance FROM users WHERE telegram_id = $1 FOR UPDATE",
            telegram_id,
        )
        if not row:
            return False, "user_not_found"
        plots = row["farm_plots"]
        if isinstance(plots, str):
            plots = json.loads(plots)
        if not isinstance(plots, list):
            return False, "plot_not_found"

        target_idx = None
        for i, p in enumerate(plots):
            if int(p.get("plot_id", -1)) == plot_id:
                target_idx = i
                break
        if target_idx is None:
            return False, "plot_not_found"
        target = plots[target_idx]
        if target.get("status") != "growing":
            return False, "plot_not_growing"
        if target.get("storm_shielded") is True:
            return False, "already_shielded"

        if deduct_balance:
            current_balance = row["balance"] or 0
            if current_balance < cost_kopecks:
                return False, "insufficient_balance"
            await c.execute(
                "UPDATE users SET balance = balance - $1 WHERE telegram_id = $2",
                cost_kopecks, telegram_id,
            )
            await c.execute(
                """INSERT INTO balance_transactions
                   (user_id, amount, type, source, description)
                   VALUES ($1, $2, 'subscription_payment', 'farm_storm_shield', $3)""",
                telegram_id, -cost_kopecks, f"Storm shield for plot {plot_id}",
            )

        plots[target_idx] = {**target, "storm_shielded": True}
        await c.execute(
            "UPDATE users SET farm_plots = $1::jsonb WHERE telegram_id = $2",
            json.dumps(plots), telegram_id,
        )
        logger.info(
            "FARM_STORM_SHIELD_APPLIED user=%s plot=%s cost_kopecks=%s via_balance=%s",
            telegram_id, plot_id, cost_kopecks, deduct_balance,
        )
        return True, "ok"

    # Caller-managed connection — assume caller holds the txn and advisory lock.
    if conn is not None:
        return await _do(conn)

    pool = await get_pool()
    if pool is None:
        return False, "db_not_ready"
    async with pool.acquire() as own_conn:
        async with own_conn.transaction():
            await own_conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
            return await _do(own_conn)


async def execute_storm_for_user(
    telegram_id: int,
    farm_plots: List[Dict[str, Any]],
    last_seen_at: Optional[datetime],
    announced_at: datetime,
    plant_rewards: Dict[str, int],
) -> Tuple[int, int, int, int]:
    """Apply storm effects to one user's plots.

    For each growing plot:
        - shielded → keep growing, reset shield (one-shot), count as shielded
        - online user (last_seen >= announced_at) → status=dead
        - offline user → auto-harvest at 50% reward, status=empty (reuse plot)

    plant_rewards maps plant_type → full reward in kopecks.

    Returns (killed_n, shielded_n, autoharv_n, autoharv_rub_total).
    """
    if not _core.DB_READY:
        return (0, 0, 0, 0)
    pool = await get_pool()
    if pool is None:
        return (0, 0, 0, 0)

    # Normalize both sides to aware UTC so naive-from-DB and aware-from-caller
    # can compare cleanly.
    last_seen_aware = _from_db_utc(last_seen_at) if last_seen_at is not None else None
    announced_aware = announced_at if announced_at.tzinfo is not None else announced_at.replace(tzinfo=timezone.utc)
    is_online = last_seen_aware is not None and last_seen_aware >= announced_aware

    killed = 0
    shielded = 0
    autoharv = 0
    autoharv_kopecks = 0

    new_plots = []
    for p in farm_plots:
        if p.get("status") != "growing":
            new_plots.append(p)
            continue

        if p.get("storm_shielded") is True:
            shielded += 1
            new_plots.append({**p, "storm_shielded": False})  # one-shot reset
            continue

        if is_online:
            killed += 1
            new_plots.append({
                **p,
                "status": "dead",
                "dead_at": datetime.now(timezone.utc).isoformat(),
                "storm_shielded": False,
            })
        else:
            reward = plant_rewards.get(p.get("plant_type"), 0)
            half = reward // 2
            autoharv += 1
            autoharv_kopecks += half
            new_plots.append({
                "plot_id": p.get("plot_id"),
                "status": "empty",
                "plant_type": None,
                "planted_at": None,
                "ready_at": None,
                "dead_at": None,
                "notified_ready": False,
                "notified_12h": False,
                "notified_dead": False,
                "water_used_at": None,
                "fertilizer_used_at": None,
                "storm_shielded": False,
            })

    if killed == 0 and shielded == 0 and autoharv == 0:
        return (0, 0, 0, 0)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
            await conn.execute(
                "UPDATE users SET farm_plots = $1::jsonb WHERE telegram_id = $2",
                json.dumps(new_plots), telegram_id,
            )
            if autoharv_kopecks > 0:
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    autoharv_kopecks, telegram_id,
                )
                await conn.execute(
                    """INSERT INTO balance_transactions
                       (user_id, amount, type, source, description)
                       VALUES ($1, $2, 'cashback', 'farm_storm_auto_harvest',
                               'Auto-harvest during storm (50%)')""",
                    telegram_id, autoharv_kopecks,
                )

    logger.info(
        "FARM_STORM_APPLIED user=%s online=%s killed=%s shielded=%s autoharv=%s autoharv_kopecks=%s",
        telegram_id, is_online, killed, shielded, autoharv, autoharv_kopecks,
    )
    return (killed, shielded, autoharv, autoharv_kopecks)


# ──────────────────────────────────────────────────────────────────────
# last_seen
# ──────────────────────────────────────────────────────────────────────

async def touch_last_seen(telegram_id: int) -> None:
    """Stamp last_seen_at = NOW().  Called from a lightweight middleware on
    every callback/message.  Fire-and-forget — never raises."""
    if not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_seen_at = CURRENT_TIMESTAMP WHERE telegram_id = $1",
                telegram_id,
            )
    except Exception as e:
        logger.warning("touch_last_seen failed user=%s err=%s", telegram_id, type(e).__name__)
