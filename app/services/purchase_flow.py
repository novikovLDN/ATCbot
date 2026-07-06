"""
Purchase-time provisioning of Remnawave entities.

Replaces the legacy `vpn_utils.add_vless_user` call at purchase /
trial / renewal time when `config.PURCHASE_FLOW_REMNAWAVE` is on.
Creates / adopts BOTH entities the customer wants in the new world:

  premium  — squad MainServer, expireAt = subscription_end, unlimited bytes
  bypass   — squad Clients,    far-future expireAt,         byte-limited

Returns a dict shaped EXACTLY like the legacy `add_vless_user` so the
existing grant_access / finalize_purchase code consumes it unchanged:

    {
        "uuid":              <samopis-style UUID, also embedded in VLESS link>,
        "vless_url":         <premium subscription URL>,
        "vless_url_plus":    <bypass subscription URL or None>,
        "subscription_type": <tariff string, e.g. "basic"/"plus"/"trial">,
    }

`vpn_key` column gets the premium URL, `vpn_key_plus` column gets the
bypass URL — so the rest of the bot continues to ship two links to
Plus / Basic / Trial buyers without code changes elsewhere.

This module never calls vpnapi master.  When `PURCHASE_FLOW_REMNAWAVE`
is OFF the legacy `vpn_utils.add_vless_user` is used instead by the
caller (see database/subscriptions.py:grant_access).
"""
from __future__ import annotations

import asyncio
import logging
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Optional

import config
from app.services import remnawave_bypass, remnawave_premium

logger = logging.getLogger(__name__)

# Free-tier traffic allowance for the bypass entity on a Trial run.
# Sourced from config.TRIAL_BYPASS_MB (default 500 MB).
def _trial_bypass_bytes() -> int:
    return int(getattr(config, "TRIAL_BYPASS_MB", 500)) * (1024 ** 2)


def _bypass_bytes_for(tariff: str, period_days: int, is_trial: bool) -> int:
    """Return the bypass entity's trafficLimitBytes for the given tariff.

    - Trial → config.TRIAL_BYPASS_MB MB
    - Combo → COMBO_TARIFFS[tariff][period_days]["gb"] GB (overrides base)
    - Basic / Plus → TRAFFIC_LIMITS[tariff][period_days] (already in bytes)
    """
    if is_trial:
        return _trial_bypass_bytes()
    combo_table = getattr(config, "COMBO_TARIFFS", {}) or {}
    if tariff in combo_table:
        per_period = combo_table[tariff].get(period_days) or {}
        gb = per_period.get("gb")
        if isinstance(gb, int) and gb > 0:
            return gb * (1024 ** 3)
    # Standard tariff
    traffic_table = getattr(config, "TRAFFIC_LIMITS", {}) or {}
    table = traffic_table.get(tariff)
    if isinstance(table, dict):
        # Same fallback shape used by remnawave_service._traffic_limit_for_tariff:
        # nearest matching period, else the largest known.
        if period_days in table:
            return int(table[period_days])
        available = sorted(table.keys())
        for p in available:
            if p >= period_days:
                return int(table[p])
        if available:
            return int(table[available[-1]])
    if isinstance(table, int):
        return int(table)
    # Last resort: 10 GB.
    return 10 * (1024 ** 3)


def _device_limit_for(tariff: str) -> int:
    """Premium device limit from existing DEVICE_LIMITS table."""
    limits = getattr(config, "DEVICE_LIMITS", {}) or {}
    return int(limits.get(tariff, 5))


def _looks_like_uuid(s: Optional[str]) -> bool:
    if not s or not isinstance(s, str):
        return False
    if len(s) != 36 or s.count("-") != 4:
        return False
    try:
        uuid_lib.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


async def _premium_url_for_existing(telegram_id: int) -> Optional[str]:
    """Return the cached premium subscriptionUrl for a user whose premium
    entity already exists (Task-1-migrated user or earlier purchase)."""
    import database
    pool = await database.get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        cached = await conn.fetchval(
            "SELECT remnawave_premium_sub_url FROM subscriptions "
            "WHERE telegram_id = $1 AND status = 'active'",
            telegram_id,
        )
    return cached or None


async def provision_subscription(
    telegram_id: int,
    *,
    tariff: str,
    subscription_end: datetime,
    period_days: int,
    is_trial: bool = False,
) -> dict:
    """Provision premium + bypass entities for a purchase / trial / renewal.

    Returns a dict shaped like the legacy `vpn_utils.add_vless_user`:
    keys `uuid`, `vless_url`, `vless_url_plus`, `subscription_type`.

    On any non-recoverable error a RuntimeError is raised so the caller's
    existing retry logic (`MAX_VPN_RETRIES` loop in grant_access) kicks in.
    """
    if not config.REMNAWAVE_ENABLED:
        raise RuntimeError("PURCHASE_FLOW_REMNAWAVE is on but REMNAWAVE_API_URL/TOKEN are not set")

    import database  # lazy import — keeps unit tests asyncpg-free

    # ── Determine the connection UUID we want the premium entity to use ──
    # If the user has an old samopis uuid (un-migrated legacy purchase, or a
    # previous bot purchase before cut-over), reuse it so legacy VLESS
    # clients keep working.  Otherwise generate a fresh one.
    existing_subscription = await database.get_subscription_any(telegram_id)
    legacy_uuid = (existing_subscription or {}).get("uuid") if existing_subscription else None
    if not _looks_like_uuid(legacy_uuid):
        legacy_uuid = None

    requested_uuid = legacy_uuid or str(uuid_lib.uuid4())

    # ── Premium + Bypass entities: параллельная провизия ─────────────
    #
    # Раньше эти два блока шли ПОСЛЕДОВАТЕЛЬНО — 2 sequential HTTP-
    # запроса к Remnawave (~0.5–1с каждый) на активацию триала /
    # покупки. Обе операции работают с независимыми сущностями (два
    # разных user'а на панели), поэтому дёргаем их через
    # asyncio.gather — итоговое время активации ≈ max(premium, bypass)
    # вместо sum. На новом юзере это режет ~1с из общего 2с.

    bypass_bytes = _bypass_bytes_for(tariff, period_days, is_trial)

    async def _do_premium() -> dict:
        existing_premium_uuid = await database.get_remnawave_premium_uuid(telegram_id)
        premium_sub_url: Optional[str] = None
        premium_panel_uuid: Optional[str] = existing_premium_uuid

        if existing_premium_uuid:
            renewed = await remnawave_premium.renew_premium_user(telegram_id, subscription_end)
            if not renewed:
                logger.warning(
                    "PURCHASE_FLOW: premium renew returned False — falling back to create-flow tg=%s",
                    telegram_id,
                )
                existing_premium_uuid = None
            else:
                premium_sub_url = await _premium_url_for_existing(telegram_id)

        if not existing_premium_uuid:
            result = await remnawave_premium.create_premium_user_entity(
                telegram_id,
                requested_uuid=requested_uuid,
                expire_at=subscription_end,
                description=f"Premium via bot ({tariff})",
            )
            if not result.ok:
                raise RuntimeError(
                    f"premium provision failed: status={result.status} error={result.error}"
                )
            premium_panel_uuid = result.panel_uuid
            premium_sub_url = result.subscription_url
            try:
                await database.set_remnawave_premium_uuid_and_url(
                    telegram_id,
                    result.panel_uuid or "",
                    result.subscription_url,
                    short_uuid=result.short_uuid,
                )
            except Exception as e:
                logger.error(
                    "PURCHASE_FLOW: failed to persist premium mapping tg=%s err=%s",
                    telegram_id, e,
                )
                raise

        if not premium_sub_url:
            # Cache miss after a renewal — back-fill from panel one time.
            try:
                from app.services import remnawave_api
                entity = await remnawave_api.get_user(premium_panel_uuid or "")
                premium_sub_url = (entity or {}).get("subscriptionUrl") or ""
                if premium_sub_url:
                    await database.set_remnawave_premium_sub_url(telegram_id, premium_sub_url)
            except Exception as e:
                logger.warning("PURCHASE_FLOW: premium url back-fill failed tg=%s %s", telegram_id, e)

        return {"panel_uuid": premium_panel_uuid, "sub_url": premium_sub_url}

    async def _do_bypass() -> dict:
        existing_bypass_uuid = await database.get_remnawave_uuid(telegram_id)
        bypass_sub_url: Optional[str] = None

        if existing_bypass_uuid:
            added = await remnawave_bypass.add_bypass_traffic(
                telegram_id, extra_bytes=bypass_bytes,
            )
            if not added:
                logger.warning(
                    "PURCHASE_FLOW: bypass top-up returned False — falling back to create-flow tg=%s",
                    telegram_id,
                )
                existing_bypass_uuid = None
            else:
                cache = await database.get_remnawave_bypass_cache(telegram_id)
                bypass_sub_url = (cache or {}).get("remnawave_bypass_sub_url") or None

        if not existing_bypass_uuid:
            bresult = await remnawave_bypass.create_bypass_user_entity(
                telegram_id,
                traffic_limit_bytes=bypass_bytes,
                description=f"Bypass via bot ({tariff})",
            )
            if not bresult.ok:
                raise RuntimeError(
                    f"bypass provision failed: tg={telegram_id} "
                    f"status={bresult.status} error={bresult.error}"
                )
            bypass_sub_url = bresult.subscription_url
            try:
                await database.set_remnawave_bypass_cache(
                    telegram_id,
                    bresult.panel_uuid,
                    bresult.subscription_url,
                    bresult.short_uuid,
                )
            except Exception as e:
                logger.warning(
                    "PURCHASE_FLOW: failed to persist bypass cache tg=%s %s",
                    telegram_id, e,
                )

        return {"sub_url": bypass_sub_url}

    premium_res, bypass_res = await asyncio.gather(_do_premium(), _do_bypass())
    premium_panel_uuid = premium_res["panel_uuid"]
    premium_sub_url = premium_res["sub_url"]
    bypass_sub_url = bypass_res["sub_url"]

    logger.info(
        "PURCHASE_FLOW_DONE: tg=%s tariff=%s premium_uuid=%s bypass_uuid=%s "
        "premium_url=%s bypass_url=%s",
        telegram_id, tariff,
        (premium_panel_uuid or "")[:8],
        ((await database.get_remnawave_uuid(telegram_id)) or "")[:8],
        bool(premium_sub_url),
        bool(bypass_sub_url),
    )

    return {
        # legacy uuid lives in subscriptions.uuid; the connection uuid that
        # ended up in the panel may differ if forced-uuid was rejected.
        "uuid": requested_uuid,
        "vless_url": premium_sub_url or "",
        "vless_url_plus": bypass_sub_url,
        "subscription_type": tariff or "basic",
    }


async def sync_renewal_to_remnawave(sync_info: dict) -> None:
    """Post-commit renewal sync — extend the user's Remnawave entities.

    Called after a renewal DB commit (grant_access STEP 2 — extending an
    already-active subscription).  Renews the premium entity's expireAt
    and tops up the bypass entity; `provision_subscription` is idempotent
    and handles both, and also creates the entities if the user somehow
    has none yet (a legacy un-migrated subscriber renewing for the first
    time after the cut-over).

    `sync_info` is the `renewal_xray_sync_after_commit` payload built by
    grant_access: telegram_id, subscription_end, tariff, period_days.
    Raises on Remnawave failure so the caller can signal the webhook to
    return 5xx and let the payment provider retry.
    """
    await provision_subscription(
        sync_info["telegram_id"],
        tariff=sync_info.get("tariff") or "basic",
        subscription_end=sync_info["subscription_end"],
        period_days=int(sync_info.get("period_days") or 30),
        is_trial=False,
    )


__all__ = ["provision_subscription", "sync_renewal_to_remnawave"]
