"""E2E world: the real bot (aiogram dispatcher + FastAPI app + workers) on a
real PostgreSQL, with only the outside world faked.

REAL: database.* on Postgres 16 (migrations + inline DDL via init_db), every
handler / middleware / router, payment webhooks, confirmation, provisioning,
remnawave_* clients, workers.
FAKE: Telegram Bot API (tests/fakes/telegram.FakeTelegram), Remnawave 3.4.3
REST (tests/fakes/remnawave_http), provider APIs + webhook signatures
(tests/fakes/providers_http).

One World per test (fresh schema contents, fresh fakes).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

import config
import database
from tests.fakes import telegram as tgf
from tests.fakes.providers_http import FakeProviders
from tests.fakes.remnawave_http import GIB, FakeRemnawaveHTTP

UTC = timezone.utc
MB = 1024 ** 2
ADMIN = int(config.ADMIN_TELEGRAM_ID)
WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"

_user_ids = itertools.count(20_001)


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def new_user(lang: str = "ru", username: Optional[str] = None) -> tgf.TgUser:
    uid = next(_user_ids)
    return tgf.TgUser(id=uid, first_name=f"User{uid}", username=username or f"user{uid}",
                      language_code=lang)


# ── dispatcher / app singletons (routers can be attached only once) ─────

_DP = None
_APP = None


def dispatcher():
    """Dispatcher wired exactly like main.main(): same middleware chain, one
    root router. Built once per process; FSM storage + rate-limit memory are
    reset per test by World."""
    global _DP
    if _DP is not None:
        return _DP
    import app.utils.button_defaults  # noqa: F401  (main.py imports it before any handler)
    from aiogram import Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.core.chat_filter_middleware import PrivateChatOnlyMiddleware
    from app.core.concurrency_middleware import ConcurrencyLimiterMiddleware
    from app.core.last_seen_middleware import LastSeenMiddleware
    from app.core.rate_limit_middleware import GlobalRateLimitMiddleware
    from app.core.telegram_error_middleware import TelegramErrorBoundaryMiddleware
    from app.handlers import router as root_router

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(ConcurrencyLimiterMiddleware(asyncio.Semaphore(20)))
    dp.update.middleware(TelegramErrorBoundaryMiddleware())
    dp.message.middleware(PrivateChatOnlyMiddleware())
    dp.callback_query.middleware(PrivateChatOnlyMiddleware())
    rl_msg, rl_cb = GlobalRateLimitMiddleware(), GlobalRateLimitMiddleware()
    dp.message.middleware(rl_msg)
    dp.callback_query.middleware(rl_cb)
    dp.message.middleware(LastSeenMiddleware())
    dp.callback_query.middleware(LastSeenMiddleware())
    if root_router.parent_router is not None:   # a hermetic test attached it before
        root_router._parent_router = None
    dp.include_router(root_router)
    dp._e2e_rate_limiters = (rl_msg, rl_cb)
    _DP = dp
    return dp


def fastapi_app():
    """The production FastAPI routes (app.api.app: Telegram webhook, payment
    webhooks, deeplinks, /health) + the dashboard routers mounted the way
    app/api/__init__.py mounts them when DASHBOARD_ENABLED (it was imported
    with the dashboard disabled in the hermetic test env)."""
    global _APP
    if _APP is not None:
        return _APP
    from fastapi import FastAPI

    from app.api import RequestSizeLimitMiddleware
    from app.api import app as real_app
    from app.api import dashboard as dash

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestSizeLimitMiddleware, max_size=1 * 1024 * 1024)
    app.router.routes.extend(real_app.router.routes)
    if not any(getattr(r, "path", "").startswith("/dashboard/api") for r in real_app.router.routes):
        app.include_router(dash.router, prefix="/dashboard/api")
    _APP = app
    return app


@dataclass
class Hook:
    status: int
    body: Any


class World:
    def __init__(self, pool, monkeypatch):
        self.pool = pool
        self.mp = monkeypatch
        self.tg = tgf.FakeTelegram()
        from aiogram import Bot
        self.bot = Bot(token="7000000001:E2E-TEST-TOKEN", session=self.tg)
        from app.utils.telegram_request_middleware import install as install_request_middlewares
        install_request_middlewares(self.bot)          # as main.main() does
        self.panel = FakeRemnawaveHTTP().install(monkeypatch)
        self.providers = FakeProviders().install(monkeypatch)
        self.dp = dispatcher()
        self.app = fastapi_app()
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                      base_url="https://testserver")
        self.resync_gate = asyncio.Event()
        self.settle_timeouts = 0           # settle() gave up waiting (a lingering task)
        self.lingering: List[str] = []
        self.lingering_counts: Dict[str, int] = {}

    # ── lifecycle ────────────────────────────────────────────────────
    def setup(self) -> None:
        from aiogram.fsm.storage.memory import MemoryStorage

        from app.api import payment_webhook, telegram_webhook
        mp = self.mp
        self.dp.fsm.storage = MemoryStorage()
        for rl in self.dp._e2e_rate_limiters:
            rl._user_requests.clear()
            rl._banned_users.clear()
        mp.setattr(telegram_webhook, "_bot", self.bot)
        mp.setattr(telegram_webhook, "_dp", self.dp)
        mp.setattr(payment_webhook, "_bot", self.bot)
        # prod-like: no stage gate, no Redis, dashboard on
        mp.setattr(config, "IS_STAGE", False)
        mp.setattr(config, "REDIS_URL", "")
        mp.setattr(config, "JWT_SECRET", "e2e-jwt-secret-" + "x" * 40)
        mp.setattr(config, "DASHBOARD_BASE_URL", "https://testserver")
        mp.setattr(config, "DASHBOARD_ENABLED", True)
        mp.setenv(MODE_VAR, "off")
        mp.delenv(EP_VAR, raising=False)
        # process-level memory that would leak between tests
        from app.core import rate_limit
        mp.setattr(rate_limit, "_rate_limiter", None)
        from app.services import admin_alerts, dashboard_cache, purchase_flow
        admin_alerts._last_alert_at.clear()
        admin_alerts._digest_failures.clear()
        dashboard_cache.clear()
        purchase_flow._forced_alert_times.clear()
        from app.services.payments import verify_delivery
        verify_delivery._alerted.clear()
        from app.api.dashboard import security
        security.clear_memory_state()
        from app.services import sbp_router
        mp.setattr(sbp_router, "_cache_expires_at", 0.0)
        try:
            from app.services import pricing
            pricing.touch_cache()
        except Exception:
            pass

        gate = self.resync_gate

        async def gated_resync_sleep(_seconds):
            await gate.wait()
        mp.setattr(purchase_flow, "_resync_sleep", gated_resync_sleep)

        # Time compression: every sleep in the code under test is ~instant
        # (verify-delivery delays, invoice auto-delete, retry backoff).
        real_sleep = asyncio.sleep

        async def fast_sleep(delay=0, result=None):
            return await real_sleep(0, result)
        mp.setattr(asyncio, "sleep", fast_sleep)
        self._real_sleep = real_sleep

    async def close(self) -> None:
        from app.services import purchase_flow
        await self.settle()
        tasks = list(purchase_flow._resync_tasks.values())
        for t in tasks:
            t.cancel()
        me = asyncio.current_task()
        others = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
        for t in others:
            t.cancel()
        if others:
            await asyncio.gather(*others, return_exceptions=True)
        purchase_flow._resync_tasks.clear()
        await self.http.aclose()

    async def settle(self, timeout: float = 5.0) -> None:
        """Wait for fire-and-forget tasks the code spawned (verify delivery,
        background GB top-ups, notifications). Tasks parked on the legacy
        re-sync gate are ignored until release_resync()."""
        from app.services import purchase_flow
        me = asyncio.current_task()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            parked = set(purchase_flow._resync_tasks.values()) if not self.resync_gate.is_set() else set()
            pending = [t for t in asyncio.all_tasks() if t is not me and not t.done() and t not in parked
                       and not getattr(t, "_e2e_daemon", False)]
            if not pending:
                return
            await asyncio.wait(pending, timeout=0.05)
        self.settle_timeouts += 1
        self.lingering = sorted({getattr(t.get_coro(), "__qualname__", repr(t)) for t in pending})
        for name in self.lingering:
            self.lingering_counts[name] = self.lingering_counts.get(name, 0) + 1

    async def release_resync(self) -> None:
        self.resync_gate.set()
        await self.settle()

    # ── provisioning flag ────────────────────────────────────────────
    def provisioning(self, mode: str, entrypoints: Optional[str] = None) -> None:
        if mode == "off":
            self.mp.setenv(MODE_VAR, "off")
            self.mp.delenv(EP_VAR, raising=False)
            return
        self.mp.setenv(MODE_VAR, mode)
        if entrypoints:
            self.mp.setenv(EP_VAR, entrypoints)
        else:
            self.mp.delenv(EP_VAR, raising=False)

    async def provisioning_tick(self) -> None:
        """One provisioning-worker tick (drains due outbox jobs), as the
        always-running worker would do within INTERVAL_S."""
        from app.workers import provisioning_worker
        await provisioning_worker.run_tick(self.bot)
        await self.settle()

    async def run_worker_iterations(self, module, task_fn, *, iterations: int = 1) -> None:
        """Run a production worker loop (`while True: … await asyncio.sleep(INTERVAL)`)
        for exactly `iterations` iterations: the startup jitter sleep is
        skipped, the (iterations+1)-th long sleep cancels the task — the same
        code the process runs, one tick at a time."""
        real_asyncio = asyncio
        real_sleep = self._real_sleep
        state = {"long_sleeps": 0}

        class _AsyncioProxy:
            def __getattr__(self, name):
                return getattr(real_asyncio, name)

            async def sleep(self, delay=0, result=None):
                if delay and delay >= 1:
                    state["long_sleeps"] += 1
                    if state["long_sleeps"] > iterations:
                        raise real_asyncio.CancelledError()
                return await real_sleep(0, result)

        self.mp.setattr(module, "asyncio", _AsyncioProxy())
        task = real_asyncio.ensure_future(task_fn(self.bot))
        try:
            await real_asyncio.wait_for(task, timeout=30)
        except real_asyncio.CancelledError:
            pass
        finally:
            self.mp.setattr(module, "asyncio", real_asyncio)
        await self.settle()

    async def jobs(self, tg: int) -> List[Dict[str, Any]]:
        return await self.rows("SELECT * FROM provisioning_jobs WHERE telegram_id=$1 ORDER BY id", tg)

    # ── Telegram in ──────────────────────────────────────────────────
    async def _post_update(self, update: Dict[str, Any], *, settle: bool = True) -> int:
        """settle=False for calls run CONCURRENTLY (asyncio.gather): the caller
        settles once afterwards — settling inside each branch made the branches
        wait for each other (and for the test task) until settle's timeout."""
        r = await self.http.post("/telegram/webhook", json=update,
                                 headers={WEBHOOK_SECRET_HEADER: config.WEBHOOK_SECRET})
        if settle:
            await self.settle()
        return r.status_code

    async def send(self, user: tgf.TgUser, text: str) -> int:
        return await self._post_update(tgf.message(user, text))

    async def tap(self, user: tgf.TgUser, data: str, *, message_id: Optional[int] = None) -> int:
        return await self._post_update(tgf.callback(user, data, message_id=message_id))

    async def pay_telegram(self, user: tgf.TgUser, payload: str, amount: int, currency: str = "RUB",
                           *, charge_id: Optional[str] = None) -> int:
        """pre_checkout_query + successful_payment, as Telegram delivers them."""
        await self._post_update(tgf.pre_checkout(user, payload, amount, currency))
        return await self._post_update(tgf.successful_payment(user, payload, amount, currency,
                                                              charge_id=charge_id))

    async def start_user(self, user: tgf.TgUser, payload: str = "") -> None:
        """/start [payload] → captcha → language: a registered user at the main menu."""
        await self.send(user, "/start" + (f" {payload}" if payload else ""))
        await self.pass_captcha(user)
        lang_btn = self.tg.with_button(user.id, "start_lang_ru")
        if lang_btn is not None:
            await self.tap(user, "start_lang_ru")

    async def pass_captcha(self, user: tgf.TgUser) -> None:
        for s in reversed(self.tg.to(user.id)):
            data = [d for d in s.callback_data() if d.startswith("captcha:")]
            if data:
                expected = data[0].split(":")[1]
                await self.tap(user, f"captcha:{expected}:{expected}")
                return

    # ── provider webhooks in ────────────────────────────────────────
    async def webhook(self, hook: tuple, *, settle: bool = True) -> Hook:
        path, raw, headers = hook
        r = await self.http.post(path, content=raw, headers=headers)
        if settle:
            await self.settle()
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return Hook(r.status_code, body)

    # ── Telegram out ─────────────────────────────────────────────────
    def admin_texts(self, since: int = 0) -> List[str]:
        return [s.text for s in self.tg.since(since, ADMIN)]

    def user_texts(self, user_id: int, since: int = 0) -> List[str]:
        return [s.text for s in self.tg.since(since, user_id)]

    # ── DB ───────────────────────────────────────────────────────────
    async def row(self, sql: str, *args) -> Optional[Dict[str, Any]]:
        r = await self.pool.fetchrow(sql, *args)
        return dict(r) if r else None

    async def rows(self, sql: str, *args) -> List[Dict[str, Any]]:
        return [dict(r) for r in await self.pool.fetch(sql, *args)]

    async def val(self, sql: str, *args):
        return await self.pool.fetchval(sql, *args)

    async def sub(self, tg: int) -> Optional[Dict[str, Any]]:
        r = await self.row("SELECT * FROM subscriptions WHERE telegram_id=$1", tg)
        if r:
            r["expires_at"] = aware(r["expires_at"])
        return r

    async def payments(self, tg: int) -> List[Dict[str, Any]]:
        return await self.rows("SELECT * FROM payments WHERE telegram_id=$1 ORDER BY id", tg)

    async def pending(self, tg: int) -> List[Dict[str, Any]]:
        return await self.rows("SELECT * FROM pending_purchases WHERE telegram_id=$1 ORDER BY id", tg)

    async def balance(self, tg: int) -> float:
        return float(await database.get_user_balance(tg))

    async def payment_errors(self) -> List[Dict[str, Any]]:
        return await self.rows("SELECT * FROM payment_errors ORDER BY id")

    # ── seeding through real code ────────────────────────────────────
    async def register(self, user: tgf.TgUser, *, balance_rub: float = 0) -> None:
        """A registered user (captcha passed) without going through /start."""
        await database.create_user(user.id, user.username, user.language_code)
        await self.pool.execute("UPDATE users SET captcha_passed_at = NOW() WHERE telegram_id=$1", user.id)
        if balance_rub:
            await database.increase_balance(user.id, balance_rub, source="admin", description="e2e seed")

    async def create_purchase(self, user: tgf.TgUser, tariff: str, period_days: int, *,
                              provider: Optional[str] = None, price_rub: Optional[float] = None,
                              purchase_type: str = "subscription", promo_code: Optional[str] = None) -> Dict[str, Any]:
        """A pending purchase exactly as the payment screens create it
        (subscription_service.create_subscription_purchase / create_pending_purchase)."""
        from app.services.subscriptions import service as subscription_service
        is_combo = tariff.startswith("combo_")
        base_tariff = tariff.replace("combo_", "")
        if price_rub is None:
            price_rub = tariff_price(tariff, period_days)
        if purchase_type == "subscription":
            pid = await subscription_service.create_subscription_purchase(
                telegram_id=user.id, tariff=base_tariff, period_days=period_days,
                price_kopecks=int(round(price_rub * 100)), promo_code=promo_code, is_combo=is_combo,
            )
        else:
            pid = await database.create_pending_purchase(
                telegram_id=user.id, tariff=tariff, period_days=period_days,
                price_kopecks=int(round(price_rub * 100)), purchase_type=purchase_type,
            )
        if provider:
            await database.update_pending_purchase_invoice_id(pid, f"inv-{pid}", provider=provider)
        return await self.row("SELECT * FROM pending_purchases WHERE purchase_id=$1", pid)

    async def set_expiry(self, tg: int, expires_at: datetime) -> None:
        """Move a subscription's end date in the DB AND in the panel (a
        consistent "subscription until X" starting state)."""
        await self.pool.execute("UPDATE subscriptions SET expires_at=$2 WHERE telegram_id=$1",
                                tg, naive(expires_at))
        ent = self.panel.premium(tg)
        if ent is not None:
            ent["expireAt"] = expires_at.astimezone(UTC)


def tariff_price(tariff: str, period_days: int) -> float:
    if tariff.startswith("combo_"):
        return float(config.COMBO_TARIFFS[tariff][period_days]["price"])
    if tariff.startswith("traffic_") or tariff.startswith("gb_"):
        gb = int("".join(ch for ch in tariff if ch.isdigit()))
        packs = {**config.TRAFFIC_PACKS, **config.TRAFFIC_PACKS_EXTENDED}
        return float(packs[gb]["price"])
    return float(config.TARIFFS[tariff][period_days]["price"])


def combo_gb(tariff: str, period_days: int) -> int:
    return int(config.COMBO_TARIFFS[tariff][period_days]["gb"])


__all__ = [
    "ADMIN", "GIB", "MB", "UTC", "World", "aware", "combo_gb", "naive", "new_user",
    "tariff_price", "utcnow",
]
