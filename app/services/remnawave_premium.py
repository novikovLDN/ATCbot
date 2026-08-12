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


DEFAULT_DESCRIPTION_MARKER = "Imported from samopis vpnapi"


@dataclass(frozen=True)
class PremiumCreateResult:
    """Outcome of a single create_premium_user_entity() call."""

    ok: bool
    panel_uuid: Optional[str]    # panel-internal uuid (None on failure).
                                 # Used for subsequent /api/users/{uuid} calls.
    forced_uuid_accepted: bool   # True if our `requested_uuid` ended up in
                                 # the panel's `vlessUuid` field.
    subscription_url: Optional[str]
    status: int                  # HTTP status from the panel (0 on transport error)
    error: Optional[str]
    # True when we adopted an entity that already existed in the panel from
    # a previous interrupted run (HTTP 409 or preflight hit).  Mutually
    # exclusive with `forced_uuid_accepted`.
    recovered: bool = False
    short_uuid: Optional[str] = None  # panel-assigned shortUuid (used to
                                       # rebuild subscription URLs if the
                                       # cached value goes stale).


def _is_our_entity(user: dict, telegram_id: int) -> bool:
    """Decide whether a panel entity originated from this bot's migration.

    True if either:
      - telegramId matches (Remnawave returns it as either `telegramId` or
        `telegram_id` depending on version), OR
      - description contains our import marker.
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
    if "samopis" in desc or "imported from samopis" in desc:
        return True
    return False


def _result_from_existing(user: dict, *, http_status: int) -> PremiumCreateResult:
    """Build a `recovered=True` PremiumCreateResult from an already-existing entity."""
    return PremiumCreateResult(
        ok=True,
        panel_uuid=user.get("uuid"),
        forced_uuid_accepted=False,
        subscription_url=user.get("subscriptionUrl") or None,
        status=http_status,
        error=None,
        recovered=True,
        short_uuid=user.get("shortUuid"),
    )


async def _ensure_premium_entity_state(
    panel_uuid: Optional[str],
    existing: dict,
    expire_at: datetime,
) -> bool:
    """After adopting a premium entity, PATCH expireAt + status (+ Task 6
    externalSquadUuid when configured) so the panel state reflects the
    caller's intent.  Idempotent — re-applying the same value is a no-op
    for the panel.

    Why this exists: `provision_subscription` falls into the adoption
    path either when the DB has no `remnawave_premium_uuid` (legacy /
    interrupted state) or when the renewal PATCH failed and we retried
    via create-then-adopt.  Without this helper the adopted entity keeps
    its STALE expireAt — the user pays, DB advances, but the panel
    silently retains the old expiry → the key dies on the OLD date.

    Returns True on success, False on failure.  On failure logs CRITICAL:
    the user has paid but the panel state is now out-of-sync with DB and
    requires manual repair (re-trigger sync or admin PATCH).  Never
    raises — the adoption itself still succeeds.
    """
    if not panel_uuid:
        return False
    update_fields: dict = {
        "expireAt": _iso_z(expire_at),
        "status": "ACTIVE",
    }
    target_squad = getattr(
        config, "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", None,
    ) or None
    if target_squad:
        update_fields["externalSquadUuid"] = target_squad
    try:
        result = await remnawave_api.update_user(panel_uuid, **update_fields)
        if result is not None:
            logger.info(
                "REMNAWAVE_PREMIUM_ADOPTED_PATCHED: uuid=%s expire=%s ext_squad=%s",
                panel_uuid[:8], update_fields["expireAt"],
                (target_squad or "")[:8] or "—",
            )
            return True
        logger.critical(
            "REMNAWAVE_PREMIUM_ADOPTED_PATCH_FAIL: uuid=%s expire=%s — "
            "entity adopted but expireAt NOT extended; user paid but "
            "panel state is stale, requires manual repair",
            panel_uuid[:8], update_fields["expireAt"],
        )
        return False
    except Exception as e:
        logger.critical(
            "REMNAWAVE_PREMIUM_ADOPTED_PATCH_ERROR: uuid=%s err=%s — "
            "entity adopted but expireAt NOT extended",
            panel_uuid[:8], e,
        )
        return False


async def create_premium_user_entity(
    telegram_id: int,
    *,
    requested_uuid: Optional[str],
    expire_at: datetime,
    existing_username: Optional[str] = None,
    description: str = DEFAULT_DESCRIPTION_MARKER,
) -> PremiumCreateResult:
    """Create (or recover) the premium Remnawave entity for a single user.

    Resolution order:
      0. Preflight — `find_user_by_username(username)`.  If the panel already
         has an entity with this username from a previous interrupted run
         and it looks like ours (telegramId or description match), adopt it
         and return `recovered=True`.  If it exists but is unrelated, abort
         with `error="conflict_unrelated_user"` — refuse to overwrite without
         an explicit `--force-overwrite`.
      1. POST /api/users with the forced UUID (if force_uuid is on and a
         requested_uuid was supplied).
      2. If the panel returns 409 (username conflict from a race) we run the
         preflight logic again and recover the existing entity if it is ours.
      3. If the panel returns 400/422 (forced UUID rejected) we retry the
         POST without the uuid field; the panel-assigned UUID becomes the
         persisted value.
    """
    if not config.REMNAWAVE_ENABLED:
        return PremiumCreateResult(False, None, False, None, 0, "remnawave_disabled")

    squad_uuid = getattr(config, "REMNAWAVE_MAIN_SQUAD_UUID", "") or ""
    short_uuid = str(uuid_lib.uuid4())[:12]
    username = build_premium_username(telegram_id, existing_username)
    device_limit = getattr(config, "REMNAWAVE_PREMIUM_DEVICE_LIMIT", 5)

    # Task 6: stamp every premium entity with the external squad uuid so
    # Remnawave overrides the subscription Template to "Unlimited" (RU
    # split-routing + SDK/SMTP/mining blocklists).  Skipped when unset
    # (local/dev environments without the config).
    external_squad_uuid = getattr(
        config, "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", None,
    ) or None

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
        external_squad_uuid=external_squad_uuid,
        raw_response=True,
    )

    force_uuid = bool(requested_uuid) and getattr(
        config, "REMNAWAVE_PREMIUM_FORCE_UUID", True
    )

    # ── 0) Preflight by username ──────────────────────────────────────
    try:
        existing = await remnawave_api.find_user_by_username(username)
    except Exception as e:
        logger.warning(
            "REMNAWAVE_PREMIUM_PREFLIGHT_FAIL: tg=%s username=%s err=%s — proceeding to POST",
            telegram_id, username, e,
        )
        existing = None

    if existing:
        if _is_our_entity(existing, telegram_id):
            logger.info(
                "REMNAWAVE_PREMIUM_RECOVERED_PREFLIGHT: tg=%s username=%s uuid=%s",
                telegram_id, username, (existing.get("uuid") or "")[:8],
            )
            result = _result_from_existing(existing, http_status=200)
            await _ensure_premium_entity_state(result.panel_uuid, existing, expire_at)
            return result
        logger.warning(
            "REMNAWAVE_PREMIUM_USERNAME_TAKEN_UNRELATED: tg=%s username=%s existing_tg=%s",
            telegram_id, username, existing.get("telegramId"),
        )
        return PremiumCreateResult(
            ok=False,
            panel_uuid=existing.get("uuid"),
            forced_uuid_accepted=False,
            subscription_url=None,
            status=409,
            error="conflict_unrelated_user",
            recovered=False,
        )

    # ── 1) POST with forced UUID if available ─────────────────────────
    first_uuid = requested_uuid if force_uuid else None
    raw = await remnawave_api.create_user(uuid=first_uuid, **body_kwargs)

    if raw and raw.get("ok"):
        response = raw.get("response") or {}
        panel_uuid = response.get("uuid")
        # Acceptance is decided by `vlessUuid` — `uuid` is always panel-assigned.
        accepted_vless = response.get("vlessUuid")
        accepted = bool(force_uuid) and (accepted_vless == requested_uuid)
        if external_squad_uuid:
            logger.info(
                "REMNAWAVE_PREMIUM_CREATED_WITH_EXT_SQUAD: tg=%s uuid=%s squad=%s",
                telegram_id, (panel_uuid or "")[:8], external_squad_uuid[:8],
            )
        return PremiumCreateResult(
            ok=True,
            panel_uuid=panel_uuid,
            forced_uuid_accepted=accepted,
            subscription_url=response.get("subscriptionUrl"),
            status=int(raw.get("status") or 0),
            error=None,
            recovered=False,
            short_uuid=response.get("shortUuid"),
        )

    first_status = int((raw or {}).get("status") or 0)

    # ── 2) 409 from POST — race between preflight and POST: another run
    #      may have created the entity in between.  Re-check by username
    #      and adopt if it's ours.
    if first_status == 409:
        try:
            existing = await remnawave_api.find_user_by_username(username)
        except Exception as e:
            logger.warning(
                "REMNAWAVE_PREMIUM_409_RECOVERY_FAIL: tg=%s err=%s", telegram_id, e,
            )
            existing = None
        if existing and _is_our_entity(existing, telegram_id):
            logger.info(
                "REMNAWAVE_PREMIUM_RECOVERED_POST409: tg=%s uuid=%s",
                telegram_id, (existing.get("uuid") or "")[:8],
            )
            result = _result_from_existing(existing, http_status=409)
            await _ensure_premium_entity_state(result.panel_uuid, existing, expire_at)
            return result
        # 409 not from a username race we own — fall through to the
        # forced-UUID retry below (might be uuid conflict).

    # ── 3) Forced-UUID rejection — retry without forced uuid.  We do NOT
    #      retry on 409 unless the username turns out unrelated (handled
    #      above); only 400/422 mean "uuid value not accepted".
    retryable = force_uuid and first_status in (400, 422)
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
                recovered=False,
                short_uuid=response.get("shortUuid"),
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
        recovered=False,
    )


# ── Lifecycle (called by handlers AFTER cutover — wired up in a follow-up) ─

async def renew_premium_user(telegram_id: int, new_expire_at: datetime) -> bool:
    """Patch expireAt on the premium entity. Returns True on success.

    Retries the PATCH up to 3 times with 1s/2s backoff so a transient
    panel hiccup (500, timeout, brief 4xx) doesn't drop the user into
    the create-fallback path with a stale expireAt.

    Task 6: when `REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID` is configured the
    same PATCH also stamps `externalSquadUuid` on the entity.  Idempotent
    (same value re-applied is a no-op) — this doubles as the safety net
    that retroactively repairs premium entities created before the Task 6
    rollout or where the create-time stamp was lost.
    """
    if not config.REMNAWAVE_ENABLED:
        return False
    import database  # lazy — keeps unit tests asyncpg-free
    try:
        rmn_uuid = await database.get_remnawave_premium_uuid(telegram_id)
        if not _is_valid_full_uuid(rmn_uuid):
            return False
        update_fields = {
            "expireAt": _iso_z(new_expire_at),
            "status": "ACTIVE",
        }
        external_squad_uuid = getattr(
            config, "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", None,
        ) or None
        if external_squad_uuid:
            update_fields["externalSquadUuid"] = external_squad_uuid

        MAX_ATTEMPTS = 3
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = await remnawave_api.update_user(rmn_uuid, **update_fields)
            except Exception as e:
                logger.warning(
                    "REMNAWAVE_PREMIUM_RENEW_EXCEPTION: tg=%s uuid=%s attempt=%d/%d %s: %s",
                    telegram_id, rmn_uuid[:8], attempt, MAX_ATTEMPTS,
                    type(e).__name__, e,
                )
                result = None
            if result is not None:
                logger.info(
                    "REMNAWAVE_PREMIUM_RENEWED: tg=%s uuid=%s new_expire=%s ext_squad=%s attempt=%d",
                    telegram_id, rmn_uuid[:8], _iso_z(new_expire_at),
                    (external_squad_uuid or "")[:8] or "—", attempt,
                )
                return True
            logger.warning(
                "REMNAWAVE_PREMIUM_RENEW_FAILED_ATTEMPT: tg=%s uuid=%s attempt=%d/%d",
                telegram_id, rmn_uuid[:8], attempt, MAX_ATTEMPTS,
            )
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(float(attempt))  # 1s, then 2s
        logger.critical(
            "REMNAWAVE_PREMIUM_RENEW_EXHAUSTED: tg=%s uuid=%s new_expire=%s — "
            "%d attempts failed; falling back to adopt-and-patch via "
            "create_premium_user_entity",
            telegram_id, rmn_uuid[:8], _iso_z(new_expire_at), MAX_ATTEMPTS,
        )
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


async def reissue_premium_user_entity(
    telegram_id: int,
    *,
    requested_uuid: str,
    expire_at: datetime,
    existing_username: Optional[str] = None,
    description: str = "Premium reissued via bot",
) -> PremiumCreateResult:
    """True key reissue: delete the user's current premium entity and
    create a fresh one.

    The old connection UUID / subscription URL stop working and a brand-new
    entity (new shortUuid → new subscriptionUrl) is issued — the Remnawave
    equivalent of the legacy "add new vless user + remove old uuid" flow.

    Because Remnawave keys entities by a deterministic username
    (`tg_{id}_premium`), the old entity MUST be deleted before the new one
    can be created.  If `create_premium_user_entity` ends up *adopting* an
    existing entity (recovered=True) it means the delete did not take
    effect — that is reported as a failure (`error="reissue_no_rotation"`)
    so the caller rolls back and the admin can retry.
    """
    if not config.REMNAWAVE_ENABLED:
        return PremiumCreateResult(False, None, False, None, 0, "remnawave_disabled")

    import database  # lazy — keeps unit tests asyncpg-free

    old_panel_uuid = await database.get_remnawave_premium_uuid(telegram_id)
    if _is_valid_full_uuid(old_panel_uuid):
        try:
            deleted = await remnawave_api.delete_user(old_panel_uuid)
            logger.info(
                "REMNAWAVE_PREMIUM_REISSUE_DELETE: tg=%s old_uuid=%s result=%s",
                telegram_id, old_panel_uuid[:8],
                "ok" if deleted is not None else "not_found_or_failed",
            )
        except Exception as e:
            logger.warning(
                "REMNAWAVE_PREMIUM_REISSUE_DELETE_ERROR: tg=%s uuid=%s %s",
                telegram_id, old_panel_uuid[:8], e,
            )

    result = await create_premium_user_entity(
        telegram_id,
        requested_uuid=requested_uuid,
        expire_at=expire_at,
        existing_username=existing_username,
        description=description,
    )
    if result.ok and result.recovered:
        logger.error(
            "REMNAWAVE_PREMIUM_REISSUE_NO_ROTATION: tg=%s adopted uuid=%s — delete did not take effect",
            telegram_id, (result.panel_uuid or "")[:8],
        )
        return PremiumCreateResult(
            ok=False,
            panel_uuid=result.panel_uuid,
            forced_uuid_accepted=False,
            subscription_url=None,
            status=result.status,
            error="reissue_no_rotation",
            recovered=False,
        )
    return result


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
    "reissue_premium_user_entity",
    "get_premium_subscription_url",
]
