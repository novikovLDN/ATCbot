"""
Нагрузочный тест sub-aggregator — измеряет реальную пропускную способность
in-process (без сети/БД: upstream и pool замоканы).

Запуск с цифрами:  python3 -m pytest tests/services/test_sub_aggregator_load.py -s -q

Смысл: показать, что агрегатор НЕ «слетает» под нагрузкой и найти потолок.
Узкое место в проде — панель Remnawave, а не агрегатор (см. вывод -s).

Пороги в assert'ах намеренно консервативные (×5-10 запас к CI-железу),
чтобы тест не флапал, но ловил регресс если что-то станет в разы медленнее.
"""
import asyncio
import base64
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.api import sub_aggregator_route as m
from tests.services.test_sub_aggregator_route import (
    FakePool, FakeRequest, FakeResp, _b64_sub, _pair_row,
)


@pytest.fixture(autouse=True)
def _reset():
    m._cache.clear(); m._pair_cache.clear(); m._inflight.clear()
    for k in list(m._metrics): m._metrics[k] = 0
    yield


def _sub_lines(n):
    # Реалистичная подписка: n серверов × ~120 байт строки.
    return [f"vless://{'a'*36}@server{i}.example.com:443?type=tcp&security=reality#Server-{i}" for i in range(n)]


async def _hammer(token_pool, total_requests, concurrency, fetch, row=None):
    """Выпустить total_requests запросов пачками по concurrency. Вернуть elapsed."""
    row = row or _pair_row()
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(row))):
        t0 = time.perf_counter()
        i = 0
        while i < total_requests:
            batch = []
            for _ in range(min(concurrency, total_requests - i)):
                tok = token_pool[i % len(token_pool)]
                batch.append(m.aggregate(FakeRequest(), token=tok))
                i += 1
            await asyncio.gather(*batch)
        return time.perf_counter() - t0


async def test_load_hot_cache_throughput(monkeypatch):
    """ГОРЯЧИЙ КЭШ: перф fresh-hit fast-path. В проде FRESH_TTL=0 (кеша нет,
    каждый запрос свежий), но механика fresh-tier жива и должна быть быстрой
    если её включить → тестируем под ttl>0."""
    monkeypatch.setattr(m, "FRESH_TTL", 60)
    servers = _sub_lines(40)
    headers = {"subscription-userinfo": "upload=1; download=1; total=80; expire=1789603200",
               "profile-title": "Atlas"}

    async def fetch(url, ua):
        return FakeResp(_b64_sub(servers), 200, headers)

    token = "hottoken1"
    # Прогрев одним запросом.
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        await m.aggregate(FakeRequest(), token=token)

    N = 20_000
    elapsed = await _hammer([token], N, concurrency=200, fetch=fetch)
    rps = N / elapsed
    print(f"\n[HOT CACHE]  {N} req in {elapsed:.3f}s → {rps:,.0f} rps  "
          f"({elapsed/N*1e6:.1f} µs/req), upstream calls after warmup: {m._metrics['upstream_ok']//2}")
    # На CI-железе горячий кэш должен давать минимум 5000 rps.
    assert rps > 5000, f"hot-cache throughput too low: {rps:.0f} rps"
    # После прогрева в панель больше НЕ ходили (кроме 1 warmup).
    assert m._metrics["upstream_ok"] == 2


async def test_load_cold_miss_throughput():
    """ХОЛОДНЫЙ КЭШ: каждый запрос — уникальный token → merge + 2 upstream.
    Худший случай (например, cold-start после рестарта). Upstream мгновенный
    (мок) → меряем чистую CPU-стоимость merge на нашей стороне."""
    servers = _sub_lines(40)
    headers = {"subscription-userinfo": "upload=1; download=1; total=80; expire=1789603200"}

    async def fetch(url, ua):
        return FakeResp(_b64_sub(servers), 200, headers)

    N = 5_000
    tokens = [f"cold{i:07d}" for i in range(N)]   # все уникальны → всегда miss
    elapsed = await _hammer(tokens, N, concurrency=100, fetch=fetch)
    rps = N / elapsed
    print(f"[COLD MISS]  {N} req in {elapsed:.3f}s → {rps:,.0f} rps  "
          f"({elapsed/N*1e6:.1f} µs/req), merges={m._metrics['misses']}")
    assert m._metrics["misses"] == N
    # Merge 40 серверов + base64 + dedup: даже холодным держим >1000 rps CPU-bound.
    assert rps > 1000, f"cold-miss throughput too low: {rps:.0f} rps"


async def test_load_realistic_mixed():
    """РЕАЛИСТИЧНО: 10k подписок, hit-ratio ~95% (клиенты дёргают раз/час,
    большинство попадает в fresh-кэш). Панель отвечает ~40мс."""
    servers = _sub_lines(30)
    headers = {"subscription-userinfo": "upload=1; download=1; total=80; expire=1789603200"}

    async def fetch(url, ua):
        await asyncio.sleep(0.04)      # панель ~40мс
        return FakeResp(_b64_sub(servers), 200, headers)

    # 200 «активных» токенов, прогреваем 190 из них (95% hit).
    tokens = [f"mix{i:05d}" for i in range(200)]
    with patch.object(m, "_fetch_upstream", fetch), \
         patch.object(m.database, "get_pool", AsyncMock(return_value=FakePool(_pair_row()))):
        await asyncio.gather(*[m.aggregate(FakeRequest(), token=t) for t in tokens[:190]])

    N = 10_000
    # 95% запросов на прогретые токены, 5% на холодные.
    pool = tokens[:190] * 19 + tokens[190:] * 10   # ~95/5 микс
    elapsed = await _hammer(pool[:N] if len(pool) >= N else (pool * (N // len(pool) + 1))[:N],
                            N, concurrency=200, fetch=fetch)
    rps = N / elapsed
    total = m._metrics["hits"] + m._metrics["misses"]
    ratio = m._metrics["hits"] / total if total else 0
    print(f"[MIXED 95%]  {N} req in {elapsed:.3f}s → {rps:,.0f} rps  "
          f"hit-ratio={ratio:.2%}, avg-upstream={m._metrics['upstream_ms_sum']//max(1,m._metrics['upstream_count'])}ms")
    assert rps > 2000, f"mixed throughput too low: {rps:.0f} rps"


async def test_load_memory_bounded_under_flood():
    """FLOOD уникальными токенами не должен разорвать память — LRU держит cap."""
    servers = _sub_lines(20)
    headers = {"subscription-userinfo": "total=1; expire=1"}

    async def fetch(url, ua):
        return FakeResp(_b64_sub(servers), 200, headers)

    with patch.object(m, "MAX_CACHE_ENTRIES", 1000):
        tokens = [f"flood{i:07d}" for i in range(5000)]
        await _hammer(tokens, 5000, concurrency=200, fetch=fetch)
        print(f"[FLOOD]      5000 уникальных токенов → cache_size={len(m._cache)} (cap=1000)")
        assert len(m._cache) <= 1000    # LRU удержал границу — памяти не течёт
