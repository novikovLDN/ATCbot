"""
Periodic site sync worker.

Runs every hour, checks site API for subscription status updates
for linked users, and syncs any changes to local DB.

Syncs: vpnKey, subscriptionEnd, subscriptionPlan, isExpired.
Also handles: detecting site-side unlink (404 → remove site_user_id).
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import config
import database
from app.services import site_api

logger = logging.getLogger(__name__)

# Sync interval: 5 minutes
SYNC_INTERVAL_SECONDS = 300

# Max users to sync per iteration (avoid API overload)
MAX_USERS_PER_ITERATION = 100


async def site_sync_iteration():
    """Single sync iteration: check site status for all linked users."""
    if not config.SITE_SYNC_ENABLED:
        return

    if not database.DB_READY:
        return

    pool = await database.get_pool()
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT telegram_id FROM users
                   WHERE site_user_id IS NOT NULL
                   ORDER BY telegram_id
                   LIMIT $1""",
                MAX_USERS_PER_ITERATION,
            )

        synced = 0
        unlinked = 0
        deactivated = 0

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
                    await _sync_user_from_status(pool, telegram_id, status)
                    synced += 1

                await asyncio.sleep(0.1)

            except Exception as e:
                logger.warning("SITE_SYNC: error for user %s: %s", telegram_id, e)

        if synced > 0 or unlinked > 0 or deactivated > 0:
            logger.info(
                "SITE_SYNC_ITERATION: synced=%d, unlinked=%d, total=%d",
                synced, unlinked, len(rows),
            )

    except Exception as e:
        logger.exception("SITE_SYNC_ITERATION_ERROR: %s", e)


async def _sync_user_from_status(pool, telegram_id: int, status: dict):
    """Sync a single user's subscription from site status response."""
    is_expired = status.get("isExpired", False)
    site_vpn_key = status.get("vpnKey")
    site_sub_end_raw = status.get("subscriptionEnd")
    site_plan = (status.get("subscriptionPlan") or "basic").lower()

    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            """SELECT vpn_key, expires_at, subscription_type, status
               FROM subscriptions WHERE telegram_id = $1""",
            telegram_id,
        )

        if is_expired:
            # Site says expired → deactivate bot subscription if active
            if current and current["status"] == "active":
                await conn.execute(
                    """UPDATE subscriptions SET status = 'expired'
                       WHERE telegram_id = $1 AND status = 'active'""",
                    telegram_id,
                )
                logger.info("SITE_SYNC: deactivated subscription for user %s (site expired)", telegram_id)
            return

        if not site_vpn_key:
            return

        # Parse site subscriptionEnd
        site_sub_end = None
        if site_sub_end_raw:
            if isinstance(site_sub_end_raw, str):
                try:
                    site_sub_end = datetime.fromisoformat(
                        site_sub_end_raw.replace("Z", "+00:00")
                    )
                    if site_sub_end.tzinfo is not None:
                        site_sub_end = site_sub_end.replace(tzinfo=None)
                except ValueError:
                    pass

        if not current:
            # No subscription row in bot — create from site data
            xray_uuid = status.get("xrayUuid") or ""
            if site_sub_end:
                await conn.execute(
                    """INSERT INTO subscriptions (
                           telegram_id, uuid, vpn_key, expires_at, status, source,
                           subscription_type, activated_at, activation_status
                       ) VALUES ($1, $2, $3, $4, 'active', 'site', $5, NOW(), 'active')""",
                    telegram_id, xray_uuid, site_vpn_key, site_sub_end, site_plan,
                )
                logger.info("SITE_SYNC: created subscription from site for user %s", telegram_id)
            return

        # Update existing subscription if any field changed
        updates = []
        params = []
        param_idx = 1

        if current["vpn_key"] != site_vpn_key:
            updates.append(f"vpn_key = ${param_idx}")
            params.append(site_vpn_key)
            param_idx += 1

        if site_sub_end and current["expires_at"] != site_sub_end:
            updates.append(f"expires_at = ${param_idx}")
            params.append(site_sub_end)
            param_idx += 1

        if current["subscription_type"] != site_plan:
            updates.append(f"subscription_type = ${param_idx}")
            params.append(site_plan)
            param_idx += 1

        # Re-activate if site has active sub but bot shows expired
        if current["status"] != "active":
            updates.append(f"status = ${param_idx}")
            params.append("active")
            param_idx += 1

        if updates:
            params.append(telegram_id)
            query = f"UPDATE subscriptions SET {', '.join(updates)} WHERE telegram_id = ${param_idx}"
            await conn.execute(query, *params)
            logger.info("SITE_SYNC: updated subscription for user %s (fields: %s)", telegram_id, ", ".join(updates))


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
