"""Lightweight health check: DB connectivity only. Crash-proof with timeouts."""
import asyncio
import logging
import random
import time
from aiogram import Bot
import database
import config

logger = logging.getLogger(__name__)

# Spam protection: only alert once per hour
_last_alert_at: float = 0.0
_ALERT_COOLDOWN = 3600.0


async def _send_admin_alert(bot: Bot, message: str) -> None:
    """Send alert to admin with timeout. Never raises."""
    global _last_alert_at
    now = time.monotonic()
    if now - _last_alert_at < _ALERT_COOLDOWN:
        return
    try:
        await asyncio.wait_for(
            bot.send_message(config.ADMIN_TELEGRAM_ID, message),
            timeout=10.0
        )
        _last_alert_at = now
    except Exception as e:
        logger.warning("health_alert_failed error=%s", e)


async def health_check_task(bot: Bot) -> None:
    """
    Lightweight health check: DB connectivity only.
    Runs every 10 minutes. Never hangs — all ops have timeouts.
    """
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("health_check_task: startup jitter done (%.1fs)", jitter_s)

    while True:
        try:
            await asyncio.wait_for(_run_health_check(bot), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error("HEALTH_CHECK_TIMEOUT exceeded 30s")
        except asyncio.CancelledError:
            logger.info("health_check_task cancelled")
            break
        except Exception as e:
            logger.exception("HEALTH_CHECK_ERROR error=%s", e)
        
        await asyncio.sleep(10 * 60)  # 10 minutes


async def _run_health_check(bot: Bot) -> None:
    """Check DB connectivity. Alert admin if down."""
    if not database.DB_READY:
        logger.warning("HEALTH_CHECK db_ready=False")
        await _send_admin_alert(bot, "⚠️ Bot running in degraded mode (DB unavailable)")
        return

    pool = await database.get_pool()
    if not pool:
        logger.error("HEALTH_CHECK pool=None")
        await _send_admin_alert(bot, "🚨 DB pool is None")
        return

    try:
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
            if result == 1:
                logger.info("HEALTH_CHECK status=ok")
            else:
                logger.error("HEALTH_CHECK unexpected_result=%s", result)
    except Exception as e:
        logger.error("HEALTH_CHECK db_error=%s", e)
        await _send_admin_alert(bot, f"🚨 DB health check failed: {e}")
