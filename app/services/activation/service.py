"""
Activation Service Layer

This module provides business logic for subscription activation, retry logic, and status management.
It coordinates between database, VPN service, and subscription service.

All functions are pure business logic:
- No aiogram imports
- No logging
- No Telegram calls
- Pure business logic only
"""

import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

import database
import config
import vpn_utils
from app.core.pool_monitor import acquire_connection
from app.services.activation.exceptions import (
    ActivationServiceError,
    ActivationNotAllowedError,
    ActivationMaxAttemptsReachedError,
    ActivationFailedError,
    VPNActivationError,
)

logger = logging.getLogger(__name__)


# ====================================================================================
# Configuration
# ====================================================================================

def get_max_activation_attempts() -> int:
    """Get maximum activation attempts from config"""
    import os
    max_attempts = int(os.getenv("MAX_ACTIVATION_ATTEMPTS", "5"))
    if max_attempts < 1:
        return 1
    if max_attempts > 20:
        return 20
    return max_attempts


def get_notification_threshold_minutes() -> int:
    """Get notification threshold in minutes (default: 30)"""
    return 30


# ====================================================================================
# Result Types
# ====================================================================================

@dataclass
class ActivationResult:
    """Result of activation attempt"""
    success: bool
    uuid: Optional[str]
    vpn_key: Optional[str]
    activation_status: str  # "active" or "failed"
    attempts: int
    vpn_key_plus: Optional[str] = None  # second key for plus only
    error: Optional[str] = None


@dataclass
class PendingSubscription:
    """Pending subscription information"""
    subscription_id: int
    telegram_id: int
    activation_attempts: int
    last_activation_error: Optional[str]
    expires_at: Optional[datetime]
    activated_at: Optional[datetime]


# ====================================================================================
# Activation Decision Logic
# ====================================================================================

def is_subscription_expired(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """
    Check if subscription has expired.
    
    Args:
        expires_at: Subscription expiration date
        now: Current time (defaults to datetime.now(timezone.utc))
        
    Returns:
        True if subscription has expired, False otherwise
    """
    if expires_at is None:
        return False
    
    if now is None:
        now = datetime.now(timezone.utc)
    
    return expires_at < now


def should_retry_activation(
    activation_attempts: int,
    max_attempts: Optional[int] = None
) -> bool:
    """
    Determine if activation should be retried.
    
    Args:
        activation_attempts: Current number of activation attempts
        max_attempts: Maximum allowed attempts (defaults to config value)
        
    Returns:
        True if activation should be retried, False if max attempts reached
    """
    if max_attempts is None:
        max_attempts = get_max_activation_attempts()
    
    return activation_attempts < max_attempts


def is_activation_allowed(
    subscription: Dict[str, Any],
    now: Optional[datetime] = None
) -> Tuple[bool, Optional[str]]:
    """
    Check if activation is allowed for a subscription.
    
    Args:
        subscription: Subscription dictionary from database
        now: Current time (defaults to datetime.now(timezone.utc))
        
    Returns:
        Tuple of (is_allowed, reason_if_not_allowed)
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    # STEP 3 — PART C: SIDE-EFFECT SAFETY
    # Check activation status - provides idempotency boundary
    # Activation is only allowed if status is 'pending'
    # If already activated (status='active'), side-effect is SKIPPED
    activation_status = subscription.get("activation_status")
    if activation_status != "pending":
        # STEP 3 — PART C: SIDE-EFFECT SAFETY
        # Activation side-effect SKIPPED due to idempotency (already activated)
        return False, f"Subscription is not pending (status={activation_status})"
    
    # Check if subscription expired
    expires_at = subscription.get("expires_at")
    if is_subscription_expired(expires_at, now):
        return False, "Subscription expired before activation"
    
    # Check max attempts
    activation_attempts = subscription.get("activation_attempts", 0)
    if not should_retry_activation(activation_attempts):
        return False, f"Maximum activation attempts reached ({activation_attempts})"
    
    return True, None


# ====================================================================================
# Database Queries
# ====================================================================================

async def get_pending_subscriptions(
    max_attempts: Optional[int] = None,
    limit: int = 50,
    conn: Optional[Any] = None
) -> List[PendingSubscription]:
    """
    Get subscriptions with pending activation status.
    
    Args:
        max_attempts: Maximum activation attempts (defaults to config value)
        limit: Maximum number of subscriptions to return
        conn: Database connection (if None, creates new connection)
        
    Returns:
        List of PendingSubscription objects
    """
    if max_attempts is None:
        max_attempts = get_max_activation_attempts()
    
    if conn is None:
        pool = await database.get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            return await _fetch_pending_subscriptions(conn, max_attempts, limit)
    else:
        return await _fetch_pending_subscriptions(conn, max_attempts, limit)


async def _fetch_pending_subscriptions(
    conn: Any,
    max_attempts: int,
    limit: int
) -> List[PendingSubscription]:
    """Internal helper to fetch pending subscriptions"""
    rows = await conn.fetch(
        """SELECT telegram_id, id, activation_attempts, last_activation_error, expires_at, activated_at
           FROM subscriptions
           WHERE activation_status = 'pending'
             AND activation_attempts < $1
           ORDER BY id ASC
           LIMIT $2""",
        max_attempts, limit
    )
    
    result = []
    for row in rows:
        result.append(PendingSubscription(
            subscription_id=row["id"],
            telegram_id=row["telegram_id"],
            activation_attempts=row["activation_attempts"],
            last_activation_error=row.get("last_activation_error"),
            expires_at=database._from_db_utc(row["expires_at"]) if row.get("expires_at") else None,
            activated_at=database._from_db_utc(row["activated_at"]) if row.get("activated_at") else None
        ))
    
    return result


async def get_pending_for_notification(
    threshold_minutes: Optional[int] = None,
    conn: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Get pending subscriptions that should trigger admin notification.
    
    Args:
        threshold_minutes: Minutes threshold for notification (defaults to config value)
        conn: Database connection (if None, creates new connection)
        
    Returns:
        List of subscription dictionaries with notification data
    """
    if threshold_minutes is None:
        threshold_minutes = get_notification_threshold_minutes()
    
    if conn is None:
        pool = await database.get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            return await _fetch_pending_for_notification(conn, threshold_minutes)
    else:
        return await _fetch_pending_for_notification(conn, threshold_minutes)


async def _fetch_pending_for_notification(
    conn: Any,
    threshold_minutes: int
) -> List[Dict[str, Any]]:
    """Internal helper to fetch subscriptions for notification"""
    notification_threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    
    rows = await conn.fetch(
        """SELECT telegram_id, id, activation_attempts, last_activation_error, activated_at
           FROM subscriptions
           WHERE activation_status = 'pending'
             AND (activation_attempts >= 2 
                  OR (activated_at IS NOT NULL AND activated_at < $1))
           ORDER BY COALESCE(activated_at, '1970-01-01'::timestamp) ASC
           LIMIT 10""",
        database._to_db_utc(notification_threshold)
    )
    
    result = []
    for row in rows:
        activated_at = row.get("activated_at")
        if activated_at and isinstance(activated_at, str):
            try:
                activated_at = datetime.fromisoformat(activated_at.replace('Z', '+00:00'))
            except Exception as e:
                logger.debug("Activated_at parse failed, using now: %s", e)
                activated_at = datetime.now(timezone.utc)
        elif not activated_at:
            activated_at = datetime.now(timezone.utc)
        
        result.append({
            "subscription_id": row["id"],
            "telegram_id": row["telegram_id"],
            "attempts": row["activation_attempts"],
            "error": row.get("last_activation_error") or "N/A",
            "pending_since": activated_at
        })
    
    return result


# ====================================================================================
# Activation Attempt Logic
# ====================================================================================

async def attempt_activation(
    subscription_id: int,
    telegram_id: int,
    current_attempts: int,
    conn: Optional[Any] = None,
    pool: Optional[Any] = None,
) -> ActivationResult:
    """
    Attempt to activate a subscription by calling VPN API.

    POOL STABILITY: No DB connection is held during the VPN HTTP call.
    Flow: (1) short-lived conn to fetch subscription row; (2) release; (3) HTTP add_vless_user;
    (4) acquire conn, advisory lock, re-check state, transaction update; (5) unlock, release.

    IDEMPOTENCY: If subscription is already active, returns existing activation.
    Advisory lock protects only the DB mutation phase; idempotency re-check inside transaction.

    Args:
        subscription_id: Subscription ID
        telegram_id: Telegram ID of the user
        current_attempts: Current number of activation attempts
        conn: Deprecated; ignored. Use pool.
        pool: Database pool (if None, get_pool() is used)

    Returns:
        ActivationResult with activation details
    """
    if pool is None:
        pool = await database.get_pool()
    if pool is None:
        raise ActivationFailedError("Database pool is not available")
    return await _attempt_activation_no_conn_hold(pool, subscription_id, telegram_id, current_attempts)


async def _attempt_activation_no_conn_hold(
    pool: Any,
    subscription_id: int,
    telegram_id: int,
    current_attempts: int,
) -> ActivationResult:
    """
    Two-phase activation without holding a connection during HTTP.
    Phase 1: short-lived conn to fetch row; release. Phase 2: HTTP add_vless_user (no conn).
    Phase 3: acquire conn, advisory lock, re-fetch (idempotency), transaction update, unlock.
    """
    # Phase 1: Pre-fetch subscription with short-lived conn; release immediately.
    async with acquire_connection(pool, "activation_phase1_fetch") as conn:
        subscription_row = await conn.fetchrow(
            """SELECT activation_status, uuid, vpn_key, vpn_key_plus, activation_attempts, expires_at, subscription_type
               FROM subscriptions WHERE id = $1""",
            subscription_id
        )
    if not subscription_row:
        raise ActivationFailedError(f"Subscription {subscription_id} not found")
    current_status = subscription_row["activation_status"]
    if current_status == "active":
        return ActivationResult(
            success=True,
            uuid=subscription_row.get("uuid"),
            vpn_key=subscription_row.get("vpn_key"),
            vpn_key_plus=subscription_row.get("vpn_key_plus"),
            activation_status="active",
            attempts=subscription_row.get("activation_attempts", current_attempts)
        )
    if current_status != "pending":
        raise ActivationNotAllowedError(
            f"Subscription {subscription_id} is not pending (status={current_status})"
        )
    if not config.VPN_ENABLED:
        raise VPNActivationError("VPN API is not enabled")
    subscription_end_raw = subscription_row.get("expires_at")
    if not subscription_end_raw:
        raise VPNActivationError("Subscription has no expires_at")
    subscription_end = database._from_db_utc(subscription_end_raw)

    # Phase 2: HTTP call with NO DB connection held.
    tariff = (subscription_row.get("subscription_type") or "basic").strip().lower()
    if tariff not in ("basic", "plus"):
        tariff = "basic"
    new_uuid = database._generate_subscription_uuid()
    try:
        vless_result = await vpn_utils.add_vless_user(
            telegram_id=telegram_id,
            subscription_end=subscription_end,
            uuid=new_uuid,
            tariff=tariff,
        )
        vless_url = vless_result.get("vless_url")
        vless_url_plus = vless_result.get("vless_url_plus")
        uuid_from_api = vless_result.get("uuid")
        if not uuid_from_api:
            raise VPNActivationError("Xray API returned empty UUID")
        new_uuid = uuid_from_api
    except Exception as e:
        raise VPNActivationError(f"VPN API call failed: {e}") from e
    if not new_uuid:
        raise VPNActivationError("VPN API returned empty UUID")
    if not vless_url:
        raise VPNActivationError("VPN API returned empty vless_url")
    logger.info(
        "ACTIVATION_PHASE1_UUID_CREATED",
        extra={"subscription_id": subscription_id, "uuid": new_uuid[:8] + "..."}
    )
    uuid_to_cleanup_on_failure = new_uuid

    # Phase 3: Acquire conn, advisory lock, re-check state (idempotency), then transaction.
    async with acquire_connection(pool, "activation_phase3_lock") as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", subscription_id)
        try:
            # Idempotency: re-fetch in case state changed during HTTP window.
            recheck_row = await conn.fetchrow(
                """SELECT activation_status, uuid, vpn_key, vpn_key_plus, activation_attempts
                   FROM subscriptions WHERE id = $1""",
                subscription_id
            )
            if not recheck_row:
                raise ActivationFailedError(f"Subscription {subscription_id} not found")
            if recheck_row["activation_status"] == "active":
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    logger.critical(
                        "ACTIVATION_ORPHAN_PREVENTED",
                        extra={"subscription_id": subscription_id, "uuid": uuid_to_cleanup_on_failure[:8] + "...", "reason": "concurrent_activation"}
                    )
                except Exception:
                    pass
                return ActivationResult(
                    success=True,
                    uuid=recheck_row.get("uuid"),
                    vpn_key=recheck_row.get("vpn_key"),
                    vpn_key_plus=recheck_row.get("vpn_key_plus"),
                    activation_status="active",
                    attempts=recheck_row.get("activation_attempts", current_attempts)
                )
            if recheck_row["activation_status"] != "pending":
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    logger.critical(
                        "ACTIVATION_ORPHAN_PREVENTED",
                        extra={"subscription_id": subscription_id, "uuid": uuid_to_cleanup_on_failure[:8] + "...", "reason": "state_changed"}
                    )
                except Exception:
                    pass
                raise ActivationNotAllowedError(
                    f"Subscription {subscription_id} is not pending (status={recheck_row['activation_status']})"
                )

            result = None
            try:
                async with conn.transaction():
                    result = await conn.execute(
                        """UPDATE subscriptions
                           SET uuid = $1, vpn_key = $2, vpn_key_plus = $3, activation_status = 'active',
                               activation_attempts = $4, last_activation_error = NULL
                           WHERE id = $5 AND activation_status = 'pending'""",
                        new_uuid, vless_url, vless_url_plus, current_attempts + 1, subscription_id
                    )
                logger.info(
                    "ACTIVATION_PHASE2_DB_COMMIT",
                    extra={"subscription_id": subscription_id, "uuid": new_uuid[:8] + "..."}
                )
            except Exception as tx_err:
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    logger.critical(
                        "ACTIVATION_ORPHAN_PREVENTED",
                        extra={"subscription_id": subscription_id, "uuid": uuid_to_cleanup_on_failure[:8] + "...", "error": str(tx_err)[:200]}
                    )
                except Exception as remove_err:
                    logger.critical(
                        "ACTIVATION_ORPHAN_PREVENTED_REMOVAL_FAILED",
                        extra={"subscription_id": subscription_id, "uuid": uuid_to_cleanup_on_failure[:8] + "...", "remove_error": str(remove_err)[:200]}
                    )
                raise ActivationFailedError(f"Failed to update subscription after VPN API success: {tx_err}") from tx_err

            rows_affected = int(result.split()[-1]) if result else 0
            if rows_affected == 0:
                updated_row = await conn.fetchrow(
                    "SELECT uuid, vpn_key, vpn_key_plus, activation_status, activation_attempts FROM subscriptions WHERE id = $1",
                    subscription_id
                )
                if updated_row and updated_row["activation_status"] == "active":
                    return ActivationResult(
                        success=True,
                        uuid=updated_row.get("uuid"),
                        vpn_key=updated_row.get("vpn_key"),
                        vpn_key_plus=updated_row.get("vpn_key_plus"),
                        activation_status="active",
                        attempts=updated_row.get("activation_attempts", current_attempts + 1)
                    )
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    logger.critical(
                        "ACTIVATION_ORPHAN_PREVENTED",
                        extra={"subscription_id": subscription_id, "uuid": uuid_to_cleanup_on_failure[:8] + "...", "reason": "concurrent_activation"}
                    )
                except Exception:
                    pass
                raise ActivationFailedError(f"Failed to update subscription {subscription_id} (concurrent modification)")
            return ActivationResult(
                success=True,
                uuid=new_uuid,
                vpn_key=vless_url,
                vpn_key_plus=vless_url_plus,
                activation_status="active",
                attempts=current_attempts + 1
            )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", subscription_id)


async def _update_subscription_activated(
    conn: Any,
    subscription_id: int,
    uuid: str,
    vpn_key: str,
    new_attempts: int
) -> None:
    """
    Internal helper to update subscription after successful activation.
    
    NOTE: This function is now deprecated - idempotency check is handled in
    _attempt_activation_two_phase_impl(). This function is kept for backward
    compatibility but should not be called directly.
    """
    await conn.execute(
        """UPDATE subscriptions
           SET uuid = $1,
               vpn_key = $2,
               activation_status = 'active',
               activation_attempts = $3,
               last_activation_error = NULL
           WHERE id = $4
             AND activation_status = 'pending'""",
        uuid, vpn_key, new_attempts, subscription_id
    )


async def mark_activation_failed(
    subscription_id: int,
    new_attempts: int,
    error_msg: str,
    max_attempts: Optional[int] = None,
    conn: Optional[Any] = None
) -> None:
    """
    Mark activation as failed and update attempt counter.
    
    If max attempts reached, sets activation_status to 'failed'.
    
    Args:
        subscription_id: Subscription ID
        new_attempts: New attempt count (current + 1)
        error_msg: Error message
        max_attempts: Maximum activation attempts (defaults to config value)
        conn: Database connection (if None, creates new connection)
    """
    if max_attempts is None:
        max_attempts = get_max_activation_attempts()
    
    if conn is None:
        pool = await database.get_pool()
        if pool is None:
            raise ActivationFailedError("Database pool is not available")
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _update_subscription_failed(conn, subscription_id, new_attempts, error_msg, max_attempts)
    else:
        async with conn.transaction():
            await _update_subscription_failed(conn, subscription_id, new_attempts, error_msg, max_attempts)


async def _update_subscription_failed(
    conn: Any,
    subscription_id: int,
    new_attempts: int,
    error_msg: str,
    max_attempts: int,
    mark_as_failed: bool = True
) -> None:
    """
    Internal helper to update subscription after failed activation.
    
    Args:
        mark_as_failed: If True and max attempts reached, mark as 'failed'.
                       If False, keep as 'pending' for retry.
    """
    # Update attempts and error
    await conn.execute(
        """UPDATE subscriptions
           SET activation_attempts = $1,
               last_activation_error = $2
           WHERE id = $3""",
        new_attempts, error_msg, subscription_id
    )
    
    # If max attempts reached AND mark_as_failed=True, mark as failed
    # Otherwise, keep as pending for retry
    if new_attempts >= max_attempts and mark_as_failed:
        await conn.execute(
            """UPDATE subscriptions
               SET activation_status = 'failed'
               WHERE id = $1""",
            subscription_id
        )


async def mark_expired_subscription_failed(
    subscription_id: int,
    conn: Optional[Any] = None
) -> None:
    """
    Mark expired subscription as failed.
    
    Args:
        subscription_id: Subscription ID
        conn: Database connection (if None, creates new connection)
    """
    if conn is None:
        pool = await database.get_pool()
        if pool is None:
            raise ActivationFailedError("Database pool is not available")
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """UPDATE subscriptions
                       SET activation_status = 'failed',
                           last_activation_error = 'Subscription expired before activation'
                       WHERE id = $1""",
                    subscription_id
                )
    else:
        async with conn.transaction():
            await conn.execute(
                """UPDATE subscriptions
                   SET activation_status = 'failed',
                       last_activation_error = 'Subscription expired before activation'
                   WHERE id = $1""",
                subscription_id
            )
