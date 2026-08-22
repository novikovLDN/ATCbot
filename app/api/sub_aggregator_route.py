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
    """Onboarding-страница подписки (референс — Remnawave sub-page, наш стиль).

    Блоки:
      1. Карточка подписки — статус, дата окончания, остаток трафика
         (из hybrid subscription-userinfo).
      2. «Установка»: селектор устройства (iOS/Android/macOS/Windows,
         авто-детект по UA) + табы клиента (Happ / Incy; Windows — только Happ).
      3. Пошаговая инструкция: установить приложение (стор-ссылка,
         для iOS Happ + видео «Как сменить регион») → добавить подписку
         (deep-link через /open/{client}, crypt-sealed) → подключиться.
      4. Ссылка подписки + «Скопировать».

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
    # Название подписки — profile-title от панели (или бренд).
    sub_title = html_escape(headers.get("profile-title", "") or _brand_title())

    # ── Deep-links (crypt-sealed через /open/{client}) ─────────────
    base = _deeplink_base()
    q = url_quote(sub_url, safe='')
    deeplinks = {
        "happ": f"{base}/open/happ?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}",
        "incy": f"{base}/open/incy?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}",
    }

    # Инструкция ручного импорта — своя на каждый клиент (совпадает с
    # текстами traffic.info в боте, чтобы юзер везде видел одно и то же).
    manual_steps = {
        "happ": (
            "<b>1.</b> Скопируйте ссылку кнопкой ниже\n"
            "<b>2.</b> Откройте Happ → вкладка «Главная»\n"
            "<b>3.</b> Нажмите <b>+</b> в правом верхнем углу\n"
            "<b>4.</b> Выберите «Вставить из буфера» — подписка добавится"
        ),
        "incy": (
            "<b>1.</b> Скопируйте ссылку кнопкой ниже\n"
            "<b>2.</b> Откройте Incy → «Настройки»\n"
            "<b>3.</b> Нажмите «Импорт» → «Из буфера» — подписка добавится"
        ),
    }

    # ── Пер-платформенные панели (статичный HTML, JS только переключает) ──
    panels: list[str] = []
    for plat, _plat_label in _PLATFORM_LABELS:
        clients = ["happ", "incy"] if plat != "windows" else ["happ"]
        for client in clients:
            store_url = _STORE_LINKS.get(plat, {}).get(client, "")
            if not store_url:
                continue
            store_esc = html_escape(store_url, quote=True)
            deep_esc = html_escape(deeplinks[client], quote=True)
            client_name = _CLIENT_LABELS[client]
            install_hint = html_escape(_INSTALL_HINTS[plat])
            store_btn = html_escape(_STORE_BTN_LABELS[plat])
            region_row = ""
            if plat == "ios" and client == "happ":
                region_row = (
                    f'<a class="ghost-btn" href="{html_escape(_REGION_HELP_URL, quote=True)}" '
                    f'target="_blank" rel="noopener">🎬 Как сменить регион аккаунта</a>'
                )
            manual_html = manual_steps[client].replace("\n", "<br>")
            panels.append(f"""
  <div class="panel" data-plat="{plat}" data-client="{client}" hidden>
    <div class="step">
      <div class="step-dot">1</div>
      <div class="step-body">
        <div class="step-title">Установите и откройте {client_name}</div>
        <p class="step-text">{install_hint}</p>
        <a class="ghost-btn" href="{store_esc}" target="_blank" rel="noopener">{store_btn}</a>
        {region_row}
      </div>
    </div>
    <div class="step">
      <div class="step-dot">2</div>
      <div class="step-body">
        <div class="step-title">Установите подписку</div>
        <p class="step-text">Нажмите кнопку — {client_name} откроется сам, и подписка установится автоматически. Разрешите переход в приложение, если браузер спросит.</p>
        <a class="btn accent" href="{deep_esc}">
          <svg class="btn-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
          Установить подписку в {client_name}
        </a>
        <details class="manual">
          <summary>
            <span>Не открылось? Установить вручную</span>
            <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
          </summary>
          <div class="manual-body">
            <p class="manual-text">{manual_html}</p>
            <div class="keyblock">{{SUB_URL_ESC}}</div>
            <button class="copy-btn" type="button">Скопировать ссылку</button>
          </div>
        </details>
      </div>
    </div>
    <div class="step">
      <div class="step-dot done">3</div>
      <div class="step-body">
        <div class="step-title">Подключите и используйте</div>
        <p class="step-text">В главном разделе нажмите большую кнопку включения в центре. При необходимости выберите другой сервер из списка.</p>
      </div>
    </div>
  </div>""")

    panels_html = "".join(panels)

    plat_tabs = "".join(
        f'<button class="seg" data-plat-btn="{plat}" type="button">{label}</button>'
        for plat, label in _PLATFORM_LABELS
    )

    support_row = (
        f'<a class="support" href="{support_esc}" target="_blank" rel="noopener">Поддержка</a>'
        if support else ""
    )

    sub_url_esc = html_escape(sub_url)
    sub_url_js = json.dumps(sub_url)
    # Панели содержат placeholder {{SUB_URL_ESC}} (f-string внутри цикла не
    # видит sub_url_esc чисто, плюс так URL не эскейпится дважды).
    panels_html = panels_html.replace("{SUB_URL_ESC}", sub_url_esc)

    # QR для десктопа — сканируешь телефоном, та же страница открывается
    # на мобиле, дальше одна кнопка. Блок скрыт на мобильных (JS).
    qr_svg = _qr_svg(sub_url)
    qr_block = f"""
  <div class="qr-card" id="qrCard" hidden>
    <div class="qr-box">{qr_svg}</div>
    <div class="qr-info">
      <div class="qr-title">Быстрее с телефона</div>
      <p class="qr-text">Наведите камеру — страница откроется на телефоне, установка займёт меньше минуты.</p>
    </div>
  </div>""" if qr_svg else ""

    # JS-мапа deep-links для sticky-CTA (href меняется при смене клиента).
    deeplinks_js = json.dumps(deeplinks)

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
    padding: 28px 16px 48px;
  }}
  .wrap {{ width: 100%; max-width: 460px; }}

  .brand {{
    font-size: 24px; font-weight: 700; letter-spacing: -0.02em;
    margin: 0 0 18px;
  }}

  /* ── Карточка подписки (расширенная, по референсу) ── */
  .card {{
    background: #fff;
    border: 1px solid #e1e4e8; border-radius: 16px;
    padding: 18px;
    margin-bottom: 28px;
  }}
  .card-head {{
    display: flex; align-items: center; gap: 12px;
    margin-bottom: 16px;
  }}
  .status-ico {{
    width: 42px; height: 42px; border-radius: 12px;
    background: linear-gradient(135deg, rgba(37,99,235,.14), rgba(16,185,129,.14));
    color: #2563EB;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; font-weight: 700;
    flex: 0 0 42px;
  }}
  .card-title {{ font-size: 16px; font-weight: 700; }}
  .card-sub {{ font-size: 13px; color: #6b7280; }}
  .badge {{
    margin-left: auto;
    padding: 5px 12px; border-radius: 999px;
    background: rgba(16,185,129,.12); color: #059669;
    font-size: 12px; font-weight: 700;
    white-space: nowrap;
  }}
  /* Строки-детали key-value с разделителями */
  .rows {{ border-top: 1px solid #eef0f3; }}
  .row {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 2px;
    border-bottom: 1px solid #eef0f3;
    font-size: 14px;
  }}
  .row-label {{ color: #6b7280; }}
  .row-value {{ font-weight: 700; }}
  .dim {{ color: #9aa1ab; font-weight: 500; }}
  /* Прогресс-бар трафика */
  .bar-wrap {{ padding: 14px 2px 4px; }}
  .bar-meta {{
    display: flex; justify-content: space-between;
    font-size: 12px; color: #9aa1ab; margin-bottom: 7px;
  }}
  .bar {{
    height: 8px; background: #eef0f3; border-radius: 999px; overflow: hidden;
  }}
  .fill {{
    height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, #2563EB, #10B981);
    transition: width 500ms ease;
  }}

  /* ── Установка: заголовок + сегменты ── */
  .section-head {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 14px;
  }}
  .section-title {{ font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }}

  .segs {{
    display: flex; gap: 6px; flex-wrap: wrap;
    margin-bottom: 14px;
  }}
  .seg {{
    appearance: none; border: 1px solid #e1e4e8; background: #fff;
    color: #4b5563; border-radius: 999px;
    padding: 8px 16px; font-size: 13px; font-weight: 600;
    cursor: pointer;
    transition: background 80ms ease, color 80ms ease, border-color 80ms ease;
  }}
  .seg.active {{ background: #111; color: #fff; border-color: #111; }}

  .client-tabs {{
    display: flex; gap: 8px; margin-bottom: 22px;
  }}
  .ctab {{
    flex: 1;
    appearance: none; border: 1px solid #e1e4e8; background: #fff;
    color: #4b5563; border-radius: 12px;
    padding: 12px 0; font-size: 14px; font-weight: 700;
    cursor: pointer;
    transition: background 80ms ease, color 80ms ease, border-color 80ms ease;
  }}
  .ctab.active {{ border-color: #111; color: #111; box-shadow: inset 0 0 0 1px #111; }}

  /* ── Timeline шагов ── */
  .panel {{ position: relative; }}
  .step {{
    display: flex; gap: 14px;
    position: relative;
    padding-bottom: 26px;
  }}
  .step:not(:last-child)::before {{
    content: "";
    position: absolute; left: 15px; top: 34px; bottom: 0;
    width: 2px; background: #e1e4e8;
  }}
  .step-dot {{
    flex: 0 0 32px; height: 32px; border-radius: 50%;
    background: #10B981; color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; font-weight: 700;
    z-index: 1;
  }}
  .step-dot.done {{ background: #e1e4e8; color: #6b7280; }}
  .step-body {{ flex: 1; padding-top: 4px; }}
  .step-title {{ font-size: 16px; font-weight: 700; margin-bottom: 6px; }}
  .step-text {{ font-size: 14px; line-height: 1.5; color: #4b5563; margin: 0 0 12px; }}

  .btn {{
    display: inline-flex; align-items: center; justify-content: center;
    gap: 8px;
    padding: 13px 24px;
    background: #111; color: #fff;
    border-radius: 12px;
    text-decoration: none;
    font-size: 14px; font-weight: 600;
    transition: transform 80ms ease, background 80ms ease, box-shadow 120ms ease;
  }}
  .btn:active {{ transform: scale(0.98); background: #000; }}
  /* Акцентная кнопка «Установить подписку» — главный CTA страницы */
  .btn.accent {{
    width: 100%;
    padding: 15px 24px;
    background: linear-gradient(135deg, #2563EB, #1d4ed8);
    box-shadow: 0 6px 18px rgba(37, 99, 235, .28);
    border-radius: 14px;
    font-size: 15px;
  }}
  .btn.accent:active {{
    transform: scale(0.98);
    background: linear-gradient(135deg, #1d4ed8, #1e40af);
    box-shadow: 0 3px 10px rgba(37, 99, 235, .22);
  }}
  .btn-ico {{ width: 18px; height: 18px; flex: 0 0 18px; }}
  .ghost-btn {{
    display: inline-flex; align-items: center; justify-content: center;
    padding: 11px 18px;
    background: #fff; color: #111;
    border: 1px solid #e1e4e8; border-radius: 12px;
    text-decoration: none;
    font-size: 13px; font-weight: 600;
    margin-right: 8px; margin-bottom: 8px;
    transition: background 80ms ease;
  }}
  .ghost-btn:active {{ background: #f0f2f5; }}

  /* ── «Установить вручную» — разворачивающаяся плашка ── */
  .manual {{
    margin-top: 12px;
    border: 1px dashed #d1d5db;
    border-radius: 12px;
    background: rgba(255,255,255,.55);
    overflow: hidden;
  }}
  .manual summary {{
    list-style: none;
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px;
    font-size: 13px; font-weight: 600; color: #4b5563;
    cursor: pointer;
    user-select: none; -webkit-user-select: none;
  }}
  .manual summary::-webkit-details-marker {{ display: none; }}
  .manual .chev {{
    width: 16px; height: 16px; color: #9aa1ab;
    transition: transform 180ms ease;
  }}
  .manual[open] .chev {{ transform: rotate(180deg); }}
  .manual[open] summary {{ border-bottom: 1px dashed #e1e4e8; }}
  .manual-body {{ padding: 14px 16px 16px; }}
  .manual-text {{
    font-size: 13px; line-height: 1.7; color: #4b5563;
    margin: 0 0 12px;
  }}
  .manual-text b {{ color: #111; }}

  .keyblock {{
    background: #eef0f3;
    border: 1px solid #e1e4e8;
    border-radius: 10px;
    padding: 12px;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 11.5px; line-height: 1.5;
    word-break: break-all;
    user-select: all; -webkit-user-select: all;
    margin-bottom: 10px;
  }}
  .copy-btn {{
    appearance: none; width: 100%;
    border: 1px solid #e1e4e8; background: #fff;
    color: #111; font-size: 13px; font-weight: 600;
    padding: 11px 0; border-radius: 10px; cursor: pointer;
    transition: background 80ms ease, color 120ms ease;
  }}
  .copy-btn:active {{ background: #f0f2f5; }}
  .copy-btn.copied {{ color: #10B981; border-color: #10B981; }}

  /* ── QR для десктопа ── */
  .qr-card {{
    display: flex; align-items: center; gap: 18px;
    background: #fff;
    border: 1px solid #e1e4e8; border-radius: 16px;
    padding: 18px;
    margin: 4px 0 26px;
  }}
  .qr-box {{
    flex: 0 0 128px;
    background: #fff; border-radius: 12px;
    padding: 8px;
    border: 1px solid #eef0f3;
  }}
  .qr-svg {{ width: 112px; height: 112px; display: block; }}
  .qr-title {{ font-size: 15px; font-weight: 700; margin-bottom: 6px; }}
  .qr-text {{ font-size: 13px; line-height: 1.5; color: #6b7280; margin: 0; }}

  /* ── Sticky CTA (мобила) ── */
  .sticky-bar {{
    position: fixed; left: 0; right: 0; bottom: 0;
    padding: 10px 16px calc(10px + env(safe-area-inset-bottom, 0px));
    background: linear-gradient(180deg, rgba(246,247,249,0), rgba(246,247,249,.92) 30%, #f6f7f9);
    display: flex; justify-content: center;
    z-index: 50;
  }}
  .sticky-bar .btn.accent {{ max-width: 460px; }}
  body.has-sticky {{ padding-bottom: 96px; }}

  /* ── Появление панели (spring) ── */
  .panel.appear {{
    animation: rise 320ms cubic-bezier(.34, 1.3, .64, 1) both;
  }}
  @keyframes rise {{
    from {{ opacity: 0; transform: translateY(10px); }}
    to   {{ opacity: 1; transform: none; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .panel.appear {{ animation: none; }}
    .fill, .chev, .btn {{ transition: none; }}
  }}

  .footer {{
    margin-top: 36px;
    display: flex; align-items: center; justify-content: space-between;
    font-size: 11px; letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #9aa1ab;
  }}
  .support {{ color: #9aa1ab; text-decoration: none; }}

  @media (prefers-color-scheme: dark) {{
    html, body {{ background: #0f1720; color: #f5f5f2; }}
    .brand, .section-title, .card-title, .step-title, .row-value {{ color: #fff; }}
    .card {{ background: #16202b; border-color: #2a3441; }}
    .card-sub, .step-text, .row-label {{ color: #9aa1ab; }}
    .rows {{ border-top-color: #2a3441; }}
    .row {{ border-bottom-color: #2a3441; }}
    .bar {{ background: #1f2937; }}
    .badge {{ background: rgba(16,185,129,.16); color: #34d399; }}
    .seg {{ background: #1f2937; color: #9aa1ab; border-color: #2a3441; }}
    .seg.active {{ background: #fff; color: #0f1720; border-color: #fff; }}
    .ctab {{ background: #1f2937; color: #9aa1ab; border-color: #2a3441; }}
    .ctab.active {{ color: #fff; border-color: #fff; box-shadow: inset 0 0 0 1px #fff; }}
    .step:not(:last-child)::before {{ background: #2a3441; }}
    .step-dot.done {{ background: #2a3441; color: #6b7280; }}
    .btn {{ background: #fff; color: #0f1720; }}
    .btn:active {{ background: #f5f5f2; }}
    .btn.accent {{
      background: linear-gradient(135deg, #3b82f6, #2563EB);
      color: #fff;
      box-shadow: 0 6px 18px rgba(37, 99, 235, .35);
    }}
    .btn.accent:active {{ background: linear-gradient(135deg, #2563EB, #1d4ed8); }}
    .ghost-btn {{ background: #1f2937; color: #fff; border-color: #2a3441; }}
    .ghost-btn:active {{ background: #2a3441; }}
    .manual {{ background: rgba(31,41,55,.45); border-color: #374151; }}
    .manual summary {{ color: #9aa1ab; }}
    .manual[open] summary {{ border-bottom-color: #374151; }}
    .manual-text {{ color: #9aa1ab; }}
    .manual-text b {{ color: #fff; }}
    .keyblock {{ background: #1f2937; border-color: #2a3441; color: #d1d5db; }}
    .copy-btn {{ background: #1f2937; color: #fff; border-color: #2a3441; }}
    .copy-btn:active {{ background: #2a3441; }}
    .copy-btn.copied {{ color: #34d399; border-color: #34d399; }}
    /* QR остаётся на белой подложке — иначе не сканируется */
    .qr-card {{ background: #16202b; border-color: #2a3441; }}
    .qr-box {{ background: #fff; border-color: #fff; }}
    .qr-title {{ color: #fff; }}
    .qr-text {{ color: #9aa1ab; }}
    .sticky-bar {{
      background: linear-gradient(180deg, rgba(15,23,32,0), rgba(15,23,32,.92) 30%, #0f1720);
    }}
    .footer {{ color: #6b7280; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">{brand}</div>

  <div class="card">
    <div class="card-head">
      <div class="status-ico">⚡</div>
      <div>
        <div class="card-title">{sub_title}</div>
        <div class="card-sub">Ваша подписка</div>
      </div>
      <div class="badge">Активна</div>
    </div>
    <div class="rows">
      <div class="row">
        <span class="row-label">Осталось</span>
        <span class="row-value">{left_str}{f' <span class="dim">из {total_str}</span>' if total > 0 else ''}</span>
      </div>
      <div class="row">
        <span class="row-label">Использовано</span>
        <span class="row-value">{used_str}</span>
      </div>
      <div class="row">
        <span class="row-label">Истекает</span>
        <span class="row-value">{html_escape(expire_str)}</span>
      </div>
    </div>
    {f'''<div class="bar-wrap">
      <div class="bar-meta"><span>Использовано {pct_used}%</span><span>{total_str}</span></div>
      <div class="bar"><div class="fill" style="width:{pct_used}%"></div></div>
    </div>''' if total > 0 else ''}
  </div>

  <div class="section-head">
    <div class="section-title">Установка</div>
  </div>

  <div class="segs" id="platSegs">{plat_tabs}</div>

  <div class="client-tabs" id="clientTabs">
    <button class="ctab" data-client-btn="happ" type="button">Happ</button>
    <button class="ctab" data-client-btn="incy" type="button">Incy</button>
  </div>

  {panels_html}
{qr_block}
  <div class="section-head" style="margin-top:10px">
    <div class="section-title" style="font-size:17px">Ключ подписки</div>
  </div>
  <div class="card" style="margin-bottom:0">
    <div class="keyblock" style="margin-bottom:12px">{sub_url_esc}</div>
    <button class="copy-btn" type="button">Скопировать ссылку</button>
  </div>

  <div class="footer">
    <span>{brand}</span>
    {support_row}
  </div>
</div>

<!-- Sticky CTA (мобила): кнопка установки всегда под пальцем -->
<div class="sticky-bar" id="stickyBar" hidden>
  <a class="btn accent" id="stickyCta" href="#">
    <svg class="btn-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
    <span id="stickyCtaLabel">Установить подписку в Happ</span>
  </a>
</div>

<script>
(function () {{
  var plat = 'ios';
  var client = 'happ';
  var DEEPLINKS = {deeplinks_js};

  // Авто-детект платформы по UA.
  var ua = navigator.userAgent || '';
  if (/android/i.test(ua)) plat = 'android';
  else if (/iphone|ipad|ipod/i.test(ua)) plat = 'ios';
  else if (/macintosh|mac os x/i.test(ua)) plat = 'macos';
  else if (/windows/i.test(ua)) plat = 'windows';

  var panels = document.querySelectorAll('.panel');
  var platBtns = document.querySelectorAll('[data-plat-btn]');
  var clientBtns = document.querySelectorAll('[data-client-btn]');
  var clientTabs = document.getElementById('clientTabs');
  var stickyBar = document.getElementById('stickyBar');
  var stickyCta = document.getElementById('stickyCta');
  var stickyLabel = document.getElementById('stickyCtaLabel');
  var qrCard = document.getElementById('qrCard');
  var isMobile = (plat === 'ios' || plat === 'android');
  var clientNames = {{happ: 'Happ', incy: 'Incy'}};

  function render() {{
    // Windows — только Happ.
    if (plat === 'windows') {{ client = 'happ'; clientTabs.style.display = 'none'; }}
    else {{ clientTabs.style.display = ''; }}
    panels.forEach(function (p) {{
      var show = (p.dataset.plat === plat && p.dataset.client === client);
      if (show && p.hidden) {{
        p.hidden = false;
        p.classList.remove('appear');
        void p.offsetWidth;  // reflow → restart animation
        p.classList.add('appear');
      }} else if (!show) {{ p.hidden = true; }}
    }});
    platBtns.forEach(function (b) {{
      b.classList.toggle('active', b.dataset.platBtn === plat);
    }});
    clientBtns.forEach(function (b) {{
      b.classList.toggle('active', b.dataset.clientBtn === client);
    }});
    // Sticky CTA — только мобила; href/label следуют выбранному клиенту.
    if (stickyBar) {{
      stickyBar.hidden = !isMobile;
      if (isMobile) {{
        stickyCta.href = DEEPLINKS[client];
        stickyLabel.textContent = 'Установить подписку в ' + clientNames[client];
        document.body.classList.add('has-sticky');
      }}
    }}
    // QR — только десктоп (на мобиле бессмысленно сканировать самого себя).
    if (qrCard) qrCard.hidden = isMobile;
  }}

  platBtns.forEach(function (b) {{
    b.addEventListener('click', function () {{ plat = b.dataset.platBtn; render(); }});
  }});
  clientBtns.forEach(function (b) {{
    b.addEventListener('click', function () {{ client = b.dataset.clientBtn; render(); }});
  }});
  render();

  // Smart-fallback: нажал «Установить подписку», приложение не перехватило
  // переход за 3 сек (мы всё ещё видимы) → авто-раскрываем «Установить
  // вручную» на активной панели и мягко подводим к ней.
  function armFallback() {{
    setTimeout(function () {{
      if (document.visibilityState !== 'visible') return;  // ушли в приложение — ок
      var active = document.querySelector('.panel:not([hidden]) details.manual');
      if (active && !active.open) {{
        active.open = true;
        try {{ active.scrollIntoView({{behavior: 'smooth', block: 'center'}}); }} catch (e) {{}}
      }}
    }}, 3000);
  }}
  document.querySelectorAll('.btn.accent').forEach(function (b) {{
    b.addEventListener('click', armFallback);
  }});

  // Copy-to-clipboard — на каждой панели своя кнопка (.copy-btn),
  // fallback через textarea для старых WebView без navigator.clipboard.
  var subUrl = {sub_url_js};
  document.querySelectorAll('.copy-btn').forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      var done = function () {{
        btn.classList.add('copied');
        btn.innerText = 'Скопировано ✓';
        setTimeout(function () {{
          btn.classList.remove('copied');
          btn.innerText = 'Скопировать ссылку';
        }}, 1500);
      }};
      var fb = function () {{
        var ta = document.createElement('textarea');
        ta.value = subUrl; ta.style.position = 'fixed'; ta.style.top = '-1000px';
        document.body.appendChild(ta); ta.select();
        try {{ document.execCommand('copy'); }} catch (e) {{}}
        document.body.removeChild(ta);
        done();
      }};
      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(subUrl).then(done).catch(fb);
      }} else {{ fb(); }}
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
