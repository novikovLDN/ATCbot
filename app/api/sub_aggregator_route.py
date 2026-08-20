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
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Optional, Tuple
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
    """Красивая HTML-страница с кнопками и копируемым ключом."""
    brand = html_escape(_brand_title())
    support = _support_url() or ""
    support_esc = html_escape(support, quote=True)

    # Traffic + expire — из subscription-userinfo
    ui = _parse_userinfo(headers.get("subscription-userinfo", ""))
    total = int(ui.get("total", 0))
    used = int(ui.get("upload", 0)) + int(ui.get("download", 0))
    left = max(0, total - used) if total > 0 else 0
    left_str = _fmt_bytes(left) if total > 0 else "∞"
    total_str = _fmt_bytes(total) if total > 0 else ""
    expire_str = _fmt_expire(int(ui.get("expire", 0)))
    pct_used = int(used / total * 100) if total > 0 and used < total else 0

    # Deep-links. Happ/Incy — через /open/{client} (crypt-sealing серверный).
    # Остальные клиенты — прямые схемы, работают без криптования.
    base = _deeplink_base()
    q = url_quote(sub_url, safe='')
    happ_href = f"{base}/open/happ?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}"
    incy_href = f"{base}/open/incy?url={q}" if base else f"happ://add/{url_quote(sub_url, safe='/:?&=@%+')}"

    other_clients = [
        ("v2rayTun",   f"v2raytun://import/{sub_url}"),
        ("v2rayNG",    f"v2rayng://install-sub?url={q}"),
        ("Streisand",  f"streisand://import/{sub_url}"),
        ("Hiddify",    f"hiddify://install-config?url={q}"),
        ("Shadowrocket", f"sub://{base64.urlsafe_b64encode(sub_url.encode()).decode().rstrip('=')}"),
    ]

    # Все href идут в HTML → escape. sub_url — в кодоблок и JS-строку.
    happ_href_esc = html_escape(happ_href, quote=True)
    incy_href_esc = html_escape(incy_href, quote=True)
    sub_url_esc = html_escape(sub_url)
    sub_url_js = json.dumps(sub_url)

    other_html = "".join(
        f'<a class="chip" href="{html_escape(href, quote=True)}">{html_escape(name)}</a>'
        for name, href in other_clients
    )

    support_row = (
        f'<a class="support" href="{support_esc}" target="_blank" rel="noopener">Поддержка</a>'
        if support else ""
    )

    stats_row = ""
    if total > 0 or expire_str:
        chunks = []
        if total > 0:
            chunks.append(
                f'<span class="stat"><b>{left_str}</b><span class="muted"> из {total_str}</span></span>'
            )
        if expire_str:
            chunks.append(f'<span class="stat">до <b>{html_escape(expire_str)}</b></span>')
        bar = ""
        if total > 0:
            bar = (
                f'<div class="bar"><div class="fill" style="width:{pct_used}%"></div></div>'
            )
        stats_row = f'<div class="stats">{"".join(chunks)}</div>{bar}'

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="theme-color" content="#f6f7f9">
<meta name="robots" content="noindex,nofollow">
<title>{brand} — Подписка</title>
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
    padding: 24px 16px 40px;
  }}
  .wrap {{ width: 100%; max-width: 480px; }}

  h1 {{ font-size: 22px; font-weight: 700; letter-spacing: -0.01em;
        margin: 8px 0 4px; }}
  p.lead {{ font-size: 14px; line-height: 1.45; color: #666;
            margin: 0 0 20px; }}

  .stats {{ display: flex; flex-wrap: wrap; gap: 10px 18px;
            margin-bottom: 8px; font-size: 14px; color: #333; }}
  .stat {{ display: inline-flex; align-items: baseline; gap: 4px; }}
  .stat b {{ font-weight: 700; color: #111; }}
  .muted {{ color: #999; font-weight: 500; }}

  .bar {{ height: 6px; background: #e5e7eb; border-radius: 999px;
          overflow: hidden; margin: 12px 0 24px; }}
  .fill {{ height: 100%; background: linear-gradient(90deg, #10B981, #059669);
          transition: width 400ms ease; }}

  .btn {{
    display: flex; align-items: center; justify-content: center;
    width: 100%; padding: 15px 22px;
    background: #111; color: #fff;
    border-radius: 12px;
    text-decoration: none;
    font-size: 15px; font-weight: 600; letter-spacing: -0.01em;
    transition: transform 80ms ease, background 80ms ease;
    border: none; cursor: pointer;
    margin-bottom: 10px;
  }}
  .btn:active {{ transform: scale(0.98); background: #000; }}
  .btn.secondary {{
    background: #fff; color: #111;
    border: 1px solid #e1e4e8;
  }}
  .btn.secondary:active {{ background: #f0f2f5; }}

  .section-label {{
    margin: 26px 0 12px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: #9aa1ab;
  }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .chip {{
    display: inline-flex; align-items: center;
    padding: 8px 14px;
    background: #fff; color: #333;
    border: 1px solid #e1e4e8; border-radius: 999px;
    font-size: 13px; font-weight: 500;
    text-decoration: none;
    transition: background 80ms ease, border-color 80ms ease;
  }}
  .chip:hover {{ background: #f0f2f5; border-color: #d1d5db; }}
  .chip:active {{ background: #e5e7eb; }}

  .keyblock {{
    margin-top: 14px;
    background: #eef0f3;
    border: 1px solid #e1e4e8;
    border-radius: 10px;
    padding: 14px 14px 10px;
    font-family: 'SF Mono', Menlo, Consolas, 'Roboto Mono', monospace;
    font-size: 12px; line-height: 1.55;
    color: #1f2328;
    word-break: break-all;
    user-select: all;
    -webkit-user-select: all;
  }}
  .copyrow {{ display: flex; justify-content: flex-end; margin-top: 8px; }}
  .copy {{
    appearance: none; border: none;
    background: transparent; color: #555;
    font-size: 12px; font-weight: 600;
    padding: 6px 10px; border-radius: 6px;
    cursor: pointer;
  }}
  .copy:hover {{ background: rgba(0,0,0,0.04); color: #111; }}
  .copy.copied {{ color: #1a7f37; }}

  .footer {{
    margin-top: 32px;
    display: flex; align-items: center; justify-content: space-between;
    font-size: 11px; letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #9aa1ab;
  }}
  .support {{ color: #9aa1ab; text-decoration: none; }}
  .support:hover {{ color: #111; }}

  @media (prefers-color-scheme: dark) {{
    html, body {{ background: #0f1720; color: #f5f5f2; }}
    h1 {{ color: #ffffff; }}
    p.lead {{ color: #9aa1ab; }}
    .stat b {{ color: #ffffff; }}
    .muted {{ color: #6b7280; }}
    .bar {{ background: #1f2937; }}
    .btn {{ background: #ffffff; color: #0f1720; }}
    .btn:active {{ background: #f5f5f2; }}
    .btn.secondary {{ background: #1f2937; color: #ffffff; border-color: #2a3441; }}
    .btn.secondary:active {{ background: #2a3441; }}
    .chip {{ background: #1f2937; color: #d1d5db; border-color: #2a3441; }}
    .chip:hover {{ background: #2a3441; border-color: #374151; }}
    .keyblock {{ background: #1f2937; border-color: #2a3441; color: #d1d5db; }}
    .copy:hover {{ background: rgba(255,255,255,0.06); color: #ffffff; }}
    .section-label {{ color: #6b7280; }}
    .footer {{ color: #6b7280; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{brand}</h1>
  <p class="lead">Ваша подписка. Выберите клиент — нажмите, чтобы импортировать автоматически.</p>

  {stats_row}

  <a class="btn" href="{happ_href_esc}">Открыть в Happ</a>
  <a class="btn secondary" href="{incy_href_esc}">Открыть в Incy</a>

  <div class="section-label">Другие клиенты</div>
  <div class="chips">{other_html}</div>

  <div class="section-label">Ссылка подписки</div>
  <div class="keyblock" id="link">{sub_url_esc}</div>
  <div class="copyrow">
    <button class="copy" id="copybtn" type="button">Скопировать</button>
  </div>

  <div class="footer">
    <span>{brand} · VPN</span>
    {support_row}
  </div>
</div>

<script>
  // Copy-to-clipboard с graceful fallback для WebView без navigator.clipboard.
  document.getElementById('copybtn').addEventListener('click', function () {{
    var text = {sub_url_js};
    var btn = this;
    var done = function () {{
      btn.classList.add('copied');
      btn.innerText = 'Скопировано';
      setTimeout(function () {{
        btn.classList.remove('copied');
        btn.innerText = 'Скопировать';
      }}, 1500);
    }};
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(text).then(done).catch(fb);
    }} else {{ fb(); }}
    function fb() {{
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.top = '-1000px';
      document.body.appendChild(ta); ta.select();
      try {{ document.execCommand('copy'); }} catch (e) {{}}
      document.body.removeChild(ta);
      done();
    }}
  }});
</script>
</body>
</html>"""


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

    ua_early = request.headers.get("user-agent", "")

    # Fast path — hit кэша.
    cached = _cache_get(token)
    if cached is not None:
        body_bytes, headers = cached
        if _wants_html(ua_early):
            sub_url = str(request.url)
            html = _render_sub_html(token=token, sub_url=sub_url, headers=headers)
            return HTMLResponse(
                content=html,
                headers={"x-cache": "hit", "cache-control": "no-store"},
            )
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

    ua = ua_early or "Aggregator/1.0"
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

    # Браузеру — HTML sub-page, клиенту — raw base64.
    if _wants_html(ua):
        # Юзер в браузере должен получить полный URL для копирования / QR /
        # deep-link'ов. Восстанавливаем с request.url (учитывает proxy-хедеры).
        sub_url = str(request.url)
        html = _render_sub_html(token=token, sub_url=sub_url, headers=headers)
        return HTMLResponse(
            content=html,
            headers={"x-cache": "miss", "cache-control": "no-store"},
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
