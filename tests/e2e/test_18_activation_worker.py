"""Scenario 18 — activation worker (activation_worker.py) on a real DB.

A paid subscription whose panel provisioning was deferred sits in
subscriptions.activation_status='pending' (legacy: the panel was disabled at
payment time). The always-running activation worker must finish it: create
the panel entities once, flip the row to active, tell the user once; leave
users with an open outbox job to the provisioning worker (plan §B.6 T7 — a
second provisioning would add GB twice); mark an already expired pending row
failed; keep retrying while the panel is down. Coverage 09: activation_worker
10 %, activation service _attempt_activation_no_conn_hold 59/60 lines unrun.
"""
from __future__ import annotations

from datetime import timedelta

import activation_worker
import config
import database
from tests.e2e.world import GIB, naive, new_user, utcnow


async def _pending(e2e, u, *, tariff="basic", days=30):
    """The legacy deferred issuance: grant_access while the panel API is off."""
    await e2e.register(u)
    e2e.mp.setattr(config, "VPN_ENABLED", False)
    try:
        await database.grant_access(telegram_id=u.id, duration=timedelta(days=days),
                                    source="payment", tariff=tariff)
    finally:
        e2e.mp.setattr(config, "VPN_ENABLED", True)
    sub = await e2e.sub(u.id)
    assert sub["activation_status"] == "pending" and sub["uuid"] is None
    assert e2e.panel.premium(u.id) is None
    return sub


async def _tick(e2e):
    await e2e.run_worker_iterations(activation_worker, activation_worker.activation_worker_task, iterations=1)


def _activated_msgs(e2e, tg):
    return [t for t in e2e.user_texts(tg) if t and "активирована" in t]


async def test_pending_activation_is_completed_once(e2e):
    u = new_user()
    await _pending(e2e, u)
    await _tick(e2e)
    sub = await e2e.sub(u.id)
    assert sub["activation_status"] == "active"
    assert sub["uuid"] and sub["vpn_key"]
    assert abs((e2e.panel.premium_expire(u.id) - sub["expires_at"]).total_seconds()) <= 1.5
    assert e2e.panel.bypass_limit(u.id) == 10 * GIB
    assert len(_activated_msgs(e2e, u.id)) == 1

    writes = len(e2e.panel.writes())
    await _tick(e2e)                                   # nothing left to do
    assert len(e2e.panel.writes()) == writes
    assert len(_activated_msgs(e2e, u.id)) == 1
    assert (await e2e.sub(u.id))["uuid"] == sub["uuid"]


async def test_user_with_an_open_outbox_job_is_left_to_the_provisioning_worker(e2e):
    u = new_user()
    await _pending(e2e, u)
    await e2e.pool.execute(
        "INSERT INTO provisioning_jobs (idempotency_key, telegram_id, source, tariff_key, status, next_attempt_at) "
        "VALUES ($1, $2, 'webhook', 'basic_30', 'pending', $3)",
        f"e2e:{u.id}", u.id, naive(utcnow() + timedelta(hours=1)))
    await _tick(e2e)
    assert (await e2e.sub(u.id))["activation_status"] == "pending"
    assert e2e.panel.writes() == []


async def test_expired_pending_row_is_marked_failed_without_touching_the_panel(e2e):
    u = new_user()
    await _pending(e2e, u)
    await e2e.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1",
                           u.id, naive(utcnow() - timedelta(hours=1)))
    await _tick(e2e)
    sub = await e2e.sub(u.id)
    assert sub["activation_status"] == "failed"
    assert e2e.panel.writes() == []
    assert _activated_msgs(e2e, u.id) == []


async def test_panel_down_keeps_it_pending_and_the_next_tick_activates(e2e):
    u = new_user()
    await _pending(e2e, u)
    e2e.panel.down = True
    await _tick(e2e)
    sub = await e2e.sub(u.id)
    assert sub["activation_status"] == "pending"
    assert sub["activation_attempts"] == 1 and sub["last_activation_error"]
    assert _activated_msgs(e2e, u.id) == []

    e2e.panel.clear_failures()
    await _tick(e2e)
    sub = await e2e.sub(u.id)
    assert sub["activation_status"] == "active"
    assert abs((e2e.panel.premium_expire(u.id) - sub["expires_at"]).total_seconds()) <= 1.5
    assert len(_activated_msgs(e2e, u.id)) == 1
