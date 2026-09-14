"""Scenario 11 — startup: init_db on an empty database (the session boot of
this suite DROPs the schema and runs it), the DB worker set is started once
(main.start_db_services, merged fix bdd0c937) and the DB-outage recovery path
starts the SAME set, once, advisory lock first.
"""
import asyncio
import re
from pathlib import Path

import pytest

import database
import main

REPO = Path(__file__).resolve().parents[2]
EXPECTED_WORKERS = {
    "reminders", "trial_notifications", "farm_notifications", "traffic_monitor",
    "fast_expiry_cleanup", "auto_renewal", "activation_worker", "wata_reconciler",
    "wata_key_warmup", "provisioning_worker", "sales_funnel",
}


async def test_init_db_on_empty_database_applied_every_migration(e2e):
    versions = {m.group(1) for p in (REPO / "migrations").glob("*.sql")
                if (m := re.match(r"^(\d+)_", p.name))}
    recorded = {r["version"] for r in await e2e.rows("SELECT version FROM schema_migrations")}
    assert versions <= recorded, sorted(versions - recorded)
    for t in ("users", "subscriptions", "pending_purchases", "payments", "balance_transactions",
              "provisioning_jobs", "referral_rewards", "gift_subscriptions", "admin_credentials"):
        assert await e2e.val("SELECT to_regclass($1)", f"public.{t}"), t
    # a second boot on the migrated DB is a no-op (every redeploy)
    database.DB_READY = False
    try:
        assert await database.init_db() is True
    finally:
        new_pool = database.core._pool
        if new_pool is not None and new_pool is not e2e.pool:
            await new_pool.close()
        database.core._pool = e2e.pool
        database.DB_READY = True


async def test_get_pool_creates_a_usable_pool_lazily(e2e):
    """E2E-POOL: with no pool yet (closed / never created by init_db),
    get_pool() must return a connected pool, not an un-awaited one."""
    core = database.core
    saved = core._pool
    core._pool = None
    try:
        pool = await database.get_pool()
        assert pool is not saved
        assert await pool.fetchval("SELECT 1") == 1
        await pool.close()
    finally:
        core._pool = saved


@pytest.fixture
def stub_workers(monkeypatch):
    """Every worker coroutine main starts → a recorder that parks forever."""
    calls = []
    gate = asyncio.Event()

    def stub(name):
        async def _run(*_a, **_k):
            calls.append(name)
            await gate.wait()
        return _run

    targets = [
        (main.reminders, "reminders_task", "reminders"),
        (main.trial_notifications, "run_trial_scheduler", "trial_notifications"),
        (main.farm_notifications, "farm_notifications_task", "farm_notifications"),
        (main.traffic_monitor, "traffic_monitor_task", "traffic_monitor"),
        (main.fast_expiry_cleanup, "fast_expiry_cleanup_task", "fast_expiry_cleanup"),
        (main.auto_renewal, "auto_renewal_task", "auto_renewal"),
        (main.activation_worker, "activation_worker_task", "activation_worker"),
        (main.provisioning_worker, "provisioning_worker_task", "provisioning_worker"),
        (main.sales_funnel, "sales_funnel_task", "sales_funnel"),
    ]
    for mod, attr, name in targets:
        monkeypatch.setattr(mod, attr, stub(name))
    import wata_service
    from app.workers import wata_reconciler
    monkeypatch.setattr(wata_reconciler, "wata_reconciler_task", stub("wata_reconciler"))
    monkeypatch.setattr(wata_service, "warmup_public_key", stub("wata_key_warmup"))
    return calls


async def _release_lock():
    conn = main.instance_lock_conn
    if conn is not None:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", main.ADVISORY_LOCK_KEY)
        finally:
            await database.core._pool.release(conn)
            main.instance_lock_conn = None


async def _cancel(tasks):
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_start_db_services_starts_each_worker_once_lock_first(e2e, stub_workers):
    tasks, started = [], {}
    try:
        await main.start_db_services(e2e.bot, tasks, started)
        await asyncio.sleep(0)
        assert main.instance_lock_conn is not None, "advisory lock not taken"
        held = await e2e.val("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=$1",
                             main.ADVISORY_LOCK_KEY % (2 ** 32))
        assert held == 1
        assert set(started) == EXPECTED_WORKERS, set(started) ^ EXPECTED_WORKERS
        # a second call (recovery racing the normal start) starts nothing new
        await main.start_db_services(e2e.bot, tasks, started)
        await asyncio.sleep(0)
        assert sorted(stub_workers) == sorted(EXPECTED_WORKERS), stub_workers
        assert len(tasks) == len(EXPECTED_WORKERS)
    finally:
        await _cancel(tasks)
        await _release_lock()


async def test_main_boot_sequence_starts_everything_once_and_shuts_down_cleanly(e2e, stub_workers, monkeypatch):
    """The real main.main() on the migrated DB: DB workers once (lock first),
    the always-on tasks once, bot commands, set_webhook(drop_pending_updates=False
    — merged fix ae7cec1f) + verification, uvicorn; cancel → delete_webhook,
    lock released."""
    import uvicorn

    import config
    from app.handlers import router as root_router
    from app.services import admin_notifier, scheduled_broadcasts_worker

    monkeypatch.setattr(asyncio, "sleep", e2e._real_sleep)      # real loops, real timing
    monkeypatch.setattr(main, "Bot", lambda *a, **k: e2e.bot)
    always = []
    park = asyncio.Event()

    def recorder(name):
        async def _run(*_a, **_k):
            always.append(name)
            await park.wait()
        return _run

    monkeypatch.setattr(main.healthcheck, "health_check_task", recorder("healthcheck"))
    monkeypatch.setattr(admin_notifier, "run_admin_notifier", recorder("admin_notifier"))
    monkeypatch.setattr(scheduled_broadcasts_worker, "run_scheduled_broadcasts_worker",
                        recorder("scheduled_broadcasts"))
    serving = asyncio.Event()

    async def fake_serve(self, *a, **k):
        serving.set()
        await park.wait()
    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)

    e2e_parent = root_router.parent_router
    root_router._parent_router = None               # main builds its own Dispatcher
    e2e.tg.clear()
    task = asyncio.ensure_future(main.main())
    try:
        await asyncio.wait_for(serving.wait(), timeout=15)
        await asyncio.sleep(0)
        assert sorted(stub_workers) == sorted(EXPECTED_WORKERS), stub_workers
        assert sorted(always) == ["admin_notifier", "healthcheck", "scheduled_broadcasts"], always
        assert main.instance_lock_conn is not None, "advisory lock not held while running"
        names = [type(m).__name__ for m in e2e.tg.calls]
        # the menu once per language: default (RU) + language_code="en"
        menu_langs = sorted(str(m.language_code) for m in e2e.tg.calls if type(m).__name__ == "SetMyCommands")
        assert menu_langs == ["None", "en"] and names.count("SetWebhook") == 1, names
        hook = next(m for m in e2e.tg.calls if type(m).__name__ == "SetWebhook")
        assert hook.url == config.WEBHOOK_URL and hook.secret_token == config.WEBHOOK_SECRET
        assert hook.drop_pending_updates is False, "updates queued during a restart would be dropped"
        assert "GetWebhookInfo" in names
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        root_router._parent_router = e2e_parent
    names = [type(m).__name__ for m in e2e.tg.calls]
    assert "DeleteWebhook" in names
    assert main.instance_lock_conn is None, "advisory lock not released on shutdown"


async def test_db_outage_recovery_starts_the_same_worker_set_once(e2e, stub_workers, monkeypatch):
    """Boot with the DB down → retry_db_init → DB back → the same set as a
    normal start, once, and the admin is told about the recovery."""
    database.DB_READY = False

    async def init_db_recovers():
        database.DB_READY = True
        return True
    monkeypatch.setattr(database, "init_db", init_db_recovers)
    tasks, started = [], {}
    mark = e2e.tg.mark()
    try:
        await asyncio.wait_for(main.retry_db_init(e2e.bot, tasks, started, retry_interval=0), timeout=10)
        await asyncio.sleep(0)
        assert set(started) == EXPECTED_WORKERS, set(started) ^ EXPECTED_WORKERS
        assert sorted(stub_workers) == sorted(EXPECTED_WORKERS)
        assert main.instance_lock_conn is not None
        assert e2e.admin_texts(mark), "admin not told about the recovery"
    finally:
        database.DB_READY = True
        await _cancel(tasks)
        await _release_lock()
