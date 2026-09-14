"""Premium over-issuance repair (owner spec 2026-09-14).

Rule (database.reconciliation.compute_repair_target), the bulk service
(app/services/premium_repair) on the 3.4.3 HTTP fake, the CLI, and the
dashboard «Сверка» fix that now shares the rule.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import database
import database.core
from app.services import admin_alerts, premium_repair, remnawave_api
from app.services.tariffs import extend_expiry
from database import reconciliation as recon
from tests.fakes.remnawave_http import GIB, FakeRemnawaveHTTP

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
FAR = NOW + timedelta(days=3650)
FIVE_Y = timedelta(days=365 * 5)


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def pay(pid, tariff, when):
    """A payments row as asyncpg returns it (naive UTC)."""
    return {"id": pid, "tariff": tariff, "effective_at": when.astimezone(timezone.utc).replace(tzinfo=None)}


def inputs(payments=(), *, db_exp=None, status="active", bypass_only=False, source="payment",
           admin_days=0, row=True):
    e = recon._count_payments(list(payments))
    e.update(db_row=row, db_expires_at=db_exp if row else None, db_status=status if row else None,
             db_source=source if row else None, db_is_bypass_only=bypass_only if row else False,
             admin_grant_days=admin_days)
    return e


def rule(inp, panel=FAR):
    return recon.compute_repair_target(inp, now=NOW, panel_expires_at=panel)


# ── the rule ───────────────────────────────────────────────────────────

def test_year_purchase_is_one_year_from_the_payment():
    d = rule(inputs([pay(1, "plus_365", utc(2026, 3, 1))]))
    assert d["target"] == utc(2027, 3, 1)
    assert (d["target_source"], d["fallback"], d["would_extend"]) == ("purchases", None, False)


def test_renewals_chain_extends_the_running_window():
    d = rule(inputs([pay(1, "basic_30", utc(2026, 8, 1)), pay(2, "basic_90", utc(2026, 8, 25))]))
    assert d["target"] == utc(2026, 12, 1)          # 09-01 + 3 calendar months


def test_a_gap_starts_a_fresh_window_and_non_subscription_payments_do_not_count():
    inp = inputs([
        pay(1, "plus_365", utc(2025, 1, 1)),          # ended 2026-01-01
        pay(2, "traffic_10", utc(2026, 2, 1)),
        pay(3, "balance_topup", utc(2026, 3, 1)),
        pay(4, "basic_30", utc(2026, 9, 1)),          # after the gap
    ])
    assert (inp["approved_payments"], inp["counted_payments"]) == (4, 2)
    assert inp["proof_payment_ids"] == [1, 4]
    assert rule(inp)["target"] == utc(2026, 10, 1)


def test_admin_grant_days_go_on_top():
    assert rule(inputs([pay(1, "basic_30", utc(2026, 9, 1))], admin_days=10))["target"] == utc(2026, 10, 11)


def test_a_sane_db_date_wins_when_later_and_purchases_win_otherwise():
    later_db = rule(inputs([pay(1, "basic_30", utc(2026, 9, 1))], db_exp=utc(2027, 1, 1)))
    assert (later_db["target"], later_db["target_source"], later_db["by_db"]) == (utc(2027, 1, 1), "db", utc(2027, 1, 1))
    earlier_db = rule(inputs([pay(1, "basic_30", utc(2026, 9, 1))], db_exp=utc(2026, 9, 20)))
    assert (earlier_db["target"], earlier_db["target_source"]) == (utc(2026, 10, 1), "purchases")


def test_a_leaked_db_date_is_ignored_and_marked_for_shortening():
    d = rule(inputs([pay(1, "basic_30", utc(2026, 9, 1))], db_exp=FAR))
    assert d["by_db"] is None and d["db_leaked"] is True
    assert d["target"] == utc(2026, 10, 1)


@pytest.mark.parametrize("kw", [{"bypass_only": True}, {"source": "bypass_only"}])
def test_a_bypass_only_row_never_counts_and_its_placeholder_is_never_touched(kw):
    far = rule(inputs(db_exp=FAR, **kw))
    assert far["by_db"] is None and far["db_leaked"] is False
    sane = rule(inputs(db_exp=utc(2027, 1, 1), **kw))
    assert sane["by_db"] is None and sane["fallback"] == "no_payments"


def test_an_inactive_db_row_does_not_count():
    assert rule(inputs(db_exp=utc(2027, 1, 1), status="expired"))["by_db"] is None


@pytest.mark.parametrize("inp", [inputs(), inputs(row=False)])
def test_no_payments_and_no_db_date_is_tomorrow(inp):
    d = rule(inp)
    assert (d["target"], d["fallback"], d["target_source"]) == (NOW + timedelta(days=1), "no_payments", "fallback")


def test_a_past_date_becomes_tomorrow():
    d = rule(inputs([pay(1, "basic_30", utc(2025, 1, 1))], db_exp=utc(2026, 1, 1)))
    assert (d["target"], d["fallback"]) == (NOW + timedelta(days=1), "past_date")


def test_never_extend_the_panel():
    inp = inputs([pay(1, "basic_30", utc(2026, 8, 1)), pay(2, "basic_90", utc(2026, 8, 25))])
    assert rule(inp, panel=utc(2026, 11, 1))["would_extend"] is True
    assert rule(inp, panel=utc(2026, 12, 1))["would_extend"] is True        # equal = no change
    assert rule(inp, panel=utc(2026, 12, 2))["would_extend"] is False


# ── the bulk service on the panel fake ─────────────────────────────────

class FakeDB:
    """In-memory stand-in for load_repair_inputs / record_premium_repair
    (their SQL is covered on real Postgres in tests/db/test_premium_repair_db.py)."""

    def __init__(self):
        self.subs: dict = {}
        self.pays: dict = {}
        self.records: list = []

    async def load(self, ids):
        out = {}
        for tg in ids:
            s = self.subs.get(tg)
            e = recon._count_payments(self.pays.get(tg, []))
            e.update(db_row=s is not None, db_expires_at=s["expires_at"] if s else None,
                     db_status=s["status"] if s else None, db_source=s["source"] if s else None,
                     db_is_bypass_only=s["is_bypass_only"] if s else False,
                     admin_grant_days=s.get("admin_grant_days", 0) if s else 0)
            out[tg] = e
        return out

    async def record(self, tg, **kw):
        self.records.append((tg, kw))
        s = self.subs.get(tg)
        shortened = False
        if (kw["shorten_db"] and s and not s["is_bypass_only"] and s["source"] != "bypass_only"
                and s["expires_at"] > kw["now"] + FIVE_Y and s["expires_at"] > kw["new_expires_at"]):
            s["expires_at"] = kw["new_expires_at"]
            shortened = True
        return {"log_id": len(self.records), "db_shortened": shortened}


def sub(exp, *, status="active", source="payment", bypass_only=False):
    return {"expires_at": exp, "status": status, "source": source, "is_bypass_only": bypass_only}


@pytest.fixture
def w(monkeypatch):
    now = datetime.now(timezone.utc)
    far = now + timedelta(days=3650)
    http = FakeRemnawaveHTTP().install(monkeypatch)
    db = FakeDB()
    monkeypatch.setattr(recon, "load_repair_inputs", db.load)
    monkeypatch.setattr(recon, "record_premium_repair", db.record)
    monkeypatch.setattr(recon, "get_pool", AsyncMock(return_value=object()))
    clock = {"t": 1000.0}

    async def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(premium_repair, "_clock", lambda: clock["t"])
    monkeypatch.setattr(premium_repair, "_sleep", fake_sleep)
    patch_times: list = []
    real_update = remnawave_api.update_user

    async def timed_update(user_id, **fields):
        patch_times.append(clock["t"])
        return await real_update(user_id, **fields)

    monkeypatch.setattr(remnawave_api, "update_user", timed_update)
    alerts = AsyncMock(return_value=True)
    monkeypatch.setattr(admin_alerts, "send_alert", alerts)

    paid_301 = now - timedelta(days=30)
    # 301: a year bought a month ago; DB row leaked to +10y → shortened too.
    http.seed_premium(301, far)
    http.seed_bypass(301, 5 * GIB)
    db.pays[301] = [pay(11, "plus_365", paid_301)]
    db.subs[301] = sub(far)
    # 302: no payments, no DB row → tomorrow.
    http.seed_premium(302, far)
    # 303: one old month (past) → tomorrow; bypass-only DB row keeps its placeholder.
    http.seed_premium(303, far)
    http.seed_bypass(303, GIB)
    db.pays[303] = [pay(31, "basic_30", now - timedelta(days=700))]
    db.subs[303] = sub(far, source="bypass_only", bypass_only=True)
    # 304: a month paid 10 days ago, but the bot accounts a later sane date (gift/balance).
    http.seed_premium(304, far)
    db.pays[304] = [pay(41, "basic_30", now - timedelta(days=10))]
    db.subs[304] = sub(now + timedelta(days=200))
    # Not candidates: a sane premium, a bypass entity, foreign usernames.
    http.seed_premium(305, now + timedelta(days=400))
    http.seed_bypass(306, GIB)
    http.seed("tg_abc_premium", tg=None, limit=0, expire_at=far)
    http.seed("tg_307_premium_old", tg=307, limit=0, expire_at=far)
    http.seed("vip_308", tg=308, limit=0, expire_at=far)
    yield SimpleNamespace(http=http, db=db, now=now, far=far, clock=clock, patch_times=patch_times,
                          alerts=alerts, target_301=extend_expiry(paid_301, 365))


def by_tg(plan):
    return {r["telegram_id"]: r for r in plan["rows"]}


def close(a, b):
    return abs((a - b).total_seconds()) < 2


async def test_dry_run_plans_by_the_rule_and_writes_nothing(w):
    plan = await premium_repair.build_plan()
    rows = by_tg(plan)
    assert set(rows) == {301, 302, 303, 304}
    assert plan["stats"] == {"panel_entities": 11, "premium_entities": 5, "candidates": 4}
    assert all(r["action"] == "would_fix" for r in rows.values())
    assert close(rows[301]["target"], w.target_301) and rows[301]["db_leaked"] is True
    assert rows[302]["fallback"] == "no_payments" and rows[303]["fallback"] == "past_date"
    assert close(rows[302]["target"], w.now + timedelta(days=1))
    assert rows[303]["db_leaked"] is False
    assert rows[304]["target_source"] == "db" and close(rows[304]["target"], w.now + timedelta(days=200))
    s = premium_repair.summarize(plan)
    assert s["plus_one_day"] == 2 and s["fallback"] == {"none": 2, "no_payments": 1, "past_date": 1}
    assert w.http.writes() == [] and w.db.records == []


async def test_apply_patches_only_expireAt_of_premium_entities_by_numeric_id(w):
    plan = await premium_repair.apply_plan(await premium_repair.build_plan())
    rows = by_tg(plan)
    assert {tg: r["action"] for tg, r in rows.items()} == {301: "fixed", 302: "fixed", 303: "fixed", 304: "fixed"}
    bodies = [r[2] for r in w.http.requests if r[0] == "PATCH"]
    assert len(bodies) == 4 and all(set(b) == {"id", "expireAt"} for b in bodies)
    assert {b["id"] for b in bodies} == {w.http.premium(tg)["id"] for tg in (301, 302, 303, 304)}
    assert close(w.http.premium_expire(301), w.target_301)
    assert close(w.http.premium_expire(304), w.now + timedelta(days=200))
    assert w.http.bypass(301)["expireAt"].year == 2099 and w.http.bypass_limit(301) == 5 * GIB
    assert w.http.premium_expire(305) < w.now + timedelta(days=401)
    # DB: the leaked date follows the panel; the bypass-only placeholder stays.
    assert close(w.db.subs[301]["expires_at"], w.target_301) and rows[301]["db_shortened"] is True
    assert w.db.subs[303]["expires_at"] == w.far and rows[303]["db_shortened"] is False
    rec = dict(w.db.records)
    assert rec[301]["proof_payment_ids"] == [11] and "bulk script" in rec[301]["reason"]
    assert rec[302]["old_expires_at"] == rows[302]["panel_expire_at"]


async def test_apply_is_paced_at_most_two_patches_per_second(w):
    await premium_repair.apply_plan(await premium_repair.build_plan())
    gaps = [b - a for a, b in zip(w.patch_times, w.patch_times[1:])]
    assert len(w.patch_times) == 4 and all(g >= 0.5 for g in gaps)


async def test_a_failing_user_is_counted_and_the_run_goes_on(w):
    bad = w.http.premium(302)["id"]
    w.http.fail("PATCH", lambda r, b: b.get("id") == bad)
    plan = await premium_repair.apply_plan(await premium_repair.build_plan())
    rows = by_tg(plan)
    assert (rows[302]["action"], rows[302]["reason"]) == ("error", "panel_patch_rejected")
    assert [rows[t]["action"] for t in (301, 303, 304)] == ["fixed"] * 3
    assert w.http.premium_expire(302) == w.far                     # failed PATCH: nothing changed
    assert 302 not in dict(w.db.records)


async def test_an_exception_for_one_user_does_not_abort(w, monkeypatch):
    real = recon.repair_premium_entity

    async def boom(tg, **kw):
        if tg == 301:
            raise RuntimeError("x")
        return await real(tg, **kw)

    monkeypatch.setattr(recon, "repair_premium_entity", boom)
    rows = by_tg(await premium_repair.apply_plan(await premium_repair.build_plan()))
    assert rows[301]["action"] == "error" and [rows[t]["action"] for t in (302, 303, 304)] == ["fixed"] * 3


async def test_limit_caps_the_patches(w):
    plan = await premium_repair.apply_plan(await premium_repair.build_plan(), limit=2)
    assert premium_repair.summarize(plan)["actions"] == {"fixed": 2, "skip": 2}
    assert len([r for r in w.http.requests if r[0] == "PATCH"]) == 2


async def test_db_is_reread_right_before_the_patch(w):
    plan = await premium_repair.build_plan()
    w.db.subs[304]["expires_at"] = w.now + timedelta(days=300)     # renewed after the scan
    rows = by_tg(await premium_repair.apply_plan(plan))
    assert close(w.http.premium_expire(304), w.now + timedelta(days=300))
    assert close(rows[304]["target"], w.now + timedelta(days=300))


async def test_purchases_past_the_panel_date_are_skipped_never_extended(w):
    panel = w.now + FIVE_Y + timedelta(days=30)
    w.http.seed_premium(309, panel)
    w.db.pays[309] = [pay(90 + i, "plus_365", w.now - timedelta(days=1)) for i in range(6)]   # 6 years
    rows = by_tg(await premium_repair.apply_plan(await premium_repair.build_plan()))
    assert (rows[309]["action"], rows[309]["reason"]) == ("skip", "would_extend")
    assert close(w.http.premium_expire(309), panel)


async def test_panel_unavailable_plans_nothing(w, monkeypatch):
    monkeypatch.setattr(remnawave_api, "get_all_users", AsyncMock(return_value=None))
    with pytest.raises(premium_repair.PanelUnavailable):
        await premium_repair.build_plan()


# ── CLI ────────────────────────────────────────────────────────────────

@pytest.fixture
def script(monkeypatch):
    import scripts.fix_premium_over_issuance as mod

    class _Conn:
        async def fetchval(self, sql):
            return 1

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(self):
                    return _Conn()

                async def __aexit__(self, *exc):
                    return False
            return _Ctx()

    monkeypatch.setattr(database.core, "get_pool", AsyncMock(return_value=_Pool()))
    # Run from outside, the CLI must never re-run migrations / inline DDL on prod.
    monkeypatch.setattr(database.core, "init_db", AsyncMock(side_effect=AssertionError("init_db called")))
    monkeypatch.setattr(mod, "_make_bot", lambda: object())
    return mod


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def test_cli_dry_run_writes_the_csv_and_changes_nothing(w, script, tmp_path, capsys):
    out = tmp_path / "r.csv"
    assert await script._main(apply=False, yes=False, limit=None, out=str(out)) == 0
    rows = read_csv(out)
    assert list(rows[0]) == premium_repair.CSV_COLUMNS
    assert {r["telegram_id"]: r["action"] for r in rows} == {str(t): "would_fix" for t in (301, 302, 303, 304)}
    assert {r["fallback"] for r in rows} == {"", "no_payments", "past_date"}
    text = capsys.readouterr().out
    assert "go to now + 1 day: 2" in text and "dry run" in text
    assert w.http.writes() == [] and w.db.records == [] and w.alerts.await_count == 0


async def test_cli_apply_without_confirmation_writes_nothing(w, script, tmp_path, monkeypatch):
    monkeypatch.setattr(script.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert await script._main(apply=True, yes=False, limit=None, out=str(tmp_path / "r.csv")) == 3
    assert w.http.writes() == [] and w.alerts.await_count == 0


async def test_cli_apply_interactive_no_writes_nothing(w, script, tmp_path, monkeypatch):
    monkeypatch.setattr(script.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "no")
    assert await script._main(apply=True, yes=False, limit=None, out=str(tmp_path / "r.csv")) == 3
    assert w.http.writes() == []


async def test_cli_apply_yes_fixes_and_sends_one_alert(w, script, tmp_path):
    out = tmp_path / "r.csv"
    assert await script._main(apply=True, yes=True, limit=None, out=str(out)) == 0
    assert {r["action"] for r in read_csv(out)} == {"fixed"}
    w.alerts.assert_awaited_once()
    assert "исправлено: 4" in w.alerts.await_args.args[2]


async def test_cli_panel_down_exits_2(w, script, tmp_path, monkeypatch):
    monkeypatch.setattr(remnawave_api, "get_all_users", AsyncMock(return_value=None))
    assert await script._main(apply=False, yes=False, limit=None, out=str(tmp_path / "r.csv")) == 2


# ── dashboard «Сверка» fix shares the rule ─────────────────────────────

FIX_KEYS = {"success", "log_id", "old_expires_at", "new_expires_at", "days_removed", "total_paid_days",
            "admin_grant_days_kept", "proof_payment_ids", "fallback_applied", "panel_updated",
            "panel_error", "is_bypass_only"}


async def test_dashboard_fix_uses_the_shared_rule(w):
    res = await recon.apply_reconciliation_fix(304, 1)
    assert FIX_KEYS <= set(res) and res["success"] is True and res["panel_updated"] is True
    assert res["fallback_applied"] is None and res["proof_payment_ids"] == [41]
    assert close(datetime.fromisoformat(res["new_expires_at"]), w.now + timedelta(days=200))
    assert close(w.http.premium_expire(304), w.now + timedelta(days=200))
    assert w.db.records[0][1]["admin_telegram_id"] == 1


async def test_dashboard_fix_never_cuts_a_legit_user(w):
    """Old code: rule date > DB date → NOW + 1 day. Now: the panel is never extended
    and a legit bot-accounted date is never cut."""
    exp = w.now + timedelta(days=200)
    w.http.seed_premium(310, exp)
    w.db.pays[310] = [pay(100, "basic_30", w.now - timedelta(days=100))]
    w.db.subs[310] = sub(exp)
    res = await recon.apply_reconciliation_fix(310, 1)
    assert (res["success"], res["error"], res["fallback_applied"]) == (False, "would_extend", "would_extend")
    assert [r for r in w.http.requests if r[0] == "PATCH"] == [] and w.db.records == []


async def test_dashboard_fix_without_a_premium_entity(w):
    res = await recon.apply_reconciliation_fix(999, 1)
    assert res["success"] is False and res["error"] == "panel_entity_unavailable"


async def test_dashboard_route_maps_would_extend_to_409(monkeypatch):
    from app.api.dashboard.routes import reconciliation as route
    monkeypatch.setattr(database, "apply_reconciliation_fix",
                        AsyncMock(return_value={"success": False, "error": "would_extend"}))
    with pytest.raises(HTTPException) as e:
        await route.apply_fix(telegram_id=5, reason="r", admin={"sub": "1"})
    assert e.value.status_code == 409
