"""The dashboard SBP-router switch (platega / wata / split) silently reverted
to the default (platega) 30 s after it was set whenever Redis was absent
(REDIS_URL not set — and, until 2e6e8ee9, always): get_config() rebuilt the
expired in-process cache from _DEFAULTS instead of the value set_config()
stored. An admin who routed SBP to WATA because Platega was down was undone.
"""
import pytest

from app.services import sbp_router
from app.utils import redis_client


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.setattr(redis_client, "is_configured", lambda: False)
    monkeypatch.setattr(sbp_router, "_cache", dict(sbp_router._DEFAULTS))
    if hasattr(sbp_router, "_mem_config"):
        monkeypatch.setattr(sbp_router, "_mem_config", dict(sbp_router._DEFAULTS))
    monkeypatch.setattr(sbp_router, "_cache_expires_at", 0.0)


async def test_set_config_survives_the_cache_ttl_without_redis(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(sbp_router.time, "monotonic", lambda: clock[0])

    await sbp_router.set_config(mode="wata", wata_percent=70)
    assert (await sbp_router.get_config())["mode"] == "wata"

    clock[0] += sbp_router._CACHE_TTL_SEC + 1       # cache expired
    cfg = await sbp_router.get_config()
    assert cfg == {"mode": "wata", "wata_percent": 70}


async def test_default_is_platega_until_something_is_set():
    assert (await sbp_router.get_config())["mode"] == "platega"
