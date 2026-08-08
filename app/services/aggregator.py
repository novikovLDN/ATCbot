"""Aggregated subscription — merge premium + whitelist в одну подписку.

Feature-flagged через config.AGG_ENABLED. Admin-only на UI-уровне.
Rollback: AGG_ENABLED=false → роут не монтируется, кнопка не показывается.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

import config
from app.services import remnawave_api

logger = logging.getLogger(__name__)

FAKE_UUID = "00000000-0000-0000-0000-000000000000"

# Готовые сообщения для fake-VLESS (отображаются как remark в клиенте).
MSG_PREMIUM_EXPIRED = "⚠️ Premium subscription ended — pay @atlas_suppbot"
MSG_WHITELIST_EXPIRED = "⚠️ Whitelist traffic ended — buy in @atlas_suppbot"
MSG_BOTH_EXPIRED = "⚠️ Both subscriptions ended — top up @atlas_suppbot"

# Happ-специфичные поля темы (headers Support-URL / Web-Page-Url / Announce
# понимает Happ Manager v4+). Остальные клиенты (v2rayN, Streisand) их
# игнорируют — не ломают.
HAPP_PROFILE_TITLE = "⭐ Atlas Combined"
HAPP_SUPPORT_URL = "https://t.me/atlas_suppbot"
HAPP_WEB_PAGE_URL = "https://t.me/atlassecure_bot"
HAPP_ANNOUNCE_ACTIVE = "🛡 Atlas Secure — доступ работает. Спасибо, что с нами!"
HAPP_ANNOUNCE_EXPIRED = (
    "⚠️ Одна из ваших подписок истекла. "
    "Продлите её в @atlassecure_bot, чтобы вернуть скорость."
)

_TIMEOUT = httpx.Timeout(connect=3.0, read=6.0, write=3.0, pool=3.0)

# In-memory кэш финального ответа: (premium_uuid, whitelist_uuid) →
# (timestamp, result). TTL 30s — Happ/v2rayN опрашивают подписку раз в
# несколько минут (Profile-Update-Interval: 24), так что 30с ok:
# юзер получает свежую версию, а частые повторные запросы (например
# сразу после клика Refresh) отдаются мгновенно из памяти.
_RESPONSE_CACHE_TTL = 30.0
_response_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _cache_get(premium_uuid: str, whitelist_uuid: str) -> Optional[dict]:
    key = (premium_uuid, whitelist_uuid)
    entry = _response_cache.get(key)
    if entry is None:
        return None
    ts, result = entry
    if time.monotonic() - ts > _RESPONSE_CACHE_TTL:
        _response_cache.pop(key, None)
        return None
    return result


def _cache_put(premium_uuid: str, whitelist_uuid: str, result: dict) -> None:
    _response_cache[(premium_uuid, whitelist_uuid)] = (time.monotonic(), result)


def clear_cache() -> None:
    """Reset in-memory response cache (для тестов и /agg regenerate flow)."""
    _response_cache.clear()


def fake_vless(remark: str) -> str:
    """Non-functional VLESS с сообщением в remark.

    VPN-клиенты (Happ, v2rayN, Streisand) показывают текст после `#` как
    имя сервера в списке. Юзер видит красную строку — понимает, что
    подписка/трафик кончились и куда идти пополнять.
    """
    return f"vless://{FAKE_UUID}@127.0.0.1:1?type=tcp&security=none#{quote(remark)}"


def _parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    """Panel возвращает ISO-8601 c 'Z' или без. Приводим к UTC-aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def is_active(user_data: dict) -> bool:
    """Пользователь active если ACTIVE + не превышен лимит трафика + не истёк.

    trafficLimitBytes == 0 → безлимит (не проверяем used < limit).
    """
    if not user_data:
        return False
    status = user_data.get("status")
    if status != "ACTIVE":
        return False

    used = user_data.get("usedTrafficBytes", 0) or 0
    limit = user_data.get("trafficLimitBytes", 0) or 0
    if limit > 0 and used >= limit:
        return False

    exp = _parse_iso_utc(user_data.get("expireAt"))
    if exp is not None and exp < datetime.now(timezone.utc):
        return False

    return True


async def fetch_subscription_lines(user_data: dict) -> list[str]:
    """Скачать содержимое подписки юзера и вернуть список vless://-строк.

    Panel отдаёт subscriptionUrl напрямую в user_data. Ходим по нему
    как обычный клиент — тот же формат base64/plain что и раньше.
    Пустой список если что-то пошло не так (упало / формат неожиданный).
    """
    sub_url = user_data.get("subscriptionUrl") or ""
    if not sub_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(sub_url)
        if resp.status_code >= 400:
            logger.warning(
                "agg fetch_sub HTTP_%s url=%s", resp.status_code, sub_url[:100],
            )
            return []
        body = resp.text.strip()
    except httpx.TimeoutException:
        logger.error("agg fetch_sub TIMEOUT url=%s", sub_url[:100])
        return []
    except Exception as e:
        logger.error("agg fetch_sub ERROR url=%s: %s", sub_url[:100], e)
        return []

    if not body:
        return []
    # Может прийти base64 или уже plain-text со списком vless://
    try:
        decoded = base64.b64decode(body, validate=False).decode("utf-8", errors="ignore")
        if "vless://" in decoded or "ss://" in decoded or "trojan://" in decoded:
            body = decoded
    except Exception:
        pass  # не base64 — используем как есть

    return [line.strip() for line in body.splitlines() if line.strip()]


def build_userinfo(premium: dict, whitelist: dict) -> str:
    """Subscription-Userinfo header (Sing-box/v2rayN spec).

    Трафик показываем по whitelist (там metered), expire — ближайший
    из двух подписок (чтобы клиент показал корректную дату отключения).
    """
    used = whitelist.get("usedTrafficBytes", 0) or 0
    limit = whitelist.get("trafficLimitBytes", 0) or 0

    p_exp = _parse_iso_utc(premium.get("expireAt"))
    w_exp = _parse_iso_utc(whitelist.get("expireAt"))
    nearest = None
    for exp in (p_exp, w_exp):
        if exp is None:
            continue
        if nearest is None or exp < nearest:
            nearest = exp
    exp_ts = int(nearest.timestamp()) if nearest else 0

    return f"upload=0; download={used}; total={limit}; expire={exp_ts}"


async def _safe_get_user(uuid: str) -> Optional[dict]:
    """Обёртка для gather с return_exceptions=True — превращаем ошибку в None."""
    try:
        return await remnawave_api.get_user(uuid)
    except Exception as e:
        logger.warning("agg _safe_get_user uuid=%s err=%s", uuid[:8], e)
        return None


async def _fetch_or_empty(user_data: Optional[dict], active: bool) -> list[str]:
    if not active or user_data is None:
        return []
    try:
        return await fetch_subscription_lines(user_data)
    except Exception as e:
        logger.warning("agg _fetch_or_empty err=%s", e)
        return []


async def build_aggregated_response(
    premium_uuid: str, whitelist_uuid: str,
) -> Optional[dict]:
    """Собрать объединённый ответ: dict с body_b64, profile_title_b64, userinfo.

    Возвращает None если Panel API недоступен для обоих UUID (упадём
    в 502). Если один из юзеров удалён — секция заменяется на fake-VLESS.

    Кэш 30s in-memory — повторные запросы в этом окне возвращаются
    мгновенно из памяти. Параллельные HTTP-вызовы к Panel (2 get_user
    одновременно + 2 fetch_subscription одновременно) — сокращает
    total latency почти вдвое.
    """
    # 1. Cache hit?
    cached = _cache_get(premium_uuid, whitelist_uuid)
    if cached is not None:
        return cached

    # 2. Параллельно тянем обоих юзеров.
    premium_user, whitelist_user = await asyncio.gather(
        _safe_get_user(premium_uuid),
        _safe_get_user(whitelist_uuid),
    )

    if premium_user is None and whitelist_user is None:
        return None

    # Пустой user (404) трактуем как истёкший — покажем fake-VLESS.
    premium_active = premium_user is not None and is_active(premium_user)
    whitelist_active = whitelist_user is not None and is_active(whitelist_user)

    # 3. Параллельно тянем содержимое обеих подписок (только для активных).
    premium_lines, whitelist_lines = await asyncio.gather(
        _fetch_or_empty(premium_user, premium_active),
        _fetch_or_empty(whitelist_user, whitelist_active),
    )

    lines: list[str] = []

    if premium_active:
        lines.extend(premium_lines)
    else:
        lines.append(fake_vless(MSG_PREMIUM_EXPIRED))

    if whitelist_active:
        lines.extend(whitelist_lines)
    else:
        lines.append(fake_vless(MSG_WHITELIST_EXPIRED))

    if not premium_active and not whitelist_active:
        # Оба истекли — добавим общий summary-remark
        lines.append(fake_vless(MSG_BOTH_EXPIRED))

    body = "\n".join(lines)
    body_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")

    profile_title_b64 = base64.b64encode(HAPP_PROFILE_TITLE.encode("utf-8")).decode("ascii")

    userinfo = build_userinfo(premium_user or {}, whitelist_user or {})

    # Announce меняется по состоянию подписок: если что-то истекло —
    # показываем warning с call-to-action.
    announce = HAPP_ANNOUNCE_ACTIVE if (premium_active and whitelist_active) else HAPP_ANNOUNCE_EXPIRED
    # HTTP headers по RFC 7230 — latin-1 only. Кириллицу/emoji нельзя
    # в raw-виде — Starlette/uvicorn падают при encode с UnicodeEncodeError
    # → 500. Happ Manager принимает "base64:xxx" префикс (как у Profile-Title)
    # и корректно декодирует UTF-8 обратно.
    announce_b64 = base64.b64encode(announce.encode("utf-8")).decode("ascii")

    result = {
        "body_b64": body_b64,
        "profile_title_b64": profile_title_b64,
        "userinfo": userinfo,
        "premium_active": premium_active,
        "whitelist_active": whitelist_active,
        # Happ theme fields — эти заголовки читает Happ Manager v4+,
        # другие клиенты их молча игнорируют.
        "support_url": HAPP_SUPPORT_URL,          # ASCII URL
        "web_page_url": HAPP_WEB_PAGE_URL,        # ASCII URL
        "announce_b64": announce_b64,             # base64 UTF-8
    }
    _cache_put(premium_uuid, whitelist_uuid, result)
    return result
