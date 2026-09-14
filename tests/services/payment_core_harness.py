"""
Hermetic harness for the payment-core characterization tests (task T0).

Not a test module (no ``test_`` prefix) — imported by
``tests/services/test_payment_core_characterization.py``.

What is REAL in these tests
---------------------------
Every payment entry point runs its production code:
``confirmation.process_confirmed_payment``, ``database.finalize_purchase``,
``database.finalize_balance_purchase``, ``database.admin_grant_access_atomic``,
``database.activate_gift_subscription``, the Telegram-native / balance / admin /
``/start gift_`` handlers,
``auto_renewal.process_auto_renewals`` and the whole Remnawave bypass layer
(``purchase_flow``, ``remnawave_bypass``, ``remnawave_service``).

What is FAKED
-------------
* ``FakePanel`` — the Remnawave HTTP client (``remnawave_api``) and the
  premium-entity helpers (``remnawave_premium``). Bypass entities are
  byte-limited dicts, so every path that adds GB is measured the same way:
  ``panel.added_gb(tg)`` = final trafficLimitBytes − seeded trafficLimitBytes.
* ``FakeConn`` / ``FakePool`` — asyncpg. SQL is dispatched on the table name,
  never on locking clauses, so it is indifferent to where ``FOR UPDATE`` sits.
* ``World.grant_access`` — a CONTRACT FAKE of ``database.subscriptions.grant_access``.
  It reproduces exactly the panel side effects of the current implementation:
    - active subscription → renewal; with ``_caller_holds_transaction`` it returns
      ``renewal_xray_sync_after_commit`` (subscriptions.py:1820-1830), otherwise it
      calls ``purchase_flow.sync_renewal_to_remnawave`` inline (:1831-1840);
    - new issuance, ``config.VPN_ENABLED`` false → ``pending_activation`` (:1859-1995);
    - new issuance in caller's tx without ``pre_provisioned_uuid`` → INVARIANT (:2013);
    - new issuance with ``pre_provisioned_uuid`` → no panel call (:2022);
    - new issuance standalone → ``purchase_flow.provision_subscription`` (:2070);
    - ``defer_panel=True`` (T7) → ZERO panel calls: renewal extends the row only
      (no inline sync, no ``renewal_xray_sync_after_commit``); new issuance without
      ``pre_provisioned_uuid`` → ``pending_activation`` (even in the caller's tx);
      the result carries ``"deferred": True``.
  If the rewrite changes grant_access semantics, update this fake with it.
* Fire-and-forget: ``remnawave_service._fire_and_forget`` collects the coroutines;
  every runner awaits them before returning, so GB totals are exact.
"""
from __future__ import annotations

import asyncio
import itertools
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

GB = 1024 ** 3
TG = 700_001
OTHER_TG = 700_999


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive(dt: datetime) -> datetime:
    """DB contract: TIMESTAMP WITHOUT TIME ZONE holds naive UTC."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ── Fake Remnawave panel ─────────────────────────────────────────────────


class FakePanel:
    """In-memory Remnawave: byte-limited bypass entities + premium expireAt."""

    def __init__(self) -> None:
        self.bypass: dict[int, dict] = {}
        self.premium_expire: dict[int, datetime] = {}
        self._seeded_bytes: dict[int, int] = {}
        self._ids = itertools.count(5000)
        self.calls: list[tuple] = []

    # seeding / measuring
    def new_bypass_entity(self, tg: int, limit_bytes: int) -> dict:
        ent = {
            "id": next(self._ids),
            "uuid": str(uuid_lib.uuid4()),
            "shortUuid": f"s{tg}",
            "username": str(tg),
            "telegramId": tg,
            "trafficLimitBytes": int(limit_bytes),
            "usedTrafficBytes": 0,
            "status": "ACTIVE",
            "subscriptionUrl": f"https://panel.test/sub/bypass-{tg}",
            "activeInternalSquads": [{"uuid": "sq"}],
        }
        self.bypass[tg] = ent
        return ent

    def bypass_bytes(self, tg: int) -> int:
        ent = self.bypass.get(tg)
        return int(ent["trafficLimitBytes"]) if ent else 0

    def added_bytes(self, tg: int) -> int:
        return self.bypass_bytes(tg) - self._seeded_bytes.get(tg, 0)

    def added_gb(self, tg: int) -> float:
        return self.added_bytes(tg) / GB

    @property
    def touched(self) -> bool:
        return bool(self.calls)

    def _find(self, ref: Any) -> Optional[dict]:
        for ent in self.bypass.values():
            if ref in (ent["id"], ent["uuid"], str(ent["id"])):
                return ent
        return None

    # remnawave_api surface
    async def find_user_by_username(self, username: str):
        self.calls.append(("find_user_by_username", username))
        for ent in self.bypass.values():
            if ent["username"] == str(username):
                return dict(ent)
        return None

    async def get_bypass_entity_safe(self, telegram_id: int):
        self.calls.append(("get_bypass_entity_safe", telegram_id))
        ent = self.bypass.get(int(telegram_id))
        return dict(ent) if ent else None

    async def get_user(self, ref):
        self.calls.append(("get_user", ref))
        ent = self._find(ref)
        return dict(ent) if ent else None

    async def create_user(self, *, username, traffic_limit_bytes, telegram_id=None,
                          raw_response=False, **_kw):
        self.calls.append(("create_user", username, traffic_limit_bytes))
        tg = int(telegram_id if telegram_id is not None else username)
        ent = self.new_bypass_entity(tg, traffic_limit_bytes)
        if raw_response:
            return {"ok": True, "status": 201, "response": dict(ent)}
        return dict(ent)

    async def update_user(self, ref, **fields):
        fields.pop("_trust_bypass", None)
        self.calls.append(("update_user", ref, dict(fields)))
        ent = self._find(ref)
        if ent is None:
            return None
        for key in ("trafficLimitBytes", "status", "expireAt", "telegramId"):
            if key in fields:
                ent[key] = fields[key]
        return dict(ent)

    async def assign_user_to_squad(self, *_a, **_kw):
        return True

    # remnawave_premium surface
    async def create_premium_user_entity(self, telegram_id, *, requested_uuid, expire_at, **_kw):
        from app.services.remnawave_premium import PremiumCreateResult
        self.calls.append(("create_premium", telegram_id))
        self.premium_expire[telegram_id] = expire_at
        return PremiumCreateResult(
            ok=True, panel_uuid=f"prem-{telegram_id}", forced_uuid_accepted=True,
            subscription_url=f"https://panel.test/sub/prem-{telegram_id}",
            status=201, error=None, recovered=False, short_uuid="ps", panel_id=9000,
        )

    async def renew_premium_user(self, telegram_id, new_expire_at, tier=None):
        self.calls.append(("renew_premium", telegram_id))
        if telegram_id not in self.premium_expire:
            return False
        self.premium_expire[telegram_id] = new_expire_at
        return True


# ── Fake asyncpg ─────────────────────────────────────────────────────────


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    """Answers queries from ``World`` state, dispatching on the table name."""

    def __init__(self, world: "World") -> None:
        self.w = world
        self.calls: list[tuple[str, str, tuple]] = []
        self._payment_ids = itertools.count(501)
        self._charges: set = set()   # payments.telegram_payment_charge_id values

    @staticmethod
    def _norm(sql: str) -> str:
        return " ".join(sql.split()).lower()

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        s = self._norm(sql)
        self.calls.append(("fetchrow", s, args))
        if "from pending_purchases" in s:
            return dict(self.w.pending) if self.w.pending else None
        if "from gift_subscriptions" in s:
            return dict(self.w.gift) if self.w.gift else None
        if "from subscriptions" in s:
            return dict(self.w.sub) if self.w.sub else None
        if "from users" in s:
            return {"balance": self.w.balance_kopecks, "trial_expires_at": None}
        return None

    async def fetchval(self, sql, *args):
        s = self._norm(sql)
        self.calls.append(("fetchval", s, args))
        if "insert into payments" in s:
            return next(self._payment_ids)
        if "select balance from users" in s:
            return self.w.balance_kopecks
        if "where telegram_payment_charge_id" in s:   # TG-RT-2 redelivery lookup
            return 1 if args and args[0] in self._charges else None
        return 1

    async def fetch(self, sql, *args):
        s = self._norm(sql)
        self.calls.append(("fetch", s, args))
        return self.w.fetch_batches.pop(0) if self.w.fetch_batches else []

    async def execute(self, sql, *args):
        s = self._norm(sql)
        self.calls.append(("execute", s, args))
        if s.startswith("update users set balance"):
            self.w.balance_kopecks = args[0]
        if s.startswith("update payments set telegram_payment_charge_id") and len(args) > 1:
            self._charges.add(args[1])
        return "UPDATE 1"

    def payment_inserts(self) -> list[tuple]:
        return [a for kind, s, a in self.calls if kind == "fetchval" and "insert into payments" in s]


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


# ── World: shared fake state + all patches ───────────────────────────────


@dataclass
class World:
    panel: FakePanel = field(default_factory=FakePanel)
    sub: Optional[dict] = None
    pending: Optional[dict] = None
    gift: Optional[dict] = None
    balance_kopecks: int = 1_000_000
    fetch_batches: list = field(default_factory=list)
    bypass_uuid: dict = field(default_factory=dict)
    bypass_id: dict = field(default_factory=dict)
    premium_uuid: dict = field(default_factory=dict)
    background: list = field(default_factory=list)
    i18n_keys: list = field(default_factory=list)
    grant_calls: list = field(default_factory=list)
    delivery_checks: list = field(default_factory=list)   # verify_delivery.schedule_legacy_check calls
    conn: Any = None
    pool: Any = None
    db: SimpleNamespace = field(default_factory=SimpleNamespace)

    # seeding
    def seed_active_subscription(self, tg: int = TG, *, tariff: str = "basic",
                                 bypass_gb: int = 3, days_left: int = 10,
                                 **extra) -> None:
        """Existing paying user: active sub + premium + bypass entity with ``bypass_gb``."""
        ent = self.panel.new_bypass_entity(tg, bypass_gb * GB)
        self.panel._seeded_bytes[tg] = bypass_gb * GB
        self.bypass_uuid[tg] = ent["uuid"]
        self.bypass_id[tg] = ent["id"]
        expires = utcnow() + timedelta(days=days_left)
        self.panel.premium_expire[tg] = expires
        self.premium_uuid[tg] = f"prem-{tg}"
        self.sub = {
            "telegram_id": tg, "status": "active", "uuid": str(uuid_lib.uuid4()),
            "vpn_key": f"https://panel.test/sub/prem-{tg}",
            "vpn_key_plus": ent["subscriptionUrl"],
            "expires_at": naive(expires), "subscription_type": tariff,
            "activation_status": "active", "auto_renew": True,
            "is_bypass_only": False, "is_combo": False,
        }
        self.sub.update(extra)

    # grant_access contract fake (see module docstring)
    async def grant_access(self, *, telegram_id, duration, source, admin_telegram_id=None,
                           admin_grant_days=None, conn=None, pre_provisioned_uuid=None,
                           _caller_holds_transaction=False, tariff="basic", country=None,
                           defer_panel=False, tariff_period_days=None, **_ignored):
        import config
        from app.services import purchase_flow
        from app.services.tariffs import extend_expiry

        self.grant_calls.append({
            "telegram_id": telegram_id, "duration": duration, "source": source,
            "caller_holds_tx": _caller_holds_transaction, "tariff": tariff,
            "pre_provisioned": pre_provisioned_uuid, "defer_panel": defer_panel,
            "tariff_period_days": tariff_period_days,
        })

        def _end_from(base):
            # same rule as the real grant_access: paid period → calendar months
            return extend_expiry(base, tariff_period_days) if tariff_period_days else base + duration
        now = utcnow()
        incoming = (tariff or "basic").strip().lower()
        sub = self.sub
        period_days = max(1, int(duration.total_seconds() // 86400))
        active = bool(
            sub and sub.get("status") == "active" and sub.get("uuid")
            and sub.get("expires_at") and aware(sub["expires_at"]) > now
        )
        if active:
            end = _end_from(max(aware(sub["expires_at"]), now))
            sub.update(expires_at=naive(end), subscription_type=incoming)
            result = {
                "uuid": sub["uuid"], "vless_url": None, "vpn_key": sub.get("vpn_key"),
                "subscription_end": end, "action": "renewal", "subscription_type": incoming,
            }
            sync = {"telegram_id": telegram_id, "uuid": sub["uuid"],
                    "subscription_end": end, "tariff": incoming, "period_days": period_days}
            if defer_panel:
                result["deferred"] = True
            elif _caller_holds_transaction:
                result["renewal_xray_sync_after_commit"] = sync
            else:
                await purchase_flow.sync_renewal_to_remnawave(sync)
            return result

        end = _end_from(now)
        has_pre = bool((pre_provisioned_uuid or {}).get("uuid"))
        if not config.VPN_ENABLED or (defer_panel and not has_pre):
            self.sub = {"telegram_id": telegram_id, "status": "active", "uuid": None,
                        "vpn_key": None, "expires_at": naive(end),
                        "subscription_type": incoming, "activation_status": "pending"}
            result = {"uuid": None, "vless_url": None, "subscription_end": end,
                      "action": "pending_activation"}
            if defer_panel:
                result["deferred"] = True
            return result
        if _caller_holds_transaction and not has_pre:
            raise RuntimeError("INVARIANT_VIOLATION: caller holds tx without pre_provisioned_uuid")
        if has_pre:
            vless = pre_provisioned_uuid
        else:
            vless = await purchase_flow.provision_subscription(
                telegram_id, tariff=tariff or "basic", subscription_end=end,
                period_days=period_days, is_trial=(source == "trial"),
            )
        self.sub = {
            "telegram_id": telegram_id, "status": "active", "uuid": vless["uuid"],
            "vpn_key": vless["vless_url"], "vpn_key_plus": vless.get("vless_url_plus"),
            "expires_at": naive(end), "subscription_type": incoming,
            "activation_status": "active", "auto_renew": False,
            "is_bypass_only": False, "is_combo": False,
        }
        result = {"uuid": vless["uuid"], "vless_url": vless["vless_url"],
                  "vpn_key": vless["vless_url"], "vless_url_plus": vless.get("vless_url_plus"),
                  "subscription_end": end, "action": "new_issuance",
                  "subscription_type": incoming}
        if defer_panel:
            result["deferred"] = True
        return result

    async def drain_background(self) -> None:
        """Await every fire-and-forget coroutine (and tasks they spawn)."""
        for _ in range(5):
            while self.background:
                await self.background.pop(0)
            await asyncio.sleep(0)


def install(monkeypatch) -> World:
    """Patch config, the panel, the DB layer and fire-and-forget; return the World."""
    import config
    import database
    import database.admin as db_admin
    import database.subscriptions as db_subs
    import database.users as db_users
    import app.i18n as i18n_mod
    from app.services import (
        purchase_flow, remnawave_api, remnawave_premium, remnawave_service, sub_aggregator,
    )
    from app.services import language_service
    from app.services.payments import verify_delivery

    w = World()
    w.conn = FakeConn(w)
    w.pool = FakePool(w.conn)

    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(config, "VPN_ENABLED", True)
    monkeypatch.setattr(database, "DB_READY", True, raising=False)

    # panel
    for name in ("find_user_by_username", "get_bypass_entity_safe", "get_user",
                 "create_user", "update_user", "assign_user_to_squad"):
        monkeypatch.setattr(remnawave_api, name, getattr(w.panel, name))
    monkeypatch.setattr(remnawave_premium, "create_premium_user_entity",
                        w.panel.create_premium_user_entity)
    monkeypatch.setattr(remnawave_premium, "renew_premium_user", w.panel.renew_premium_user)

    async def _premium_url(tg):
        return f"https://panel.test/sub/prem-{tg}"
    monkeypatch.setattr(purchase_flow, "_premium_url_for_existing", _premium_url)
    monkeypatch.setattr(sub_aggregator, "invalidate_bg", lambda *_a, **_k: None)
    monkeypatch.setattr(remnawave_service, "_fire_and_forget", w.background.append)
    monkeypatch.setattr(verify_delivery, "verify_bypass_delivery", AsyncMock())
    monkeypatch.setattr(verify_delivery, "verify_premium_delivery", AsyncMock())
    monkeypatch.setattr(verify_delivery, "schedule_legacy_check",
                        lambda tg, **kw: w.delivery_checks.append((tg, kw)))

    # DB pointers to panel entities
    async def get_remnawave_uuid(tg): return w.bypass_uuid.get(tg)
    async def set_remnawave_uuid(tg, u): w.bypass_uuid[tg] = u
    async def clear_remnawave_uuid(tg): w.bypass_uuid.pop(tg, None)
    async def get_remnawave_id(tg): return w.bypass_id.get(tg)
    async def set_remnawave_id(tg, i): w.bypass_id[tg] = i
    async def get_remnawave_bypass_cache(tg):
        return {"remnawave_uuid": w.bypass_uuid.get(tg),
                "remnawave_bypass_sub_url": f"https://panel.test/sub/bypass-{tg}"}
    async def set_remnawave_bypass_cache(tg, u, url=None, short=None):
        if u:
            w.bypass_uuid[tg] = u
    async def get_remnawave_premium_uuid(tg): return w.premium_uuid.get(tg)
    async def set_remnawave_premium_uuid_and_url(tg, u, url=None, **_k): w.premium_uuid[tg] = u
    async def get_subscription_any(tg): return dict(w.sub) if w.sub else None
    async def get_subscription(tg):
        return dict(w.sub) if w.sub and w.sub.get("status") == "active" else None
    async def get_pool(): return w.pool

    db = w.db
    db.record_traffic_purchase = AsyncMock()
    db.set_combo_flag = AsyncMock()
    db.set_bypass_only_flag = AsyncMock()
    db.consume_promo = AsyncMock()
    db.process_referral_reward = AsyncMock(return_value={"success": False, "reason": "no_referrer"})
    db.mark_payment_notification_sent = AsyncMock(return_value=True)
    db.is_payment_notification_sent = AsyncMock(return_value=False)
    db.audit = AsyncMock()

    patches = {
        "get_remnawave_uuid": get_remnawave_uuid, "set_remnawave_uuid": set_remnawave_uuid,
        "clear_remnawave_uuid": clear_remnawave_uuid, "get_remnawave_id": get_remnawave_id,
        "set_remnawave_id": set_remnawave_id,
        "get_remnawave_bypass_cache": get_remnawave_bypass_cache,
        "set_remnawave_bypass_cache": set_remnawave_bypass_cache,
        "get_remnawave_premium_uuid": get_remnawave_premium_uuid,
        "set_remnawave_premium_uuid_and_url": set_remnawave_premium_uuid_and_url,
        "set_remnawave_premium_id": AsyncMock(), "set_remnawave_premium_sub_url": AsyncMock(),
        "reset_traffic_notification_flags": AsyncMock(),
        "get_subscription_any": get_subscription_any, "get_subscription": get_subscription,
        "get_pool": get_pool, "grant_access": w.grant_access,
        "record_traffic_purchase": db.record_traffic_purchase,
        "set_combo_flag": db.set_combo_flag, "set_bypass_only_flag": db.set_bypass_only_flag,
        "mark_payment_notification_sent": db.mark_payment_notification_sent,
        "is_payment_notification_sent": db.is_payment_notification_sent,
        "_log_audit_event_atomic_standalone": db.audit,
        "ensure_bypass_only_subscription": AsyncMock(),
    }
    for name, fn in patches.items():
        monkeypatch.setattr(database, name, fn, raising=False)
    # Module-global bindings used inside database.subscriptions / database.admin.
    for mod in (db_subs, db_admin):
        monkeypatch.setattr(mod, "get_pool", get_pool)
    monkeypatch.setattr(db_subs, "grant_access", w.grant_access)
    monkeypatch.setattr(db_subs, "set_combo_flag", db.set_combo_flag)
    monkeypatch.setattr(db_subs, "set_bypass_only_flag", db.set_bypass_only_flag)
    monkeypatch.setattr(db_subs, "_consume_promo_in_transaction", db.consume_promo)
    monkeypatch.setattr(db_users, "process_referral_reward", db.process_referral_reward)

    # language + i18n key recording (wraps the real texts)
    async def _lang(_tg): return "ru"
    monkeypatch.setattr(language_service, "resolve_user_language", _lang)
    real_get_text = i18n_mod.get_text

    def recording_get_text(language, key, *args, **kwargs):
        w.i18n_keys.append(key)
        return real_get_text(language, key, *args, **kwargs)
    w.recording_get_text = recording_get_text
    w.real_get_text = real_get_text
    monkeypatch.setattr(i18n_mod, "get_text", recording_get_text)
    return w


def text_prefix(w: World, key: str) -> str:
    """Static head of an i18n template (text before the first placeholder)."""
    return w.real_get_text("ru", key).split("{", 1)[0]


# ── Runners: one per payment entry point ─────────────────────────────────


def make_pending(*, purchase_id="pid-1", tariff="basic", period_days=30, price_rub=None,
                 purchase_type="subscription", is_combo=False, promo_code=None, tg=TG) -> dict:
    import config
    if price_rub is None:
        if is_combo:
            price_rub = config.COMBO_TARIFFS[f"combo_{tariff}"][period_days]["price"]
        elif tariff in config.TARIFFS and period_days in config.TARIFFS[tariff]:
            price_rub = config.TARIFFS[tariff][period_days]["price"]
        else:
            price_rub = 100
    return {
        "purchase_id": purchase_id, "telegram_id": tg, "status": "pending",
        "tariff": tariff, "period_days": period_days, "purchase_type": purchase_type,
        "price_kopecks": int(price_rub * 100), "is_combo": is_combo,
        "promo_code": promo_code, "country": None,
    }


async def run_webhook(w: World, monkeypatch, pending: dict, *, provider="platega") -> dict:
    """External-provider webhook → confirmation.process_confirmed_payment."""
    import database
    from app.handlers.callbacks import payments_callbacks
    from app.services.payments import confirmation

    w.pending = pending

    async def by_id(pid, check_expiry=False):
        return dict(w.pending) if w.pending and w.pending["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase_by_id", by_id)
    monkeypatch.setattr(payments_callbacks, "delete_invoice_message_for_purchase", AsyncMock())
    import app.services.automated_notifications as autonotif
    monkeypatch.setattr(autonotif, "get_custom_notification_text", AsyncMock(return_value=None))
    bot = MagicMock()
    bot.send_message = AsyncMock()
    result = await confirmation.process_confirmed_payment(
        provider=provider, purchase_id=pending["purchase_id"],
        amount_rubles=pending["price_kopecks"] / 100, invoice_id="inv-1",
        telegram_id=pending["telegram_id"], bot=bot,
    )
    await w.drain_background()
    return {"result": result, "bot": bot}


def _fsm(tg=TG):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.storage.base import StorageKey
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=tg, user_id=tg))


async def run_balance_purchase(w: World, monkeypatch, *, tariff="basic", period_days=30,
                               combo_gb=0, promo_code=None, tg=TG) -> MagicMock:
    """Balance purchase → payments_callbacks.callback_pay_balance (real finalize_balance_purchase)."""
    import config
    import database
    import app.services.automated_notifications as autonotif
    from app.handlers.callbacks import payments_callbacks as pc
    from app.handlers.common.states import PurchaseState

    price_rub = (config.COMBO_TARIFFS[f"combo_{tariff}"][period_days]["price"] if combo_gb
                 else config.TARIFFS[tariff][period_days]["price"])
    state = _fsm(tg)
    await state.set_state(PurchaseState.choose_payment_method)
    await state.update_data(tariff_type=tariff, period_days=period_days,
                            final_price_kopecks=price_rub * 100, combo_bypass_gb=combo_gb)

    async def _balance(_tg): return w.balance_kopecks / 100
    monkeypatch.setattr(database, "get_user_balance", _balance, raising=False)
    monkeypatch.setattr(pc, "check_rate_limit", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(pc, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pc, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pc, "get_promo_session",
                        AsyncMock(return_value={"promo_code": promo_code} if promo_code else None))
    # the code the price screen applied (owner rule: largest discount wins)
    monkeypatch.setattr(pc, "get_applied_promo_code", AsyncMock(return_value=promo_code))
    monkeypatch.setattr(pc, "clear_promo_session", AsyncMock())
    monkeypatch.setattr(autonotif, "is_notification_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(autonotif, "get_notification_text", AsyncMock(return_value=None))
    monkeypatch.setattr(autonotif, "get_custom_notification_text", AsyncMock(return_value=None))
    monkeypatch.setattr(autonotif, "log_notification_send", AsyncMock())

    callback = MagicMock()
    callback.from_user.id = tg
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.delete = AsyncMock()
    await pc.callback_pay_balance(callback, state)
    await w.drain_background()
    return callback


async def run_telegram_payment(w: World, monkeypatch, pending: dict, *, fsm_combo_gb=0) -> MagicMock:
    """Telegram-native successful_payment → payments_messages.process_successful_payment."""
    import database
    from app.handlers.payments import payments_messages as pm

    w.pending = pending

    async def get_pending(pid, tg, check_expiry=True):
        return dict(w.pending) if w.pending and w.pending["purchase_id"] == pid else None
    monkeypatch.setattr(database, "get_pending_purchase", get_pending)
    monkeypatch.setattr(pm, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(pm, "i18n_get_text", w.recording_get_text)
    monkeypatch.setattr(pm, "clear_promo_session", AsyncMock())
    import app.services.automated_notifications as autonotif
    monkeypatch.setattr(autonotif, "get_custom_notification_text", AsyncMock(return_value=None))

    state = _fsm(pending["telegram_id"])
    if fsm_combo_gb:
        await state.update_data(combo_bypass_gb=fsm_combo_gb)
    message = MagicMock()
    message.from_user.id = pending["telegram_id"]
    message.message_id = 1
    message.answer = AsyncMock()
    message.successful_payment = SimpleNamespace(
        currency="RUB", total_amount=pending["price_kopecks"],
        invoice_payload=f"purchase:{pending['purchase_id']}",
        telegram_payment_charge_id="tg-charge-1",
    )
    await pm.process_successful_payment(message, state)
    await w.drain_background()
    return message


async def run_admin_grant_days(w: World, monkeypatch, *, days=7, tariff="basic", tg=TG) -> dict:
    """Admin «grant N days» → the dashboard endpoint POST /users/{tg}/grant
    (the only admin grant entry point since the bot admin panel was removed)."""
    import config
    from app.api.dashboard.routes import users as routes_mod

    monkeypatch.setattr(routes_mod.bus, "publish", MagicMock())
    resp = await routes_mod.user_grant(
        telegram_id=tg, body=routes_mod.GrantRequest(days=days, tariff=tariff),
        admin={"sub": str(config.ADMIN_TELEGRAM_ID)},
    )
    await w.drain_background()
    return resp


async def run_gift_activation(w: World, monkeypatch, *, tariff="basic", period_days=30,
                              code="GIFT1234", tg=TG) -> MagicMock:
    """/start gift_<code> → start.cmd_start → database.activate_gift_subscription."""
    import config
    import database
    from app.handlers.user import start

    w.gift = {"id": 1, "gift_code": code, "status": "paid", "buyer_telegram_id": OTHER_TG,
              "tariff": tariff, "period_days": period_days,
              "expires_at": naive(utcnow() + timedelta(days=30))}
    monkeypatch.setattr(config, "IS_STAGE", False)
    monkeypatch.setattr(database, "get_user",
                        AsyncMock(return_value={"language": "ru", "referral_code": "ref"}))
    monkeypatch.setattr(start, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(start, "get_main_menu_keyboard", AsyncMock(return_value=None))
    message = MagicMock()
    message.chat.type = "private"
    message.text = f"/start gift_{code}"
    message.from_user.id = tg
    message.from_user.language_code = "ru"
    message.from_user.username = "buyer"
    message.answer = AsyncMock()
    await start.cmd_start(message, _fsm(tg))
    await w.drain_background()
    return message


async def run_auto_renewal(w: World, monkeypatch, *, last_payment_tariff="basic_30", tg=TG) -> dict:
    """Auto-renewal worker → auto_renewal.process_auto_renewals (one due subscription)."""
    import auto_renewal
    import database

    w.fetch_batches = [[{**w.sub, "telegram_id": tg, "language": "ru",
                         "balance": w.balance_kopecks}]]
    decrease = AsyncMock(return_value=True)
    sent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(database, "get_last_subscription_payment",
                        AsyncMock(return_value={"tariff": last_payment_tariff}), raising=False)
    monkeypatch.setattr(database, "get_user_discount", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(database, "decrease_balance", decrease, raising=False)
    monkeypatch.setattr(database, "increase_balance", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr(auto_renewal, "acquire_connection", lambda pool, _name: _Acquire(w.conn))
    monkeypatch.setattr(auto_renewal, "safe_send_message", sent)
    monkeypatch.setattr(auto_renewal, "resolve_user_language", AsyncMock(return_value="ru"))
    await auto_renewal.process_auto_renewals(MagicMock())
    await w.drain_background()
    return {"decrease_balance": decrease, "safe_send_message": sent}
