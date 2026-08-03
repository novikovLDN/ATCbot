"""Куда вести кнопки «Подключиться» и «Скопировать ключ».

ЧТО ЗДЕСЬ

    Ссылку на подписку выдаёт панель Remnawave. Модуль отвечает на один
    вопрос — какая ссылка сейчас у этого человека, — и умеет чинить
    ситуацию, когда её нет:

      1. кэш в subscriptions (remnawave_premium_sub_url /
         remnawave_bypass_sub_url);
      2. живой запрос к панели, если кэш пуст (старые записи, дрейф
         статуса) — с записью результата обратно в кэш;
      3. ленивое создание сущности, если у человека активная подписка,
         а сущности в панели нет вовсе: триальщики и всё, что пропустил
         переезд с samopis. Замок на telegram_id не даёт создать две
         сущности при двух быстрых нажатиях подряд;
      4. пустая строка, если ссылки так и нет.

ПОЧЕМУ ПУСТАЯ СТРОКА, А НЕ ССЫЛКА СТАРОГО ФОРМАТА

    Четвёртым шагом раньше возвращалась самописная ссылка
    /api/sub/{подпись}?id=. Обслуживать её может только
    app/api/subscription_proxy.py, а он по умолчанию выключен: человек
    получал кнопку «Подключиться» с заведомо мёртвой ссылкой и решал,
    что купил неработающий ключ.

    Пустую строку вызывающие проверяют и показывают «ключ ещё
    выдаётся» — что соответствует действительности: сущность создастся
    при следующей попытке.

Функции отсюда не бросают исключений: экран должен отрисоваться в любом
случае.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)


# Per-process lock per telegram_id: prevents two concurrent button clicks
# from racing to create two premium entities for the same user.  Locks are
# cheap and short-lived; we never bother to GC them.
_lazy_provision_locks: dict[int, asyncio.Lock] = {}


async def get_user_premium_url(telegram_id: int) -> Optional[str]:
    """Return the Remnawave premium subscription URL for the user, or None.

    Resolution order:
      1. cached `remnawave_premium_sub_url` column (status='active' row).
      2. cached `remnawave_premium_sub_url` column ignoring status filter
         (rare: status was != 'active' at the moment the cache was written
         and the column never got populated for that row).
      3. live GET /api/users/{remnawave_premium_uuid} via the panel,
         followed by best-effort back-fill of the cache so the next
         request is fast.
    None means "no migrated/provisioned premium entity at all" — caller
    should consider lazy-provisioning or fall back to the legacy URL.
    """
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return None
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            # Step 1: cache hit on the active row.
            row = await conn.fetchrow(
                "SELECT remnawave_premium_uuid, remnawave_premium_sub_url "
                "FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
                telegram_id,
            )
            # Step 2: any row (status-agnostic) — covers users whose
            # subscription was inactive when the cache was first written.
            if not row:
                row = await conn.fetchrow(
                    "SELECT remnawave_premium_uuid, remnawave_premium_sub_url "
                    "FROM subscriptions WHERE telegram_id = $1 "
                    "ORDER BY (status='active') DESC, expires_at DESC NULLS LAST LIMIT 1",
                    telegram_id,
                )
        if not row:
            return None
        cached_raw = row["remnawave_premium_sub_url"]
        cached = cached_raw.strip() if cached_raw else ""
        if cached:
            return cached

        # Step 3: panel fallback.  We have the entity uuid but the URL
        # column was never populated (e.g. row migrated before column 046
        # existed, or written when status wasn't active).  One round-trip
        # to fix it forever.
        panel_uuid_raw = row["remnawave_premium_uuid"]
        panel_uuid = panel_uuid_raw.strip() if panel_uuid_raw else ""
        if not panel_uuid:
            return None
        try:
            from app.services import remnawave_api
            entity = await remnawave_api.get_user(panel_uuid)
        except Exception as e:
            logger.warning("USER_PREMIUM_PANEL_FALLBACK_FAIL: tg=%s %s", telegram_id, e)
            return None
        url = ((entity or {}).get("subscriptionUrl") or "").strip() or None
        if not url:
            return None
        # Best-effort cache write so the next call is fast.
        try:
            await database.set_remnawave_premium_sub_url(telegram_id, url)
        except Exception as e:
            logger.warning("USER_PREMIUM_BACKFILL_FAIL: tg=%s %s", telegram_id, e)
        return url
    except Exception as e:
        logger.warning("USER_PREMIUM_URL_LOOKUP_FAIL: tg=%s %s", telegram_id, e)
        return None


async def _try_lazy_provision_entities(telegram_id: int) -> dict:
    """Ensure user has BOTH premium AND bypass Remnawave entities.

    For paid purchases bypass already gets created by
    `remnawave_service.create_remnawave_user` at payment-confirmation
    time.  This helper covers the gaps: trial users (the existing
    paid-flow service skips them) and any edge case where one of the
    two entities never landed in the panel.

    Returns dict with two booleans (created_premium / created_bypass)
    so callers can log which side was actually filled.

    Per-process lock keyed on telegram_id prevents duplicate creation
    under concurrent button clicks.
    """
    out = {"created_premium": False, "created_bypass": False}
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return out

    lock = _lazy_provision_locks.setdefault(telegram_id, asyncio.Lock())
    async with lock:
        try:
            import database
            sub = await database.get_subscription_any(telegram_id)
            if not sub:
                return out
            # Only provision for users with a live subscription.  Expired /
            # blocked rows have no business getting fresh entities.
            from datetime import datetime, timezone, timedelta
            expires = sub.get("expires_at")
            if expires:
                if getattr(expires, "tzinfo", None) is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= datetime.now(timezone.utc):
                    return out
                expire_at = expires
            else:
                expire_at = datetime.now(timezone.utc) + timedelta(days=3)

            samopis_uuid_raw = sub.get("uuid")
            samopis_uuid = samopis_uuid_raw.strip() if samopis_uuid_raw else ""
            is_trial = (sub.get("source") == "trial")

            # ── Premium entity ────────────────────────────────────────
            existing_premium = (sub.get("remnawave_premium_uuid") or "").strip()
            if not existing_premium and getattr(config, "REMNAWAVE_MAIN_SQUAD_UUID", ""):
                from app.services import remnawave_premium
                presult = await remnawave_premium.create_premium_user_entity(
                    telegram_id,
                    requested_uuid=samopis_uuid or None,
                    expire_at=expire_at,
                    description=("Lazy trial via URL" if is_trial else "Lazy-provisioned via URL"),
                )
                if presult.ok:
                    try:
                        await database.set_remnawave_premium_uuid_and_url(
                            telegram_id,
                            presult.panel_uuid or "",
                            presult.subscription_url,
                            short_uuid=presult.short_uuid,
                        )
                        out["created_premium"] = True
                        logger.info(
                            "LAZY_PROVISION_PREMIUM_DONE: tg=%s uuid=%s recovered=%s trial=%s",
                            telegram_id, (presult.panel_uuid or "")[:8],
                            presult.recovered, is_trial,
                        )
                    except Exception as e:
                        logger.warning(
                            "LAZY_PROVISION_PREMIUM_PERSIST_FAIL: tg=%s err=%s",
                            telegram_id, e,
                        )
                else:
                    logger.warning(
                        "LAZY_PROVISION_PREMIUM_FAILED: tg=%s status=%s err=%s",
                        telegram_id, presult.status, presult.error,
                    )

            # ── Bypass entity ─────────────────────────────────────────
            # Trial → TRIAL_BYPASS_MB MB, paid (any tariff) → 10 GB.
            # Combo edge case: paid combo buyers got their bypass through
            # the regular purchase flow already, so they wouldn't reach
            # this branch (existing_bypass would be set).
            existing_bypass = (sub.get("remnawave_uuid") or "").strip()
            if not existing_bypass and getattr(config, "REMNAWAVE_SQUAD_UUID", ""):
                if is_trial:
                    trial_mb = int(getattr(config, "TRIAL_BYPASS_MB", 500)) or 500
                    bypass_bytes = trial_mb * (1024 ** 2)
                else:
                    bypass_bytes = 10 * (1024 ** 3)  # default basic/plus traffic cap

                from app.services import remnawave_bypass
                bresult = await remnawave_bypass.create_bypass_user_entity(
                    telegram_id,
                    traffic_limit_bytes=bypass_bytes,
                    description=("Lazy trial bypass" if is_trial else "Lazy-provisioned bypass"),
                )
                if bresult.ok:
                    try:
                        await database.set_remnawave_bypass_cache(
                            telegram_id,
                            bresult.panel_uuid,
                            bresult.subscription_url,
                            bresult.short_uuid,
                        )
                        out["created_bypass"] = True
                        logger.info(
                            "LAZY_PROVISION_BYPASS_DONE: tg=%s uuid=%s bytes=%d trial=%s",
                            telegram_id, (bresult.panel_uuid or "")[:8],
                            bypass_bytes, is_trial,
                        )
                    except Exception as e:
                        logger.warning(
                            "LAZY_PROVISION_BYPASS_PERSIST_FAIL: tg=%s err=%s",
                            telegram_id, e,
                        )
                else:
                    logger.warning(
                        "LAZY_PROVISION_BYPASS_FAILED: tg=%s status=%s err=%s",
                        telegram_id, bresult.status, bresult.error,
                    )

            return out
        except Exception as e:
            logger.warning("LAZY_PROVISION_EXCEPTION: tg=%s err=%s", telegram_id, e)
            return out


# Backward-compat alias retained for any external callers / older tests.
_try_lazy_provision_premium = _try_lazy_provision_entities


async def _bypass_url_from_cache(telegram_id: int) -> Optional[str]:
    """Plain DB read of the cached bypass sub URL.  None on miss."""
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return None
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT remnawave_uuid, remnawave_bypass_sub_url "
                "FROM subscriptions WHERE telegram_id = $1 "
                "ORDER BY (status='active') DESC, expires_at DESC NULLS LAST LIMIT 1",
                telegram_id,
            )
        if not row:
            return None
        cached_raw = row["remnawave_bypass_sub_url"]
        cached = cached_raw.strip() if cached_raw else ""
        if cached:
            return cached
        # Cache miss but uuid present — fetch from panel + back-fill.
        rmn_uuid_raw = row["remnawave_uuid"]
        rmn_uuid = rmn_uuid_raw.strip() if rmn_uuid_raw else ""
        if not rmn_uuid:
            return None
        try:
            from app.services import remnawave_api
            entity = await remnawave_api.get_user(rmn_uuid)
        except Exception as e:
            logger.warning("USER_BYPASS_PANEL_FALLBACK_FAIL: tg=%s %s", telegram_id, e)
            return None
        url = ((entity or {}).get("subscriptionUrl") or "").strip() or None
        if not url:
            return None
        try:
            await database.set_remnawave_bypass_cache(telegram_id, rmn_uuid, url, (entity or {}).get("shortUuid"))
        except Exception as e:
            logger.warning("USER_BYPASS_BACKFILL_FAIL: tg=%s %s", telegram_id, e)
        return url
    except Exception as e:
        logger.warning("USER_BYPASS_URL_LOOKUP_FAIL: tg=%s %s", telegram_id, e)
        return None


async def get_user_bypass_url(telegram_id: int) -> Optional[str]:
    """Return the Remnawave bypass subscription URL for the user, or None.

    Resolution order:
      1. Cached `remnawave_bypass_sub_url`.
      2. Live panel lookup via stored `remnawave_uuid` + back-fill.
      3. Lazy-provision a bypass entity for an active user without one.
      4. Re-query layer (1).
    None means we genuinely could not produce a URL — caller should
    skip rendering the bypass link rather than show a broken one.
    """
    cached = await _bypass_url_from_cache(telegram_id)
    if cached:
        return cached
    out = await _try_lazy_provision_entities(telegram_id)
    if out.get("created_bypass"):
        cached = await _bypass_url_from_cache(telegram_id)
        if cached:
            return cached
    return None


async def get_user_primary_subscription_url(telegram_id: int) -> str:
    """Куда должны вести кнопки «Подключиться» и «Скопировать ключ».

    Порядок:
      1. Ссылка премиум-сущности из Remnawave (из кэша или живая).
      2. Ленивое создание сущностей для активного пользователя, у
         которого их почему-то нет (триал, старые записи), — и повторный
         запрос ссылки.
      3. Пустая строка.

    ПОЧЕМУ ПУСТАЯ СТРОКА, А НЕ ССЫЛКА СТАРОГО ФОРМАТА

        Третьим шагом здесь возвращалась samopis-ссылка через
        _legacy_sub_url. Обслуживать её может только subscription_proxy,
        а он по умолчанию выключен — то есть человек, у которого не
        получилось создать премиум-сущность (панель недоступна, имя занято
        чужой записью), получал кнопку «Подключиться» с заведомо
        нерабочей ссылкой и решал, что купил неработающий ключ.

        Даже с включённым прокси толку нет: он резолвит через тот же
        get_user_premium_url, а сюда мы попали ровно потому, что ссылки
        нет.

        Пустая строка — честный ответ. Вызывающие её проверяют и
        показывают «ключ ещё выдаётся», что соответствует
        действительности: сущность создастся при следующей попытке.
    """
    premium = await get_user_premium_url(telegram_id)
    if premium:
        return premium

    out = await _try_lazy_provision_entities(telegram_id)
    if out.get("created_premium"):
        premium = await get_user_premium_url(telegram_id)
        if premium:
            return premium

    logger.warning(
        "PRIMARY_SUB_URL_UNAVAILABLE tg=%s — премиум-сущность не создана, "
        "показываем «ключ выдаётся» вместо нерабочей ссылки",
        telegram_id,
    )
    return ""


__all__ = [
    "get_user_premium_url",
    "get_user_bypass_url",
    "get_user_primary_subscription_url",
]
