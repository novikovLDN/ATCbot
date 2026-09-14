"""Branding — the one place the product's name, logo and colours live.

The dashboard (frontend and its backend) reads the brand from here and
nowhere else, so a rename is an env change, not a code change. Bot texts
(app/i18n) are out of scope and still carry their own wording.

Env (APP_ENV-prefixed like every other setting, e.g. PROD_BRAND_NAME; the
unprefixed name is accepted as a fallback):

  BRAND_NAME           full name            default "Atlas Secure"
  BRAND_SHORT          short name / PWA     default "Atlas"
  BRAND_LOGO_URL       https URL or /path   default "" (built-in mark)
  BRAND_PRIMARY_COLOR  #RRGGBB accent       default "#F2E8C9" (cream)
  BRAND_SUPPORT_URL    support link         default "https://t.me/atlas_suppbot"
  BRAND_CHANNEL_URL    news channel link    default "https://t.me/ATC_VPN"
  BRAND_BOT_USERNAME   bot for share links  default config.BOT_USERNAME
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULTS = {
    "BRAND_NAME": "Atlas Secure",
    "BRAND_SHORT": "Atlas",
    "BRAND_LOGO_URL": "",
    "BRAND_PRIMARY_COLOR": "#F2E8C9",
    "BRAND_SUPPORT_URL": "https://t.me/atlas_suppbot",
    "BRAND_CHANNEL_URL": "https://t.me/ATC_VPN",
    # Bot username for share links built by the dashboard (gift-GB links).
    # Empty here on purpose: the default is config.BOT_USERNAME, the bot's
    # own setting, so the two can never disagree.
    "BRAND_BOT_USERNAME": "",
}

_BOT = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def _env(name: str) -> str:
    prefix = (os.getenv("APP_ENV") or "").upper()
    value = os.getenv(f"{prefix}_{name}") if prefix else None
    if value is None or value.strip() == "":
        value = os.getenv(name)
    if value is None or value.strip() == "":
        return DEFAULTS[name]
    return value.strip()


def _safe_url(value: str, *, allow_path: bool) -> str:
    """Only https URLs (or a same-origin /path) reach the page: the logo
    ends up in an <img src> and the support link in an <a href>, so a
    `javascript:` value must never pass."""
    if value.startswith("https://"):
        return value
    if allow_path and value.startswith("/") and not value.startswith("//"):
        return value
    return ""


@dataclass(frozen=True)
class Brand:
    name: str
    short: str
    logo_url: str
    primary_color: str
    support_url: str
    channel_url: str
    bot_username: str = ""

    @property
    def admin_title(self) -> str:
        return f"{self.short} Admin"

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.short.lower()).strip("-") or "admin"

    def public(self) -> dict[str, Any]:
        """What the login screen may see without a session."""
        return {
            "name": self.name,
            "short": self.short,
            "admin_title": self.admin_title,
            "logo_url": self.logo_url or None,
            "primary_color": self.primary_color,
            # Public anyway: it is the bot's t.me name.
            "bot_username": self.bot_username or None,
        }

    def full(self) -> dict[str, Any]:
        extra = {k: v for k, v in asdict(self).items() if k.endswith("_url")}
        return {**self.public(), **extra}


def _bot_username() -> str:
    """BRAND_BOT_USERNAME if valid, else config.BOT_USERNAME."""
    raw = _env("BRAND_BOT_USERNAME").lstrip("@")
    if _BOT.match(raw):
        return raw
    try:
        import config
        fallback = str(getattr(config, "BOT_USERNAME", "") or "").lstrip("@")
    except Exception:
        fallback = ""
    return fallback if _BOT.match(fallback) else ""


def load() -> Brand:
    color = _env("BRAND_PRIMARY_COLOR")
    return Brand(
        name=_env("BRAND_NAME")[:60],
        short=_env("BRAND_SHORT")[:24],
        logo_url=_safe_url(_env("BRAND_LOGO_URL"), allow_path=True),
        primary_color=color if _HEX.match(color) else DEFAULTS["BRAND_PRIMARY_COLOR"],
        support_url=_safe_url(_env("BRAND_SUPPORT_URL"), allow_path=False) or DEFAULTS["BRAND_SUPPORT_URL"],
        channel_url=_safe_url(_env("BRAND_CHANNEL_URL"), allow_path=False) or DEFAULTS["BRAND_CHANNEL_URL"],
        bot_username=_bot_username(),
    )


@lru_cache(maxsize=1)
def get_brand() -> Brand:
    return load()
