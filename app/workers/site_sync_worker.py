"""
Site sync worker — periodic balance & referral sync with Atlas Secure website.

Runs every 5 minutes. For each user with active subscription:
1. POST /api/bot/sync-balance → apply pending cashback from site
2. POST /api/bot/sync-referrals → merge referral data

Only syncs users who have active subscriptions (not all users).
Skips if site sync is not configured.
"""
import asyncio
import logging
import time

import database
from app.services.site_sync import sync_balance, sync_referrals, is_enabled

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 5 * 60  # 5 minutes
SYNC_CONCURRENCY = 5  # max concurrent API calls
SYNC_USER_DELAY = 0.5  # delay between users to avoid rate limiting


async def site_sync_worker_task(bot=None):
    """Background worker: periodic site sync every 5 minutes."""
    logger.info("site_sync_worker started (interval=%ds)", SYNC_INTERVAL)

    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL)

            if not is_enabled():
                continue

            if not database.DB_READY:
                continue

            start_time = time.monotonic()
            logger.info("SITE_SYNC_ITERATION_START")

            # Get only site-linked users with active subscriptions
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT DISTINCT s.telegram_id FROM subscriptions s
                       JOIN users u ON u.telegram_id = s.telegram_id
                       WHERE s.expires_at > NOW() AND s.telegram_id IS NOT NULL
                       AND u.site_linked = TRUE
                       LIMIT 500"""
                )

            synced = 0
            errors = 0
            for row in rows:
                telegram_id = row["telegram_id"]
                try:
                    await sync_balance(telegram_id)
                    await sync_referrals(telegram_id)
                    synced += 1
                except Exception as e:
                    errors += 1
                    logger.debug("site_sync_worker: user=%s error=%s", telegram_id, e)

                await asyncio.sleep(SYNC_USER_DELAY)

            duration_ms = (time.monotonic() - start_time) * 1000
            logger.info("SITE_SYNC_ITERATION_END: synced=%d errors=%d duration=%.0fms", synced, errors, duration_ms)

        except asyncio.CancelledError:
            logger.info("site_sync_worker cancelled (shutdown)")
            break
        except Exception as e:
            logger.exception("site_sync_worker unexpected error: %s", e)
            await asyncio.sleep(60)  # wait before retry on unexpected error
