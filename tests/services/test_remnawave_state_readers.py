"""T4 — remnawave_api.get_bypass_state / get_premium_state.

Precise readers for the provisioning CAS: unlike get_bypass_entity_safe (None =
absent OR panel down) they return ("present", entity) | ("absent", None) |
("unavailable", None) so a panel outage is retried and never mistaken for a
missing entity (which would trigger a create). _request_raw is scripted.
"""
from unittest.mock import AsyncMock

import pytest

import config
import database
from app.services import remnawave_api as api
from app.services import remnawave_premium

GIB = 1024 ** 3
TG = 4242
PREMIUM_USERNAME = remnawave_premium.build_premium_username(TG)

BYPASS = {"id": 12, "uuid": "b-uuid", "username": str(TG), "telegramId": TG,
          "trafficLimitBytes": 3 * GIB, "expireAt": "2099-01-01T00:00:00.000Z",
          "subscriptionUrl": "https://s/b", "shortUuid": "bs"}
PREMIUM = {"id": 22, "uuid": "p-uuid", "username": PREMIUM_USERNAME, "telegramId": TG,
           "trafficLimitBytes": 0, "expireAt": "2026-10-13T12:00:00.000Z",
           "subscriptionUrl": "https://s/p", "shortUuid": "ps"}


def ok(entity):
    return {"ok": True, "status": 200, "body": {"response": entity}, "response": entity}


def err(status, body=None):
    return {"ok": False, "status": status, "body": body, "response": body}


NET = {"ok": False, "status": 0, "body": None, "response": None, "error": "timeout"}
NOT_FOUND = err(404, {"message": "User not found", "statusCode": 404})


class Raw:
    """Scripted _request_raw: GET by path, resolve by username."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    async def __call__(self, method, path, **kwargs):
        if method == "POST" and path == "/api/users/resolve":
            key = ("RESOLVE", kwargs["json"]["username"])
        else:
            key = (method, path)
        self.calls.append(key)
        return self.routes[key]


@pytest.fixture
def raw(monkeypatch):
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)

    def _install(routes, *, bypass_id=None, premium_id=None):
        r = Raw(routes)
        monkeypatch.setattr(api, "_request_raw", r)
        monkeypatch.setattr(database, "get_remnawave_id", AsyncMock(return_value=bypass_id))
        monkeypatch.setattr(database, "get_remnawave_premium_id", AsyncMock(return_value=premium_id))
        return r

    return _install


# ── bypass ─────────────────────────────────────────────────────────────

async def test_bypass_present_via_cached_id(raw):
    r = raw({("GET", "/api/users/12"): ok(BYPASS)}, bypass_id=12)
    assert await api.get_bypass_state(TG) == ("present", BYPASS)
    assert r.calls == [("GET", "/api/users/12")]


async def test_bypass_cached_id_pointing_at_premium_falls_back_to_username(raw):
    r = raw({("GET", "/api/users/22"): ok(PREMIUM), ("RESOLVE", str(TG)): ok(BYPASS)}, bypass_id=22)
    kind, ent = await api.get_bypass_state(TG)
    assert kind == "present" and ent["id"] == 12
    assert r.calls == [("GET", "/api/users/22"), ("RESOLVE", str(TG))]


@pytest.mark.parametrize("cached", [NOT_FOUND, err(400, {"message": "expected number"})])
async def test_bypass_cached_id_gone_falls_back_to_username(raw, cached):
    r = raw({("GET", "/api/users/12"): cached, ("RESOLVE", str(TG)): NOT_FOUND}, bypass_id=12)
    assert await api.get_bypass_state(TG) == ("absent", None)
    assert r.calls == [("GET", "/api/users/12"), ("RESOLVE", str(TG))]


@pytest.mark.parametrize("resp", [NET, err(500), err(502), err(503), err(401), err(403), err(429)])
async def test_bypass_cached_read_unavailable_does_not_fall_through(raw, resp):
    r = raw({("GET", "/api/users/12"): resp}, bypass_id=12)
    assert await api.get_bypass_state(TG) == ("unavailable", None)
    assert r.calls == [("GET", "/api/users/12")]


@pytest.mark.parametrize("resp", [NOT_FOUND, err(400, {"message": "User not found by username"})])
async def test_bypass_resolve_not_found_is_absent(raw, resp):
    raw({("RESOLVE", str(TG)): resp})
    assert await api.get_bypass_state(TG) == ("absent", None)


@pytest.mark.parametrize("resp", [
    NET, err(500), err(503), err(401), err(429),
    err(422, {"message": "validation failed"}),     # ambiguous 4xx → retry, never create
])
async def test_bypass_resolve_failure_is_unavailable(raw, resp):
    raw({("RESOLVE", str(TG)): resp})
    assert await api.get_bypass_state(TG) == ("unavailable", None)


async def test_bypass_resolve_trimmed_entity_is_completed_by_id(raw):
    trimmed = {"id": 12, "username": str(TG), "telegramId": TG}
    r = raw({("RESOLVE", str(TG)): ok({"user": trimmed}), ("GET", "/api/users/12"): ok(BYPASS)})
    kind, ent = await api.get_bypass_state(TG)
    assert kind == "present" and ent["trafficLimitBytes"] == 3 * GIB
    assert r.calls == [("RESOLVE", str(TG)), ("GET", "/api/users/12")]


@pytest.mark.parametrize("full, expected", [(err(503), "unavailable"), (NOT_FOUND, "absent")])
async def test_bypass_trimmed_entity_full_fetch_failure(raw, full, expected):
    trimmed = {"id": 12, "username": str(TG)}
    raw({("RESOLVE", str(TG)): ok(trimmed), ("GET", "/api/users/12"): full})
    assert await api.get_bypass_state(TG) == (expected, None)


async def test_bypass_resolve_other_username_is_unavailable(raw):
    raw({("RESOLVE", str(TG)): ok(PREMIUM)})
    assert await api.get_bypass_state(TG) == ("unavailable", None)


async def test_bypass_cache_lookup_error_falls_back_to_username(raw, monkeypatch):
    r = raw({("RESOLVE", str(TG)): ok(BYPASS)})
    monkeypatch.setattr(database, "get_remnawave_id", AsyncMock(side_effect=RuntimeError("db")))
    assert (await api.get_bypass_state(TG))[0] == "present"
    assert r.calls == [("RESOLVE", str(TG))]


async def test_disabled_remnawave_is_unavailable(raw, monkeypatch):
    r = raw({})
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", False)
    assert await api.get_bypass_state(TG) == ("unavailable", None)
    assert await api.get_premium_state(TG) == ("unavailable", None)
    assert r.calls == []


# ── premium ────────────────────────────────────────────────────────────

async def test_premium_present_via_cached_id(raw):
    r = raw({("GET", "/api/users/22"): ok(PREMIUM)}, premium_id=22)
    assert await api.get_premium_state(TG) == ("present", PREMIUM)
    assert r.calls == [("GET", "/api/users/22")]


async def test_premium_cached_legacy_username_accepted_by_telegram_id(raw):
    legacy = {**PREMIUM, "username": "alice_premium"}
    raw({("GET", "/api/users/22"): ok(legacy)}, premium_id=22)
    assert await api.get_premium_state(TG) == ("present", legacy)


async def test_premium_cached_id_pointing_at_bypass_falls_back(raw):
    r = raw({("GET", "/api/users/12"): ok(BYPASS), ("RESOLVE", PREMIUM_USERNAME): ok(PREMIUM)},
            premium_id=12)
    kind, ent = await api.get_premium_state(TG)
    assert kind == "present" and ent["id"] == 22
    assert r.calls == [("GET", "/api/users/12"), ("RESOLVE", PREMIUM_USERNAME)]


async def test_premium_absent_and_unavailable(raw):
    raw({("RESOLVE", PREMIUM_USERNAME): NOT_FOUND})
    assert await api.get_premium_state(TG) == ("absent", None)
    raw({("RESOLVE", PREMIUM_USERNAME): err(500)})
    assert await api.get_premium_state(TG) == ("unavailable", None)
