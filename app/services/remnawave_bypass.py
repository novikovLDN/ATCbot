"""
High-level Remnawave operations for the BYPASS tier (Clients squad,
traffic-limited, no real expiry).

Sibling of `remnawave_premium`.  Together they cover the two entities
the Task-2 cut-over needs to provision on every paid purchase:

  premium  → squad config.REMNAWAVE_MAIN_SQUAD_UUID,    column remnawave_premium_uuid
  bypass   → squad config.REMNAWAVE_SQUAD_UUID,         column remnawave_uuid (+ caches)

This module wraps `remnawave_api.create_user` with bypass-specific
defaults and persists the panel-issued (uuid, subscriptionUrl,
shortUuid) trio into the new cache columns from migration 048 so the
purchase-success UI can render the bypass link with zero panel reads.

The existing `remnawave_service` module is left untouched for backward
compatibility — its `create_remnawave_user` / `add_traffic` / etc are
still used by background workers and admin tools that pre-date Task 2.
"""
from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import config
from app.services import remnawave_api

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────

DEFAULT_BYPASS_USERNAME_PATTERN = "{telegram_id}"
DEFAULT_DESCRIPTION_MARKER = "bypass via bot"


def build_bypass_username(telegram_id: int) -> str:
    """Build the bypass entity's username from REMNAWAVE_BYPASS_USERNAME_PATTERN.

    Capped to 32 chars (Remnawave panel limit).  Defaults to plain
    `{telegram_id}` so we don't accidentally rename the ~2500 existing
    bypass entities that pre-date Task 2.
    """
    pattern = (
        getattr(config, "REMNAWAVE_BYPASS_USERNAME_PATTERN", "")
        or DEFAULT_BYPASS_USERNAME_PATTERN
    )
    try:
        username = pattern.format(telegram_id=telegram_id)
    except (KeyError, IndexError):
        username = DEFAULT_BYPASS_USERNAME_PATTERN.format(telegram_id=telegram_id)
    return username[:32]


def _bypass_expire_iso() -> str:
    """Bypass entities are byte-limited, not time-limited.  We still need
    to send an ISO date because the panel requires `expireAt`; pick
    config.BYPASS_INFINITE_EXPIRE_ISO (default 2099-12-31T23:59:59Z)."""
    return getattr(config, "BYPASS_INFINITE_EXPIRE_ISO", "2099-12-31T23:59:59Z")


def _is_our_entity(user: dict, telegram_id: int) -> bool:
    """Heuristic: does this panel entity belong to our bot?

    Identical contract to remnawave_premium._is_our_entity — accept
    on telegramId match OR description containing one of our markers.
    """
    if not isinstance(user, dict):
        return False
    tg_field = user.get("telegramId")
    if tg_field is None:
        tg_field = user.get("telegram_id")
    try:
        if tg_field is not None and int(tg_field) == int(telegram_id):
            return True
    except (TypeError, ValueError):
        pass
    desc = (user.get("description") or "").lower()
    if "bypass" in desc or "samopis" in desc or "via bot" in desc:
        return True
    return False


@dataclass(frozen=True)
class BypassCreateResult:
    ok: bool
    panel_uuid: Optional[str]
    subscription_url: Optional[str]
    short_uuid: Optional[str]
    status: int
    error: Optional[str]
    recovered: bool = False


def _result_from_existing(user: dict, *, http_status: int) -> BypassCreateResult:
    return BypassCreateResult(
        ok=True,
        panel_uuid=user.get("uuid"),
        subscription_url=user.get("subscriptionUrl") or None,
        short_uuid=user.get("shortUuid"),
        status=http_status,
        error=None,
        recovered=True,
    )


# ── Create ────────────────────────────────────────────────────────────

async def create_bypass_user_entity(
    telegram_id: int,
    *,
    traffic_limit_bytes: int,
    description: str = DEFAULT_DESCRIPTION_MARKER,
) -> BypassCreateResult:
    """Create the bypass Remnawave entity for a user.

    Behavior mirrors remnawave_premium.create_premium_user_entity:
      1. Preflight by username — adopt an existing entity if it's ours.
      2. Refuse if the username is held by an unrelated entity.
      3. POST /api/users with the configured Clients squad + far-future
         expireAt + the requested byte cap.

    Never raises — failures come back as `ok=False, error=...`.
    """
    if not config.REMNAWAVE_ENABLED:
        return BypassCreateResult(False, None, None, None, 0, "remnawave_disabled")
    if traffic_limit_bytes <= 0:
        return BypassCreateResult(False, None, None, None, 0, "non_positive_traffic_limit")

    squad_uuid = (
        getattr(config, "REMNAWAVE_CLIENTS_SQUAD_UUID", "")
        or getattr(config, "REMNAWAVE_SQUAD_UUID", "")
        or ""
    )
    username = build_bypass_username(telegram_id)
    device_limit = getattr(config, "REMNAWAVE_BYPASS_DEVICE_LIMIT", 5)

    # Preflight — survives an interrupted previous attempt.
    try:
        existing = await remnawave_api.find_user_by_username(username)
    except Exception as e:
        logger.warning(
            "REMNAWAVE_BYPASS_PREFLIGHT_FAIL: tg=%s username=%s err=%s — proceeding to POST",
            telegram_id, username, e,
        )
        existing = None

    if existing:
        if _is_our_entity(existing, telegram_id):
            logger.info(
                "REMNAWAVE_BYPASS_RECOVERED_PREFLIGHT: tg=%s username=%s uuid=%s",
                telegram_id, username, (existing.get("uuid") or "")[:8],
            )
            return _result_from_existing(existing, http_status=200)
        logger.warning(
            "REMNAWAVE_BYPASS_USERNAME_TAKEN_UNRELATED: tg=%s username=%s existing_tg=%s",
            telegram_id, username, existing.get("telegramId"),
        )
        return BypassCreateResult(
            ok=False,
            panel_uuid=existing.get("uuid"),
            subscription_url=None,
            short_uuid=None,
            status=409,
            error="conflict_unrelated_user",
        )

    short_uuid = str(uuid_lib.uuid4())[:12]
    raw = await remnawave_api.create_user(
        username=username,
        short_uuid=short_uuid,
        traffic_limit_bytes=int(traffic_limit_bytes),
        expire_at=_bypass_expire_iso(),
        device_limit=device_limit,
        squad_uuid=squad_uuid or "",
        description=description,
        telegram_id=telegram_id,
        traffic_limit_strategy="NO_RESET",
        raw_response=True,
    )

    if raw and raw.get("ok"):
        response = raw.get("response") or {}
        return BypassCreateResult(
            ok=True,
            panel_uuid=response.get("uuid"),
            subscription_url=response.get("subscriptionUrl"),
            short_uuid=response.get("shortUuid"),
            status=int(raw.get("status") or 0),
            error=None,
        )

    # 409 from POST — race between preflight and POST.
    first_status = int((raw or {}).get("status") or 0)
    if first_status == 409:
        try:
            existing2 = await remnawave_api.find_user_by_username(username)
        except Exception:
            existing2 = None
        if existing2 and _is_our_entity(existing2, telegram_id):
            return _result_from_existing(existing2, http_status=409)

    err_body = (raw or {}).get("body")
    err_str = str(err_body)[:200] if err_body is not None else "unknown_error"
    return BypassCreateResult(
        ok=False,
        panel_uuid=None,
        subscription_url=None,
        short_uuid=None,
        status=first_status,
        error=err_str,
    )


# ── Top-up traffic on an existing entity ───────────────────────────────

async def add_bypass_traffic(telegram_id: int, extra_bytes: int) -> bool:
    """Accumulate `extra_bytes` onto the existing bypass entity's limit.

    Matches the customer's "трафик не сбрасывать" requirement: we read
    the current trafficLimitBytes and PATCH the sum, never reset.
    Returns True on success.
    """
    if not config.REMNAWAVE_ENABLED or extra_bytes <= 0:
        return False
    import database  # lazy
    cache = await database.get_remnawave_bypass_cache(telegram_id)
    rmn_uuid = cache.get("remnawave_uuid") if cache else None
    if not rmn_uuid:
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
    if not rmn_uuid:
        return False
    try:
        user = await remnawave_api.get_user(rmn_uuid)
    except Exception as e:
        logger.error("REMNAWAVE_BYPASS_TOPUP_GET_FAIL: tg=%s %s", telegram_id, e)
        return False
    if not user:
        return False
    current_limit = int(user.get("trafficLimitBytes") or 0)
    new_limit = current_limit + int(extra_bytes)
    try:
        result = await remnawave_api.update_user(
            rmn_uuid,
            trafficLimitBytes=new_limit,
            status="ACTIVE",
        )
    except Exception as e:
        logger.error("REMNAWAVE_BYPASS_TOPUP_PATCH_FAIL: tg=%s %s", telegram_id, e)
        return False
    if result is None:
        return False
    logger.info(
        "REMNAWAVE_BYPASS_TOPPED_UP: tg=%s uuid=%s +%d bytes (new=%d)",
        telegram_id, rmn_uuid[:8], extra_bytes, new_limit,
    )
    return True


# ── Delete ─────────────────────────────────────────────────────────────

async def delete_bypass_user(telegram_id: int) -> bool:
    """Delete the bypass entity, if any.  Idempotent."""
    if not config.REMNAWAVE_ENABLED:
        return False
    import database  # lazy
    rmn_uuid = await database.get_remnawave_uuid(telegram_id)
    if not rmn_uuid:
        return False
    try:
        await remnawave_api.delete_user(rmn_uuid)
    except Exception as e:
        logger.error("REMNAWAVE_BYPASS_DELETE_FAIL: tg=%s %s", telegram_id, e)
        return False
    try:
        await database.clear_remnawave_uuid(telegram_id)
    except Exception as e:
        logger.warning("REMNAWAVE_BYPASS_DELETE_DB_CLEAR_FAIL: tg=%s %s", telegram_id, e)
    return True


__all__ = [
    "BypassCreateResult",
    "build_bypass_username",
    "create_bypass_user_entity",
    "add_bypass_traffic",
    "delete_bypass_user",
]
