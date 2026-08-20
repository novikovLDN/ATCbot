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
import json
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote as url_quote

import httpx
from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

import config
import database

logger = logging.getLogger(__name__)

router = APIRouter()

# HTTP-клиент — переиспользуем keep-alive пул между запросами.
_client: Optional[httpx.AsyncClient] = None

# Разрешённые токены — совпадает с sub_aggregator.py: 4–128 [A-Za-z0-9_-]
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")

UPSTREAM_TIMEOUT = 5.0  # сек

# ── Production cache tiers ─────────────────────────────────────────
# TWO-TIER caching + singleflight + LRU bound.
#
# FRESH  (15 сек)  — hit → мгновенно из памяти. Не идём в панель.
# STALE  (24 часа) — если fresh expired И апстрим упал, отдаём это.
#                    Панель down → юзер видит последнее известное состояние,
#                    а не 503. Cover для нестабильной панели.
# PAIR   (1 час)   — mapping token → (main_url, gb_url, main_uuid, gb_uuid,
#                    status). DB SELECT только раз/час на token, не на
#                    каждый request. При invalidate() чистим и его.
#
# LRU cap = 20 000 записей — при 20k активных подписок каждая ~30 KB body =
# ~600 MB. Bound через OrderedDict.move_to_end + popitem(last=False) на
# переполнении.
#
# Singleflight (in-flight dict) — параллельные запросы одного token
# ждут одного и того же upstream fetch. Защищает панель от стадных
# запросов при cold-start (rest бота, TTL expire всех сразу).
FRESH_TTL = 15
STALE_TTL = 24 * 3600
PAIR_TTL = 3600
MAX_CACHE_ENTRIES = 20_000
MAX_PAIR_ENTRIES = 40_000

# fresh_until, stale_until, body, headers — фрешность отделена от stale-версии.
# После fresh_until истечения — запись всё ещё в кеше как «stale-copy».
_cache: "OrderedDict[str, tuple[float, float, bytes, dict[str, str]]]" = OrderedDict()

# pair-mapping cache: token → (expires_ts, pair-dict|None). None = negative cache
# (token не найден в БД) — избавляет от повторных DB-запросов при spamm'е случайных токенов.
_pair_cache: "OrderedDict[str, tuple[float, Optional[dict]]]" = OrderedDict()
NEG_PAIR_TTL = 60  # negative кеш (not-found) короче — вдруг только-только создался

# Singleflight: token → Future с результатом текущего upstream fetch.
# Второй запрос на тот же token во время активного fetch — ждёт первого.
_inflight: Dict[str, "asyncio.Future"] = {}

# Метрики — периодически логируем + видно через /a/_metrics
_metrics: Dict[str, int] = {
    "hits": 0, "misses": 0, "stale": 0,
    "upstream_ok": 0, "upstream_fail": 0,
    "singleflight_wait": 0,
    "not_found": 0, "revoked": 0,
}


def _cache_get(token: str) -> tuple[Optional[bytes], Optional[dict], str]:
    """Fetch cached entry.
    Returns (body, headers, state):
      state = 'fresh'   → отдать мгновенно
      state = 'stale'   → отдать если апстрим упал, иначе refresh
      state = 'miss'    → в кеше нет вообще
    """
    entry = _cache.get(token)
    if entry is None:
        return None, None, "miss"
    fresh_until, stale_until, body, headers = entry
    now = time.monotonic()
    if now < fresh_until:
        _cache.move_to_end(token)  # LRU touch
        return body, headers, "fresh"
    if now < stale_until:
        return body, headers, "stale"
    # Полностью expired — удаляем.
    _cache.pop(token, None)
    return None, None, "miss"


def _cache_set(token: str, body: bytes, headers: dict) -> None:
    """Set both fresh + stale copy. LRU-evict если превысили cap."""
    now = time.monotonic()
    _cache[token] = (now + FRESH_TTL, now + STALE_TTL, body, dict(headers))
    _cache.move_to_end(token)
    # Bound size.
    while len(_cache) > MAX_CACHE_ENTRIES:
        _cache.popitem(last=False)


def _pair_get(token: str) -> tuple[Optional[dict], bool]:
    """(pair_dict, is_cached).
      pair_dict=None + is_cached=True → negative кеш (нет в БД)
      pair_dict=dict + is_cached=True → положительный хит
      is_cached=False → нет в кеше, надо в БД"""
    entry = _pair_cache.get(token)
    if entry is None:
        return None, False
    expires_ts, pair = entry
    if time.monotonic() >= expires_ts:
        _pair_cache.pop(token, None)
        return None, False
    _pair_cache.move_to_end(token)
    return pair, True


def _pair_set(token: str, pair: Optional[dict]) -> None:
    ttl = PAIR_TTL if pair else NEG_PAIR_TTL
    _pair_cache[token] = (time.monotonic() + ttl, pair)
    _pair_cache.move_to_end(token)
    while len(_pair_cache) > MAX_PAIR_ENTRIES:
        _pair_cache.popitem(last=False)


def clear_cache(token: Optional[str] = None) -> None:
    """Прямой in-process сброс кеша (fresh+stale+pair) по token.
    None → полный wipe (админский рычаг). Экспортится для sub_aggregator.py:invalidate."""
    if token is None:
        _cache.clear()
        _pair_cache.clear()
        return
    _cache.pop(token, None)
    _pair_cache.pop(token, None)

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
    """Прод-путь: сначала pair-кеш (1 час), потом DB. Negative-кеш (60с)
    защищает от token-flood. При mutation'ах — clear_cache чистит и это."""
    cached, is_cached = _pair_get(token)
    if is_cached:
        return cached  # dict | None
    pool = await database.get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token, main_sub_url, gb_sub_url, status FROM sub_pairs WHERE token = $1",
            token,
        )
    pair = dict(row) if row else None
    _pair_set(token, pair)
    return pair


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


# ── User-Agent classification ───────────────────────────────────────
# Один URL — два ответа. Клиенты (Happ/v2rayNG/…) получают base64
# подписку, браузеры (Chrome/Safari/Firefox) — HTML-страницу с кнопками
# «Открыть в Happ» / «Открыть в Incy» / прочих клиентов + копируемый ключ.
#
# Правило: только явно распознанный браузер → HTML. Всё остальное (клиенты,
# curl, боты, unknown UA) → raw base64. Consequences of misclassification:
# новый клиент неопознанный → получит base64 (работает). Скрапер под
# Chrome UA → получит HTML (не страшно).
_CLIENT_UA_RE = re.compile(
    r"\b(happ|v2rayng|v2raytun|v2box|streisand|shadowrocket|foxray|hiddify|"
    r"nekoray|nekobox|clash|sing-?box|stash|shadowlink|oneclick)\b",
    re.IGNORECASE,
)
_BROWSER_UA_RE = re.compile(
    r"\b(mozilla|chrome|safari|firefox|edg|opera|opr/)\b",
    re.IGNORECASE,
)


def _wants_html(ua: str) -> bool:
    if not ua:
        return False
    if _CLIENT_UA_RE.search(ua):
        return False
    if _BROWSER_UA_RE.search(ua):
        return True
    return False


# ── HTML rendering ──────────────────────────────────────────────────
# Стиль совпадает с deeplink_redirect.py (light палитра, SF/Inter, минимал)
# чтобы юзер видел один и тот же язык дизайна во всех бот-страницах.

def _fmt_bytes(n: int) -> str:
    """1234567890 → '1.15 ГБ'. Компактно для показа юзеру."""
    if n <= 0:
        return "0"
    for unit, div in (("ТБ", 1024**4), ("ГБ", 1024**3), ("МБ", 1024**2), ("КБ", 1024)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{n} Б"


def _fmt_expire(ts: int) -> Optional[str]:
    """Unix ts → '15.10.2026' или None."""
    if not ts or ts <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%d.%m.%Y")
    except (ValueError, OSError, OverflowError):
        return None


def _deeplink_base() -> str:
    """https://api.atlassecure.ru — берём из WEBHOOK_URL. Endpoint /open/{client}
    делает crypt-sealing для Happ/Incy и редирект в приложение."""
    webhook = getattr(config, "WEBHOOK_URL", "") or ""
    if not webhook:
        return ""
    m = re.match(r"^(https?://[^/]+)", webhook)
    return m.group(1) if m else ""


def _render_sub_html(*, token: str, sub_url: str, headers: dict) -> str:
    """Минимальная splash-страница: бренд + 2 кнопки Happ/Incy.

    Никакой статистики трафика / expire / других клиентов — юзеру не нужно
    видеть детали подписки в браузере (это может ввести в заблуждение:
    какая именно подписка? актуально ли?). Клик по кнопке → deep-link →
    приложение импортирует агрегатор-ссылку и всё сразу видно там.
    """
    brand = html_escape(_brand_title())
    support = _support_url() or ""
    support_esc = html_escape(support, quote=True)

    # Deep-links. Happ/Incy — через /open/{client} (crypt-sealing серверный).
    base = _deeplink_base()
    q = url_quote(sub_url, safe='')
    happ_href = f"{base}/open/happ?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}"
    incy_href = f"{base}/open/incy?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}"

    happ_href_esc = html_escape(happ_href, quote=True)
    incy_href_esc = html_escape(incy_href, quote=True)

    support_row = (
        f'<a class="support" href="{support_esc}" target="_blank" rel="noopener">Поддержка</a>'
        if support else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="theme-color" content="#f6f7f9">
<meta name="robots" content="noindex,nofollow">
<title>{brand}</title>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0;
    background: #f6f7f9; color: #111;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 'Inter', 'Helvetica Neue', sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  body {{
    min-height: 100vh;
    display: flex; flex-direction: column; align-items: center;
    justify-content: center;
    padding: 40px 20px;
  }}
  .wrap {{ width: 100%; max-width: 400px; text-align: center; }}

  .brand {{
    font-size: 28px; font-weight: 700; letter-spacing: -0.02em;
    margin: 0 0 8px;
  }}
  p.lead {{ font-size: 14px; line-height: 1.45; color: #666;
            margin: 0 0 32px; }}

  .btn {{
    display: flex; align-items: center; justify-content: center;
    width: 100%; padding: 16px 22px;
    background: #111; color: #fff;
    border-radius: 12px;
    text-decoration: none;
    font-size: 15px; font-weight: 600; letter-spacing: -0.01em;
    transition: transform 80ms ease, background 80ms ease;
    border: none; cursor: pointer;
    margin-bottom: 12px;
  }}
  .btn:active {{ transform: scale(0.98); background: #000; }}
  .btn.secondary {{
    background: #fff; color: #111;
    border: 1px solid #e1e4e8;
  }}
  .btn.secondary:active {{ background: #f0f2f5; }}

  .footer {{
    margin-top: 40px;
    display: flex; align-items: center; justify-content: center;
    gap: 16px;
    font-size: 11px; letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #9aa1ab;
  }}
  .support {{ color: #9aa1ab; text-decoration: none; }}
  .support:hover {{ color: #111; }}

  @media (prefers-color-scheme: dark) {{
    html, body {{ background: #0f1720; color: #f5f5f2; }}
    .brand {{ color: #ffffff; }}
    p.lead {{ color: #9aa1ab; }}
    .btn {{ background: #ffffff; color: #0f1720; }}
    .btn:active {{ background: #f5f5f2; }}
    .btn.secondary {{ background: #1f2937; color: #ffffff; border-color: #2a3441; }}
    .btn.secondary:active {{ background: #2a3441; }}
    .footer {{ color: #6b7280; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">{brand}</div>
  <p class="lead">Откройте подписку в приложении</p>

  <a class="btn" href="{happ_href_esc}">Открыть в Happ</a>
  <a class="btn secondary" href="{incy_href_esc}">Открыть в Incy</a>

  <div class="footer">
    <span>{brand}</span>
    {support_row}
  </div>
</div>
</body>
</html>"""


async def _do_fetch_and_merge(token: str, pair: dict, ua: str) -> tuple[Optional[bytes], Optional[dict]]:
    """Реальная работа: 2 параллельных upstream GET → merge → (body, headers).
    Возвращает (None, None) если ОБА апстрима не отдали ни одной строки —
    caller решит, отдавать stale или 503."""
    main_task = asyncio.create_task(_fetch_upstream(pair["main_sub_url"], ua))
    gb_task = asyncio.create_task(_fetch_upstream(pair["gb_sub_url"], ua))
    main_resp, gb_resp = await asyncio.gather(main_task, gb_task)

    main_ok = main_resp is not None and main_resp.status_code == 200
    gb_ok = gb_resp is not None and gb_resp.status_code == 200

    if main_ok:
        _metrics["upstream_ok"] += 1
    else:
        _metrics["upstream_fail"] += 1
    if gb_ok:
        _metrics["upstream_ok"] += 1
    else:
        _metrics["upstream_fail"] += 1

    main_lines = _decode_body(main_resp) if main_ok else []
    gb_lines = _decode_body(gb_resp) if gb_ok else []

    if not main_lines and not gb_lines:
        logger.error(
            "SUB_AGG_EMPTY token=%s... main_status=%s gb_status=%s",
            token[:6],
            main_resp.status_code if main_resp else "None",
            gb_resp.status_code if gb_resp else "None",
        )
        return None, None

    # Merge — main первыми, gb вторыми, dedupe.
    seen: set[str] = set()
    merged: list[str] = []
    for line in main_lines + gb_lines:
        if line not in seen:
            seen.add(line)
            merged.append(line)
    body_bytes = base64.b64encode("\n".join(merged).encode())

    main_h = main_resp.headers.get("subscription-userinfo", "") if main_resp else ""
    gb_h = gb_resp.headers.get("subscription-userinfo", "") if gb_resp else ""
    userinfo = _build_hybrid_userinfo(main_h, gb_h)

    headers: dict[str, str] = {
        "subscription-userinfo": userinfo,
        "profile-update-interval": str(_CLIENT_UPDATE_INTERVAL_HOURS),
    }
    title = ""
    if main_resp:
        title = main_resp.headers.get("profile-title", "") or ""
    if not title and gb_resp:
        title = gb_resp.headers.get("profile-title", "") or ""
    headers["profile-title"] = title or _brand_title()

    for h in ("profile-web-page-url", "announce"):
        v = ""
        if main_resp:
            v = main_resp.headers.get(h, "") or ""
        if v:
            headers[h] = v
    sup = ""
    if main_resp:
        sup = main_resp.headers.get("support-url", "") or ""
    if not sup and gb_resp:
        sup = gb_resp.headers.get("support-url", "") or ""
    if not sup:
        sup = _support_url() or ""
    if sup:
        headers["support-url"] = sup

    logger.info(
        "SUB_AGG_OK token=%s... main_lines=%d gb_lines=%d merged=%d ua=%s",
        token[:6], len(main_lines), len(gb_lines), len(merged), ua[:40],
    )
    return body_bytes, headers


async def _fetch_singleflight(token: str, pair: dict, ua: str) -> tuple[Optional[bytes], Optional[dict]]:
    """Схлопывает параллельные запросы одного token в один upstream fetch.

    Стадные запросы (cold-start, TTL-expire бума в 5:00 утра) — 100 клиентов
    приходят одновременно → делаем 1 пару upstream GET → все 100 разделяют
    результат. Панель не получает N-кратной нагрузки.

    In-flight dict хранит Future текущего fetch. Второй запрос делает await
    того же Future.
    """
    fut = _inflight.get(token)
    if fut is not None:
        _metrics["singleflight_wait"] += 1
        try:
            return await fut
        except Exception:
            # Fallthrough — если leader упал, пусть follower попробует свой.
            pass

    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    _inflight[token] = fut
    try:
        result = await _do_fetch_and_merge(token, pair, ua)
        if not fut.done():
            fut.set_result(result)
        return result
    except Exception as e:
        if not fut.done():
            fut.set_exception(e)
        raise
    finally:
        _inflight.pop(token, None)


@router.get("/a/{token}")
async def aggregate(
    request: Request,
    token: str = Path(..., min_length=4, max_length=128),
) -> Response:
    """GET /a/{token} — production merge premium+bypass subscriptions.

    Fast path (95% в проде): fresh cache hit → <5ms, ноль работы.
    Cold path: singleflight → 1 пара upstream GET на все параллельные запросы.
    Failure path: если апстрим упал — stale-copy (до 24 часов) → юзер не
    получит 503 при недолгом падении панели.

    Multi-tier caching:
      1. FRESH_TTL=15s  → hit → мгновенно
      2. STALE_TTL=24h  → упал апстрим → отдаём последнее известное
      3. PAIR_TTL=1h    → DB SELECT только раз/час на token
      4. LRU cap=20k    → bounded memory
    """
    if not _TOKEN_RE.match(token):
        return PlainTextResponse("Not found", status_code=404)

    ua_early = request.headers.get("user-agent", "")

    # ── 1. Fresh cache hit ─────────────────────────────────────────
    body_bytes, headers, state = _cache_get(token)
    if state == "fresh":
        _metrics["hits"] += 1
        return _make_response(request, ua_early, token, body_bytes, headers, "hit")

    # ── 2. Load pair (with pair-cache — DB touch раз/час на token) ─
    pair = await _load_pair(token)
    if not pair:
        _metrics["not_found"] += 1
        # Если ЕСТЬ stale-копия но pair нет — не отдаём: юзера удалили.
        return PlainTextResponse("Not found", status_code=404)

    # ── 3. Revoked → короткий stub + короткий кеш (10s) ────────────
    if pair.get("status") == "revoked":
        _metrics["revoked"] += 1
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
        # Короткий TTL — юзер может тут же активировать доступ.
        now = time.monotonic()
        _cache[token] = (now + 10, now + 60, body_bytes, dict(headers))
        _cache.move_to_end(token)
        return _make_response(request, ua_early, token, body_bytes, headers, "miss")

    # ── 4. Fetch (singleflight) ────────────────────────────────────
    ua = ua_early or "Aggregator/1.0"
    try:
        body_bytes, headers = await _fetch_singleflight(token, pair, ua)
    except Exception as e:
        logger.exception("SUB_AGG_FETCH_UNEXPECTED token=%s... err=%s", token[:6], e)
        body_bytes, headers = None, None

    # ── 5. Success → cache and respond ─────────────────────────────
    if body_bytes is not None and headers is not None:
        _cache_set(token, body_bytes, headers)
        _metrics["misses"] += 1
        return _make_response(request, ua_early, token, body_bytes, headers, "miss")

    # ── 6. Upstream failure → stale fallback ───────────────────────
    stale_body, stale_headers, stale_state = _cache_get(token)
    if stale_body is not None and stale_headers is not None and stale_state == "stale":
        _metrics["stale"] += 1
        logger.warning(
            "SUB_AGG_STALE_SERVED token=%s... — оба апстрима упали, "
            "отдаём последнюю копию из stale-tier",
            token[:6],
        )
        return _make_response(request, ua_early, token, stale_body, stale_headers, "stale")

    # ── 7. No stale → 503 с retry-after (панель полностью down + мы cold) ──
    logger.error("SUB_AGG_BOTH_UPSTREAMS_FAIL_NO_STALE token=%s...", token[:6])
    return Response(
        status_code=503,
        headers={"retry-after": "30"},
        content="Upstreams unavailable",
    )


def _make_response(
    request: Request,
    ua: str,
    token: str,
    body_bytes: bytes,
    headers: dict,
    cache_state: str,
) -> Response:
    """Единая точка формирования ответа: browser → HTML, client → base64."""
    if _wants_html(ua):
        sub_url = str(request.url)
        html = _render_sub_html(token=token, sub_url=sub_url, headers=headers)
        return HTMLResponse(
            content=html,
            headers={"x-cache": cache_state, "cache-control": "no-store"},
        )
    return Response(
        content=body_bytes,
        media_type="text/plain; charset=utf-8",
        headers={**headers, "x-cache": cache_state},
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


@router.get("/a/_metrics")
async def metrics_endpoint() -> Response:
    """JSON-метрики агрегатора: счётчики + размеры кешей + in-flight.
    Публично доступен (не sensitive). Prometheus-friendly через json→text."""
    payload = {
        **_metrics,
        "cache_size": len(_cache),
        "pair_cache_size": len(_pair_cache),
        "inflight_size": len(_inflight),
    }
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )


async def close() -> None:
    """Закрыть httpx клиент при graceful shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        finally:
            _client = None


__all__ = ["router", "close", "clear_cache"]
