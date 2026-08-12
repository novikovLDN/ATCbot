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
from datetime import datetime, timezone, timedelta
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


def _is_valid_panel_ref(s) -> bool:
    """Accept either a numeric Remnawave 3.x id (str/int) or a legacy full UUID.

    Used at call sites that previously rejected non-UUID strings — with
    the 3.x migration the stored identifier can be a numeric id, so we
    must not treat that as invalid.
    """
    if s is None:
        return False
    if isinstance(s, int):
        return s > 0
    try:
        ss = str(s).strip()
    except Exception:
        return False
    if not ss:
        return False
    if ss.isdigit():
        return int(ss) > 0
    return _is_valid_full_uuid(ss)


def _traffic_limit_for_tariff(tariff: str, period_days: int = 30) -> int:
    """Return traffic limit bytes for tariff+period. 0 for trial/unknown."""
    tariff_limits = config.TRAFFIC_LIMITS.get(tariff)
    if tariff_limits is None:
        return 0
    if isinstance(tariff_limits, dict):
        # Find closest matching period (fallback to 30-day)
        if period_days in tariff_limits:
            return tariff_limits[period_days]
        # Fallback: closest available period
        available = sorted(tariff_limits.keys())
        for p in available:
            if p >= period_days:
                return tariff_limits[p]
        return tariff_limits[available[-1]] if available else 0
    # Backward compat: flat int value
    return tariff_limits


def _device_limit_for_tariff(tariff: str) -> int:
    return config.DEVICE_LIMITS.get(tariff, 3)


async def _resolve_bypass_id(telegram_id: int) -> Optional[int]:
    """Best-effort: return the numeric bypass id for telegram_id.

    Uses the cache first, then the shared resolver (short-uuid /
    stream fallback), so callers get a usable Remnawave 3.x id even for
    users provisioned before migration 075.
    """
    try:
        panel_id = await database.get_remnawave_id(telegram_id)
    except Exception:
        panel_id = None
    if panel_id is not None:
        return int(panel_id)
    try:
        from app.services.remnawave_id_resolver import get_remnawave_id_for
        return await get_remnawave_id_for(telegram_id, "bypass")
    except Exception as e:
        logger.warning("REMNAWAVE_BYPASS_RESOLVE_FAIL: tg=%s %s", telegram_id, e)
        return None


async def _get_user_with_recovery(telegram_id: int, rmn_uuid):
    """Get user by stored identifier (numeric id or legacy UUID).

    On Remnawave 3.x the stored value is now the numeric BigInt id; on
    legacy rows it may still be a UUID string.  For a UUID we resolve
    the numeric id via the shared resolver (short-uuid / stream) and
    cache it back.  If neither yields a usable id the row is treated
    as gone from the panel.
    """
    if not _is_valid_panel_ref(rmn_uuid):
        # Legacy bug: shortUuid was stored instead of full UUID.
        logger.warning(
            "REMNAWAVE_INVALID_ID: tg=%s stored=%s is not a numeric id or full UUID, clearing",
            telegram_id, rmn_uuid,
        )
        await database.clear_remnawave_uuid(telegram_id)
        return None

    # If we already have a numeric id, use it directly.  Otherwise
    # attempt to resolve — the API layer refuses raw UUIDs on 3.x.
    api_id: Optional[int] = None
    try:
        api_id = int(str(rmn_uuid).strip())
    except (TypeError, ValueError):
        api_id = await _resolve_bypass_id(telegram_id)

    if api_id is None:
        logger.warning(
            "REMNAWAVE_ID_UNRESOLVABLE: tg=%s stored=%s — panel entity likely gone",
            telegram_id, rmn_uuid,
        )
        return None

    return await remnawave_api.get_user(api_id)


# ── Create ──────────────────────────────────────────────────────────────

async def create_remnawave_user(
    telegram_id: int,
    tariff: str,
    subscription_end: datetime,
    traffic_limit_override: Optional[int] = None,
    period_days: int = 30,
) -> None:
    """Create a Remnawave user for the given subscriber.

    Args:
        traffic_limit_override: if set, use this instead of tariff-based limit.
            Used for auto-provisioning existing users with a smaller starter pack.
        period_days: subscription period for traffic calculation.
    """
    if not config.REMNAWAVE_ENABLED:
        return
    if tariff == "trial" and not traffic_limit_override:
        return  # Trial without explicit override gets no bypass

    traffic_limit = traffic_limit_override or _traffic_limit_for_tariff(tariff, period_days)
    if traffic_limit <= 0:
        return

    try:
        short_uuid = str(uuid_lib.uuid4())[:12]
        # Bypass works by traffic (GB), not by date. Set expireAt far in the
        # future so Remnawave never auto-expires the user when the main
        # subscription ends — as long as GB remain, bypass must keep working.
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        expire_str = far_future.strftime("%Y-%m-%dT%H:%M:%SZ")

        result = await remnawave_api.create_user(
            username=str(telegram_id),
            short_uuid=short_uuid,
            traffic_limit_bytes=traffic_limit,
            expire_at=expire_str,
            device_limit=_device_limit_for_tariff(tariff),
            telegram_id=telegram_id,
        )
        if result:
            # 3.x response: `id` (BigInt) + `shortUuid` + `subscriptionUrl`.
            # The legacy `uuid` field is gone — we still save `shortUuid`
            # into remnawave_uuid for by-short-uuid fallback / historical
            # cross-reference.
            panel_id = result.get("id")
            legacy_uuid = result.get("uuid") or result.get("shortUuid") or short_uuid
            await database.set_remnawave_uuid(telegram_id, legacy_uuid)
            if panel_id is not None:
                try:
                    await database.set_remnawave_id(telegram_id, int(panel_id))
                except (TypeError, ValueError):
                    logger.warning(
                        "REMNAWAVE_ID_NOT_INT: tg=%s got=%r", telegram_id, panel_id,
                    )
            await database.reset_traffic_notification_flags(telegram_id)
            sub_url = result.get("subscriptionUrl", "")
            logger.info(
                "REMNAWAVE_USER_CREATED: tg=%s id=%s sub_url=%s tariff=%s limit=%d",
                telegram_id, panel_id, sub_url, tariff, traffic_limit,
            )
        else:
            logger.warning("REMNAWAVE_USER_CREATE_FAILED: tg=%s", telegram_id)
    except Exception as e:
        logger.error("REMNAWAVE_CREATE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def create_remnawave_user_bg(telegram_id: int, tariff: str, subscription_end: datetime, period_days: int = 30) -> None:
    _fire_and_forget(create_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days))


def _panel_api_id(user_data: dict, fallback) -> Optional[int]:
    """Extract Remnawave 3.x numeric id from a user payload, with a
    (possibly-stringified) fallback identifier from cache."""
    if isinstance(user_data, dict):
        raw = user_data.get("id")
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    if fallback is None:
        return None
    try:
        return int(str(fallback).strip())
    except (TypeError, ValueError):
        return None


async def ensure_squad(telegram_id: int) -> None:
    """Ensure existing Remnawave user is assigned to the configured squad.
    Checks first via GET — skips if already assigned."""
    if not config.REMNAWAVE_ENABLED or not config.REMNAWAVE_SQUAD_UUID:
        return
    try:
        api_id = await _resolve_bypass_id(telegram_id)
        if api_id is None:
            return
        # Quick check — if squad already assigned, skip
        user_data = await remnawave_api.get_user(api_id)
        if user_data:
            squads = user_data.get("activeInternalSquads") or []
            if squads:
                return  # Already has squad
            # No squad — assign
            await remnawave_api.assign_user_to_squad(api_id, config.REMNAWAVE_SQUAD_UUID)
    except Exception as e:
        logger.error("REMNAWAVE_ENSURE_SQUAD_ERROR: tg=%s %s", telegram_id, e)


# ── Renew (extend traffic) ─────────────────────────────────────────────

async def renew_remnawave_user(
    telegram_id: int,
    tariff: str,
    subscription_end: datetime,
    period_days: int = 30,
) -> None:
    """Renew: add tariff traffic to current limit, update expiry."""
    if not config.REMNAWAVE_ENABLED:
        return
    if tariff == "trial":
        return

    traffic_add = _traffic_limit_for_tariff(tariff, period_days)
    if traffic_add <= 0:
        return

    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            # User has no Remnawave account yet — create one
            await create_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days)
            return

        # Get current limit and add tariff traffic
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            # User might have been deleted from Remnawave — recreate
            await create_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days)
            return

        api_id = _panel_api_id(user_data, rmn_uuid)
        if api_id is None:
            logger.warning("REMNAWAVE_RENEW_NO_API_ID: tg=%s stored=%s", telegram_id, rmn_uuid)
            return
        current_limit = user_data.get("trafficLimitBytes", 0)
        new_limit = current_limit + traffic_add
        # Bypass works by traffic (GB), not by date — keep expireAt far future
        # so Remnawave is never marked expired while GB remain.
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        expire_str = far_future.strftime("%Y-%m-%dT%H:%M:%SZ")

        await remnawave_api.update_user(
            api_id,
            trafficLimitBytes=new_limit,
            expireAt=expire_str,
            deviceLimit=_device_limit_for_tariff(tariff),
        )
        # Re-enable if disabled
        if user_data.get("status") != "ACTIVE":
            await remnawave_api.update_user(api_id, status="ACTIVE")
        # Ensure squad assigned (skip if already has one)
        if config.REMNAWAVE_SQUAD_UUID:
            squads = user_data.get("activeInternalSquads") or []
            if not squads:
                await remnawave_api.assign_user_to_squad(api_id, config.REMNAWAVE_SQUAD_UUID)
        # Persist the id in case it was still cache-cold.
        try:
            await database.set_remnawave_id(telegram_id, api_id)
        except Exception:
            pass
        await database.reset_traffic_notification_flags(telegram_id)
        logger.info(
            "REMNAWAVE_RENEWED: tg=%s id=%s old_limit=%d new_limit=%d",
            telegram_id, api_id, current_limit, new_limit,
        )
    except Exception as e:
        logger.error("REMNAWAVE_RENEW_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def renew_remnawave_user_bg(telegram_id: int, tariff: str, subscription_end: datetime, period_days: int = 30) -> None:
    _fire_and_forget(renew_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days))


# ── Disable (subscription expired) ─────────────────────────────────────

async def extend_remnawave_for_bypass(telegram_id: int) -> None:
    """Extend Remnawave expiry to far future for bypass-only mode.

    When main subscription expires but user has bypass traffic,
    Remnawave user must stay ACTIVE with a far-future expireAt.
    Otherwise Remnawave marks user as expired and bypass stops working.
    """
    if not config.REMNAWAVE_ENABLED:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            return
        api_id = _panel_api_id(user_data, rmn_uuid)
        if api_id is None:
            return

        from datetime import timedelta
        far_future = (datetime.now(timezone.utc) + timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await remnawave_api.update_user(api_id, expireAt=far_future, status="ACTIVE")
        logger.info("REMNAWAVE_BYPASS_EXTENDED: tg=%s id=%s — expiry set to +10 years", telegram_id, api_id)
    except Exception as e:
        logger.error("REMNAWAVE_BYPASS_EXTEND_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def extend_remnawave_for_bypass_bg(telegram_id: int) -> None:
    _fire_and_forget(extend_remnawave_for_bypass(telegram_id))


async def disable_remnawave_user(telegram_id: int) -> None:
    """Disable Remnawave user when subscription expires.

    If user still has bypass traffic remaining — extend instead of disable.
    """
    if not config.REMNAWAVE_ENABLED:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            return
        api_id = _panel_api_id(user_data, rmn_uuid)
        if api_id is None:
            return

        # Check if user still has bypass traffic — don't disable if GB remaining
        traffic_limit = user_data.get("trafficLimitBytes", 0)
        traffic_used = user_data.get("usedTrafficBytes", 0)
        if traffic_limit > 0 and traffic_used < traffic_limit:
            # User still has bypass GB — extend instead of disable
            far_future = (datetime.now(timezone.utc) + timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
            await remnawave_api.update_user(api_id, expireAt=far_future, status="ACTIVE")
            logger.info("REMNAWAVE_KEPT_ACTIVE: tg=%s id=%s — bypass traffic remaining (%d/%d bytes)",
                        telegram_id, api_id, traffic_used, traffic_limit)
            return

        await remnawave_api.update_user(api_id, status="DISABLED")
        logger.info("REMNAWAVE_DISABLED: tg=%s id=%s", telegram_id, api_id)
    except Exception as e:
        logger.error("REMNAWAVE_DISABLE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def disable_remnawave_user_bg(telegram_id: int) -> None:
    _fire_and_forget(disable_remnawave_user(telegram_id))


# ── Delete ─────────────────────────────────────────────────────────────

async def delete_remnawave_user(telegram_id: int) -> None:
    """Delete Remnawave user (bypass entity only, kept for bwd-compat).
    Для полного удаления обеих entity (bypass + premium) используй
    delete_remnawave_user_full()."""
    if not config.REMNAWAVE_ENABLED:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        api_id = _panel_api_id(user_data, rmn_uuid) if user_data else None
        if api_id is None:
            # Fall back to whatever numeric id we have cached — if the
            # user is already gone from the panel delete_user() will just
            # 404 and clear_remnawave_uuid() below still cleans DB.
            api_id = await _resolve_bypass_id(telegram_id)
        if api_id is not None:
            await remnawave_api.delete_user(api_id)
        await database.clear_remnawave_uuid(telegram_id)
        logger.info("REMNAWAVE_DELETED: tg=%s id=%s", telegram_id, api_id)
    except Exception as e:
        logger.error("REMNAWAVE_DELETE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def delete_remnawave_user_bg(telegram_id: int) -> None:
    _fire_and_forget(delete_remnawave_user(telegram_id))


async def delete_remnawave_user_full(
    telegram_id: int,
    *,
    bypass_uuid: Optional[str] = None,
    bypass_id: Optional[int] = None,
    premium_uuid: Optional[str] = None,
    premium_id: Optional[int] = None,
) -> dict:
    """Удаляет ОБЕ entity юзера (bypass + premium) в Remnawave панели.

    Принимает опциональные uuid/id как аргументы — чтобы работать даже
    когда строка subscriptions уже удалена из БД (dashboard delete flow).
    Если параметры не переданы, читает из БД.

    Возвращает dict с результатами: {"bypass": bool, "premium": bool}
    """
    result = {"bypass": False, "premium": False}
    if not config.REMNAWAVE_ENABLED:
        return result

    # === Bypass entity ===
    try:
        if bypass_id is None and bypass_uuid is None:
            bypass_uuid = await database.get_remnawave_uuid(telegram_id)
        if bypass_id is None and bypass_uuid:
            user_data = await _get_user_with_recovery(telegram_id, bypass_uuid)
            bypass_id = _panel_api_id(user_data, bypass_uuid) if user_data else None
            if bypass_id is None:
                bypass_id = await _resolve_bypass_id(telegram_id)
        if bypass_id is not None:
            await remnawave_api.delete_user(int(bypass_id))
            result["bypass"] = True
            logger.info("REMNAWAVE_DELETED_BYPASS: tg=%s id=%s", telegram_id, bypass_id)
    except Exception as e:
        logger.error(
            "REMNAWAVE_DELETE_BYPASS_ERROR: tg=%s %s: %s",
            telegram_id, type(e).__name__, e,
        )

    # === Premium entity ===
    try:
        if premium_id is None and premium_uuid is None:
            # Попробуем через resolver (тянет из panel по short_uuid или stream).
            try:
                from app.services.remnawave_id_resolver import get_remnawave_id_for
                premium_id = await get_remnawave_id_for(telegram_id, kind="premium")
            except Exception as e:
                logger.debug("resolver premium failed tg=%s: %s", telegram_id, e)
        if premium_id is None and premium_uuid:
            # Панель-lookup по short_uuid → id
            try:
                entity = await remnawave_api.get_user_by_short_uuid(str(premium_uuid))
                if entity:
                    premium_id = entity.get("id")
            except Exception as e:
                logger.debug("premium by-short-uuid failed tg=%s: %s", telegram_id, e)
        if premium_id is not None:
            await remnawave_api.delete_user(int(premium_id))
            result["premium"] = True
            logger.info("REMNAWAVE_DELETED_PREMIUM: tg=%s id=%s", telegram_id, premium_id)
    except Exception as e:
        logger.error(
            "REMNAWAVE_DELETE_PREMIUM_ERROR: tg=%s %s: %s",
            telegram_id, type(e).__name__, e,
        )

    return result


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

        api_id = _panel_api_id(user_data, rmn_uuid)
        if api_id is None:
            return False
        current_limit = user_data.get("trafficLimitBytes", 0)
        new_limit = current_limit + extra_bytes

        result = await remnawave_api.update_user(api_id, trafficLimitBytes=new_limit)
        if result is not None:
            # Re-enable if disabled
            if user_data.get("status") != "ACTIVE":
                await remnawave_api.update_user(api_id, status="ACTIVE")
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


async def add_bypass_traffic(
    telegram_id: int,
    extra_bytes: int,
    subscription_type: str,
    subscription_end: Optional[datetime] = None,
    period_days: int = 30,
) -> bool:
    """Add bypass traffic; create Remnawave user if none exists yet.

    First-time combo/bypass buyers have no Remnawave UUID at the moment of
    payment confirmation, so plain add_traffic() returns False. This helper
    falls back to create_remnawave_user(traffic_limit_override=extra_bytes)
    so the purchased GB actually land on a fresh account.
    """
    if not config.REMNAWAVE_ENABLED:
        return False
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if rmn_uuid:
            if await add_traffic(telegram_id, extra_bytes):
                return True
            # Stale UUID (user deleted in Remnawave) — drop and recreate
            await database.clear_remnawave_uuid(telegram_id)

        expire_at = subscription_end or (datetime.now(timezone.utc) + timedelta(days=3650))
        await create_remnawave_user(
            telegram_id,
            subscription_type,
            expire_at,
            traffic_limit_override=extra_bytes,
            period_days=period_days,
        )
        return bool(await database.get_remnawave_uuid(telegram_id))
    except Exception as e:
        logger.error("REMNAWAVE_ADD_BYPASS_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


# ── Tariff change (Basic → Plus) ───────────────────────────────────────

async def update_tariff(telegram_id: int, new_tariff: str, period_days: int = 30) -> None:
    """Update device limit and traffic limit for tariff change."""
    if not config.REMNAWAVE_ENABLED:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        new_limit = _traffic_limit_for_tariff(new_tariff, period_days)
        new_devices = _device_limit_for_tariff(new_tariff)
        if new_limit <= 0:
            return
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            return
        api_id = _panel_api_id(user_data, rmn_uuid)
        if api_id is None:
            return
        await remnawave_api.update_user(
            api_id,
            trafficLimitBytes=new_limit,
            deviceLimit=new_devices,
        )
        logger.info("REMNAWAVE_TARIFF_UPDATED: tg=%s tariff=%s", telegram_id, new_tariff)
    except Exception as e:
        logger.error("REMNAWAVE_TARIFF_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
