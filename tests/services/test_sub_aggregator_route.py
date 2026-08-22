"""
Тесты embedded sub-aggregator (app/api/sub_aggregator_route.py).

Покрытие:
  • Юниты чистых функций: token-regex, decode_body, parse_userinfo,
    hybrid_userinfo, cache-tiers (fresh/stale/miss/expiry/LRU),
    pair-cache + negative, attack-detector, clear_cache.
  • Интеграция aggregate(): fresh-miss→merge, hit, revoked, 404,
    partial-upstream, both-down→503, stale-fallback, invalidate.
  • Singleflight: N параллельных запросов одного token = 1 upstream-пара.

Мок: _fetch_upstream (без сети) + database.get_pool (без БД).
Модульное состояние сбрасывается autouse-фикстурой перед каждым тестом.
"""
import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.api import sub_aggregator_route as m


# ── Фейки ────────────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, text="", status=200, headers=None):
        self.text = text
        self.status_code = status
        self.headers = headers or {}


class FakeConn:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, *a, **k):
        return self._row


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self, row):
        self._row = row

    def acquire(self):
        return FakeAcquire(FakeConn(self._row))


class FakeRequest:
    def __init__(self, ua="Mozilla/5.0"):
        self.headers = {"user-agent": ua}
        self.url = "https://subscription.example/a/token"


def _b64_sub(lines):
    return base64.b64encode("\n".join(lines).encode()).decode()


def _pair_row(status="active"):
    return {
        "token": "tok12345",
        "main_sub_url": "https://panel/main",
        "gb_sub_url": "https://panel/gb",
        "status": status,
    }


@pytest.fixture(autouse=True)
def _reset_state():
    """Чистим всё модульное состояние перед каждым тестом."""
    m._cache.clear()
    m._pair_cache.clear()
    m._inflight.clear()
    for k in list(m._metrics.keys()):
        m._metrics[k] = 0
    m._attack_window_start = 0.0
    m._attack_not_found = 0
    m._attack_upstream_fail = 0
    yield


# ══════════════════════════════════════════════════════════════════════
# ЮНИТЫ — чистые функции
# ══════════════════════════════════════════════════════════════════════

def test_token_regex_valid():
    assert m._TOKEN_RE.match("abcd")
    assert m._TOKEN_RE.match("A1_z-9" * 10)

def test_token_regex_rejects_bad():
    assert not m._TOKEN_RE.match("ab")          # слишком коротко
    assert not m._TOKEN_RE.match("a" * 129)     # слишком длинно
    assert not m._TOKEN_RE.match("../etc")      # traversal
    assert not m._TOKEN_RE.match("a b")         # пробел
    assert not m._TOKEN_RE.match("токен")       # не-ASCII


def test_decode_body_base64():
    resp = FakeResp(text=_b64_sub(["vless://a", "vless://b"]))
    assert m._decode_body(resp) == ["vless://a", "vless://b"]

def test_decode_body_plaintext():
    resp = FakeResp(text="vless://x\nvless://y\n")
    assert m._decode_body(resp) == ["vless://x", "vless://y"]

def test_decode_body_strips_empty_lines():
    resp = FakeResp(text=_b64_sub(["vless://a", "", "  ", "vless://b"]))
    assert m._decode_body(resp) == ["vless://a", "vless://b"]

def test_decode_body_empty():
    assert m._decode_body(FakeResp(text="")) == []


def test_normalize_upstream_dead_host():
    # Мёртвый vps-cloud host → живой панельный
    assert m._normalize_upstream_url("https://subscription.vps-cloud.uk/abc123") \
        == "https://sub.atlassecure.ru/abc123"

def test_normalize_upstream_live_host_untouched():
    live = "https://sub.atlassecure.ru/abc123"
    assert m._normalize_upstream_url(live) == live

def test_normalize_upstream_empty():
    assert m._normalize_upstream_url("") == ""


def test_parse_userinfo():
    d = m._parse_userinfo("upload=10; download=20; total=100; expire=1789")
    assert d == {"upload": 10, "download": 20, "total": 100, "expire": 1789}

def test_parse_userinfo_garbage_safe():
    assert m._parse_userinfo("bad; x=; total=abc; download=5") == {"download": 5}

def test_hybrid_userinfo_traffic_from_gb_expire_from_main():
    main = "upload=1; download=1; total=1; expire=999"
    gb = "upload=5; download=7; total=80; expire=111"
    res = m._build_hybrid_userinfo(main, gb)
    # трафик от gb, expire от main
    assert res == "upload=5; download=7; total=80; expire=999"

def test_hybrid_userinfo_missing_all_zero():
    assert m._build_hybrid_userinfo("", "") == "upload=0; download=0; total=0; expire=0"

def test_hybrid_userinfo_expire_fallback_to_gb():
    # main без expire → берём из gb
    res = m._build_hybrid_userinfo("total=1", "expire=42; total=9")
    assert "expire=42" in res


# ── Cache tiers ───────────────────────────────────────────────────────

def test_cache_fresh_hit():
    m._cache_set("t", b"body", {"h": "1"})
    body, headers, state = m._cache_get("t")
    assert state == "fresh" and body == b"body" and headers["h"] == "1"

def test_cache_miss():
    assert m._cache_get("nope") == (None, None, "miss")

def test_cache_stale_after_fresh_expires(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    m._cache_set("t", b"body", {})
    t[0] += m.FRESH_TTL + 1          # fresh истёк, stale ещё жив
    _, _, state = m._cache_get("t")
    assert state == "stale"

def test_cache_fully_expired_evicts(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    m._cache_set("t", b"body", {})
    t[0] += m.STALE_TTL + 1
    assert m._cache_get("t")[2] == "miss"
    assert "t" not in m._cache          # физически удалён

def test_cache_lru_evicts_oldest(monkeypatch):
    monkeypatch.setattr(m, "MAX_CACHE_ENTRIES", 3)
    for i in range(5):
        m._cache_set(f"t{i}", b"x", {})
    assert len(m._cache) == 3
    assert "t0" not in m._cache and "t1" not in m._cache
    assert "t4" in m._cache

def test_cache_hit_refreshes_lru_position(monkeypatch):
    monkeypatch.setattr(m, "MAX_CACHE_ENTRIES", 3)
    for i in range(3):
        m._cache_set(f"t{i}", b"x", {})
    m._cache_get("t0")                  # трогаем t0 → он теперь свежий в LRU
    m._cache_set("t3", b"x", {})        # выселяет самый старый (t1)
    assert "t0" in m._cache and "t1" not in m._cache


# ── Pair cache ────────────────────────────────────────────────────────

def test_pair_cache_positive_and_negative():
    m._pair_set("t", {"token": "t"})
    assert m._pair_get("t") == ({"token": "t"}, True)
    m._pair_set("bad", None)            # negative
    assert m._pair_get("bad") == (None, True)

def test_pair_cache_expiry(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    m._pair_set("t", {"x": 1})
    t[0] += m.PAIR_TTL + 1
    assert m._pair_get("t") == (None, False)

def test_pair_negative_ttl_shorter(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    m._pair_set("bad", None)
    t[0] += m.NEG_PAIR_TTL + 1
    assert m._pair_get("bad") == (None, False)   # negative истёк быстрее


# ── clear_cache ───────────────────────────────────────────────────────

def test_clear_cache_token_wipes_both():
    m._cache_set("t", b"x", {})
    m._pair_set("t", {"y": 1})
    m.clear_cache("t")
    assert "t" not in m._cache and "t" not in m._pair_cache

def test_clear_cache_all():
    m._cache_set("a", b"x", {})
    m._pair_set("b", {"y": 1})
    m.clear_cache(None)
    assert len(m._cache) == 0 and len(m._pair_cache) == 0


# ── Attack detector ───────────────────────────────────────────────────

def test_attack_detector_fires_at_threshold(monkeypatch):
    fired = []
    monkeypatch.setattr(m, "_fire_attack_alert", lambda *, kind, count: fired.append((kind, count)))
    monkeypatch.setattr(m.time, "monotonic", lambda: 5000.0)
    m._attack_window_start = 5000.0
    for _ in range(m.ATTACK_NOT_FOUND_THRESHOLD):
        m._bump_attack_window("not_found")
    assert fired == [("not_found", m.ATTACK_NOT_FOUND_THRESHOLD)]

def test_attack_detector_single_fire_per_window(monkeypatch):
    fired = []
    monkeypatch.setattr(m, "_fire_attack_alert", lambda *, kind, count: fired.append(kind))
    monkeypatch.setattr(m.time, "monotonic", lambda: 5000.0)
    m._attack_window_start = 5000.0
    for _ in range(m.ATTACK_NOT_FOUND_THRESHOLD + 200):
        m._bump_attack_window("not_found")
    assert len(fired) == 1               # ровно раз, не спамит

def test_attack_detector_window_resets(monkeypatch):
    t = [5000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    m._attack_window_start = 5000.0
    m._bump_attack_window("not_found")
    t[0] += 61                            # окно истекло
    m._bump_attack_window("not_found")
    assert m._attack_not_found == 1       # счётчик сброшен


# ══════════════════════════════════════════════════════════════════════
# ИНТЕГРАЦИЯ — aggregate()
# ══════════════════════════════════════════════════════════════════════

def _patch_fetch(main_lines=("vless://main1", "vless://main2"),
                 gb_lines=("vless://gb1",),
                 main_headers=None, gb_headers=None,
                 main_status=200, gb_status=200):
    main_headers = main_headers or {"subscription-userinfo": "expire=999", "profile-title": "Atlas"}
    gb_headers = gb_headers or {"subscription-userinfo": "upload=5; download=7; total=80; expire=1"}

    async def fake_fetch(url, ua):
        if url.endswith("/main"):
            return None if main_status is None else FakeResp(_b64_sub(main_lines), main_status, main_headers)
        return None if gb_status is None else FakeResp(_b64_sub(gb_lines), gb_status, gb_headers)
    return fake_fetch


async def test_aggregate_fresh_miss_merges():
    fetch = AsyncMock(side_effect=_patch_fetch())
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        resp = await m.aggregate(FakeRequest(), token="tok12345")
    assert resp.status_code == 200
    assert resp.headers["x-cache"] == "miss"
    body = base64.b64decode(resp.body).decode()
    assert body.splitlines() == ["vless://main1", "vless://main2", "vless://gb1"]
    # hybrid userinfo: трафик gb, expire main
    assert resp.headers["subscription-userinfo"] == "upload=5; download=7; total=80; expire=999"
    assert fetch.await_count == 2

async def test_aggregate_dedupes():
    fetch = AsyncMock(side_effect=_patch_fetch(main_lines=("vless://x",), gb_lines=("vless://x", "vless://y")))
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        resp = await m.aggregate(FakeRequest(), token="tok12345")
    body = base64.b64decode(resp.body).decode()
    assert body.splitlines() == ["vless://x", "vless://y"]   # дубль убран

async def test_aggregate_second_call_is_hit():
    fetch = AsyncMock(side_effect=_patch_fetch())
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        await m.aggregate(FakeRequest(), token="tok12345")
        resp2 = await m.aggregate(FakeRequest(), token="tok12345")
    assert resp2.headers["x-cache"] == "hit"
    assert fetch.await_count == 2        # второй раз в панель НЕ ходили

async def test_aggregate_invalid_token_404():
    resp = await m.aggregate(FakeRequest(), token="../x")
    assert resp.status_code == 404
    assert m._metrics["not_found"] == 1

async def test_aggregate_unknown_token_404():
    with patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(None))):
        resp = await m.aggregate(FakeRequest(), token="tok99999")
    assert resp.status_code == 404
    assert m._metrics["not_found"] == 1

async def test_aggregate_revoked_stub():
    with patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row("revoked")))):
        resp = await m.aggregate(FakeRequest(), token="tok12345")
    assert resp.status_code == 200
    body = base64.b64decode(resp.body).decode()
    assert "00000000-0000-0000-0000-000000000000" in body
    assert m._metrics["revoked"] == 1

async def test_aggregate_partial_one_upstream_down():
    # gb упал (None) → отдаём только main
    fetch = AsyncMock(side_effect=_patch_fetch(gb_status=None))
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        resp = await m.aggregate(FakeRequest(), token="tok12345")
    assert resp.status_code == 200
    body = base64.b64decode(resp.body).decode()
    assert body.splitlines() == ["vless://main1", "vless://main2"]

async def test_aggregate_both_down_no_cache_503():
    fetch = AsyncMock(side_effect=_patch_fetch(main_status=None, gb_status=None))
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        resp = await m.aggregate(FakeRequest(), token="tok12345")
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "30"

async def test_aggregate_stale_served_when_upstream_dies(monkeypatch):
    # 1) успешный запрос → кеш; 2) fresh истёк; 3) панель упала → stale
    t = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: t[0])
    ok_fetch = AsyncMock(side_effect=_patch_fetch())
    with patch.object(m, "_fetch_upstream", ok_fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        await m.aggregate(FakeRequest(), token="tok12345")
        t[0] += m.FRESH_TTL + 1          # fresh истёк, stale жив
        dead_fetch = AsyncMock(side_effect=_patch_fetch(main_status=None, gb_status=None))
        with patch.object(m, "_fetch_upstream", dead_fetch):
            resp = await m.aggregate(FakeRequest(), token="tok12345")
    assert resp.status_code == 200
    assert resp.headers["x-cache"] == "stale"
    assert m._metrics["stale"] == 1

async def test_invalidate_endpoint_clears_both_caches():
    m._cache_set("tok12345", b"x", {})
    m._pair_set("tok12345", {"y": 1})
    with patch.object(m.config, "SUB_AGGREGATOR_INTERNAL_SECRET", ""):
        resp = await m.invalidate_cache(FakeRequest(), token="tok12345")
    assert resp.status_code == 200
    assert "tok12345" not in m._cache and "tok12345" not in m._pair_cache

async def test_invalidate_endpoint_secret_enforced():
    req = FakeRequest()
    req.headers = {"x-internal-secret": "wrong"}
    with patch.object(m.config, "SUB_AGGREGATOR_INTERNAL_SECRET", "right"):
        resp = await m.invalidate_cache(req, token="tok12345")
    assert resp.status_code == 403


# ── Singleflight ──────────────────────────────────────────────────────

async def test_singleflight_collapses_concurrent_requests():
    """50 параллельных запросов одного token → ровно 1 пара upstream GET."""
    calls = {"n": 0}

    async def slow_fetch(url, ua):
        calls["n"] += 1
        await asyncio.sleep(0.05)        # держим leader'а, чтобы налетели followers
        return FakeResp(_b64_sub(["vless://a"]), 200,
                        {"subscription-userinfo": "total=1; expire=1"})

    with patch.object(m, "_fetch_upstream", slow_fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        results = await asyncio.gather(*[
            m.aggregate(FakeRequest(), token="tok12345") for _ in range(50)
        ])
    assert all(r.status_code == 200 for r in results)
    # 2 upstream GET (main+gb) на ВСЕ 50 запросов, а не 100.
    assert calls["n"] == 2
    assert m._metrics["singleflight_wait"] >= 40


async def test_metrics_endpoint_shape():
    m._cache_set("t", b"x", {})
    m._metrics["hits"] = 90
    m._metrics["misses"] = 10
    resp = await m.metrics_endpoint()
    import json
    data = json.loads(resp.body)
    assert data["hit_ratio"] == 0.9
    assert data["cache_size"] == 1
    assert "avg_upstream_ms" in data
