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
            try:
                farm_plots = json.loads(farm_plots)
            except (json.JSONDecodeError, ValueError):
                logger.warning("FARM_INVALID_JSON user=%s", telegram_id)
                continue
        elif farm_plots is None:
            continue
        if not isinstance(farm_plots, list):
            logger.warning("FARM_INVALID_FORMAT user=%s type=%s", telegram_id, type(farm_plots).__name__)
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
