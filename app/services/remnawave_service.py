"""
High-level Remnawave operations (create / renew / delete / add_traffic).

All public functions follow fire-and-forget pattern:
- *_bg() variants schedule work as background tasks
- Errors are logged but never raised to callers
- Main subscription flow must never fail because of Remnawave
"""
import asyncio
import logging
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Optional

import config
import database
from app.services import remnawave_api

logger = logging.getLogger(__name__)

# Background task set (prevent GC)
_bg_tasks: set = set()


def _fire_and_forget(coro) -> None:
    try:
        task = asyncio.create_task(coro)
        _bg_tasks.add(task)

        def _done(t):
            _bg_tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.warning("REMNAWAVE_BG_FAIL: %s", t.exception())

        task.add_done_callback(_done)
    except Exception as e:
        logger.warning("REMNAWAVE_BG_SCHEDULE_FAIL: %s", e)


def _is_valid_full_uuid(s: str) -> bool:
    """Check if string looks like a full UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)."""
    try:
        uuid_lib.UUID(s, version=4)
        return True
    except (ValueError, AttributeError):
        return len(s) == 36 and s.count("-") == 4


def _traffic_limit_for_tariff(tariff: str) -> int:
    """Return traffic limit bytes for tariff. 0 for trial/unknown."""
    return config.TRAFFIC_LIMITS.get(tariff, 0)


def _device_limit_for_tariff(tariff: str) -> int:
    return config.DEVICE_LIMITS.get(tariff, 3)


async def _get_user_with_recovery(telegram_id: int, rmn_uuid: str):
    """Get user by stored UUID. If stored value is a legacy shortUuid, clear it
    so the caller can recreate the user with proper UUID storage."""
    if not _is_valid_full_uuid(rmn_uuid):
        # Legacy bug: shortUuid was stored instead of full UUID.
        logger.warning(
            "REMNAWAVE_INVALID_UUID: tg=%s stored=%s is not a full UUID, clearing",
            telegram_id, rmn_uuid,
        )
        await database.clear_remnawave_uuid(telegram_id)
        return None

    user_data = await remnawave_api.get_user(rmn_uuid)
    return user_data


# ── Create ──────────────────────────────────────────────────────────────

async def create_remnawave_user(
    telegram_id: int,
    tariff: str,
    subscription_end: datetime,
    traffic_limit_override: Optional[int] = None,
) -> None:
    """Create a Remnawave user for the given subscriber.

    Args:
        traffic_limit_override: if set, use this instead of tariff-based limit.
            Used for auto-provisioning existing users with a smaller starter pack.
    """
    if not config.REMNAWAVE_ENABLED:
        return
    if tariff == "trial":
        return  # Trial has NO bypass

    traffic_limit = traffic_limit_override or _traffic_limit_for_tariff(tariff)
    if traffic_limit <= 0:
        return

    try:
        short_uuid = str(uuid_lib.uuid4())[:12]
        expire_str = subscription_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        result = await remnawave_api.create_user(
            username=str(telegram_id),
            short_uuid=short_uuid,
            traffic_limit_bytes=traffic_limit,
            expire_at=expire_str,
            device_limit=_device_limit_for_tariff(tariff),
        )
        if result:
            # Save full UUID for API calls (/api/users/{uuid})
            rmn_uuid = result.get("uuid") or short_uuid
            await database.set_remnawave_uuid(telegram_id, rmn_uuid)
            await database.reset_traffic_notification_flags(telegram_id)
            sub_url = result.get("subscriptionUrl", "")
            logger.info(
                "REMNAWAVE_USER_CREATED: tg=%s uuid=%s sub_url=%s tariff=%s limit=%d",
                telegram_id, rmn_uuid[:8], sub_url, tariff, traffic_limit,
            )
        else:
            logger.warning("REMNAWAVE_USER_CREATE_FAILED: tg=%s", telegram_id)
    except Exception as e:
        logger.error("REMNAWAVE_CREATE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def create_remnawave_user_bg(telegram_id: int, tariff: str, subscription_end: datetime) -> None:
    _fire_and_forget(create_remnawave_user(telegram_id, tariff, subscription_end))


async def ensure_squad(telegram_id: int) -> None:
    """Ensure existing Remnawave user is assigned to the configured squad.
    Safe to call multiple times — Remnawave will ignore if already assigned."""
    if not config.REMNAWAVE_ENABLED or not config.REMNAWAVE_SQUAD_UUID:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        await remnawave_api.assign_user_to_squad(rmn_uuid, config.REMNAWAVE_SQUAD_UUID)
    except Exception as e:
        logger.error("REMNAWAVE_ENSURE_SQUAD_ERROR: tg=%s %s", telegram_id, e)


# ── Renew (extend traffic) ─────────────────────────────────────────────

async def renew_remnawave_user(
    telegram_id: int,
    tariff: str,
    subscription_end: datetime,
) -> None:
    """Renew: add tariff traffic to current limit, update expiry."""
    if not config.REMNAWAVE_ENABLED:
        return
    if tariff == "trial":
        return

    traffic_add = _traffic_limit_for_tariff(tariff)
    if traffic_add <= 0:
        return

    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            # User has no Remnawave account yet — create one
            await create_remnawave_user(telegram_id, tariff, subscription_end)
            return

        # Get current limit and add tariff traffic
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            # User might have been deleted from Remnawave — recreate
            await create_remnawave_user(telegram_id, tariff, subscription_end)
            return

        api_uuid = user_data.get("uuid") or rmn_uuid
        current_limit = user_data.get("trafficLimitBytes", 0)
        new_limit = current_limit + traffic_add
        expire_str = subscription_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        await remnawave_api.update_user(
            api_uuid,
            trafficLimitBytes=new_limit,
            expireAt=expire_str,
            deviceLimit=_device_limit_for_tariff(tariff),
        )
        # Re-enable if disabled
        if user_data.get("status") != "ACTIVE":
            await remnawave_api.update_user(api_uuid, status="ACTIVE")
        # Ensure squad assigned (idempotent)
        if config.REMNAWAVE_SQUAD_UUID:
            await remnawave_api.assign_user_to_squad(api_uuid, config.REMNAWAVE_SQUAD_UUID)
        await database.reset_traffic_notification_flags(telegram_id)
        logger.info(
            "REMNAWAVE_RENEWED: tg=%s uuid=%s old_limit=%d new_limit=%d",
            telegram_id, api_uuid[:8], current_limit, new_limit,
        )
    except Exception as e:
        logger.error("REMNAWAVE_RENEW_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def renew_remnawave_user_bg(telegram_id: int, tariff: str, subscription_end: datetime) -> None:
    _fire_and_forget(renew_remnawave_user(telegram_id, tariff, subscription_end))


# ── Disable (subscription expired) ─────────────────────────────────────

async def disable_remnawave_user(telegram_id: int) -> None:
    """Disable Remnawave user when subscription expires (keep data)."""
    if not config.REMNAWAVE_ENABLED:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            return
        api_uuid = user_data.get("uuid") or rmn_uuid
        await remnawave_api.update_user(api_uuid, status="DISABLED")
        logger.info("REMNAWAVE_DISABLED: tg=%s uuid=%s", telegram_id, api_uuid[:8])
    except Exception as e:
        logger.error("REMNAWAVE_DISABLE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def disable_remnawave_user_bg(telegram_id: int) -> None:
    _fire_and_forget(disable_remnawave_user(telegram_id))


# ── Delete ─────────────────────────────────────────────────────────────

async def delete_remnawave_user(telegram_id: int) -> None:
    """Delete Remnawave user and clear DB reference."""
    if not config.REMNAWAVE_ENABLED:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        api_uuid = (user_data.get("uuid") if user_data else None) or rmn_uuid
        await remnawave_api.delete_user(api_uuid)
        await database.clear_remnawave_uuid(telegram_id)
        logger.info("REMNAWAVE_DELETED: tg=%s uuid=%s", telegram_id, api_uuid[:8])
    except Exception as e:
        logger.error("REMNAWAVE_DELETE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def delete_remnawave_user_bg(telegram_id: int) -> None:
    _fire_and_forget(delete_remnawave_user(telegram_id))


# ── Add traffic (purchased pack) ──────────────────────────────────────

async def add_traffic(telegram_id: int, extra_bytes: int) -> bool:
    """Add purchased traffic to current limit. Returns True on success."""
    if not config.REMNAWAVE_ENABLED:
        return False
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return False

        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            return False

        api_uuid = user_data.get("uuid") or rmn_uuid
        current_limit = user_data.get("trafficLimitBytes", 0)
        new_limit = current_limit + extra_bytes

        result = await remnawave_api.update_user(api_uuid, trafficLimitBytes=new_limit)
        if result is not None:
            # Re-enable if disabled
            if user_data.get("status") != "ACTIVE":
                await remnawave_api.update_user(api_uuid, status="ACTIVE")
            await database.reset_traffic_notification_flags(telegram_id)
            logger.info(
                "REMNAWAVE_TRAFFIC_ADDED: tg=%s +%d bytes, new_limit=%d",
                telegram_id, extra_bytes, new_limit,
            )
            return True
        return False
    except Exception as e:
        logger.error("REMNAWAVE_ADD_TRAFFIC_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


# ── Tariff change (Basic → Plus) ───────────────────────────────────────

async def update_tariff(telegram_id: int, new_tariff: str) -> None:
    """Update device limit and traffic limit for tariff change."""
    if not config.REMNAWAVE_ENABLED:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        new_limit = _traffic_limit_for_tariff(new_tariff)
        new_devices = _device_limit_for_tariff(new_tariff)
        if new_limit <= 0:
            return
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            return
        api_uuid = user_data.get("uuid") or rmn_uuid
        await remnawave_api.update_user(
            api_uuid,
            trafficLimitBytes=new_limit,
            deviceLimit=new_devices,
        )
        logger.info("REMNAWAVE_TARIFF_UPDATED: tg=%s tariff=%s", telegram_id, new_tariff)
    except Exception as e:
        logger.error("REMNAWAVE_TARIFF_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
