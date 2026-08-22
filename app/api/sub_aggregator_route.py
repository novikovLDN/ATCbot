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
from functools import lru_cache
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


async def _fetch_upstream(url: str, user_agent: str) -> Optional[httpx.Response]:
    """Единичный GET апстрима с форвардом UA. None при таймауте/ошибке.
    Пишет latency в метрики для мониторинга."""
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


# ── Onboarding page: per-device install links ───────────────────────
# Happ iOS — НЕ-РФ регион (глобальный App Store) по решению владельца:
# RU-стор версия нестабильна, global — основная. Рядом даём ссылку на
# видео «Как сменить регион» (t.me/atlas_secure/75).
_REGION_HELP_URL = "https://t.me/atlas_secure/75"

_STORE_LINKS: dict[str, dict[str, str]] = {
    "ios": {
        "happ": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
        "incy": "https://apps.apple.com/ru/app/incy/id6756943388?l=en-GB",
    },
    "android": {
        "happ": "https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
        "incy": "https://play.google.com/store/apps/details?id=llc.itdev.incy&hl=en_IE",
    },
    "macos": {
        "happ": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
        "incy": "https://apps.apple.com/ru/app/incy/id6756943388?l=en-GB",
    },
    "windows": {
        "happ": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
    },
}

_PLATFORM_LABELS = [
    ("ios", "iOS"),
    ("android", "Android"),
    ("macos", "macOS"),
    ("windows", "Windows"),
]

_CLIENT_LABELS = {"happ": "Happ", "incy": "Incy"}

# Текст «установите приложение» per platform (store = куда ведёт кнопка).
_INSTALL_HINTS = {
    "ios": "Откройте страницу в App Store и установите приложение. Запустите его, в окне разрешения конфигурации нажмите «Разрешить» и введите код-пароль.",
    "android": "Установите приложение из Google Play и запустите его.",
    "macos": "Установите приложение из App Store и запустите его.",
    "windows": "Скачайте установщик, запустите его и завершите установку.",
}

_STORE_BTN_LABELS = {
    "ios": "Открыть в App Store",
    "android": "Открыть в Google Play",
    "macos": "Открыть в App Store",
    "windows": "Скачать для Windows",
}

# ── Кастомные line-иконки (inline SVG, currentColor, без стоковых эмодзи) ──
# Все 24×24, stroke-based — рисуются в цвет текста, масштабируются без потерь,
# ноль внешних ассетов. Узнаваемые силуэты для сканирования глазом.
_PLATFORM_ICONS = {
    # iPhone — скруглённый корпус + кнопка
    "ios": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="7" y="2.5" width="10" height="19" rx="2.6"/><path d="M10.5 18.5h3"/></svg>',
    # Android — робот: голова, антенны, глаза
    "android": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 9h11v7.5a1.8 1.8 0 0 1-1.8 1.8H8.3a1.8 1.8 0 0 1-1.8-1.8Z"/><path d="M8 9 6.6 6.4M16 9l1.4-2.6"/><circle cx="10" cy="12.5" r=".7" fill="currentColor" stroke="none"/><circle cx="14" cy="12.5" r=".7" fill="currentColor" stroke="none"/></svg>',
    # macOS — ноутбук
    "macos": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="5" width="14" height="10" rx="1.6"/><path d="M2.5 18.5h19"/></svg>',
    # Windows — четыре плитки
    "windows": '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M3.5 6 11 4.9V11H3.5Zm8.6-1.2L20.5 3.5V11h-8.4ZM3.5 13H11v6.1L3.5 18ZM12.1 13h8.4v7.5l-8.4-1.2Z"/></svg>',
}

# Иконка стора — стрелка-загрузка в лоток (единая для всех платформ).
_STORE_GLYPH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v10"/><path d="m8 11 4 4 4-4"/><path d="M5 19.5h14"/></svg>'
# Совместимость: старое имя ссылается на glyph (используется в шаблоне store-btn).
_STORE_ICONS = {p: _STORE_GLYPH for p in ("ios", "android", "macos", "windows")}

# Иконки шагов установки.
_STEP_ICONS = {
    # Шаг 1 — скачать/установить
    "install": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v10"/><path d="m8 11 4 4 4-4"/><path d="M5 19.5h14"/></svg>',
    # Шаг 2 — добавить подписку (молния = мгновенно)
    "add": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 3 5 13h6l-1 8 8-10h-6z"/></svg>',
    # Шаг 3 — подключиться (power)
    "connect": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5v8"/><path d="M7 6.5a7 7 0 1 0 10 0"/></svg>',
}

# Клиентские бренд-глифы для карточек выбора.
_CLIENT_GLYPHS = {
    # Happ — звезда (рекомендуем)
    "happ": '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2.6l2.6 5.9 6.4.6-4.8 4.3 1.4 6.3L12 20.4 6.4 19.7l1.4-6.3L3 9.1l6.4-.6z"/></svg>',
    # Incy — щит
    "incy": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 5 6v5.5c0 4.4 3 7.6 7 9 4-1.4 7-4.6 7-9V6z"/><path d="m9.5 12 1.8 1.8 3.4-3.6"/></svg>',
}
_ARROW_GLYPH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>'
_BACK_GLYPH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m14 6-6 6 6 6"/></svg>'


@lru_cache(maxsize=512)
def _qr_svg(url: str) -> str:
    """Server-side QR как inline-SVG (без CDN — работает в РФ без прокси).

    Для десктопа: сканируешь телефоном → та же страница открывается на
    телефоне → одна кнопка «Установить подписку». Кешируем per-URL —
    генерация ~2мс, а URL стабилен для token.

    Возвращает пустую строку, если qrcode-либа недоступна (тогда блок
    просто не показывается — graceful degrade).
    """
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(
            url,
            image_factory=qrcode.image.svg.SvgPathImage,
            box_size=10,
            border=1,
        )
        raw = img.to_string().decode()
        # Стилизация: фиксированный размер через CSS-класс, path — тёмный.
        raw = raw.replace("<svg", '<svg class="qr-svg"', 1)
        return raw
    except Exception as e:  # noqa: BLE001
        logger.warning("SUB_AGG_QR_FAIL: %s", e)
        return ""


def _render_sub_html(*, token: str, sub_url: str, headers: dict) -> str:
    """Экранная onboarding-страница подписки (браузерный UA).

    Архитектура «веду пользователя» (маркетинг-first, сканирование не чтение):
      • Экран 0 «Домой» — hero-карточка подписки + выбор приложения
        (две крупные карточки: Happ «рекомендуем» / Incy).
      • Экран Happ / Экран Incy — настройка ТОЛЬКО под выбранный клиент:
        селектор платформы (кастомные SVG-иконки, авто-детект) + 3 шага
        с verb-first заголовками и большими кнопками, плашка «вручную»,
        ключ подписки.
      • Слайд-переходы между экранами (transform/opacity — GPU, без jank).

    Клиенты (Happ/v2rayNG/…) сюда не попадают — UA-split отдаёт им raw
    base64 (_wants_html). Страница — только для браузеров.
    """
    brand = html_escape(_brand_title())
    support = _support_url() or ""
    support_esc = html_escape(support, quote=True)

    # ── Данные подписки из hybrid userinfo ─────────────────────────
    ui = _parse_userinfo(headers.get("subscription-userinfo", ""))
    total = int(ui.get("total", 0))
    used = int(ui.get("upload", 0)) + int(ui.get("download", 0))
    left = max(0, total - used) if total > 0 else 0
    left_str = _fmt_bytes(left) if total > 0 else "∞"
    total_str = _fmt_bytes(total) if total > 0 else "∞"
    used_str = _fmt_bytes(used) if used > 0 else "0"
    pct_used = min(100, int(used / total * 100)) if total > 0 else 0
    expire_str = _fmt_expire(int(ui.get("expire", 0))) or "—"
    sub_title = html_escape(headers.get("profile-title", "") or _brand_title())

    # ── Deep-links (crypt-sealed через /open/{client}) ─────────────
    base = _deeplink_base()
    q = url_quote(sub_url, safe='')
    deeplinks = {
        "happ": f"{base}/open/happ?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}",
        "incy": f"{base}/open/incy?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}",
    }

    sub_url_esc = html_escape(sub_url)
    sub_url_js = json.dumps(sub_url)
    deeplinks_js = json.dumps(deeplinks)

    # Ручной импорт — своя инструкция на клиент (совпадает с traffic.info в боте).
    manual_steps = {
        "happ": (
            "<b>1.</b> Скопируйте ссылку кнопкой ниже<br>"
            "<b>2.</b> Откройте Happ → вкладка «Главная»<br>"
            "<b>3.</b> Нажмите <b>+</b> в правом верхнем углу<br>"
            "<b>4.</b> «Вставить из буфера» — подписка добавится"
        ),
        "incy": (
            "<b>1.</b> Скопируйте ссылку кнопкой ниже<br>"
            "<b>2.</b> Откройте Incy → «Настройки»<br>"
            "<b>3.</b> «Импорт» → «Из буфера» — подписка добавится"
        ),
    }

    def _build_platform_panel(plat: str, client: str) -> str:
        store_url = _STORE_LINKS.get(plat, {}).get(client, "")
        if not store_url:
            return ""
        store_esc = html_escape(store_url, quote=True)
        deep_esc = html_escape(deeplinks[client], quote=True)
        client_name = _CLIENT_LABELS[client]
        install_hint = html_escape(_INSTALL_HINTS[plat])
        store_btn = html_escape(_STORE_BTN_LABELS[plat])
        region_row = ""
        if plat == "ios" and client == "happ":
            region_row = (
                f'<a class="link-row" href="{html_escape(_REGION_HELP_URL, quote=True)}" '
                f'target="_blank" rel="noopener">'
                f'<span class="lr-play"></span>Видео: как сменить регион аккаунта</a>'
            )
        return f"""
      <div class="panel" data-plat="{plat}" hidden>
        <ol class="steps">
          <li class="step">
            <div class="step-ico i-install">{_STEP_ICONS['install']}</div>
            <div class="step-main">
              <div class="step-eyebrow">Шаг 1</div>
              <div class="step-title">Установить {client_name}</div>
              <p class="step-text">{install_hint}</p>
              <a class="store-btn" href="{store_esc}" target="_blank" rel="noopener">
                <span class="store-ico">{_STORE_GLYPH}</span>
                <span class="store-lbl">{store_btn}</span>
                <span class="store-arr">{_ARROW_GLYPH}</span>
              </a>
              {region_row}
            </div>
          </li>
          <li class="step">
            <div class="step-ico i-add">{_STEP_ICONS['add']}</div>
            <div class="step-main">
              <div class="step-eyebrow">Шаг 2</div>
              <div class="step-title">Добавить подписку</div>
              <p class="step-text">Откроется {client_name} — просто подтвердите. Всё настроится само.</p>
              <a class="cta" href="{deep_esc}">
                <span class="cta-ico">{_STEP_ICONS['add']}</span>
                <span>Добавить в {client_name}</span>
                <span class="cta-arr">{_ARROW_GLYPH}</span>
              </a>
              <details class="manual">
                <summary>
                  <span>Не открылось? Добавить вручную</span>
                  <span class="chev">{_BACK_GLYPH}</span>
                </summary>
                <div class="manual-body">
                  <p class="manual-text">{manual_steps[client]}</p>
                  <div class="keyblock">{sub_url_esc}</div>
                  <button class="copy-btn" type="button">Скопировать ссылку</button>
                </div>
              </details>
            </div>
          </li>
          <li class="step last">
            <div class="step-ico i-connect">{_STEP_ICONS['connect']}</div>
            <div class="step-main">
              <div class="step-eyebrow">Шаг 3</div>
              <div class="step-title">Подключиться</div>
              <p class="step-text">Большая кнопка в центре — и вы под защитой. Сервер можно сменить в списке.</p>
            </div>
          </li>
        </ol>
      </div>"""

    def _build_client_screen(client: str) -> str:
        client_name = _CLIENT_LABELS[client]
        plats = [p for p, _ in _PLATFORM_LABELS if _STORE_LINKS.get(p, {}).get(client)]
        seg_html = "".join(
            f'<button class="seg" data-plat-btn="{p}" type="button">'
            f'<span class="seg-ico">{_PLATFORM_ICONS[p]}</span>'
            f'<span>{dict(_PLATFORM_LABELS)[p]}</span></button>'
            for p in plats
        )
        panels = "".join(_build_platform_panel(p, client) for p in plats)
        glyph = _CLIENT_GLYPHS[client]
        return f"""
  <section class="screen" data-screen="{client}" data-plats='{json.dumps(plats)}' hidden>
    <button class="back-btn" data-nav="home" type="button">
      <span class="back-ico">{_BACK_GLYPH}</span>Другое приложение
    </button>
    <div class="scr-head {client}">
      <div class="scr-glyph">{glyph}</div>
      <div>
        <div class="scr-title">{client_name}</div>
        <div class="scr-sub">Настройка за 3 шага</div>
      </div>
    </div>
    <div class="segs">{seg_html}</div>
    {panels}
    <div class="keycard">
      <div class="keycard-label">Ключ подписки</div>
      <div class="keyblock">{sub_url_esc}</div>
      <button class="copy-btn" type="button">Скопировать ссылку</button>
    </div>
  </section>"""

    screen_happ = _build_client_screen("happ")
    screen_incy = _build_client_screen("incy")

    # QR — только для десктопа (на «домашнем» экране).
    qr_svg = _qr_svg(sub_url)
    qr_block = f"""
    <div class="qr-card" id="qrCard" hidden>
      <div class="qr-box">{qr_svg}</div>
      <div class="qr-info">
        <div class="qr-title">Быстрее с телефона</div>
        <p class="qr-text">Наведите камеру — эта страница откроется на телефоне.</p>
      </div>
    </div>""" if qr_svg else ""

    support_row = (
        f'<a class="support" href="{support_esc}" target="_blank" rel="noopener">Поддержка</a>'
        if support else ""
    )
    traffic_row = (
        f'<span class="row-value">{left_str}<span class="dim"> из {total_str}</span></span>'
        if total > 0 else '<span class="row-value">∞</span>'
    )
    bar_block = (
        f'''<div class="bar-wrap">
          <div class="bar-meta"><span>Использовано {pct_used}%</span><span>{total_str}</span></div>
          <div class="bar"><div class="fill" data-w="{pct_used}"></div></div>
        </div>''' if total > 0 else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f6f7f9">
<meta name="robots" content="noindex,nofollow">
<title>{brand}</title>
<style>
  * {{ box-sizing: border-box; }}
  :root {{
    --blue: #2563EB; --violet: #6d28d9; --green: #10B981;
    --ink: #0f1720; --muted: #6b7280;
    --bg: #f6f7f9; --card: #fff; --line: #e6e8ec;
    --spring: cubic-bezier(.34, 1.28, .5, 1);
  }}
  html, body {{
    margin: 0; padding: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', sans-serif;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  }}
  body {{
    min-height: 100vh; padding: 26px 16px 44px;
    display: flex; flex-direction: column; align-items: center;
    position: relative; overflow-x: hidden;
  }}
  /* Aurora-фон — два размытых blob'а, статичны (0 нагрузки на скролл) */
  body::before, body::after {{
    content: ""; position: fixed; z-index: -1; border-radius: 50%;
    filter: blur(100px); pointer-events: none;
  }}
  body::before {{ width: 460px; height: 460px; top: -190px; right: -150px;
    background: radial-gradient(circle, rgba(37,99,235,.16), transparent 65%); }}
  body::after {{ width: 420px; height: 420px; bottom: -170px; left: -170px;
    background: radial-gradient(circle, rgba(124,58,237,.13), transparent 65%); }}
  .wrap {{ width: 100%; max-width: 460px; }}

  .brand {{
    font-size: 27px; font-weight: 800; letter-spacing: -0.03em; margin: 0 0 18px;
    background: linear-gradient(100deg, var(--ink) 28%, var(--blue) 72%, var(--violet));
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
    opacity: 0; animation: fadeUp .5s var(--spring) .05s forwards;
  }}

  /* ── Hero-карточка подписки ── */
  .hero {{
    border-radius: 22px; padding: 22px 20px 20px; margin-bottom: 26px;
    background: linear-gradient(140deg, #1e3a8a 0%, #2563EB 46%, #6d28d9 100%);
    color: #fff; box-shadow: 0 18px 44px rgba(37,99,235,.30);
    position: relative; overflow: hidden;
    opacity: 0; animation: fadeUp .55s var(--spring) .12s forwards;
  }}
  .hero::before {{ content: ""; position: absolute; inset: 0; pointer-events: none;
    background: radial-gradient(120% 60% at 18% -12%, rgba(255,255,255,.24), transparent 55%); }}
  .hero-head {{ display: flex; align-items: center; gap: 12px; margin-bottom: 15px; position: relative; }}
  .hero-ico {{ width: 44px; height: 44px; border-radius: 14px; flex: 0 0 44px;
    background: rgba(255,255,255,.18); display: flex; align-items: center; justify-content: center; }}
  .hero-ico svg {{ width: 24px; height: 24px; color: #fff; }}
  .hero-title {{ font-size: 17px; font-weight: 800; letter-spacing: -0.01em; }}
  .hero-sub {{ font-size: 13px; color: rgba(255,255,255,.66); }}
  .badge {{ margin-left: auto; padding: 6px 13px; border-radius: 999px;
    background: rgba(52,211,153,.25); color: #d1fae5; border: 1px solid rgba(52,211,153,.45);
    font-size: 12px; font-weight: 700; display: inline-flex; align-items: center; gap: 6px; }}
  .badge .dot {{ width: 7px; height: 7px; border-radius: 50%; background: #34d399;
    box-shadow: 0 0 0 0 rgba(52,211,153,.7); animation: pulse 2s infinite; }}
  .rows {{ border-top: 1px solid rgba(255,255,255,.16); position: relative; }}
  .row {{ display: flex; align-items: center; justify-content: space-between;
    padding: 12px 2px; border-bottom: 1px solid rgba(255,255,255,.16); font-size: 14px; }}
  .row-label {{ color: rgba(255,255,255,.62); }}
  .row-value {{ font-weight: 800; font-variant-numeric: tabular-nums; }}
  .dim {{ color: rgba(255,255,255,.55); font-weight: 500; }}
  .bar-wrap {{ padding: 14px 2px 4px; position: relative; }}
  .bar-meta {{ display: flex; justify-content: space-between; font-size: 12px;
    color: rgba(255,255,255,.62); margin-bottom: 7px; }}
  .bar {{ height: 9px; background: rgba(255,255,255,.18); border-radius: 999px; overflow: hidden; }}
  .fill {{ height: 100%; width: 0; border-radius: 999px;
    background: linear-gradient(90deg, #34d399, #a7f3d0); transition: width 900ms var(--spring); }}

  .section-title {{ font-size: 19px; font-weight: 800; letter-spacing: -0.01em; margin: 0 0 4px; }}
  .section-sub {{ font-size: 13.5px; color: var(--muted); margin: 0 0 16px; }}

  /* ── Экраны + слайд-переходы ── */
  .screen {{ animation: slideR .34s var(--spring) both; }}
  .screen.back {{ animation: slideL .34s var(--spring) both; }}

  /* ── Выбор приложения (домашний экран) ── */
  .choose {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 8px; }}
  .choice {{
    position: relative; display: flex; align-items: center; gap: 15px;
    padding: 18px; border-radius: 18px; border: 1.5px solid var(--line); background: var(--card);
    cursor: pointer; text-align: left; width: 100%; appearance: none;
    transition: transform .12s var(--spring), box-shadow .2s ease, border-color .2s ease;
  }}
  .choice:hover {{ transform: translateY(-2px); box-shadow: 0 12px 28px rgba(15,23,32,.10); }}
  .choice:active {{ transform: scale(.985); }}
  .choice.primary {{ border-color: transparent;
    background: linear-gradient(150deg, rgba(37,99,235,.06), rgba(109,40,217,.06)), var(--card);
    box-shadow: 0 8px 26px rgba(37,99,235,.14); }}
  .choice.primary::after {{ content: ""; position: absolute; inset: 0; border-radius: 18px;
    padding: 1.5px; background: linear-gradient(140deg, var(--blue), var(--violet));
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor; mask-composite: exclude; pointer-events: none; }}
  .ch-glyph {{ width: 48px; height: 48px; border-radius: 14px; flex: 0 0 48px;
    display: flex; align-items: center; justify-content: center; }}
  .ch-glyph svg {{ width: 26px; height: 26px; }}
  .choice.primary .ch-glyph {{ background: linear-gradient(140deg, var(--blue), var(--violet)); color: #fff; }}
  .choice.alt .ch-glyph {{ background: rgba(15,23,32,.06); color: var(--ink); }}
  .ch-body {{ flex: 1; min-width: 0; }}
  .ch-name {{ font-size: 17px; font-weight: 800; display: flex; align-items: center; gap: 8px; }}
  .ch-desc {{ font-size: 13px; color: var(--muted); margin-top: 2px; }}
  .ch-arr {{ color: #c4c9d0; flex: 0 0 22px; }}
  .ch-arr svg {{ width: 22px; height: 22px; }}
  .rec {{ font-size: 10px; font-weight: 800; letter-spacing: .05em; text-transform: uppercase;
    color: #fff; background: linear-gradient(90deg, #f59e0b, #f97316);
    padding: 3px 8px; border-radius: 999px; box-shadow: 0 2px 6px rgba(249,115,22,.35); }}

  /* ── Клиентский экран: назад + шапка ── */
  .back-btn {{ appearance: none; border: none; background: none; cursor: pointer;
    display: inline-flex; align-items: center; gap: 4px; color: var(--muted);
    font-size: 14px; font-weight: 600; padding: 4px 0; margin-bottom: 14px; }}
  .back-btn:active {{ opacity: .6; }}
  .back-ico svg {{ width: 20px; height: 20px; display: block; }}
  .scr-head {{ display: flex; align-items: center; gap: 13px; margin-bottom: 18px; }}
  .scr-glyph {{ width: 52px; height: 52px; border-radius: 16px; flex: 0 0 52px;
    display: flex; align-items: center; justify-content: center; color: #fff; }}
  .scr-glyph svg {{ width: 28px; height: 28px; }}
  .scr-head.happ .scr-glyph {{ background: linear-gradient(140deg, #f59e0b, #f97316); }}
  .scr-head.incy .scr-glyph {{ background: linear-gradient(140deg, var(--blue), var(--violet)); }}
  .scr-title {{ font-size: 22px; font-weight: 800; letter-spacing: -0.02em; }}
  .scr-sub {{ font-size: 13.5px; color: var(--muted); }}

  /* ── Segmented control платформ (кастомные SVG) ── */
  .segs {{ display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 4px;
    background: #e9ebef; border-radius: 14px; padding: 4px; margin-bottom: 20px; }}
  .seg {{ appearance: none; border: none; background: transparent; color: var(--muted);
    border-radius: 11px; padding: 9px 4px; font-size: 12px; font-weight: 700;
    display: flex; flex-direction: column; align-items: center; gap: 4px;
    cursor: pointer; min-height: 54px;
    transition: background .16s var(--spring), color .16s ease, box-shadow .16s ease; }}
  .seg-ico svg {{ width: 20px; height: 20px; display: block; }}
  .seg.active {{ background: #fff; color: var(--ink); box-shadow: 0 2px 8px rgba(15,23,32,.10); }}

  /* ── Шаги ── */
  .steps {{ list-style: none; margin: 0; padding: 0; }}
  .step {{ display: flex; gap: 14px; position: relative; padding-bottom: 24px; }}
  .step:not(.last)::before {{ content: ""; position: absolute; left: 21px; top: 46px; bottom: 2px;
    width: 2px; background: var(--line); }}
  .step-ico {{ flex: 0 0 44px; width: 44px; height: 44px; border-radius: 13px;
    display: flex; align-items: center; justify-content: center; z-index: 1; }}
  .step-ico svg {{ width: 22px; height: 22px; }}
  .i-install {{ background: rgba(37,99,235,.12); color: var(--blue); }}
  .i-add {{ background: linear-gradient(140deg, var(--blue), var(--violet)); color: #fff;
    box-shadow: 0 6px 16px rgba(37,99,235,.28); }}
  .i-connect {{ background: rgba(16,185,129,.14); color: var(--green); }}
  .step-main {{ flex: 1; min-width: 0; padding-top: 2px; }}
  .step-eyebrow {{ font-size: 11px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase;
    color: #a5abb5; margin-bottom: 3px; }}
  .step-title {{ font-size: 17px; font-weight: 800; letter-spacing: -0.01em; margin-bottom: 5px; }}
  .step-text {{ font-size: 13.5px; line-height: 1.5; color: var(--muted); margin: 0 0 12px; }}

  /* Стор-кнопка */
  .store-btn {{ display: flex; align-items: center; gap: 12px; width: 100%; min-height: 54px;
    padding: 12px 14px; background: var(--card); color: var(--ink);
    border: 1.5px solid var(--line); border-radius: 14px; text-decoration: none;
    font-size: 14px; font-weight: 700;
    transition: background .12s ease, border-color .12s ease, transform .08s ease; }}
  .store-btn:active {{ background: #f0f2f5; transform: scale(.99); }}
  .store-ico {{ width: 36px; height: 36px; flex: 0 0 36px; border-radius: 10px; background: #f0f2f5;
    display: flex; align-items: center; justify-content: center; color: var(--ink); }}
  .store-ico svg {{ width: 19px; height: 19px; }}
  .store-lbl {{ flex: 1; }}
  .store-arr svg {{ width: 17px; height: 17px; color: #b6bcc6; display: block; }}

  /* Ссылка-видео региона */
  .link-row {{ display: inline-flex; align-items: center; gap: 8px; margin-top: 10px;
    color: var(--blue); font-size: 13.5px; font-weight: 700; text-decoration: none; }}
  .link-row:active {{ opacity: .6; }}
  .lr-play {{ width: 20px; height: 20px; border-radius: 6px; flex: 0 0 20px;
    background: rgba(37,99,235,.12); position: relative; }}
  .lr-play::before {{ content: ""; position: absolute; top: 50%; left: 54%; transform: translate(-50%,-50%);
    border-left: 6px solid var(--blue); border-top: 4px solid transparent; border-bottom: 4px solid transparent; }}

  /* Главный CTA */
  .cta {{ display: flex; align-items: center; justify-content: center; gap: 9px; width: 100%; min-height: 56px;
    padding: 15px 20px; border-radius: 16px; text-decoration: none;
    background: linear-gradient(135deg, var(--blue), var(--violet)); color: #fff;
    font-size: 15.5px; font-weight: 700; letter-spacing: -0.01em;
    box-shadow: 0 10px 26px rgba(37,99,235,.36);
    transition: transform .09s ease, box-shadow .2s ease; }}
  .cta:active {{ transform: scale(.975); box-shadow: 0 5px 14px rgba(37,99,235,.3); }}
  .cta-ico svg {{ width: 19px; height: 19px; display: block; }}
  .cta-arr svg {{ width: 18px; height: 18px; display: block; opacity: .85; }}

  /* «Вручную» */
  .manual {{ margin-top: 12px; border: 1px dashed #d1d5db; border-radius: 12px;
    background: rgba(255,255,255,.5); overflow: hidden; }}
  .manual summary {{ list-style: none; display: flex; align-items: center; justify-content: space-between;
    padding: 12px 15px; font-size: 13px; font-weight: 700; color: var(--muted);
    cursor: pointer; user-select: none; -webkit-user-select: none; }}
  .manual summary::-webkit-details-marker {{ display: none; }}
  .manual .chev svg {{ width: 15px; height: 15px; color: #9aa1ab; transform: rotate(-90deg);
    transition: transform .2s var(--spring); display: block; }}
  .manual[open] .chev svg {{ transform: rotate(90deg); }}
  .manual[open] summary {{ border-bottom: 1px dashed var(--line); }}
  .manual-body {{ padding: 14px 15px 16px; animation: fadeUp .28s var(--spring); }}
  .manual-text {{ font-size: 13px; line-height: 1.75; color: var(--muted); margin: 0 0 12px; }}
  .manual-text b {{ color: var(--ink); }}

  /* Ключ подписки */
  .keycard {{ margin-top: 14px; background: var(--card); border: 1px solid var(--line);
    border-radius: 16px; padding: 16px; }}
  .keycard-label {{ font-size: 12px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
    color: #a5abb5; margin-bottom: 10px; }}
  .keyblock {{ background: #eef0f3; border: 1px solid var(--line); border-radius: 10px; padding: 12px;
    font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 11.5px; line-height: 1.5;
    word-break: break-all; user-select: all; -webkit-user-select: all; margin-bottom: 10px; }}
  .copy-btn {{ appearance: none; width: 100%; border: 1.5px solid var(--line); background: var(--card);
    color: var(--ink); font-size: 13.5px; font-weight: 700; padding: 12px 0; border-radius: 11px;
    cursor: pointer; transition: background .1s ease, color .15s ease, border-color .15s ease; }}
  .copy-btn:active {{ background: #f0f2f5; }}
  .copy-btn.copied {{ color: var(--green); border-color: var(--green); }}

  /* QR десктоп */
  .qr-card {{ display: flex; align-items: center; gap: 18px; background: var(--card);
    border: 1px solid var(--line); border-radius: 16px; padding: 16px; margin: 18px 0 4px; }}
  .qr-box {{ flex: 0 0 120px; background: #fff; border-radius: 12px; padding: 8px; border: 1px solid #eef0f3; }}
  .qr-svg {{ width: 104px; height: 104px; display: block; }}
  .qr-title {{ font-size: 15px; font-weight: 800; margin-bottom: 5px; }}
  .qr-text {{ font-size: 13px; line-height: 1.5; color: var(--muted); margin: 0; }}

  .footer {{ margin-top: 34px; display: flex; align-items: center; justify-content: space-between;
    font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: #a5abb5; }}
  .support {{ color: #a5abb5; text-decoration: none; }}

  /* ── Keyframes (только transform/opacity — GPU) ── */
  @keyframes fadeUp {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: none; }} }}
  @keyframes slideR {{ from {{ opacity: 0; transform: translateX(26px); }} to {{ opacity: 1; transform: none; }} }}
  @keyframes slideL {{ from {{ opacity: 0; transform: translateX(-26px); }} to {{ opacity: 1; transform: none; }} }}
  @keyframes pulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(52,211,153,.6); }} 70% {{ box-shadow: 0 0 0 7px rgba(52,211,153,0); }} 100% {{ box-shadow: 0 0 0 0 rgba(52,211,153,0); }} }}
  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{ animation: none !important; transition: none !important; }}
    .fill {{ transition: none; }}
  }}

  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #0f1720; --card: #16202b; --line: #29323f; --ink: #f5f5f2; --muted: #9aa1ab; }}
    .brand {{ background: linear-gradient(100deg, #fff 28%, #60a5fa 70%, #a78bfa);
      -webkit-background-clip: text; background-clip: text; }}
    .section-title {{ color: #fff; }}
    .choice.alt .ch-glyph {{ background: rgba(255,255,255,.08); color: #fff; }}
    .ch-arr {{ color: #4b5563; }}
    .segs {{ background: #1a2431; }}
    .seg.active {{ background: #2a3441; color: #fff; box-shadow: 0 2px 8px rgba(0,0,0,.4); }}
    .scr-title, .step-title, .keycard-label, .qr-title {{ color: #fff; }}
    .step:not(.last)::before {{ background: #29323f; }}
    .i-install {{ background: rgba(59,130,246,.18); color: #60a5fa; }}
    .i-connect {{ background: rgba(16,185,129,.18); color: #34d399; }}
    .store-btn {{ background: #1f2937; color: #fff; border-color: #29323f; }}
    .store-btn:active {{ background: #2a3441; }}
    .store-ico {{ background: #2a3441; color: #fff; }}
    .link-row {{ color: #60a5fa; }} .lr-play {{ background: rgba(96,165,250,.18); }}
    .lr-play::before {{ border-left-color: #60a5fa; }}
    .manual {{ background: rgba(31,41,55,.4); border-color: #374151; }}
    .manual summary {{ color: #9aa1ab; }} .manual[open] summary {{ border-bottom-color: #374151; }}
    .manual-text {{ color: #9aa1ab; }} .manual-text b {{ color: #fff; }}
    .keyblock {{ background: #1f2937; border-color: #29323f; color: #d1d5db; }}
    .copy-btn {{ background: #1f2937; color: #fff; border-color: #29323f; }}
    .copy-btn:active {{ background: #2a3441; }} .copy-btn.copied {{ color: #34d399; border-color: #34d399; }}
    .qr-card {{ background: #16202b; border-color: #29323f; }} .qr-box {{ background: #fff; border-color: #fff; }}
    .qr-text {{ color: #9aa1ab; }} .footer {{ color: #6b7280; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">{brand}</div>

  <div class="hero">
    <div class="hero-head">
      <div class="hero-ico">{_STEP_ICONS['connect']}</div>
      <div>
        <div class="hero-title">{sub_title}</div>
        <div class="hero-sub">Ваша подписка</div>
      </div>
      <div class="badge"><span class="dot"></span>Активна</div>
    </div>
    <div class="rows">
      <div class="row"><span class="row-label">Осталось</span>{traffic_row}</div>
      <div class="row"><span class="row-label">Использовано</span><span class="row-value">{used_str}</span></div>
      <div class="row"><span class="row-label">Истекает</span><span class="row-value">{html_escape(expire_str)}</span></div>
    </div>
    {bar_block}
  </div>

  <!-- ЭКРАН: выбор приложения -->
  <section class="screen" data-screen="home">
    <div class="section-title">Подключение за минуту</div>
    <p class="section-sub">Выберите приложение — покажем настройку под ваше устройство.</p>
    <div class="choose">
      <button class="choice primary" data-nav="happ" type="button">
        <div class="ch-glyph">{_CLIENT_GLYPHS['happ']}</div>
        <div class="ch-body">
          <div class="ch-name">Happ <span class="rec">Рекомендуем</span></div>
          <div class="ch-desc">Проще всего — подойдёт большинству</div>
        </div>
        <span class="ch-arr">{_ARROW_GLYPH}</span>
      </button>
      <button class="choice alt" data-nav="incy" type="button">
        <div class="ch-glyph">{_CLIENT_GLYPHS['incy']}</div>
        <div class="ch-body">
          <div class="ch-name">Incy</div>
          <div class="ch-desc">Альтернатива, если Happ не подошёл</div>
        </div>
        <span class="ch-arr">{_ARROW_GLYPH}</span>
      </button>
    </div>
    {qr_block}
  </section>
{screen_happ}
{screen_incy}

  <div class="footer">
    <span>{brand}</span>
    {support_row}
  </div>
</div>

<script>
(function () {{
  var DEEPLINKS = {deeplinks_js};
  var ua = navigator.userAgent || '';
  var detected = 'ios';
  if (/android/i.test(ua)) detected = 'android';
  else if (/iphone|ipad|ipod/i.test(ua)) detected = 'ios';
  else if (/macintosh|mac os x/i.test(ua)) detected = 'macos';
  else if (/windows/i.test(ua)) detected = 'windows';
  var isMobile = (detected === 'ios' || detected === 'android');

  var screens = {{}};
  document.querySelectorAll('.screen').forEach(function (s) {{ screens[s.dataset.screen] = s; }});
  var qrCard = document.getElementById('qrCard');
  if (qrCard) qrCard.hidden = isMobile;

  // Навигация между экранами со слайдом.
  var current = 'home';
  function go(name, back) {{
    if (name === current) return;
    var from = screens[current], to = screens[name];
    if (!to) return;
    from.hidden = true;
    to.hidden = false;
    to.classList.remove('back'); void to.offsetWidth;
    if (back) to.classList.add('back');
    current = name;
    if (name !== 'home') setupClient(name);
    try {{ window.scrollTo({{top: 0, behavior: 'smooth'}}); }} catch (e) {{ window.scrollTo(0, 0); }}
  }}
  document.querySelectorAll('[data-nav]').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var t = b.dataset.nav;
      go(t, t === 'home');
    }});
  }});

  // Настройка клиентского экрана: платформа + панели.
  function setupClient(client) {{
    var scr = screens[client];
    if (scr.dataset.ready) {{ return; }}
    var plats = JSON.parse(scr.dataset.plats || '[]');
    var plat = plats.indexOf(detected) >= 0 ? detected : plats[0];
    var segBtns = scr.querySelectorAll('[data-plat-btn]');
    var panels = scr.querySelectorAll('.panel');
    function renderPlat() {{
      panels.forEach(function (p) {{
        var show = p.dataset.plat === plat;
        if (show && p.hidden) {{ p.hidden = false; p.classList.remove('appear'); void p.offsetWidth; p.classList.add('appear'); }}
        else if (!show) {{ p.hidden = true; }}
      }});
      segBtns.forEach(function (b) {{ b.classList.toggle('active', b.dataset.platBtn === plat); }});
    }}
    segBtns.forEach(function (b) {{
      b.addEventListener('click', function () {{ plat = b.dataset.platBtn; renderPlat(); }});
    }});
    renderPlat();
    scr.dataset.ready = '1';
  }}

  // Анимация прогресс-бара (после первого кадра).
  requestAnimationFrame(function () {{
    document.querySelectorAll('.fill').forEach(function (f) {{ f.style.width = (f.dataset.w || 0) + '%'; }});
  }});

  // Smart-fallback: нажал CTA, за 3 сек не ушёл в приложение → раскрываем «вручную».
  function armFallback() {{
    setTimeout(function () {{
      if (document.visibilityState !== 'visible') return;
      var d = document.querySelector('.screen:not([hidden]) .panel:not([hidden]) details.manual');
      if (d && !d.open) {{ d.open = true; try {{ d.scrollIntoView({{behavior:'smooth',block:'center'}}); }} catch (e) {{}} }}
    }}, 3000);
  }}
  document.addEventListener('click', function (e) {{
    var t = e.target.closest('.cta'); if (t) armFallback();
  }});

  // Copy — все .copy-btn.
  var subUrl = {sub_url_js};
  document.querySelectorAll('.copy-btn').forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      var done = function () {{ btn.classList.add('copied'); btn.innerText = 'Скопировано ✓';
        setTimeout(function () {{ btn.classList.remove('copied'); btn.innerText = 'Скопировать ссылку'; }}, 1500); }};
      var fb = function () {{ var ta = document.createElement('textarea'); ta.value = subUrl;
        ta.style.position='fixed'; ta.style.top='-1000px'; document.body.appendChild(ta); ta.select();
        try {{ document.execCommand('copy'); }} catch (e) {{}} document.body.removeChild(ta); done(); }};
      if (navigator.clipboard && window.isSecureContext) {{ navigator.clipboard.writeText(subUrl).then(done).catch(fb); }}
      else {{ fb(); }}
    }});
  }});
}})();
</script>
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
    """JSON-метрики агрегатора: счётчики + размеры кешей + средний latency.
    Публично доступен (не sensitive). Prometheus-friendly через json→text."""
    total = _metrics["hits"] + _metrics["misses"] + _metrics["stale"]
    hit_ratio = round(_metrics["hits"] / total, 4) if total > 0 else 0
    avg_upstream_ms = (
        round(_metrics["upstream_ms_sum"] / _metrics["upstream_count"], 1)
        if _metrics["upstream_count"] > 0 else 0
    )
    payload = {
        **_metrics,
        "cache_size": len(_cache),
        "pair_cache_size": len(_pair_cache),
        "inflight_size": len(_inflight),
        "hit_ratio": hit_ratio,
        "avg_upstream_ms": avg_upstream_ms,
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
