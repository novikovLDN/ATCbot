"""
Deep link redirect endpoint.

Telegram blocks custom URL schemes (happ://, incy://) in inline
keyboard buttons. This endpoint serves an HTML page that redirects
the browser to the custom scheme, opening the VPN client and importing
the subscription automatically.

If the auto-redirect fails (built-in Telegram browser on iOS sometimes
blocks `happ://`), the page also shows the deep link as a monospaced
block with a Copy button so the user can import it manually.

Usage:
    GET /open/{client}?url={subscription_url}[&name={profile_name}]

Supported clients: happ, incy, v2raytun, karing, stash, clash (Clash Verge).
`url` must be an absolute http(s) URL (no whitespace/control chars, ≤2048);
anything else is rejected with 400 before a deep link is built.

Import schemes (official docs):
  karing   karing://install-config?url=<enc>&name=<enc>
           https://karing.app/en/cooperation/scheme
  stash    stash://install-config?url=<enc>
           https://stash.wiki/en/faq/url-schema
  clash    clash://install-config?url=<enc>   (Clash Verge Rev, desktop)
           https://www.clashverge.dev/guide/url_schemes.html
  v2raytun v2raytun://import/<subscription_link>
           https://docs.v2raytun.com/deep-link
"""

import json
import logging
from html import escape as html_escape
from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_URL_LEN = 2048
_MAX_NAME_LEN = 64
_DEFAULT_PROFILE_NAME = "Atlas Secure"
# Never valid unencoded in a URL (RFC 3986) — would break out of the page/scheme.
_URL_FORBIDDEN_CHARS = frozenset('"<>\\^`{|}')


def _is_valid_sub_url(raw_url: str) -> bool:
    """Only absolute http(s) subscription URLs may be turned into a deep link.

    Rejects javascript:/data:/custom schemes, relative paths, whitespace and
    control characters (they would change the meaning of the deep link).
    """
    if not raw_url or len(raw_url) > _MAX_URL_LEN:
        return False
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F or ch in _URL_FORBIDDEN_CHARS for ch in raw_url):
        return False
    try:
        parts = urlsplit(raw_url)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme in ("https", "http") and bool(host)


def _clean_profile_name(name: Optional[str]) -> str:
    """Profile label for clients that take one (Karing). Printable, ≤64 chars."""
    cleaned = "".join(ch for ch in (name or "") if ch.isprintable()).strip()
    return cleaned[:_MAX_NAME_LEN] or _DEFAULT_PROFILE_NAME


_SCHEMES = {
    # Happ: server-side seals the URL via happ_crypto → happ://crypt4/<base64>.
    # Incy: server-side awaits incy_crypto.to_incy_link (Node sidecar
    #       under the hood) → incy://crypt1/<payload>. If Node/package
    #       is unavailable at request time, falls back to a clean error
    #       page so the user understands "не получилось", not a 500.
    # V2RayTun: без шифрования — принимает обычную ссылку подписки
    #       напрямую по схеме v2raytun://import/<url>. iOS + Android.
    # Karing / Stash / Clash Verge: обычная (не зашифрованная) ссылка
    #       подписки, url-encoded в параметре `url` схемы install-config.
    "happ": "happ",
    "incy": "incy",
    "v2raytun": "v2raytun",
    "karing": "karing",
    "stash": "stash",
    "clash": "clash",
}

_CLIENT_NAMES = {
    "happ": "Happ",
    "incy": "Incy",
    "v2raytun": "V2RayTun",
    "karing": "Karing",
    "stash": "Stash",
    "clash": "Clash Verge",
}


async def _build_deep_link(client: str, raw_url: str, name: Optional[str] = None) -> str | None:
    """Build the client-specific deep link for a subscription URL.

    Happ: pure-Python RSA-4096/PKCS#1v1.5 (happ_crypto), always works.
    Incy: AES-256-GCM via @incy/link-encoder npm package, behind a
          Node.js sidecar. Returns None if the sidecar/package is
          unavailable — caller renders a clean error page.
    """
    if client == "happ":
        try:
            from app.services import happ_crypto
            return happ_crypto.to_crypt_link(raw_url)
        except Exception:
            # Defensive fallback — happ://add/<plain> still opens Happ
            # and imports a subscription, just without the sealing.
            logger.exception(
                "HAPP_CRYPT4_BUILD_FAIL — falling back to plain happ://add/"
            )
            safe = quote(raw_url, safe='/:?&=@%+')
            return f"happ://add/{safe}"
    if client == "incy":
        try:
            from app.services import incy_crypto
            return await incy_crypto.to_incy_link(raw_url)
        except Exception:
            logger.exception("INCY_BUILD_FAIL — returning None")
            return None
    if client == "v2raytun":
        # V2RayTun не шифрует ссылку — принимает обычную подписку по
        # схеме v2raytun://import/<urlencoded_url>. Открывает приложение
        # на iOS/Android и импортирует подписку.
        safe = quote(raw_url, safe='')
        return f"v2raytun://import/{safe}"
    if client == "karing":
        # https://karing.app/en/cooperation/scheme — «Parameters must be urlencoded».
        return (
            f"karing://install-config?url={quote(raw_url, safe='')}"
            f"&name={quote(_clean_profile_name(name), safe='')}"
        )
    if client == "stash":
        # https://stash.wiki/en/faq/url-schema — url must be encoded.
        return f"stash://install-config?url={quote(raw_url, safe='')}"
    if client == "clash":
        # Clash Verge Rev: https://www.clashverge.dev/guide/url_schemes.html
        return f"clash://install-config?url={quote(raw_url, safe='')}"
    return None


def _render_page(client: str, deep_link: str) -> str:
    client_name = html_escape(_CLIENT_NAMES.get(client, client))
    # safe_deep_link  → goes into HTML (href + visible text). `&` → `&amp;`.
    # js_deep_link    → goes into a JS string literal. Use json.dumps so
    #                   `&` stays `&`, but quotes/backslashes/control chars
    #                   are escaped — no chance of breaking out of the
    #                   literal even if the URL is hostile.
    safe_deep_link = html_escape(deep_link, quote=True)
    js_deep_link = json.dumps(deep_link)
    # Auto-redirect happens via window.location; users with an iOS
    # in-app browser that blocks happ:// will see the Copy block as
    # fallback and import the link by hand.
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="theme-color" content="#ffffff">
<title>Atlas Secure — {client_name}</title>
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
  .wrap {{
    width: 100%; max-width: 480px;
  }}
  h1 {{
    font-size: 22px; font-weight: 700; letter-spacing: -0.01em;
    margin: 8px 0 12px;
  }}
  p.lead {{
    font-size: 15px; line-height: 1.45;
    color: #333; margin: 0 0 20px;
  }}
  .btn {{
    display: inline-flex; align-items: center; justify-content: center;
    width: auto; padding: 14px 22px;
    background: #111; color: #fff;
    border-radius: 10px;
    text-decoration: none;
    font-size: 15px; font-weight: 600; letter-spacing: -0.01em;
    transition: transform 80ms ease, background 80ms ease;
    border: none; cursor: pointer;
  }}
  .btn:active {{ transform: scale(0.98); background: #000; }}
  .hint {{
    margin-top: 28px; font-size: 13px; color: #555;
  }}
  .codeblock {{
    margin-top: 10px;
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
  .copyrow {{
    display: flex; justify-content: flex-end; margin-top: 8px;
  }}
  .copy {{
    appearance: none; border: none;
    background: transparent; color: #555;
    font-size: 12px; font-weight: 600;
    padding: 6px 10px; border-radius: 6px;
    cursor: pointer;
  }}
  .copy:hover {{ background: rgba(0,0,0,0.04); color: #111; }}
  .copy.copied {{ color: #1a7f37; }}
  .brand {{
    margin-top: 36px;
    font-size: 11px; letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #9aa1ab;
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Подключение Atlas Secure</h1>
  <p class="lead">
    Нажми кнопку — откроется приложение {client_name} и импортируется подписка.
  </p>

  <a class="btn" id="open" href="{safe_deep_link}">Открыть в {client_name}</a>

  <div class="hint">Если не открылось, скопируйте и вставьте вручную:</div>
  <div class="codeblock" id="link">{safe_deep_link}</div>
  <div class="copyrow">
    <button class="copy" id="copybtn" type="button">Скопировать</button>
  </div>

  <div class="brand">Atlas Secure · VPN</div>
</div>

<script>
  // Try to fire the deep link automatically. On platforms that block
  // it (iOS in-app browsers, some Android WebViews) the user falls
  // back to tapping the visible button or copying the link.
  (function () {{
    try {{ window.location.href = {js_deep_link}; }} catch (e) {{}}
  }})();

  // Copy-to-clipboard with a graceful textarea fallback for older
  // WebViews that don't expose navigator.clipboard.
  document.getElementById('copybtn').addEventListener('click', function () {{
    var text = document.getElementById('link').innerText;
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
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    }} else {{
      fallback();
    }}
    function fallback() {{
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


@router.get("/open/{client}")
async def deeplink_redirect(
    client: str,
    url: str = Query(...),
    name: Optional[str] = Query(None),
):
    """Redirect browser to VPN client deep link."""
    if client not in _SCHEMES:
        return HTMLResponse("<h3>Unknown client</h3>", status_code=400)
    if not _is_valid_sub_url(url):
        logger.warning("DEEPLINK_REDIRECT_REJECTED client=%s reason=bad_url", client)
        return HTMLResponse("<h3>Invalid link</h3>", status_code=400)

    deep_link = await _build_deep_link(client, url, name)
    if not deep_link:
        # Currently only the Incy path can return None — happens when the
        # Node sidecar / @incy/link-encoder package isn't deployed yet.
        # Surface a friendly page rather than a 500.
        client_name = html_escape(_CLIENT_NAMES.get(client, client))
        return HTMLResponse(
            f"<!doctype html><meta charset='utf-8'>"
            f"<title>Atlas Secure</title>"
            f"<body style='font-family:system-ui;padding:40px;text-align:center;color:#333'>"
            f"<h2>{client_name} временно недоступен</h2>"
            f"<p>Вернись в бота и попробуй вариант «Открыть в Happ» — "
            f"подписку всегда можно поднять через него.</p></body>",
            status_code=503,
        )
    logger.info("DEEPLINK_REDIRECT client=%s", client)
    return HTMLResponse(_render_page(client, deep_link))
