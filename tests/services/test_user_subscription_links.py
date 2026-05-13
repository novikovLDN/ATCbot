"""
Unit tests for app.services.user_subscription_links.

Verifies the user-facing URL helpers return the cached Remnawave
premium / bypass URL when one is recorded against the subscription,
and fall back to the legacy samopis URL otherwise.
"""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import user_subscription_links


class _FakeConn:
    def __init__(self, ret):
        self._ret = ret

    async def fetchval(self, *_a, **_kw):
        return self._ret

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _FakePool:
    def __init__(self, ret):
        self._ret = ret

    def acquire(self):
        return _FakeConn(self._ret)


def _patch_db(monkeypatch, *, premium_url=None, bypass_url=None):
    """Inject a stub `database` module that returns the given cached URLs."""
    pool_premium = _FakePool(premium_url)
    pool_bypass = _FakePool(bypass_url)

    db = SimpleNamespace(
        get_pool=AsyncMock(side_effect=[pool_premium, pool_bypass, pool_premium, pool_bypass]),
    )
    monkeypatch.setitem(sys.modules, "database", db)
    return db


def _patch_config(enabled=True):
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=enabled)
    return patch.object(user_subscription_links, "config", cfg)


# ── get_user_premium_url ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_premium_url_returns_cached_value(monkeypatch):
    _patch_db(monkeypatch, premium_url="https://rmnw/sub/prem123")
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url == "https://rmnw/sub/prem123"


@pytest.mark.asyncio
async def test_get_premium_url_returns_none_when_cache_empty(monkeypatch):
    _patch_db(monkeypatch, premium_url=None)
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


@pytest.mark.asyncio
async def test_get_premium_url_returns_none_when_cache_blank_string(monkeypatch):
    """Empty string in DB column is treated as 'not cached'."""
    _patch_db(monkeypatch, premium_url="")
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


@pytest.mark.asyncio
async def test_get_premium_url_short_circuits_when_remnawave_disabled(monkeypatch):
    db = SimpleNamespace(get_pool=AsyncMock())
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_config(enabled=False):
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None
    db.get_pool.assert_not_called()


@pytest.mark.asyncio
async def test_get_premium_url_swallows_db_errors(monkeypatch):
    """DB exceptions are logged + return None — never raise to handlers."""
    async def _boom():
        raise RuntimeError("db gone")

    db = SimpleNamespace(get_pool=_boom)
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


# ── get_user_bypass_url ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_bypass_url_returns_cached_value(monkeypatch):
    _patch_db(monkeypatch, bypass_url="https://rmnw/sub/byp456")
    with _patch_config():
        url = await user_subscription_links.get_user_bypass_url(42)
    assert url == "https://rmnw/sub/byp456"


# ── get_user_primary_subscription_url ─────────────────────────────────

@pytest.mark.asyncio
async def test_primary_url_prefers_remnawave_when_cached(monkeypatch):
    _patch_db(monkeypatch, premium_url="https://rmnw/sub/prem")
    with _patch_config(), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://rmnw/sub/prem"


@pytest.mark.asyncio
async def test_primary_url_falls_back_to_legacy_when_remnawave_empty(monkeypatch):
    _patch_db(monkeypatch, premium_url=None)
    with _patch_config(), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://atlassecure.ru/api/sub/legacy"


@pytest.mark.asyncio
async def test_primary_url_falls_back_when_remnawave_disabled(monkeypatch):
    db = SimpleNamespace(get_pool=AsyncMock())
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_config(enabled=False), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://atlassecure.ru/api/sub/legacy"
