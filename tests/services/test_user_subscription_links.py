"""
Unit tests for app.services.user_subscription_links.

Covers four resolution layers for premium URLs and the bypass URL
helper, plus the lazy-provision orchestrator that fills in BOTH
entities (premium + bypass) for any active user that's missing them.
"""
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import user_subscription_links


# ── Fixtures ──────────────────────────────────────────────────────────

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
              set_premium_sub_url_mock=None, set_bypass_cache_mock=None,
              set_premium_uuid_and_url_mock=None,
              get_subscription_any_value=None,
              get_pool_side_effect=None):
    set_premium_sub_url_mock = set_premium_sub_url_mock or AsyncMock()
    set_bypass_cache_mock = set_bypass_cache_mock or AsyncMock()
    set_premium_uuid_and_url_mock = set_premium_uuid_and_url_mock or AsyncMock()
    if get_pool_side_effect is not None:
        db = SimpleNamespace(
            get_pool=AsyncMock(side_effect=get_pool_side_effect),
            set_remnawave_premium_sub_url=set_premium_sub_url_mock,
            set_remnawave_bypass_cache=set_bypass_cache_mock,
            set_remnawave_premium_uuid_and_url=set_premium_uuid_and_url_mock,
            get_subscription_any=AsyncMock(return_value=get_subscription_any_value),
        )
    else:
        pool = _FakePool(rows=rows, vals=vals)
        db = SimpleNamespace(
            get_pool=AsyncMock(return_value=pool),
            set_remnawave_premium_sub_url=set_premium_sub_url_mock,
            set_remnawave_bypass_cache=set_bypass_cache_mock,
            set_remnawave_premium_uuid_and_url=set_premium_uuid_and_url_mock,
            get_subscription_any=AsyncMock(return_value=get_subscription_any_value),
        )
    monkeypatch.setitem(sys.modules, "database", db)
    return db


def _patch_config(enabled=True, main_squad="main-sq", clients_squad="clients-sq", trial_gb=1):
    cfg = SimpleNamespace(
        REMNAWAVE_ENABLED=enabled,
        REMNAWAVE_MAIN_SQUAD_UUID=main_squad,
        REMNAWAVE_SQUAD_UUID=clients_squad,
        TRIAL_BYPASS_GB=trial_gb,
    )
    return patch.object(user_subscription_links, "config", cfg)


def _reset_locks():
    user_subscription_links._lazy_provision_locks.clear()


# ── get_user_premium_url: cache hit / status-agnostic / panel fallback ──

@pytest.mark.asyncio
async def test_premium_url_returns_cache_hit(monkeypatch):
    _patch_db(monkeypatch, rows=[
        _Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url="https://rmnw/sub/cached"),
    ])
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url == "https://rmnw/sub/cached"


@pytest.mark.asyncio
async def test_premium_url_falls_back_to_inactive_row(monkeypatch):
    _patch_db(monkeypatch, rows=[
        None,
        _Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url="https://rmnw/sub/inactive_cached"),
    ])
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url == "https://rmnw/sub/inactive_cached"


@pytest.mark.asyncio
async def test_premium_url_panel_fallback_with_backfill(monkeypatch):
    backfill = AsyncMock()
    _patch_db(
        monkeypatch,
        rows=[_Row(remnawave_premium_uuid="prem-uuid", remnawave_premium_sub_url=None)],
        set_premium_sub_url_mock=backfill,
    )
    panel = AsyncMock(return_value={"subscriptionUrl": "https://rmnw/sub/from-panel"})
    with _patch_config(), patch("app.services.remnawave_api.get_user", panel):
        url = await user_subscription_links.get_user_premium_url(42)
    assert url == "https://rmnw/sub/from-panel"
    panel.assert_awaited_once_with("prem-uuid")
    backfill.assert_awaited_once_with(42, "https://rmnw/sub/from-panel")


@pytest.mark.asyncio
async def test_premium_url_returns_none_when_uuid_missing(monkeypatch):
    _patch_db(monkeypatch, rows=[None, None])
    with _patch_config():
        url = await user_subscription_links.get_user_premium_url(42)
    assert url is None


# ── get_user_bypass_url: cache hit / panel fallback / lazy provision ───

@pytest.mark.asyncio
async def test_bypass_url_returns_cache_hit(monkeypatch):
    _patch_db(monkeypatch, rows=[
        _Row(remnawave_uuid="byp-uuid", remnawave_bypass_sub_url="https://rmnw/sub/byp-cached"),
    ])
    with _patch_config():
        url = await user_subscription_links.get_user_bypass_url(42)
    assert url == "https://rmnw/sub/byp-cached"


@pytest.mark.asyncio
async def test_bypass_url_panel_fallback_with_backfill(monkeypatch):
    set_cache_mock = AsyncMock()
    _patch_db(
        monkeypatch,
        rows=[_Row(remnawave_uuid="byp-uuid", remnawave_bypass_sub_url=None)],
        set_bypass_cache_mock=set_cache_mock,
    )
    panel = AsyncMock(return_value={"subscriptionUrl": "https://rmnw/sub/byp-panel", "shortUuid": "by_s"})
    with _patch_config(), patch("app.services.remnawave_api.get_user", panel):
        url = await user_subscription_links.get_user_bypass_url(42)
    assert url == "https://rmnw/sub/byp-panel"
    set_cache_mock.assert_awaited_once_with(42, "byp-uuid", "https://rmnw/sub/byp-panel", "by_s")


@pytest.mark.asyncio
async def test_bypass_url_lazy_provisions_when_no_entity(monkeypatch):
    """User has no remnawave_uuid → lazy-provision creates one → URL returned."""
    # First _bypass_url_from_cache call: no row.
    # Lazy-provision creates entity (mocked).
    # Second _bypass_url_from_cache call: cache populated.
    db = _patch_db(monkeypatch, rows=[
        None,                                                            # 1st cache miss
        _Row(remnawave_uuid="new-byp", remnawave_bypass_sub_url="https://rmnw/sub/new-byp"),
    ], get_subscription_any_value={
        "telegram_id": 42, "uuid": "samopis", "status": "active",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=10),
        "source": "trial", "remnawave_uuid": None, "remnawave_premium_uuid": "exists",
    })
    _reset_locks()

    from app.services import remnawave_bypass
    bcreate = AsyncMock(return_value=remnawave_bypass.BypassCreateResult(
        ok=True, panel_uuid="new-byp", subscription_url="https://rmnw/sub/new-byp",
        short_uuid="ns", status=201, error=None, recovered=False,
    ))
    with _patch_config(), \
         patch.object(user_subscription_links, "config", SimpleNamespace(
             REMNAWAVE_ENABLED=True, REMNAWAVE_MAIN_SQUAD_UUID="main-sq",
             REMNAWAVE_SQUAD_UUID="clients-sq", TRIAL_BYPASS_GB=1,
         )), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", bcreate):
        url = await user_subscription_links.get_user_bypass_url(42)

    assert url == "https://rmnw/sub/new-byp"
    bcreate.assert_awaited_once()
    # Trial-source → 1 GB
    assert bcreate.call_args.kwargs["traffic_limit_bytes"] == 1 * 1024**3


@pytest.mark.asyncio
async def test_bypass_url_returns_none_when_provision_fails(monkeypatch):
    _patch_db(monkeypatch, rows=[None, None],
              get_subscription_any_value={
                  "telegram_id": 42, "uuid": "u", "status": "active",
                  "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
                  "source": "payment", "remnawave_uuid": None, "remnawave_premium_uuid": "x",
              })
    _reset_locks()
    from app.services import remnawave_bypass
    bcreate = AsyncMock(return_value=remnawave_bypass.BypassCreateResult(
        ok=False, panel_uuid=None, subscription_url=None, short_uuid=None,
        status=500, error="panel_down", recovered=False,
    ))
    with patch.object(user_subscription_links, "config", SimpleNamespace(
             REMNAWAVE_ENABLED=True, REMNAWAVE_MAIN_SQUAD_UUID="main-sq",
             REMNAWAVE_SQUAD_UUID="clients-sq", TRIAL_BYPASS_GB=1,
         )), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", bcreate):
        url = await user_subscription_links.get_user_bypass_url(42)
    assert url is None


# ── _try_lazy_provision_entities: both-entity creation ────────────────

@pytest.mark.asyncio
async def test_lazy_provision_creates_both_entities_for_trial(monkeypatch):
    """Trial user with no remnawave entities → premium + bypass(1GB) created."""
    future = datetime.now(timezone.utc) + timedelta(days=3)
    persist_premium = AsyncMock()
    persist_bypass = AsyncMock()
    _patch_db(
        monkeypatch,
        get_subscription_any_value={
            "telegram_id": 42, "uuid": "samopis-uuid", "status": "active",
            "expires_at": future, "source": "trial",
            "remnawave_uuid": None, "remnawave_premium_uuid": None,
        },
        set_premium_uuid_and_url_mock=persist_premium,
        set_bypass_cache_mock=persist_bypass,
    )
    _reset_locks()

    from app.services import remnawave_premium, remnawave_bypass
    pres = remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="prem-new", forced_uuid_accepted=True,
        subscription_url="https://rmnw/sub/prem", status=201,
        error=None, recovered=False, short_uuid="ps",
    )
    bres = remnawave_bypass.BypassCreateResult(
        ok=True, panel_uuid="byp-new", subscription_url="https://rmnw/sub/byp",
        short_uuid="bs", status=201, error=None, recovered=False,
    )
    with _patch_config(), \
         patch("app.services.remnawave_premium.create_premium_user_entity", AsyncMock(return_value=pres)), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", AsyncMock(return_value=bres)):
        out = await user_subscription_links._try_lazy_provision_entities(42)

    assert out == {"created_premium": True, "created_bypass": True}
    persist_premium.assert_awaited_once_with(42, "prem-new", "https://rmnw/sub/prem", short_uuid="ps")
    persist_bypass.assert_awaited_once_with(42, "byp-new", "https://rmnw/sub/byp", "bs")


@pytest.mark.asyncio
async def test_lazy_provision_creates_only_missing_entity(monkeypatch):
    """Premium already present → only bypass is created."""
    future = datetime.now(timezone.utc) + timedelta(days=30)
    persist_bypass = AsyncMock()
    _patch_db(
        monkeypatch,
        get_subscription_any_value={
            "telegram_id": 42, "uuid": "samopis", "status": "active",
            "expires_at": future, "source": "payment",
            "remnawave_uuid": None,                  # bypass missing
            "remnawave_premium_uuid": "already-set", # premium present
        },
        set_bypass_cache_mock=persist_bypass,
    )
    _reset_locks()

    from app.services import remnawave_premium, remnawave_bypass
    pres = AsyncMock()  # MUST NOT be called
    bres = remnawave_bypass.BypassCreateResult(
        ok=True, panel_uuid="byp", subscription_url="u", short_uuid="s",
        status=201, error=None, recovered=False,
    )
    with _patch_config(), \
         patch("app.services.remnawave_premium.create_premium_user_entity", pres), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", AsyncMock(return_value=bres)):
        out = await user_subscription_links._try_lazy_provision_entities(42)

    assert out == {"created_premium": False, "created_bypass": True}
    pres.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_provision_uses_10gb_for_paid_users(monkeypatch):
    """Non-trial source → bypass gets 10 GB (not 1 GB)."""
    future = datetime.now(timezone.utc) + timedelta(days=30)
    _patch_db(
        monkeypatch,
        get_subscription_any_value={
            "telegram_id": 42, "uuid": None, "status": "active",
            "expires_at": future, "source": "payment",
            "remnawave_uuid": None, "remnawave_premium_uuid": "exists",
        },
    )
    _reset_locks()

    from app.services import remnawave_bypass
    bcreate = AsyncMock(return_value=remnawave_bypass.BypassCreateResult(
        ok=True, panel_uuid="b", subscription_url="u", short_uuid="s",
        status=201, error=None, recovered=False,
    ))
    with _patch_config(), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", bcreate):
        await user_subscription_links._try_lazy_provision_entities(42)

    bcreate.assert_awaited_once()
    assert bcreate.call_args.kwargs["traffic_limit_bytes"] == 10 * 1024**3


@pytest.mark.asyncio
async def test_lazy_provision_skips_for_expired_subscription(monkeypatch):
    _patch_db(
        monkeypatch,
        get_subscription_any_value={
            "telegram_id": 42, "uuid": "samopis", "status": "expired",
            "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
            "source": "payment",
            "remnawave_uuid": None, "remnawave_premium_uuid": None,
        },
    )
    _reset_locks()
    pres = AsyncMock()
    bres = AsyncMock()
    with _patch_config(), \
         patch("app.services.remnawave_premium.create_premium_user_entity", pres), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", bres):
        out = await user_subscription_links._try_lazy_provision_entities(42)
    assert out == {"created_premium": False, "created_bypass": False}
    pres.assert_not_called()
    bres.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_provision_skips_when_no_subscription_row(monkeypatch):
    _patch_db(monkeypatch, get_subscription_any_value=None)
    _reset_locks()
    with _patch_config():
        out = await user_subscription_links._try_lazy_provision_entities(42)
    assert out == {"created_premium": False, "created_bypass": False}


@pytest.mark.asyncio
async def test_lazy_provision_short_circuits_when_remnawave_disabled(monkeypatch):
    db = SimpleNamespace(get_subscription_any=AsyncMock())
    monkeypatch.setitem(sys.modules, "database", db)
    _reset_locks()
    with _patch_config(enabled=False):
        out = await user_subscription_links._try_lazy_provision_entities(42)
    assert out == {"created_premium": False, "created_bypass": False}
    db.get_subscription_any.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_provision_uses_legacy_uuid_as_forced_vless(monkeypatch):
    """User has samopis uuid → premium gets forced vlessUuid for backward compat."""
    future = datetime.now(timezone.utc) + timedelta(days=30)
    _patch_db(
        monkeypatch,
        get_subscription_any_value={
            "telegram_id": 42, "uuid": "11111111-2222-3333-4444-555555555555",
            "status": "active", "expires_at": future, "source": "payment",
            "remnawave_uuid": None, "remnawave_premium_uuid": None,
        },
    )
    _reset_locks()
    from app.services import remnawave_premium, remnawave_bypass
    pcreate = AsyncMock(return_value=remnawave_premium.PremiumCreateResult(
        ok=True, panel_uuid="p", forced_uuid_accepted=True,
        subscription_url="u", status=201, error=None, recovered=False, short_uuid="s",
    ))
    bcreate = AsyncMock(return_value=remnawave_bypass.BypassCreateResult(
        ok=True, panel_uuid="b", subscription_url="u", short_uuid="s",
        status=201, error=None, recovered=False,
    ))
    with _patch_config(), \
         patch("app.services.remnawave_premium.create_premium_user_entity", pcreate), \
         patch("app.services.remnawave_bypass.create_bypass_user_entity", bcreate):
        await user_subscription_links._try_lazy_provision_entities(42)

    assert pcreate.call_args.kwargs["requested_uuid"] == "11111111-2222-3333-4444-555555555555"


# ── get_user_primary_subscription_url: integration ────────────────────

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
async def test_primary_url_falls_back_to_legacy_only_when_provision_fails(monkeypatch):
    """Lazy provision fails → only then we fall back to legacy URL."""
    _patch_db(monkeypatch, rows=[None, None, None, None])
    with _patch_config(), \
         patch.object(user_subscription_links, "_try_lazy_provision_entities",
                      AsyncMock(return_value={"created_premium": False, "created_bypass": False})), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://atlassecure.ru/api/sub/legacy"


@pytest.mark.asyncio
async def test_primary_url_lazy_provisions_then_returns_remnawave(monkeypatch):
    """Premium missing → lazy-provision → re-query returns the new URL."""
    _patch_db(monkeypatch, rows=[
        None, None,                                                        # premium lookup miss
        _Row(remnawave_premium_uuid="new", remnawave_premium_sub_url="https://rmnw/sub/new"),
    ])
    with _patch_config(), \
         patch.object(user_subscription_links, "_try_lazy_provision_entities",
                      AsyncMock(return_value={"created_premium": True, "created_bypass": True})), \
         patch.object(user_subscription_links, "_legacy_sub_url",
                      MagicMock(return_value="https://atlassecure.ru/api/sub/legacy")):
        url = await user_subscription_links.get_user_primary_subscription_url(42)
    assert url == "https://rmnw/sub/new"
