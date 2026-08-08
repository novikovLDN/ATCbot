"""Aggregated subscription — merge premium + whitelist в одну подписку.

Feature-flagged через config.AGG_ENABLED. Admin-only на UI-уровне.
Rollback: AGG_ENABLED=false → роут не монтируется, кнопка не показывается.
"""
from __future__ import annotations

import base64
import logging
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

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


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


async def build_aggregated_response(
    premium_uuid: str, whitelist_uuid: str,
) -> Optional[dict]:
    """Собрать объединённый ответ: dict с body_b64, profile_title_b64, userinfo.

    Возвращает None если Panel API недоступен для обоих UUID (упадём
    в 502). Если один из юзеров удалён — секция заменяется на fake-VLESS.
    """
    premium_user = await remnawave_api.get_user(premium_uuid)
    whitelist_user = await remnawave_api.get_user(whitelist_uuid)

    if premium_user is None and whitelist_user is None:
        return None

    # Пустой user (404) трактуем как истёкший — покажем fake-VLESS.
    premium_active = premium_user is not None and is_active(premium_user)
    whitelist_active = whitelist_user is not None and is_active(whitelist_user)

    lines: list[str] = []

    if premium_active:
        lines.extend(await fetch_subscription_lines(premium_user))
    else:
        lines.append(fake_vless(MSG_PREMIUM_EXPIRED))

    if whitelist_active:
        lines.extend(await fetch_subscription_lines(whitelist_user))
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

    return {
        "body_b64": body_b64,
        "profile_title_b64": profile_title_b64,
        "userinfo": userinfo,
        "premium_active": premium_active,
        "whitelist_active": whitelist_active,
        # Happ theme fields — эти заголовки читает Happ Manager v4+,
        # другие клиенты их молча игнорируют.
        "support_url": HAPP_SUPPORT_URL,
        "web_page_url": HAPP_WEB_PAGE_URL,
        "announce": announce,
    }
