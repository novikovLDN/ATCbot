"""
Periodic site sync worker.

Runs every hour, checks site API for subscription status updates
for active users, and syncs any changes to local DB.

Also handles: detecting site-side unlink (404 → remove site_user_id).
"""
import asyncio
import logging
import time

import config
import database
from app.services import site_api

logger = logging.getLogger(__name__)

# Sync interval: 1 hour
SYNC_INTERVAL_SECONDS = 3600

# Max users to sync per iteration (avoid API overload)
MAX_USERS_PER_ITERATION = 100


async def site_sync_iteration():
    """Single sync iteration: check site status for active users."""
    if not config.SITE_SYNC_ENABLED:
        return

    if not database.DB_READY:
        return

    pool = await database.get_pool()
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            # Get users who have site_user_id (linked to site)
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE site_user_id IS NOT NULL
                   ORDER BY telegram_id
                   LIMIT $1""",
                MAX_USERS_PER_ITERATION,
            )

        synced = 0
        unlinked = 0

        for row in rows:
            telegram_id = row["telegram_id"]
            try:
                status = await site_api.get_status(telegram_id, force=True)
                if status is None:
                    # 404 = user unlinked on site
                    await database.clear_site_user_id(telegram_id)
                    unlinked += 1
                    logger.info("SITE_SYNC: user %s unlinked (404)", telegram_id)
                else:
                    # Update local vpn_key if changed
                    site_vpn_key = status.get("vpnKey")
                    if site_vpn_key:
                        async with pool.acquire() as conn:
                            current = await conn.fetchval(
                                "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                                telegram_id,
                            )
                            if current and current != site_vpn_key:
                                await conn.execute(
                                    "UPDATE subscriptions SET vpn_key = $1 WHERE telegram_id = $2",
                                    site_vpn_key, telegram_id,
                                )
                                logger.info("SITE_SYNC: vpn_key updated for user %s", telegram_id)
                    synced += 1

                # Small delay to avoid hammering the API
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.warning("SITE_SYNC: error for user %s: %s", telegram_id, e)

        if synced > 0 or unlinked > 0:
            logger.info(
                "SITE_SYNC_ITERATION: synced=%d, unlinked=%d, total=%d",
                synced, unlinked, len(rows),
            )

    except Exception as e:
        logger.exception("SITE_SYNC_ITERATION_ERROR: %s", e)


async def start_site_sync_worker():
    """Background worker: runs site sync every hour."""
    if not config.SITE_SYNC_ENABLED:
        logger.info("Site sync worker disabled (SITE_API_URL or BOT_API_KEY not set)")
        return

    logger.info("Site sync worker started (interval=%ds)", SYNC_INTERVAL_SECONDS)

    # Initial delay to let bot fully start
    await asyncio.sleep(60)

    while True:
        try:
            start = time.time()
            await site_sync_iteration()
            duration_ms = (time.time() - start) * 1000
            logger.debug("SITE_SYNC_WORKER: iteration completed in %.0fms", duration_ms)
        except Exception as e:
            logger.exception("SITE_SYNC_WORKER_ERROR: %s", e)

        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
