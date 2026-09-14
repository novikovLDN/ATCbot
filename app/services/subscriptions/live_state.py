"""How many bypass GB a user has left RIGHT NOW — read from the panel (the
source of truth) for the notices whose text depends on it:

  * «подписка закончилась» — «обход работает, осталось N» vs «VPN отключён»
    (docs/notifications/matrix.md #2);
  * the paid «завтра» / «3 часа» reminders — «обход останется» (#9).

One read per notice, bounded by PANEL_TIMEOUT_S, never while a DB connection
is held. A panel that does not answer is "unavailable" — the caller then uses
wording that names no amount (never a stale or guessed number).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app import i18n as _i18n

logger = logging.getLogger(__name__)

PANEL_TIMEOUT_S = 3.0


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


UNAVAILABLE_BYPASS = BypassInfo("unavailable")


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


async def read_bypass(telegram_id: int, *, timeout: float = PANEL_TIMEOUT_S) -> BypassInfo:
    """The bypass entity now. Never raises."""
    try:
        from app.services import remnawave_api
        state, ent = await asyncio.wait_for(remnawave_api.get_bypass_state(telegram_id), timeout=timeout)
        return _bypass_from(state, ent)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — timeout / transport / parse: unknown, never a guess
        logger.warning("LIVE_STATE_BYPASS_UNAVAILABLE: tg=%s %s", telegram_id, type(e).__name__)
        return UNAVAILABLE_BYPASS


def format_bytes(language: str, b: int) -> str:
    """«7.5 ГБ» / «640 МБ» / «0 ГБ» in the user's language."""
    b = max(0, int(b or 0))
    gb, mb = 1024 ** 3, 1024 ** 2
    if b >= gb:
        v = b / gb
        num = f"{v:.1f}".rstrip("0").rstrip(".") if v < 100 else f"{v:.0f}"
        return f"{num} {_i18n.get_text(language, 'common.unit_gb')}"
    if b >= mb:
        return f"{b / mb:.0f} {_i18n.get_text(language, 'common.unit_mb')}"
    if b == 0:
        return f"0 {_i18n.get_text(language, 'common.unit_gb')}"
    return f"{max(1, round(b / 1024))} {_i18n.get_text(language, 'common.unit_kb')}"


__all__ = ["BypassInfo", "PANEL_TIMEOUT_S", "format_bytes", "read_bypass"]
