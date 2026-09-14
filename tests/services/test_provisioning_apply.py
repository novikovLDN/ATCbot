"""T4 — app.services.provisioning: enqueue / run_now / apply against FakePanel.

The outbox (database.provisioning_jobs) is replaced by the in-memory FakeJobs
from tests/fakes/provisioning.py (same semantics as the SQL: idempotent insert,
per-user FIFO claim with lease, one-shot bypass plan, status transitions); the
subscriptions cache helpers and payment_errors logger by FakeDB. No Postgres, no HTTP.
Numbers: docs/audit/02_payment_core_plan.md task T4.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import database
import database.provisioning_jobs as pj
from app.api import payment_webhook
from app.services import admin_alerts, provisioning, remnawave_api, remnawave_bypass, sub_aggregator
from app.services.provisioning import ProvisioningPermanent, ProvisioningTransient
from app.services.remnawave_bypass import BypassCreateResult
from app.services.tariffs import for_grant, for_pack, for_purchase
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeConn, FakeDB, FakeJobs

GIB = 1024 ** 3
TG = 4242
BOT = object()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
UNTIL = NOW + timedelta(days=30)
LEGACY_UUID = "11111111-2222-4333-8444-555555555555"

BASIC = for_purchase("basic", 30)
COMBO = for_purchase("combo_basic", 30)
GRANT = for_grant("basic", 30)
RETRY_SQL = (
    "UPDATE provisioning_jobs SET status='pending', "
    "next_attempt_at=now() AT TIME ZONE 'UTC' WHERE id={}"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── fixtures / helpers ─────────────────────────────────────────────────

@pytest.fixture
def panel(monkeypatch):
    return FakePanel().install(monkeypatch)


@pytest.fixture
def jobs(monkeypatch, panel):
    return FakeJobs(panel).install(monkeypatch)


@pytest.fixture
def db(monkeypatch):
    return FakeDB().install(monkeypatch)


@pytest.fixture
def alerts(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", mock)
    return mock


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda tg: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)


async def enqueue(key, ent, until=UNTIL, tg=TG):
    return await provisioning.enqueue(
        FakeConn(), key=key, telegram_id=tg, ent=ent, premium_until=until, source="test",
    )


def forces(alerts):
    return [c.kwargs.get("force") for c in alerts.await_args_list]


def bypass_patches(panel):
    return [c for c in panel.calls if c[0] == "update_user" and "trafficLimitBytes" in c[2]]


def premium_patches(panel):
    return [c for c in panel.calls if c[0] == "update_user" and "expireAt" in c[2]]


def names(panel):
    return [c[0] for c in panel.calls]


def test_catalog_numbers_used_below():
    assert BASIC.bypass_bytes == 10 * GIB and BASIC.premium_days == 30
    assert COMBO.bypass_bytes == 75 * GIB
    assert GRANT.bypass_bytes == 0


def test_backoff_is_capped_exponential():
    assert provisioning.backoff_seconds(1) == 60
    assert provisioning.backoff_seconds(2) == 120
    assert provisioning.backoff_seconds(6) == 1920
    assert provisioning.backoff_seconds(7) == 3600
    assert provisioning.backoff_seconds(10_000) == 3600


# ── enqueue ────────────────────────────────────────────────────────────

async def test_enqueue_writes_job_in_callers_tx_without_http(panel, jobs, db):
    conn = FakeConn()
    job_id = await provisioning.enqueue(
        conn, key="purchase:1", telegram_id=TG, ent=BASIC, premium_until=UNTIL,
        source="webhook", context={"purchase_id": "1"},
    )
    assert conn.calls == [("insert_job", "purchase:1", True)]
    row = jobs.job(job_id)
    assert row["status"] == "pending"
    assert row["bypass_add_bytes"] == 10 * GIB and row["premium_until"] == UNTIL
    assert row["context"] == {"purchase_id": "1", "premium_days": 30, "premium_tier": "basic"}
    assert panel.calls == []


async def test_enqueue_refuses_outside_a_transaction(panel, jobs, db):
    with pytest.raises(RuntimeError):
        await provisioning.enqueue(
            FakeConn(in_tx=False), key="purchase:1", telegram_id=TG, ent=BASIC,
            premium_until=UNTIL, source="webhook",
        )
    assert jobs.rows == {}


async def test_duplicate_enqueue_same_key_changes_nothing(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    j1 = await enqueue("purchase:1", BASIC)
    j2 = await enqueue("purchase:1", BASIC)
    assert j1 == j2 and len(jobs.rows) == 1
    assert await provisioning.run_now(j1, bot=BOT) is True
    calls_after = len(panel.calls)
    # webhook replay after done: same job, run_now is a no-op
    assert await enqueue("purchase:1", BASIC) == j1
    assert await provisioning.run_now(j1, bot=BOT) is False
    assert panel.bypass_limit(TG) == 13 * GIB
    assert len(panel.calls) == calls_after
    alerts.assert_not_awaited()


# ── bypass CAS ─────────────────────────────────────────────────────────

async def test_basic_with_existing_bypass_3gb_becomes_13gb(monkeypatch, panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, NOW - timedelta(days=1))
    seen = []
    real_update = remnawave_api.update_user

    async def spy(user_id, **fields):
        seen.append((user_id, dict(fields)))
        return await real_update(user_id, **fields)

    monkeypatch.setattr(remnawave_api, "update_user", spy)

    job_id = await enqueue("purchase:1", BASIC)
    assert await provisioning.run_now(job_id, bot=BOT) is True

    assert panel.bypass_limit(TG) == 13 * GIB
    assert panel.premium_expire(TG) == UNTIL
    row = jobs.job(job_id)
    assert row["status"] == "done"
    assert (row["bypass_base_bytes"], row["bypass_target_bytes"]) == (3 * GIB, 13 * GIB)
    # absolute PATCH, trusted, addressed by the bypass entity's own numeric id
    traffic = [s for s in seen if "trafficLimitBytes" in s[1]]
    # the untagged bypass entity gets BYPASS in the same PATCH (no extra request)
    assert traffic == [(panel.bypass[TG]["id"],
                        {"trafficLimitBytes": 13 * GIB, "status": "ACTIVE", "_trust_bypass": True,
                         "tag": "BYPASS"})]
    (_, pid, fields), = premium_patches(panel)
    assert pid == panel.premium[TG]["id"]
    assert fields["expireAt"].startswith(UNTIL.strftime("%Y-%m-%dT%H:%M:%S"))
    assert fields["status"] == "ACTIVE"
    alerts.assert_not_awaited()


async def test_combo_basic_without_bypass_creates_75gb(panel, jobs, db, alerts):
    job_id = await enqueue("purchase:2", COMBO)
    assert await provisioning.run_now(job_id, bot=BOT) is True

    assert panel.bypass_limit(TG) == 75 * GIB
    assert [c for c in panel.calls if c[0] == "create_bypass_user_entity"] == [
        ("create_bypass_user_entity", TG, 75 * GIB),
    ]
    assert bypass_patches(panel) == []
    assert panel.premium_expire(TG) == UNTIL
    row = jobs.job(job_id)
    assert (row["bypass_base_bytes"], row["bypass_target_bytes"]) == (0, 75 * GIB)
    # caches persisted like purchase_flow does
    b, p, c = panel.bypass[TG], panel.premium[TG], db.cache[TG]
    assert (c["bypass_uuid"], c["bypass_url"], c["bypass_short"], c["bypass_id"]) == (
        b["vlessUuid"], b["subscriptionUrl"], b["shortUuid"], b["id"])
    assert (c["premium_uuid"], c["premium_url"], c["premium_short"], c["premium_id"]) == (
        p["vlessUuid"], p["subscriptionUrl"], p["shortUuid"], p["id"])


@pytest.mark.parametrize("ent, seed, first, second", [
    (BASIC, 3 * GIB, 13 * GIB, 23 * GIB),
    (COMBO, None, 75 * GIB, 150 * GIB),
])
async def test_second_renewal_adds_again(panel, jobs, db, alerts, ent, seed, first, second):
    if seed is not None:
        panel.seed_bypass(TG, seed)
    j1 = await enqueue("purchase:1", ent, UNTIL)
    assert await provisioning.run_now(j1, bot=BOT) is True
    assert panel.bypass_limit(TG) == first
    j2 = await enqueue("purchase:2", ent, UNTIL + timedelta(days=30))
    assert await provisioning.run_now(j2, bot=BOT) is True
    assert panel.bypass_limit(TG) == second
    assert panel.premium_expire(TG) == UNTIL + timedelta(days=30)


async def test_crash_after_patch_then_retry_adds_exactly_10gb(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, UNTIL + timedelta(days=1))   # premium already ahead → only the bypass PATCH
    job_id = await enqueue("purchase:1", BASIC)
    panel.mode = "crash_after_patch"
    assert await provisioning.run_now(job_id, bot=BOT) is False
    row = jobs.job(job_id)
    assert row["status"] == "pending" and "ConnectionError" in row["last_error"]
    assert (row["bypass_base_bytes"], row["bypass_target_bytes"]) == (3 * GIB, 13 * GIB)
    assert panel.bypass_limit(TG) == 13 * GIB        # PATCH landed before the drop

    panel.mode = "ok"
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert panel.bypass_limit(TG) == 13 * GIB        # exactly +10, no second +N
    assert panel.patch_count == 1
    assert jobs.job(job_id)["status"] == "done"


async def test_bypass_plan_is_saved_before_the_patch(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, UNTIL + timedelta(days=1))
    job_id = await enqueue("purchase:1", BASIC)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    seq = names(panel)
    i_plan = seq.index("save_bypass_plan")
    i_patch = panel.calls.index(bypass_patches(panel)[0])
    assert i_plan < i_patch
    assert seq[:i_plan].count("get_bypass_state") == 1
    assert seq[i_plan:i_patch].count("get_bypass_state") == 1   # CAS re-read


async def test_conflict_marks_dead_with_forced_alert(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, UNTIL + timedelta(days=1))
    job_id = await enqueue("purchase:1", BASIC)
    panel.mode = "conflict"          # +1 GB lands between the plan read and the CAS read
    assert await provisioning.run_now(job_id, bot=BOT) is False

    row = jobs.job(job_id)
    assert row["status"] == "dead"
    assert f"conflict: base={3 * GIB}, target={13 * GIB}, current={4 * GIB}" in row["last_error"]
    assert panel.bypass_limit(TG) == 4 * GIB   # external change kept, nothing overwritten
    assert panel.patch_count == 0
    assert forces(alerts) == [True]
    bot, category, text = alerts.await_args.args
    assert bot is BOT and category == "payment"
    for piece in (f"job_id: {job_id}", "purchase:1", str(TG), "basic", "+30d", "+10 GB",
                  "conflict", RETRY_SQL.format(job_id)):
        assert piece in text, piece
    err = db.payment_errors[-1]
    assert (err["stage"], err["telegram_id"], err["purchase_id"], err["error_code"]) == (
        "provisioning", TG, "purchase:1", "dead")


async def test_absent_bypass_adopted_with_zero_limit_is_patched_to_target(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 0)
    panel.seed_premium(TG, UNTIL + timedelta(days=1))
    panel.bypass_invisible_reads = 2   # plan read + CAS read miss the entity
    job_id = await enqueue("purchase:1", BASIC)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert names(panel).count("create_bypass_user_entity") == 1
    # create ADOPTED it and kept the old limit (0) → CAS PATCHes to the target
    assert panel.bypass_limit(TG) == 10 * GIB
    assert len(bypass_patches(panel)) == 1


async def test_adopted_bypass_with_foreign_limit_is_a_conflict(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, UNTIL + timedelta(days=1))
    panel.bypass_invisible_reads = 2
    job_id = await enqueue("purchase:1", BASIC)
    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert jobs.job(job_id)["status"] == "dead"
    assert panel.bypass_limit(TG) == 3 * GIB
    assert forces(alerts) == [True]


async def test_unrelated_username_owner_is_permanent(monkeypatch, panel, jobs, db, alerts):
    panel.seed_premium(TG, UNTIL + timedelta(days=1))
    monkeypatch.setattr(remnawave_bypass, "create_bypass_user_entity", AsyncMock(
        return_value=BypassCreateResult(False, "x", None, None, 409, "conflict_unrelated_user")))
    job_id = await enqueue("purchase:1", BASIC)
    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert jobs.job(job_id)["status"] == "dead"
    assert forces(alerts) == [True]


async def test_pack_job_touches_only_bypass_and_never_creates_while_down(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    job_id = await enqueue("pack:1", for_pack(15), until=None)
    panel.mode = "down"
    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert "create_bypass_user_entity" not in names(panel)
    panel.mode = "ok"
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert panel.bypass_limit(TG) == 18 * GIB
    assert not {"get_premium_state", "create_premium_user_entity", "renew_premium_user"} & set(names(panel))


# ── transient failures, backoff, alerts ────────────────────────────────

async def test_panel_down_backoff_alert_once_forced_then_recovers(panel, jobs, db, alerts):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, NOW)
    job_id = await enqueue("purchase:1", BASIC)
    panel.mode = "down"

    t0 = _utcnow()
    assert await provisioning.run_now(job_id, bot=BOT) is False
    row = jobs.job(job_id)
    assert (row["status"], row["attempts"]) == ("pending", 1)
    assert 59 <= (row["next_attempt_at"] - t0).total_seconds() <= 62

    t1 = _utcnow()
    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert (row["status"], row["attempts"]) == ("pending", 2)
    assert 119 <= (row["next_attempt_at"] - t1).total_seconds() <= 122
    assert forces(alerts) == [True, False]
    assert panel.patch_count == 0

    panel.mode = "ok"
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert (row["status"], row["attempts"]) == ("done", 3)
    assert panel.bypass_limit(TG) == 13 * GIB
    assert panel.premium_expire(TG) == UNTIL
    assert len(alerts.await_args_list) == 2          # no alert on success
    assert [e["error_code"] for e in db.payment_errors] == ["retry", "retry"]


async def test_dead_after_24h_with_forced_alert(panel, jobs, db, alerts):
    job_id = await enqueue("purchase:1", BASIC)
    jobs.job(job_id)["created_at"] = _utcnow() - timedelta(hours=25)
    jobs.job(job_id)["attempts"] = 5
    panel.mode = "down"
    assert await provisioning.run_now(job_id, bot=BOT) is False
    assert jobs.job(job_id)["status"] == "dead"
    assert forces(alerts) == [True]
    assert "DEAD" in alerts.await_args.args[2]
    assert db.payment_errors[-1]["error_code"] == "dead"


async def test_alert_falls_back_to_webhook_bot(monkeypatch, panel, jobs, db, alerts):
    webhook_bot = object()
    monkeypatch.setattr(payment_webhook, "_bot", webhook_bot)
    job_id = await enqueue("purchase:1", BASIC)
    panel.mode = "down"
    assert await provisioning.run_now(job_id) is False
    assert alerts.await_args.args[0] is webhook_bot


async def test_without_any_bot_only_logs(panel, jobs, db, alerts):
    job_id = await enqueue("purchase:1", BASIC)
    panel.mode = "down"
    assert await provisioning.run_now(job_id) is False
    alerts.assert_not_awaited()
    assert len(db.payment_errors) == 1


# ── premium: never shorten ─────────────────────────────────────────────

async def test_premium_never_shortened(panel, jobs, db, alerts):
    later = UNTIL + timedelta(days=30)
    panel.seed_premium(TG, later)
    job_id = await enqueue("grant:1", GRANT)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert panel.premium_expire(TG) == later
    assert not {"update_user", "create_premium_user_entity", "renew_premium_user"} & set(names(panel))


async def test_premium_target_uses_later_subscription_expiry(panel, jobs, db, alerts):
    panel.seed_premium(TG, NOW + timedelta(days=10))
    db.subs[TG] = {"expires_at": UNTIL + timedelta(days=10), "uuid": None}
    job_id = await enqueue("grant:1", GRANT)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert panel.premium_expire(TG) == UNTIL + timedelta(days=10)
    assert len(premium_patches(panel)) == 1


async def test_bypass_only_placeholder_expiry_is_not_a_premium_target(panel, jobs, db, alerts):
    # ensure_bypass_only_subscription writes expires_at = now + 3650d; a premium job
    # retried after the user became bypass-only must not push premium ~10 years out.
    panel.seed_premium(TG, NOW + timedelta(days=10))
    db.subs[TG] = {"expires_at": NOW + timedelta(days=3650), "uuid": None, "is_bypass_only": True}
    job_id = await enqueue("grant:1", GRANT)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert panel.premium_expire(TG) == UNTIL


async def test_absent_premium_is_created_with_target(panel, jobs, db, alerts):
    db.subs[TG] = {"expires_at": NOW - timedelta(days=3), "uuid": LEGACY_UUID}
    job_id = await enqueue("grant:1", GRANT)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert [c for c in panel.calls if c[0] == "create_premium_user_entity"] == [
        ("create_premium_user_entity", TG, UNTIL),
    ]
    assert panel.premium_expire(TG) == UNTIL
    assert panel.premium[TG]["vlessUuid"] == LEGACY_UUID     # legacy uuid reused like purchase_flow
    assert premium_patches(panel) == []
    p = panel.premium[TG]
    assert (db.cache[TG]["premium_uuid"], db.cache[TG]["premium_id"]) == (p["vlessUuid"], p["id"])


async def test_premium_stale_adoption_is_patched_up_to_target(panel, jobs, db, alerts):
    panel.seed_premium(TG, NOW - timedelta(days=5))
    panel.premium_invisible_reads = 1      # reader misses it → create → adopt
    panel.premium_adopt_patch_ok = False   # adoption PATCH failed → stale expireAt
    job_id = await enqueue("grant:1", GRANT)
    assert await provisioning.run_now(job_id, bot=BOT) is True
    assert "create_premium_user_entity" in names(panel)
    assert panel.premium_expire(TG) == UNTIL
    assert len(premium_patches(panel)) == 1


# ── run_now never raises / apply contract ──────────────────────────────

async def test_run_now_never_raises_when_claim_fails(monkeypatch, panel, jobs, db, alerts):
    monkeypatch.setattr(pj, "claim", AsyncMock(side_effect=RuntimeError("db down")))
    assert await provisioning.run_now(1, bot=BOT) is False


async def test_run_now_never_raises_when_everything_fails(monkeypatch, panel, jobs, db, alerts):
    job_id = await enqueue("purchase:1", BASIC)
    monkeypatch.setattr(provisioning, "apply", AsyncMock(side_effect=ValueError("bug")))
    monkeypatch.setattr(pj, "mark_retry", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(database, "log_payment_error", AsyncMock(side_effect=RuntimeError("db down")))
    alerts.side_effect = RuntimeError("telegram down")
    assert await provisioning.run_now(job_id, bot=BOT) is False


async def test_run_now_unknown_job_returns_false_without_http(panel, jobs, db, alerts):
    assert await provisioning.run_now(999, bot=BOT) is False
    assert panel.calls == []


async def test_run_now_timeout_is_transient(monkeypatch, panel, jobs, db, alerts):
    async def hang(tg):
        await asyncio.sleep(5)

    monkeypatch.setattr(remnawave_api, "get_premium_state", hang)
    job_id = await enqueue("purchase:1", BASIC)
    assert await provisioning.run_now(job_id, bot=BOT, timeout=0.05) is False
    row = jobs.job(job_id)
    assert row["status"] == "pending" and "timeout" in row["last_error"].lower()


async def test_apply_raises_transient_when_panel_down(panel, jobs, db):
    job_id = await enqueue("purchase:1", BASIC)
    job = await pj.claim(job_id)
    panel.mode = "down"
    with pytest.raises(ProvisioningTransient):
        await provisioning.apply(job)


async def test_apply_raises_permanent_on_conflict(panel, jobs, db):
    panel.seed_bypass(TG, 3 * GIB)
    panel.seed_premium(TG, UNTIL + timedelta(days=1))
    job_id = await enqueue("purchase:1", BASIC)
    job = await pj.claim(job_id)
    panel.mode = "conflict"
    with pytest.raises(ProvisioningPermanent):
        await provisioning.apply(job)
