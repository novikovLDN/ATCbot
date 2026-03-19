"""
Comprehensive health check: DB, Redis, pool stats, memory, workers.
Crash-proof with timeouts. Alerts admin on degradation.
"""
import asyncio
import logging
import os
import random
import resource
import time
from aiogram import Bot
import database
import config

logger = logging.getLogger(__name__)

# Spam protection: per-category cooldowns
_last_alert_at: dict = {}
_ALERT_COOLDOWNS = {
    "db_degraded": 3600.0,    # 1 hour
    "db_pool": 1800.0,        # 30 min
    "db_query": 1800.0,       # 30 min
    "redis": 3600.0,          # 1 hour
    "memory": 1800.0,         # 30 min
    "pool_exhaustion": 600.0, # 10 min (critical)
    "worker_stale": 1800.0,   # 30 min
}

# Memory threshold (MB) — alert if RSS exceeds this
MEMORY_ALERT_THRESHOLD_MB = int(os.getenv("MEMORY_ALERT_THRESHOLD_MB", "512"))

# Pool utilization threshold — alert if > 80% used
POOL_UTILIZATION_ALERT_THRESHOLD = float(os.getenv("POOL_UTILIZATION_ALERT_THRESHOLD", "0.8"))


async def _send_admin_alert(bot: Bot, category: str, message: str) -> None:
    """Send alert to admin with timeout and per-category cooldown. Never raises."""
    now = time.monotonic()
    cooldown = _ALERT_COOLDOWNS.get(category, 3600.0)
    last = _last_alert_at.get(category, 0.0)

    if now - last < cooldown:
        return

    try:
        await asyncio.wait_for(
            bot.send_message(config.ADMIN_TELEGRAM_ID, message),
            timeout=10.0,
        )
        _last_alert_at[category] = now
    except Exception as e:
        logger.warning("health_alert_failed category=%s error=%s", category, e)


def _get_memory_rss_mb() -> float:
    """Get current process RSS in MB."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB -> MB
    except Exception:
        pass
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_maxrss / 1024  # Linux: KB -> MB
    except Exception:
        return 0.0


def _get_pool_stats(pool) -> dict:
    """Extract pool statistics from asyncpg pool."""
    try:
        return {
            "size": pool.get_size(),
            "free": pool.get_idle_size(),
            "used": pool.get_size() - pool.get_idle_size(),
            "min": pool.get_min_size(),
            "max": pool.get_max_size(),
        }
    except Exception:
        return {"size": 0, "free": 0, "used": 0, "min": 0, "max": 0}


async def health_check_task(bot: Bot) -> None:
    """
    Comprehensive health check: DB, Redis, memory, pool, workers.
    Runs every 5 minutes. Never hangs — all ops have timeouts.
    """
    from app.core.worker_monitor import worker_heartbeat

    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("health_check_task: startup jitter done (%.1fs)", jitter_s)

    while True:
        try:
            await asyncio.wait_for(_run_health_check(bot), timeout=45.0)
            worker_heartbeat("healthcheck")
        except asyncio.TimeoutError:
            logger.error("HEALTH_CHECK_TIMEOUT exceeded 45s")
        except asyncio.CancelledError:
            logger.info("health_check_task cancelled")
            break
        except Exception as e:
            logger.exception("HEALTH_CHECK_ERROR error=%s", e)

        await asyncio.sleep(5 * 60)  # 5 minutes (reduced from 10)


async def _run_health_check(bot: Bot) -> None:
    """Comprehensive health check with admin alerts."""
    issues = []

    # ── 1. Database connectivity ──────────────────────────────────
    if not database.DB_READY:
        logger.warning("HEALTH_CHECK db_ready=False")
        await _send_admin_alert(
            bot, "db_degraded",
            "⚠️ HEALTH CHECK\n\nBot running in DEGRADED mode\nDatabase: UNAVAILABLE\n\nUsers cannot make payments or manage subscriptions."
        )
        issues.append("db_not_ready")
    else:
        pool = await database.get_pool()
        if not pool:
            logger.error("HEALTH_CHECK pool=None")
            await _send_admin_alert(bot, "db_pool", "🚨 HEALTH CHECK\n\nDB pool is None — all DB operations will fail!")
            issues.append("pool_none")
        else:
            # DB query check
            try:
                start = time.monotonic()
                async with pool.acquire() as conn:
                    result = await conn.fetchval("SELECT 1")
                latency_ms = (time.monotonic() - start) * 1000

                if result == 1:
                    logger.info("HEALTH_CHECK db=ok latency=%.1fms", latency_ms)
                    if latency_ms > 5000:
                        await _send_admin_alert(
                            bot, "db_query",
                            f"⚠️ HEALTH CHECK\n\nDB query latency very high: {latency_ms:.0f}ms\nThis may indicate DB overload."
                        )
                else:
                    logger.error("HEALTH_CHECK unexpected_result=%s", result)
                    issues.append("db_unexpected_result")
            except Exception as e:
                logger.error("HEALTH_CHECK db_error=%s", type(e).__name__)
                await _send_admin_alert(
                    bot, "db_query",
                    f"🚨 HEALTH CHECK\n\nDB query failed: {type(e).__name__}\nDatabase may be down or overloaded."
                )
                issues.append("db_query_failed")

            # Pool utilization check
            stats = _get_pool_stats(pool)
            max_size = stats["max"]
            used = stats["used"]
            if max_size > 0:
                utilization = used / max_size
                logger.info(
                    "HEALTH_CHECK pool size=%d used=%d free=%d utilization=%.0f%%",
                    stats["size"], used, stats["free"], utilization * 100,
                )
                if utilization >= POOL_UTILIZATION_ALERT_THRESHOLD:
                    await _send_admin_alert(
                        bot, "pool_exhaustion",
                        f"🚨 HEALTH CHECK — DB POOL EXHAUSTION\n\n"
                        f"Pool: {used}/{max_size} connections used ({utilization:.0%})\n"
                        f"Free: {stats['free']}\n\n"
                        f"Bot may start rejecting requests if pool runs out!"
                    )
                    issues.append("pool_near_exhaustion")

    # ── 2. Redis health ────────────────────────────────────────────
    try:
        from app.utils.redis_client import ping as redis_ping, is_configured as redis_configured
        if redis_configured():
            redis_ok = await redis_ping()
            if redis_ok:
                logger.info("HEALTH_CHECK redis=ok")
            else:
                logger.warning("HEALTH_CHECK redis=unavailable")
                await _send_admin_alert(
                    bot, "redis",
                    "⚠️ HEALTH CHECK\n\nRedis: UNAVAILABLE\nFSM states may be lost. Rate limiting degraded to in-memory."
                )
                issues.append("redis_down")
    except Exception as e:
        logger.warning("HEALTH_CHECK redis_check_error=%s", e)

    # ── 3. Memory usage ───────────────────────────────────────────
    rss_mb = _get_memory_rss_mb()
    if rss_mb > 0:
        logger.info("HEALTH_CHECK memory_rss=%.1fMB threshold=%dMB", rss_mb, MEMORY_ALERT_THRESHOLD_MB)
        if rss_mb > MEMORY_ALERT_THRESHOLD_MB:
            await _send_admin_alert(
                bot, "memory",
                f"⚠️ HEALTH CHECK — HIGH MEMORY\n\n"
                f"RSS: {rss_mb:.0f} MB (threshold: {MEMORY_ALERT_THRESHOLD_MB} MB)\n\n"
                f"Possible memory leak. Bot may be OOM-killed."
            )
            issues.append("high_memory")

    # ── 4. Worker health (via metrics) ────────────────────────────
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        worker_statuses = m.get_worker_status()
        for wname, wstatus in worker_statuses.items():
            if wstatus["status"] == "failing":
                await _send_admin_alert(
                    bot, "worker_stale",
                    f"⚠️ HEALTH CHECK — WORKER ISSUE\n\n"
                    f"Worker: {wname}\n"
                    f"Status: {wstatus['status']}\n"
                    f"Errors: {wstatus['errors']}\n"
                    f"Last error: {wstatus.get('last_error', 'N/A')}"
                )
            elif wstatus["status"] == "stale":
                since = wstatus.get("since_last_ok_s")
                await _send_admin_alert(
                    bot, "worker_stale",
                    f"⚠️ HEALTH CHECK — WORKER STALE\n\n"
                    f"Worker: {wname}\n"
                    f"No heartbeat for: {since}s\n"
                    f"Worker may be stuck in an infinite operation."
                )
    except Exception as e:
        logger.warning("HEALTH_CHECK worker_status_error=%s", e)

    # ── 5. Metrics snapshot log ───────────────────────────────────
    try:
        from app.core.metrics import get_metrics
        m = get_metrics()
        snap = m.snapshot()
        logger.info(
            "HEALTH_CHECK_METRICS requests=%d errors=%d rate=%.1f/s "
            "concurrent=%d peak_concurrent=%d "
            "db_queries=%d db_errors=%d "
            "memory_mb=%.0f alerts_sent=%d",
            snap["requests"]["total"],
            snap["requests"]["errors"],
            snap["requests"]["rate_per_sec"],
            snap["concurrency"]["current"],
            snap["concurrency"]["peak"],
            snap["database"]["queries"],
            snap["database"]["errors"],
            rss_mb,
            snap["alerts"]["sent"],
        )
    except Exception:
        pass

    if not issues:
        logger.info("HEALTH_CHECK all_checks_passed")
