"""
Deep link redirect endpoint.

Telegram blocks custom URL schemes (happ://, v2raytun://, hiddify://) in
inline keyboard buttons. This endpoint serves an HTML page that redirects
the browser to the custom scheme, opening the VPN client and importing
the subscription automatically.

Usage:
    GET /open/{client}?url={subscription_url}

Supported clients: happ, v2raytun, hiddify
"""

import logging
from urllib.parse import quote, unquote

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_SCHEMES = {
    "happ": "happ://add/",
    "v2raytun": "v2raytun://import/",
    "hiddify": "hiddify://import/",
}

_CLIENT_NAMES = {
    "happ": "Happ",
    "v2raytun": "V2RayTun",
    "hiddify": "Hiddify",
}


@router.get("/open/{client}")
async def deeplink_redirect(client: str, url: str = Query(...)):
    """Redirect browser to VPN client deep link."""
    scheme = _SCHEMES.get(client)
    if not scheme:
        return HTMLResponse("<h3>Unknown client</h3>", status_code=400)

    from html import escape as html_escape
    from urllib.parse import quote
    safe_url = quote(url, safe='/:?&=@%+')
    deep_link = f"{scheme}{safe_url}"
    client_name = html_escape(_CLIENT_NAMES.get(client, client))

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Atlas Secure — {client_name}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    display: flex; justify-content: center; align-items: center;
    min-height: 100vh; margin: 0;
    background: #1a1a2e; color: #e0e0e0;
    text-align: center;
  }}
  .card {{
    background: #16213e; border-radius: 16px; padding: 40px 32px;
    max-width: 400px; box-shadow: 0 8px 32px rgba(0,0,0,0.3);
  }}
  h2 {{ color: #00d4ff; margin-bottom: 8px; }}
  p {{ color: #a0a0b0; line-height: 1.5; }}
  .btn {{
    display: inline-block; margin-top: 20px; padding: 14px 32px;
    background: #00d4ff; color: #1a1a2e; border-radius: 10px;
    text-decoration: none; font-weight: 600; font-size: 16px;
  }}
  .btn:active {{ background: #00b8d9; }}
</style>
</head>
<body>
<div class="card">
  <h2>{client_name}</h2>
  <p>Opening {client_name}...</p>
  <p>If the app did not open automatically, tap the button below.</p>
  <a class="btn" href="{deep_link}">Open {client_name}</a>
</div>
<script>window.location = "{deep_link}";</script>
</body>
</html>"""

    logger.info(f"DEEPLINK_REDIRECT client={client}")
    return HTMLResponse(html)
