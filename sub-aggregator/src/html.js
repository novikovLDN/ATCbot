// HTML sub-page renderer for browsers.
// Same URL as VPN clients — src/ua.js routes browsers here, clients get raw.
//
// Design principles:
//   * NO external assets — все SVG inline, ни одного HTTPS-запроса наружу
//     (в РФ Google Fonts/CDN не грузятся).
//   * System font stack — быстро, нативно.
//   * Auto light/dark через `prefers-color-scheme`.
//   * Ссылка `sub://<publicUrl>` для one-click открытия в клиенте через
//     кастомную схему (плюс традиционные `happ://add/`, `v2raytun://`).
//   * QR-код рендерится server-side через qrcode-svg, инлайним в разметку.

import QRCode from 'qrcode-svg';
import { config } from './config.js';

/** Escape strings for HTML text/attribute contexts. */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Escape strings that go straight into inline JS via JSON. */
function jsStr(s) {
  return JSON.stringify(String(s == null ? '' : s));
}

/** Render QR-код в SVG-строку. `size` = pixel edge. */
function qrSvg(text, size = 240) {
  const qr = new QRCode({
    content: text,
    padding: 2,
    width: size,
    height: size,
    color: '#0F1720',
    background: '#ffffff',
    ecl: 'M',   // medium — баланс между плотностью и tolerance к грязи
    join: true, // merge cells → меньше DOM-узлов
  });
  // qrcode-svg возвращает `<svg ...>...</svg>` строку. Достаточно.
  return qr.svg();
}

/**
 * Render the HTML sub-page.
 * @param {{
 *   publicUrl: string,     // https://sub.<domain>/<token>
 *   subUserinfo: string,   // "upload=..; download=..; total=..; expire=.."
 *   profileTitle?: string, // из main upstream (может быть null)
 *   isRevoked?: boolean,
 * }} data
 * @returns {string} full HTML document
 */
export function renderSubPage(data) {
  const {
    publicUrl,
    subUserinfo = '',
    profileTitle = null,
    isRevoked = false,
  } = data;

  const brand = config.brandName;
  const slogan = config.brandSlogan;
  const primary = config.brandPrimaryColor;
  const bgLight = config.brandBgLight;
  const bgDark = config.brandBgDark;
  const supportUrl = config.supportUrl;
  const botUrl = config.botUrl;

  const stats = parseUserinfo(subUserinfo);
  const usedGb = formatBytes(stats.upload + stats.download);
  const totalGb = stats.total > 0 ? formatBytes(stats.total) : '∞';
  const percent = stats.total > 0
    ? Math.min(100, Math.round(((stats.upload + stats.download) / stats.total) * 100))
    : 0;
  const expiresAt = stats.expire > 0 ? formatDate(stats.expire) : null;

  // `sub://` — one-click add в клиентах, поддерживающих схему; happ:// —
  // Happ-specific; v2raytun://install-config?url= — V2rayTun. Остальные
  // клиенты юзер копирует URL руками через кнопку «Скопировать».
  const clientUrls = {
    happ:      `happ://add/${publicUrl}`,
    v2raytun:  `v2raytun://import/${encodeURIComponent(publicUrl)}`,
    streisand: `streisand://import/${encodeURIComponent(publicUrl)}`,
    v2rayng:   `v2rayng://install-sub?url=${encodeURIComponent(publicUrl)}`,
    hiddify:   `hiddify://install-config?url=${encodeURIComponent(publicUrl)}`,
    shadowrocket: `shadowrocket://add/sub://${btoa(publicUrl)}`,
  };

  return `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex,nofollow">
<title>${esc(profileTitle || brand)}</title>
<style>
  :root {
    --primary: ${esc(primary)};
    --bg: ${esc(bgLight)};
    --bg-card: #ffffff;
    --bg-subtle: #F5F5F2;
    --border: #E5E5E0;
    --text: #0F1720;
    --text-muted: #6B7280;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: ${esc(bgDark)};
      --bg-card: #1a2431;
      --bg-subtle: #131c26;
      --border: #2a3644;
      --text: #f5f5f0;
      --text-muted: #9ca3af;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 460px; margin: 0 auto; padding: 24px 16px 48px; }
  header { display: flex; align-items: center; gap: 10px; padding: 8px 0 20px; }
  header .logo { width: 32px; height: 32px; color: var(--primary); }
  header .logo svg { width: 100%; height: 100%; display: block; }
  header .brand { font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
  header .slogan { font-size: 13px; color: var(--text-muted); margin-left: auto; }

  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 12px;
  }
  h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: -0.02em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin: 0 0 12px; font-weight: 600; }
  .muted { color: var(--text-muted); font-size: 13px; }

  .qr {
    display: block;
    width: 100%;
    max-width: 240px;
    aspect-ratio: 1;
    margin: 16px auto;
    background: #fff;
    border-radius: 12px;
    padding: 10px;
    border: 1px solid var(--border);
  }
  .qr svg { display: block; width: 100%; height: 100%; }

  .url-row {
    display: flex; gap: 8px; align-items: center;
    background: var(--bg-subtle);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 12px;
    margin-top: 8px;
  }
  .url-row code {
    flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font: 13px/1 ui-monospace, "SF Mono", Menlo, monospace;
    color: var(--text);
  }
  .copy-btn {
    background: var(--primary); color: #fff;
    border: 0; border-radius: 8px;
    padding: 8px 14px; font-size: 13px; font-weight: 600;
    cursor: pointer; white-space: nowrap;
  }
  .copy-btn.copied { background: #10B981; }

  .platform { margin-top: 16px; }
  .platform h3 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin: 12px 0 8px; font-weight: 600; }
  .btn-row { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
  a.btn {
    display: flex; align-items: center; justify-content: center;
    gap: 6px;
    background: var(--bg-subtle);
    border: 1px solid var(--border);
    color: var(--text);
    text-decoration: none;
    padding: 12px 8px;
    border-radius: 10px;
    font-size: 14px; font-weight: 500;
    transition: background 0.15s, border-color 0.15s;
  }
  a.btn:hover { background: var(--border); border-color: var(--primary); }
  a.btn strong { font-weight: 600; }

  .stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px 16px;
  }
  .stat-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .stat-value { font-size: 20px; font-weight: 600; letter-spacing: -0.01em; margin-top: 2px; }
  .bar { grid-column: 1 / -1; height: 6px; background: var(--bg-subtle); border-radius: 3px; overflow: hidden; margin-top: 4px; }
  .bar > div { height: 100%; background: var(--primary); border-radius: 3px; transition: width 0.4s; }

  .steps { padding: 0; margin: 0; list-style: none; counter-reset: step; }
  .steps li {
    counter-increment: step;
    display: grid;
    grid-template-columns: 28px 1fr;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .steps li:last-child { border-bottom: none; }
  .steps li::before {
    content: counter(step);
    display: flex; align-items: center; justify-content: center;
    width: 28px; height: 28px;
    background: var(--primary); color: #fff;
    border-radius: 50%;
    font-size: 13px; font-weight: 700;
  }

  footer { text-align: center; margin-top: 24px; font-size: 12px; color: var(--text-muted); }
  footer a { color: var(--text-muted); }

  .revoked { text-align: center; padding: 20px; }
  .revoked .icon { font-size: 40px; }

  @media (max-width: 380px) {
    .btn-row { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">${config.brandLogoSvg || defaultLogoSvg(primary)}</div>
    <div class="brand">${esc(brand)}</div>
    <div class="slogan">${esc(slogan)}</div>
  </header>

  ${isRevoked ? renderRevoked() : renderActive({
    publicUrl, qrSvg: qrSvg(publicUrl), profileTitle,
    stats, usedGb, totalGb, percent, expiresAt, clientUrls, brand,
  })}

  <footer>
    ${supportUrl ? `<a href="${esc(supportUrl)}">Поддержка</a> · ` : ''}
    ${botUrl ? `<a href="${esc(botUrl)}">Бот</a> · ` : ''}
    ${esc(brand)}
  </footer>
</div>

<script>
  (function() {
    var btn = document.getElementById('copy-btn');
    if (!btn) return;
    var url = ${jsStr(publicUrl)};
    btn.addEventListener('click', function() {
      // Modern clipboard first, textarea fallback for iOS Safari.
      var done = function() {
        btn.classList.add('copied');
        btn.textContent = 'Скопировано';
        setTimeout(function() {
          btn.classList.remove('copied');
          btn.textContent = 'Копировать';
        }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done, fallback);
      } else {
        fallback();
      }
      function fallback() {
        var t = document.createElement('textarea');
        t.value = url; t.style.position='fixed'; t.style.top='-9999px';
        document.body.appendChild(t); t.select();
        try { document.execCommand('copy'); done(); } catch(_) {}
        document.body.removeChild(t);
      }
    });
  })();
</script>
</body>
</html>`;
}

function renderActive(ctx) {
  const { publicUrl, qrSvg, profileTitle, stats, usedGb, totalGb, percent, expiresAt, clientUrls, brand } = ctx;
  return `
  <div class="card">
    <h1>${esc(profileTitle || brand)}</h1>
    <p class="muted">Подписка активна</p>
    <div class="qr">${qrSvg}</div>
    <div class="url-row">
      <code id="sub-url">${esc(publicUrl)}</code>
      <button class="copy-btn" id="copy-btn" type="button">Копировать</button>
    </div>
  </div>

  ${stats.total > 0 || expiresAt ? `
  <div class="card">
    <h2>Статус</h2>
    <div class="stats">
      <div>
        <div class="stat-label">Использовано</div>
        <div class="stat-value">${esc(usedGb)}</div>
      </div>
      <div>
        <div class="stat-label">Всего</div>
        <div class="stat-value">${esc(totalGb)}</div>
      </div>
      ${stats.total > 0 ? `<div class="bar"><div style="width:${percent}%"></div></div>` : ''}
      ${expiresAt ? `
      <div style="grid-column: 1 / -1; padding-top: 8px; border-top: 1px solid var(--border);">
        <div class="stat-label">Действует до</div>
        <div class="stat-value">${esc(expiresAt)}</div>
      </div>` : ''}
    </div>
  </div>` : ''}

  <div class="card platform">
    <h2>Установка</h2>
    <h3>iPhone / iPad</h3>
    <div class="btn-row">
      <a class="btn" href="${esc(clientUrls.happ)}"><strong>Happ</strong></a>
      <a class="btn" href="${esc(clientUrls.v2raytun)}"><strong>V2rayTun</strong></a>
      <a class="btn" href="${esc(clientUrls.streisand)}"><strong>Streisand</strong></a>
      <a class="btn" href="${esc(clientUrls.shadowrocket)}"><strong>Shadowrocket</strong></a>
    </div>
    <h3>Android</h3>
    <div class="btn-row">
      <a class="btn" href="${esc(clientUrls.happ)}"><strong>Happ</strong></a>
      <a class="btn" href="${esc(clientUrls.v2raytun)}"><strong>V2rayTun</strong></a>
      <a class="btn" href="${esc(clientUrls.v2rayng)}"><strong>v2rayNG</strong></a>
      <a class="btn" href="${esc(clientUrls.hiddify)}"><strong>Hiddify</strong></a>
    </div>
    <h3>Windows / macOS / Linux</h3>
    <div class="btn-row">
      <a class="btn" href="${esc(clientUrls.hiddify)}"><strong>Hiddify</strong></a>
      <a class="btn" href="${esc(clientUrls.v2raytun)}"><strong>V2rayTun</strong></a>
    </div>
  </div>

  <div class="card">
    <h2>Инструкция</h2>
    <ol class="steps">
      <li>Скачайте клиент из App Store, Google Play или официального сайта.</li>
      <li>Нажмите кнопку установки выше — приложение автоматически добавит подписку.</li>
      <li>Если авто-открытие не сработало — скопируйте ссылку и вставьте её в приложение вручную.</li>
    </ol>
  </div>
  `;
}

function renderRevoked() {
  return `
  <div class="card revoked">
    <div class="icon">⛔</div>
    <h1>Подписка отозвана</h1>
    <p class="muted">Обратитесь в поддержку для восстановления доступа.</p>
  </div>
  `;
}

/** Дефолтный лого — минимальный shield SVG. Заменяется через BRAND_LOGO_SVG. */
function defaultLogoSvg(color) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
    <path d="M9 12l2 2 4-4"/>
  </svg>`;
}

/** Small copy of userinfo.js parser (avoid circular import & keep this file self-contained). */
function parseUserinfo(raw) {
  const out = { upload: 0, download: 0, total: 0, expire: 0 };
  if (!raw || typeof raw !== 'string') return out;
  for (const part of raw.split(';')) {
    const s = part.trim();
    const eq = s.indexOf('=');
    if (eq <= 0) continue;
    const key = s.slice(0, eq).trim().toLowerCase();
    const val = s.slice(eq + 1).trim();
    if (!(key in out)) continue;
    const n = Number.parseInt(val, 10);
    out[key] = Number.isFinite(n) ? n : 0;
  }
  return out;
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return '0 ГБ';
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return gb.toFixed(gb >= 10 ? 0 : 1) + ' ГБ';
  const mb = bytes / (1024 ** 2);
  return mb.toFixed(mb >= 10 ? 0 : 1) + ' МБ';
}

function formatDate(unixSec) {
  try {
    const d = new Date(unixSec * 1000);
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
  } catch { return ''; }
}
