"""Remnawave tag backfill (app/services/remnawave_tags) on the 3.4.3 HTTP fake.

Owner decision 2026-09-14: users WITH AN ACTIVE SUBSCRIPTION only, tags only,
≤ 2 PATCH/s, only entities whose tag differs, resumable, one job at a time,
errors counted (never abort), one admin alert at the end. Also the CLI script.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import database.core
from app.services import admin_alerts, purchase_flow, remnawave_api
from app.services.remnawave_tags import service as svc
from tests.fakes.remnawave_http import GIB, FakeRemnawaveHTTP

FUTURE = datetime.now(timezone.utc) + timedelta(days=20)
ROWS = [
    {"telegram_id": 101, "subscription_type": "basic", "source": "trial"},                    # TRIAL
    {"telegram_id": 102, "subscription_type": "basic", "source": "payment"},                  # BASIC
    {"telegram_id": 103, "subscription_type": "plus", "source": "payment"},                   # PLUS (right)
    {"telegram_id": 104, "subscription_type": "basic", "is_combo": True, "source": "payment"},  # COMBO_BASIC
    {"telegram_id": 105, "subscription_type": "plus", "is_combo": True, "source": "payment"},   # COMBO_PLUS
    {"telegram_id": 106, "subscription_type": "biz_team", "source": "payment"},               # legacy → PLUS
    {"telegram_id": 107, "subscription_type": "basic", "source": "payment"},                  # no entities
]


class MemStore:
    def __init__(self):
        self.state = None

    async def load(self):
        return None if self.state is None else json.loads(json.dumps(self.state))

    async def save(self, state):
        self.state = json.loads(json.dumps(state))


@pytest.fixture
def w(monkeypatch):
    http = FakeRemnawaveHTTP().install(monkeypatch)
    rows = {r["telegram_id"]: dict(r) for r in ROWS}
    fresh: dict = {}          # telegram_id → row the batch re-read sees (None = left the scope)

    async def load(ids=None):
        if ids is None:
            return [dict(r) for r in rows.values()]
        out = []
        for tg in ids:
            row = fresh.get(tg, rows.get(tg))
            if row is not None:
                out.append(dict(row))
        return out

    monkeypatch.setattr(svc, "load_active_rows", load)
    store = MemStore()
    monkeypatch.setattr(svc, "_store", store)
    clock = {"t": 1000.0}
    sleeps: list = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(svc, "_clock", lambda: clock["t"])
    monkeypatch.setattr(svc, "_sleep", fake_sleep)
    patch_times: list = []
    real_set = remnawave_api.set_user_tag

    async def timed_set(user_id, tag):
        patch_times.append(clock["t"])
        return await real_set(user_id, tag)

    monkeypatch.setattr(remnawave_api, "set_user_tag", timed_set)
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", alerts)
    monkeypatch.setattr(purchase_flow, "_alert_bot", lambda: object())
    for r in ROWS[:-1]:
        http.seed_premium(r["telegram_id"], FUTURE)
        http.seed_bypass(r["telegram_id"], GIB)
    http.premium(103)["tag"] = "PLUS"
    http.bypass(103)["tag"] = "BYPASS"
    http.premium(102)["tag"] = "TRIAL"                   # wrong tag → corrected
    http.seed_premium(999, FUTURE)                       # not an active subscriber: never touched
    svc.reset_for_tests()
    yield SimpleNamespace(http=http, rows=rows, fresh=fresh, store=store, sleeps=sleeps,
                          patch_times=patch_times, alerts=alerts)
    svc.reset_for_tests()


EXPECTED = {
    101: ("TRIAL", "BYPASS"), 102: ("BASIC", "BYPASS"), 103: ("PLUS", "BYPASS"),
    104: ("COMBO_BASIC", "BYPASS"), 105: ("COMBO_PLUS", "BYPASS"), 106: ("PLUS", "BYPASS"),
}


def tags_now(http):
    return {tg: (http.premium(tg)["tag"], http.bypass(tg)["tag"]) for tg in EXPECTED}


async def finish():
    await svc._task


# ── preview ────────────────────────────────────────────────────────────

async def test_preview_counts_and_changes_nothing(w):
    s = await svc.preview()
    assert (s["users"], s["entities"], s["differ"], s["already"], s["missing"]) == (7, 12, 10, 2, 2)
    by_tag = {t["tag"]: (t["differ"], t["total"]) for t in s["tags"]}
    assert by_tag == {"TRIAL": (1, 1), "BASIC": (1, 1), "PLUS": (1, 2), "COMBO_BASIC": (1, 1),
                      "COMBO_PLUS": (1, 1), "BYPASS": (5, 6)}
    assert s["eta_seconds"] == 5
    assert w.http.writes() == []


# ── the job ────────────────────────────────────────────────────────────

async def test_job_patches_only_differing_entities_with_a_tag_only_payload(w):
    r = await svc.start(1)
    assert r["ok"] and r["status"]["running"]
    await finish()
    st = await svc.get_status()
    assert (st["state"], st["total"], st["done"], st["patched"], st["errors"]) == ("done", 10, 10, 10, 0)
    assert tags_now(w.http) == EXPECTED
    patches = w.http.tag_patches()
    assert len(patches) == 10
    assert all(set(body) == {"id", "tag"} for _, _, body in patches)       # tags only
    touched = {body["id"] for _, _, body in patches}
    assert w.http.premium(103)["id"] not in touched and w.http.bypass(103)["id"] not in touched
    assert w.http.premium(999)["id"] not in touched and w.http.premium(999)["tag"] is None
    assert st["per_tag"]["BYPASS"] == 5 and st["per_tag"]["PLUS"] == 1
    w.alerts.assert_awaited_once()
    assert w.alerts.await_args.kwargs["force"] is True
    assert "изменено: 10 из 10" in w.alerts.await_args.args[2]


async def test_pacing_is_at_most_two_patches_per_second(w):
    await svc.start(1)
    await finish()
    gaps = [b - a for a, b in zip(w.patch_times, w.patch_times[1:])]
    assert len(gaps) == 9 and min(gaps) >= 0.5 - 1e-9


async def test_a_failing_entity_is_counted_and_the_run_goes_on(w):
    bad = w.http.premium(104)["id"]
    w.http.fail("PATCH", lambda req, body: body.get("id") == bad, status=500)
    await svc.start(1)
    await finish()
    st = await svc.get_status()
    assert (st["state"], st["patched"], st["errors"]) == ("done", 9, 1)
    assert "tg:104" in st["last_error"]
    w.alerts.assert_awaited_once()


async def test_one_job_at_a_time(w):
    gate = asyncio.Event()

    async def blocked(_s):
        await gate.wait()

    svc._sleep = blocked
    await svc.start(1)
    again = await svc.start(1)
    assert again == {"ok": False, "error": "already_running", "status": again["status"]}
    gate.set()
    await finish()


async def test_the_target_tag_is_reread_before_the_patch(w):
    w.fresh[102] = {"telegram_id": 102, "subscription_type": "plus", "source": "payment"}  # upgraded meanwhile
    w.fresh[105] = None                                                                   # expired meanwhile
    await svc.start(1)
    await finish()
    st = await svc.get_status()
    assert w.http.premium(102)["tag"] == "PLUS"
    assert w.http.premium(105)["tag"] is None and w.http.bypass(105)["tag"] is None
    assert (st["total"], st["patched"]) == (8, 8)


async def test_pause_then_resume_keeps_counters(w):
    gate = asyncio.Event()

    async def gated(seconds):
        await gate.wait()

    svc._sleep = gated
    await svc.start(1)
    for _ in range(200):
        if w.http.tag_patches():
            break
        await asyncio.sleep(0)
    pausing = asyncio.create_task(svc.pause())
    await asyncio.sleep(0)
    gate.set()
    r = await pausing
    assert r["ok"] and r["status"]["state"] == "paused"
    assert r["status"]["patched"] == 1
    w.alerts.assert_not_awaited()                            # a pause is not the end
    r = await svc.resume(1)
    assert r["ok"]
    await finish()
    st = await svc.get_status()
    assert (st["state"], st["total"], st["patched"]) == ("done", 10, 10)
    assert len(w.http.tag_patches()) == 10
    w.alerts.assert_awaited_once()


async def test_resume_after_a_restart_skips_what_is_done(w):
    await svc.run_foreground(1, limit=4)
    assert w.store.state["state"] == "paused" and w.store.state["patched"] == 4
    w.store.state["state"] = "running"                       # the process died mid-run
    svc.reset_for_tests()
    assert (await svc.get_status())["state"] == "interrupted"
    r = await svc.resume(1)
    assert r["ok"]
    await finish()
    st = await svc.get_status()
    assert (st["state"], st["total"], st["done"], st["patched"]) == ("done", 10, 10, 10)
    assert len(w.http.tag_patches()) == 10                   # nobody patched twice
    assert tags_now(w.http) == EXPECTED


async def test_stop_an_interrupted_run_closes_it_with_one_alert(w):
    await svc.run_foreground(1, limit=2)
    w.store.state["state"] = "running"
    svc.reset_for_tests()
    r = await svc.stop(1)
    assert r["ok"] and r["status"]["state"] == "stopped" and r["status"]["finished_at"]
    w.alerts.assert_awaited_once()
    assert (await svc.stop(1))["error"] == "not_running"
    assert (await svc.resume(1))["error"] == "not_resumable"


async def test_panel_down_fails_the_run_with_an_alert(w):
    w.http.down = True
    await svc.start(1)
    await finish()
    st = await svc.get_status()
    assert st["state"] == "failed" and "panel_unavailable" in st["last_error"]
    w.alerts.assert_awaited_once()


# ── CLI ────────────────────────────────────────────────────────────────

@pytest.fixture
def script(monkeypatch):
    import scripts.backfill_remnawave_tags as mod
    monkeypatch.setattr(database.core, "init_db", AsyncMock(return_value=True))
    return mod


async def test_script_dry_run_on_the_fake_panel(w, script, capsys):
    assert await script._main(apply=False, limit=None, force=False) == 0
    out = capsys.readouterr().out
    assert "to change: 10" in out and "COMBO_BASIC" in out and "dry run" in out
    assert w.http.writes() == []


async def test_script_apply_is_paced_and_resumable(w, script, capsys):
    assert await script._main(apply=True, limit=3, force=False) == 0
    assert len(w.http.tag_patches()) == 3
    assert await script._main(apply=True, limit=None, force=True) == 0     # paused → continue
    assert len(w.http.tag_patches()) == 10 and tags_now(w.http) == EXPECTED


async def test_script_refuses_while_the_bot_runs_it(w, script, capsys):
    w.store.state = {"state": "running", "total": 10, "done": 3, "patched": 3, "errors": 0}
    assert await script._main(apply=True, limit=None, force=False) == 3
    assert w.http.tag_patches() == []
