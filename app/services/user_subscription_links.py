"""
Helpers that return the right subscription URL for a Telegram user
*depending on whether the bot has cut over to the Remnawave-only flow*.

Async helpers that surface the Remnawave-issued URL on the bot's
"Подключиться" buttons / copy-key blocks:
  1. Read the cached `remnawave_premium_sub_url` /
     `remnawave_bypass_sub_url` from `subscriptions` (populated by
     Task 1 migration + Task 2 purchase flow).
  2. Live panel fallback via `remnawave_api.get_user(uuid)` when the
     cache column was never populated for some reason (status drift,
     legacy migration before column 046 existed, …) — back-fills the
     cache on success.
  3. LAZY PROVISION: when a user has an active subscription with a
     samopis uuid but NO premium entity at all (trial users + any
     edge case the migration script missed), provision one on the
     fly so the link is finally a real Remnawave URL.  Per-process
     dedup lock prevents duplicate creation under concurrent clicks.

These helpers never raise.
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


# ── Subscription host rewrite ────────────────────────────────────────
# `subscription.vps-cloud.uk` был RF-фронтом (reverse-proxy) к панели. Его
# снесли — TLS-cert невалиден, и любая ссылка, всё ещё указывающая туда
# (в БД remnawave_*_sub_url, в панельном subscriptionUrl или уже вшитая в
# клиент юзера), падает в Happ/Incy с «Сертификат недействителен».
#
# Единая точка нормализации ИСХОДЯЩИХ ссылок: любой МЁРТВЫЙ host гоним на
# ЖИВОЙ (тот же, что использует агрегатор — config.SUB_AGGREGATOR_UPSTREAM_HOST,
# по умолчанию sub.atlassecure.ru). Живые/samopis/прочие хосты НЕ трогаем —
# rewrite строго по списку мёртвых, чтобы не трогать чужие ссылки.
# path/shortuuid у vps-cloud.uk и живого host совпадают (это был
# просто фронт к той же панели) → достаточно подменить host.
_DEAD_SUB_HOST_SUFFIXES = ("vps-cloud.uk",)


def _live_sub_host() -> str:
    return getattr(config, "SUB_AGGREGATOR_UPSTREAM_HOST", "") or "sub.atlassecure.ru"


def _rewrite_sub_host(url: Optional[str]) -> Optional[str]:
    """Rewrite ONLY decommissioned subscription hosts to the live panel host.
    Anything not on the dead-host list (already-live, samopis, etc.) is
    returned unchanged."""
    if not url:
        return url
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host and any(host == s or host.endswith("." + s) for s in _DEAD_SUB_HOST_SUFFIXES):
            live = _live_sub_host()
            netloc = f"{live}:{parts.port}" if parts.port else live
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return url


# Публичный alias — низкоуровневые сервисы (remnawave_api) применяют
# этот rewrite централизованно, чтобы raw URL из панели никогда не
# уходил юзеру с невалидным cert-хостом.
rewrite_sub_host = _rewrite_sub_host


_PANEL_SUB_PATH_PREFIX = "/api/sub/"


def public_sub_url(url: Optional[str]) -> Optional[str]:
    """Plain subscription link as users must see it: https://sub.atlassecure.ru/<shortuuid>.

    The panel's subscriptionUrl is https://rmnw.atlassecure.ru/api/sub/<shortuuid>
    (panel host, legacy path); the public host serves the same subscription at
    /<shortuuid> (the /api/sub/ path is 404 there). Only the panel host, the live
    host and dead hosts are rewritten; any other host is returned unchanged."""
    url = _rewrite_sub_host(url)
    if not url:
        return url
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        live = _live_sub_host()
        panel = (urlsplit(getattr(config, "REMNAWAVE_SUB_BASE_URL", "") or "").hostname or "").lower()
        if host not in {live, panel, "rmnw.atlassecure.ru"}:
            return url
        path = parts.path
        if path.startswith(_PANEL_SUB_PATH_PREFIX):
            path = "/" + path[len(_PANEL_SUB_PATH_PREFIX):]
        return urlunsplit((parts.scheme or "https", live, path, parts.query, parts.fragment))
    except Exception:
        return url


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
            return _rewrite_sub_host(cached)

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
        # Best-effort cache write so the next call is fast.  We store the
        # panel's raw URL (legacy host) so the DB stays consistent with
        # Remnawave — the host swap only happens on the user-facing return.
        try:
            await database.set_remnawave_premium_sub_url(telegram_id, url)
        except Exception as e:
            logger.warning("USER_PREMIUM_BACKFILL_FAIL: tg=%s %s", telegram_id, e)
        return _rewrite_sub_host(url)
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
            # ⚠️ bypass-only строка держит expires_at = NOW+10y как маркер
            # (премиум истёк, остался только bypass). НЕЛЬЗЯ создавать по ней
            # premium-энтити — иначе минтим фантомный premium на 10 лет
            # (ровно инцидент «Откат premium ×10y»). Премиум провижиним только
            # для НЕ-bypass-only строк.
            is_bypass_only = bool(sub.get("is_bypass_only"))

            # ── Premium entity ────────────────────────────────────────
            existing_premium = (sub.get("remnawave_premium_uuid") or "").strip()
            if not existing_premium and not is_bypass_only and getattr(config, "REMNAWAVE_MAIN_SQUAD_UUID", ""):
                from app.services import remnawave_premium
                from app.services.tariffs import premium_panel_tag_for_subscription
                presult = await remnawave_premium.create_premium_user_entity(
                    telegram_id,
                    requested_uuid=samopis_uuid or None,
                    expire_at=expire_at,
                    description=("Lazy trial via URL" if is_trial else "Lazy-provisioned via URL"),
                    # devices by tariff (owner 2026-09-14); trial = Basic
                    tier=("basic" if is_trial else (sub.get("subscription_type") or "basic")),
                    tag=premium_panel_tag_for_subscription(sub),
                )
                if presult.ok:
                    try:
                        await database.set_remnawave_premium_uuid_and_url(
                            telegram_id,
                            presult.panel_uuid or "",
                            presult.subscription_url,
                            short_uuid=presult.short_uuid,
                        )
                        if presult.panel_id is not None:
                            await database.set_remnawave_premium_id(telegram_id, presult.panel_id)
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
                        if bresult.panel_id is not None:
                            await database.set_remnawave_id(telegram_id, bresult.panel_id)
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
                "SELECT remnawave_uuid, remnawave_bypass_sub_url, "
                "       remnawave_premium_uuid, remnawave_premium_sub_url "
                "FROM subscriptions WHERE telegram_id = $1 "
                "ORDER BY (status='active') DESC, expires_at DESC NULLS LAST LIMIT 1",
                telegram_id,
            )
        if not row:
            return None
        cached_raw = row["remnawave_bypass_sub_url"]
        cached = cached_raw.strip() if cached_raw else ""
        bypass_uuid = (row["remnawave_uuid"] or "").strip()
        premium_uuid = (row["remnawave_premium_uuid"] or "").strip()
        premium_url_raw = row["remnawave_premium_sub_url"]
        premium_url = premium_url_raw.strip() if premium_url_raw else ""

        # ⚠️ Contamination guard. Legacy backfill / stream lookups sometimes
        # wrote the PREMIUM entity's uuid/url into the bypass columns (panel
        # stream returns premium first). Then this helper faithfully served
        # premium's URL as "обход" → the manual-setup screen showed the SAME
        # key for основные and обход. Detect it cheaply (no panel call for
        # healthy users): the bypass pointer/url must not equal the premium
        # one. If it does — fall through to the username-verified self-heal.
        contaminated = bool(
            (bypass_uuid and premium_uuid and bypass_uuid == premium_uuid)
            or (cached and premium_url
                and _rewrite_sub_host(cached) == _rewrite_sub_host(premium_url))
        )
        if cached and not contaminated:
            return _rewrite_sub_host(cached)

        # Miss OR contaminated → resolve the GENUINE bypass entity by username
        # (get_bypass_entity_safe verifies username == str(tg), the canonical
        # bypass discriminator, and self-heals remnawave_id/uuid in the DB).
        try:
            from app.services import remnawave_api
            entity = await remnawave_api.get_bypass_entity_safe(telegram_id)
        except Exception as e:
            logger.warning("USER_BYPASS_PANEL_FALLBACK_FAIL: tg=%s %s", telegram_id, e)
            return None
        url = ((entity or {}).get("subscriptionUrl") or "").strip() or None
        if not url:
            return None
        # Overwrite the (possibly contaminated) cached bypass URL with the
        # verified one. uuid was already fixed inside get_bypass_entity_safe.
        try:
            await database.set_remnawave_bypass_cache(
                telegram_id,
                (entity or {}).get("uuid") or (entity or {}).get("vlessUuid"),
                url,
                (entity or {}).get("shortUuid"),
            )
        except Exception as e:
            logger.warning("USER_BYPASS_BACKFILL_FAIL: tg=%s %s", telegram_id, e)
        if contaminated:
            logger.warning(
                "USER_BYPASS_URL_SELFHEAL: tg=%s bypass column held premium data "
                "(bypass_uuid==premium_uuid or bypass_url==premium_url) — replaced "
                "with username-verified bypass URL",
                telegram_id,
            )
        return _rewrite_sub_host(url)
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


# ── Dashboard: «Обновить ссылки» / «Перевыпустить подписку» ─────────
#
# The bot serves subscription links from its cache (remnawave_*_sub_url,
# vpn_key / vpn_key_plus). A panel «перевыпуск» (revoke) issues a new
# shortUuid, so after a manual one in the panel the bot kept handing out the
# dead link (prod 2026-09-15). Owner's choice: fixed from the user's card in
# the dashboard, never on the user's key screens (no panel wait there).

_PANEL_TIMEOUT_S = 15.0
_ENTITIES = ("premium", "bypass")


async def _sub_row(telegram_id: int):
    import database
    pool = await database.get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT remnawave_premium_id, remnawave_premium_uuid, remnawave_premium_sub_url, "
            "       remnawave_bypass_sub_url "
            "FROM subscriptions WHERE telegram_id = $1 "
            "ORDER BY (status='active') DESC, expires_at DESC NULLS LAST LIMIT 1",
            telegram_id,
        )


async def _premium_entity(telegram_id: int, row) -> Optional[dict]:
    """The premium panel entity — only if its username is tg_{id}_premium."""
    from app.services import remnawave_api
    from app.services.remnawave_premium import build_premium_username
    ref = row and (row["remnawave_premium_id"] or (row["remnawave_premium_uuid"] or "").strip())
    if not ref:
        return None
    ent = await remnawave_api.get_user(ref)
    if not isinstance(ent, dict) or str(ent.get("username") or "") != build_premium_username(telegram_id):
        return None
    return ent


async def _bypass_entity(telegram_id: int) -> Optional[dict]:
    """The bypass panel entity (get_bypass_entity_safe checks username == str(tg))."""
    from app.services import remnawave_api
    ent = await remnawave_api.get_bypass_entity_safe(telegram_id)
    return ent if isinstance(ent, dict) else None


async def refresh_cached_sub_urls(telegram_id: int) -> dict:
    """Re-read both subscription links from the panel; store the ones that changed.

    Returns {"premium": s, "bypass": s}, s ∈ updated / unchanged / no_entity /
    error / disabled. Never raises."""
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return dict.fromkeys(_ENTITIES, "disabled")
    try:
        return await asyncio.wait_for(_refresh_cached_sub_urls(telegram_id), timeout=_PANEL_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — timeout included
        logger.warning("SUB_URL_REFRESH_FAIL: tg=%s %s", telegram_id, type(e).__name__)
        return dict.fromkeys(_ENTITIES, "error")


async def _refresh_cached_sub_urls(telegram_id: int) -> dict:
    import database
    row = await _sub_row(telegram_id)
    if not row:
        return dict.fromkeys(_ENTITIES, "no_entity")

    async def one(which: str, entity, cached: str) -> str:
        ent = await entity
        url = ((ent or {}).get("subscriptionUrl") or "").strip()
        if not url:
            return "no_entity"
        if url == cached:
            return "unchanged"
        await database.replace_cached_sub_url(telegram_id, which, cached, url, ent.get("shortUuid"))
        logger.info("SUB_URL_REFRESHED: tg=%s which=%s", telegram_id, which)
        return "updated"

    results = await asyncio.gather(
        one("premium", _premium_entity(telegram_id, row), (row["remnawave_premium_sub_url"] or "").strip()),
        one("bypass", _bypass_entity(telegram_id), (row["remnawave_bypass_sub_url"] or "").strip()),
        return_exceptions=True,
    )
    out = {}
    for which, res in zip(_ENTITIES, results):
        if isinstance(res, BaseException):
            logger.warning("SUB_URL_REFRESH_FAIL: tg=%s which=%s %s", telegram_id, which, type(res).__name__)
            res = "error"
        out[which] = res
    return out


async def reissue_sub_urls(telegram_id: int) -> dict:
    """Full «перевыпуск» of both panel entities (new shortUuid, vless uuid and
    passwords — the old links and client configs stop working), then the new
    links go to the cache.

    Returns {which: {"revoke": revoked / no_entity / error / disabled,
    "links": <refresh status>}}. Never raises."""
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return {w: {"revoke": "disabled", "links": "disabled"} for w in _ENTITIES}
    try:
        return await asyncio.wait_for(_reissue_sub_urls(telegram_id), timeout=2 * _PANEL_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — timeout included
        logger.warning("SUB_REISSUE_FAIL: tg=%s %s", telegram_id, type(e).__name__)
        return {w: {"revoke": "error", "links": "error"} for w in _ENTITIES}


async def _reissue_sub_urls(telegram_id: int) -> dict:
    from app.services import remnawave_api
    row = await _sub_row(telegram_id)
    entities = await asyncio.gather(
        _premium_entity(telegram_id, row), _bypass_entity(telegram_id), return_exceptions=True,
    )
    revoke = {}
    for which, ent in zip(_ENTITIES, entities):
        if isinstance(ent, BaseException):
            revoke[which] = "error"
        elif not ent or ent.get("id") is None:
            revoke[which] = "no_entity"
        else:
            try:
                done = await remnawave_api.revoke_user_subscription(int(ent["id"]))
                revoke[which] = "revoked" if done is not None else "error"
            except Exception as e:  # noqa: BLE001
                logger.warning("SUB_REISSUE_FAIL: tg=%s which=%s %s", telegram_id, which, type(e).__name__)
                revoke[which] = "error"
    links = await _refresh_cached_sub_urls(telegram_id)
    logger.info("SUB_REISSUED: tg=%s revoke=%s links=%s", telegram_id, revoke, links)
    return {w: {"revoke": revoke[w], "links": links[w]} for w in _ENTITIES}


async def get_user_primary_subscription_url(telegram_id: int) -> str:
    """Return the URL the bot's "Подключиться" / copy-key buttons should
    point at for this user.

    Resolution order:
      1. Cached / live Remnawave premium URL.
      2. Lazy-provision both entities for an active user that somehow
         doesn't have them yet (trial / pre-Task-2 edge cases) — then
         re-query premium.

    Returns "" if neither yields a premium URL (callers treat a falsy
    URL as "no key").
    """
    premium = await get_user_premium_url(telegram_id)
    if premium:
        return premium

    out = await _try_lazy_provision_entities(telegram_id)
    if out.get("created_premium"):
        premium = await get_user_premium_url(telegram_id)
        if premium:
            return premium

    # Legacy samopis /api/sub/{token} fallback removed together with
    # subscription_proxy (the only server of that path).
    return ""


__all__ = [
    "get_user_premium_url",
    "get_user_bypass_url",
    "get_user_primary_subscription_url",
    "refresh_cached_sub_urls",
    "reissue_sub_urls",
]
