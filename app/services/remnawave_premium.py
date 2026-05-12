"""
High-level Remnawave operations for the PREMIUM tier (MainServer squad,
unlimited traffic).

This module is a sibling of `remnawave_service` (bypass tier).  It keeps the
two entities cleanly separated:

  bypass   → squad config.REMNAWAVE_SQUAD_UUID,        column remnawave_uuid
  premium  → squad config.REMNAWAVE_MAIN_SQUAD_UUID,   column remnawave_premium_uuid

Most callers should NOT use this module directly during the migration — the
one-shot script in scripts/migrate_samopis_to_remnawave.py is the entry point.
Once the bot is cut over to issuing premium subscriptions through Remnawave
the handlers will call create_premium_user() / renew_premium_user() at
purchase time (follow-up PR).

All public coroutines log on failure and never raise to callers — the
subscription flow must never crash because Remnawave is unhappy.
"""
import asyncio
import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import config
from app.services import remnawave_api

logger = logging.getLogger(__name__)

# Default username pattern.  Used by both the migration script and the
# go-forward purchase flow (when wired up).
DEFAULT_PREMIUM_USERNAME_PATTERN = "tg_{telegram_id}_premium"


def _is_valid_full_uuid(s: Optional[str]) -> bool:
    if not s:
        return False
    try:
        uuid_lib.UUID(s, version=4)
        return True
    except (ValueError, AttributeError):
        return len(s) == 36 and s.count("-") == 4


def _iso_z(dt: datetime) -> str:
    """Return a Remnawave-compatible ISO-8601 timestamp (UTC, Z suffix)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_premium_username(telegram_id: int, existing_username: Optional[str] = None) -> str:
    """Build the username for the premium Remnawave entity.

    Defaults to `tg_{telegram_id}_premium`.  If REMNAWAVE_PREMIUM_USERNAME_PATTERN
    is set, that pattern is used (Python str.format with `telegram_id` and
    `existing_username`).  Usernames are clamped to 32 chars (Remnawave limit).
    """
    pattern = (
        getattr(config, "REMNAWAVE_PREMIUM_USERNAME_PATTERN", "") or DEFAULT_PREMIUM_USERNAME_PATTERN
    )
    try:
        username = pattern.format(
            telegram_id=telegram_id,
            existing_username=existing_username or str(telegram_id),
        )
    except (KeyError, IndexError):
        username = DEFAULT_PREMIUM_USERNAME_PATTERN.format(telegram_id=telegram_id)
    # Remnawave username is typically capped at 32 chars
    return username[:32]


@dataclass(frozen=True)
class PremiumCreateResult:
    """Outcome of a single create_premium_user_entity() call."""

    ok: bool
    panel_uuid: Optional[str]    # uuid the panel actually assigned (None on failure)
    forced_uuid_accepted: bool   # True if our `requested_uuid` was honoured
    subscription_url: Optional[str]
    status: int                  # HTTP status from the panel (0 on transport error)
    error: Optional[str]


async def create_premium_user_entity(
    telegram_id: int,
    *,
    requested_uuid: Optional[str],
    expire_at: datetime,
    existing_username: Optional[str] = None,
    description: str = "Imported from samopis vpnapi",
) -> PremiumCreateResult:
    """Create the premium Remnawave entity for a single user.

    Tries to POST with the requested UUID.  If the panel rejects it
    (HTTP 400/409/422), retries WITHOUT the UUID so the migration can
    proceed regardless of how strict the panel is.  In both cases the
    panel-assigned UUID is what the script should persist.
    """
    if not config.REMNAWAVE_ENABLED:
        return PremiumCreateResult(False, None, False, None, 0, "remnawave_disabled")

    squad_uuid = getattr(config, "REMNAWAVE_MAIN_SQUAD_UUID", "") or ""
    short_uuid = str(uuid_lib.uuid4())[:12]
    username = build_premium_username(telegram_id, existing_username)
    device_limit = getattr(config, "REMNAWAVE_PREMIUM_DEVICE_LIMIT", 5)

    body_kwargs = dict(
        username=username,
        short_uuid=short_uuid,
        traffic_limit_bytes=0,                          # unlimited (per ТЗ)
        expire_at=_iso_z(expire_at),
        device_limit=device_limit,
        squad_uuid=squad_uuid or "",
        description=description,
        telegram_id=telegram_id,
        traffic_limit_strategy="NO_RESET",
        raw_response=True,
    )

    force_uuid = bool(requested_uuid) and getattr(
        config, "REMNAWAVE_PREMIUM_FORCE_UUID", True
    )

    # First attempt — with forced uuid if requested
    first_uuid = requested_uuid if force_uuid else None
    raw = await remnawave_api.create_user(uuid=first_uuid, **body_kwargs)

    if raw and raw.get("ok"):
        response = raw.get("response") or {}
        panel_uuid = response.get("uuid")
        sub_url = response.get("subscriptionUrl")
        accepted = bool(force_uuid) and (panel_uuid == requested_uuid)
        return PremiumCreateResult(
            ok=True,
            panel_uuid=panel_uuid,
            forced_uuid_accepted=accepted,
            subscription_url=sub_url,
            status=int(raw.get("status") or 0),
            error=None,
        )

    # Retry without forced UUID only if forcing was attempted and the panel
    # complained — otherwise the failure is unrelated to the UUID.
    first_status = int((raw or {}).get("status") or 0)
    retryable = force_uuid and first_status in (400, 409, 422)
    if retryable:
        logger.warning(
            "REMNAWAVE_PREMIUM_FORCED_UUID_REJECTED: tg=%s requested=%s status=%s — retrying without uuid",
            telegram_id, (requested_uuid or "")[:8], first_status,
        )
        raw2 = await remnawave_api.create_user(uuid=None, **body_kwargs)
        if raw2 and raw2.get("ok"):
            response = raw2.get("response") or {}
            return PremiumCreateResult(
                ok=True,
                panel_uuid=response.get("uuid"),
                forced_uuid_accepted=False,
                subscription_url=response.get("subscriptionUrl"),
                status=int(raw2.get("status") or 0),
                error=None,
            )
        raw = raw2

    err_body = (raw or {}).get("body")
    err_str = str(err_body)[:200] if err_body is not None else "unknown_error"
    return PremiumCreateResult(
        ok=False,
        panel_uuid=None,
        forced_uuid_accepted=False,
        subscription_url=None,
        status=int((raw or {}).get("status") or 0),
        error=err_str,
    )


# ── Lifecycle (called by handlers AFTER cutover — wired up in a follow-up) ─

async def renew_premium_user(telegram_id: int, new_expire_at: datetime) -> bool:
    """Patch expireAt on the premium entity. Returns True on success."""
    if not config.REMNAWAVE_ENABLED:
        return False
    import database  # lazy — keeps unit tests asyncpg-free
    try:
        rmn_uuid = await database.get_remnawave_premium_uuid(telegram_id)
        if not _is_valid_full_uuid(rmn_uuid):
            return False
        result = await remnawave_api.update_user(
            rmn_uuid,
            expireAt=_iso_z(new_expire_at),
            status="ACTIVE",
        )
        if result is not None:
            logger.info(
                "REMNAWAVE_PREMIUM_RENEWED: tg=%s uuid=%s new_expire=%s",
                telegram_id, rmn_uuid[:8], _iso_z(new_expire_at),
            )
            return True
        return False
    except Exception as e:
        logger.error("REMNAWAVE_PREMIUM_RENEW_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


async def disable_premium_user(telegram_id: int) -> bool:
    """Disable (status=DISABLED) the premium entity when the subscription
    fully expires. The bypass entity is handled separately by remnawave_service.
    """
    if not config.REMNAWAVE_ENABLED:
        return False
    import database  # lazy — keeps unit tests asyncpg-free
    try:
        rmn_uuid = await database.get_remnawave_premium_uuid(telegram_id)
        if not _is_valid_full_uuid(rmn_uuid):
            return False
        result = await remnawave_api.update_user(rmn_uuid, status="DISABLED")
        if result is not None:
            logger.info("REMNAWAVE_PREMIUM_DISABLED: tg=%s uuid=%s", telegram_id, rmn_uuid[:8])
            return True
        return False
    except Exception as e:
        logger.error("REMNAWAVE_PREMIUM_DISABLE_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


async def get_premium_subscription_url(telegram_id: int) -> Optional[str]:
    """Return the panel-issued subscription URL for the premium entity, or None."""
    if not config.REMNAWAVE_ENABLED:
        return None
    import database  # lazy
    rmn_uuid = await database.get_remnawave_premium_uuid(telegram_id)
    if not _is_valid_full_uuid(rmn_uuid):
        return None
    try:
        user = await remnawave_api.get_user(rmn_uuid)
        if not user:
            return None
        return user.get("subscriptionUrl") or None
    except Exception as e:
        logger.error("REMNAWAVE_PREMIUM_GETURL_ERROR: tg=%s %s", telegram_id, e)
        return None


__all__ = [
    "PremiumCreateResult",
    "build_premium_username",
    "create_premium_user_entity",
    "renew_premium_user",
    "disable_premium_user",
    "get_premium_subscription_url",
]
