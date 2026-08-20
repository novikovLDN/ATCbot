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
from typing import Any, Optional

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

        # PREFLIGHT (3.x): существующий bypass entity с username=str(tg_id)
        # уже мог быть создан ранее (2.7.4 legacy). POST /api/users вернёт
        # 400 A019 "username already exists" без preflight. Найдём entity
        # через resolve, адоптим (кешируем uuid + id + telegramId), затем
        # PATCH trafficLimitBytes + expireAt в актуальные значения.
        existing = await remnawave_api.find_user_by_username(str(telegram_id))
        if existing and isinstance(existing, dict):
            existing_id = existing.get("id")
            # 3.x response не отдаёт `uuid` — только `vlessUuid`.
            # Fallback гарантирует, что колонка `remnawave_uuid`
            # не останется NULL после adopt.
            existing_uuid = existing.get("uuid") or existing.get("vlessUuid")
            if existing_id is not None or existing_uuid:
                if existing_uuid:
                    await database.set_remnawave_uuid(telegram_id, str(existing_uuid))
                if existing_id is not None:
                    try:
                        await database.set_remnawave_id(telegram_id, int(existing_id))
                    except (TypeError, ValueError):
                        pass
                logger.info(
                    "REMNAWAVE_USER_ADOPTED: tg=%s uuid=%s id=%s (bypass legacy)",
                    telegram_id, str(existing_uuid or "")[:8], existing_id,
                )
                # Обновляем expiry + status. trafficLimitBytes — ТОЛЬКО
                # если у существующей entity лимит меньше нужного (never-
                # -decrease, чтобы не обнулить накопленные покупки/докупки
                # трафика при повторном adopt из profile.show fallback).
                update_fields = {
                    "expireAt": expire_str,
                    "status": "ACTIVE",
                }
                try:
                    existing_limit = int(existing.get("trafficLimitBytes") or 0)
                except (TypeError, ValueError):
                    existing_limit = 0
                # Если у entity безлимит (0) — оставить безлимитом.
                # Иначе — set только если новый лимит строго больше.
                if existing_limit != 0 and int(traffic_limit) > existing_limit:
                    update_fields["trafficLimitBytes"] = int(traffic_limit)
                if not existing.get("telegramId"):
                    update_fields["telegramId"] = int(telegram_id)
                await remnawave_api.update_user(
                    int(existing_id) if existing_id is not None else existing_uuid,
                    **update_fields,
                )
                await database.reset_traffic_notification_flags(telegram_id)
                return

        result = await remnawave_api.create_user(
            username=str(telegram_id),
            short_uuid=short_uuid,
            traffic_limit_bytes=traffic_limit,
            expire_at=expire_str,
            device_limit=_device_limit_for_tariff(tariff),
            telegram_id=telegram_id,
        )
        if result:
            # 3.x: response не отдаёт `uuid` — только `vlessUuid` + `id`.
            # Fallback на vlessUuid → short_uuid, чтобы колонка не осталась
            # пустой (иначе profile.show_traffic=False и т.д.).
            rmn_uuid = result.get("uuid") or result.get("vlessUuid") or short_uuid
            await database.set_remnawave_uuid(telegram_id, rmn_uuid)
            # 3.x: сохранить numeric id (панель отдаёт его в response.id).
            rmn_id = result.get("id")
            if rmn_id is not None:
                try:
                    await database.set_remnawave_id(telegram_id, int(rmn_id))
                except (TypeError, ValueError):
                    pass
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


def create_remnawave_user_bg(telegram_id: int, tariff: str, subscription_end: datetime, period_days: int = 30) -> None:
    _fire_and_forget(create_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days))


async def ensure_squad(telegram_id: int) -> None:
    """Ensure existing Remnawave user is assigned to the configured squad.
    Checks first via GET — skips if already assigned."""
    if not config.REMNAWAVE_ENABLED or not config.REMNAWAVE_SQUAD_UUID:
        return
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return
        # Quick check — if squad already assigned, skip
        user_data = await remnawave_api.get_user(rmn_uuid)
        if user_data:
            squads = user_data.get("activeInternalSquads") or []
            if squads:
                return  # Already has squad
            # No squad — assign
            await remnawave_api.assign_user_to_squad(rmn_uuid, config.REMNAWAVE_SQUAD_UUID)
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

        # Резолвим цель через numeric bypass id из БД — не через
        # user_data.get("uuid"), чтобы гарантированно попасть в bypass
        # entity, а не в premium (safety-drop иначе тихо теряет PATCH).
        bypass_id = await database.get_remnawave_id(telegram_id)
        api_target: Any = bypass_id if bypass_id is not None else (
            user_data.get("uuid") or rmn_uuid
        )
        current_limit = user_data.get("trafficLimitBytes", 0)
        new_limit = current_limit + traffic_add
        # Bypass works by traffic (GB), not by date — keep expireAt far future
        # so Remnawave is never marked expired while GB remain.
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        expire_str = far_future.strftime("%Y-%m-%dT%H:%M:%SZ")

        await remnawave_api.update_user(
            api_target,
            trafficLimitBytes=new_limit,
            expireAt=expire_str,
            # 3.x: hwidDeviceLimit — новое имя. Шлём оба для совместимости.
            hwidDeviceLimit=_device_limit_for_tariff(tariff),
            deviceLimit=_device_limit_for_tariff(tariff),
        )
        # Re-enable if disabled
        if user_data.get("status") != "ACTIVE":
            await remnawave_api.update_user(api_target, status="ACTIVE")
        # Ensure squad assigned (skip if already has one)
        if config.REMNAWAVE_SQUAD_UUID:
            squads = user_data.get("activeInternalSquads") or []
            if not squads:
                await remnawave_api.assign_user_to_squad(api_target, config.REMNAWAVE_SQUAD_UUID)
        await database.reset_traffic_notification_flags(telegram_id)
        logger.info(
            "REMNAWAVE_RENEWED: tg=%s target=%s old_limit=%d new_limit=%d",
            telegram_id, str(api_target)[:16], current_limit, new_limit,
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
        api_uuid = user_data.get("uuid") or rmn_uuid

        from datetime import timedelta
        far_future = (datetime.now(timezone.utc) + timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await remnawave_api.update_user(api_uuid, expireAt=far_future, status="ACTIVE")
        logger.info("REMNAWAVE_BYPASS_EXTENDED: tg=%s uuid=%s — expiry set to +10 years", telegram_id, api_uuid[:8])
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
        api_uuid = user_data.get("uuid") or rmn_uuid

        # Check if user still has bypass traffic — don't disable if GB remaining
        traffic_limit = user_data.get("trafficLimitBytes", 0)
        traffic_used = user_data.get("usedTrafficBytes", 0)
        if traffic_limit > 0 and traffic_used < traffic_limit:
            # User still has bypass GB — extend instead of disable
            far_future = (datetime.now(timezone.utc) + timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
            await remnawave_api.update_user(api_uuid, expireAt=far_future, status="ACTIVE")
            logger.info("REMNAWAVE_KEPT_ACTIVE: tg=%s uuid=%s — bypass traffic remaining (%d/%d bytes)",
                        telegram_id, api_uuid[:8], traffic_used, traffic_limit)
            return

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
    """Add purchased traffic to current limit. Returns True on success.

    Safe против overwrite unlimited: если у entity уже trafficLimitBytes=0
    (безлимит), сохраняем 0 — иначе +extra превратил бы безлимит в лимит.
    """
    if not config.REMNAWAVE_ENABLED:
        logger.warning("REMNAWAVE_ADD_TRAFFIC_DISABLED: tg=%s", telegram_id)
        return False
    try:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            logger.info("REMNAWAVE_ADD_TRAFFIC_NO_UUID: tg=%s (need recovery)", telegram_id)
            return False

        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            logger.warning(
                "REMNAWAVE_ADD_TRAFFIC_NO_USER: tg=%s uuid=%s (get_user returned None)",
                telegram_id, str(rmn_uuid)[:8],
            )
            return False

        # Резолвим цель через numeric bypass id из БД (миграция 078).
        # НЕ через user_data.get("uuid") — тот резолв через telegram_id
        # stream возвращал ПЕРВУЮ entity (часто premium) → safety-drop
        # → трафик не добавлялся, а логировался как SUCCESS.
        bypass_id = await database.get_remnawave_id(telegram_id)
        api_target: Any = bypass_id if bypass_id is not None else (
            user_data.get("uuid") or rmn_uuid
        )
        current_limit = int(user_data.get("trafficLimitBytes", 0) or 0)
        # Юзер оплатил пакет → просто добавляем ровно extra_bytes.
        # current=0 означает "нет доступного трафика" (израсходовал или
        # ещё не было выдано) — не безлимит. Складываем без условий.
        new_limit = current_limit + int(extra_bytes)

        result = await remnawave_api.update_user(api_target, trafficLimitBytes=new_limit)
        if result is not None:
            # Re-enable if disabled
            if user_data.get("status") != "ACTIVE":
                await remnawave_api.update_user(api_target, status="ACTIVE")
            await database.reset_traffic_notification_flags(telegram_id)
            logger.info(
                "REMNAWAVE_TRAFFIC_ADDED: tg=%s +%d bytes, current=%d → new=%d",
                telegram_id, extra_bytes, current_limit, new_limit,
            )
            return True
        logger.warning(
            "REMNAWAVE_ADD_TRAFFIC_PATCH_FAILED: tg=%s target=%s (update_user returned None — "
            "возможно safety-drop на premium; вышестоящий флоу пусть fallback'нет)",
            telegram_id, str(api_target)[:16],
        )
        return False
    except Exception as e:
        logger.exception("REMNAWAVE_ADD_TRAFFIC_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
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

        # DB has no UUID but the panel may still hold an entity created earlier
        # with username=str(telegram_id). Trying create_user directly would fail
        # with A019 "User username already exists". Recover the existing entity
        # by username first, cache its UUID, and top-up its trafficLimitBytes.
        existing = await remnawave_api.find_user_by_username(str(telegram_id))
        if existing:
            # 3.x response не отдаёт `uuid` — только `vlessUuid`. Берём его
            # как idempotent connection UUID, чтобы колонка не осталась пустой.
            api_uuid = existing.get("uuid") or existing.get("vlessUuid")
            if api_uuid:
                await database.set_remnawave_uuid(telegram_id, str(api_uuid))
                # 3.x: сразу закешируем numeric id, чтобы add_traffic /
                # update_user работали без повторного stream-резолва.
                api_id = existing.get("id")
                if api_id is not None:
                    try:
                        await database.set_remnawave_id(telegram_id, int(api_id))
                    except (TypeError, ValueError):
                        pass
                logger.info(
                    "REMNAWAVE_BYPASS_RECOVERED: tg=%s uuid=%s id=%s (was orphaned in panel)",
                    telegram_id, str(api_uuid)[:8], api_id,
                )
                if await add_traffic(telegram_id, extra_bytes):
                    return True
                # add_traffic failed for a reason other than missing UUID —
                # fall through to create_remnawave_user, which will surface
                # the real error in logs.
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
        api_uuid = user_data.get("uuid") or rmn_uuid
        await remnawave_api.update_user(
            api_uuid,
            trafficLimitBytes=new_limit,
            hwidDeviceLimit=new_devices,
            deviceLimit=new_devices,
        )
        logger.info("REMNAWAVE_TARIFF_UPDATED: tg=%s tariff=%s", telegram_id, new_tariff)
    except Exception as e:
        logger.error("REMNAWAVE_TARIFF_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
