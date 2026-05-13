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
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")), \
         patch.object(user_subscription_links, "_try_lazy_provision_premium",
                      AsyncMock(return_value=False)):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://atlassecure.ru/api/sub/legacy"


# ── Lazy provisioning ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_primary_url_lazy_provisions_when_no_premium_entity(monkeypatch):
    """User without premium entity → orchestrator runs → premium URL returned."""
    # First lookup returns no premium uuid → falls through to lazy provision.
    # After lazy provision the second lookup returns the freshly-cached URL.
    _patch_db(monkeypatch, rows=[
        None, None,                                      # 1st premium-url lookup (no rows)
        _Row(remnawave_premium_uuid="new", remnawave_premium_sub_url="https://rmnw/sub/new"),
        # 2nd lookup after provisioning — fed by the second pool.acquire()
    ])
    lazy_mock = AsyncMock(return_value=True)
    with _patch_config(), \
         patch.object(user_subscription_links, "_try_lazy_provision_premium", lazy_mock), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    lazy_mock.assert_awaited_once_with(42)
    assert url == "https://rmnw/sub/new"


@pytest.mark.asyncio
async def test_primary_url_returns_legacy_when_lazy_provision_returns_false(monkeypatch):
    _patch_db(monkeypatch, rows=[None, None, None, None])
    with _patch_config(), \
         patch.object(user_subscription_links, "_try_lazy_provision_premium",
                      AsyncMock(return_value=False)), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://atlassecure.ru/api/sub/legacy"


@pytest.mark.asyncio
async def test_lazy_provision_skips_when_remnawave_disabled(monkeypatch):
    db = SimpleNamespace(get_subscription_any=AsyncMock())
    monkeypatch.setitem(sys.modules, "database", db)
    with _patch_config(enabled=False):
        result = await user_subscription_links._try_lazy_provision_premium(42)
    assert result is False
    db.get_subscription_any.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_provision_skips_when_no_main_squad_configured(monkeypatch):
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=True, REMNAWAVE_MAIN_SQUAD_UUID="")
    db = SimpleNamespace(get_subscription_any=AsyncMock())
    monkeypatch.setitem(sys.modules, "database", db)
    with patch.object(user_subscription_links, "config", cfg):
        result = await user_subscription_links._try_lazy_provision_premium(42)
    assert result is False
    db.get_subscription_any.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_provision_skips_when_user_already_has_premium(monkeypatch):
    """If user got premium uuid between cache miss and our look-up we
    must not duplicate it."""
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=True, REMNAWAVE_MAIN_SQUAD_UUID="main-sq")
    db = SimpleNamespace(
        get_subscription_any=AsyncMock(return_value={
            "telegram_id": 42,
            "uuid": "samopis-uuid",
            "remnawave_premium_uuid": "already-have-uuid",
            "status": "active",
            "expires_at": None,
        }),
    )
    monkeypatch.setitem(sys.modules, "database", db)
    create_mock = AsyncMock()
    with patch.object(user_subscription_links, "config", cfg), \
         patch("app.services.remnawave_premium.create_premium_user_entity", create_mock):
        result = await user_subscription_links._try_lazy_provision_premium(42)
    assert result is False
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_provision_skips_for_expired_subscription(monkeypatch):
    from datetime import datetime, timezone, timedelta
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=True, REMNAWAVE_MAIN_SQUAD_UUID="main-sq")
    db = SimpleNamespace(
        get_subscription_any=AsyncMock(return_value={
            "telegram_id": 42,
            "uuid": "samopis-uuid",
            "remnawave_premium_uuid": None,
            "status": "expired",
            "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
        }),
    )
    monkeypatch.setitem(sys.modules, "database", db)
    create_mock = AsyncMock()
    with patch.object(user_subscription_links, "config", cfg), \
         patch("app.services.remnawave_premium.create_premium_user_entity", create_mock):
        result = await user_subscription_links._try_lazy_provision_premium(42)
    assert result is False
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_provision_creates_premium_with_forced_legacy_uuid(monkeypatch):
    """Active subscription with legacy uuid + no premium → create and persist."""
    from datetime import datetime, timezone, timedelta
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=True, REMNAWAVE_MAIN_SQUAD_UUID="main-sq")
    future = datetime.now(timezone.utc) + timedelta(days=30)
    persist_mock = AsyncMock()
    db = SimpleNamespace(
        get_subscription_any=AsyncMock(return_value={
            "telegram_id": 42,
            "uuid": "samopis-uuid-xyz",
            "remnawave_premium_uuid": None,
            "status": "active",
            "expires_at": future,
        }),
        set_remnawave_premium_uuid_and_url=persist_mock,
    )
    monkeypatch.setitem(sys.modules, "database", db)

    from app.services import remnawave_premium
    create_result = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="panel-prem", forced_uuid_accepted=True,
        subscription_url="https://rmnw/sub/new", status=201,
        error=None, recovered=False, short_uuid="new_s",
    )
    create_mock = AsyncMock(return_value=create_result)

    # Reset the module-level lazy lock so test reruns don't see a stale lock.
    user_subscription_links._lazy_provision_locks.clear()

    with patch.object(user_subscription_links, "config", cfg), \
         patch("app.services.remnawave_premium.create_premium_user_entity", create_mock):
        result = await user_subscription_links._try_lazy_provision_premium(42)

    assert result is True
    create_mock.assert_awaited_once()
    kwargs = create_mock.call_args.kwargs
    assert kwargs["requested_uuid"] == "samopis-uuid-xyz"
    assert kwargs["expire_at"] == future
    persist_mock.assert_awaited_once_with(42, "panel-prem", "https://rmnw/sub/new", short_uuid="new_s")


@pytest.mark.asyncio
async def test_lazy_provision_works_without_legacy_uuid(monkeypatch):
    """Trial/new user without samopis uuid still gets a premium entity
    (panel assigns a fresh UUID since forced uuid is None)."""
    from datetime import datetime, timezone, timedelta
    cfg = SimpleNamespace(REMNAWAVE_ENABLED=True, REMNAWAVE_MAIN_SQUAD_UUID="main-sq")
    future = datetime.now(timezone.utc) + timedelta(days=3)
    persist_mock = AsyncMock()
    db = SimpleNamespace(
        get_subscription_any=AsyncMock(return_value={
            "telegram_id": 42,
            "uuid": None,
            "remnawave_premium_uuid": None,
            "status": "active",
            "expires_at": future,
        }),
        set_remnawave_premium_uuid_and_url=persist_mock,
    )
    monkeypatch.setitem(sys.modules, "database", db)

    from app.services import remnawave_premium
    create_result = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="panel-fresh", forced_uuid_accepted=False,
        subscription_url="https://rmnw/sub/fresh", status=201,
        error=None, recovered=False, short_uuid="fresh_s",
    )
    create_mock = AsyncMock(return_value=create_result)

    user_subscription_links._lazy_provision_locks.clear()

    with patch.object(user_subscription_links, "config", cfg), \
         patch("app.services.remnawave_premium.create_premium_user_entity", create_mock):
        result = await user_subscription_links._try_lazy_provision_premium(42)

    assert result is True
    # requested_uuid should be None when user has no legacy samopis uuid.
    assert create_mock.call_args.kwargs["requested_uuid"] is None
    persist_mock.assert_awaited_once_with(42, "panel-fresh", "https://rmnw/sub/fresh", short_uuid="fresh_s")
