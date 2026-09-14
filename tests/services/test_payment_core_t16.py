"""T16 — free grants through the provisioning outbox (entry point "grants").
Spec: docs/audit/02_payment_core_plan.md §A flow 8, §G0.

Flows: game (bowling / dice), promo link (days / GB), admin bonus (days / GB),
bypass-gift link (GB), broadcast trial key (+1 day, +1 GB).

Flag ON: one tx (grant_access(defer_panel=True, _caller_holds_transaction=True)
for days + provisioning.enqueue) → commit → run_now against FakePanel.
Day grants give 0 GB; GB rewards exactly N GB; the same event key never grants
twice; panel down → job pending + admin alert, the user sees the normal texts.
Flag OFF: the legacy calls are still made and nothing is enqueued.

Hermetic: FakePanel / FakeJobs / FakeDB (tests/fakes), a fake pool whose
connection emulates the few statements the handlers issue, a fake grant_access.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import database
import database.provisioning_jobs as pj
import database.subscriptions as db_subs
from app.api import payment_webhook
from app.handlers import game as game_mod
from app.handlers.callbacks import broadcast_trial_key as btk_mod
from app.handlers.user import start as start_mod
from app.i18n import get_text as i18n_get_text
from app.services import admin_alerts, grant_outbox, provisioning, remnawave_service, sub_aggregator
from tests.fakes.panel import FakePanel
from tests.fakes.provisioning import FakeDB, FakeJobs

GIB = 1024 ** 3
TG = 5151
TG2 = 5152
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


# ── fake DB world ──────────────────────────────────────────────────────

class World:
    def __init__(self, jobs: FakeJobs, db: FakeDB):
        self.jobs = jobs
        self.db = db
        self.users = {}           # tg -> {"game_last_played": naive, "dice_last_played": naive}
        self.redemptions = {}     # (link_id, tg) -> promo_link_redemptions.id
        self.grant_calls = []
        self.ensure_calls = []
        self.locks = []
        self.on_user_read = None  # hook: simulate a concurrent click between read and CAS
        self.fail_grant = False


class _Tx:
    """conn.transaction(): rolls jobs / subscriptions back on an exception."""

    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        w = self.conn.world
        self.jobs_before = set(w.jobs.rows)
        self.subs_before = copy.deepcopy(w.db.subs)
        self.conn.in_tx = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        w = self.conn.world
        self.conn.in_tx = False
        if exc_type is not None:
            for job_id in set(w.jobs.rows) - self.jobs_before:
                del w.jobs.rows[job_id]
            w.db.subs = self.subs_before
        return False


class TxConn:
    def __init__(self, world: World):
        self.world = world
        self.in_tx = False
        self.calls = []

    def is_in_transaction(self) -> bool:
        return self.in_tx

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("execute", s, args))
        w = self.world
        if s.startswith("SELECT pg_advisory_xact_lock"):
            assert self.in_tx, "advisory xact lock outside a transaction"
            w.locks.append(args[0])
            return "SELECT 1"
        if s.startswith("INSERT INTO users"):
            w.users.setdefault(args[0], {})
            return "INSERT 0 1"
        if s.startswith("UPDATE users SET username"):
            return "UPDATE 1"
        if s.startswith("UPDATE users SET"):
            col = s.split()[3]
            row = w.users.setdefault(args[1], {})
            if "IS NOT DISTINCT FROM" in s and row.get(col) != args[2]:
                return "UPDATE 0"
            row[col] = args[0]
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute: {s}")

    async def fetchrow(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchrow", s, args))
        w = self.world
        if "FROM provisioning_jobs" in s:
            assert self.in_tx
            for r in w.jobs.rows.values():
                if r["idempotency_key"] == args[0]:
                    until = r["premium_until"]
                    return {"id": r["id"], "premium_until": until.replace(tzinfo=None) if until else None}
            return None
        if "FROM users" in s:
            col = "game_last_played" if "game_last_played" in s else "dice_last_played"
            out = {col: (w.users.get(args[0]) or {}).get(col)}
            if w.on_user_read:
                w.on_user_read(args[0], col)
            return out
        raise AssertionError(f"unexpected fetchrow: {s}")

    async def fetchval(self, sql, *args):
        s = _norm(sql)
        self.calls.append(("fetchval", s, args))
        if "FROM promo_link_redemptions" in s:
            return self.world.redemptions.get((args[0], args[1]))
        raise AssertionError(f"unexpected fetchval: {s}")


class FakePool:
    def __init__(self, world: World):
        self.world = world
        self.conns = []

    def acquire(self):
        conn = TxConn(self.world)
        self.conns.append(conn)

        class _Acq:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _Acq()


# ── fixtures ───────────────────────────────────────────────────────────

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


@pytest.fixture
def legacy_bypass(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(remnawave_service, "add_bypass_traffic", mock)
    return mock


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "off")
    monkeypatch.delenv(EP_VAR, raising=False)
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda tg: None)
    monkeypatch.setattr(payment_webhook, "_bot", None)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "grants")
    assert grant_outbox.is_on()


@pytest.fixture
def world(monkeypatch, jobs, db, alerts):
    w = World(jobs, db)
    monkeypatch.setattr(database, "DB_READY", True)
    pool = w.pool = FakePool(w)

    async def get_pool():
        return pool

    async def grant_access(*, telegram_id, duration, source, admin_telegram_id=None,
                           admin_grant_days=None, conn=None, _caller_holds_transaction=False,
                           tariff="basic", defer_panel=False, **kw):
        w.grant_calls.append({
            "tg": telegram_id, "days": duration.days, "source": source, "tariff": tariff,
            "admin_telegram_id": admin_telegram_id, "admin_grant_days": admin_grant_days,
            "defer": defer_panel, "holds": _caller_holds_transaction,
            "in_tx": conn.is_in_transaction() if conn is not None else None, "extra": kw,
        })
        if w.fail_grant:
            raise RuntimeError("db boom")
        now = utcnow()
        sub = w.db.subs.get(telegram_id)
        if sub and sub["status"] == "active" and sub["expires_at"] > now:
            end = sub["expires_at"] + duration
            sub["expires_at"] = end
            return {"subscription_end": end, "action": "renewal", **({"deferred": True} if defer_panel else {})}
        end = now + duration
        w.db.subs[telegram_id] = {
            "telegram_id": telegram_id, "uuid": None, "status": "active", "expires_at": end,
            "subscription_type": tariff, "activation_status": "pending" if defer_panel else "active",
        }
        return {"subscription_end": end, "action": "pending_activation" if defer_panel else "new_issuance",
                **({"deferred": True} if defer_panel else {})}

    async def get_subscription(tg):
        sub = w.db.subs.get(tg)
        if sub and sub["status"] == "active" and sub["expires_at"] > utcnow():
            return dict(sub)
        return None

    async def ensure_bypass_only_subscription(tg):
        w.ensure_calls.append(tg)
        return True

    async def complete_activation(tg, *, vpn_key, vpn_key_plus, uuid=None, conn=None):
        sub = w.db.subs.get(tg)
        if not sub or sub.get("activation_status") != "pending":
            return False
        sub.update(activation_status="active", vpn_key=vpn_key, vpn_key_plus=vpn_key_plus)
        return True

    monkeypatch.setattr(database, "get_pool", get_pool)
    monkeypatch.setattr(database, "grant_access", grant_access)
    monkeypatch.setattr(db_subs, "grant_access", grant_access)
    monkeypatch.setattr(database, "get_subscription", get_subscription)
    monkeypatch.setattr(database, "ensure_bypass_only_subscription", ensure_bypass_only_subscription)
    monkeypatch.setattr(database, "complete_activation", complete_activation)
    return w


def seed_active(world: World, panel: FakePanel, tg=TG, *, tariff="basic", days_left=10, bypass_gb=None):
    end = (utcnow() + timedelta(days=days_left)).replace(microsecond=0)
    world.db.subs[tg] = {
        "telegram_id": tg, "uuid": None, "status": "active", "expires_at": end,
        "subscription_type": tariff, "activation_status": "active",
    }
    panel.seed_premium(tg, end)
    if bypass_gb is not None:
        panel.seed_bypass(tg, bypass_gb * GIB)
    return end


def only_job(world: World):
    assert len(world.jobs.rows) == 1, world.jobs.rows
    return next(iter(world.jobs.rows.values()))


def ceil_s(dt: datetime) -> datetime:
    return provisioning._ceil_second(dt)


def assert_outbox_grant_call(call, *, source, days):
    assert call["defer"] is True and call["holds"] is True and call["in_tx"] is True
    assert call["source"] == source and call["days"] == days


# ── grant_outbox helper ────────────────────────────────────────────────

def test_entitlements_day_grants_zero_gb_gb_rewards_exact():
    ent = grant_outbox.entitlement(days=7, tier="plus")
    assert (ent.tariff_key, ent.premium_days, ent.premium_tier, ent.bypass_bytes) == ("grant", 7, "plus", 0)
    ent = grant_outbox.entitlement(gb=15)
    assert (ent.tariff_key, ent.premium_days, ent.premium_tier, ent.bypass_bytes) == ("bypass_gift", 0, None, 15 * GIB)
    ent = grant_outbox.entitlement(days=1, gb=1)
    assert (ent.premium_days, ent.bypass_bytes) == (1, GIB)
    assert grant_outbox.grant_tier("combo_plus") == "plus"
    assert grant_outbox.grant_tier("weird") == "basic"


async def test_same_key_twice_grants_once(flag_on, world, panel):
    end0 = seed_active(world, panel)
    a = await grant_outbox.grant(telegram_id=TG, key="k:1", days=3, gb=2, grant_source="admin")
    b = await grant_outbox.grant(telegram_id=TG, key="k:1", days=3, gb=2, grant_source="admin")
    assert a.job_id == b.job_id and not a.duplicate and b.duplicate
    assert b.subscription_end == a.subscription_end == end0 + timedelta(days=3)
    assert len(world.grant_calls) == 1
    assert panel.premium_expire(TG) == ceil_s(end0 + timedelta(days=3))
    assert panel.bypass_limit(TG) == 2 * GIB
    assert world.locks == ["k:1", "k:1"]


async def test_tx_failure_enqueues_nothing(flag_on, world, panel):
    seed_active(world, panel)
    world.fail_grant = True
    with pytest.raises(RuntimeError):
        await grant_outbox.grant(telegram_id=TG, key="k:fail", days=3, grant_source="admin")
    assert world.jobs.rows == {}


# ── game ───────────────────────────────────────────────────────────────

class FakeBot:
    def __init__(self, dice_value):
        self.dice_value = dice_value
        self.send_dice = AsyncMock(side_effect=self._dice)
        self.send_message = AsyncMock()
        self.edit_message_text = AsyncMock()

    async def _dice(self, **kw):
        return SimpleNamespace(dice=SimpleNamespace(value=self.dice_value))


@pytest.fixture
def game_env(monkeypatch):
    monkeypatch.setattr(game_mod, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(game_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(game_mod, "safe_edit_text", AsyncMock())
    monkeypatch.setattr(game_mod, "asyncio", SimpleNamespace(sleep=AsyncMock()))


def game_callback(bot, tg=TG):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=tg), message=SimpleNamespace(chat=SimpleNamespace(id=tg)),
        answer=AsyncMock(), bot=bot,
    )


def sent_text(bot) -> str:
    return bot.send_message.await_args.kwargs["text"]


async def test_game_bowling_strike_7_days_premium_only(flag_on, world, panel, game_env):
    end0 = seed_active(world, panel, tariff="plus", bypass_gb=3)
    bot = FakeBot(6)
    await game_mod.callback_game_bowling(game_callback(bot), bot=bot)

    job = only_job(world)
    played = world.users[TG]["game_last_played"].replace(tzinfo=timezone.utc)
    assert job["idempotency_key"] == f"game:{TG}:bowling:{played.isoformat()}"
    assert job["source"] == "grants" and job["tariff_key"] == "grant" and job["bypass_add_bytes"] == 0
    assert job["status"] == "done"
    assert_outbox_grant_call(world.grant_calls[0], source="game_strike", days=7)
    assert world.grant_calls[0]["tariff"] == "plus"
    assert panel.premium_expire(TG) == ceil_s(end0 + timedelta(days=7))
    assert panel.bypass_limit(TG) == 3 * GIB  # day grant: 0 GB
    assert (end0 + timedelta(days=7)).strftime("%d.%m.%Y") in sent_text(bot)


async def test_game_dice_grants_dice_value_days(flag_on, world, panel, game_env):
    end0 = seed_active(world, panel)
    bot = FakeBot(3)
    await game_mod.callback_game_dice(game_callback(bot), bot=bot)

    job = only_job(world)
    played = world.users[TG]["dice_last_played"].replace(tzinfo=timezone.utc)
    assert job["idempotency_key"] == f"game:{TG}:dice:{played.isoformat()}"
    assert job["bypass_add_bytes"] == 0 and job["context"]["dice_value"] == 3
    assert_outbox_grant_call(world.grant_calls[0], source="game_dice", days=3)
    assert panel.premium_expire(TG) == ceil_s(end0 + timedelta(days=3))
    assert panel.bypass_limit(TG) is None


async def test_game_concurrent_double_click_grants_once(flag_on, world, panel, game_env):
    """Both clicks read the same cooldown value; only the CAS winner rolls and grants."""
    seed_active(world, panel)
    other_click = datetime(2026, 1, 1, 12, 0, 0)

    def concurrent_writer(tg, col):
        world.users[tg][col] = other_click  # the other click consumed the cooldown first
        world.on_user_read = None

    world.on_user_read = concurrent_writer
    bot = FakeBot(6)
    await game_mod.callback_game_bowling(game_callback(bot), bot=bot)
    bot.send_dice.assert_not_awaited()
    assert world.jobs.rows == {} and world.grant_calls == []


async def test_game_second_click_hits_cooldown(flag_on, world, panel, game_env):
    seed_active(world, panel)
    bot = FakeBot(4)
    await game_mod.callback_game_dice(game_callback(bot), bot=bot)
    await game_mod.callback_game_dice(game_callback(bot), bot=bot)
    assert len(world.jobs.rows) == 1 and len(world.grant_calls) == 1
    assert bot.send_dice.await_count == 1


async def test_game_panel_down_job_pending_alert_success_text(flag_on, world, panel, game_env, alerts):
    end0 = seed_active(world, panel)
    panel.mode = "down"
    bot = FakeBot(6)
    await game_mod.callback_game_bowling(game_callback(bot), bot=bot)

    job = only_job(world)
    assert job["status"] == "pending" and job["attempts"] == 1
    alerts.assert_awaited_once()
    assert alerts.await_args.args[0] is bot and alerts.await_args.kwargs["force"] is True
    assert "⚠️" not in sent_text(bot)
    assert (end0 + timedelta(days=7)).strftime("%d.%m.%Y") in sent_text(bot)
    panel.mode = "ok"
    assert await provisioning.run_now(job["id"]) is True
    assert panel.premium_expire(TG) == ceil_s(end0 + timedelta(days=7))


async def test_game_flag_off_uses_legacy_grant(world, panel, game_env):
    seed_active(world, panel)
    bot = FakeBot(2)
    await game_mod.callback_game_dice(game_callback(bot), bot=bot)
    assert world.jobs.rows == {}
    call = world.grant_calls[0]
    assert call["defer"] is False and call["holds"] is False and call["in_tx"] is None
    assert call["source"] == "game_dice" and call["days"] == 2
    update = [c for conn in world.pool.conns for c in conn.calls
              if c[1].startswith("UPDATE users SET dice_last_played")]
    # the cooldown is consumed by CAS on both paths (a double click pays once)
    assert update and "IS NOT DISTINCT FROM" in update[0][1]


@pytest.mark.parametrize("game", ["bowling", "dice"])
async def test_game_flag_off_concurrent_double_click_grants_once(world, panel, game_env, game):
    """P2: with the grants flag off the cooldown was consumed by an unconditional
    UPDATE — two concurrent clicks both passed the check and both got days."""
    seed_active(world, panel)
    col = "game_last_played" if game == "bowling" else "dice_last_played"
    other_click = datetime(2026, 1, 1, 12, 0, 0)

    def concurrent_writer(tg, c):
        world.users[tg][c] = other_click  # the other click consumed the cooldown first
        world.on_user_read = None

    world.on_user_read = concurrent_writer
    bot = FakeBot(6)
    handler = game_mod.callback_game_bowling if game == "bowling" else game_mod.callback_game_dice
    await handler(game_callback(bot), bot=bot)
    bot.send_dice.assert_not_awaited()
    assert world.grant_calls == []
    assert world.users[TG][col] == other_click


# ── promo link ─────────────────────────────────────────────────────────

async def test_promo_days_premium_only_key_from_redemption(flag_on, world, panel):
    world.redemptions[(7, TG)] = 41
    ok, text = await start_mod._apply_promo_reward(
        TG, "subscription_days", 5, {"tariff": "plus"}, link_id=7, bot=object(),
    )
    assert ok and text
    job = only_job(world)
    assert job["idempotency_key"] == f"promo:7:{TG}:41"
    assert job["bypass_add_bytes"] == 0 and job["status"] == "done"
    call = world.grant_calls[0]
    assert_outbox_grant_call(call, source="admin", days=5)
    assert call["tariff"] == "plus" and call["admin_grant_days"] == 5
    # new user: pending → created in the panel → activated
    assert panel.premium_expire(TG) == ceil_s(world.db.subs[TG]["expires_at"])
    assert world.db.subs[TG]["activation_status"] == "active"
    assert panel.bypass_limit(TG) is None


async def test_promo_retry_same_redemption_grants_once(flag_on, world, panel):
    world.redemptions[(7, TG)] = 41
    for _ in range(2):
        ok, _t = await start_mod._apply_promo_reward(TG, "bypass_gb", 15, {}, link_id=7)
        assert ok
    job = only_job(world)
    assert job["idempotency_key"] == f"promo:7:{TG}:41"
    assert panel.bypass_limit(TG) == 15 * GIB


async def test_promo_second_redemption_is_a_new_event(flag_on, world, panel):
    """max_uses_per_user > 1: each redemption row is its own event."""
    world.redemptions[(7, TG)] = 41
    await start_mod._apply_promo_reward(TG, "bypass_gb", 5, {}, link_id=7)
    world.redemptions[(7, TG)] = 42
    await start_mod._apply_promo_reward(TG, "bypass_gb", 5, {}, link_id=7)
    assert len(world.jobs.rows) == 2
    assert panel.bypass_limit(TG) == 10 * GIB


async def test_promo_gb_adds_exactly_n_gb(flag_on, world, panel):
    seed_active(world, panel, bypass_gb=3)
    world.redemptions[(9, TG)] = 5
    ok, _ = await start_mod._apply_promo_reward(TG, "bypass_gb", 15, {}, link_id=9)
    assert ok
    job = only_job(world)
    assert job["tariff_key"] == "bypass_gift" and job["premium_until"] is None
    assert panel.bypass_limit(TG) == 18 * GIB
    assert world.grant_calls == []
    assert world.ensure_calls == []  # active subscription: row untouched


async def test_promo_gb_without_subscription_ensures_bypass_row(flag_on, world, panel):
    world.redemptions[(9, TG)] = 5
    ok, _ = await start_mod._apply_promo_reward(TG, "bypass_gb", 15, {}, link_id=9)
    assert ok and world.ensure_calls == [TG]
    assert panel.bypass_limit(TG) == 15 * GIB


async def test_promo_panel_down_still_applied(flag_on, world, panel, alerts):
    panel.mode = "down"
    world.redemptions[(9, TG)] = 5
    bot = object()
    ok, text = await start_mod._apply_promo_reward(TG, "bypass_gb", 15, {}, link_id=9, bot=bot)
    assert ok and "15" in text
    job = only_job(world)
    assert job["status"] == "pending"
    assert alerts.await_args.args[0] is bot and alerts.await_args.kwargs["force"] is True
    assert world.db.payment_errors and world.db.payment_errors[0]["purchase_id"] == f"promo:9:{TG}:5"


async def test_promo_missing_redemption_row_is_not_applied(flag_on, world, panel):
    ok, text = await start_mod._apply_promo_reward(TG, "bypass_gb", 15, {}, link_id=9)
    assert (ok, text) == (False, "")
    assert world.jobs.rows == {}


async def test_promo_flag_off_uses_legacy_calls(world, panel, legacy_bypass):
    ok, _ = await start_mod._apply_promo_reward(TG, "bypass_gb", 15, {}, link_id=9)
    assert ok
    legacy_bypass.assert_awaited_once_with(
        telegram_id=TG, extra_bytes=15 * GIB, subscription_type="basic",
        subscription_end=None, period_days=30,
    )
    ok, _ = await start_mod._apply_promo_reward(TG, "subscription_days", 5, {"tariff": "plus"}, link_id=9)
    assert ok
    call = world.grant_calls[0]
    assert call["defer"] is False and call["in_tx"] is None and call["source"] == "admin"
    assert world.jobs.rows == {}


# ── bypass-gift link (/start bgift_<code>) ─────────────────────────────

@pytest.fixture
def start_env(monkeypatch):
    monkeypatch.setattr(database, "get_user", AsyncMock(return_value={"language": "ru", "referral_code": "R1"}))
    monkeypatch.setattr(start_mod, "safe_resolve_username", lambda *a, **k: "user")
    monkeypatch.setattr(start_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(start_mod, "get_main_menu_keyboard", AsyncMock(return_value=None))
    rollback = AsyncMock(return_value=True)
    monkeypatch.setattr(database, "rollback_bypass_gift_redemption", rollback)
    return SimpleNamespace(rollback=rollback)


def redeem_ok(monkeypatch, gb=10, code="GIFT1234"):
    mock = AsyncMock(return_value={"status": "success", "gb_amount": gb,
                                   "link": {"id": 3, "code": code}, "redemption_count": 1})
    monkeypatch.setattr(database, "redeem_bypass_gift_link", mock)
    return mock


async def start_bgift(code="GIFT1234", bot=None):
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private"), text=f"/start bgift_{code}",
        from_user=SimpleNamespace(id=TG, language_code="ru", username="user", first_name="U"),
        answer=AsyncMock(), bot=bot,
    )
    await start_mod.cmd_start(message, SimpleNamespace(clear=AsyncMock()))
    return message


async def test_bgift_adds_exactly_n_gb(flag_on, world, panel, start_env, monkeypatch):
    seed_active(world, panel, bypass_gb=2)
    redeem_ok(monkeypatch, gb=10)
    message = await start_bgift()
    job = only_job(world)
    assert job["idempotency_key"] == f"bgift:GIFT1234:{TG}"
    assert job["tariff_key"] == "bypass_gift" and job["premium_until"] is None
    assert panel.bypass_limit(TG) == 12 * GIB
    assert world.grant_calls == []
    assert message.answer.await_args.args[0] == i18n_get_text("ru", "bypass_gift.activated", gb=10)
    start_env.rollback.assert_not_awaited()


async def test_bgift_replay_grants_once(flag_on, world, panel, start_env, monkeypatch):
    redeem_ok(monkeypatch, gb=10)
    await start_bgift()
    await start_bgift()
    assert len(world.jobs.rows) == 1
    assert panel.bypass_limit(TG) == 10 * GIB
    assert world.ensure_calls == [TG, TG]  # no subscription: bypass row ensured (legacy step)


async def test_bgift_panel_down_keeps_redemption(flag_on, world, panel, start_env, monkeypatch, alerts):
    panel.mode = "down"
    redeem_ok(monkeypatch, gb=10)
    bot = object()
    message = await start_bgift(bot=bot)
    job = only_job(world)
    assert job["status"] == "pending"
    alerts.assert_awaited_once()
    assert alerts.await_args.args[0] is bot
    assert message.answer.await_args.args[0] == i18n_get_text("ru", "bypass_gift.activated", gb=10)
    start_env.rollback.assert_not_awaited()


async def test_bgift_tx_failure_rolls_back_redemption(flag_on, world, panel, start_env, monkeypatch):
    redeem_ok(monkeypatch, gb=10)

    async def boom(*a, **k):
        raise RuntimeError("insert failed")
    monkeypatch.setattr(pj, "insert_job", boom)
    message = await start_bgift()
    assert world.jobs.rows == {}
    start_env.rollback.assert_awaited_once_with(3, TG)
    assert message.answer.await_args.args[0] == i18n_get_text("ru", "bypass_gift.error_remnawave")


async def test_bgift_flag_off_uses_legacy_call(world, panel, start_env, monkeypatch, legacy_bypass):
    redeem_ok(monkeypatch, gb=10)
    await start_bgift()
    legacy_bypass.assert_awaited_once_with(
        telegram_id=TG, extra_bytes=10 * GIB, subscription_type="basic",
        subscription_end=None, period_days=30,
    )
    assert world.jobs.rows == {}


# ── broadcast trial key ────────────────────────────────────────────────

@pytest.fixture
def btk_env(monkeypatch):
    monkeypatch.setattr(btk_mod, "ensure_db_ready_callback", AsyncMock(return_value=True))
    monkeypatch.setattr(btk_mod, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(btk_mod, "check_rate_limit", lambda *a: (True, None))
    monkeypatch.setattr(btk_mod, "_send_device_screen_delayed", AsyncMock())
    env = SimpleNamespace(claim=AsyncMock(return_value=True), release=AsyncMock())
    monkeypatch.setattr(database, "claim_broadcast_trial_key", env.claim)
    monkeypatch.setattr(database, "release_broadcast_trial_key", env.release)
    return env


def btk_callback(bot, broadcast_id=55):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=TG), data=f"broadcast_trial_key:{broadcast_id}",
        message=SimpleNamespace(chat=SimpleNamespace(id=TG), answer=AsyncMock()),
        answer=AsyncMock(), bot=bot,
    )


async def test_btk_new_user_gets_1_day_and_exactly_1_gb(flag_on, world, panel, btk_env):
    cb = btk_callback(object())
    await btk_mod.callback_broadcast_trial_key(cb)
    job = only_job(world)
    assert job["idempotency_key"] == f"btk:55:{TG}"
    assert job["bypass_add_bytes"] == GIB and job["status"] == "done"
    assert_outbox_grant_call(world.grant_calls[0], source="trial", days=1)
    assert panel.bypass_limit(TG) == GIB
    assert panel.premium_expire(TG) == ceil_s(world.db.subs[TG]["expires_at"])
    assert world.db.subs[TG]["activation_status"] == "active"
    assert cb.message.answer.await_args.args[0] == i18n_get_text("ru", "broadcast.trial_key_activated")
    btk_env.release.assert_not_awaited()


async def test_btk_existing_user_plus_1_day_plus_1_gb(flag_on, world, panel, btk_env):
    end0 = seed_active(world, panel, bypass_gb=3)
    await btk_mod.callback_broadcast_trial_key(btk_callback(object()))
    assert panel.bypass_limit(TG) == 4 * GIB
    assert panel.premium_expire(TG) == ceil_s(end0 + timedelta(days=1))


async def test_btk_released_claim_retry_grants_once(flag_on, world, panel, btk_env):
    """Claim released after a commit (e.g. error after the tx) → retry reuses the job."""
    seed_active(world, panel, bypass_gb=3)
    await btk_mod.callback_broadcast_trial_key(btk_callback(object()))
    await btk_mod.callback_broadcast_trial_key(btk_callback(object()))
    assert len(world.jobs.rows) == 1 and len(world.grant_calls) == 1
    assert panel.bypass_limit(TG) == 4 * GIB


async def test_btk_double_click_second_claim_is_a_toast(flag_on, world, panel, btk_env):
    btk_env.claim.side_effect = [True, False]
    cb2 = btk_callback(object())
    await btk_mod.callback_broadcast_trial_key(btk_callback(object()))
    await btk_mod.callback_broadcast_trial_key(cb2)
    assert len(world.jobs.rows) == 1
    assert cb2.answer.await_args.args[0] == i18n_get_text("ru", "broadcast.trial_key_already")


async def test_btk_panel_down_success_text_job_pending(flag_on, world, panel, btk_env, alerts):
    panel.mode = "down"
    bot = object()
    cb = btk_callback(bot)
    await btk_mod.callback_broadcast_trial_key(cb)
    assert only_job(world)["status"] == "pending"
    assert alerts.await_args.args[0] is bot
    assert cb.message.answer.await_args.args[0] == i18n_get_text("ru", "broadcast.trial_key_activated")
    btk_env.release.assert_not_awaited()


async def test_btk_tx_failure_releases_claim(flag_on, world, panel, btk_env):
    world.fail_grant = True
    cb = btk_callback(object())
    await btk_mod.callback_broadcast_trial_key(cb)
    assert world.jobs.rows == {}
    btk_env.release.assert_awaited_once_with(55, TG)
    assert cb.message.answer.await_args.args[0] == i18n_get_text("ru", "broadcast.trial_key_error")


async def test_btk_flag_off_uses_legacy_calls(world, panel, btk_env, legacy_bypass):
    await btk_mod.callback_broadcast_trial_key(btk_callback(object()))
    call = world.grant_calls[0]
    assert call["defer"] is False and call["in_tx"] is None and call["source"] == "trial"
    legacy_bypass.assert_awaited_once_with(
        TG, extra_bytes=GIB, subscription_type="basic", period_days=1,
    )
    assert world.jobs.rows == {}
