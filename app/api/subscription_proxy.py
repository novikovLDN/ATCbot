"""
Subscription URL backward-compat router.

Legacy users opened subscription links of two shapes:

    https://sub.atlassecure.ru/sub/{uuid}                (samopis path)
    https://atlassecure.ru/api/sub/{token}?id={tg_id}    (current bot path)

After the migration to Remnawave premium each migrated user has a
Remnawave subscription URL (panel-issued).  This router translates the
legacy paths into a 302 redirect to the Remnawave URL.  Unmigrated users
are forwarded to the legacy samopis endpoint (`LEGACY_SAMOPIS_SUB_BASE_URL`)
so existing clients keep working during the grace period.

Endpoints (mounted only when config.SUBSCRIPTION_PROXY_ENABLED is True):

    GET /sub/{uuid}        — legacy samopis-style URL
    GET /api/sub/{token}   — current bot-style URL (needs ?id={telegram_id})

Resolution order for /sub/{uuid}:
    1) Look up the uuid in subscriptions.remnawave_premium_uuid (migrated user).
    2) Look up the uuid in subscriptions.uuid (samopis fallback).
    3) 404 if neither matches.

/api/sub/{token} резолвится иначе: token — это подпись, а не
идентификатор, см. докстринг обработчика.

Errors are intentionally vague — the panel-issued URL is sensitive and we
do not want to leak per-user state to scanners.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Path, Query
from fastapi.responses import RedirectResponse, JSONResponse

import config
from app.services import remnawave_api

logger = logging.getLogger(__name__)


def _redact_target(url: str) -> str:
    """Оставить от целевой ссылки только схему и хост.

    Файл сам объявляет выданную панелью ссылку чувствительной (см. докстринг
    выше), но записи о редиректе печатали `target.split("?")[0]` — это
    отрезает лишь query, а секрет лежит в ПУТИ: `/sub/<shortUuid>`. Кто
    прочитал такую строку, тот открывает чужую подписку. Для разбора
    достаточно знать, на какой бэкенд ушёл редирект.
    """
    if not url:
        return "<none>"
    scheme, sep, rest = url.partition("://")
    if not sep:
        return "<redacted>"
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}/<redacted>"

router = APIRouter()


def _legacy_fallback_url(uuid_or_token: str) -> Optional[str]:
    base = (config.LEGACY_SAMOPIS_SUB_BASE_URL or "").rstrip("/")
    if not base:
        return None
    return f"{base}/sub/{quote(uuid_or_token, safe='')}"


async def _resolve_remnawave_url(panel_uuid: str) -> Optional[str]:
    """Fetch the Remnawave-issued subscription URL for an entity uuid."""
    try:
        user = await remnawave_api.get_user(panel_uuid)
    except Exception as e:
        logger.warning("SUB_PROXY_PANEL_LOOKUP_FAIL: uuid=%s %s", panel_uuid[:8], e)
        return None
    if not user:
        return None
    url = user.get("subscriptionUrl") or ""
    return url or None


async def _redirect_target_for_subscription(sub: dict) -> Optional[str]:
    """Return the Remnawave URL for a subscription row.

    Uses subscriptions.remnawave_premium_sub_url as the cache.  If the cache
    is empty (legacy rows migrated before column 046 existed), falls back
    to GET /api/users/{uuid} and back-fills the column so the next request
    is cache-hit.
    """
    cached = (sub.get("remnawave_premium_sub_url") or "").strip()
    if cached:
        return cached

    panel_uuid = sub.get("remnawave_premium_uuid")
    if not panel_uuid:
        return None

    target = await _resolve_remnawave_url(panel_uuid)
    if not target:
        return None

    # Back-fill the cache. Best-effort: a DB hiccup here only means the next
    # request will re-fetch — not user-visible.
    try:
        import database  # lazy
        tg = sub.get("telegram_id")
        if tg:
            await database.set_remnawave_premium_sub_url(int(tg), target)
    except Exception as e:
        logger.warning("SUB_PROXY_BACKFILL_FAIL: uuid=%s %s", panel_uuid[:8], e)
    return target


async def _resolve(uuid: str) -> Optional[str]:
    """Return the target redirect URL for a legacy uuid/token, or None."""
    import database  # lazy — keeps unit tests asyncpg-free
    # 1) Migrated user — look up by remnawave_premium_uuid (the column the
    #    migration script writes).
    try:
        sub = await database.get_subscription_by_premium_uuid(uuid)
    except Exception as e:
        logger.warning("SUB_PROXY_DB_PREMIUM_FAIL: uuid=%s %s", uuid[:8], e)
        sub = None
    if sub and sub.get("remnawave_premium_uuid"):
        target = await _redirect_target_for_subscription(sub)
        if target:
            return target

    # 2) Unmigrated user — look up by legacy samopis uuid.
    try:
        sub2 = await database.get_subscription_by_samopis_uuid(uuid)
    except Exception as e:
        logger.warning("SUB_PROXY_DB_SAMOPIS_FAIL: uuid=%s %s", uuid[:8], e)
        sub2 = None
    if sub2:
        # If they happen to be migrated under a different column we still
        # have a Remnawave url to redirect to.
        if sub2.get("remnawave_premium_uuid"):
            target = await _redirect_target_for_subscription(sub2)
            if target:
                return target
        # Otherwise fall back to the legacy samopis sub endpoint, if configured.
        return _legacy_fallback_url(uuid)

    # 3) Unknown uuid — last-resort fallback to legacy (still works for users
    #    whose row was archived but their xray inbound is alive).
    return _legacy_fallback_url(uuid)


async def _resolve_for_telegram_id(telegram_id: int) -> Optional[str]:
    """Куда вести человека, которого мы опознали по подписанной ссылке.

    Тот же источник, что и у кнопки «Подключиться» в боте, — иначе старая
    ссылка и кнопка вели бы в разные места.
    """
    try:
        from app.services.user_subscription_links import get_user_premium_url
        return (await get_user_premium_url(telegram_id)) or None
    except Exception as e:
        logger.warning("SUB_PROXY_PREMIUM_URL_FAIL: tg=%s %s", telegram_id, e)
        return None


@router.get("/sub/{uuid}", include_in_schema=False)
async def legacy_sub(uuid: str = Path(..., min_length=8, max_length=128)):
    target = await _resolve(uuid)
    if target:
        logger.info("SUB_PROXY_REDIRECT: uuid=%s -> %s", uuid[:8], _redact_target(target))
        return RedirectResponse(target, status_code=302)
    return JSONResponse({"error": "not_found"}, status_code=404)


@router.get("/api/sub/{token}", include_in_schema=False)
async def bot_sub(
    token: str = Path(..., min_length=8, max_length=128),
    id: Optional[int] = Query(None, gt=0),
):
    """Ссылка, которую раздавал сам бот: /api/sub/{token}?id={telegram_id}.

    ЧЕМ ЭТОТ МАРШРУТ ОТЛИЧАЕТСЯ ОТ /sub/{uuid}

        Там в пути стоит UUID — его можно найти в базе. Здесь стоит
        ПОДПИСЬ: HMAC-SHA256(bot_token, telegram_id), обрезанная до 32
        символов (vpn_utils.build_sub_url). Она нигде не хранится, и
        искать её в колонках бессмысленно — раньше маршрут именно это и
        делал, поэтому не мог отрезолвиться ни разу за всё время: оба
        поиска промахивались, и запрос уходил в samopis-фолбэк, где
        такого пути тоже нет.

        Кто пользователь — сообщает параметр id, а токен доказывает, что
        ссылку выдали мы. Без проверки подписи любой мог бы подставить
        чужой id и получить чужую ссылку на подписку.
    """
    if id is None:
        # Ссылка без id пришла не от нас: build_sub_url всегда его ставит.
        logger.info("SUB_PROXY_API_NO_ID: token=%s", token[:8])
        return JSONResponse({"error": "not_found"}, status_code=404)

    from vpn_utils import generate_sub_token

    expected = generate_sub_token(config.BOT_TOKEN, id)
    # Сравнение постоянного времени: обычное != утекает длину совпавшего
    # префикса и позволяет подбирать подпись по одному символу.
    if not hmac.compare_digest(expected, token):
        logger.warning("SUB_PROXY_API_BAD_TOKEN: id=%s token=%s", id, token[:8])
        return JSONResponse({"error": "not_found"}, status_code=404)

    target = await _resolve_for_telegram_id(id)
    if target:
        logger.info("SUB_PROXY_REDIRECT_API: id=%s -> %s", id, _redact_target(target))
        return RedirectResponse(target, status_code=302)
    return JSONResponse({"error": "not_found"}, status_code=404)
