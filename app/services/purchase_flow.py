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


def _bypass_bytes_for(
    tariff: str,
    period_days: int,
    is_trial: bool,
    is_combo: bool = False,
) -> int:
    """Return the bypass entity's trafficLimitBytes for the given tariff.

    - Trial → config.TRIAL_BYPASS_MB MB
    - Combo → COMBO_TARIFFS[combo_{tariff}][period_days]["gb"] GB
      (base tariff basic/plus + combo_gb пакет — combo_gb это лимит bypass)
    - Basic / Plus → TRAFFIC_LIMITS[tariff][period_days] (already in bytes)
    """
    if is_trial:
        return _trial_bypass_bytes()
    combo_table = getattr(config, "COMBO_TARIFFS", {}) or {}
    # is_combo flag → tariff="basic"/"plus" мапится в combo_basic/combo_plus.
    # Fallback: tariff уже может быть с префиксом (напр. broadcast gift-combo).
    if is_combo:
        combo_key = tariff if tariff.startswith("combo_") else f"combo_{tariff}"
        per_period = (combo_table.get(combo_key) or {}).get(period_days) or {}
        gb = per_period.get("gb")
        if isinstance(gb, int) and gb > 0:
            return gb * (1024 ** 3)
    if tariff in combo_table:
        per_period = combo_table[tariff].get(period_days) or {}
        gb = per_period.get("gb")
        if isinstance(gb, int) and gb > 0:
            return gb * (1024 ** 3)
    # Standard tariff
    traffic_table = getattr(config, "TRAFFIC_LIMITS", {}) or {}
    table = traffic_table.get(tariff)
    if isinstance(table, dict):
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
    is_combo: bool = False,
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

    # ── Premium entity ───────────────────────────────────────────────
    existing_premium_uuid = await database.get_remnawave_premium_uuid(telegram_id)
    premium_sub_url: Optional[str] = None
    premium_panel_uuid: Optional[str] = existing_premium_uuid

    if existing_premium_uuid:
        # Renewal: PATCH expireAt.  Bypass entity is handled below independently.
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
            raise RuntimeError(f"premium provision failed: status={result.status} error={result.error}")
        premium_panel_uuid = result.panel_uuid
        premium_sub_url = result.subscription_url
        try:
            await database.set_remnawave_premium_uuid_and_url(
                telegram_id,
                result.panel_uuid or "",
                result.subscription_url,
                short_uuid=result.short_uuid,
            )
            # 3.x: numeric id обязателен для последующих actions/PATCH.
            if result.panel_id is not None:
                await database.set_remnawave_premium_id(telegram_id, result.panel_id)
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

    # ── Bypass entity ────────────────────────────────────────────────
    # ⚠️ ПРАВИЛА:
    #   • TRIAL             → создаём bypass с TRIAL_BYPASS_MB (единственный
    #                         путь для trial, webhook confirmation НЕ идёт).
    #   • Fresh paid (нет   → создаём bypass С ФИНАЛЬНЫМ лимитом (combo → 75 ГБ,
    #     entity)             обычный basic 30d → 10 ГБ). confirmation.py
    #                         увидит bypass_created_fresh=True в return и
    #                         SKIP свой top-up → никакого double-add.
    #   • Renewal (есть     → SKIP полностью. confirmation.py сам всё сделает:
    #     entity)             top-up на нужную сумму по tariff / combo.
    #                         Раньше double-add: +tariff здесь + +combo там.
    bypass_bytes = _bypass_bytes_for(tariff, period_days, is_trial, is_combo=is_combo)
    bypass_sub_url: Optional[str] = None
    bypass_created_fresh = False

    existing_bypass_uuid = await database.get_remnawave_uuid(telegram_id)
    if existing_bypass_uuid:
        # Renewal — bypass entity уже есть. Топ-ап делает confirmation.py.
        cache = await database.get_remnawave_bypass_cache(telegram_id)
        bypass_sub_url = (cache or {}).get("remnawave_bypass_sub_url") or None

    # Fresh create — только если нет entity вообще И это trial ИЛИ paid.
    if not existing_bypass_uuid:
        bresult = await remnawave_bypass.create_bypass_user_entity(
            telegram_id,
            traffic_limit_bytes=bypass_bytes,
            description=f"Bypass via bot ({tariff})",
        )
        if not bresult.ok:
            # Bypass fail НЕ блокирует premium (юзер получит ключ),
            # но админ должен узнать — иначе тихая потеря bypass tier.
            # Reconciliation-flow добэкфилит через resolve_bypass /
            # admin dashboard.
            logger.warning(
                "PURCHASE_FLOW_BYPASS_FAILED_NON_FATAL: tg=%s status=%s error=%s",
                telegram_id, bresult.status, bresult.error,
            )
            bypass_sub_url = None
            # Fire-and-forget DM админу (не блокирует flow).
            try:
                import asyncio as _aio
                _aio.create_task(_notify_admin_bypass_failed(
                    telegram_id, tariff, bresult.status, bresult.error,
                ))
            except Exception:
                pass
        else:
            bypass_sub_url = bresult.subscription_url
            bypass_created_fresh = True
        if bresult.ok:
            try:
                await database.set_remnawave_bypass_cache(
                    telegram_id,
                    bresult.panel_uuid,
                    bresult.subscription_url,
                    bresult.short_uuid,
                )
                # 3.x: numeric id для быстрого пути update/actions без
                # UUID→id auto-resolve overhead.
                if bresult.panel_id is not None:
                    await database.set_remnawave_id(telegram_id, bresult.panel_id)
            except Exception as e:
                logger.warning(
                    "PURCHASE_FLOW: failed to persist bypass cache tg=%s %s",
                    telegram_id, e,
                )

    # Backfill bypass_sub_url через панель, если POST не отдал subscriptionUrl
    # (защита: без URL bypass-кнопка "Подключиться" не появится).
    if not bypass_sub_url:
        try:
            from app.services import remnawave_api
            # Приоритет — numeric id (стабильно 3.x), затем uuid, затем tg_id.
            probe_key = None
            cache = await database.get_remnawave_bypass_cache(telegram_id)
            if cache and cache.get("remnawave_uuid"):
                probe_key = cache["remnawave_uuid"]
            if probe_key:
                entity = await remnawave_api.get_user(probe_key)
            else:
                entity = await remnawave_api.find_user_by_telegram_id(telegram_id)
            fetched_url = ((entity or {}).get("subscriptionUrl") or "").strip() or None
            if fetched_url:
                bypass_sub_url = fetched_url
                try:
                    # Сохраняем URL в кеш — не перезаписывает uuid/short_uuid.
                    await database.set_remnawave_bypass_cache(
                        telegram_id, None, fetched_url, None,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("PURCHASE_FLOW: bypass url back-fill failed tg=%s %s", telegram_id, e)

    logger.info(
        "PURCHASE_FLOW_DONE: tg=%s tariff=%s is_combo=%s premium_uuid=%s bypass_uuid=%s "
        "premium_url=%s bypass_url=%s bypass_fresh=%s bypass_bytes=%s",
        telegram_id, tariff, is_combo,
        (premium_panel_uuid or "")[:8],
        ((await database.get_remnawave_uuid(telegram_id)) or "")[:8],
        bool(premium_sub_url),
        bool(bypass_sub_url),
        bypass_created_fresh,
        bypass_bytes if bypass_created_fresh else "(untouched)",
    )

    return {
        # legacy uuid lives in subscriptions.uuid; the connection uuid that
        # ended up in the panel may differ if forced-uuid was rejected.
        "uuid": requested_uuid,
        "vless_url": premium_sub_url or "",
        "vless_url_plus": bypass_sub_url,
        "subscription_type": tariff or "basic",
        # NEW: True если мы только что создали bypass entity с ФИНАЛЬНЫМ лимитом.
        # confirmation.py: если True → НЕ добавлять combo/tariff GB (иначе double).
        "bypass_created_fresh": bypass_created_fresh,
    }


async def sync_renewal_to_remnawave(sync_info: dict) -> None:
    """Post-commit renewal sync — продлить ТОЛЬКО premium expireAt.

    Bypass GB добавляется отдельно в confirmation.py (там знают is_combo
    и сколько GB именно этой покупки). Раньше здесь звался
    provision_subscription, который делал double-add: +tariff_gb здесь
    и потом +combo_gb в confirmation → юзер получал сумму (85 вместо 75
    для combo, 20 вместо 10 для обычного renewal + случайного combo-фикса).

    Простая логика: renewal = продлить срок на premium. Всё.
    Bypass GB — отдельная зона ответственности confirmation.py.
    """
    from app.services import remnawave_premium
    tg = int(sync_info["telegram_id"])
    new_expire = sync_info["subscription_end"]
    ok = await remnawave_premium.renew_premium_user(tg, new_expire)
    if not ok:
        # Premium entity не найден — вызовем полный provision, который
        # создаст premium (и bypass если нужно) через preflight+adopt.
        # Это redundancy для legacy юзеров без premium entity в панели.
        logger.warning(
            "sync_renewal: renew_premium_user returned False tg=%s — "
            "falling back to full provision_subscription (creates missing entities)",
            tg,
        )
        await provision_subscription(
            tg,
            tariff=sync_info.get("tariff") or "basic",
            subscription_end=new_expire,
            period_days=int(sync_info.get("period_days") or 30),
            is_trial=False,
            is_combo=bool(sync_info.get("is_combo", False)),
        )


async def _notify_admin_bypass_failed(
    telegram_id: int,
    tariff: str,
    status: int,
    error: Optional[str],
) -> None:
    """DM админу что bypass не создался — premium у юзера работает,
    но bypass tier требует ручной добэкфилл (кнопка в dashboard
    users → tools или через reconciliation flow)."""
    try:
        import config
        from aiogram import Bot
        from app.api import telegram_webhook
        bot: Optional[Bot] = getattr(telegram_webhook, "_bot", None)
        if bot is None or not config.ADMIN_TELEGRAM_ID:
            return
        text = (
            "⚠️ <b>Bypass не создался</b>\n"
            f"User: <code>tg:{telegram_id}</code>\n"
            f"Tariff: <b>{tariff}</b>\n"
            f"Status: <code>{status}</code>\n"
            f"<i>{(error or 'unknown')[:180]}</i>\n\n"
            "Premium ключ у юзера работает. Bypass добэкфилить "
            "через дашборд Юзеры → карточка → «Резолв bypass»."
        )
        await bot.send_message(
            chat_id=config.ADMIN_TELEGRAM_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("bypass-fail admin-notify failed: %s", e)


__all__ = ["provision_subscription", "sync_renewal_to_remnawave"]
