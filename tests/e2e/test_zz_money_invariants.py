"""Randomized, seeded money invariants on the real Postgres world.

Owner: «проверь платежи несколько раз, без двойного или ненужного начисления».

A seeded RNG draws N operations across a pool of users — purchases of every
kind via every provider, duplicate / concurrent / replayed webhooks, top-ups,
balance purchases incl. double taps, auto-renewal ticks, gift buy + activation,
GB packs, trials, bad callbacks (underpaid, badly signed, wrong provider) and
panel outages — through the REAL code (bot UI, webhooks, workers). The test
keeps its own ledger: it never predicts whether an operation succeeds; it books
money and GB only on DB evidence (a payments row appeared, the balance moved by
exactly the price, the receiver's subscription moved). Then, after the panel
recovered, it asserts GLOBAL invariants:

  I1 every paid purchase_id has exactly one payments row, of the paid amount;
     no purchase_id is paid twice
  I2 no negative balance; each balance == the ledger (top-ups credited −
     balance purchases − auto-renewals) + referral cashback received
  I3 referral cashback at most once per (buyer, purchase) and never for a top-up
  I4 each user's bypass limit == Σ rule-based grants (Basic/Plus +10 GB once,
     Combo table, packs +N, trial 500 MB, day grants 0)
  I5 premium expiry never goes backwards (DB and panel) and panel == DB at the end
  I6 every abnormal operation alerted the admin; normal ones did not

  E2E_SEED=<int>  E2E_OPS=<int>  pytest tests/e2e/test_zz_money_invariants.py

On failure the seed and the operation log are in the assertion message.
Flag off: the generator does not draw the known legacy cells (matrix ids in
FLAG_OFF_KNOWN), it never weakens an invariant.
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional

import pytest

import auto_renewal
import config
import wata_service
from tests.e2e import flows
from tests.e2e.world import GIB, MB, UTC, new_user, utcnow
from tests.fakes import providers_http as prov
from tests.fakes import telegram as tgf

SEED = int(os.getenv("E2E_SEED", "20260914"))
OPS = int(os.getenv("E2E_OPS", "200"))
N_USERS = 8
TARIFFS = [("basic", 30), ("plus", 90), ("combo_basic", 30), ("combo_plus", 30)]
WEBHOOK_METHODS = ["sbp", "wata", "crypto"]
# Flag off: cells that are KNOWN legacy deviations (docs/audit/03_payment_matrix.md §4)
# — not drawn in the flag-off run; the flag-on run draws everything.
FLAG_OFF_KNOWN = {
    ("card", "combo", "new"): "T0-TG-COMBO-NEW",
    ("stars", "combo", "new"): "T0-TG-COMBO-NEW",
    ("balance", "combo", "any"): "T0-BAL-COMBO-NEW / T0-BAL-COMBO-RENEW",
    ("autorenew", "plus", "any"): "T0-AUTORENEW-PLUS",
    ("autorenew", "combo", "any"): "T0-AUTORENEW-COMBO",
}


def gb_gain(tariff: str, period: int) -> int:
    if tariff.startswith("combo_"):
        return config.COMBO_TARIFFS[tariff][period]["gb"] * GIB
    return 10 * GIB


@dataclass
class Ledger:
    gb: Dict[int, int] = field(default_factory=dict)          # expected bypass bytes
    balance: Dict[int, int] = field(default_factory=dict)     # kopecks, without cashback
    paid: Dict[str, int] = field(default_factory=dict)        # purchase_id → paid kopecks
    topups: set = field(default_factory=set)                  # top-up purchase ids
    hooks: List[tuple] = field(default_factory=list)          # delivered provider callbacks
    premium_seen: Dict[int, object] = field(default_factory=dict)
    panel_seen: Dict[int, object] = field(default_factory=dict)
    gifts: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)


class Run:
    def __init__(self, e2e, rng: random.Random, flag: str):
        self.w, self.rng, self.flag = e2e, rng, flag
        self.L = Ledger()
        self.users: List[tgf.TgUser] = []
        self.referrer: Optional[tgf.TgUser] = None

    # ── helpers ──────────────────────────────────────────────────────
    def note(self, msg: str) -> None:
        self.L.log.append(msg)

    async def kopecks(self, tg: int) -> int:
        return int(await self.w.val("SELECT balance FROM users WHERE telegram_id=$1", tg))

    async def pay_rows(self, pid: str) -> int:
        return int(await self.w.val("SELECT count(*) FROM payments WHERE purchase_id=$1", pid))

    def premium_state(self, tg):
        return self.w.panel.premium_expire(tg)

    async def check_all_monotonic(self) -> None:
        """I5 after every operation — one query for every user."""
        rows = await self.w.rows(
            "SELECT telegram_id, expires_at, is_bypass_only FROM subscriptions WHERE telegram_id = ANY($1::bigint[])",
            [u.id for u in self.users])
        by_tg = {r["telegram_id"]: r for r in rows}
        for u in self.users:
            await self.check_monotonic(u.id, by_tg.get(u.id))

    async def check_monotonic(self, tg: int, sub=None) -> None:
        if sub is not None and sub.get("expires_at") is not None:
            sub = {**sub, "expires_at": sub["expires_at"].astimezone(UTC)
                   if sub["expires_at"].tzinfo else sub["expires_at"].replace(tzinfo=UTC)}
        exp = sub["expires_at"] if sub and not sub.get("is_bypass_only") else None
        prev = self.L.premium_seen.get(tg)
        if exp is not None and prev is not None:
            assert exp >= prev, f"I5 DB premium went backwards for {tg}: {prev} → {exp}"
        if exp is not None:
            self.L.premium_seen[tg] = exp
        pexp = self.premium_state(tg)
        pprev = self.L.panel_seen.get(tg)
        if pexp is not None and pprev is not None and not self.w.panel.down:
            assert pexp >= pprev, f"I5 panel premium went backwards for {tg}: {pprev} → {pexp}"
        if pexp is not None:
            self.L.panel_seen[tg] = pexp

    @staticmethod
    def held_alerts() -> int:
        """Alerts the outbox / webhook budget held for the digest (5 immediate per
        300 s window, then ONE digest — «never a flood, never dropped»)."""
        from app.services import provisioning
        return sum(provisioning.pending_alert_counts().values())

    def alerted_since(self, mark: int, held_before: int) -> bool:
        return bool(self.w.admin_texts(mark)) or self.held_alerts() > held_before

    def expect_gb(self, tg: int, add: int) -> None:
        self.L.gb[tg] = self.L.gb.get(tg, 0) + add

    def has_sub(self, tg: int) -> bool:
        return tg in self.L.premium_seen

    # ── setup ────────────────────────────────────────────────────────
    async def setup(self) -> None:
        self.referrer = new_user()
        await self.w.start_user(self.referrer)
        code = await self.w.val("SELECT referral_code FROM users WHERE telegram_id=$1", self.referrer.id)
        self.L.balance[self.referrer.id] = 0
        for i in range(N_USERS):
            u = new_user()
            if i % 2 == 0:
                await self.w.start_user(u, f"ref_{code}")          # referred users
            else:
                await self.w.register(u)
            self.users.append(u)
            self.L.balance[u.id] = 0
        self.w.provisioning(self.flag)

    # ── operations ───────────────────────────────────────────────────
    async def op_webhook_purchase(self, u, *, panel_down=False) -> None:
        method = self.rng.choice(WEBHOOK_METHODS)
        tariff, period = self.rng.choice(TARIFFS)
        p = await self.w.create_purchase(u, tariff, period, provider=flows.METHOD_PROVIDER[method])
        amount = p["price_kopecks"] / 100
        hook = {"sbp": lambda: prov.platega_webhook(p["purchase_id"], amount),
                "wata": lambda: prov.wata_webhook(p["purchase_id"], amount),
                "crypto": lambda: prov.cryptobot_webhook(p["purchase_id"], amount)}[method]()
        mark = self.w.tg.mark()
        held0 = self.held_alerts()
        concurrent = self.rng.random() < 0.25
        gb_before = self.w.panel.bypass_limit(u.id) or 0
        if panel_down:
            self.w.panel.down = True
        try:
            if concurrent:                                     # the provider fires twice at once
                await asyncio.gather(self.w.webhook(hook, settle=False), self.w.webhook(hook, settle=False))
                await self.w.settle()
            else:
                await self.w.webhook(hook)
        finally:
            self.w.panel.down = False
        if panel_down:
            await self.recover()
        n = await self.pay_rows(p["purchase_id"])
        assert n <= 1, f"I1 purchase {p['purchase_id']} paid {n} times"
        if n == 1:
            self.L.paid[p["purchase_id"]] = p["price_kopecks"]
            self.L.hooks.append((hook, p["purchase_id"]))
            gain = gb_gain(tariff, period)
            delivered = (self.w.panel.bypass_limit(u.id) or 0) - gb_before
            alerted = any(p["purchase_id"] in t for t in self.w.admin_texts(mark))
            if panel_down and self.flag == "off" and delivered == 0 and alerted:
                # documented legacy rule (matrix §2.4 gb_or_alert): add_bypass_traffic is not
                # retried (double-add risk); the forced alert asks the admin to add them
                self.note(f"legacy: {gain // GIB} GB not delivered, admin alerted ({p['purchase_id']})")
            else:
                self.expect_gb(u.id, gain)
        if panel_down:
            assert self.alerted_since(mark, held0), f"I6 panel outage during {method} purchase was silent"
        self.note(f"purchase {method} {tariff}{period} u={u.id} concurrent={concurrent} "
                  f"panel_down={panel_down} paid={n}")

    async def op_replay(self) -> None:
        if not self.L.hooks:
            return
        hook, pid = self.rng.choice(self.L.hooks)
        tg = int(await self.w.val("SELECT telegram_id FROM pending_purchases WHERE purchase_id=$1", pid))
        bal0 = await self.kopecks(tg)
        pays0 = int(await self.w.val("SELECT count(*) FROM payments WHERE telegram_id=$1", tg))
        gb0 = self.w.panel.bypass_limit(tg)
        res = await self.w.webhook(hook)
        # top-up payments rows carry no purchase_id: judge every replay by its effects
        assert await self.kopecks(tg) == bal0, f"I2 replay of {pid} moved the balance"
        assert int(await self.w.val("SELECT count(*) FROM payments WHERE telegram_id=$1", tg)) == pays0, (
            f"I1 replay of {pid} added a payments row")
        assert self.w.panel.bypass_limit(tg) == gb0, f"I4 replay of {pid} changed the GB"
        assert res.status in (200, 500), res
        self.note(f"replay {pid} → {res.status} {res.body}")

    async def op_ui_purchase(self, u, method: str) -> None:
        tariff, period = self.rng.choice(TARIFFS)
        combo = tariff.startswith("combo_")
        state = "new" if not self.has_sub(u.id) else "any"
        if self.flag == "off" and ((method, "combo" if combo else "x", state) in FLAG_OFF_KNOWN
                                   or (method == "balance" and combo)):
            tariff, period = "basic", 30
        if method == "card":
            self.w.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", "")
        if method == "balance":
            price = int(flows.tariff_price(tariff, period) * 100)
            if self.rng.random() < 0.7:                        # sometimes top up the difference first
                await self.w.pool.execute("UPDATE users SET balance = balance + $2 WHERE telegram_id=$1", u.id, price)
                self.L.balance[u.id] += price
            before = await self.kopecks(u.id)
            await flows.open_payment_screen(self.w, u, tariff, period)
            if self.rng.random() < 0.4:                        # double tap
                await asyncio.gather(self.w._post_update(tgf.callback(u, "pay:balance"), settle=False),
                                     self.w._post_update(tgf.callback(u, "pay:balance"), settle=False))
                await self.w.settle()
            else:
                await self.w.tap(u, "pay:balance")
            delta = await self.kopecks(u.id) - before
            assert delta in (0, -price), f"I2 balance purchase moved {delta} (price {price})"
            if delta == -price:
                self.L.balance[u.id] -= price
                self.expect_gb(u.id, gb_gain(tariff, period))
            self.note(f"balance {tariff}{period} u={u.id} delta={delta}")
            return
        try:
            pending = await flows.buy(self.w, u, tariff, period, method)
            await flows.pay(self.w, u, pending, method)
        finally:
            if method == "card":                               # never leave WATA disabled for later ops
                self.w.mp.setattr(wata_service, "WATA_ACCESS_TOKEN", prov.WATA_TOKEN)
        n = await self.pay_rows(pending["purchase_id"])
        assert n <= 1
        if n == 1:
            self.L.paid[pending["purchase_id"]] = int(await self.w.val(
                "SELECT amount FROM payments WHERE purchase_id=$1", pending["purchase_id"]))
            self.expect_gb(u.id, gb_gain(tariff, period))
        self.note(f"ui {method} {tariff}{period} u={u.id} paid={n}")

    async def op_topup(self, u) -> None:
        amount = self.rng.choice([250, 750, 999])
        button = self.rng.choice(["topup_sbp", "topup_wata"])
        last_id = await self.w.val("SELECT COALESCE(max(id), 0) FROM pending_purchases WHERE telegram_id=$1", u.id)
        await self.w.tap(u, "topup_balance")
        await self.w.tap(u, f"topup_amount:{amount}")
        await self.w.tap(u, f"{button}:{amount}")
        p = await flows.latest_pending(self.w, u.id)
        if not p or p["id"] <= last_id or p["purchase_type"] != "balance_topup":
            # the screen created no invoice: never "pay" an older row instead
            self.note(f"topup {button} {amount} u={u.id}: NO INVOICE" + await flows.screen_evidence(self.w, u))
            return
        charged = p["price_kopecks"] / 100
        hook = (prov.platega_webhook(p["purchase_id"], charged) if button == "topup_sbp"
                else prov.wata_webhook(p["purchase_id"], charged))
        credit = int(p.get("credit_kopecks") or p["price_kopecks"])
        bal0 = await self.kopecks(u.id)
        pays0 = int(await self.w.val("SELECT count(*) FROM payments WHERE telegram_id=$1", u.id))
        await self.w.webhook(hook)
        delta = await self.kopecks(u.id) - bal0
        new_pays = int(await self.w.val("SELECT count(*) FROM payments WHERE telegram_id=$1", u.id)) - pays0
        # evidence = the balance itself: credited exactly once, or not at all
        assert delta in (0, credit), f"I2 top-up {p['purchase_id']} moved the balance by {delta} (credit {credit})"
        assert new_pays == (1 if delta else 0), f"I1 top-up {p['purchase_id']}: {new_pays} payments rows"
        if delta:
            self.L.balance[u.id] += credit
            self.L.topups.add(p["purchase_id"])
            self.L.hooks.append((hook, p["purchase_id"]))
            if await self.pay_rows(p["purchase_id"]) == 1:
                self.L.paid[p["purchase_id"]] = p["price_kopecks"]
        self.note(f"topup {button} {amount} u={u.id} pid={p['purchase_id']} credited={delta}")

    async def op_pack(self, u) -> None:
        gb = self.rng.choice([15, 50])
        p = await self.w.create_purchase(u, f"traffic_{gb}gb", 0, provider="platega",
                                         purchase_type="traffic_pack",
                                         price_rub=config.TRAFFIC_PACKS[gb]["price"])
        hook = prov.platega_webhook(p["purchase_id"], p["price_kopecks"] / 100)
        await self.w.webhook(hook)
        if await self.pay_rows(p["purchase_id"]) == 1:
            self.L.paid[p["purchase_id"]] = p["price_kopecks"]
            self.expect_gb(u.id, gb * GIB)
            self.L.hooks.append((hook, p["purchase_id"]))
        self.note(f"pack {gb} u={u.id}")

    async def op_gift(self, buyer, receiver) -> None:
        g = await self.w.create_purchase(buyer, "basic", 30, provider="platega", purchase_type="gift")
        hook = prov.platega_webhook(g["purchase_id"], g["price_kopecks"] / 100)
        await self.w.webhook(hook)
        if await self.pay_rows(g["purchase_id"]) != 1:
            return
        self.L.paid[g["purchase_id"]] = g["price_kopecks"]
        self.L.hooks.append((hook, g["purchase_id"]))
        code = await self.w.val("SELECT gift_code FROM gift_subscriptions WHERE purchase_id=$1", g["purchase_id"])
        assert code, "gift paid but no code"
        before = await self.w.sub(receiver.id)
        await self.w.send(receiver, f"/start gift_{code}")
        await self.w.send(receiver, f"/start gift_{code}")      # the link opened twice
        after = await self.w.sub(receiver.id)
        if after and (before is None or after["expires_at"] > before["expires_at"]):
            self.expect_gb(receiver.id, 10 * GIB)
        self.note(f"gift {buyer.id}→{receiver.id} code={code}")

    async def op_trial(self) -> None:
        u = new_user()
        await self.w.start_user(u)
        from app.core import rate_limit
        self.w.mp.setattr(rate_limit, "_rate_limiter", None)
        await self.w.tap(u, "activate_trial")
        await self.w.tap(u, "activate_trial")                   # second tap
        self.users.append(u)
        self.L.balance[u.id] = 0
        if await self.w.sub(u.id):
            self.expect_gb(u.id, 500 * MB)
        self.note(f"trial u={u.id}")

    async def op_autorenew(self) -> None:
        cands = [u for u in self.users if self.has_sub(u.id)]
        if not cands:
            return
        u = self.rng.choice(cands)
        sub = await self.w.sub(u.id)
        if not sub or sub.get("is_bypass_only") or sub["source"] == "trial":
            return
        tier = (sub["subscription_type"] or "basic")
        is_combo = bool(sub.get("is_combo"))
        if self.flag == "off" and (tier == "plus" or is_combo):
            return                                             # FLAG_OFF_KNOWN T0-AUTORENEW-*
        # move the end into the renewal window (a manipulation → new baseline)
        due = utcnow() + timedelta(hours=3)
        await self.w.set_expiry(u.id, due)
        self.L.premium_seen[u.id] = due
        self.L.panel_seen[u.id] = due
        await self.w.pool.execute("UPDATE subscriptions SET auto_renew=TRUE, last_auto_renewal_at=NULL "
                                  "WHERE telegram_id=$1", u.id)
        await self.w.pool.execute("UPDATE users SET balance = balance + 1000000 WHERE telegram_id=$1", u.id)
        self.L.balance[u.id] += 1_000_000
        before_bal = await self.kopecks(u.id)
        n_before = int(await self.w.val("SELECT count(*) FROM payments WHERE telegram_id=$1", u.id))
        await auto_renewal.process_auto_renewals(self.w.bot)
        await self.w.settle()
        await self.w.provisioning_tick()
        n_after = int(await self.w.val("SELECT count(*) FROM payments WHERE telegram_id=$1", u.id))
        charged = before_bal - await self.kopecks(u.id)
        assert n_after - n_before in (0, 1), "I1 one auto-renewal tick renewed twice"
        if n_after - n_before == 1:
            row = await self.w.row("SELECT tariff, amount FROM payments WHERE telegram_id=$1 ORDER BY id DESC LIMIT 1", u.id)
            assert charged == row["amount"], f"I2 auto-renewal charged {charged}, payments says {row['amount']}"
            self.L.balance[u.id] -= charged
            key = row["tariff"] or ""
            tariff = ("combo_" if is_combo else "") + ("plus" if tier != "basic" else "basic")
            period = int(key.rsplit("_", 1)[-1]) if key.rsplit("_", 1)[-1].isdigit() else 30
            self.expect_gb(u.id, gb_gain(tariff, period))     # owner rule: the real tariff's GB
        else:
            assert charged == 0, f"I2 no renewal but {charged} kopecks charged"
        self.note(f"autorenew u={u.id} tier={tier} combo={is_combo} renewed={n_after - n_before}")

    async def op_bad_callback(self, u) -> None:
        kind = self.rng.choice(["underpaid", "bad_signature", "wrong_provider"])
        p = await self.w.create_purchase(u, "basic", 30, provider="platega" if kind != "bad_signature" else "wata")
        mark = self.w.tg.mark()
        held0 = self.held_alerts()
        if kind == "underpaid":
            await self.w.webhook(prov.platega_webhook(p["purchase_id"], 100.0))
        elif kind == "bad_signature":
            await self.w.webhook(prov.wata_webhook(p["purchase_id"], 199.0, bad_signature=True))
        else:
            await self.w.webhook(prov.cryptobot_webhook(p["purchase_id"], 199.0))
        assert await self.pay_rows(p["purchase_id"]) == 0, f"I1 {kind} callback credited"
        assert self.alerted_since(mark, held0), f"I6 {kind} callback did not alert the admin"
        self.note(f"bad {kind} u={u.id}")

    # ── driver ───────────────────────────────────────────────────────
    async def run(self) -> None:
        await self.setup()
        ops = ["purchase"] * 6 + ["ui_card", "ui_stars", "ui_balance", "ui_balance"] + \
              ["topup", "topup", "replay", "replay", "pack", "gift", "trial", "autorenew", "bad", "panel_down"]
        from app.core import rate_limit
        import time as _time
        self.timing: Dict[str, List[float]] = {}
        for i in range(OPS):
            op = self.rng.choice(ops)
            u = self.rng.choice(self.users)
            _t0 = _time.perf_counter()
            # real operations are spread over time: neither the per-user payment_init
            # limit (5 / 60 s) nor the dispatcher's GlobalRateLimitMiddleware
            # (30 updates / 60 s, flood ban) — both covered elsewhere — may drop a
            # compressed random run's taps
            self.w.mp.setattr(rate_limit, "_rate_limiter", None)
            for rl in self.w.dp._e2e_rate_limiters:
                rl._user_requests.clear()
                rl._banned_users.clear()
            mark = self.w.tg.mark()
            normal = op not in ("bad", "panel_down")
            if op == "purchase":
                await self.op_webhook_purchase(u)
            elif op == "panel_down":
                await self.op_webhook_purchase(u, panel_down=True)
            elif op == "replay":
                await self.op_replay()
            elif op.startswith("ui_"):
                await self.op_ui_purchase(u, op[3:])
            elif op == "topup":
                await self.op_topup(u)
            elif op == "pack":
                await self.op_pack(u)
            elif op == "gift":
                await self.op_gift(u, self.rng.choice([x for x in self.users if x.id != u.id]))
            elif op == "trial":
                await self.op_trial()
            elif op == "autorenew":
                await self.op_autorenew()
            elif op == "bad":
                await self.op_bad_callback(u)
            if normal:
                assert self.w.admin_texts(mark) == [], f"I6 normal op {op} alerted: {self.w.admin_texts(mark)}"
            await self.check_all_monotonic()
            self.timing.setdefault(op, []).append(_time.perf_counter() - _t0)
        await self.recover()
        # I6 end of run: every alert held by the budget is DELIVERED as a digest
        from app.services import provisioning
        held = self.held_alerts()
        mark = self.w.tg.mark()
        await provisioning.flush_alert_digests(self.w.bot, final=True)
        await self.w.settle()
        assert self.held_alerts() == 0, f"I6 alerts still held after the final digest: {provisioning.pending_alert_counts()}"
        if held:
            assert self.w.admin_texts(mark), f"I6 {held} held alerts, but no digest reached the admin"
        self.note(f"end: {held} held alerts delivered as digest")

    async def recover(self) -> None:
        self.w.panel.clear_failures()
        await self.w.release_resync()
        self.w.resync_gate.clear()
        await self.w.pool.execute("UPDATE provisioning_jobs SET next_attempt_at = NOW() - interval '1 second' "
                                  "WHERE status = 'pending'")
        await self.w.provisioning_tick()

    async def evidence(self, tg: int) -> str:
        pays = await self.w.rows("SELECT purchase_id, tariff, amount FROM payments WHERE telegram_id=$1 ORDER BY id", tg)
        pend = await self.w.rows(
            "SELECT purchase_id, purchase_type, tariff, status, price_kopecks, credit_kopecks "
            "FROM pending_purchases WHERE telegram_id=$1 ORDER BY id", tg)
        tx = await self.w.rows("SELECT amount, type, source FROM balance_transactions WHERE user_id=$1 ORDER BY id", tg)
        ops = [line for line in self.L.log if f"u={tg}" in line or f"→{tg}" in line]
        return (f"\n  payments={pays}\n  pending={pend}\n  balance_tx={tx}\n  ops={ops}")

    # ── global invariants ────────────────────────────────────────────
    async def check(self) -> None:
        w = self.w
        # I1
        dup = await w.rows("SELECT purchase_id, count(*) AS n FROM payments WHERE purchase_id IS NOT NULL "
                           "GROUP BY 1 HAVING count(*) > 1")
        assert dup == [], f"I1 purchase ids paid twice: {dup}"
        for pid, kop in self.L.paid.items():
            row = await w.row("SELECT amount FROM payments WHERE purchase_id=$1", pid)
            assert row and int(row["amount"]) == kop, f"I1 {pid}: payments {row} != paid {kop}"
            assert await w.val("SELECT status FROM pending_purchases WHERE purchase_id=$1", pid) == "paid"
        # I3
        rew = await w.rows("SELECT buyer_id, purchase_id, count(*) AS n FROM referral_rewards GROUP BY 1, 2")
        assert all(r["n"] == 1 for r in rew), f"I3 cashback twice: {rew}"
        assert not [r for r in rew if r["purchase_id"] in self.L.topups], "I3 cashback for a top-up"
        # I2
        everyone = [self.referrer] + self.users
        for u in everyone:
            bal = await self.kopecks(u.id)
            assert bal >= 0, f"I2 negative balance {u.id}: {bal}"
            cashback = await w.val("SELECT COALESCE(SUM(reward_amount), 0) FROM referral_rewards WHERE referrer_id=$1",
                                   u.id)
            cash_k = int(round(float(cashback) * (100 if float(cashback) and float(cashback) < 1000 else 1)))
            expected = self.L.balance.get(u.id, 0)
            assert bal in (expected + int(float(cashback)), expected + cash_k), (
                f"I2 balance {u.id}: {bal} != ledger {expected} + cashback {cashback}" + await self.evidence(u.id))
        # I4 + I5 (final)
        for u in self.users:
            got = w.panel.bypass_limit(u.id) or 0
            assert got == self.L.gb.get(u.id, 0), (
                f"I4 bypass {u.id}: {got / GIB:.2f} GB != rules {self.L.gb.get(u.id, 0) / GIB:.2f} GB"
                + await self.evidence(u.id))
            sub = await w.sub(u.id)
            if sub and sub["status"] == "active" and not sub.get("is_bypass_only") and sub["expires_at"] > utcnow():
                pexp = w.panel.premium_expire(u.id)
                assert pexp is not None and abs((pexp - sub["expires_at"]).total_seconds()) <= 1.5, (
                    f"I5 panel {pexp} != DB {sub['expires_at']} for {u.id}")


@pytest.mark.parametrize("flag", ["on", "off"])
async def test_money_invariants_under_random_operations(e2e, flag):
    rng = random.Random(f"{SEED}-{flag}")
    run = Run(e2e, rng, flag)
    try:
        await run.run()
        await run.check()
        if os.getenv("E2E_PROFILE"):
            rows = sorted(((sum(v), len(v), k) for k, v in run.timing.items()), reverse=True)
            print(f"\nPROFILE flag={flag} settle_timeouts={e2e.settle_timeouts} " + " ".join(
                f"{k}={tot:.1f}s/{n}" for tot, n, k in rows))
            print(f"LINGERING flag={flag} {e2e.lingering_counts}")
    except AssertionError as e:
        tail = "\n".join(run.L.log[-25:])
        raise AssertionError(f"seed={SEED} flag={flag} ops={OPS}: {e}\n--- last operations ---\n{tail}") from e
