"""Branding for the SPA.

`GET /branding` is public on purpose: the login screen needs the name,
logo and accent colour before anyone is signed in. Everything else (the
support / channel links) is only added for an authenticated admin.
`GET /branding/manifest.webmanifest` is the PWA manifest built from the
same config, so the installed app's name follows the brand too.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse

from app.branding import get_brand
from app.services import admin_auth

router = APIRouter()

# Light-grey wall of the default v3 look (index.css --c-wall-2 / prefs.ts
# theme-color), so the iOS/Android launch splash matches the first frame
# instead of flashing charcoal. The accent is the brand's.
_SHELL_COLOR = "#CFCECA"


@router.get("")
async def branding(atlas_admin_session: Optional[str] = Cookie(default=None)):
    brand = get_brand()
    if atlas_admin_session:
        tg = await admin_auth.lookup_session(atlas_admin_session)
        if tg is not None and admin_auth.is_admin(tg):
            return brand.full()
    return brand.public()


@router.get("/manifest.webmanifest")
async def manifest():
    brand = get_brand()
    icons = [
        {"src": "/dashboard/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        {"src": "/dashboard/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/dashboard/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/dashboard/icon-mask-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    if brand.logo_url:
        icons.insert(0, {"src": brand.logo_url, "sizes": "any", "purpose": "any"})
    return JSONResponse(
        {
            "name": brand.admin_title,
            "short_name": brand.short,
            "description": f"{brand.name} — admin dashboard",
            "start_url": "/dashboard/",
            "scope": "/dashboard/",
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": _SHELL_COLOR,
            "theme_color": _SHELL_COLOR,
            "lang": "ru",
            "icons": icons,
        },
        media_type="application/manifest+json",
    )
