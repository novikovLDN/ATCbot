"""FastAPI route: GET /agg/{combined_token} — aggregated subscription.

Feature-flagged: монтируется только когда config.AGG_ENABLED=true.
Rollback — снять флаг и рестартнуть.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import PlainTextResponse

import database.aggregator as agg_db
from app.services.aggregator import build_aggregated_response

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory rate limit: max 20 запросов/60сек на один токен.
# Клиенты подписки опрашивают регулярно, но 20/мин это перебор.
# Для продакшена/multi-instance стоило бы Redis, для beta admin-only норм.
_RATE_LIMIT = 20
_RATE_WINDOW = 60.0
_rate_store: dict[str, list[float]] = defaultdict(list)


def _rate_check(token: str) -> bool:
    """True если запрос в пределах лимита, False → должны вернуть 429."""
    now = time.time()
    calls = _rate_store[token]
    _rate_store[token] = [t for t in calls if now - t < _RATE_WINDOW]
    if len(_rate_store[token]) >= _RATE_LIMIT:
        return False
    _rate_store[token].append(now)
    return True


def _is_valid_token(token: str) -> bool:
    """32 hex-символа = 128 бит энтропии (secrets.token_hex(16))."""
    return len(token) == 32 and all(c in "0123456789abcdef" for c in token)


@router.get("/agg/health")
async def agg_health():
    """Простой health-check для aggregator. DB опционально."""
    checks = {"db": False}
    try:
        from database.core import get_pool
        pool = await get_pool()
        if pool is not None:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["db"] = True
    except Exception as e:
        logger.warning("agg health db fail: %s", e)
    status_code = 200 if checks["db"] else 503
    return PlainTextResponse(
        content=("ok" if checks["db"] else "degraded"),
        status_code=status_code,
    )


@router.get("/agg/{combined_token}")
async def aggregated_subscription(
    combined_token: str = Path(..., min_length=32, max_length=32),
):
    """Aggregated subscription endpoint.

    - 404 если токен невалиден / не найден
    - 429 если превышен rate limit (20/мин на токен)
    - 502 если Panel API недоступен для обоих UUID
    - 200 body — base64-encoded newline-separated список vless://
    """
    if not _is_valid_token(combined_token):
        raise HTTPException(status_code=404, detail="not_found")

    if not _rate_check(combined_token):
        raise HTTPException(status_code=429, detail="too_many_requests")

    record = await agg_db.get_by_token(combined_token)
    if record is None:
        raise HTTPException(status_code=404, detail="not_found")

    try:
        result = await build_aggregated_response(
            premium_uuid=record["premium_uuid"],
            whitelist_uuid=record["whitelist_uuid"],
        )
    except Exception as e:
        logger.exception(
            "AGG_BUILD_FAIL token=%s tg=%s err=%s",
            combined_token[:8], record["telegram_id"], e,
        )
        raise HTTPException(status_code=502, detail=f"build_failed: {type(e).__name__}")
    if result is None:
        raise HTTPException(status_code=502, detail="panel_api_unavailable")

    # Метрика доступа — не блокирует ответ, fail-safe внутри.
    await agg_db.touch_access(combined_token)

    logger.info(
        "AGG_SERVED token=%s tg=%s premium_active=%s wl_active=%s",
        combined_token[:8], record["telegram_id"],
        result["premium_active"], result["whitelist_active"],
    )

    # ВНИМАНИЕ про headers: HTTP по RFC 7230 разрешает только latin-1
    # в значениях. Все Unicode-фрагменты передаём в base64:xxx-обёртке
    # (Happ Manager v4+ декодирует). Иначе Starlette кинет
    # UnicodeEncodeError и вернёт 500.
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Update-Interval": "24",
        "Profile-Title": f"base64:{result['profile_title_b64']}",
        "Subscription-Userinfo": result["userinfo"],
        # Happ Manager v4+ theme headers (другие клиенты игнорируют).
        "Support-URL": result["support_url"],
        "Profile-Web-Page-Url": result["web_page_url"],
        "Announce": f"base64:{result['announce_b64']}",
        # Дублируем title в Content-Disposition — некоторые клиенты
        # (Streisand, v2rayN) достают имя профиля именно оттуда.
        "Content-Disposition": 'inline; filename="atlas-combined.txt"',
    }
    return PlainTextResponse(content=result["body_b64"], headers=headers)
