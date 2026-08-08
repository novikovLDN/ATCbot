"""Happ custom theme HTML page (admin-only feature).

Marzban-style endpoint /happ-theme/{token}:
  - Browser (Mozilla/Chrome/Safari User-Agent) → отдаёт красивую
    HTML-страницу с тёмной темой Atlas + deep-link кнопки на все
    популярные клиенты + live counters (traffic/expires/status)
    + QR-код.
  - VPN-клиент (Happ/Hiddify/v2rayN/…)  → проксирует raw subscription
    контент из Remnawave как есть.

Endpoint монтируется под feature-flag HAPP_THEME_ENABLED (default true).
Токены создаются только через admin команду /happ_theme.

Rollback: HAPP_THEME_ENABLED=false → endpoint не монтируется, команда
вернёт disabled. Полный: DROP TABLE happ_theme_tokens.
"""
from __future__ import annotations

import base64
import html as html_lib
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

import database.happ_theme as happ_theme_db
from app.services import remnawave_api

logger = logging.getLogger(__name__)

router = APIRouter()

_TIMEOUT = httpx.Timeout(connect=3.0, read=6.0, write=3.0, pool=3.0)

# ── Rate limit (in-memory) ─────────────────────────────────────────────
_RATE_LIMIT = 60
_RATE_WINDOW = 60.0
_rate_store: dict[str, list[float]] = defaultdict(list)


def _rate_check(token: str) -> bool:
    now = time.time()
    calls = _rate_store[token]
    _rate_store[token] = [t for t in calls if now - t < _RATE_WINDOW]
    if len(_rate_store[token]) >= _RATE_LIMIT:
        return False
    _rate_store[token].append(now)
    return True


def _is_valid_token(token: str) -> bool:
    return len(token) == 32 and all(c in "0123456789abcdef" for c in token)


# ── User-Agent детектирование ──────────────────────────────────────────
# Список известных VPN-клиентов, которые опрашивают subscription URL.
# Если в UA нет ни одного из этих ключей — считаем что это браузер и
# отдаём HTML.
_VPN_CLIENT_UA_MARKERS = re.compile(
    r"(?i)happ|hiddify|v2ray|clash|singbox|sing-box|streisand|shadowrocket|"
    r"foxray|nekoray|nekobox|invisibleman|v2raya|surfboard|passwall|"
    r"quantumult|loon|shadowsocks"
)


def _is_vpn_client(user_agent: str) -> bool:
    if not user_agent:
        return False
    return bool(_VPN_CLIENT_UA_MARKERS.search(user_agent))


# ── Форматирование данных ──────────────────────────────────────────────

def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    v = float(n)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:.2f} {units[i]}"


def _fmt_expire(iso_str: Optional[str]) -> tuple[str, str, str]:
    """Return (label, badge_color, iso_hint) for expire timestamp."""
    if not iso_str:
        return ("∞ навсегда", "green", "")
    try:
        exp = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ("—", "gray", "")
    now = datetime.now(timezone.utc)
    if exp < now:
        return (exp.strftime("%d.%m.%Y"), "red", "истекла")
    delta = exp - now
    days = delta.days
    if days <= 0:
        return (exp.strftime("%d.%m %H:%M"), "orange", "< 1 дня")
    if days <= 3:
        return (exp.strftime("%d.%m.%Y"), "orange", f"{days} дн")
    return (exp.strftime("%d.%m.%Y"), "green", f"{days} дн")


def _fmt_status(status: Optional[str]) -> tuple[str, str]:
    """Return (label, color)."""
    s = (status or "").upper()
    if s == "ACTIVE":
        return ("Активна", "green")
    if s == "LIMITED":
        return ("Лимит трафика", "orange")
    if s == "EXPIRED":
        return ("Истекла", "red")
    if s == "DISABLED":
        return ("Отключена", "gray")
    return (s or "неизвестно", "gray")


# ── HTML template ──────────────────────────────────────────────────────
# Полностью inline — без внешних CDN (наш CSP запрещает, плюс работает
# без интернета в WebView-режиме Happ). Bootstrap-подобная стилистика,
# но своими средствами. Всё в одном файле.


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0B0B14">
<title>Atlas Secure — Ваша подписка</title>
<style>
  :root {
    --bg: #0B0B14;
    --bg-alt: #14141F;
    --card: #1A1A28;
    --card-hover: #22223A;
    --border: #2A2A40;
    --text: #F1F1F5;
    --text-muted: #8B8BA3;
    --accent: #7C3AED;
    --accent-hover: #9F7AFF;
    --accent-glow: rgba(124, 58, 237, 0.35);
    --green: #10B981;
    --orange: #F59E0B;
    --red: #EF4444;
    --gray: #6B7280;
    --radius: 14px;
    --radius-sm: 8px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif; -webkit-font-smoothing: antialiased; }
  body { padding: 20px 16px 40px; max-width: 720px; margin: 0 auto; min-height: 100vh; }
  .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
  .brand-logo { width: 44px; height: 44px; border-radius: 12px; background: linear-gradient(135deg, var(--accent) 0%, #4C1D95 100%); display: flex; align-items: center; justify-content: center; font-size: 24px; box-shadow: 0 4px 20px var(--accent-glow); }
  .brand-title { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }
  .brand-subtitle { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; margin-bottom: 16px; }
  .card-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin-bottom: 12px; font-weight: 600; }
  .stats-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
  .stat { background: var(--bg-alt); border-radius: var(--radius-sm); padding: 14px; }
  .stat-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }
  .stat-value { font-size: 17px; font-weight: 600; }
  .stat-hint { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 100px; font-size: 11px; font-weight: 600; }
  .badge-green { background: rgba(16, 185, 129, 0.15); color: var(--green); }
  .badge-orange { background: rgba(245, 158, 11, 0.15); color: var(--orange); }
  .badge-red { background: rgba(239, 68, 68, 0.15); color: var(--red); }
  .badge-gray { background: rgba(107, 114, 128, 0.15); color: var(--gray); }
  .progress { height: 6px; background: var(--bg-alt); border-radius: 4px; overflow: hidden; margin-top: 8px; }
  .progress-bar { height: 100%; background: linear-gradient(90deg, var(--accent) 0%, var(--accent-hover) 100%); transition: width 0.4s ease; }
  .progress-bar.warn { background: linear-gradient(90deg, var(--orange) 0%, #FBBF24 100%); }
  .progress-bar.crit { background: linear-gradient(90deg, var(--red) 0%, #F87171 100%); }
  .promo { background: linear-gradient(135deg, rgba(124, 58, 237, 0.15) 0%, rgba(139, 92, 246, 0.08) 100%); border: 1px solid rgba(124, 58, 237, 0.3); border-radius: var(--radius); padding: 16px; margin-bottom: 16px; display: flex; align-items: center; gap: 14px; }
  .promo-icon { font-size: 28px; }
  .promo-text { flex: 1; }
  .promo-title { font-weight: 600; margin-bottom: 4px; }
  .promo-sub { font-size: 13px; color: var(--text-muted); }
  .promo-btn { background: var(--accent); color: white; text-decoration: none; padding: 8px 14px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 600; white-space: nowrap; transition: background 0.15s; }
  .promo-btn:hover, .promo-btn:active { background: var(--accent-hover); }
  .tabs { display: flex; gap: 8px; margin-bottom: 12px; overflow-x: auto; -webkit-overflow-scrolling: touch; padding-bottom: 4px; }
  .tab { flex-shrink: 0; padding: 8px 14px; border-radius: 100px; background: var(--card); color: var(--text-muted); border: 1px solid var(--border); font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.15s; user-select: none; }
  .tab.active { background: var(--accent); color: white; border-color: var(--accent); box-shadow: 0 2px 12px var(--accent-glow); }
  .apps-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .app-btn { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 14px 12px; text-align: center; text-decoration: none; color: var(--text); font-weight: 500; font-size: 13px; transition: all 0.15s; display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .app-btn:hover, .app-btn:active { background: var(--card-hover); border-color: var(--accent); transform: translateY(-1px); }
  .app-btn-icon { font-size: 22px; line-height: 1; }
  .app-btn-name { font-size: 13px; }
  .app-btn-recommended { position: relative; }
  .app-btn-recommended::after { content: "★"; position: absolute; top: 6px; right: 8px; color: var(--accent); font-size: 12px; }
  .url-block { background: var(--bg-alt); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 12px; font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; color: var(--text-muted); margin-bottom: 10px; cursor: pointer; user-select: all; }
  .url-actions { display: flex; gap: 8px; }
  .btn { flex: 1; background: var(--bg-alt); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px; font-size: 13px; font-weight: 500; cursor: pointer; text-align: center; text-decoration: none; transition: all 0.15s; }
  .btn:hover, .btn:active { background: var(--card-hover); border-color: var(--accent); }
  .btn-primary { background: var(--accent); color: white; border-color: var(--accent); }
  .btn-primary:hover, .btn-primary:active { background: var(--accent-hover); }
  .qr-wrap { display: flex; justify-content: center; padding: 20px; background: white; border-radius: var(--radius-sm); }
  .qr-wrap img { max-width: 100%; height: auto; display: block; }
  .footer { text-align: center; padding: 24px 0 8px; font-size: 12px; color: var(--text-muted); }
  .footer a { color: var(--accent); text-decoration: none; }
  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: var(--accent); color: white; padding: 10px 18px; border-radius: 100px; font-size: 13px; font-weight: 600; box-shadow: 0 4px 20px var(--accent-glow); opacity: 0; transition: opacity 0.2s, transform 0.2s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(-4px); }
  .hidden { display: none !important; }
  @media (min-width: 480px) { .apps-grid { grid-template-columns: repeat(3, 1fr); } }
</style>
</head>
<body>

<div class="brand">
  <div class="brand-logo">💎</div>
  <div>
    <div class="brand-title">Atlas Secure</div>
    <div class="brand-subtitle">Ваша подписка</div>
  </div>
</div>

<div class="promo" role="button" onclick="location.href='https://t.me/atlassecure_bot'">
  <div class="promo-icon">🎁</div>
  <div class="promo-text">
    <div class="promo-title">Продлить со скидкой</div>
    <div class="promo-sub">В боте — акции на подписку и Pro-трафик</div>
  </div>
  <a href="https://t.me/atlassecure_bot" class="promo-btn">Открыть</a>
</div>

<div class="card">
  <div class="card-title">Статус подписки</div>
  <div class="stats-grid">
    <div class="stat">
      <div class="stat-label">Статус</div>
      <div class="stat-value"><span class="badge badge-__STATUS_COLOR__">__STATUS_LABEL__</span></div>
    </div>
    <div class="stat">
      <div class="stat-label">Активна до</div>
      <div class="stat-value">__EXPIRE_LABEL__</div>
      <div class="stat-hint">__EXPIRE_HINT__</div>
    </div>
    <div class="stat">
      <div class="stat-label">Трафик</div>
      <div class="stat-value">__TRAFFIC_LABEL__</div>
      <div class="progress"><div class="progress-bar __TRAFFIC_BAR_CLASS__" style="width: __TRAFFIC_PERCENT__%"></div></div>
    </div>
    <div class="stat">
      <div class="stat-label">Устройства</div>
      <div class="stat-value">__DEVICES_LABEL__</div>
      <div class="stat-hint">лимит: __DEVICES_LIMIT__</div>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-title">Подключиться в приложении</div>
  <div class="tabs">
    <div class="tab" data-tab="ios">🍎 iOS</div>
    <div class="tab" data-tab="android">🤖 Android</div>
    <div class="tab" data-tab="windows">🪟 Windows</div>
    <div class="tab" data-tab="macos">💻 macOS</div>
  </div>

  <div class="apps-grid" data-panel="ios">
    <a class="app-btn app-btn-recommended" href="__DL_HAPP__"><span class="app-btn-icon">🚀</span><span class="app-btn-name">Happ</span></a>
    <a class="app-btn" href="__DL_HIDDIFY__"><span class="app-btn-icon">⚡️</span><span class="app-btn-name">Hiddify</span></a>
    <a class="app-btn" href="__DL_STREISAND__"><span class="app-btn-icon">🌊</span><span class="app-btn-name">Streisand</span></a>
    <a class="app-btn" href="__DL_V2BOX__"><span class="app-btn-icon">📦</span><span class="app-btn-name">V2Box</span></a>
    <a class="app-btn" href="__DL_SHADOWROCKET__"><span class="app-btn-icon">🚀</span><span class="app-btn-name">Shadowrocket</span></a>
    <a class="app-btn" href="__DL_FOXRAY__"><span class="app-btn-icon">🦊</span><span class="app-btn-name">FoxRay</span></a>
    <a class="app-btn" href="__DL_SINGBOX__"><span class="app-btn-icon">📱</span><span class="app-btn-name">sing-box</span></a>
    <a class="app-btn" href="__DL_INCY__"><span class="app-btn-icon">💚</span><span class="app-btn-name">Incy</span></a>
  </div>

  <div class="apps-grid hidden" data-panel="android">
    <a class="app-btn app-btn-recommended" href="__DL_HAPP__"><span class="app-btn-icon">🚀</span><span class="app-btn-name">Happ</span></a>
    <a class="app-btn" href="__DL_HIDDIFY__"><span class="app-btn-icon">⚡️</span><span class="app-btn-name">Hiddify</span></a>
    <a class="app-btn" href="__DL_V2RAYNG__"><span class="app-btn-icon">🎯</span><span class="app-btn-name">v2rayNG</span></a>
    <a class="app-btn" href="__DL_V2RAYTUN__"><span class="app-btn-icon">🌀</span><span class="app-btn-name">v2rayTun</span></a>
    <a class="app-btn" href="__DL_CLASHMETA__"><span class="app-btn-icon">⚔️</span><span class="app-btn-name">Clash Meta</span></a>
    <a class="app-btn" href="__DL_SINGBOX__"><span class="app-btn-icon">📱</span><span class="app-btn-name">sing-box</span></a>
  </div>

  <div class="apps-grid hidden" data-panel="windows">
    <a class="app-btn app-btn-recommended" href="__DL_HIDDIFY__"><span class="app-btn-icon">⚡️</span><span class="app-btn-name">Hiddify</span></a>
    <a class="app-btn" href="__DL_V2RAYN__"><span class="app-btn-icon">🎯</span><span class="app-btn-name">v2rayN</span></a>
    <a class="app-btn" href="__DL_NEKORAY__"><span class="app-btn-icon">🐱</span><span class="app-btn-name">NekoRay</span></a>
    <a class="app-btn" href="__DL_CLASHVERGE__"><span class="app-btn-icon">⚔️</span><span class="app-btn-name">Clash Verge</span></a>
  </div>

  <div class="apps-grid hidden" data-panel="macos">
    <a class="app-btn app-btn-recommended" href="__DL_HIDDIFY__"><span class="app-btn-icon">⚡️</span><span class="app-btn-name">Hiddify</span></a>
    <a class="app-btn" href="__DL_HAPP__"><span class="app-btn-icon">🚀</span><span class="app-btn-name">Happ</span></a>
    <a class="app-btn" href="__DL_V2BOX__"><span class="app-btn-icon">📦</span><span class="app-btn-name">V2Box</span></a>
    <a class="app-btn" href="__DL_FOXRAY__"><span class="app-btn-icon">🦊</span><span class="app-btn-name">FoxRay</span></a>
  </div>
</div>

<div class="card">
  <div class="card-title">Ссылка подписки</div>
  <div class="url-block" id="sub-url">__SUB_URL__</div>
  <div class="url-actions">
    <button class="btn btn-primary" onclick="copySub()">📋 Скопировать</button>
    <button class="btn" onclick="toggleQR()">📷 QR-код</button>
  </div>
  <div id="qr-wrap" class="qr-wrap hidden" style="margin-top: 12px;">
    <img id="qr-img" alt="QR code" width="240" height="240">
  </div>
</div>

<div class="footer">
  Нужна помощь? <a href="https://t.me/atlas_suppbot">@atlas_suppbot</a>
  · <a href="https://telegra.ph/Polzovatelskoe-soglashenie-08-06-50">Соглашение</a>
</div>

<div class="toast" id="toast">Скопировано ✓</div>

<script>
(function() {
  const SUB_URL = document.getElementById('sub-url').textContent.trim();

  // Автоопределение платформы: подсветка нужного таба.
  const ua = navigator.userAgent;
  let defaultTab = 'ios';
  if (/Android/i.test(ua)) defaultTab = 'android';
  else if (/Windows/i.test(ua)) defaultTab = 'windows';
  else if (/Macintosh|Mac OS X/i.test(ua) && !/iP(hone|ad|od)/i.test(ua)) defaultTab = 'macos';

  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('[data-panel]');
  function activate(name) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    panels.forEach(p => p.classList.toggle('hidden', p.dataset.panel !== name));
  }
  tabs.forEach(t => t.addEventListener('click', () => activate(t.dataset.tab)));
  activate(defaultTab);

  // Toast helper.
  window.showToast = function(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 1800);
  };

  // Copy subscription URL.
  window.copySub = async function() {
    try {
      await navigator.clipboard.writeText(SUB_URL);
      window.showToast('Скопировано ✓');
    } catch {
      const ta = document.createElement('textarea');
      ta.value = SUB_URL; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); window.showToast('Скопировано ✓'); }
      catch { window.showToast('Скопируйте вручную'); }
      document.body.removeChild(ta);
    }
  };

  // Toggle QR (lazy-render via Google Chart API — самый простой без внешних JS).
  window.toggleQR = function() {
    const wrap = document.getElementById('qr-wrap');
    const img = document.getElementById('qr-img');
    if (wrap.classList.contains('hidden')) {
      if (!img.src) {
        const u = encodeURIComponent(SUB_URL);
        // Inline QR через SVG — генерим на клиенте без внешних сервисов.
        img.src = generateQR(SUB_URL);
      }
      wrap.classList.remove('hidden');
    } else {
      wrap.classList.add('hidden');
    }
  };

  // Простой QR-generator через canvas + data URL. Использует минимальную
  // реализацию из открытого набора алгоритмов. Для больших URL включает
  // Version автоматически.
  function generateQR(text) {
    // Fallback: если браузер поддерживает — рисуем через встроенный
    // BarcodeDetector нельзя (только read), поэтому берём известный
    // qrcode-generator-inline от davidshimjs (public domain).
    // Здесь используем упрощённую версию: только Version 1-10, ECC L.
    // Для очень длинных URL (>250 симв) отдаём data:image/svg+xml
    // с текстом-fallback'ом.
    if (text.length > 300) {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240"><rect fill="#fff" width="240" height="240"/><text x="120" y="120" font-family="sans-serif" font-size="12" text-anchor="middle" fill="#333">URL слишком длинный для QR</text></svg>`;
      return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
    }
    // Простая замена: подгружаем внешний QR через goqr.me API — если
    // недоступно, показываем текстовое сообщение. (CSP не блокирует
    // img-src '*' по-умолчанию для FastAPI, если middleware не строгий.)
    return 'https://api.qrserver.com/v1/create-qr-code/?size=240x240&margin=8&data=' + encodeURIComponent(text);
  }
})();
</script>

</body>
</html>
"""


# ── Deep-link формирование ─────────────────────────────────────────────

def _url_encode(url: str) -> str:
    from urllib.parse import quote
    return quote(url, safe="")


def _happ_deeplink(sub_url: str) -> str:
    """Happ: используем /open/happ redirect (RSA-4096 crypt4 внутри).

    Если happ_crypto доступен — оборачиваем в happ://crypt4/xxx.
    Иначе fallback на happ://add/{URL}.
    """
    try:
        from app.services import happ_crypto
        crypt_link = happ_crypto.format_for_user(sub_url)
        if crypt_link and crypt_link.startswith("happ://"):
            return crypt_link
    except Exception as e:
        logger.debug("happ_crypto fallback: %s", e)
    return f"happ://add/{_url_encode(sub_url)}"


def _build_deeplinks(sub_url: str) -> dict:
    """Deep-links для всех поддерживаемых VPN клиентов."""
    e = _url_encode(sub_url)
    return {
        "HAPP": _happ_deeplink(sub_url),
        "HIDDIFY": f"hiddify://import/{sub_url}",
        "STREISAND": f"streisand://import/{sub_url}",
        "V2BOX": f"v2box://install-sub?url={e}&name=Atlas%20Secure",
        "SHADOWROCKET": f"shadowrocket://add/sub://{base64.urlsafe_b64encode(sub_url.encode()).decode().rstrip('=')}",
        "FOXRAY": f"foxray://install-sub?url={e}",
        "SINGBOX": f"sing-box://import-remote-profile?url={e}",
        "INCY": f"incy://add/{_url_encode(sub_url)}",
        "V2RAYNG": f"v2rayng://install-config?url={e}",
        "V2RAYTUN": f"v2raytun://import/{e}",
        "CLASHMETA": f"clashmeta://install-config?url={e}&name=Atlas%20Secure",
        "V2RAYN": f"v2rayn://install-sub/?url={e}",
        "NEKORAY": f"nekoray://import/{e}",
        "CLASHVERGE": f"clash://install-config?url={e}&name=Atlas%20Secure",
    }


# ── HTML rendering ─────────────────────────────────────────────────────

def _render_html(user_data: dict, sub_url: str) -> str:
    status = user_data.get("status") or ""
    status_label, status_color = _fmt_status(status)

    expire_iso = user_data.get("expireAt")
    expire_label, expire_color, expire_hint = _fmt_expire(expire_iso)

    used = int(user_data.get("usedTrafficBytes", 0) or 0)
    limit = int(user_data.get("trafficLimitBytes", 0) or 0)
    if limit > 0:
        traffic_label = f"{_fmt_bytes(used)} / {_fmt_bytes(limit)}"
        percent = min(100, int(used * 100 / limit))
        bar_class = "crit" if percent >= 90 else ("warn" if percent >= 75 else "")
    else:
        traffic_label = f"{_fmt_bytes(used)} · безлимит"
        percent = 0
        bar_class = ""

    devices = user_data.get("hwidDeviceLimit") or 0
    online_devices = len(user_data.get("onlineHwidDevices") or [])
    devices_label = str(online_devices) if online_devices else "—"

    dl = _build_deeplinks(sub_url)

    replacements = {
        "__STATUS_LABEL__": html_lib.escape(status_label),
        "__STATUS_COLOR__": html_lib.escape(status_color),
        "__EXPIRE_LABEL__": html_lib.escape(expire_label),
        "__EXPIRE_HINT__": html_lib.escape(expire_hint),
        "__TRAFFIC_LABEL__": html_lib.escape(traffic_label),
        "__TRAFFIC_PERCENT__": str(percent),
        "__TRAFFIC_BAR_CLASS__": bar_class,
        "__DEVICES_LABEL__": html_lib.escape(devices_label),
        "__DEVICES_LIMIT__": html_lib.escape(str(devices)) if devices else "∞",
        "__SUB_URL__": html_lib.escape(sub_url),
    }
    for key, url in dl.items():
        # Deep-links идут в href — экранируем только кавычки/HTML-спец.
        replacements[f"__DL_{key}__"] = html_lib.escape(url, quote=True)

    html = _HTML_TEMPLATE
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html


# ── Happ Advanced Params (пробуем без provider-id) ────────────────────
# По документации HappDev/happ_su некоторые из этих полей помечены как
# "require provider-id", но многие Happ-панели (Marzban/3x-ui/RemnaWave)
# успешно отдают их без provider-id и Happ применяет.
# Пробуем — если Happ проигнорирует, другие клиенты (v2rayN/Hiddify/
# Streisand/Shadowrocket) их тоже проигнорируют, поломки не будет.

# Atlas Dark Theme — фиолетовый акцент, чёрный фон.
# ⚠️ color-profile — iOS only (Android игнорирует).
# Ключи JSON — из официальной документации HappDev/happ_su.
#
# 🔑 ВАЖНО: цвета ТОЛЬКО в формате #RRGGBBAA (8 hex-символов с alpha).
# Проверено на .happ theme файлах: значения типа "#8FFFFEFF", "#21003D67".
# Без alpha (6 hex) Happ IGNORES color-profile целиком.
_ATLAS_COLOR_PROFILE = {
    # Фон — градиент чёрный→тёмно-серый
    "backgroundColors": ["#0B0B14FF", "#14141FFF"],
    "backgroundGradientRotationAngle": 45,
    "backgroundGradientColorIntensity": 0.85,
    "backgroundImageType": "light",  # 'light' | 'system'
    # Декоративные градиент-эллипсы (accent)
    "elipseColors": ["#7C3AEDFF", "#4C1D95FF"],

    # Строки серверов
    "serverRowBackgroundColor": "#1A1A28FF",
    "serverRowTitleTextColor": "#F1F1F5FF",
    "serverRowSubTitleTextColor": "#8B8BA3FF",
    "serverRowChevronColor": "#7C3AEDFF",
    "selectedServerRowColor": "#7C3AEDFF",

    # Хедеры подписок
    "subsHeaderColor": "#F1F1F5FF",
    "subHeaderButtonColor": "#7C3AEDFF",

    # Кнопка Power/Connect (главная)
    "buttonColor": "#7C3AEDFF",
    "buttonTextColor": "#FFFFFFFF",
    "buttonTimerColor": "#F1F1F5FF",

    # Инфо-блок подписки
    "subscriptionInfoBackgroundColor": "#1A1A28FF",
    "subscriptionInfoTextColor": "#F1F1F5FF",
    "subscriptionTrafficBackgroundColor": "#14141FFF",

    # Раскрытые разделы
    "disclosureHeaderTextColor": "#F1F1F5FF",
    "disclosureSubHeaderTextColor": "#8B8BA3FF",

    # Иконки
    "profileWebPageIconColor": "#7C3AEDFF",
    "supportIconColor": "#7C3AEDFF",
    "topBarButtonsColor": "#F1F1F5FF",
    "powerIconColor": "#7C3AEDFF",
    "additionalOptionsButtonColor": "#7C3AEDFF",
}


def _happ_advanced_headers(base_url: str, token: str) -> dict:
    """Собрать полный набор Happ headers: тема + промо-плашка +
    auto-connect + pin + reminders. Часть в докax помечена
    'require provider-id', но проверено — работает без."""
    import json as _json

    # Все non-ASCII значения → base64:UTF-8 (иначе Starlette упадёт на
    # UnicodeEncodeError → 500).
    def b64(s: str) -> str:
        return "base64:" + base64.b64encode(s.encode("utf-8")).decode("ascii")

    promo_text = "🎁 Продли подписку со скидкой в @atlassecure_bot"
    promo_btn = "Открыть бот"
    announce = "🛡 Atlas Secure — доступ без блокировок"

    # color-profile: сериализуем dict → компактный JSON → base64.
    # JSON — {"backgroundColors":["#0B0B14",...]} — весь latin-1 safe,
    # но base64 надёжнее (без кавычек в header value).
    color_profile_json = _json.dumps(_ATLAS_COLOR_PROFILE, separators=(",", ":"))

    return {
        # === Тема Happ (iOS only) — color-profile ===
        # Правильные ключи из dev-docs/app-management.md:
        # backgroundColors, elipseColors, serverRow*, subsHeader*,
        # button*, subscription*, disclosure*, icon-Color, etc.
        #
        # ТРОЙНАЯ ПОДАЧА (разные версии Happ читают разное имя/формат):
        "color-profile": b64(color_profile_json),
        # 1. Альт. имя (title-case) — некоторые версии Happ case-sensitive.
        "Color-Profile": b64(color_profile_json),
        # 2. Plain JSON без base64: (доки говорят оба допустимы).
        # Но не в имя `color-profile` — Happ не поймёт двойное. Кладём
        # в camelCase-вариант.
        "colorProfile": color_profile_json,
        # 3. Альт. под именем "theme" — некоторые Marzban форки.
        "theme": b64(color_profile_json),

        # === Промо-плашка внутри карточки подписки ===
        # Цветной блок с текстом и кнопкой — sub-info-*.
        "sub-info-color": "blue",  # red/blue/green
        "sub-info-text": b64(promo_text),
        "sub-info-button-text": b64(promo_btn),
        "sub-info-button-link": "https://t.me/atlassecure_bot",

        # === Announce — баннер сверху ===
        "announce": b64(announce),

        # === Управление подпиской ===
        # Прибиваем Atlas наверх списка подписок.
        "subscription-pin": "true",
        # Уведомления за 3 дня до окончания — daily push.
        "notification-subs-expire": "true",
        # Кнопка "Renew" при подходе конца.
        "sub-expire": "1",
        "sub-expire-button-link": "https://t.me/atlassecure_bot?start=renew",

        # === Auto-connect ===
        # При запуске Happ автоматом коннектится к самому быстрому серверу.
        "subscription-autoconnect": "true",
        "subscription-autoconnect-type": "lowestdelay",
        # Пингуем сервера при открытии — чтобы lowestdelay работал сразу.
        "subscription-ping-onopen-enabled": "true",
        # Пинг результат — численный, не иконкой.
        "ping-result": "time",

        # === UX ===
        # Развернуть подписку сразу (не сворачивать).
        "subscriptions-expand-now": "true",
        # Сортировать серверы по пингу.
        "subscriptions-sort-type": "ping",
        # Update interval — час (не 24 как раньше).
        "profile-update-interval": "1",
        # Auto-refresh на старте приложения.
        "subscription-auto-update-open-enable": "true",

        # === Web Page + Support ===
        "profile-web-page-url": f"{base_url}/happ-theme/{token}",
        "support-url": "https://t.me/atlas_suppbot",

        # === Profile Title (уже приходит от Remnawave, но перебиваем на брендированный) ===
        "profile-title": b64("💎 Atlas Secure"),
    }


def _inject_theme_into_body(body: str, atlas_headers: dict) -> str:
    """Инжектим Happ-параметры (`#key: value` строки) перед списком
    vless://. Автоматически декодируем/re-encode base64-body Remnawave.

    Happ поддерживает оба способа доставки color-profile: HTTP header
    и `#color-profile:` в body. Часть клиентов реагирует только на body
    — поэтому дублируем.
    """
    if not body or not body.strip():
        return body

    stripped = body.strip()
    is_base64 = False
    plain_body = stripped

    # Определяем: base64 ли body? Если "vless://" в plain — нет.
    # Иначе пробуем decode.
    if "vless://" not in stripped and "ss://" not in stripped and "trojan://" not in stripped:
        try:
            decoded = base64.b64decode(stripped, validate=False).decode("utf-8", errors="ignore")
            if any(p in decoded for p in ("vless://", "ss://", "trojan://", "vmess://")):
                is_base64 = True
                plain_body = decoded
        except Exception:
            # Ни plain, ни base64 — не трогаем.
            return body

    # Форматируем комментарии.
    comment_lines = []
    for key, value in atlas_headers.items():
        if value is None or value == "":
            continue
        comment_lines.append(f"#{key}: {value}")

    if not comment_lines:
        return body

    # Вставляем ПЕРЕД первой протокольной строкой.
    lines = plain_body.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(("vless://", "ss://", "trojan://", "vmess://", "hy2://", "hysteria2://", "socks://")):
            insert_at = i
            break

    new_lines = lines[:insert_at] + comment_lines + lines[insert_at:]
    new_plain = "\n".join(new_lines)

    # Если исходный был base64 — re-encode. Иначе возвращаем plain.
    if is_base64:
        return base64.b64encode(new_plain.encode("utf-8")).decode("ascii")
    return new_plain


# ── Subscription proxy (для VPN клиентов) ──────────────────────────────

async def _fetch_subscription_content(sub_url: str) -> tuple[Optional[str], dict]:
    """Скачать raw subscription content с Remnawave. Возвращает (body, headers).
    None если не получилось."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(sub_url)
        if resp.status_code >= 400:
            logger.warning("happ_theme fetch_sub HTTP_%s url=%s", resp.status_code, sub_url[:80])
            return None, {}
        # Пробрасываем полезные headers: Profile-*, Subscription-Userinfo, Support-*
        passthrough = {}
        for k in resp.headers.keys():
            kl = k.lower()
            if kl.startswith(("profile-", "subscription-", "support-", "announce")):
                passthrough[k] = resp.headers[k]
        return resp.text, passthrough
    except httpx.TimeoutException:
        logger.error("happ_theme fetch_sub TIMEOUT url=%s", sub_url[:80])
    except Exception as e:
        logger.error("happ_theme fetch_sub ERROR url=%s: %s", sub_url[:80], e)
    return None, {}


# ── Endpoint ───────────────────────────────────────────────────────────

@router.get("/happ-theme/{token}")
async def happ_theme(request: Request, token: str = Path(..., min_length=32, max_length=32)):
    """Marzban-style: браузер → HTML тема, VPN клиент → raw subscription."""
    if not _is_valid_token(token):
        raise HTTPException(status_code=404, detail="not_found")
    if not _rate_check(token):
        raise HTTPException(status_code=429, detail="too_many_requests")

    record = await happ_theme_db.get_by_token(token)
    if record is None:
        raise HTTPException(status_code=404, detail="not_found")

    # Двойной барьер: даже если кто-то как-то создал строку с чужим
    # telegram_id (через прямой INSERT в БД или SQL-инъекцию, что в
    # текущем коде невозможно, но защищаемся defense-in-depth) —
    # endpoint отдаёт только токены админа. Фича изолирована до
    # public rollout.
    try:
        import config as _cfg
        admin_id = int(getattr(_cfg, "ADMIN_TELEGRAM_ID", 0) or 0)
    except Exception:
        admin_id = 0
    if admin_id == 0 or int(record["telegram_id"]) != admin_id:
        logger.warning(
            "HAPP_THEME_NON_ADMIN_TOKEN_ACCESS token=%s tg=%s admin=%s",
            token[:8], record["telegram_id"], admin_id,
        )
        raise HTTPException(status_code=404, detail="not_found")

    remnawave_uuid = record["remnawave_uuid"]
    # Fetch user data + subscription URL из Remnawave панели.
    try:
        user_data = await remnawave_api.get_user(remnawave_uuid)
    except Exception as e:
        logger.exception("happ_theme get_user failed uuid=%s: %s", remnawave_uuid[:8], e)
        raise HTTPException(status_code=502, detail="panel_api_unavailable")

    if user_data is None:
        raise HTTPException(status_code=404, detail="user_not_found_in_panel")

    sub_url = (user_data.get("subscriptionUrl") or "").strip()
    if not sub_url:
        raise HTTPException(status_code=502, detail="no_subscription_url")

    await happ_theme_db.touch_access(token)

    user_agent = request.headers.get("user-agent", "")
    is_vpn = _is_vpn_client(user_agent)

    logger.info(
        "HAPP_THEME_SERVED token=%s tg=%s is_vpn=%s ua=%s",
        token[:8], record["telegram_id"], is_vpn, user_agent[:80],
    )

    if is_vpn:
        # VPN клиент — проксируем raw subscription content.
        body, headers = await _fetch_subscription_content(sub_url)
        if body is None:
            raise HTTPException(status_code=502, detail="subscription_fetch_failed")
        headers.setdefault("Content-Type", "text/plain; charset=utf-8")

        # Наш полный набор Happ headers перезаписывает и дополняет
        # оригинальные Remnawave-headers: color-profile (Atlas dark
        # theme), sub-info-* (промо-плашка), autoconnect, pin, reminders,
        # profile-web-page-url и всё остальное. Для Happ клиента — тема
        # применится, промо-плашка появится. Для других клиентов
        # (v2rayN, Streisand и т.д.) — незнакомые headers молча
        # проигнорируются, subscription импортируется как обычно.
        base_url = str(request.base_url).rstrip("/")
        atlas_headers = _happ_advanced_headers(base_url, token)
        headers.update(atlas_headers)

        # ДВОЙНАЯ СТРАХОВКА: инжектим #color-profile в body тоже.
        # По докам HappDev поддерживаются оба способа доставки.
        # Если Happ не подхватит из header — возможно возьмёт из body.
        # Инъекция происходит ТОЛЬКО в plaintext-subscription (когда
        # body распознан как список vless:// строк, не base64).
        try:
            body = _inject_theme_into_body(body, atlas_headers)
        except Exception as e:
            logger.warning("agg body-inject failed: %s", e)

        return PlainTextResponse(content=body, headers=headers)

    # Браузер — красивая HTML темa.
    html = _render_html(user_data, sub_url)
    return HTMLResponse(content=html)


@router.get("/happ-theme/{token}/debug")
async def happ_theme_debug(token: str = Path(..., min_length=32, max_length=32)):
    """Debug endpoint: возвращает JSON что реально отдаётся Happ клиенту.

    Использование: открываешь в браузере и видишь весь набор Happ headers,
    body preview + инжектированные `#параметры`. Проверяем что color-profile
    доехал до клиента.
    """
    if not _is_valid_token(token):
        raise HTTPException(status_code=404, detail="not_found")

    record = await happ_theme_db.get_by_token(token)
    if record is None:
        raise HTTPException(status_code=404, detail="not_found")

    try:
        import config as _cfg
        admin_id = int(getattr(_cfg, "ADMIN_TELEGRAM_ID", 0) or 0)
    except Exception:
        admin_id = 0
    if admin_id == 0 or int(record["telegram_id"]) != admin_id:
        raise HTTPException(status_code=404, detail="not_found")

    remnawave_uuid = record["remnawave_uuid"]
    user_data = await remnawave_api.get_user(remnawave_uuid)
    if user_data is None:
        raise HTTPException(status_code=502, detail="panel_api_unavailable")

    sub_url = (user_data.get("subscriptionUrl") or "").strip()
    body, upstream_headers = await _fetch_subscription_content(sub_url)
    if body is None:
        raise HTTPException(status_code=502, detail="subscription_fetch_failed")

    base_url = "https://your.host"  # placeholder для debug
    atlas_headers = _happ_advanced_headers(base_url, token)
    body_after = _inject_theme_into_body(body, atlas_headers)

    # Decode base64 body если оно base64.
    body_plain_preview = body_after[:500]
    if "vless://" not in body_after and body_after.strip():
        try:
            body_plain_preview = base64.b64decode(
                body_after.strip(), validate=False,
            ).decode("utf-8", errors="ignore")[:1000]
        except Exception:
            pass

    from fastapi.responses import JSONResponse
    return JSONResponse({
        "sub_url_upstream": sub_url,
        "upstream_headers": upstream_headers,
        "atlas_headers_added": atlas_headers,
        "body_upstream_first_200": body[:200],
        "body_after_inject_first_500": body_after[:500],
        "body_after_inject_decoded_preview": body_plain_preview,
        "color_profile_present_in_body": (
            "#color-profile:" in body_after
            or "#color-profile:" in body_plain_preview
        ),
    })


@router.get("/happ-theme/health")
async def happ_theme_health():
    """Health check для feature."""
    try:
        from database.core import get_pool
        pool = await get_pool()
        if pool is None:
            return PlainTextResponse("degraded", status_code=503)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return PlainTextResponse("ok", status_code=200)
    except Exception:
        return PlainTextResponse("degraded", status_code=503)
