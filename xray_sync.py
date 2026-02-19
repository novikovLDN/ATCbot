"""
Xray state auto-recovery & full user sync.

DB = source of truth. Xray = stateless executor.
After Xray restart/crash: all active users are auto-synced to Xray.
"""
import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

import config
import database
import vpn_utils
from app.utils.logging_helpers import log_worker_iteration_start, log_worker_iteration_end

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

FULL_SYNC_COOLDOWN_SECONDS = 300  # 5 minutes
XRAY_SYNC_INTERVAL_SECONDS = 300  # 5 minutes
_last_full_sync_at: float = 0.0


async def _get_active_subscriptions() -> list[dict]:
    """Get active subscriptions from DB (source of truth)."""
    if not database.DB_READY:
        return []
    pool = await database.get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.telegram_id, s.uuid, s.expires_at
               FROM subscriptions s
               WHERE s.status = 'active'
                 AND s.uuid IS NOT NULL
                 AND s.expires_at > NOW()"""
        )
    return [dict(r) for r in rows]


def _can_run_full_sync() -> bool:
    """Cooldown guard: full sync no more than once per 5 minutes."""
    global _last_full_sync_at
    now = time.monotonic()
    if now - _last_full_sync_at < FULL_SYNC_COOLDOWN_SECONDS:
        return False
    return True


def _mark_full_sync_done() -> None:
    global _last_full_sync_at
    _last_full_sync_at = time.monotonic()


async def full_sync(*, force: bool = False) -> int:
    """
    Sync all active subscriptions from DB to Xray.
    Uses add_user (idempotent: create or update expiry).
    Returns count of successfully synced users.

    Args:
        force: If True, bypass cooldown (e.g. for admin /xray_sync command).
    """
    if not force and not _can_run_full_sync():
        logger.info("XRAY_FULL_SYNC_SKIPPED reason=cooldown")
        return 0

    users = await _get_active_subscriptions()
    logger.info("XRAY_FULL_SYNC_START total_users=%s", len(users))
    _mark_full_sync_done()

    if not users:
        logger.info("XRAY_FULL_SYNC_COMPLETE synced=0")
        return 0

    ok_count = 0
    err_count = 0
    for user in users:
        telegram_id = user["telegram_id"]
        uuid_val = user["uuid"]
        expires_at_raw = user["expires_at"]
        expires_at = database._from_db_utc(expires_at_raw) if expires_at_raw else None
        if not expires_at:
            logger.warning("XRAY_SYNC_USER_SKIP user=%s reason=no_expires_at", telegram_id)
            continue
        try:
            await asyncio.wait_for(
                vpn_utils.add_vless_user(
                    telegram_id=telegram_id,
                    subscription_end=expires_at,
                    uuid=uuid_val
                ),
                timeout=XRAY_SYNC_API_TIMEOUT,
            )
            ok_count += 1
            logger.debug("XRAY_SYNC_USER_OK user=%s uuid=%s...", telegram_id, uuid_val[:8] if uuid_val else "N/A")
        except Exception as e:
            err_count += 1
            logger.error(
                "XRAY_SYNC_USER_ERROR user=%s uuid=%s... error=%s",
                telegram_id,
                uuid_val[:8] if uuid_val else "N/A",
                str(e),
            )
            # Continue with other users, don't fail fast

    logger.info("XRAY_FULL_SYNC_COMPLETE synced=%s errors=%s", ok_count, err_count)
    return ok_count


XRAY_SYNC_API_TIMEOUT = 10  # seconds


async def start(bot: "Bot") -> None:
    """
    Safe entry point: startup sync then worker loop.
    Never crashes - all errors are caught and logged.
    """
    try:
        await trigger_startup_sync()
    except Exception as e:
        logger.error("[XRAY_SYNC] startup sync failed: %s", e)
    try:
        await xray_sync_worker_task(bot)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[XRAY_SYNC] worker failed: %s", e)


async def trigger_startup_sync() -> None:
    """Run full sync on startup. Fire-and-forget."""
    logger.info("XRAY_STARTUP_SYNC_TRIGGERED")
    try:
        if not database.DB_READY:
            logger.warning("XRAY_STARTUP_SYNC_SKIPPED reason=db_not_ready")
            return
        if not config.VPN_ENABLED:
            logger.warning("XRAY_STARTUP_SYNC_SKIPPED reason=vpn_disabled")
            return
        await full_sync()
    except Exception as e:
        logger.error("XRAY_STARTUP_SYNC_FAILED error=%s", str(e), exc_info=True)


async def xray_sync_worker_task(bot: "Bot") -> None:
    """
    Periodic worker: health check Xray, trigger full sync on failure.
    Interval: 5 minutes.
    """
    # Prevent worker burst at startup
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("xray_sync: startup jitter done (%.1fs)", jitter_s)
    
    iteration = 0
    while True:
        iteration += 1
        correlation_id = log_worker_iteration_start(
            worker_name="xray_sync",
            iteration_number=iteration,
        )
        try:
            if not database.DB_READY:
                log_worker_iteration_end(worker_name="xray_sync", outcome="skipped", **{"reason": "db_not_ready"})
                await asyncio.sleep(XRAY_SYNC_INTERVAL_SECONDS)
                continue

            if not config.VPN_ENABLED:
                log_worker_iteration_end(worker_name="xray_sync", outcome="skipped", **{"reason": "vpn_disabled"})
                await asyncio.sleep(XRAY_SYNC_INTERVAL_SECONDS)
                continue

            healthy = await asyncio.wait_for(
                vpn_utils.check_xray_health(),
                timeout=XRAY_SYNC_API_TIMEOUT,
            )
            if not healthy:
                logger.warning("XRAY_HEALTH_CHECK_FAILED triggering_full_sync")
                await full_sync()
            else:
                logger.debug("XRAY_HEALTH_CHECK_OK")

            log_worker_iteration_end(
                worker_name="xray_sync",
                outcome="success",
            )
        except asyncio.CancelledError:
            logger.info("xray_sync_worker cancelled")
            break
        except Exception as e:
            logger.error("xray_sync_worker error=%s", str(e), exc_info=True)
            log_worker_iteration_end(
                worker_name="xray_sync",
                outcome="failed",
                error_type=type(e).__name__,
            )

        await asyncio.sleep(XRAY_SYNC_INTERVAL_SECONDS)
