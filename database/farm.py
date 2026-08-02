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


async def replace_pending_storm_at(scheduled_at: datetime, announce_now: bool = True) -> int:
    """Reschedule (or create) the pending storm at an exact moment.

    Used by the admin "schedule in N hours" tool: replaces the existing
    pending storm in-place so already-purchased shields carry over, and
    optionally stamps announced_at=NOW so the announce-push goes out
    immediately instead of waiting for the worker to notice the 24h window.

    Returns the storm id.
    """
    if not _core.DB_READY:
        raise RuntimeError("DB not ready")
    pool = await get_pool()
    if pool is None:
        raise RuntimeError("DB not ready")
    sched_naive = _to_db_utc(scheduled_at)
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id FROM farm_storms WHERE executed_at IS NULL FOR UPDATE",
            )
            if existing is None:
                row = await conn.fetchrow(
                    """INSERT INTO farm_storms (scheduled_at, announced_at)
                       VALUES ($1, CASE WHEN $2 THEN CURRENT_TIMESTAMP ELSE NULL END)
                       RETURNING id""",
                    sched_naive, announce_now,
                )
                storm_id = row["id"]
            else:
                storm_id = existing["id"]
                await conn.execute(
                    """UPDATE farm_storms
                       SET scheduled_at = $2,
                           announced_at = CASE WHEN $3 THEN CURRENT_TIMESTAMP ELSE NULL END
                       WHERE id = $1""",
                    storm_id, sched_naive, announce_now,
                )
    logger.info(
        "STORM_RESCHEDULED storm_id=%s scheduled_at=%s announce_now=%s",
        storm_id, scheduled_at, announce_now,
    )
    return storm_id


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


GAME_REWARD_ACTION_PREFIX = "game_reward_"


async def get_game_days_granted_this_month(telegram_id: int) -> int:
    """Сколько дней подписки игрок уже получил от игр в текущем месяце.

    Учёт ведётся в audit_log, чтобы не заводить новую таблицу: схема БД
    сейчас управляется двумя конкурирующими механизмами (SQL-миграции и DDL
    в database/core.py), и разводить их — задача подпроекта B.
    """
    if not _core.DB_READY:
        return 0
    pool = await get_pool()
    if pool is None:
        return 0
    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                """
                SELECT COALESCE(SUM(details::int), 0)
                  FROM audit_log
                 WHERE telegram_id = $1
                   AND action LIKE $2
                   AND created_at >= date_trunc('month', CURRENT_TIMESTAMP)
                """,
                telegram_id, f"{GAME_REWARD_ACTION_PREFIX}%",
            )
        return int(total or 0)
    except Exception as e:
        # Счётчик не должен ломать игру: при сбое считаем, что лимит не выбран,
        # но пишем ошибку — иначе потолок молча перестанет работать.
        logger.error("GAME_CAP_READ_FAILED user=%s error=%s", telegram_id, e)
        return 0


async def log_game_reward_days(telegram_id: int, days: int, game: str) -> None:
    """Записать выданные игрой дни подписки для учёта месячного потолка."""
    if days <= 0 or not _core.DB_READY:
        return
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (action, telegram_id, target_user, details)
                VALUES ($1, $2, NULL, $3)
                """,
                f"{GAME_REWARD_ACTION_PREFIX}{game}", telegram_id, str(int(days)),
            )
    except Exception as e:
        logger.error(
            "GAME_REWARD_LOG_FAILED user=%s game=%s days=%s error=%s",
            telegram_id, game, days, e,
        )


async def check_game_days_cap(telegram_id: int, requested_days: int, cap: int) -> tuple:
    """Проверить месячный потолок игровых наград.

    Возвращает (разрешённые_дни, уже_выдано, остаток_до_потолка).
    Ноль в первом элементе означает, что лимит исчерпан.
    """
    if cap <= 0:
        return requested_days, 0, requested_days
    granted = await get_game_days_granted_this_month(telegram_id)
    remaining = max(0, cap - granted)
    return min(requested_days, remaining), granted, remaining


async def harvest_plot_atomic(
    telegram_id: int,
    plot_id: int,
    reward_kopecks: int,
    *,
    expected_status: str = "ready",
    source: str = "farm_harvest",
    description: str = "",
) -> Tuple[bool, str]:
    """Собрать урожай с грядки атомарно: сброс грядки и начисление в одной транзакции.

    Раньше сбор был read-modify-write без блокировки: состояние читалось,
    проверялся статус, начислялась награда и только потом грядка сбрасывалась
    отдельным запросом. Два параллельных клика проходили проверку оба и
    начисляли награду дважды.

    Здесь грядка переводится в 'empty' под тем же advisory-локом, под которым
    начисляется баланс, поэтому второй запрос увидит уже пустую грядку и
    получит отказ.

    Returns (success, reason). reason: "ok", "user_not_found", "plot_not_found",
    "plot_wrong_status", "db_not_ready", "no_reward".
    """
    if not _core.DB_READY:
        return False, "db_not_ready"
    if reward_kopecks <= 0:
        return False, "no_reward"

    pool = await get_pool()
    if pool is None:
        return False, "db_not_ready"

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)

            row = await conn.fetchrow(
                "SELECT farm_plots FROM users WHERE telegram_id = $1 FOR UPDATE",
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
            if target.get("status") != expected_status:
                # Сюда попадает второй параллельный клик: грядку уже собрали.
                return False, "plot_wrong_status"

            plots[target_idx] = {
                "plot_id": plot_id,
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
            }
            await conn.execute(
                "UPDATE users SET farm_plots = $1::jsonb WHERE telegram_id = $2",
                json.dumps(plots), telegram_id,
            )
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                reward_kopecks, telegram_id,
            )
            await conn.execute(
                """INSERT INTO balance_transactions
                   (user_id, amount, type, source, description)
                   VALUES ($1, $2, 'topup', $3, $4)""",
                telegram_id, reward_kopecks, source,
                description or f"Farm harvest plot {plot_id}",
            )
            logger.info(
                "FARM_HARVEST_ATOMIC user=%s plot=%s reward_kopecks=%s source=%s",
                telegram_id, plot_id, reward_kopecks, source,
            )
            return True, "ok"


async def buy_farm_plot_atomic(
    telegram_id: int,
    price_kopecks: int,
    max_plots: int,
    *,
    source: str = "farm_buy_plot",
    description: str = "Покупка грядки",
) -> Tuple[bool, str]:
    """Купить грядку одной транзакцией: списание, грядка и счётчик вместе.

    Раньше это были три отдельных запроса: decrease_balance, save_farm_plots
    и update_farm_plot_count. Сбой между первым и вторым снимал деньги, не
    выдав грядку; сбой между вторым и третьим давал грядку, которую не видел
    счётчик. Параллельные клики к тому же читали одинаковый plot_count и
    создавали две грядки с одним идентификатором.

    Здесь всё под advisory-локом: свежее состояние читается внутри
    транзакции, проверки лимита и баланса делаются по нему же.

    Returns (успех, причина). Причины: "ok", "max_plots_reached",
    "insufficient_balance", "user_not_found", "db_not_ready".
    """
    if not _core.DB_READY:
        return False, "db_not_ready"
    pool = await get_pool()
    if pool is None:
        return False, "db_not_ready"

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)

            row = await conn.fetchrow(
                "SELECT farm_plots, farm_plot_count, balance FROM users "
                "WHERE telegram_id = $1 FOR UPDATE",
                telegram_id,
            )
            if not row:
                return False, "user_not_found"

            plots = row["farm_plots"]
            if isinstance(plots, str):
                plots = json.loads(plots)
            if not isinstance(plots, list):
                plots = []

            # Счётчик и фактическая длина массива могли разойтись из-за старой
            # неатомарной покупки — берём большее, чтобы не выдать грядку
            # с уже занятым идентификатором.
            current_count = max(int(row["farm_plot_count"] or 0), len(plots))
            if current_count >= max_plots:
                return False, "max_plots_reached"

            current_balance = int(row["balance"] or 0)
            if current_balance < price_kopecks:
                return False, "insufficient_balance"

            plots.append({
                "plot_id": current_count,
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

            await conn.execute(
                "UPDATE users SET balance = balance - $1, farm_plots = $2::jsonb, "
                "farm_plot_count = $3 WHERE telegram_id = $4",
                price_kopecks, json.dumps(plots), current_count + 1, telegram_id,
            )
            await conn.execute(
                """INSERT INTO balance_transactions
                   (user_id, amount, type, source, description)
                   VALUES ($1, $2, 'subscription_payment', $3, $4)""",
                telegram_id, -price_kopecks, source, description,
            )
            logger.info(
                "FARM_PLOT_BOUGHT user=%s plot_id=%s price_kopecks=%s",
                telegram_id, current_count, price_kopecks,
            )
            return True, "ok"


async def mark_plot_notified(
    telegram_id: int,
    plot_id: int,
    flag: str,
    *,
    expected_planted_at: Optional[str] = None,
) -> bool:
    """Поставить один флаг уведомления на одной грядке.

    Зачем отдельная функция: воркер уведомлений раньше читал весь массив
    грядок, рассылал сообщения (а это сетевые запросы, они занимают время)
    и записывал массив обратно целиком. Если за это время пользователь
    собирал урожай, его изменение затиралось устаревшим снимком: грядка
    возвращалась в состояние «готова», хотя награда уже начислена.

    Здесь под advisory-локом читается свежее состояние и меняется ровно
    один флаг. Остальные грядки и все поля этой грядки остаются как есть.

    expected_planted_at защищает от гонки другого рода: пока шло
    уведомление, грядку могли собрать и засеять заново. Тогда время
    посадки отличается, и флаг ставить нельзя — он относился к прошлому
    растению.

    Возвращает True, если флаг проставлен.
    """
    allowed = {"notified_ready", "notified_12h", "notified_dead"}
    if flag not in allowed:
        logger.error("MARK_PLOT_NOTIFIED_BAD_FLAG flag=%s user=%s", flag, telegram_id)
        return False
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
            row = await conn.fetchrow(
                "SELECT farm_plots FROM users WHERE telegram_id = $1 FOR UPDATE",
                telegram_id,
            )
            if not row:
                return False
            plots = row["farm_plots"]
            if isinstance(plots, str):
                plots = json.loads(plots)
            if not isinstance(plots, list):
                return False

            for i, p in enumerate(plots):
                if int(p.get("plot_id", -1)) != plot_id:
                    continue
                if expected_planted_at is not None and p.get("planted_at") != expected_planted_at:
                    # Грядку успели собрать и засеять заново — уведомление
                    # относилось к прошлому растению.
                    logger.info(
                        "MARK_PLOT_NOTIFIED_STALE user=%s plot=%s flag=%s",
                        telegram_id, plot_id, flag,
                    )
                    return False
                if p.get(flag) is True:
                    return False
                plots[i] = {**p, flag: True}
                await conn.execute(
                    "UPDATE users SET farm_plots = $1::jsonb WHERE telegram_id = $2",
                    json.dumps(plots), telegram_id,
                )
                return True
            return False


async def execute_storm_for_user(
    telegram_id: int,
    farm_plots: List[Dict[str, Any]],
    last_seen_at: Optional[datetime],
    announced_at: datetime,
    plant_rewards: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Apply storm effects to one user's plots.

    Для каждой растущей грядки:
        - со щитом → продолжает расти, щит сгорает (одноразовый)
        - без щита → status=dead, независимо от присутствия игрока

    Раньше офлайновый игрок получал автосбор 50%, а онлайновый терял растение
    целиком: присутствие в боте наказывалось, отсутствие вознаграждалось.
    Теперь шторм действует одинаково на всех, а спастись можно только
    действием — щитом или ранним сбором.

    plant_rewards больше не используется и принимается лишь ради совместимости
    вызовов; поля autoharv в ответе всегда нулевые и сохранены, чтобы не ломать
    существующего потребителя.

    Returns a dict:
        {
            killed: int,
            shielded: int,
            autoharv: int,              # всегда 0
            autoharv_kopecks: int,      # всегда 0
            killed_plants: list[(plot_id, plant_type)],   # for itemized push
            autoharv_plants: list,      # всегда пустой
        }
    """
    empty_result = {
        "killed": 0, "shielded": 0, "autoharv": 0, "autoharv_kopecks": 0,
        "killed_plants": [], "autoharv_plants": [],
    }
    if not _core.DB_READY:
        return empty_result
    pool = await get_pool()
    if pool is None:
        return empty_result

    # Normalize both sides to aware UTC so naive-from-DB and aware-from-caller
    # can compare cleanly.
    last_seen_aware = _from_db_utc(last_seen_at) if last_seen_at is not None else None
    announced_aware = announced_at if announced_at.tzinfo is not None else announced_at.replace(tzinfo=timezone.utc)
    is_online = last_seen_aware is not None and last_seen_aware >= announced_aware

    killed = 0
    shielded = 0
    autoharv = 0
    autoharv_kopecks = 0
    killed_plants: List[Tuple[int, str]] = []
    autoharv_plants: List[Tuple[int, str, int]] = []

    new_plots = []
    for p in farm_plots:
        if p.get("status") != "growing":
            new_plots.append(p)
            continue

        if p.get("storm_shielded") is True:
            shielded += 1
            new_plots.append({**p, "storm_shielded": False})  # one-shot reset
            continue

        plant_type = p.get("plant_type") or ""
        plot_id = int(p.get("plot_id", -1))

        # Шторм действует одинаково независимо от того, был ли игрок онлайн.
        #
        # Раньше офлайновый игрок получал автосбор 50%, а онлайновый терял
        # растение целиком. Стимул был вывернут наизнанку: заходить в бота во
        # время шторма было невыгодно, оптимальной стратегией было не заходить.
        # Спастись по-прежнему можно — щитом или ранним сбором, но и то и
        # другое требует присутствия.
        killed += 1
        killed_plants.append((plot_id, plant_type))
        new_plots.append({
            **p,
            "status": "dead",
            "dead_at": datetime.now(timezone.utc).isoformat(),
            "storm_shielded": False,
        })

    if killed == 0 and shielded == 0 and autoharv == 0:
        return empty_result

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
    return {
        "killed": killed,
        "shielded": shielded,
        "autoharv": autoharv,
        "autoharv_kopecks": autoharv_kopecks,
        "killed_plants": killed_plants,
        "autoharv_plants": autoharv_plants,
    }


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
