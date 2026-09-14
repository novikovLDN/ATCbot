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
from app.services.tariffs import PANEL_TAG_BYPASS

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
) -> bool:
    """Create a Remnawave user for the given subscriber.

    Returns True when the bypass entity was created or adopted+updated
    (callers used to ignore the result; renew_remnawave_user alerts on False).

    Args:
        traffic_limit_override: if set, use this instead of tariff-based limit.
            Used for auto-provisioning existing users with a smaller starter pack.
        period_days: subscription period for traffic calculation.
    """
    if not config.REMNAWAVE_ENABLED:
        return
    if tariff == "trial" and not traffic_limit_override:
        return False  # Trial without explicit override gets no bypass

    traffic_limit = traffic_limit_override or _traffic_limit_for_tariff(tariff, period_days)
    if traffic_limit <= 0:
        return False

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
                if existing.get("tag") != PANEL_TAG_BYPASS:
                    update_fields["tag"] = PANEL_TAG_BYPASS
                adopted = await remnawave_api.update_user(
                    int(existing_id) if existing_id is not None else existing_uuid,
                    **update_fields,
                )
                await database.reset_traffic_notification_flags(telegram_id)
                return adopted is not None

        result = await remnawave_api.create_user(
            username=str(telegram_id),
            short_uuid=short_uuid,
            traffic_limit_bytes=traffic_limit,
            expire_at=expire_str,
            device_limit=_device_limit_for_tariff(tariff),
            telegram_id=telegram_id,
            tag=PANEL_TAG_BYPASS,
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
            return True
        logger.warning("REMNAWAVE_USER_CREATE_FAILED: tg=%s", telegram_id)
        return False
    except Exception as e:
        logger.error("REMNAWAVE_CREATE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


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
            if not await create_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days):
                await _alert_bypass_not_delivered(telegram_id, traffic_add, tariff, "bypass create failed")
            return

        # Get current limit and add tariff traffic
        user_data = await _get_user_with_recovery(telegram_id, rmn_uuid)
        if not user_data:
            # User might have been deleted from Remnawave — recreate
            if not await create_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days):
                await _alert_bypass_not_delivered(telegram_id, traffic_add, tariff,
                                                  "bypass entity not readable and re-create failed")
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

        tag_field = {} if user_data.get("tag") == PANEL_TAG_BYPASS else {"tag": PANEL_TAG_BYPASS}
        patched = await remnawave_api.update_user(
            api_target,
            trafficLimitBytes=new_limit,
            expireAt=expire_str,
            # 3.x: hwidDeviceLimit (update-user.command.ts:53); deviceLimit
            # в контракте нет и вырезался валидатором панели.
            hwidDeviceLimit=_device_limit_for_tariff(tariff),
            **tag_field,
        )
        if patched is None:
            # M-RENEW-GB-SILENT (docs/audit/03_payment_matrix.md): update_user
            # returns None on failure; before, the result was ignored and the
            # renewal was logged as REMNAWAVE_RENEWED with the GB never added.
            logger.error(
                "REMNAWAVE_RENEW_NOT_APPLIED: tg=%s target=%s +%d bytes",
                telegram_id, str(api_target)[:16], traffic_add,
            )
            await _alert_bypass_not_delivered(telegram_id, traffic_add, tariff, "bypass PATCH not applied")
            return
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
        await _alert_bypass_not_delivered(telegram_id, traffic_add, tariff, f"{type(e).__name__}: {e}")


async def _alert_bypass_not_delivered(telegram_id: int, add_bytes: int, tariff: str, reason: str) -> None:
    """Legacy bypass top-up (renew_remnawave_user) did not land: payment_errors row
    + admin alert (force within the purchase_flow budget). Never raises, no secrets."""
    try:
        await database.log_payment_error(
            stage="bypass_topup",
            telegram_id=telegram_id,
            error_code="bypass_not_delivered",
            error_message=str(reason)[:500],
            raw_payload={"add_bytes": int(add_bytes), "tariff": tariff},
        )
    except Exception as e:
        logger.warning("BYPASS_NOT_DELIVERED_LOG_FAILED: tg=%s %s", telegram_id, e)
    try:
        from app.services import admin_alerts, purchase_flow
        bot = purchase_flow._alert_bot()
        if bot is None:
            logger.error("BYPASS_NOT_DELIVERED_ALERT_NO_BOT: tg=%s", telegram_id)
            return
        sent = await admin_alerts.send_alert(
            bot, "payment",
            "\n".join([
                "Bypass GB NOT delivered (legacy renewal / grant top-up)",
                f"user: tg:{telegram_id}",
                f"GB to add: +{add_bytes / 1024 ** 3:g} ({tariff})",
                f"error: {str(reason)[:300]}",
                "Action: add the GB manually (dashboard → Traffic Audit / user card).",
            ]),
            force=purchase_flow._take_forced_alert_slot(),
        )
        if sent:
            # P2-25: the delayed legacy check must not alert this bypass problem again.
            from app.services.payments import verify_delivery
            verify_delivery.note_alerted(telegram_id, "bypass")
    except Exception as e:
        logger.warning("BYPASS_NOT_DELIVERED_ALERT_FAILED: tg=%s %s", telegram_id, e)


def renew_remnawave_user_bg(telegram_id: int, tariff: str, subscription_end: datetime, period_days: int = 30) -> None:
    _fire_and_forget(renew_remnawave_user(telegram_id, tariff, subscription_end, period_days=period_days))


# ── Disable (subscription expired) ─────────────────────────────────────

def _parse_expire_at(value) -> Optional[datetime]:
    """Panel expireAt (ISO, 'Z' suffix) → aware UTC datetime; None if absent/bad."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# An ACTIVE bypass entity whose expireAt is further away than this needs no PATCH.
_BYPASS_EXTEND_IF_WITHIN = timedelta(days=365)


async def extend_remnawave_for_bypass(telegram_id: int) -> None:
    """Extend Remnawave expiry to far future for bypass-only mode.

    When main subscription expires but user has bypass traffic,
    Remnawave user must stay ACTIVE with a far-future expireAt.
    Otherwise Remnawave marks user as expired and bypass stops working.

    Called on every open of the setup screen and on trial / expiry
    transitions: PATCH only when needed (not ACTIVE, or expireAt within
    _BYPASS_EXTEND_IF_WITHIN / unknown). An ACTIVE entity with a far-future
    expireAt is left alone.
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

        expire_at = _parse_expire_at(user_data.get("expireAt"))
        if (
            user_data.get("status") == "ACTIVE"
            and expire_at is not None
            and expire_at > datetime.now(timezone.utc) + _BYPASS_EXTEND_IF_WITHIN
        ):
            logger.debug(
                "REMNAWAVE_BYPASS_EXTEND_SKIPPED: tg=%s — ACTIVE, expireAt %s",
                telegram_id, expire_at.date().isoformat(),
            )
            return

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
        # 3.4.3: used bytes live in userTraffic.usedTrafficBytes
        # (models/extended-users.schema.ts); there is no top-level field, so
        # this read 0 and a bypass with its GB used up was never disabled.
        user_traffic = user_data.get("userTraffic") or {}
        traffic_used = int(
            user_traffic.get("usedTrafficBytes", user_data.get("usedTrafficBytes", 0)) or 0
        )
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

    ⚠️ УСТАРЕВШИЙ helper — использует get_bypass_entity_safe для резолва
    правильной bypass entity (не premium). Ранее слепо использовал
    remnawave_id из БД, который у скорапченных юзеров указывал на premium
    → SAFETY-DROP → трафик молча терялся.

    Для новых мест предпочтительнее использовать
    remnawave_bypass.add_bypass_traffic (та же логика через тот же helper).
    """
    if not config.REMNAWAVE_ENABLED:
        logger.warning("REMNAWAVE_ADD_TRAFFIC_DISABLED: tg=%s", telegram_id)
        return False
    try:
        # Резолвим гарантированно bypass entity через self-heal helper.
        # Он: проверяет username, чистит кривой remnawave_id (premium),
        # резолвит через username=str(tg), backfillит правильный id.
        entity = await remnawave_api.get_bypass_entity_safe(telegram_id)
        if not isinstance(entity, dict):
            logger.info(
                "REMNAWAVE_ADD_TRAFFIC_NO_ENTITY: tg=%s (bypass entity не найден в панели, "
                "нужна recovery через add_bypass_traffic)",
                telegram_id,
            )
            return False

        api_target: Any = entity.get("id")
        if api_target is None:
            api_target = entity.get("uuid") or entity.get("vlessUuid")
        if api_target is None:
            logger.warning(
                "REMNAWAVE_ADD_TRAFFIC_NO_TARGET: tg=%s username=%r — resolved bypass без id/uuid",
                telegram_id, entity.get("username"),
            )
            return False

        current_limit = int(entity.get("trafficLimitBytes", 0) or 0)
        # Юзер оплатил пакет → просто добавляем ровно extra_bytes.
        # current=0 означает "нет доступного трафика" (израсходовал или
        # ещё не было выдано) — не безлимит. Складываем без условий.
        new_limit = current_limit + int(extra_bytes)

        tag_field = {} if entity.get("tag") == PANEL_TAG_BYPASS else {"tag": PANEL_TAG_BYPASS}
        result = await remnawave_api.update_user(api_target, trafficLimitBytes=new_limit, **tag_field)
        if result is not None:
            # Re-enable if disabled
            if entity.get("status") != "ACTIVE":
                await remnawave_api.update_user(api_target, status="ACTIVE")
            await database.reset_traffic_notification_flags(telegram_id)
            logger.info(
                "REMNAWAVE_TRAFFIC_ADDED: tg=%s target=%s username=%r +%d bytes, current=%d → new=%d",
                telegram_id, str(api_target)[:16], entity.get("username"),
                extra_bytes, current_limit, new_limit,
            )
            return True
        logger.warning(
            "REMNAWAVE_ADD_TRAFFIC_PATCH_FAILED: tg=%s target=%s username=%r "
            "(update_user returned None — вышестоящий флоу пусть fallback'нет)",
            telegram_id, str(api_target)[:16], entity.get("username"),
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

