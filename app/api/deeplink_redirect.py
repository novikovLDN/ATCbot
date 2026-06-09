"""
Deep link redirect endpoint.

Telegram blocks custom URL schemes (happ://, hiddify://) in inline
keyboard buttons. This endpoint serves an HTML page that redirects
the browser to the custom scheme, opening the VPN client and importing
the subscription automatically.

If the auto-redirect fails (built-in Telegram browser on iOS sometimes
blocks `happ://`), the page also shows the deep link as a monospaced
block with a Copy button so the user can import it manually.

Usage:
    GET /open/{client}?url={subscription_url}

Supported clients: happ, hiddify
"""

import json
import logging
from html import escape as html_escape
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_SCHEMES = {
    "happ": "happ://add/",
    "hiddify": "hiddify://import/",
}

_CLIENT_NAMES = {
    "happ": "Happ",
    "hiddify": "Hiddify",
}


def _build_deep_link(client: str, raw_url: str) -> str:
    """Build the client-specific deep link for a subscription URL.

    Happ goes through crypt4 — RSA-4096 / PKCS#1 v1.5 sealed payload
    base64-encoded behind the `happ://crypt4/` scheme. Only the Happ
    client (with its embedded private key) can decrypt it; the plain
    sub URL never leaves the server, and DPI/parental controls don't
    see a recognisable subscription endpoint in the deep link.

    Other clients stay on their plain deep-link schemes — they don't
    implement crypt4 and would just fail to parse a sealed payload.
    """
    if client == "happ":
        try:
            from app.services import happ_crypto
            return happ_crypto.to_crypt_link(raw_url)
        except Exception:
            # Defensive fallback — happ://add/<plain> still opens Happ
            # and imports a subscription, just without the sealing.
            # An ERROR-level log makes this loud in production logs.
            logger.exception(
                "HAPP_CRYPT4_BUILD_FAIL — falling back to plain happ://add/"
            )
    scheme = _SCHEMES[client]
    safe_url = quote(raw_url, safe='/:?&=@%+')
    return f"{scheme}{safe_url}"


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
async def deeplink_redirect(client: str, url: str = Query(...)):
    """Redirect browser to VPN client deep link."""
    if client not in _SCHEMES:
        return HTMLResponse("<h3>Unknown client</h3>", status_code=400)

    deep_link = _build_deep_link(client, url)
    logger.info("DEEPLINK_REDIRECT client=%s", client)
    return HTMLResponse(_render_page(client, deep_link))
