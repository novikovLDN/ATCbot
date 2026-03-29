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
    """
    Bidirectional sync for a single user.

    Compares bot and site expires_at:
    - Bot is newer → push bot data to site
    - Site is newer → pull site data into bot
    - Same → no-op (or sync vpnKey if different)
    """
    is_expired = status.get("isExpired", False)
    has_active_sub = status.get("hasActiveSubscription", False)
    site_vpn_key = status.get("vpnKey")
    site_sub_end_raw = status.get("subscriptionEnd")
    site_plan = (status.get("subscriptionPlan") or "basic").lower()

    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            """SELECT vpn_key, uuid, expires_at, subscription_type, status
               FROM subscriptions WHERE telegram_id = $1""",
            telegram_id,
        )

        bot_is_active = current and current["status"] == "active"
        bot_expires = current["expires_at"] if current else None

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

        # === Case 1: Bot has subscription, site doesn't (or expired) → push to site ===
        if bot_is_active and bot_expires and (not has_active_sub or is_expired):
            if isinstance(bot_expires, datetime):
                sub_end_iso = bot_expires.isoformat()
            else:
                sub_end_iso = str(bot_expires)
            plan = (current["subscription_type"] or "basic").lower()
            vpn_key = current["vpn_key"] or ""
            xray_uuid = current["uuid"] or ""
            logger.info(
                "SITE_SYNC: bot has active sub, site doesn't → pushing bot→site for user %s",
                telegram_id,
            )
            await site_api.sync_overwrite_site(
                telegram_id=telegram_id,
                subscription_end=sub_end_iso,
                plan=plan,
                vpn_key=vpn_key,
                xray_uuid=xray_uuid,
            )
            return

        # === Case 2: Site has subscription, bot doesn't → pull from site ===
        if has_active_sub and not is_expired and site_vpn_key and not bot_is_active:
            xray_uuid = status.get("xrayUuid") or ""
            if site_sub_end:
                if current:
                    # Update existing inactive row
                    await conn.execute(
                        """UPDATE subscriptions
                           SET vpn_key = $1, expires_at = $2, subscription_type = $3,
                               status = 'active', uuid = $4
                           WHERE telegram_id = $5""",
                        site_vpn_key, site_sub_end, site_plan, xray_uuid, telegram_id,
                    )
                else:
                    await conn.execute(
                        """INSERT INTO subscriptions (
                               telegram_id, uuid, vpn_key, expires_at, status, source,
                               subscription_type, activated_at, activation_status
                           ) VALUES ($1, $2, $3, $4, 'active', 'site', $5, NOW(), 'active')""",
                        telegram_id, xray_uuid, site_vpn_key, site_sub_end, site_plan,
                    )
                logger.info("SITE_SYNC: site has active sub, bot doesn't → pulled site→bot for user %s", telegram_id)
            return

        # === Case 3: Both have active subscriptions → compare expires_at ===
        if bot_is_active and has_active_sub and not is_expired and bot_expires and site_sub_end:
            # Normalize both to naive UTC for comparison
            bot_exp = bot_expires.replace(tzinfo=None) if bot_expires.tzinfo else bot_expires
            site_exp = site_sub_end.replace(tzinfo=None) if site_sub_end.tzinfo else site_sub_end

            if bot_exp > site_exp:
                # Bot is newer → push to site
                if isinstance(bot_expires, datetime):
                    sub_end_iso = bot_expires.isoformat()
                else:
                    sub_end_iso = str(bot_expires)
                plan = (current["subscription_type"] or "basic").lower()
                vpn_key = current["vpn_key"] or ""
                xray_uuid = current["uuid"] or ""
                logger.info(
                    "SITE_SYNC: bot expires %s > site expires %s → pushing bot→site for user %s",
                    bot_exp, site_exp, telegram_id,
                )
                await site_api.sync_overwrite_site(
                    telegram_id=telegram_id,
                    subscription_end=sub_end_iso,
                    plan=plan,
                    vpn_key=vpn_key,
                    xray_uuid=xray_uuid,
                )
            elif site_exp > bot_exp:
                # Site is newer → pull into bot
                updates = []
                params = []
                param_idx = 1

                if site_vpn_key and current["vpn_key"] != site_vpn_key:
                    updates.append(f"vpn_key = ${param_idx}")
                    params.append(site_vpn_key)
                    param_idx += 1

                if current["expires_at"] != site_sub_end:
                    updates.append(f"expires_at = ${param_idx}")
                    params.append(site_sub_end)
                    param_idx += 1

                if current["subscription_type"] != site_plan:
                    updates.append(f"subscription_type = ${param_idx}")
                    params.append(site_plan)
                    param_idx += 1

                if updates:
                    params.append(telegram_id)
                    query = f"UPDATE subscriptions SET {', '.join(updates)} WHERE telegram_id = ${param_idx}"
                    await conn.execute(query, *params)
                    logger.info(
                        "SITE_SYNC: site expires %s > bot expires %s → pulled site→bot for user %s (fields: %s)",
                        site_exp, bot_exp, telegram_id, ", ".join(updates),
                    )
            else:
                # Same expires_at → sync vpnKey if different
                if site_vpn_key and current["vpn_key"] != site_vpn_key:
                    await conn.execute(
                        "UPDATE subscriptions SET vpn_key = $1 WHERE telegram_id = $2",
                        site_vpn_key, telegram_id,
                    )
                    logger.info("SITE_SYNC: same expires, synced vpnKey for user %s", telegram_id)

        # === Case 4: Site expired, bot active → site should get bot data ===
        # (already handled in Case 1 above)

        # === Case 5: Both expired → no-op ===


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
