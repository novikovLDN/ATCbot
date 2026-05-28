"""Модуль для отправки уведомлений о ферме"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot

import database
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)

logger = logging.getLogger(__name__)

# Import PLANT_TYPES - try importing from app.handlers.game
# If circular import occurs, we'll use fallback
try:
    from app.handlers.game import PLANT_TYPES
except ImportError:
    # Fallback: define PLANT_TYPES here if import fails
    PLANT_TYPES = {
        "tomato":    {"emoji": "🍅", "name": "Томаты",   "days": 3,  "reward": 500},
        "potato":    {"emoji": "🥔", "name": "Картофель","days": 5,  "reward": 1000},
        "carrot":    {"emoji": "🥕", "name": "Морковь",  "days": 7,  "reward": 1000},
        "cactus":    {"emoji": "🌵", "name": "Кактус",   "days": 10, "reward": 1500},
        "apple":     {"emoji": "🍏", "name": "Яблоня",   "days": 8,  "reward": 1500},
        "lavender":  {"emoji": "💜", "name": "Лаванда",  "days": 6,  "reward": 2000},
    }


async def farm_notifications_iteration(bot: Bot):
    """Process one iteration of farm notifications"""
    users = await database.get_users_with_active_farm()
    now = datetime.now(timezone.utc)
    
    for user in users:
        telegram_id = user["telegram_id"]
        farm_plots = user["farm_plots"]
        
        # Parse JSONB if needed
        if isinstance(farm_plots, str):
            farm_plots = json.loads(farm_plots)
        elif farm_plots is None:
            continue
        
        changed = False
        
        for plot in farm_plots:
            if plot["status"] not in ("growing", "ready"):
                continue
            
            plant_type = plot.get("plant_type")
            if not plant_type or plant_type not in PLANT_TYPES:
                continue
            
            plant_name = PLANT_TYPES[plant_type]["name"]
            ready_at = datetime.fromisoformat(plot["ready_at"]) if plot.get("ready_at") else None
            dead_at = datetime.fromisoformat(plot["dead_at"]) if plot.get("dead_at") else None
            
            # A: Ready notification
            if ready_at and now >= ready_at and not plot.get("notified_ready"):
                plot["status"] = "ready"
                plot["notified_ready"] = True
                changed = True
                try:
                    await bot.send_message(
                        telegram_id,
                        f"🌾 Ваши <b>{plant_name}</b> созрели!\n"
                        f"Заходите скорее собирать урожай, пока он не испортился 🌻",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Failed to send farm ready notification to {telegram_id}: {e}")
            
            # B: 12h warning
            if dead_at and now >= (dead_at - timedelta(hours=12)) and not plot.get("notified_12h"):
                plot["notified_12h"] = True
                changed = True
                try:
                    await bot.send_message(
                        telegram_id,
                        f"⚠️ Не забудьте собрать <b>{plant_name}</b>!\n"
                        f"У вас осталось ~12 часов до того, как урожай сгниёт 🕐",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Failed to send farm 12h warning to {telegram_id}: {e}")
            
            # C: Dead notification
            if dead_at and now >= dead_at and not plot.get("notified_dead"):
                plot["status"] = "dead"
                plot["notified_dead"] = True
                changed = True
                try:
                    await bot.send_message(
                        telegram_id,
                        f"💀 Ваши <b>{plant_name}</b> сгнили — вы не успели собрать урожай 😢\n"
                        f"Зайдите на ферму, чтобы убрать погибшее растение.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Failed to send farm dead notification to {telegram_id}: {e}")
        
        if changed:
            await database.save_farm_plots(telegram_id, farm_plots)


def _format_eta(delta_seconds: int) -> str:
    """Human-friendly ETA: '6 ч', '1 д 4 ч', '20 мин' (for the last hour)."""
    if delta_seconds < 3600:
        m = max(1, delta_seconds // 60)
        return f"{m} мин"
    h = delta_seconds // 3600
    if h < 24:
        return f"{h} ч"
    d, rh = divmod(h, 24)
    return f"{d} д {rh} ч" if rh else f"{d} д"


async def broadcast_storm_announce(bot: Bot, users, scheduled_at: datetime):
    """Send the storm warning to every user with growing plots.

    Used by both the 24h worker pass and the admin "schedule in N hours"
    tool — keeping a single source of truth for the wording and ETA math.
    """
    now = datetime.now(timezone.utc)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    eta = _format_eta(max(0, int((scheduled_at - now).total_seconds())))
    text = (
        "⛈ <b>Надвигается шторм!</b>\n\n"
        f"Через ~{eta} твои растущие грядки могут погибнуть.\n"
        "🛡 Накрой их плёнкой (10/20/30 ₽ в зависимости от культуры), "
        "или 🚜 собери незрелым за 50 %.\n\n"
        "Зайди на ферму, чтобы выбрать."
    )
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["telegram_id"], text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            logger.warning("STORM_ANNOUNCE push failed user=%s err=%s", u["telegram_id"], type(e).__name__)
    logger.info("STORM_ANNOUNCE broadcast: sent=%s/%s", sent, len(users))
    return sent


async def farm_storm_iteration(bot: Bot):
    """One pass of the storm scheduler.

    Drives the storm lifecycle: pending → announced → executed → next-pending.
    Runs inside the same 30-minute loop as ripe/dead notifications.
    """
    storm = await database.get_pending_storm()
    if storm is None:
        return

    scheduled_at = storm["scheduled_at"]
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    announce_threshold = scheduled_at - timedelta(hours=database.STORM_ANNOUNCE_BEFORE_HOURS)

    # 1. ANNOUNCE — within 24h of impact, and not yet announced.
    if storm.get("announced_at") is None and now >= announce_threshold:
        ok = await database.mark_storm_announced(storm["id"])
        if ok:
            logger.info("STORM_ANNOUNCED storm_id=%s scheduled_at=%s", storm["id"], scheduled_at)
            users = await database.list_users_with_growing_plots()
            await broadcast_storm_announce(bot, users, scheduled_at)

    # Re-read after potential announce (so executed_at sees the latest)
    storm = await database.get_pending_storm()
    if storm is None:
        return

    scheduled_at = storm["scheduled_at"]
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    announced_at = storm.get("announced_at")
    if announced_at and announced_at.tzinfo is None:
        announced_at = announced_at.replace(tzinfo=timezone.utc)

    # 2. EXECUTE — past scheduled_at, not yet executed.
    if storm.get("executed_at") is None and now >= scheduled_at:
        if announced_at is None:
            # Defensive: if somehow announce was skipped, treat the moment of
            # execution as "no online window existed" — everyone is offline.
            announced_at = now

        plant_rewards = {k: v["reward"] for k, v in PLANT_TYPES.items()}
        users = await database.list_users_with_growing_plots()
        total_k, total_s, total_ah, total_ahk = 0, 0, 0, 0

        for u in users:
            result = await database.execute_storm_for_user(
                u["telegram_id"], u["farm_plots"], u["last_seen_at"],
                announced_at, plant_rewards,
            )
            killed = result["killed"]
            shielded = result["shielded"]
            autoharv = result["autoharv"]
            autoharv_kop = result["autoharv_kopecks"]
            total_k += killed
            total_s += shielded
            total_ah += autoharv
            total_ahk += autoharv_kop

            # Per-user wrap-up push (only if anything happened to that user)
            if killed + autoharv > 0:
                lines = ["🌪 <b>Шторм прошёл</b>\n"]
                if killed > 0:
                    lines.append(f"💀 <b>Погибли без плёнки ({killed}):</b>")
                    for plot_id, ptype in result["killed_plants"]:
                        plant = PLANT_TYPES.get(ptype, {})
                        emoji = plant.get("emoji", "🌿")
                        name = plant.get("name", ptype or "растение")
                        lines.append(f"  {emoji} {name} (грядка {plot_id + 1})")
                if autoharv > 0:
                    lines.append(
                        f"\n🚜 <b>Авто-сбор за 50% (+{autoharv_kop // 100} ₽):</b>"
                    )
                    for plot_id, ptype, half_kop in result["autoharv_plants"]:
                        plant = PLANT_TYPES.get(ptype, {})
                        emoji = plant.get("emoji", "🌿")
                        name = plant.get("name", ptype or "растение")
                        lines.append(f"  {emoji} {name} (грядка {plot_id + 1}) +{half_kop // 100} ₽")
                if shielded > 0:
                    lines.append(f"\n🛡 Спасено плёнкой: {shielded}")
                try:
                    await bot.send_message(u["telegram_id"], "\n".join(lines), parse_mode="HTML")
                except Exception as e:
                    logger.warning("STORM_WRAPUP push failed user=%s err=%s", u["telegram_id"], type(e).__name__)

        await database.mark_storm_executed(
            storm["id"],
            killed=total_k, shielded=total_s,
            auto_harvested=total_ah, auto_harvested_rub=total_ahk // 100,
        )
        next_id = await database.schedule_next_storm()
        logger.info(
            "STORM_EXECUTED storm_id=%s killed=%s shielded=%s auto=%s auto_rub=%s next_storm_id=%s",
            storm["id"], total_k, total_s, total_ah, total_ahk // 100, next_id,
        )


async def farm_notifications_task(bot: Bot):
    """Фоновая задача для отправки уведомлений о ферме (выполняется каждые 30 минут)"""
    # Небольшая задержка при старте, чтобы БД успела инициализироваться
    await asyncio.sleep(60)

    iteration_number = 0
    while True:
        iteration_number += 1
        iteration_start_time = time.time()
        
        correlation_id = log_worker_iteration_start(
            worker_name="farm_notifications",
            iteration_number=iteration_number
        )
        
        iteration_outcome = "success"
        iteration_error_type = None
        
        try:
            async def _run_iteration():
                await farm_notifications_iteration(bot)
                await farm_storm_iteration(bot)
            
            try:
                await asyncio.wait_for(_run_iteration(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=farm_notifications exceeded 120s — iteration cancelled"
                )
                iteration_outcome = "timeout"
                iteration_error_type = "timeout"
        except asyncio.CancelledError:
            logger.info("Farm notifications task cancelled")
            iteration_outcome = "cancelled"
            break
        except Exception as e:
            logger.error(f"farm_notifications: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("farm_notifications: Full traceback for task loop", exc_info=True)
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "farm_notifications", e, iteration=iteration_number)
            except Exception:
                pass
        finally:
            duration_ms = int((time.time() - iteration_start_time) * 1000)
            log_worker_iteration_end(
                worker_name="farm_notifications",
                outcome=iteration_outcome,
                items_processed=0,
                error_type=iteration_error_type,
                duration_ms=duration_ms,
                correlation_id=correlation_id
            )
        
        await asyncio.sleep(1800)  # 30 minutes
