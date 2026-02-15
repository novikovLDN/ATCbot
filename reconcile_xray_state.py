"""
Xray Reconciliation Worker — Orphan UUID Cleanup

Compares UUIDs in DB (subscriptions) vs Xray API.
- ORPHANS (in Xray, not in DB): remove from Xray only after live DB re-check (no active UUID ever removed)
- MISSING_IN_XRAY (in DB, not in Xray): log CRITICAL, require manual review (do NOT auto-recreate)

Safety (CRITICAL FIX):
- TWO-LAYER PROTECTION: (1) Grace window filter (2) Live DB re-check before every delete
- Reconcile NEVER deletes UUID belonging to an active subscription
- Reconcile NEVER deletes UUID created/recently updated within RECONCILE_GRACE_SECONDS
- Batch size limit (default 100 per run)
- Feature flag: XRAY_RECONCILIATION_ENABLED
"""
import asyncio
import logging
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import database
import config
import vpn_utils
from app.core.metrics import get_metrics
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection

logger = logging.getLogger(__name__)

RECONCILIATION_TIMEOUT_SECONDS = int(os.getenv("XRAY_RECONCILIATION_TIMEOUT_SECONDS", "20"))
RECONCILE_GRACE_SECONDS = int(os.getenv("RECONCILE_GRACE_SECONDS", "120"))  # Never delete UUID touched in last N seconds
_worker_lock = asyncio.Lock()


def _is_valid_uuid(val: str) -> bool:
    """Validate UUID format; skip non-UUID entries to prevent retry storms."""
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, TypeError, AttributeError):
        return False

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
    Compare DB vs Xray UUIDs; remove orphans with TWO-LAYER protection.

    LAYER 1: Grace window — never delete UUID touched within RECONCILE_GRACE_SECONDS.
    LAYER 2: Live DB re-check before every delete — if subscription row exists and is
    active or recently expired/touched, skip. Only delete when no row exists or row
    is expired and older than grace window.

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
        pool = await database.get_pool()
        if not pool:
            return result

        now_utc = datetime.now(timezone.utc)
        grace_delta = timedelta(seconds=RECONCILE_GRACE_SECONDS)
        cutoff_old = now_utc - grace_delta

        # 1. Fetch DB uuid map: uuid -> (status, expires_at, last_touch) for grace/snapshot
        db_map = {}
        last_seen_id = 0
        while True:
            async with acquire_connection(pool, "reconcile_fetch_db") as conn:
                rows = await conn.fetch(
                    """SELECT id, uuid, status, expires_at,
                              COALESCE(last_auto_renewal_at, activated_at) AS last_touch
                       FROM subscriptions
                       WHERE uuid IS NOT NULL AND id > $1
                       ORDER BY id ASC
                       LIMIT $2""",
                    last_seen_id,
                    BATCH_SIZE_LIMIT
                )
            if not rows:
                break
            for r in rows:
                u = (r.get("uuid") or "").strip()
                if not u:
                    continue
                expires_at = database._from_db_utc(r["expires_at"]) if r.get("expires_at") else None
                last_touch = database._from_db_utc(r["last_touch"]) if r.get("last_touch") else None
                db_map[u] = (r.get("status"), expires_at, last_touch)
            last_seen_id = rows[-1]["id"]
            await asyncio.sleep(0)

        db_uuids = set(db_map.keys())

        # 2. Fetch UUID list from Xray API
        xray_uuids = set(await vpn_utils.list_vless_users())

        # 3. Orphan candidates: in Xray but not in snapshot (or in snapshot but will be re-checked live)
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

        # 4. Per-UUID: live DB re-check, then delete only if safe (no connection held during HTTP)
        orphans_list = list(orphans)[:BATCH_SIZE_LIMIT]
        for i, uuid_val in enumerate(orphans_list):
            if i > 0 and i % 50 == 0:
                await cooperative_yield()
            if not _is_valid_uuid(uuid_val):
                logger.info("Skipping non-UUID entry in Xray config", extra={"uuid": str(uuid_val)[:64]})
                continue

            uuid_preview = f"{uuid_val[:8]}..." if len(uuid_val) > 8 else "***"

            # LAYER 2: Authoritative live re-check before any delete
            async with acquire_connection(pool, "reconcile_live_check") as conn:
                row = await conn.fetchrow(
                    """SELECT id, status, expires_at,
                              COALESCE(last_auto_renewal_at, activated_at) AS last_touch
                       FROM subscriptions
                       WHERE uuid = $1
                       LIMIT 1""",
                    uuid_val
                )

            if row is not None:
                status = row.get("status")
                expires_at = database._from_db_utc(row["expires_at"]) if row.get("expires_at") else None
                last_touch = database._from_db_utc(row["last_touch"]) if row.get("last_touch") else None

                if status == "active":
                    logger.info("RECONCILE_SKIP_ACTIVE_UUID", extra={"uuid": uuid_preview})
                    continue
                if last_touch is not None and last_touch > cutoff_old:
                    logger.info("RECONCILE_SKIP_GRACE_WINDOW", extra={"uuid": uuid_preview})
                    continue
                if expires_at is not None and expires_at > cutoff_old:
                    logger.info("RECONCILE_SKIP_GRACE_WINDOW", extra={"uuid": uuid_preview})
                    continue

            # Safe to delete: no row, or row is expired and older than grace
            try:
                await vpn_utils.remove_vless_user(uuid_val)
                result["orphans_removed"] += 1
                logger.info("RECONCILE_UUID_DELETE", extra={"uuid": uuid_preview})
                get_metrics().increment_counter("reconciliation_orphans_removed", value=1)
            except Exception as e:
                result["errors"].append(str(e))
                logger.warning("reconciliation_remove_failed", extra={"uuid": uuid_preview, "error": str(e)})

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

    # POOL STABILITY: One-time startup jitter to avoid 600s worker alignment burst.
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug(f"reconcile_xray_state_task: startup jitter done ({jitter_s:.1f}s)")

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
                async with _worker_lock:
                    r = await asyncio.wait_for(reconcile_xray_state(), timeout=RECONCILIATION_TIMEOUT_SECONDS)
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
            except asyncio.TimeoutError:
                _failure_count += 1
                _last_failure_ts = time.time()
                logger.error("Reconciliation timeout — iteration aborted safely")
                if _failure_count >= BREAKER_FAILURE_THRESHOLD:
                    _breaker_open_until = time.time() + BREAKER_OPEN_SECONDS
                    logger.warning("RECONCILIATION_BREAKER_OPEN", extra={"failure_count": _failure_count})
                await asyncio.sleep(min(60, 2 ** _failure_count))
                continue
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
