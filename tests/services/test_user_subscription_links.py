"""
Unit tests for app.services.user_subscription_links.

The premium-URL helper has three resolution layers:
  1. cache hit on the active subscriptions row
  2. cache hit on any subscriptions row for the user (fallback for users
     whose row wasn't 'active' when the cache write happened)
  3. live panel fallback via remnawave_api.get_user(uuid) + cache back-fill

Each layer is exercised below with a stubbed `database` module + an
optional patched `remnawave_api`.  All paths must swallow exceptions
and never raise to handlers.
"""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import user_subscription_links


class _Row:
    """asyncpg-Record-like mapping that supports row['key'] access."""
    def __init__(self, **fields):
        self._d = fields

    def __getitem__(self, k):
        return self._d.get(k)

    def get(self, k, default=None):
        return self._d.get(k, default)


class _FakeConn:
    def __init__(self, *, rows=None, vals=None):
        self._rows = list(rows or [])
        self._vals = list(vals or [])

    async def fetchrow(self, *_a, **_kw):
        return self._rows.pop(0) if self._rows else None

    async def fetchval(self, *_a, **_kw):
        return self._vals.pop(0) if self._vals else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _FakePool:
    def __init__(self, *, rows=None, vals=None):
        self._conn = _FakeConn(rows=rows, vals=vals)

    def acquire(self):
        return self._conn


def _patch_db(monkeypatch, *, rows=None, vals=None,
              set_premium_sub_url_mock=None,
              get_pool_side_effect=None):
    set_premium_sub_url_mock = set_premium_sub_url_mock or AsyncMock()
    if get_pool_side_effect is not None:
        db = SimpleNamespace(
            get_pool=AsyncMock(side_effect=get_pool_side_effect),
            set_remnawave_premium_sub_url=set_premium_sub_url_mock,
        )
    else:
        pool = _FakePool(rows=rows, vals=vals)
        db = SimpleNamespace(
            get_pool=AsyncMock(return_value=pool),
            set_remnawave_premium_sub_url=set_premium_sub_url_mock,
        )
    monkeypatch.setitem(sys.modules, "database", db)
    return db, set_premium_sub_url_mock


def _patch_config(enabled=True):
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=enabled)
    return patch.object(user_subscription_links, "config", cfg)


# ── Layer 1: cache hit on active row ──────────────────────────────────

@pytest.mark.asyncio
async def test_premium_url_returns_cache_hit(monkeypatch):
    _patch_db(monkeypatch, rows=[
        _Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url="https://rmnw/sub/cached"),
    ])
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url == "https://rmnw/sub/cached"


# ── Layer 2: status-agnostic fallback row ─────────────────────────────

@pytest.mark.asyncio
async def test_premium_url_falls_back_to_inactive_row(monkeypatch):
    _patch_db(monkeypatch, rows=[
        None,  # active-row query: no match
        _Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url="https://rmnw/sub/inactive_cached"),
    ])
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url == "https://rmnw/sub/inactive_cached"


# ── Layer 3: panel fallback when cache empty ──────────────────────────

@pytest.mark.asyncio
async def test_premium_url_falls_back_to_panel_when_cache_empty(monkeypatch):
    """Has remnawave_premium_uuid but sub_url cache is NULL → call panel + back-fill."""
    backfill_mock = AsyncMock()
    _patch_db(
        monkeypatch,
        rows=[_Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url=None)],
        set_premium_sub_url_mock=backfill_mock,
    )
    panel_mock = AsyncMock(return_value={"subscriptionUrl": "https://rmnw/sub/from-panel"})
    with _patch_config(), \
         patch("app.services.remnawave_api.get_user", panel_mock):
        url = await user_subscription_links.get_user_premium_url(42)
    assert url == "https://rmnw/sub/from-panel"
    panel_mock.assert_awaited_once_with("prem-uuid")
    backfill_mock.assert_awaited_once_with(42, "https://rmnw/sub/from-panel")


@pytest.mark.asyncio
async def test_premium_url_panel_returns_no_url_yields_none(monkeypatch):
    _patch_db(monkeypatch, rows=[
        _Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url=None),
    ])
    panel_mock = AsyncMock(return_value=None)  # entity not found
    with _patch_config(), \
         patch("app.services.remnawave_api.get_user", panel_mock):
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


@pytest.mark.asyncio
async def test_premium_url_panel_exception_is_swallowed(monkeypatch):
    _patch_db(monkeypatch, rows=[
        _Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url=None),
    ])
    async def _boom(*_a, **_kw):
        raise RuntimeError("panel timeout")
    with _patch_config(), \
         patch("app.services.remnawave_api.get_user", _boom):
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


# ── No premium uuid at all → None (caller will fall back to legacy) ───

@pytest.mark.asyncio
async def test_premium_url_returns_none_when_uuid_missing(monkeypatch):
    """Un-migrated user — no premium entity → None → caller uses legacy URL."""
    _patch_db(monkeypatch, rows=[None, None])
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


# ── Short-circuit when REMNAWAVE_ENABLED is false ─────────────────────

@pytest.mark.asyncio
async def test_premium_url_short_circuits_when_remnawave_disabled(monkeypatch):
    db = SimpleNamespace(get_pool=AsyncMock(), set_remnawave_premium_sub_url=AsyncMock())
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_config(enabled=False):
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None
    db.get_pool.assert_not_called()


@pytest.mark.asyncio
async def test_premium_url_swallows_pool_errors(monkeypatch):
    async def _boom():
        raise RuntimeError("db gone")
    db = SimpleNamespace(get_pool=_boom, set_remnawave_premium_sub_url=AsyncMock())
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


# ── get_user_bypass_url (unchanged contract) ──────────────────────────

@pytest.mark.asyncio
async def test_bypass_url_returns_cache_hit(monkeypatch):
    _patch_db(monkeypatch, vals=["https://rmnw/sub/byp-cached"])
    with _patch_config():
        url = await user_subscription_links.get_user_bypass_url(42)
    assert url == "https://rmnw/sub/byp-cached"


@pytest.mark.asyncio
async def test_bypass_url_returns_none_when_cache_empty(monkeypatch):
    _patch_db(monkeypatch, vals=[None])
    with _patch_config():
        url = await user_subscription_links.get_user_bypass_url(42)
    assert url is None


# ── get_user_primary_subscription_url ─────────────────────────────────

@pytest.mark.asyncio
async def test_primary_url_uses_remnawave_when_premium_url_present(monkeypatch):
    _patch_db(monkeypatch, rows=[
        _Row(remnawave_premium_uuid="u", remnawave_premium_sub_url="https://rmnw/sub/p"),
    ])
    with _patch_config(), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://rmnw/sub/p"


@pytest.mark.asyncio
async def test_primary_url_falls_back_to_legacy_when_no_premium(monkeypatch):
    _patch_db(monkeypatch, rows=[None, None])
    with _patch_config(), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://atlassecure.ru/api/sub/legacy"
