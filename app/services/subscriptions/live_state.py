"""What the panel (the source of truth) says about a user's access RIGHT NOW.

Used where the bot tells the user something about their access:
  * «Моя подписка» / «Мой профиль» (owner 2026-09-14: «актуальную информацию
    надо давать 100%») — get_view(): DB row + panel premium + panel bypass,
    reconciled, cached for CACHE_TTL_S per user;
  * notices that depend on the bypass GB left (subscription ended, paid
    reminders) — read_bypass(), uncached.

Every panel read is bounded by PANEL_TIMEOUT_S and never runs while a DB
connection is held (the DB reads here are short and finished before). A panel
that does not answer is reported as "unavailable" — callers then show the DB
values and say so, they never present stale numbers as current.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app import i18n as _i18n

logger = logging.getLogger(__name__)

PANEL_TIMEOUT_S = 3.0
CACHE_TTL_S = 30.0   # owner 2026-09-14: repeated presses must not hammer the panel
# Panel expireAt vs DB expires_at: the same tolerance as the delivery check.
PREMIUM_TOLERANCE = timedelta(minutes=5)
# One mismatch alert per user per this many seconds (the panel read is cached
# anyway; this stops a user who keeps opening the screen from flooding the admin).
MISMATCH_ALERT_TTL_S = 6 * 3600.0

_clock = time.monotonic   # tests patch it
_cache: Dict[int, Tuple[float, tuple, "LiveView"]] = {}
_alerted: Dict[int, float] = {}
_tasks: set = set()


def _utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class BypassInfo:
    """The bypass entity: present / absent / unavailable (panel did not answer)."""
    state: str
    used: int = 0
    limit: int = 0          # Remnawave: 0 = unlimited
    status: str = ""

    @property
    def known(self) -> bool:
        return self.state != "unavailable"

    @property
    def unlimited(self) -> bool:
        return self.state == "present" and self.limit <= 0

    @property
    def remaining(self) -> int:
        """Bytes left (0 when absent / unavailable / disabled; see `unlimited`)."""
        if self.state != "present" or self.status == "DISABLED" or self.limit <= 0:
            return 0
        return max(0, self.limit - self.used)

    @property
    def works(self) -> Optional[bool]:
        """None — unknown (panel unavailable); else whether bypass traffic is left."""
        if not self.known:
            return None
        if self.state != "present" or self.status == "DISABLED":
            return False
        return self.unlimited or self.remaining > 0


@dataclass(frozen=True)
class PremiumInfo:
    state: str
    expire_at: Optional[datetime] = None
    status: str = ""

    @property
    def known(self) -> bool:
        return self.state != "unavailable"

    @property
    def active(self) -> bool:
        return (self.state == "present" and self.status == "ACTIVE"
                and self.expire_at is not None and self.expire_at > datetime.now(timezone.utc))


UNAVAILABLE_BYPASS = BypassInfo("unavailable")
UNAVAILABLE_PREMIUM = PremiumInfo("unavailable")


def _bypass_from(state: str, ent: Optional[Dict[str, Any]]) -> BypassInfo:
    if state != "present" or not isinstance(ent, dict):
        return BypassInfo(state if state in ("absent", "unavailable") else "unavailable")
    traffic = ent.get("userTraffic") or {}
    used = traffic.get("usedTrafficBytes", ent.get("usedTrafficBytes", 0))
    try:
        return BypassInfo("present", used=int(used or 0), limit=int(ent.get("trafficLimitBytes") or 0),
                          status=str(ent.get("status") or "").upper())
    except (TypeError, ValueError):
        return UNAVAILABLE_BYPASS


def _premium_from(state: str, ent: Optional[Dict[str, Any]]) -> PremiumInfo:
    if state != "present" or not isinstance(ent, dict):
        return PremiumInfo(state if state in ("absent", "unavailable") else "unavailable")
    return PremiumInfo("present", expire_at=_utc(ent.get("expireAt")), status=str(ent.get("status") or "").upper())


async def read_bypass(telegram_id: int, *, timeout: float = PANEL_TIMEOUT_S) -> BypassInfo:
    """The bypass entity now (uncached). Never raises."""
    try:
        from app.services import remnawave_api
        state, ent = await asyncio.wait_for(remnawave_api.get_bypass_state(telegram_id), timeout=timeout)
        return _bypass_from(state, ent)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — timeout / transport / parse: unknown, never a guess
        logger.warning("LIVE_STATE_BYPASS_UNAVAILABLE: tg=%s %s", telegram_id, type(e).__name__)
        return UNAVAILABLE_BYPASS


async def read_premium(telegram_id: int, *, timeout: float = PANEL_TIMEOUT_S) -> PremiumInfo:
    """The premium entity now (uncached). Never raises."""
    try:
        from app.services import remnawave_api
        state, ent = await asyncio.wait_for(remnawave_api.get_premium_state(telegram_id), timeout=timeout)
        return _premium_from(state, ent)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("LIVE_STATE_PREMIUM_UNAVAILABLE: tg=%s %s", telegram_id, type(e).__name__)
        return UNAVAILABLE_PREMIUM


def format_bytes(language: str, b: int) -> str:
    """«7.5 ГБ» / «640 МБ» / «0 ГБ» in the user's language."""
    b = max(0, int(b or 0))
    gb, mb = 1024 ** 3, 1024 ** 2
    if b >= gb:
        v = b / gb
        num = f"{v:.1f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.0f}"
        return f"{num} {_i18n.get_text(language, 'common.unit_gb')}"
    if b >= mb:
        return f"{b / mb:.0f} {_i18n.get_text(language, 'common.unit_mb')}"
    if b == 0:
        return f"0 {_i18n.get_text(language, 'common.unit_gb')}"
    return f"{max(1, round(b / 1024))} {_i18n.get_text(language, 'common.unit_kb')}"


# ── the screens: DB + panel, reconciled ────────────────────────────────


@dataclass(frozen=True)
class LiveView:
    """What «Моя подписка» / «Мой профиль» show.

    `sub` is the DB row. `premium_until` / `premium_active` / `pending` are the
    reconciled premium state: the panel when it answered, else the DB (then
    `panel_unavailable` is True and the screen says so)."""
    sub: Optional[Dict[str, Any]]
    premium: PremiumInfo
    bypass: BypassInfo
    premium_active: bool
    premium_until: Optional[datetime]
    pending: bool
    is_bypass_only: bool
    mismatch: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def panel_unavailable(self) -> bool:
        return not self.bypass.known or (self._premium_relevant and not self.premium.known)

    @property
    def _premium_relevant(self) -> bool:
        return bool(self.sub) and not self.is_bypass_only and not self.pending


def _db_premium(sub: Optional[Dict[str, Any]], now: datetime) -> Tuple[bool, Optional[datetime], bool, bool]:
    """(active, until, pending, bypass_only) from the DB row alone."""
    if not sub:
        return False, None, False, False
    bypass_only = bool(sub.get("is_bypass_only")) or (sub.get("source") or "") == "bypass_only"
    exp = _utc(sub.get("expires_at"))
    active = (sub.get("status") == "active" and exp is not None and exp > now and not bypass_only)
    pending = active and (sub.get("activation_status") or "active") == "pending"
    return active, (exp if active else None), pending, bypass_only


def reconcile(sub: Optional[Dict[str, Any]], premium: PremiumInfo, bypass: BypassInfo,
              now: Optional[datetime] = None) -> LiveView:
    """Pure: the premium state to show and the DB↔panel mismatch, if any."""
    now = now or datetime.now(timezone.utc)
    db_active, db_until, pending, bypass_only = _db_premium(sub, now)
    mismatch = None
    active, until = db_active, db_until
    if pending or bypass_only and not premium.active:
        # activation still queued / premium ended long ago: the DB decides
        pass
    elif premium.state == "present":
        p_until = premium.expire_at
        if premium.active:
            active, until = True, p_until
            if not db_active:
                mismatch = f"DB: premium not active; panel: ACTIVE until {p_until:%Y-%m-%d %H:%M} UTC"
            elif db_until is not None and abs(p_until - db_until) > PREMIUM_TOLERANCE:
                mismatch = (f"DB expires_at {db_until:%Y-%m-%d %H:%M} UTC, "
                            f"panel expireAt {p_until:%Y-%m-%d %H:%M} UTC")
        elif db_active:
            active, until = False, None
            exp = f"{p_until:%Y-%m-%d %H:%M} UTC" if p_until else "?"
            mismatch = (f"DB: active until {db_until:%Y-%m-%d %H:%M} UTC; panel: {premium.status or '?'}, "
                        f"expireAt {exp}")
    elif premium.state == "absent" and db_active:
        mismatch = f"DB: active until {db_until:%Y-%m-%d %H:%M} UTC; panel: premium entity absent"
    return LiveView(sub=sub, premium=premium, bypass=bypass, premium_active=active,
                    premium_until=until, pending=pending, is_bypass_only=bypass_only, mismatch=mismatch)


def _fingerprint(sub: Optional[Dict[str, Any]]) -> tuple:
    if not sub:
        return ()
    return tuple(str(sub.get(k)) for k in (
        "expires_at", "status", "source", "activation_status", "is_bypass_only",
        "subscription_type", "remnawave_uuid", "uuid"))


def invalidate(telegram_id: Optional[int] = None) -> None:
    if telegram_id is None:
        _cache.clear()
    else:
        _cache.pop(int(telegram_id), None)


async def get_view(telegram_id: int) -> LiveView:
    """DB row + panel premium + panel bypass, reconciled (see LiveView).

    Cached for CACHE_TTL_S per user and DB state: any change of the DB row (a
    purchase, a renewal, an expiry) is read fresh at once. Never raises for
    the panel part; a DB failure propagates (the screen shows its error)."""
    import database
    sub = await database.get_subscription_any(telegram_id)
    fp = _fingerprint(sub)
    hit = _cache.get(int(telegram_id))
    now_m = _clock()
    if hit is not None and hit[1] == fp and now_m - hit[0] < CACHE_TTL_S:
        return hit[2]
    _, _, pending, bypass_only = _db_premium(sub, datetime.now(timezone.utc))
    if sub and not bypass_only and not pending:
        premium, bypass = await asyncio.gather(read_premium(telegram_id), read_bypass(telegram_id))
    else:
        premium, bypass = PremiumInfo("absent"), await read_bypass(telegram_id)
    view = reconcile(sub, premium, bypass)
    if len(_cache) > 5000:
        _cache.clear()
    if not view.panel_unavailable:
        _cache[int(telegram_id)] = (now_m, fp, view)
    if view.mismatch:
        _schedule_mismatch_alert(telegram_id, view)
    return view


def _schedule_mismatch_alert(telegram_id: int, view: LiveView) -> None:
    """DELIVERY_MISMATCH-style admin alert (shared budget, digest over it),
    at most once per user per MISMATCH_ALERT_TTL_S. Fire-and-forget."""
    now_m = _clock()
    at = _alerted.get(int(telegram_id))
    if at is not None and now_m - at < MISMATCH_ALERT_TTL_S:
        return
    _alerted[int(telegram_id)] = now_m
    logger.critical("DELIVERY_MISMATCH_VIEW: tg=%s %s", telegram_id, view.mismatch)
    text = "\n".join([
        "DELIVERY_MISMATCH (user screen): DB and panel disagree on premium",
        f"user: tg:{telegram_id}",
        f"- {view.mismatch}",
        "",
        "The user was shown the panel value. Check the subscription in the dashboard.",
    ])
    try:
        from app.services import provisioning
        loop = asyncio.get_running_loop()
        task = loop.create_task(provisioning.report_payment_alert(
            "legacy_delivery", text, reason=f"screen: {view.mismatch}",
            telegram_id=telegram_id, key=f"view:{telegram_id}"))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as e:  # noqa: BLE001
        logger.warning("LIVE_STATE_ALERT_SCHEDULE_FAILED: tg=%s %s", telegram_id, e)


def reset_state() -> None:
    """Forget the cache and the alert marks (tests)."""
    _cache.clear()
    _alerted.clear()


__all__ = [
    "BypassInfo", "CACHE_TTL_S", "LiveView", "PANEL_TIMEOUT_S", "PremiumInfo", "format_bytes",
    "get_view", "invalidate", "read_bypass", "read_premium", "reconcile", "reset_state",
]
