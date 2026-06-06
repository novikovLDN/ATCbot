"""Admin-level notification toggles persisted in Redis (memory fallback).

Three categories, each on/off:
  - payment_error
  - broadcast_done
  - revenue_milestone

Defaults to all ON when the key is missing so first-time admins get
the full feed.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_REDIS_KEY = "dashboard:notifications_enabled"

_DEFAULTS = {
    "payment_error": True,
    "broadcast_done": True,
    "revenue_milestone": True,
}

_MEM_CACHE: dict[str, bool] = dict(_DEFAULTS)


async def _redis():
    try:
        from app.utils.redis_client import get_client, is_configured
        if not is_configured():
            return None
        return await get_client()
    except Exception:
        return None


async def get_notification_flags() -> dict[str, bool]:
    """Returns current toggles, fully populated (missing keys → default)."""
    r = await _redis()
    if r is not None:
        try:
            raw = await r.get(_REDIS_KEY)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                stored = json.loads(raw)
                merged = {**_DEFAULTS, **{k: bool(v) for k, v in stored.items()}}
                return merged
        except Exception as e:
            logger.warning("notification flags redis read failed: %s", e)
    return {**_DEFAULTS, **_MEM_CACHE}


async def set_notification_flag(key: str, enabled: bool) -> dict[str, bool]:
    """Update one flag, write the merged dict back. Returns new state."""
    if key not in _DEFAULTS:
        raise ValueError(f"unknown notification flag: {key}")
    current = await get_notification_flags()
    current[key] = bool(enabled)
    r = await _redis()
    if r is not None:
        try:
            await r.set(_REDIS_KEY, json.dumps(current))
        except Exception as e:
            logger.warning("notification flags redis write failed: %s", e)
    _MEM_CACHE.update(current)
    return current


async def is_enabled(key: str) -> bool:
    flags = await get_notification_flags()
    return flags.get(key, True)
