"""Shutdown (SIGTERM / Railway redeploy): Telegram successful_payment updates run as
shielded tasks (telegram_webhook._payment_tasks, HOW_IT_WORKS P1-6). Telegram never
resends successful_payment, so the process must not exit while one is finalizing:
telegram_webhook.drain_payment_tasks waits up to 20 s (bounded), run by the app's
shutdown hook (uvicorn runs it on SIGTERM) and by main()'s finally; anything still
running afterwards is logged CRITICAL and alerted to the admin. Never raises.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

import app.api as api_pkg
from app.api import telegram_webhook as tw
from app.services import provisioning

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean():
    tw._payment_tasks.clear()
    yield
    for task in list(tw._payment_tasks):
        task.cancel()
    tw._payment_tasks.clear()


@pytest.fixture()
def alerts(monkeypatch):
    sent = []

    async def report(budget, text, **kw):
        sent.append({"budget": budget, "text": text, **kw})
        return True
    monkeypatch.setattr(provisioning, "report_payment_alert", report)
    return sent


def _payment_task(coro):
    task = asyncio.ensure_future(coro)
    tw._payment_tasks.add(task)
    task.add_done_callback(tw._payment_tasks.discard)
    return task


async def test_waits_for_a_payment_task_that_finishes_in_time(alerts):
    finished = asyncio.Event()

    async def finalize():
        await asyncio.sleep(0.05)
        finished.set()
    task = _payment_task(finalize())

    left = await tw.drain_payment_tasks(timeout=2)

    assert left == 0 and finished.is_set() and not task.cancelled()
    assert not alerts


async def test_nothing_in_flight_returns_at_once(alerts):
    assert await tw.drain_payment_tasks(timeout=0.01) == 0
    assert not alerts


async def test_still_running_after_the_timeout_is_logged_and_alerted(alerts, caplog):
    task = _payment_task(asyncio.sleep(10))

    with caplog.at_level("CRITICAL"):
        left = await tw.drain_payment_tasks(timeout=0.05)

    assert left == 1
    assert not task.done(), "the drain must not cancel a finalization itself"
    assert "SHUTDOWN_PAYMENT_TASKS_UNFINISHED" in caplog.text
    assert len(alerts) == 1 and "successful_payment" in alerts[0]["text"]


def test_the_default_wait_is_bounded_to_20_seconds():
    assert tw.PAYMENT_DRAIN_TIMEOUT_S == 20.0


async def test_the_app_shutdown_hook_drains_before_the_alert_flush(alerts):
    names = [getattr(h, "__name__", "") for h in api_pkg.app.router.on_shutdown]
    assert "drain_payment_tasks_on_shutdown" in names
    assert names.index("drain_payment_tasks_on_shutdown") < names.index("flush_alerts_on_shutdown")

    finished = asyncio.Event()

    async def finalize():
        await asyncio.sleep(0.05)
        finished.set()
    _payment_task(finalize())
    await api_pkg.drain_payment_tasks_on_shutdown()
    assert finished.is_set()


def test_main_drains_before_cancelling_tasks_and_closing_the_pool():
    src = (ROOT / "main.py").read_text()
    at = src.index("drain_payment_tasks(")
    assert at < src.index("# Step 1: Cancel all tasks")
    assert at < src.index("await database.close_pool()")
