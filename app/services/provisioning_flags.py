"""Feature flag for the new provisioning outbox (payment core, phase 2).

USE_NEW_PROVISIONING          off | shadow | on   (default: on — emergency kill-switch only)
NEW_PROVISIONING_ENTRYPOINTS  comma-separated subset of ALL_ENTRYPOINTS;
                              empty = every entry point.

Read at call time via config.env() (PROD_/STAGE_ prefix), so the switch
takes effect without a code change. Until shadow mode is implemented
(plan task T17), callers must treat "shadow" exactly like "off".
"""
from __future__ import annotations

import logging

import config

logger = logging.getLogger(__name__)

VALID_MODES = ("off", "shadow", "on")
# Owner decision 2026-09-14: the new provisioning core is the default for every
# entry point. USE_NEW_PROVISIONING=off stays only as an emergency kill-switch.
DEFAULT_MODE = "on"
ALL_ENTRYPOINTS = (
    "webhook",
    "telegram",
    "balance",
    "autorenew",
    "admin",
    "gift",
    "grants",
    "trial",
)

_warned: set[str] = set()


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg, *args)


def global_mode() -> str:
    raw = (config.env("USE_NEW_PROVISIONING", DEFAULT_MODE) or DEFAULT_MODE).strip().lower()
    if raw not in VALID_MODES:
        _warn_once(f"mode:{raw}", "USE_NEW_PROVISIONING=%r is invalid; treating as %r", raw, DEFAULT_MODE)
        return DEFAULT_MODE
    return raw


def enabled_entrypoints() -> frozenset[str]:
    raw = config.env("NEW_PROVISIONING_ENTRYPOINTS", "") or ""
    items = {x.strip().lower() for x in raw.split(",") if x.strip()}
    if not items:
        return frozenset(ALL_ENTRYPOINTS)
    unknown = items.difference(ALL_ENTRYPOINTS)
    if unknown:
        _warn_once(
            f"ep:{','.join(sorted(unknown))}",
            "NEW_PROVISIONING_ENTRYPOINTS has unknown entries %s; ignored",
            sorted(unknown),
        )
    return frozenset(items.intersection(ALL_ENTRYPOINTS))


def mode_for(entrypoint: str) -> str:
    """Effective mode for one entry point: "off", "shadow" or "on"."""
    if entrypoint not in ALL_ENTRYPOINTS:
        raise ValueError(f"unknown provisioning entrypoint: {entrypoint!r}")
    mode = global_mode()
    if mode == "off" or entrypoint not in enabled_entrypoints():
        return "off"
    return mode


def is_on(entrypoint: str) -> bool:
    """True only when the new outbox path must be used for this entry point."""
    return mode_for(entrypoint) == "on"


__all__ = [
    "ALL_ENTRYPOINTS",
    "VALID_MODES",
    "enabled_entrypoints",
    "global_mode",
    "is_on",
    "mode_for",
]
