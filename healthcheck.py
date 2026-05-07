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


# Hard-cap admin alert messages to avoid leaking stack traces, DSN strings,
# or other sensitive material that may have been concatenated into the message.
_ADMIN_ALERT_MAX_LEN = 200


async def _send_admin_alert(bot: Bot, message: str) -> None:
    """Send alert to admin with timeout. Never raises.

    The message is truncated to ``_ADMIN_ALERT_MAX_LEN`` characters as a
    last-line-of-defence against accidental PII / secret leakage. Callers
    SHOULD only pass error type names and short reasons.
    """
    global _last_alert_at
    now = time.monotonic()
    if now - _last_alert_at < _ALERT_COOLDOWN:
        return
    safe_message = (message or "").strip()
    if len(safe_message) > _ADMIN_ALERT_MAX_LEN:
        safe_message = safe_message[:_ADMIN_ALERT_MAX_LEN] + "…"
    try:
        await asyncio.wait_for(
            bot.send_message(config.ADMIN_TELEGRAM_ID, safe_message),
            timeout=10.0,
        )
        _last_alert_at = now
    except Exception as e:
        logger.warning("health_alert_failed error_type=%s", type(e).__name__)


async def health_check_task(bot: Bot) -> None:
    """
    Lightweight health check: DB connectivity only.
    Runs every 10 minutes. Never hangs — all ops have timeouts.
    """
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("health_check_task: startup jitter done (%.1fs)", jitter_s)

    while True:
        outcome = "success"
        try:
            from app.core import worker_registry
            worker_registry.mark_iteration_start("health_check")
            await asyncio.wait_for(_run_health_check(bot), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error("HEALTH_CHECK_TIMEOUT exceeded 30s")
            outcome = "failed"
        except asyncio.CancelledError:
            logger.info("health_check_task cancelled")
            try:
                from app.core import worker_registry
                worker_registry.mark_iteration_end("health_check", outcome="cancelled")
            except Exception:
                pass
            break
        except Exception as e:
            logger.exception("HEALTH_CHECK_ERROR error=%s", e)
            outcome = "failed"
        finally:
            try:
                from app.core import worker_registry
                worker_registry.mark_iteration_end("health_check", outcome=outcome)
            except Exception:
                pass

        await asyncio.sleep(10 * 60)  # 10 minutes


async def _run_health_check(bot: Bot) -> None:
    """Check DB and Redis connectivity. Alert admin if down."""
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
                logger.info("HEALTH_CHECK db=ok")
            else:
                logger.error("HEALTH_CHECK unexpected_result=%s", result)
        # Publish pool gauges for the admin observability dashboard.
        try:
            from app.core import metrics as _metrics
            size = getattr(pool, "_maxsize", None) or getattr(pool, "get_max_size", lambda: None)()
            free = getattr(pool, "_queue", None)
            free_count = free.qsize() if free is not None and hasattr(free, "qsize") else None
            if size is not None:
                _metrics.gauge(_metrics.M.DB_POOL_SIZE).set(float(size))
            if free_count is not None:
                _metrics.gauge(_metrics.M.DB_POOL_FREE).set(float(free_count))
        except Exception:
            pass
    except Exception as e:
        # SECURITY: Only log exception type, never connection strings or credentials
        logger.error("HEALTH_CHECK db_error=%s", type(e).__name__)
        await _send_admin_alert(bot, f"DB health check failed: {type(e).__name__}")

    # Redis health check (if configured)
    try:
        from app.utils.redis_client import ping as redis_ping, is_configured as redis_configured
        if redis_configured():
            redis_ok = await redis_ping()
            if redis_ok:
                logger.info("HEALTH_CHECK redis=ok")
            else:
                logger.warning("HEALTH_CHECK redis=unavailable")
                await _send_admin_alert(bot, "⚠️ Redis health check failed — FSM states may be lost")
    except Exception as e:
        logger.warning("HEALTH_CHECK redis_check_error=%s", e)
