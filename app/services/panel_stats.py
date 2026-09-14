"""Remnawave panel statistics for the dashboard — read-only, cached.

One call per panel endpoint per 45 s, whatever the number of open
dashboards. Failures degrade to `available=False` for that block; the
dashboard renders "panel unavailable" rather than an error page.
Shapes follow remnawave/backend 3.4.3 libs/contract (see metrics.md).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.services import remnawave_api
from app.services.dashboard_cache import cached

logger = logging.getLogger(__name__)

PANEL_TTL_SECONDS = 45.0
USER_STATUSES = ("ACTIVE", "DISABLED", "LIMITED", "EXPIRED")

_IEC = {"b": 0, "kib": 1, "mib": 2, "gib": 3, "tib": 4, "pib": 5,
        "kb": 1, "mb": 2, "gb": 3, "tb": 4, "pb": 5}
_SIZE_RE = re.compile(r"^\s*(-?[0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)?\s*$")


def parse_size(value: Any) -> Optional[int]:
    """Bytes from the panel's size strings ("1.25 TiB", "0", "123456")."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = _SIZE_RE.match(str(value))
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "b").lower()
    if unit not in _IEC:
        return None
    return int(num * (1024 ** _IEC[unit]))


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def normalize_system(raw: Optional[dict]) -> dict[str, Any]:
    if not raw:
        return {"available": False}
    users = raw.get("users") or {}
    counts = users.get("statusCounts") or {}
    online = raw.get("onlineStats") or {}
    nodes = raw.get("nodes") or {}
    memory = raw.get("memory") or {}
    return {
        "available": True,
        "users_total": _int(users.get("totalUsers")),
        "status_counts": {s: _int(counts.get(s)) for s in USER_STATUSES},
        "online_now": _int(online.get("onlineNow")),
        "online_day": _int(online.get("lastDay")),
        "online_week": _int(online.get("lastWeek")),
        "never_online": _int(online.get("neverOnline")),
        "nodes_online": _int(nodes.get("totalOnline")),
        "traffic_lifetime_bytes": parse_size(nodes.get("totalBytesLifetime")) or 0,
        "uptime_seconds": _int(raw.get("uptime")),
        "memory": {"total": _int(memory.get("total")), "used": _int(memory.get("used"))},
    }


_BANDWIDTH_KEYS = {
    "bandwidthLastTwoDays": "day",
    "bandwidthLastSevenDays": "week",
    "bandwidthLast30Days": "days30",
    "bandwidthCalendarMonth": "month",
    "bandwidthCurrentYear": "year",
}


def normalize_bandwidth(raw: Optional[dict]) -> dict[str, Any]:
    if not raw:
        return {"available": False}
    out: dict[str, Any] = {"available": True}
    for src, key in _BANDWIDTH_KEYS.items():
        stat = raw.get(src) or {}
        out[key] = {
            "current": stat.get("current"),
            "previous": stat.get("previous"),
            "current_bytes": parse_size(stat.get("current")),
            "previous_bytes": parse_size(stat.get("previous")),
        }
    return out


def normalize_hwid(raw: Optional[dict]) -> dict[str, Any]:
    if not raw:
        return {"available": False}
    stats = raw.get("stats") or {}
    return {
        "available": True,
        "unique_devices": _int(stats.get("totalUniqueDevices")),
        "devices": _int(stats.get("totalHwidDevices")),
        "avg_per_user": float(stats.get("averageHwidDevicesPerUser") or 0),
        "by_platform": sorted(
            ({"platform": p.get("platform") or "unknown", "count": _int(p.get("count"))}
             for p in (raw.get("byPlatform") or [])),
            key=lambda p: -p["count"],
        ),
    }


def normalize_nodes(nodes: Optional[list], metrics: Optional[dict]) -> dict[str, Any]:
    if nodes is None:
        return {"available": False, "nodes": []}
    by_uuid = {m.get("nodeUuid"): m for m in ((metrics or {}).get("nodes") or [])}
    out = []
    for n in nodes:
        m = by_uuid.get(n.get("uuid")) or {}
        if n.get("isDisabled"):
            state = "disabled"
        elif n.get("isConnected"):
            state = "online"
        elif n.get("isConnecting"):
            state = "connecting"
        else:
            state = "offline"
        limit = n.get("trafficLimitBytes")
        used = n.get("trafficUsedBytes")
        out.append({
            "uuid": n.get("uuid"),
            "name": n.get("name"),
            "country": n.get("countryCode"),
            "state": state,
            "status_message": n.get("lastStatusMessage"),
            "status_changed_at": n.get("lastStatusChange"),
            "users_online": _int(n.get("usersOnline")),
            "traffic_used_bytes": _int(used) if used is not None else None,
            "traffic_limit_bytes": _int(limit) if limit else None,
            "xray_uptime_seconds": _int(n.get("xrayUptime")),
            "versions": n.get("versions"),
            "provider": (n.get("provider") or {}).get("name") if n.get("provider") else m.get("providerName"),
            "tags": n.get("tags") or [],
        })
    out.sort(key=lambda x: ({"offline": 0, "connecting": 1, "online": 2, "disabled": 3}[x["state"]], x["name"] or ""))
    disabled = sum(1 for x in out if x["state"] == "disabled")
    return {
        "available": True,
        "nodes": out,
        "total": len(out),
        # Switched off on purpose in the panel: not a problem, not "offline".
        "disabled": disabled,
        "enabled": len(out) - disabled,
        "online": sum(1 for x in out if x["state"] == "online"),
        "offline": sum(1 for x in out if x["state"] in ("offline", "connecting")),
        "users_online": sum(x["users_online"] for x in out),
    }


def normalize_usage(raw: Optional[dict]) -> dict[str, Any]:
    if not raw:
        return {"available": False, "categories": [], "series": []}
    return {
        "available": True,
        "categories": raw.get("categories") or [],
        "total": [_int(v) for v in (raw.get("sparklineData") or [])],
        "series": [
            {"name": s.get("name"), "country": s.get("countryCode"),
             "total_bytes": _int(s.get("total")), "data": [_int(v) for v in (s.get("data") or [])]}
            for s in (raw.get("series") or [])
        ],
    }


async def system_overview() -> dict[str, Any]:
    async def load() -> dict[str, Any]:
        stats, bandwidth, hwid = await asyncio.gather(
            remnawave_api.get_system_stats(),
            remnawave_api.get_bandwidth_stats(),
            remnawave_api.get_hwid_stats(),
            return_exceptions=True,
        )
        return {
            "system": normalize_system(stats if isinstance(stats, dict) else None),
            "bandwidth": normalize_bandwidth(bandwidth if isinstance(bandwidth, dict) else None),
            "hwid": normalize_hwid(hwid if isinstance(hwid, dict) else None),
        }
    return await cached("panel:overview", PANEL_TTL_SECONDS, load)


async def nodes_overview() -> dict[str, Any]:
    async def load() -> dict[str, Any]:
        nodes, metrics = await asyncio.gather(
            remnawave_api.get_nodes(), remnawave_api.get_nodes_metrics(),
            return_exceptions=True,
        )
        return normalize_nodes(
            nodes if isinstance(nodes, list) else None,
            metrics if isinstance(metrics, dict) else None,
        )
    return await cached("panel:nodes", PANEL_TTL_SECONDS, load)


# P2-17: "today" of the bandwidth window is the admin's day (MSK, like every
# other day boundary on the dashboard), not the host's local date.
_MSK = timezone(timedelta(hours=3))


async def nodes_usage(days: int, today: Optional[date] = None) -> dict[str, Any]:
    end = today or datetime.now(_MSK).date()
    start = end - timedelta(days=days - 1)

    async def load() -> dict[str, Any]:
        try:
            raw = await remnawave_api.get_nodes_usage(start.isoformat(), end.isoformat())
        except Exception as e:  # never let the panel break the dashboard
            logger.warning("panel nodes usage failed: %s", e)
            raw = None
        return normalize_usage(raw if isinstance(raw, dict) else None)
    return await cached(f"panel:usage:{start}:{end}", PANEL_TTL_SECONDS, load)


def discrepancy(panel_system: dict, db_counts: dict) -> dict[str, Any]:
    """Panel ACTIVE vs what the DB says should be active.

    Every user has two panel entities (premium + bypass), so the expected
    figure is premium-with-access + bypass entities. A bypass entity in
    LIMITED is legitimate (GB used up), so this is a signal, not an exact
    reconciliation.
    """
    if not panel_system.get("available"):
        return {"available": False}
    expected = int(db_counts.get("premium_active", 0)) + int(db_counts.get("bypass_entities", 0))
    active = panel_system["status_counts"].get("ACTIVE", 0)
    limited = panel_system["status_counts"].get("LIMITED", 0)
    return {
        "available": True,
        "panel_active": active,
        "panel_active_or_limited": active + limited,
        "db_expected": expected,
        "db_premium_active": int(db_counts.get("premium_active", 0)),
        "db_bypass_entities": int(db_counts.get("bypass_entities", 0)),
        "difference": active + limited - expected,
    }
