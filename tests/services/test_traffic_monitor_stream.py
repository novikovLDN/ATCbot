"""traffic_monitor reads the panel in ONE paged stream pass per interval.

Production (550k users): the worker did `GET /api/users/{id}` + sleep(0.2) for
every active subscription with a panel uuid (thousands of bypass-only rows
included). A pass never finished within the 5-minute interval, so the panel saw
a constant ~1.5 req/s (~130k requests/day).

Now: `GET /api/users/stream` in pages of 500 (get_all_users, 3.4.3 contract:
nextCursor is a string, no `total`), a compact index of the rows we need, the
same threshold / notification / flag logic, zero per-user GETs. A failed stream
skips the iteration (no partial data, no alerts); passes never overlap.
"""
from __future__ import annotations

import asyncio
import math
import re
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

import database
from app.services import remnawave_api
from app.workers import traffic_monitor as tm

GB = 1024**3
MB = 1024**2
FILLER = 1226  # other panel entities (premium entities, other users' bypass …)


def _entity(pid: int, limit: int, used: int) -> dict:
    return {
        "id": pid, "vlessUuid": f"00000000-0000-4000-8000-{pid:012d}", "username": str(pid),
        "telegramId": pid, "trafficLimitBytes": limit, "status": "ACTIVE",
        "userTraffic": {"usedTrafficBytes": used}, "subscriptionUrl": f"https://x/{pid}",
    }


FLAGS_NONE: dict = {}
FLAGS_8 = {"traffic_notified_8gb": True}
FLAGS_ALL_BUT_0 = {"traffic_notified_8gb": True, "traffic_notified_5gb": True, "traffic_notified_3gb": True,
                   "traffic_notified_1gb": True, "traffic_notified_500mb": True}
FLAGS_ALL = dict(FLAGS_ALL_BUT_0, traffic_notified_0=True)

# telegram_id, panel id (None = not in the panel), limit, used, flags
CASES = [
    (101, 101, 500 * MB, 0, FLAGS_NONE),                 # trial 500 MB, untouched: nothing
    (102, 102, 10 * GB, int(2.5 * GB), FLAGS_NONE),      # 7.5 GB left → 8 GB notice
    (103, 103, 10 * GB, 6 * GB, FLAGS_8),                # 4 GB left, 8 GB sent → 5 GB notice
    (104, 104, 15 * GB, 16 * GB, FLAGS_ALL_BUT_0),       # exhausted → 0 notice
    (105, 105, 0, 50 * GB, FLAGS_NONE),                  # unlimited premium: nothing
    (106, None, 10 * GB, 9 * GB, FLAGS_NONE),            # not in the panel: nothing
    (107, 107, 10 * GB, int(9.7 * GB), FLAGS_ALL),       # everything already sent
    (108, 108, 2 * GB, int(1.2 * GB), FLAGS_NONE),       # 0.8 GB of 2 GB → 1 GB notice
]
EXPECTED = [
    (102, int(7.5 * GB), "traffic_notified_8gb"),
    (103, 4 * GB, "traffic_notified_5gb"),
    (104, 0, "traffic_notified_0"),
    (108, 2 * GB - int(1.2 * GB), "traffic_notified_1gb"),
]


class FakePanel:
    def __init__(self, entities):
        self.entities = entities
        self.by_id = {e["id"]: e for e in entities}
        self.stream_calls: list = []
        self.user_gets: list = []
        self.fail_stream_after: int | None = None   # fail every stream call after N ok pages
        self.gate: asyncio.Event | None = None      # hold the first stream page until set
        self.entered = asyncio.Event()

    async def request(self, method, path, quiet=False, **kwargs):
        if method == "GET" and path.startswith("/api/users/stream"):
            q = parse_qs(urlparse(path).query)
            self.stream_calls.append(q)
            self.entered.set()
            if self.gate is not None:
                await self.gate.wait()
            if self.fail_stream_after is not None and len(self.stream_calls) > self.fail_stream_after:
                return None
            size = int(q["size"][0])
            start = int(q["cursor"][0]) if "cursor" in q else 0
            assert "cursor" not in q or isinstance(q["cursor"][0], str)
            end = min(start + size, len(self.entities))
            more = end < len(self.entities)
            return {"users": [dict(e) for e in self.entities[start:end]],
                    "nextCursor": str(end) if more else None, "hasMore": more}
        m = re.fullmatch(r"/api/users/(\d+)", path)
        if method == "GET" and m:
            self.user_gets.append(int(m.group(1)))
            return self.by_id.get(int(m.group(1)))
        raise AssertionError(f"unexpected panel call {method} {path}")


def _world(monkeypatch, cases=CASES, *, filler=FILLER):
    entities = [_entity(pid, limit, used) for _tg, pid, limit, used, _f in cases if pid is not None]
    entities += [_entity(900_000 + i, 10 * GB, 0) for i in range(filler)]
    panel = FakePanel(entities)
    monkeypatch.setattr(remnawave_api, "_request", panel.request)
    monkeypatch.setattr(tm, "PAGE_DELAY_S", 0.0, raising=False)

    rows = [{"telegram_id": tg, "remnawave_uuid": _entity(pid or 555_000 + tg, 0, 0)["vlessUuid"],
             "remnawave_id": pid or 555_000 + tg, "subscription_type": "basic"}
            for tg, pid, _l, _u, _f in cases]
    flags = {tg: dict(f) for tg, _p, _l, _u, f in cases}
    state = {"sent": [], "set": [], "flag_reads": 0, "panel": panel, "rows": rows}

    async def get_active_remnawave_users():
        return [dict(r) for r in state["rows"]]

    async def get_traffic_notification_flags(tg):
        state["flag_reads"] += 1
        return {"traffic_notified_8gb": False, "traffic_notified_5gb": False, "traffic_notified_3gb": False,
                "traffic_notified_1gb": False, "traffic_notified_500mb": False, "traffic_notified_0": False,
                **flags.get(tg, {})}

    async def set_traffic_notification_flag(tg, key):
        state["set"].append((tg, key))
        flags.setdefault(tg, {})[key] = True

    async def send(bot, tg, remaining, key):
        state["sent"].append((tg, remaining, key))

    monkeypatch.setattr(database, "get_active_remnawave_users", get_active_remnawave_users)
    monkeypatch.setattr(database, "get_traffic_notification_flags", get_traffic_notification_flags)
    monkeypatch.setattr(database, "set_traffic_notification_flag", set_traffic_notification_flag)
    monkeypatch.setattr(tm, "_send_traffic_notification", send)
    return state


async def test_notification_decisions_are_the_same_as_the_per_user_checks(monkeypatch):
    state = _world(monkeypatch)
    await tm.traffic_monitor_iteration(MagicMock())
    assert state["sent"] == EXPECTED
    assert state["set"] == [(tg, key) for tg, _r, key in EXPECTED]


async def test_one_stream_pass_and_no_per_user_gets(monkeypatch):
    state = _world(monkeypatch)
    panel = state["panel"]
    await tm.traffic_monitor_iteration(MagicMock())
    assert panel.user_gets == [], "no GET /api/users/{id} per user"
    assert len(panel.stream_calls) == math.ceil(len(panel.entities) / 500) == 3
    assert all(q["size"] == ["500"] for q in panel.stream_calls)
    # flags are read only for users a threshold can fire for (not for every row)
    assert state["flag_reads"] == len(EXPECTED) + 1   # +1: tg 107 has everything sent


async def test_second_pass_sends_nothing_new(monkeypatch):
    state = _world(monkeypatch)
    await tm.traffic_monitor_iteration(MagicMock())
    first = list(state["sent"])
    await tm.traffic_monitor_iteration(MagicMock())
    # one notice per user per pass: 104 got "0" (the last one); 102/103/108 have
    # nothing lower to reach at the same usage.
    assert state["sent"] == first


async def test_legacy_row_without_numeric_id_is_matched_by_vless_uuid(monkeypatch):
    state = _world(monkeypatch)
    for r in state["rows"]:
        if r["telegram_id"] == 102:
            r["remnawave_id"] = None
    await tm.traffic_monitor_iteration(MagicMock())
    assert (102, int(7.5 * GB), "traffic_notified_8gb") in state["sent"]
    assert state["panel"].user_gets == []


async def test_stream_failure_skips_the_iteration(monkeypatch):
    state = _world(monkeypatch)
    monkeypatch.setattr(tm, "STREAM_MAX_RETRIES", 1, raising=False)
    state["panel"].fail_stream_after = 1          # page 1 ok, page 2 fails
    await tm.traffic_monitor_iteration(MagicMock())
    assert state["sent"] == [] and state["set"] == [] and state["flag_reads"] == 0
    assert state["panel"].user_gets == []


async def test_passes_never_overlap(monkeypatch):
    state = _world(monkeypatch)
    panel = state["panel"]
    panel.gate = asyncio.Event()
    first = asyncio.create_task(tm.traffic_monitor_iteration(MagicMock()))
    await asyncio.wait_for(panel.entered.wait(), timeout=2)
    calls = len(panel.stream_calls)
    await asyncio.wait_for(tm.traffic_monitor_iteration(MagicMock()), timeout=2)   # skipped
    assert len(panel.stream_calls) == calls
    panel.gate.set()
    await asyncio.wait_for(first, timeout=5)
    assert state["sent"] == EXPECTED


async def test_get_all_users_with_on_page_keeps_nothing(monkeypatch):
    panel = FakePanel([_entity(i + 1, GB, 0) for i in range(1200)])
    monkeypatch.setattr(remnawave_api, "_request", panel.request)
    seen = []
    result = await remnawave_api.get_all_users(page_size=500, on_page=lambda b: seen.append(len(b)))
    assert seen == [500, 500, 200] and result == []
    assert await remnawave_api.get_all_users(page_size=500) and len(panel.stream_calls) == 6


@pytest.mark.parametrize("n,calls", [(1, 1), (500, 1), (501, 2)])
async def test_stream_call_count_is_ceil_n_over_500(monkeypatch, n, calls):
    state = _world(monkeypatch, cases=[], filler=n)
    state["rows"] = [{"telegram_id": 1, "remnawave_uuid": "x", "remnawave_id": 900_000, "subscription_type": "basic"}]
    await tm.traffic_monitor_iteration(MagicMock())
    assert len(state["panel"].stream_calls) == calls
