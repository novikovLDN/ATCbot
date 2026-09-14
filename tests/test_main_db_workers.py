"""N5 (docs/audit/06_bug_hunt.md §3): startup with the DB down → recovery.

The recovery task (retry_db_init) had its own copy of the worker list: it started
5 of the 9 DB workers (no trial notifications, farm, traffic monitor, WATA
reconciler) and never took the single-instance advisory lock. Now both paths call
ONE function, start_db_services: advisory lock FIRST, then every DB worker, each
at most once.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.conftest  # noqa: F401  (env before config)
import config
import database
import main

MAIN_SRC = Path(main.__file__).read_text(encoding="utf-8")

WORKER_ENTRYPOINTS = {
    "reminders_task", "run_trial_scheduler", "farm_notifications_task", "traffic_monitor_task",
    "fast_expiry_cleanup_task", "auto_renewal_task", "activation_worker_task",
    "wata_reconciler_task", "provisioning_worker_task",
}
EXPECTED = {
    "reminders", "trial_notifications", "farm_notifications", "traffic_monitor",
    "fast_expiry_cleanup", "auto_renewal", "activation_worker", "wata_reconciler",
    "wata_key_warmup", "provisioning_worker",
}


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"main.py has no function {name}()")


def _calls(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
    return out


def test_both_startup_paths_use_the_same_starter():
    tree = ast.parse(MAIN_SRC)
    starter = _func(tree, "start_db_services")
    assert "start_db_services" in _calls(_func(tree, "main")), "normal DB-ready start"
    assert "start_db_services" in _calls(_func(tree, "retry_db_init")), "recovery after DB outage"
    # no worker is started anywhere else in main.py
    starter_lines = set(range(starter.lineno, starter.end_lineno + 1))
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr in WORKER_ENTRYPOINTS:
            assert n.lineno in starter_lines, f"{n.attr} referenced outside start_db_services (line {n.lineno})"
        if isinstance(n, ast.Name) and n.id in WORKER_ENTRYPOINTS:
            assert n.lineno in starter_lines, f"{n.id} referenced outside start_db_services (line {n.lineno})"
    assert "pg_advisory_lock(" in ast.get_source_segment(MAIN_SRC, _func(tree, "acquire_instance_lock"))


class _LockConn:
    def __init__(self, events, fail=None):
        self.events = events
        self.fail = fail

    async def execute(self, sql, *args):
        await asyncio.sleep(0)
        if "pg_advisory_lock" in sql:
            if self.fail:
                raise self.fail
            self.events.append("lock")


class _Pool:
    def __init__(self, conn):
        self.conn = conn
        self.released = []

    async def acquire(self):
        return self.conn

    async def release(self, conn):
        self.released.append(conn)


@pytest.fixture
def fakes(monkeypatch):
    events: list = []

    def stub(name):
        async def _run(*_a, **_k):
            events.append(name)
        return _run

    import activation_worker
    import auto_renewal
    import fast_expiry_cleanup
    import reminders
    import trial_notifications
    import wata_service
    from app.workers import farm_notifications, provisioning_worker, traffic_monitor, wata_reconciler
    monkeypatch.setattr(reminders, "reminders_task", stub("reminders"))
    monkeypatch.setattr(trial_notifications, "run_trial_scheduler", stub("trial_notifications"))
    monkeypatch.setattr(farm_notifications, "farm_notifications_task", stub("farm_notifications"))
    monkeypatch.setattr(traffic_monitor, "traffic_monitor_task", stub("traffic_monitor"))
    monkeypatch.setattr(fast_expiry_cleanup, "fast_expiry_cleanup_task", stub("fast_expiry_cleanup"))
    monkeypatch.setattr(auto_renewal, "auto_renewal_task", stub("auto_renewal"))
    monkeypatch.setattr(activation_worker, "activation_worker_task", stub("activation_worker"))
    monkeypatch.setattr(wata_reconciler, "wata_reconciler_task", stub("wata_reconciler"))
    monkeypatch.setattr(wata_service, "warmup_public_key", stub("wata_key_warmup"))
    monkeypatch.setattr(wata_service, "is_enabled", lambda: True)
    monkeypatch.setattr(provisioning_worker, "provisioning_worker_task", stub("provisioning_worker"))
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(main, "get_feature_flags", lambda: SimpleNamespace(
        background_workers_enabled=True, auto_renewal_enabled=True))
    monkeypatch.setattr(main, "instance_lock_conn", None)
    pool = _Pool(_LockConn(events))
    monkeypatch.setattr(database, "get_pool", AsyncMock(return_value=pool))
    return SimpleNamespace(events=events, pool=pool)


async def _settle(tasks):
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_recovery_takes_the_lock_first_and_starts_every_worker_once(fakes, monkeypatch):
    monkeypatch.setattr(database, "DB_READY", False)

    async def init_db():
        database.DB_READY = True
        return True
    monkeypatch.setattr(database, "init_db", init_db)
    monkeypatch.setattr(main.admin_notifications, "notify_admin_recovered", AsyncMock())
    tasks: list = []
    started: dict = {}

    await main.retry_db_init(MagicMock(), tasks, started, retry_interval=0)
    await _settle(tasks)

    assert fakes.events[0] == "lock", f"advisory lock must come first: {fakes.events}"
    workers = fakes.events[1:]
    assert sorted(workers) == sorted(EXPECTED), "the recovery path must start the full set"
    assert len(workers) == len(set(workers)), "each worker exactly once"

    await main.start_db_services(MagicMock(), tasks, started)   # a second call starts nothing new
    await _settle(tasks)
    assert sorted(fakes.events[1:]) == sorted(EXPECTED)


async def test_normal_start_and_recovery_start_the_same_set(fakes, monkeypatch):
    monkeypatch.setattr(database, "DB_READY", True)
    tasks: list = []

    await main.start_db_services(MagicMock(), tasks, {})
    await _settle(tasks)

    assert fakes.events[0] == "lock" and sorted(fakes.events[1:]) == sorted(EXPECTED)


async def test_lock_held_by_another_instance_in_prod_exits_before_any_worker(fakes, monkeypatch):
    fakes.pool.conn.fail = asyncio.TimeoutError("lock_timeout")
    monkeypatch.setattr(config, "IS_PROD", True)
    tasks: list = []

    with pytest.raises(SystemExit):
        await main.start_db_services(MagicMock(), tasks, {})
    await _settle(tasks)

    assert fakes.events == [] and tasks == []
