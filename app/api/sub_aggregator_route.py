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

Кеш (in-process, один uvicorn-worker):
  • body-кеш: fresh 15s → hit мгновенно; stale 24h → отдаём при падении
    панели. LRU-границей MAX_CACHE_ENTRIES ограничена память.
  • pair-кеш: token→URLs, 1 час, чтобы не бить БД на каждый запрос.
  • singleflight: параллельные запросы одного token = 1 upstream fetch.
Обновление подписки: бот после mutation зовёт clear_cache(token) —
in-process, 0ms → следующий запрос клиента = свежие данные из панели.

Нагрузка (см. tests/services/test_sub_aggregator_load.py): при hit-ratio
>90% держит тысячи rps; узкое место — не агрегатор, а панель Remnawave.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

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
    # Latency-статистика: сумма ms + count → avg = sum/count.
    # p95/p99 в проде считаются через x-cache=miss логи (в них timestamp).
    "upstream_ms_sum": 0, "upstream_count": 0,
    "attack_alerts_sent": 0,
}


# ── Attack detector ─────────────────────────────────────────────────
# Скользящее 60-сек окно считает «плохие» события: not_found (флуд
# случайных tokens) и upstream_fail (панель гасят / упала). При пробитии
# порога — разовый Telegram-алерт админу через send_alert (security).
# Cost: инкремент int + сравнение на request — микросекунды.
_attack_window_start: float = 0.0
_attack_not_found: int = 0
_attack_upstream_fail: int = 0

# Пороги per 60 sec — норма (даже пиковая) не должна триггерить.
ATTACK_NOT_FOUND_THRESHOLD = 300     # >5 rps not-found = random-token DoS
ATTACK_UPSTREAM_FAIL_THRESHOLD = 60  # >1 rps upstream fail = панель тупит


def _bump_attack_window(kind: str) -> None:
    """Тик счётчика 60-сек окна; на пробитии порога — разовый алерт."""
    global _attack_window_start, _attack_not_found, _attack_upstream_fail
    now = time.monotonic()
    if now - _attack_window_start >= 60:
        _attack_window_start = now
        _attack_not_found = 0
        _attack_upstream_fail = 0
    if kind == "not_found":
        _attack_not_found += 1
        if _attack_not_found == ATTACK_NOT_FOUND_THRESHOLD:
            _fire_attack_alert(kind="not_found", count=_attack_not_found)
    elif kind == "upstream_fail":
        _attack_upstream_fail += 1
        if _attack_upstream_fail == ATTACK_UPSTREAM_FAIL_THRESHOLD:
            _fire_attack_alert(kind="upstream_fail", count=_attack_upstream_fail)


def _fire_attack_alert(*, kind: str, count: int) -> None:
    """Fire-and-forget алерт админу. Триггерится РОВНО раз на окно
    (== вместо >= в caller'е); поверх — cooldown send_alert('security')."""
    try:
        from app.api import telegram_webhook as _tw
        bot = getattr(_tw, "_bot", None)
        if bot is None:
            return
        _metrics["attack_alerts_sent"] += 1
        if kind == "not_found":
            msg = (
                f"🚨 <b>Sub-aggregator: подозрительный флуд</b>\n\n"
                f"За последнюю минуту: <b>{count}+</b> запросов с несуществующим token.\n"
                f"Возможно — random-token DoS.\n\n"
                f"Проверить: <code>curl https://api.atlassecure.ru/a/_metrics</code>\n"
                f"Порог: {ATTACK_NOT_FOUND_THRESHOLD}/мин.\n\n"
                f"Действие: включить Cloudflare proxied:true, если продолжится."
            )
        else:
            msg = (
                f"🚨 <b>Sub-aggregator: массовые upstream fails</b>\n\n"
                f"За последнюю минуту: <b>{count}+</b> фейлов запроса к панели.\n"
                f"Возможно — панель Remnawave тупит / упала / сеть.\n\n"
                f"Порог: {ATTACK_UPSTREAM_FAIL_THRESHOLD}/мин.\n\n"
                f"Юзеры получают stale-cache — 503 не видят первые 24 часа."
            )
        from app.services.admin_alerts import send_alert
        asyncio.create_task(send_alert(bot, "security", msg, force=False))
    except Exception as e:  # noqa: BLE001
        logger.warning("SUB_AGG_ATTACK_ALERT_FAIL: %s", e)


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


def _cache_put(token: str, body: bytes, headers: dict, fresh_ttl: float, stale_ttl: float) -> None:
    """Единая запись в кеш с LRU-границей. Используется и для обычных
    ответов (_cache_set), и для revoked-заглушки — чтобы rev-записи тоже
    считались в MAX_CACHE_ENTRIES и не текла память."""
    now = time.monotonic()
    _cache[token] = (now + fresh_ttl, now + stale_ttl, body, dict(headers))
    _cache.move_to_end(token)
    while len(_cache) > MAX_CACHE_ENTRIES:
        _cache.popitem(last=False)


def _cache_set(token: str, body: bytes, headers: dict) -> None:
    """Обычный ответ: fresh 15s + stale 24h."""
    _cache_put(token, body, headers, FRESH_TTL, STALE_TTL)


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
    """Общий httpx клиент с keep-alive пулом.

    Лимиты рассчитаны на пик ~10k активных подписок:
    - client refresh раз/час → avg ~3 rps, каждый = 2 upstream GET
    - burst до 800 юзеров в минуту → ~13 rps × 2 = 26 upstream rps
    - safety-margin ×4 → 100 connections покрывают даже single-second бурсты
    max_keepalive_connections=50 держит warm-pool чтобы новый запрос не
    ждал TCP+TLS handshake.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=UPSTREAM_TIMEOUT,
            follow_redirects=True,
            http2=False,  # панель на nginx + может не иметь HTTP/2 → false безопаснее
            limits=httpx.Limits(
                max_keepalive_connections=50,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
        )
    return _client


# Мёртвые/устаревшие хосты в сохранённых sub-URL → живой панельный host.
# У части юзеров в БД (remnawave_premium_sub_url / remnawave_bypass_sub_url)
# лежат ссылки на subscription.vps-cloud.uk — RF-фронт, который снесли.
# Агрегатор нормализует host перед скачиванием, иначе fetch падает →
# пустая склейка → 503 → клиент пишет «неизвестный тип контента».
_DEAD_UPSTREAM_HOSTS = {
    "subscription.vps-cloud.uk": "sub.atlassecure.ru",
}


def _normalize_upstream_url(url: str) -> str:
    """Подменить мёртвый host на живой панельный. Ссылка остаётся ПЛЕЙН
    (без шифрования) — агрегатор качает её напрямую, а юзеру на выход
    /open/{client} отдаёт уже одну зашифрованную ссылку."""
    if not url:
        return url
    for dead, live in _DEAD_UPSTREAM_HOSTS.items():
        if dead in url:
            return url.replace(dead, live)
    return url


async def _fetch_upstream(url: str, user_agent: str) -> Optional[httpx.Response]:
    """Единичный GET апстрима с форвардом UA. None при таймауте/ошибке.
    Пишет latency в метрики для мониторинга."""
    url = _normalize_upstream_url(url)
    t0 = time.monotonic()
    try:
        client = _get_client()
        resp = await client.get(url, headers={"User-Agent": user_agent})
        _metrics["upstream_ms_sum"] += int((time.monotonic() - t0) * 1000)
        _metrics["upstream_count"] += 1
        return resp
    except Exception as e:
        _metrics["upstream_ms_sum"] += int((time.monotonic() - t0) * 1000)
        _metrics["upstream_count"] += 1
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
        _bump_attack_window("upstream_fail")
    if gb_ok:
        _metrics["upstream_ok"] += 1
    else:
        _metrics["upstream_fail"] += 1
        _bump_attack_window("upstream_fail")

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

    loop = asyncio.get_running_loop()
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
        _metrics["not_found"] += 1
        _bump_attack_window("not_found")
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
        _bump_attack_window("not_found")
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
        # Короткий TTL — юзер может тут же активировать доступ. Через _cache_put
        # → тоже под LRU-границей.
        _cache_put(token, body_bytes, headers, 10, 60)
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
    """Любой запрос (браузер или VPN-клиент) получает сырую base64-подписку.
    HTML sub-page убрана по решению владельца — вернулись к поведению до её
    введения (52860b1 / 6274b21)."""
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
    # Чистим и body-кеш, И pair-кеш (иначе старые sub-URL живут до PAIR_TTL).
    clear_cache(token)
    return Response(content=b'{"ok":true}', media_type="application/json")


def get_metrics_snapshot() -> dict:
    """Снимок метрик — для HTTP-эндпоинта И для бот-команды /aggstats.
    Считает hit_ratio + avg_upstream_ms поверх сырых счётчиков."""
    total = _metrics["hits"] + _metrics["misses"] + _metrics["stale"]
    hit_ratio = round(_metrics["hits"] / total, 4) if total > 0 else 0
    avg_upstream_ms = (
        round(_metrics["upstream_ms_sum"] / _metrics["upstream_count"], 1)
        if _metrics["upstream_count"] > 0 else 0
    )
    return {
        **_metrics,
        "cache_size": len(_cache),
        "pair_cache_size": len(_pair_cache),
        "inflight_size": len(_inflight),
        "hit_ratio": hit_ratio,
        "avg_upstream_ms": avg_upstream_ms,
    }


@router.get("/a/_metrics")
async def metrics_endpoint() -> Response:
    """JSON-метрики агрегатора: счётчики + размеры кешей + средний latency.
    Публично доступен (не sensitive). Prometheus-friendly через json→text."""
    return Response(
        content=json.dumps(get_metrics_snapshot()),
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


__all__ = ["router", "close", "clear_cache", "get_metrics_snapshot"]
