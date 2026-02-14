"""
Xray Reconciliation Worker — Orphan UUID Cleanup

Compares UUIDs in DB (subscriptions) vs Xray API.
- ORPHANS (in Xray, not in DB): remove from Xray, log reconciliation_removed
- MISSING_IN_XRAY (in DB, not in Xray): log CRITICAL, require manual review (do NOT auto-recreate)

Safety:
- Never mass-delete blindly
- Batch size limit (default 100 per run)
- Feature flag: XRAY_RECONCILIATION_ENABLED
- Run every 10 minutes
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import database
import config
import vpn_utils
from app.core.metrics import get_metrics

logger = logging.getLogger(__name__)

RECONCILIATION_INTERVAL_SECONDS = int(os.getenv("XRAY_RECONCILIATION_INTERVAL_SECONDS", "600"))
BATCH_SIZE_LIMIT = int(os.getenv("XRAY_RECONCILIATION_BATCH_LIMIT", "100"))
BREAKER_OPEN_SECONDS = int(os.getenv("XRAY_RECONCILIATION_BREAKER_OPEN_SECONDS", "600"))  # 10 min
BREAKER_FAILURE_THRESHOLD = 3

# Circuit breaker state
_failure_count = 0
_last_failure_ts: float = 0.0
_breaker_open_until: float = 0.0


async def reconcile_xray_state() -> dict:
    """
    Compare DB vs Xray UUIDs; remove orphans.
    
    Returns:
        {
            "orphans_found": int,
            "orphans_removed": int,
            "missing_in_xray": int,
            "errors": list
        }
    """
    result = {"orphans_found": 0, "orphans_removed": 0, "missing_in_xray": 0, "errors": []}
    
    if not config.XRAY_RECONCILIATION_ENABLED:
        return result
    
    if not config.VPN_ENABLED:
        logger.debug("reconcile_xray_state: VPN disabled, skipping")
        return result
    
    try:
        # 1. Fetch UUID list from DB
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT uuid FROM subscriptions WHERE uuid IS NOT NULL"
            )
        db_uuids = {r["uuid"].strip() for r in rows if r.get("uuid")}
        
        # 2. Fetch UUID list from Xray API
        xray_uuids = set(await vpn_utils.list_vless_users())
        
        # 3. Compare
        orphans = xray_uuids - db_uuids
        missing_in_xray = db_uuids - xray_uuids
        
        result["orphans_found"] = len(orphans)
        result["missing_in_xray"] = len(missing_in_xray)
        
        if missing_in_xray:
            for uuid_val in list(missing_in_xray)[:10]:
                uuid_preview = f"{uuid_val[:8]}..." if len(uuid_val) > 8 else "***"
                logger.critical(
                    f"reconciliation_missing_in_xray uuid={uuid_preview} — "
                    "UUID in DB but not in Xray, manual review required"
                )
            if len(missing_in_xray) > 10:
                logger.critical(
                    f"reconciliation_missing_in_xray total={len(missing_in_xray)} — "
                    "additional UUIDs omitted from log"
                )
        
        # 4. Remove orphans (batch limited)
        orphans_list = list(orphans)[:BATCH_SIZE_LIMIT]
        for uuid_val in orphans_list:
            try:
                await vpn_utils.remove_vless_user(uuid_val)
                result["orphans_removed"] += 1
                uuid_preview = f"{uuid_val[:8]}..." if len(uuid_val) > 8 else "***"
                logger.info(f"reconciliation_removed uuid={uuid_preview}")
                get_metrics().increment_counter("reconciliation_orphans_removed", value=1)
            except Exception as e:
                result["errors"].append(str(e))
                uuid_preview = f"{uuid_val[:8]}..." if len(uuid_val) > 8 else "***"
                logger.warning(f"reconciliation_remove_failed uuid={uuid_preview} error={e}")
        
        get_metrics().increment_counter("reconciliation_orphans_found", value=result["orphans_found"])
        get_metrics().increment_counter("reconciliation_missing_in_xray", value=result["missing_in_xray"])
        
    except Exception as e:
        logger.error(f"reconcile_xray_state: {e}", exc_info=True)
        result["errors"].append(str(e))
    
    return result


async def reconcile_xray_state_task():
    """Background task: run reconciliation every 10 minutes. Circuit breaker on repeated failure."""
    global _failure_count, _last_failure_ts, _breaker_open_until

    logger.info(
        f"Xray reconciliation task started "
        f"(interval={RECONCILIATION_INTERVAL_SECONDS}s, batch_limit={BATCH_SIZE_LIMIT}, "
        f"breaker_threshold={BREAKER_FAILURE_THRESHOLD}, breaker_open_s={BREAKER_OPEN_SECONDS}, "
        f"enabled={config.XRAY_RECONCILIATION_ENABLED})"
    )

    while True:
        try:
            await asyncio.sleep(RECONCILIATION_INTERVAL_SECONDS)

            if not config.XRAY_RECONCILIATION_ENABLED:
                continue

            now = time.time()
            if now < _breaker_open_until:
                logger.warning(
                    "RECONCILIATION_BREAKER_OPEN",
                    extra={
                        "remaining_s": int(_breaker_open_until - now),
                        "failure_count": _failure_count
                    }
                )
                continue

            start = time.time()
            try:
                r = await reconcile_xray_state()
                _failure_count = 0
                if _breaker_open_until > 0:
                    logger.info(
                        "RECONCILIATION_BREAKER_RESET",
                        extra={"reason": "half_open_trial_success"}
                    )
                    _breaker_open_until = 0.0

                duration_ms = (time.time() - start) * 1000
                if r["orphans_found"] or r["orphans_removed"] or r["missing_in_xray"]:
                    logger.info(
                        f"reconciliation_complete orphans_found={r['orphans_found']} "
                        f"orphans_removed={r['orphans_removed']} missing_in_xray={r['missing_in_xray']} "
                        f"duration_ms={duration_ms:.0f}"
                    )
            except Exception as run_err:
                _failure_count += 1
                _last_failure_ts = time.time()
                logger.error(
                    "RECONCILIATION_FAILURE",
                    extra={
                        "failure_count": _failure_count,
                        "error": str(run_err)[:200]
                    },
                    exc_info=True
                )
                if _failure_count >= BREAKER_FAILURE_THRESHOLD:
                    _breaker_open_until = time.time() + BREAKER_OPEN_SECONDS
                    logger.warning(
                        "RECONCILIATION_BREAKER_OPEN",
                        extra={
                            "failure_count": _failure_count,
                            "open_until_s": BREAKER_OPEN_SECONDS
                        }
                    )
                await asyncio.sleep(min(60, 2 ** _failure_count))
        except asyncio.CancelledError:
            logger.info("Xray reconciliation task cancelled")
            raise
        except Exception as e:
            logger.error(f"reconcile_xray_state_task: {e}", exc_info=True)
            await asyncio.sleep(60)
