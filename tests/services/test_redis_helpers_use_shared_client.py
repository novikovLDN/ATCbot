"""The dashboard session / passkey challenge / admin settings / SBP-router
stores imported `get_client` from app.utils.redis_client — a name that never
existed there (the module exposes `get_redis`). The ImportError was swallowed
by each `_redis()` helper, so all four always fell back to process memory even
with REDIS_URL set: dashboard sessions lost on every redeploy, SBP-router
config never shared.
"""
import importlib

import pytest

from app.utils import redis_client

MODULES = [
    "app.services.admin_auth",
    "app.services.admin_passkeys",
    "app.services.admin_settings",
    "app.services.sbp_router",
]


@pytest.mark.parametrize("modname", MODULES)
async def test_redis_helper_returns_the_shared_client(monkeypatch, modname):
    sentinel = object()

    async def fake_get_redis():
        return sentinel

    monkeypatch.setattr(redis_client, "is_configured", lambda: True)
    monkeypatch.setattr(redis_client, "get_redis", fake_get_redis)
    mod = importlib.import_module(modname)
    assert await mod._redis() is sentinel


@pytest.mark.parametrize("modname", MODULES)
async def test_redis_helper_is_none_when_not_configured(monkeypatch, modname):
    monkeypatch.setattr(redis_client, "is_configured", lambda: False)
    mod = importlib.import_module(modname)
    assert await mod._redis() is None
