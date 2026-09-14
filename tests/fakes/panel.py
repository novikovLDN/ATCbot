"""FakePanel — in-memory Remnawave for provisioning tests.

Mirrors the REAL return contracts of the functions the provisioning core
calls (docs/audit/02_payment_core_plan.md §A.3):

  remnawave_premium.create_premium_user_entity → PremiumCreateResult, never raises;
      adopting an existing entity PATCHes expireAt to EXACTLY the requested
      value (real _ensure_premium_entity_state — it can shorten premium)
  remnawave_premium.renew_premium_user          → bool, never raises
  remnawave_bypass.create_bypass_user_entity    → BypassCreateResult, never raises;
      adopting an existing entity does NOT change its trafficLimitBytes
  remnawave_api.get_bypass_entity_safe          → dict | None (None = absent OR panel down)
  remnawave_api.get_bypass_state /
  remnawave_api.get_premium_state               → ("present", dict) | ("absent", None)
                                                  | ("unavailable", None)
  remnawave_api.update_user                     → dict | None (None = failure / unknown id /
      premium SAFETY-DROP of trafficLimitBytes without _trust_bypass)

Modes (settable at any time via `panel.mode = ...`):
  "ok"                normal behaviour
  "down"              transport failure: failed results / None / False, no state change;
                      state readers report "unavailable"
  "crash_after_patch" update_user applies the PATCH, then raises ConnectionError
                      (renew_premium_user applies, then reports False — the real
                      one swallows the exception)
  "conflict"          the bypass limit is changed externally (+conflict_delta)
                      between the 1st and 2nd bypass read (get_bypass_entity_safe
                      or get_bypass_state) of a user after entering the mode;
                      later reads see the changed value
  "ignore_patch"      update_user answers like a successful PATCH (returns the
                      entity) but changes NOTHING — the panel silently ignored it
                      ("bought a month, the panel still shows 20 October")

Knobs for adoption edge cases:
  bypass_invisible_reads   N next get_bypass_state calls report "absent" even if the
                           entity exists (lookup miss / race) → core goes to create,
                           which ADOPTS it and keeps its old trafficLimitBytes
  premium_invisible_reads  same for get_premium_state
  premium_adopt_patch_ok   False → create_premium_user_entity adopts WITHOUT applying
                           expireAt (real _ensure_premium_entity_state PATCH failed:
                           result is still ok=True, the entity keeps a stale expireAt)

Usage:
    panel = FakePanel().install(monkeypatch)
    panel.seed_bypass(tg, 3 * GIB)
    ...
    assert panel.bypass_limit(tg) == 13 * GIB

Callers must reach these functions through the module attribute
(`remnawave_api.update_user(...)`), not a `from … import` binding made
before install().
"""
from __future__ import annotations

import itertools
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services import remnawave_api, remnawave_bypass, remnawave_premium
from app.services.remnawave_bypass import BypassCreateResult
from app.services.remnawave_premium import PremiumCreateResult

GIB = 1024 ** 3
MODES = ("ok", "down", "crash_after_patch", "conflict", "ignore_patch")
BYPASS_EXPIRE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class FakePanel:
    def __init__(self, mode: str = "ok", *, conflict_delta: int = GIB):
        self.conflict_delta = conflict_delta
        self.premium: Dict[int, Dict[str, Any]] = {}
        self.bypass: Dict[int, Dict[str, Any]] = {}
        self.calls: List[Tuple[Any, ...]] = []
        self.patch_count = 0          # PATCHes actually applied to an entity
        self._ids = itertools.count(1001)
        self._reads: Dict[int, int] = {}
        self.bypass_invisible_reads = 0
        self.premium_invisible_reads = 0
        self.premium_adopt_patch_ok = True
        self.mode = mode

    # ── mode ────────────────────────────────────────────────────────────
    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in MODES:
            raise ValueError(f"FakePanel mode must be one of {MODES}, got {value!r}")
        self._mode = value
        self._reads = {}

    # ── state helpers ───────────────────────────────────────────────────
    def _new_entity(self, tg: int, username: str, *, limit: int, expire_at: datetime) -> Dict[str, Any]:
        short = uuid_lib.uuid4().hex[:12]
        # Remnawave 3.4.3 ExtendedUsersSchema (libs/contract/models/
        # extended-users.schema.ts): numeric `id`, NO `uuid` field — the bot
        # stores `vlessUuid`; used traffic only under userTraffic.
        return {
            "id": next(self._ids),
            "vlessUuid": str(uuid_lib.uuid4()),
            "shortUuid": short,
            "username": username,
            "telegramId": tg,
            "trafficLimitBytes": int(limit),
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": _parse_dt(expire_at),
            "status": "ACTIVE",
            "hwidDeviceLimit": None,
            "externalSquadUuid": None,
            "description": None,
            "subscriptionUrl": f"https://panel.test/api/sub/{short}",
            "activeInternalSquads": [],
            "userTraffic": {
                "usedTrafficBytes": 0, "lifetimeUsedTrafficBytes": 0, "onlineAt": None,
                "firstConnectedAt": None, "lastConnectedNodeUuid": None,
            },
        }

    @staticmethod
    def _view(ent: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(ent)
        out["expireAt"] = _iso(ent["expireAt"])
        return out

    def seed_premium(self, tg: int, expire_at: datetime) -> Dict[str, Any]:
        ent = self._new_entity(tg, remnawave_premium.build_premium_username(tg, None), limit=0, expire_at=expire_at)
        self.premium[tg] = ent
        return self._view(ent)

    def seed_bypass(self, tg: int, limit_bytes: int) -> Dict[str, Any]:
        ent = self._new_entity(tg, remnawave_bypass.build_bypass_username(tg), limit=limit_bytes,
                               expire_at=BYPASS_EXPIRE)
        self.bypass[tg] = ent
        return self._view(ent)

    def premium_expire(self, tg: int) -> Optional[datetime]:
        ent = self.premium.get(tg)
        return ent["expireAt"] if ent else None

    def bypass_limit(self, tg: int) -> Optional[int]:
        ent = self.bypass.get(tg)
        return ent["trafficLimitBytes"] if ent else None

    def _find(self, user_id: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if isinstance(user_id, str) and user_id.isdigit():
            user_id = int(user_id)
        for kind, store in (("premium", self.premium), ("bypass", self.bypass)):
            for ent in store.values():
                if user_id in (ent["id"], ent["vlessUuid"]):
                    return ent, kind
        return None, None

    @staticmethod
    def _patch_rejected(fields: Dict[str, Any]) -> bool:
        """What Remnawave 3.4.3 PATCH /api/users rejects with HTTP 400
        (libs/contract/commands/users/update-user.command.ts): status other
        than ACTIVE|DISABLED, an expireAt not in the future, a negative
        trafficLimitBytes."""
        status = fields.get("status")
        if status is not None and status not in ("ACTIVE", "DISABLED"):
            return True
        if "expireAt" in fields and _parse_dt(fields["expireAt"]) <= datetime.now(timezone.utc):
            return True
        limit = fields.get("trafficLimitBytes")
        return limit is not None and int(limit) < 0

    def _apply(self, ent: Dict[str, Any], fields: Dict[str, Any]) -> None:
        for key, value in fields.items():
            ent[key] = _parse_dt(value) if key == "expireAt" else value
        self.patch_count += 1

    # ── remnawave_premium ───────────────────────────────────────────────
    async def create_premium_user_entity(
        self,
        telegram_id: int,
        *,
        requested_uuid: Optional[str],
        expire_at: datetime,
        existing_username: Optional[str] = None,
        description: str = "",
        tier: Optional[str] = None,
    ) -> PremiumCreateResult:
        self.calls.append(("create_premium_user_entity", telegram_id, expire_at))
        if self.mode == "down":
            return PremiumCreateResult(False, None, False, None, 0, "timeout")
        cap = remnawave_premium._device_limit_for(tier)   # same rule as the real code
        ent = self.premium.get(telegram_id)
        if ent is not None:
            if self.premium_adopt_patch_ok:
                fields = {"expireAt": expire_at, "status": "ACTIVE"}
                if tier:
                    fields["hwidDeviceLimit"] = cap
                self._apply(ent, fields)
            return PremiumCreateResult(
                True, ent["vlessUuid"], False, ent["subscriptionUrl"], 200, None,
                recovered=True, short_uuid=ent["shortUuid"], panel_id=ent["id"],
            )
        username = remnawave_premium.build_premium_username(telegram_id, existing_username)
        ent = self._new_entity(telegram_id, username, limit=0, expire_at=expire_at)
        ent["hwidDeviceLimit"] = cap
        if requested_uuid:
            ent["vlessUuid"] = requested_uuid
        self.premium[telegram_id] = ent
        return PremiumCreateResult(
            True, ent["vlessUuid"], bool(requested_uuid), ent["subscriptionUrl"], 201, None,
            recovered=False, short_uuid=ent["shortUuid"], panel_id=ent["id"],
        )

    async def renew_premium_user(self, telegram_id: int, new_expire_at: datetime,
                                 tier: Optional[str] = None) -> bool:
        self.calls.append(("renew_premium_user", telegram_id, new_expire_at))
        if self.mode == "down":
            return False
        ent = self.premium.get(telegram_id)
        if ent is None:
            return False
        fields = {"expireAt": new_expire_at, "status": "ACTIVE"}
        if tier:
            fields["hwidDeviceLimit"] = remnawave_premium._device_limit_for(tier)
        self._apply(ent, fields)
        return self.mode != "crash_after_patch"

    # ── remnawave_bypass ────────────────────────────────────────────────
    async def create_bypass_user_entity(
        self,
        telegram_id: int,
        *,
        traffic_limit_bytes: int,
        description: str = "",
    ) -> BypassCreateResult:
        self.calls.append(("create_bypass_user_entity", telegram_id, traffic_limit_bytes))
        if traffic_limit_bytes <= 0:
            return BypassCreateResult(False, None, None, None, 0, "non_positive_traffic_limit")
        if self.mode == "down":
            return BypassCreateResult(False, None, None, None, 0, "timeout")
        ent = self.bypass.get(telegram_id)
        if ent is not None:
            return BypassCreateResult(
                True, ent["vlessUuid"], ent["subscriptionUrl"], ent["shortUuid"], 200, None,
                recovered=True, panel_id=ent["id"],
            )
        ent = self._new_entity(telegram_id, remnawave_bypass.build_bypass_username(telegram_id),
                               limit=traffic_limit_bytes, expire_at=BYPASS_EXPIRE)
        self.bypass[telegram_id] = ent
        return BypassCreateResult(
            True, ent["vlessUuid"], ent["subscriptionUrl"], ent["shortUuid"], 201, None,
            recovered=False, panel_id=ent["id"],
        )

    # ── remnawave_api ───────────────────────────────────────────────────
    def _read_bypass(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        ent = self.bypass.get(telegram_id)
        if ent is None:
            return None
        self._reads[telegram_id] = self._reads.get(telegram_id, 0) + 1
        if self.mode == "conflict" and self._reads[telegram_id] == 2:
            ent["trafficLimitBytes"] += self.conflict_delta
        return self._view(ent)

    async def get_bypass_entity_safe(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        self.calls.append(("get_bypass_entity_safe", telegram_id))
        if self.mode == "down":
            return None
        return self._read_bypass(telegram_id)

    async def get_bypass_state(self, telegram_id: int) -> Tuple[str, Optional[Dict[str, Any]]]:
        self.calls.append(("get_bypass_state", telegram_id))
        if self.mode == "down":
            return "unavailable", None
        if self.bypass_invisible_reads > 0:
            self.bypass_invisible_reads -= 1
            return "absent", None
        view = self._read_bypass(telegram_id)
        return ("present", view) if view is not None else ("absent", None)

    async def get_premium_state(self, telegram_id: int) -> Tuple[str, Optional[Dict[str, Any]]]:
        self.calls.append(("get_premium_state", telegram_id))
        if self.mode == "down":
            return "unavailable", None
        if self.premium_invisible_reads > 0:
            self.premium_invisible_reads -= 1
            return "absent", None
        ent = self.premium.get(telegram_id)
        return ("present", self._view(ent)) if ent is not None else ("absent", None)

    async def update_user(self, user_id: Any, **fields) -> Optional[Dict[str, Any]]:
        trust_bypass = bool(fields.pop("_trust_bypass", False))
        self.calls.append(("update_user", user_id, dict(fields)))
        if self.mode == "down":
            return None
        ent, kind = self._find(user_id)
        if ent is None:
            return None
        if kind == "premium" and "trafficLimitBytes" in fields and not trust_bypass:
            return None  # real SAFETY-DROP: premium must stay unlimited
        if self._patch_rejected(fields):
            return None  # the real panel answers HTTP 400 → update_user returns None
        if self.mode == "ignore_patch":
            return self._view(ent)  # "200 OK", nothing applied
        self._apply(ent, fields)
        if self.mode == "crash_after_patch":
            raise ConnectionError("FakePanel: connection dropped after the PATCH was applied")
        return self._view(ent)

    # ── wiring ──────────────────────────────────────────────────────────
    def install(self, monkeypatch) -> "FakePanel":
        monkeypatch.setattr(remnawave_premium, "create_premium_user_entity", self.create_premium_user_entity)
        monkeypatch.setattr(remnawave_premium, "renew_premium_user", self.renew_premium_user)
        monkeypatch.setattr(remnawave_bypass, "create_bypass_user_entity", self.create_bypass_user_entity)
        monkeypatch.setattr(remnawave_api, "get_bypass_entity_safe", self.get_bypass_entity_safe)
        # raising=False: install() must also work before the readers exist upstream.
        monkeypatch.setattr(remnawave_api, "get_bypass_state", self.get_bypass_state, raising=False)
        monkeypatch.setattr(remnawave_api, "get_premium_state", self.get_premium_state, raising=False)
        monkeypatch.setattr(remnawave_api, "update_user", self.update_user)
        return self
