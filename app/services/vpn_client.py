"""
VPN API Client — Facade for Telegram Bot ↔ Xray integration via vpn-api.

Architecture:
    Telegram Bot → vpn_client → vpn_utils (HTTP) → Xray API (localhost:8000)
                                         ↘ database (subscriptions)

All client management goes through vpn-api. No direct config.json edits.
Idempotent operations. Failures do not corrupt Xray config.

API endpoints (Xray API / vpn-api):
    POST /add-user    — create client
    POST /update-user — extend subscription
    POST /remove-user/{uuid} — disable client
    GET  /health      — availability check
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import config
import vpn_utils
import database


# =============================================================================
# Exceptions
# =============================================================================

class VPNClientError(Exception):
    """Base exception for VPN client operations."""
    pass


class VPNClientDisabled(VPNClientError):
    """VPN API is not configured or disabled."""
    pass


class VPNClientCreateError(VPNClientError):
    """Failed to create VPN user."""
    pass


class VPNClientExtendError(VPNClientError):
    """Failed to extend subscription."""
    pass


class VPNClientDisableError(VPNClientError):
    """Failed to disable VPN user."""
    pass


# =============================================================================
# Health Check
# =============================================================================

async def health_check() -> bool:
    """
    Verify vpn-api (Xray API) endpoint availability.

    Returns:
        True if GET /health returns ok, False otherwise.
    """
    return await vpn_utils.check_xray_health()


# =============================================================================
# Create User
# =============================================================================

async def create_user(
    telegram_id: int,
    days: int,
    source: str = "payment",
    conn=None
) -> Dict[str, Any]:
    """
    Create new VPN user via vpn-api.

    Uses database.grant_access which calls vpn_utils.add_vless_user → Xray API /add-user.

    Args:
        telegram_id: Telegram user ID
        days: Subscription duration in days
        source: Grant source ('payment', 'admin', 'test')
        conn: Optional DB connection (for transactional use)

    Returns:
        {"uuid": str, "config_link": str, "expires_at": datetime, "action": str}

    Raises:
        VPNClientDisabled: If VPN API is not configured
        VPNClientCreateError: If creation fails
    """
    if not config.VPN_ENABLED:
        raise VPNClientDisabled("VPN API is not configured (XRAY_API_URL/XRAY_API_KEY)")
    duration = timedelta(days=days)
    try:
        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=duration,
            source=source,
            conn=conn
        )
        uuid = result.get("uuid")
        vless_url = result.get("vless_url")
        subscription_end = result.get("subscription_end")
        if not vless_url and result.get("action") == "renewal":
            # Renewal returns vless_url=None; generate from uuid
            if uuid:
                vless_url = vpn_utils.generate_vless_url(uuid)
        return {
            "uuid": uuid,
            "config_link": vless_url or "",
            "expires_at": subscription_end,
            "action": result.get("action", "new_issuance")
        }
    except Exception as e:
        raise VPNClientCreateError(f"Failed to create VPN user: {e}") from e


# =============================================================================
# Extend User
# =============================================================================

async def extend_user(
    uuid: str,
    days: int,
    telegram_id: Optional[int] = None,
    conn=None
) -> Dict[str, Any]:
    """
    Extend subscription via vpn-api.

    Uses ensure_user_in_xray / update_vless_user → Xray API /update-user.
    For renewal flow, use database.grant_access (renewal path) which handles DB + Xray.

    Args:
        uuid: Client UUID
        days: Additional days
        telegram_id: Required for grant_access renewal path
        conn: Optional DB connection

    Returns:
        {"uuid": str, "expires_at": datetime}

    Raises:
        VPNClientDisabled: If VPN API is not configured
        VPNClientExtendError: If extend fails
    """
    if not config.VPN_ENABLED:
        raise VPNClientDisabled("VPN API is not configured")
    if not uuid or not uuid.strip():
        raise VPNClientExtendError("Invalid UUID")
    # Use grant_access for renewal when we have telegram_id (updates DB + Xray)
    if telegram_id:
        duration = timedelta(days=days)
        try:
            result = await database.grant_access(
                telegram_id=telegram_id,
                duration=duration,
                source="payment",
                conn=conn
            )
            return {
                "uuid": result.get("uuid"),
                "expires_at": result.get("subscription_end")
            }
        except Exception as e:
            raise VPNClientExtendError(f"Failed to extend subscription: {e}") from e
    # Fallback: update Xray only (no DB)
    try:
        now = datetime.now(timezone.utc)
        new_expires = now + timedelta(days=days)
        await vpn_utils.update_vless_user(uuid=uuid.strip(), subscription_end=new_expires)
        return {"uuid": uuid, "expires_at": new_expires}
    except Exception as e:
        raise VPNClientExtendError(f"Failed to extend subscription: {e}") from e


# =============================================================================
# Disable User
# =============================================================================

async def disable_user(uuid: str) -> None:
    """
    Disable VPN user via vpn-api.

    Calls POST /remove-user/{uuid}. Idempotent.

    Args:
        uuid: Client UUID to disable

    Raises:
        VPNClientDisabled: If VPN API is not configured
        VPNClientDisableError: If disable fails
    """
    if not config.VPN_ENABLED:
        raise VPNClientDisabled("VPN API is not configured")
    if not uuid or not uuid.strip():
        raise VPNClientDisableError("Invalid UUID")
    try:
        await vpn_utils.remove_vless_user(uuid.strip())
    except Exception as e:
        raise VPNClientDisableError(f"Failed to disable VPN user: {e}") from e


# =============================================================================
# Get User
# =============================================================================

async def get_user(uuid: str) -> Optional[Dict[str, Any]]:
    """
    Get user info by UUID from database.

    Xray API does not expose GET /users/{uuid}; data comes from subscriptions table.

    Args:
        uuid: Client UUID

    Returns:
        {"telegram_id", "uuid", "expires_at", "status", "vpn_key"} or None
    """
    if not uuid or not uuid.strip():
        return None
    pool = await database.get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT telegram_id, uuid, expires_at, status, vpn_key
               FROM subscriptions
               WHERE uuid = $1""",
            uuid.strip()
        )
    if not row:
        return None
    expires = database._from_db_utc(row["expires_at"]) if row.get("expires_at") else None
    return {
        "telegram_id": row["telegram_id"],
        "uuid": row["uuid"],
        "expires_at": expires,
        "status": row["status"],
        "vpn_key": row.get("vpn_key")
    }
