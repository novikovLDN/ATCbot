"""
Embedded sub-aggregator — GET /a/{token}.

Проще предыдущего плана с WG/streams/Docker: агрегатор живёт прямо
внутри бота (FastAPI), RF-1 nginx делает обычный HTTPS reverse-proxy
на api.atlassecure.ru/a/{token}.

Флоу запроса:
  1. Клиент/браузер → https://subscription.palantirdns.uk/<token>
  2. RF-1 nginx → https://api.atlassecure.ru/a/<token>  (простой proxy_pass)
  3. Bot FastAPI → SELECT из sub_pairs → 2 параллельных httpx GET к
     Remnawave панели → merge base64 → response

Никаких докеров, стрим-сервисов, WG. RF-1 = TLS + rate-limit + proxy.

Кэша нет (SWR/Redis добавим позже, если понадобится). Сейчас каждый
запрос идёт в панель — для беты одного админа это нормально.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Optional, Tuple

import httpx
from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import PlainTextResponse

import database

logger = logging.getLogger(__name__)

router = APIRouter()

# HTTP-клиент — переиспользуем keep-alive пул между запросами.
_client: Optional[httpx.AsyncClient] = None

# Разрешённые токены — совпадает с sub_aggregator.py: 4–128 [A-Za-z0-9_-]
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")

UPSTREAM_TIMEOUT = 5.0  # сек


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=UPSTREAM_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
        )
    return _client


async def _fetch_upstream(url: str, user_agent: str) -> Optional[httpx.Response]:
    """Единичный GET апстрима с форвардом UA. None при таймауте/ошибке."""
    try:
        client = _get_client()
        return await client.get(url, headers={"User-Agent": user_agent})
    except Exception as e:
        logger.warning("SUB_AGG_UPSTREAM_FAIL url=%s err=%s", url[:60], e)
        return None


def _decode_body(resp: httpx.Response) -> list[str]:
    """Разложить тело апстрима на строки vless://... — base64 или plaintext.
    Пустые строки убираем."""
    raw = resp.text or ""
    stripped = raw.strip()
    # Пробуем base64 — если декодит без ошибок И начинается на vless://
    try:
        decoded = base64.b64decode(stripped, validate=False).decode("utf-8", errors="replace")
        if "vless://" in decoded or "vmess://" in decoded or "ss://" in decoded or "trojan://" in decoded:
            return [ln.strip() for ln in decoded.splitlines() if ln.strip()]
    except Exception:
        pass
    # Fallback: считаем plaintext-списком строк
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


def _parse_userinfo(header: str) -> dict:
    """subscription-userinfo: upload=1; download=2; total=3; expire=1234 → dict."""
    out: dict[str, int] = {}
    for part in (header or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        try:
            out[k.strip()] = int(v.strip())
        except (TypeError, ValueError):
            pass
    return out


def _build_hybrid_userinfo(main_h: str, gb_h: str) -> str:
    """Traffic (upload/download/total) от gb, expire от main. Отсутствующие → 0."""
    main = _parse_userinfo(main_h)
    gb = _parse_userinfo(gb_h)
    upload = gb.get("upload", 0)
    download = gb.get("download", 0)
    total = gb.get("total", 0)
    expire = main.get("expire", gb.get("expire", 0))
    return f"upload={upload}; download={download}; total={total}; expire={expire}"


async def _load_pair(token: str) -> Optional[dict]:
    pool = await database.get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token, main_sub_url, gb_sub_url, status FROM sub_pairs WHERE token = $1",
            token,
        )
    return dict(row) if row else None


@router.get("/a/{token}")
async def aggregate(
    request: Request,
    token: str = Path(..., min_length=4, max_length=128),
) -> Response:
    """GET /a/{token} — merge premium+bypass subscriptions в одну."""
    if not _TOKEN_RE.match(token):
        return PlainTextResponse("Not found", status_code=404)

    pair = await _load_pair(token)
    if not pair:
        return PlainTextResponse("Not found", status_code=404)

    if pair.get("status") == "revoked":
        # Stub — панель показывает remark, юзер знает что подписка отозвана.
        stub_line = (
            "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
            "?type=tcp&security=none#Subscription%20revoked"
        )
        body = base64.b64encode(stub_line.encode()).decode()
        return PlainTextResponse(
            body,
            headers={"subscription-userinfo": "upload=0; download=0; total=0; expire=0"},
        )

    ua = request.headers.get("user-agent", "Aggregator/1.0")
    main_task = asyncio.create_task(_fetch_upstream(pair["main_sub_url"], ua))
    gb_task = asyncio.create_task(_fetch_upstream(pair["gb_sub_url"], ua))
    main_resp, gb_resp = await asyncio.gather(main_task, gb_task, return_exceptions=False)

    # Оба апстрима упали → 503, retry-after 30.
    if main_resp is None and gb_resp is None:
        logger.error("SUB_AGG_BOTH_UPSTREAMS_FAIL token=%s...", token[:6])
        return Response(status_code=503, headers={"retry-after": "30"}, content="Upstreams unavailable")

    main_lines = _decode_body(main_resp) if main_resp is not None and main_resp.status_code == 200 else []
    gb_lines = _decode_body(gb_resp) if gb_resp is not None and gb_resp.status_code == 200 else []

    if not main_lines and not gb_lines:
        # Оба ответили не-200 или пустышкой.
        logger.error(
            "SUB_AGG_EMPTY token=%s... main_status=%s gb_status=%s",
            token[:6],
            main_resp.status_code if main_resp else "None",
            gb_resp.status_code if gb_resp else "None",
        )
        return Response(status_code=503, headers={"retry-after": "30"}, content="Upstreams empty")

    # Merge: main-строки первыми, потом gb. Точные дубликаты убираем.
    seen: set[str] = set()
    merged: list[str] = []
    for line in main_lines + gb_lines:
        if line not in seen:
            seen.add(line)
            merged.append(line)

    body = base64.b64encode("\n".join(merged).encode()).decode()

    # Hybrid userinfo: expire от main, traffic от gb.
    main_h = main_resp.headers.get("subscription-userinfo", "") if main_resp else ""
    gb_h = gb_resp.headers.get("subscription-userinfo", "") if gb_resp else ""
    userinfo = _build_hybrid_userinfo(main_h, gb_h)

    # Копируем profile-* заголовки от main (title, update-interval, web-page-url).
    passthrough_headers: dict[str, str] = {"subscription-userinfo": userinfo}
    if main_resp:
        for h in ("profile-title", "profile-update-interval", "profile-web-page-url", "support-url", "announce"):
            v = main_resp.headers.get(h)
            if v:
                passthrough_headers[h] = v

    logger.info(
        "SUB_AGG_OK token=%s... main_lines=%d gb_lines=%d merged=%d",
        token[:6], len(main_lines), len(gb_lines), len(merged),
    )
    return PlainTextResponse(body, headers=passthrough_headers)


async def close() -> None:
    """Закрыть httpx клиент при graceful shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        finally:
            _client = None


__all__ = ["router", "close"]
