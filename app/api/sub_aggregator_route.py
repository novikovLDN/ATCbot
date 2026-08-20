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
import time
from typing import Optional, Tuple

import httpx
from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import PlainTextResponse

import config
import database

logger = logging.getLogger(__name__)

router = APIRouter()

# HTTP-клиент — переиспользуем keep-alive пул между запросами.
_client: Optional[httpx.AsyncClient] = None

# Разрешённые токены — совпадает с sub_aggregator.py: 4–128 [A-Za-z0-9_-]
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")

UPSTREAM_TIMEOUT = 5.0  # сек

# ── In-memory cache ─────────────────────────────────────────────────
# Кеш: token → (expires_ts, body_b64, headers_dict).
# TTL 60 сек — компромисс между «свежо» и «не жрать панель».
# Инвалидация: bot.sub_aggregator.invalidate(token) → чистит запись из
# этого dict через POST /a/_invalidate/{token} с internal-secret.
# Локальный dict — один Railway worker, синхронизация не нужна; при
# масштабировании на N воркеров перейдём на Redis.
_CACHE_TTL = 60
_cache: dict[str, tuple[float, bytes, dict[str, str]]] = {}

# Клиенты подписываются с интервалом. profile-update-interval — часы;
# Happ/v2rayTun/Streisand дёргают апстрим раз в N часов. 1 час = свежие
# конфиги + минимум трафика.
_CLIENT_UPDATE_INTERVAL_HOURS = 1


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


def _cache_get(token: str) -> Optional[tuple[bytes, dict[str, str]]]:
    entry = _cache.get(token)
    if entry is None:
        return None
    expires_ts, body, headers = entry
    if time.monotonic() >= expires_ts:
        _cache.pop(token, None)
        return None
    return body, headers


def _cache_set(token: str, body: bytes, headers: dict[str, str]) -> None:
    _cache[token] = (time.monotonic() + _CACHE_TTL, body, headers)


def _brand_title() -> str:
    """profile-title fallback — из config если панель ничего не дала."""
    return getattr(config, "SUB_AGGREGATOR_BRAND_TITLE", None) or "Atlas Secure"


def _support_url() -> Optional[str]:
    """support-url fallback — из bot config, если панель ничего не отдала."""
    for attr in ("SUB_AGGREGATOR_SUPPORT_URL", "SUPPORT_URL", "SUPPORT_CONTACT_URL"):
        val = getattr(config, attr, "") or ""
        if val:
            return val
    return None


@router.get("/a/{token}")
async def aggregate(
    request: Request,
    token: str = Path(..., min_length=4, max_length=128),
) -> Response:
    """GET /a/{token} — merge premium+bypass subscriptions в одну.

    Fast path: 60-сек in-memory кэш. При изменении подписки (renew, top-up)
    бот зовёт invalidate → кеш чистится → следующий запрос перечитает.
    Клиенты (Happ/v2rayTun/Streisand) дёргают апстрим раз/час по
    profile-update-interval — свежие конфиги без вмешательства юзера.
    """
    if not _TOKEN_RE.match(token):
        return PlainTextResponse("Not found", status_code=404)

    # Fast path — hit кэша.
    cached = _cache_get(token)
    if cached is not None:
        body_bytes, headers = cached
        return Response(
            content=body_bytes,
            media_type="text/plain; charset=utf-8",
            headers={**headers, "x-cache": "hit"},
        )

    pair = await _load_pair(token)
    if not pair:
        return PlainTextResponse("Not found", status_code=404)

    if pair.get("status") == "revoked":
        stub_line = (
            "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443"
            "?type=tcp&security=none#Subscription%20revoked"
        )
        body_bytes = base64.b64encode(stub_line.encode())
        headers = {
            "subscription-userinfo": "upload=0; download=0; total=0; expire=0",
            "profile-title": _brand_title(),
            "profile-update-interval": str(_CLIENT_UPDATE_INTERVAL_HOURS),
        }
        # revoked-состояние кэшируем короче — юзер мог тут же восстановить
        # доступ через бота, не заставлять его ждать 60 сек.
        _cache[token] = (time.monotonic() + 10, body_bytes, headers)
        return Response(
            content=body_bytes,
            media_type="text/plain; charset=utf-8",
            headers={**headers, "x-cache": "miss"},
        )

    ua = request.headers.get("user-agent", "Aggregator/1.0")
    main_task = asyncio.create_task(_fetch_upstream(pair["main_sub_url"], ua))
    gb_task = asyncio.create_task(_fetch_upstream(pair["gb_sub_url"], ua))
    main_resp, gb_resp = await asyncio.gather(main_task, gb_task)

    if main_resp is None and gb_resp is None:
        logger.error("SUB_AGG_BOTH_UPSTREAMS_FAIL token=%s...", token[:6])
        return Response(status_code=503, headers={"retry-after": "30"}, content="Upstreams unavailable")

    main_lines = _decode_body(main_resp) if main_resp is not None and main_resp.status_code == 200 else []
    gb_lines = _decode_body(gb_resp) if gb_resp is not None and gb_resp.status_code == 200 else []

    if not main_lines and not gb_lines:
        logger.error(
            "SUB_AGG_EMPTY token=%s... main_status=%s gb_status=%s",
            token[:6],
            main_resp.status_code if main_resp else "None",
            gb_resp.status_code if gb_resp else "None",
        )
        return Response(status_code=503, headers={"retry-after": "30"}, content="Upstreams empty")

    # Merge: main-строки первыми, gb вторыми. Дубликаты по всей строке
    # убираем (порядок сохраняем — dict-order с Py3.7+ стабильный).
    seen: set[str] = set()
    merged: list[str] = []
    for line in main_lines + gb_lines:
        if line not in seen:
            seen.add(line)
            merged.append(line)

    body_bytes = base64.b64encode("\n".join(merged).encode())

    # ── Hybrid userinfo (traffic от gb, expire от main) ────────────
    main_h = main_resp.headers.get("subscription-userinfo", "") if main_resp else ""
    gb_h = gb_resp.headers.get("subscription-userinfo", "") if gb_resp else ""
    userinfo = _build_hybrid_userinfo(main_h, gb_h)

    # ── Полный набор profile-headers ────────────────────────────────
    # Happ/v2rayTun/Streisand читают эти хедеры и показывают юзеру:
    #   profile-title              → название подписки в UI приложения
    #   profile-update-interval    → раз в сколько ЧАСОВ клиент авто-refresh'ит
    #   profile-web-page-url       → «Веб-страница» кнопка в клиенте (личный кабинет)
    #   support-url                → «Поддержка» кнопка в клиенте (t.me/…)
    #   announce                   → важное сообщение от провайдера, если есть
    headers: dict[str, str] = {
        "subscription-userinfo": userinfo,
        "profile-update-interval": str(_CLIENT_UPDATE_INTERVAL_HOURS),
    }

    # profile-title: приоритет — от панели (уже брендированный per-user),
    # fallback — наш бренд.
    title = ""
    if main_resp:
        title = main_resp.headers.get("profile-title", "") or ""
    if not title and gb_resp:
        title = gb_resp.headers.get("profile-title", "") or ""
    headers["profile-title"] = title or _brand_title()

    # profile-web-page-url + support-url: если панель не дала — подставляем
    # наши дефолты, чтобы кнопки в клиенте не пустовали.
    for h in ("profile-web-page-url", "announce"):
        v = ""
        if main_resp:
            v = main_resp.headers.get(h, "") or ""
        if v:
            headers[h] = v
    # support-url — с fallback на bot конфиг.
    sup = ""
    if main_resp:
        sup = main_resp.headers.get("support-url", "") or ""
    if not sup and gb_resp:
        sup = gb_resp.headers.get("support-url", "") or ""
    if not sup:
        sup = _support_url() or ""
    if sup:
        headers["support-url"] = sup

    _cache_set(token, body_bytes, headers)

    logger.info(
        "SUB_AGG_OK token=%s... main_lines=%d gb_lines=%d merged=%d ua=%s",
        token[:6], len(main_lines), len(gb_lines), len(merged), ua[:40],
    )
    return Response(
        content=body_bytes,
        media_type="text/plain; charset=utf-8",
        headers={**headers, "x-cache": "miss"},
    )


# ── Internal endpoint для invalidate ────────────────────────────────
# Bot зовёт этот путь после mutation'ов подписки (renew, add_traffic,
# combo). Секрет захардкожен как SUB_AGGREGATOR_INTERNAL_SECRET в config;
# если пусто — endpoint принимает всё (для беты).
@router.post("/a/_invalidate/{token}")
async def invalidate_cache(
    request: Request,
    token: str = Path(..., min_length=4, max_length=128),
) -> Response:
    secret = getattr(config, "SUB_AGGREGATOR_INTERNAL_SECRET", "") or ""
    if secret:
        client_secret = request.headers.get("x-internal-secret", "")
        if client_secret != secret:
            return PlainTextResponse("Forbidden", status_code=403)
    if not _TOKEN_RE.match(token):
        return PlainTextResponse("Bad token", status_code=400)
    _cache.pop(token, None)
    return Response(content=b'{"ok":true}', media_type="application/json")


async def close() -> None:
    """Закрыть httpx клиент при graceful shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        finally:
            _client = None


__all__ = ["router", "close"]
