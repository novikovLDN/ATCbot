"""Premium expireAt > 5 years — the dashboard job (app/services/premium_repair_job)
on the 3.4.3 HTTP panel fake with a fake clock.

Check = build_plan + summarize in the background (writes nothing but the report);
apply = the same apply_plan as the CLI, ≤ 2 PATCH/s, pause / resume / stop,
progress in app_settings, "interrupted" after a restart, one job at a time,
one admin alert per finished run. Also: the CLI refuses while a dashboard run
is not finished (unless --force).
"""
from __future__ import annotations

import asyncio
import csv
import io
from unittest.mock import AsyncMock

import pytest

import database.core
from app.services import premium_repair, purchase_flow, remnawave_api
from app.services import premium_repair_job as job
from tests.fakes.premium_repair_world import FakePool, MemKV, install_world, patches


@pytest.fixture
def w(monkeypatch):
    world = install_world(monkeypatch)
    world.store = MemKV()
    monkeypatch.setattr(job, "_store", world.store)
    monkeypatch.setattr(purchase_flow, "_alert_bot", lambda: object())
    job.reset_for_tests()
    yield world
    if job._task is not None and not job._task.done():
        job._task.cancel()
    job.reset_for_tests()


async def finish():
    await job._task


def gate_sleep(monkeypatch) -> asyncio.Event:
    """Every pacing sleep waits for the gate (the job is "in the middle")."""
    gate = asyncio.Event()

    async def gated(_seconds):
        await gate.wait()

    monkeypatch.setattr(premium_repair, "_sleep", gated)
    return gate


async def until_patches(http, n: int) -> None:
    for _ in range(500):
        if len(patches(http)) >= n:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"no {n} PATCHes")


# ── check (dry run) ───────────────────────────────────────────────────

async def test_check_runs_in_the_background_and_writes_nothing(w):
    r = await job.start_check(1)
    assert r["ok"] and r["status"]["running_kind"] == "check"
    await finish()
    st = await job.get_status()
    c = st["check"]
    assert (c["state"], c["would_fix"], c["eta_seconds"], c["started_by"]) == ("done", 4, 2, 1)
    s = c["summary"]
    assert (s["panel_entities"], s["premium_entities"], s["candidates"]) == (11, 5, 4)
    assert s["plus_one_day"] == 2 and s["db_leaked"] == 1
    assert s["target_source"] == {"purchases": 1, "db": 1, "fallback": 2}
    assert st["report"]["kind"] == "check" and st["report"]["rows"] == 4
    assert st["apply"]["state"] == "idle" and st["running"] is False
    assert w.http.writes() == [] and w.db.records == [] and w.alerts.await_count == 0


async def test_report_pages_and_csv_have_the_cli_columns(w):
    await job.start_check(1)
    await finish()
    page = await job.get_report(offset=1, limit=2)
    assert (page["kind"], page["total"], page["offset"], len(page["rows"])) == ("check", 4, 1, 2)
    row = page["rows"][0]
    assert isinstance(row["target"], str) and row["action"] == "would_fix"
    rep = await job.report_csv()
    rows = list(csv.DictReader(io.StringIO(rep["text"])))
    assert list(rows[0]) == premium_repair.CSV_COLUMNS and len(rows) == rep["rows"] == 4
    assert {r["fallback"] for r in rows} == {"", "no_payments", "past_date"}


async def test_no_report_yet(w):
    assert await job.report_csv() is None
    assert (await job.get_report())["rows"] == []


async def test_check_panel_down_fails_without_an_alert(w, monkeypatch):
    monkeypatch.setattr(remnawave_api, "get_all_users", AsyncMock(return_value=None))
    await job.start_check(1)
    await finish()
    c = (await job.get_status())["check"]
    assert (c["state"], c["last_error"]) == ("failed", "panel_unavailable")
    w.alerts.assert_not_awaited()


# ── apply ─────────────────────────────────────────────────────────────

async def test_apply_fixes_every_candidate_with_one_alert(w):
    await job.start_check(1)
    await finish()
    r = await job.start_apply(1)
    assert r["ok"] and r["status"]["running_kind"] == "apply" and r["status"]["check"]["stale"] is True
    await finish()
    st = await job.get_status()
    a = st["apply"]
    assert (a["state"], a["total"], a["done"], a["fixed"], a["errors"], a["skipped"]) == ("done", 4, 4, 4, 0, 0)
    assert (a["plus_one_day"], a["db_shortened"], a["candidates"], a["remaining"]) == (2, 1, 4, 0)
    assert a["finished_at"] and a["summary"]["actions"]["fixed"] == 4
    bodies = [p[2] for p in patches(w.http)]
    assert len(bodies) == 4 and all(set(b) == {"id", "expireAt"} for b in bodies)
    assert st["report"]["kind"] == "apply"
    assert {x["action"] for x in (await job.get_report())["rows"]} == {"fixed"}
    w.alerts.assert_awaited_once()
    text = w.alerts.await_args.args[2]
    assert "(дашборд) завершена" in text and "исправлено: 4" in text and "на +1 день: 2" in text
    assert w.alerts.await_args.kwargs["force"] is True


async def test_apply_is_paced_at_most_two_patches_per_second(w):
    await job.start_apply(1)
    await finish()
    gaps = [b - a for a, b in zip(w.patch_times, w.patch_times[1:])]
    assert len(w.patch_times) == 4 and min(gaps) >= 0.5 - 1e-9


async def test_a_failing_user_is_counted_and_the_run_goes_on(w):
    bad = w.http.premium(302)["id"]
    w.http.fail("PATCH", lambda req, body: body.get("id") == bad)
    await job.start_apply(1)
    await finish()
    a = (await job.get_status())["apply"]
    assert (a["state"], a["fixed"], a["errors"], a["done"]) == ("done", 3, 1, 4)
    assert a["last_error"] == "tg:302: panel_patch_rejected"
    assert w.http.premium_expire(302) == w.far
    w.alerts.assert_awaited_once()
    assert "ошибок: 1" in w.alerts.await_args.args[2]


async def test_one_job_at_a_time(w, monkeypatch):
    gate = gate_sleep(monkeypatch)
    await job.start_apply(1)
    await until_patches(w.http, 1)
    for again in (await job.start_apply(1), await job.start_check(1), await job.resume(1)):
        assert (again["ok"], again["error"]) == (False, "already_running")
    gate.set()
    await finish()
    assert len(patches(w.http)) == 4


async def test_trial_limit_pauses_and_resume_finishes(w):
    await job.start_apply(1, limit=2)
    await finish()
    a = (await job.get_status())["apply"]
    assert (a["state"], a["fixed"], a["total"], a["remaining"], a["limit"]) == ("paused", 2, 2, 2, 2)
    w.alerts.assert_not_awaited()                      # a pause is not the end
    assert (await job.resume(1))["ok"]
    await finish()
    a = (await job.get_status())["apply"]
    assert (a["state"], a["fixed"], a["total"], a["done"]) == ("done", 4, 4, 4)
    assert len(patches(w.http)) == 4                   # nobody patched twice
    w.alerts.assert_awaited_once()


async def test_pause_then_resume_keeps_counters(w, monkeypatch):
    gate = gate_sleep(monkeypatch)
    await job.start_apply(1)
    await until_patches(w.http, 1)
    pausing = asyncio.create_task(job.pause())
    await asyncio.sleep(0)
    gate.set()
    r = await pausing
    assert r["ok"] and r["status"]["apply"]["state"] == "paused"
    assert (r["status"]["apply"]["fixed"], r["status"]["apply"]["remaining"]) == (1, 3)
    w.alerts.assert_not_awaited()
    assert (await job.pause())["error"] == "not_running"
    assert (await job.resume(1))["ok"]
    await finish()
    a = (await job.get_status())["apply"]
    assert (a["state"], a["total"], a["fixed"]) == ("done", 4, 4)
    assert len(patches(w.http)) == 4
    w.alerts.assert_awaited_once()


async def test_after_a_restart_it_is_interrupted_and_resume_rescans(w):
    await job.start_apply(1, limit=1)
    await finish()
    w.store.data[job.STATE_KEY]["apply"]["state"] = "running"     # the process died mid-run
    job.reset_for_tests()
    st = await job.get_status()
    assert st["apply"]["state"] == "interrupted" and st["running"] is False
    assert (await job.resume(1))["ok"]
    await finish()
    a = (await job.get_status())["apply"]
    assert (a["state"], a["fixed"], a["total"]) == ("done", 4, 4)
    assert len(patches(w.http)) == 4                   # the fixed one dropped out of the rescan
    assert (await job.get_report())["total"] == 3
    w.alerts.assert_awaited_once()


async def test_stop_a_running_job_sends_one_alert(w, monkeypatch):
    gate = gate_sleep(monkeypatch)
    await job.start_apply(1)
    await until_patches(w.http, 1)
    stopping = asyncio.create_task(job.stop(1))
    await asyncio.sleep(0)
    gate.set()
    r = await stopping
    a = r["status"]["apply"]
    assert r["ok"] and (a["state"], a["fixed"], a["remaining"]) == ("stopped", 1, 3) and a["finished_at"]
    assert len(patches(w.http)) == 1
    w.alerts.assert_awaited_once()
    text = w.alerts.await_args.args[2]
    assert "остановлена" in text and "не обработано: 3" in text


async def test_stop_an_interrupted_run_closes_it(w):
    await job.start_apply(1, limit=1)
    await finish()
    w.store.data[job.STATE_KEY]["apply"]["state"] = "running"
    job.reset_for_tests()
    r = await job.stop(1)
    assert r["ok"] and r["status"]["apply"]["state"] == "stopped"
    w.alerts.assert_awaited_once()
    assert (await job.stop(1))["error"] == "not_running"
    assert (await job.resume(1))["error"] == "not_resumable"


async def test_apply_panel_down_fails_with_an_alert(w, monkeypatch):
    monkeypatch.setattr(remnawave_api, "get_all_users", AsyncMock(return_value=None))
    await job.start_apply(1)
    await finish()
    a = (await job.get_status())["apply"]
    assert (a["state"], a["last_error"]) == ("failed", "panel_unavailable")
    w.alerts.assert_awaited_once()
    assert "прервана ошибкой" in w.alerts.await_args.args[2]


# ── CLI vs the dashboard job ──────────────────────────────────────────

@pytest.fixture
def script(monkeypatch):
    import scripts.fix_premium_over_issuance as mod
    monkeypatch.setattr(database.core, "get_pool", AsyncMock(return_value=FakePool()))
    monkeypatch.setattr(database.core, "init_db", AsyncMock(side_effect=AssertionError("init_db called")))
    monkeypatch.setattr(mod, "_make_bot", lambda: object())
    return mod


@pytest.mark.parametrize("state", ["running", "paused"])
async def test_cli_apply_refuses_while_a_dashboard_run_is_unfinished(w, script, tmp_path, state):
    w.store.data[job.STATE_KEY] = {"apply": {"state": state, "done": 1, "total": 4}}
    out = str(tmp_path / "r.csv")
    assert await script._main(apply=True, yes=True, limit=None, out=out) == 3
    assert w.http.writes() == []
    assert await script._main(apply=False, yes=False, limit=None, out=out) == 0     # dry run is fine
    assert await script._main(apply=True, yes=True, limit=None, out=out, force=True) == 0
    assert len(patches(w.http)) == 4
