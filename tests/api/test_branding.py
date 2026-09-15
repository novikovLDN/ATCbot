"""Branding config + the public /branding endpoint."""
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi import FastAPI  # noqa: E402

import config  # noqa: E402
from app import branding  # noqa: E402
from app.api.dashboard.routes import branding as branding_routes  # noqa: E402
from app.services import admin_auth  # noqa: E402

PREFIX = f"{config.APP_ENV.upper()}_"   # CI runs APP_ENV=local, ad-hoc runs stage


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in branding.DEFAULTS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{PREFIX}{name}", raising=False)
    branding.get_brand.cache_clear()
    yield
    branding.get_brand.cache_clear()


def _client():
    app = FastAPI()
    app.include_router(branding_routes.router, prefix="/branding")
    return TestClient(app)


def test_defaults_keep_current_brand():
    b = branding.load()
    assert b.name == "Atlas Secure"
    assert b.admin_title == "Atlas Admin"
    assert b.support_url == "https://t.me/atlas_suppbot"


def test_env_override_prefixed_wins(monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "Plain")
    monkeypatch.setenv(f"{PREFIX}BRAND_NAME", "Nova VPN")
    monkeypatch.setenv(f"{PREFIX}BRAND_SHORT", "Nova")
    b = branding.load()
    assert b.name == "Nova VPN"
    assert b.admin_title == "Nova Admin"
    assert b.slug == "nova"


def test_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("BRAND_PRIMARY_COLOR", "red; background:url(x)")
    monkeypatch.setenv("BRAND_LOGO_URL", "javascript:alert(1)")
    monkeypatch.setenv("BRAND_SUPPORT_URL", "http://insecure.example")
    b = branding.load()
    assert b.primary_color == "#F2E8C9"
    assert b.logo_url == ""
    assert b.support_url == "https://t.me/atlas_suppbot"


def test_logo_path_allowed(monkeypatch):
    monkeypatch.setenv("BRAND_LOGO_URL", "/dashboard/logo.svg")
    assert branding.load().logo_url == "/dashboard/logo.svg"
    monkeypatch.setenv("BRAND_LOGO_URL", "//evil.example/x.svg")
    assert branding.load().logo_url == ""


def test_public_endpoint_without_session():
    body = _client().get("/branding").json()
    assert body["name"] == "Atlas Secure"
    assert body["primary_color"] == "#F2E8C9"
    assert "support_url" not in body


def test_admin_session_gets_links(monkeypatch):
    monkeypatch.setattr(admin_auth, "lookup_session", AsyncMock(return_value=1))
    monkeypatch.setattr(admin_auth, "is_admin", lambda tg: tg == 1)
    c = _client()
    c.cookies.set(admin_auth.COOKIE_NAME, "tok")
    body = c.get("/branding").json()
    assert body["support_url"] == "https://t.me/atlas_suppbot"


def test_manifest_follows_brand(monkeypatch):
    monkeypatch.setenv("BRAND_SHORT", "Nova")
    resp = _client().get("/branding/manifest.webmanifest")
    assert resp.headers["content-type"].startswith("application/manifest+json")
    body = resp.json()
    assert body["short_name"] == "Nova"
    assert body["name"] == "Nova Admin"


def test_bot_username_defaults_to_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "BOT_USERNAME", "atlassecure_bot")
    assert branding.load().bot_username == "atlassecure_bot"
    monkeypatch.setenv("BRAND_BOT_USERNAME", "@nova_vpn_bot")
    assert branding.load().bot_username == "nova_vpn_bot"
    monkeypatch.setenv("BRAND_BOT_USERNAME", "bad name/../x")
    assert branding.load().bot_username == "atlassecure_bot"


def test_bot_username_is_public(monkeypatch):
    import config
    monkeypatch.setattr(config, "BOT_USERNAME", "atlassecure_bot")
    assert _client().get("/branding").json()["bot_username"] == "atlassecure_bot"
