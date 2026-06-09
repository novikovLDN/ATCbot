"""Incy (`incy://crypt1/<payload>`) deep-link generator.

Incy uses AES-256-GCM with a key baked into both its mobile clients
and the `@incy/link-encoder` npm package — we don't (and can't) re-
implement that key in Python without bundling Incy's binary assets.
So this module shells out to a tiny Node sidecar (`scripts/incy_encode.mjs`).

Graceful degradation
--------------------
If Node.js or the npm package is missing on the host (dev laptop with
no Node, prod that hasn't been re-deployed yet, etc.), the very first
call sets a module-level flag and returns None. The bot caller then
just doesn't show the Incy button — Happ keeps working, no exceptions
escape.

Async
-----
We use `asyncio.create_subprocess_exec` so each encode (~50–150 ms,
mostly Node start-up) doesn't block the FastAPI event loop the way
`subprocess.run` would.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEEP_LINK_PREFIX = "incy://crypt1/"

# scripts/incy_encode.mjs lives at the project root. We resolve relative
# to __file__ so cwd at bot start-up doesn't matter.
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "incy_encode.mjs"
)

# Once we hit a permanent failure (no node, no package), stop spawning
# new processes — every attempt would just lose 50 ms to a guaranteed
# stderr. Set during _spawn(); never reset within a process lifetime.
_disabled: bool = False
_disabled_reason: Optional[str] = None


def _mark_disabled(reason: str) -> None:
    global _disabled, _disabled_reason
    if not _disabled:
        _disabled = True
        _disabled_reason = reason
        logger.warning(
            "INCY_DISABLED: %s — the «Открыть в Incy» button will be hidden",
            reason,
        )


def is_available() -> bool:
    """Cheap check the bot can call before deciding whether to render
    the Incy button. False once we've recorded a permanent failure."""
    return not _disabled and _SCRIPT_PATH.is_file()


async def _spawn(url: str) -> Optional[str]:
    """Run the Node sidecar once and return stdout, or None on any
    expected-by-the-TZ failure mode. Unexpected failures still return
    None but get a logger.exception trail for debugging."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(_SCRIPT_PATH),
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_SCRIPT_PATH.parent.parent),  # repo root so npm finds node_modules
        )
    except FileNotFoundError:
        _mark_disabled("node binary not on PATH")
        return None
    except Exception:
        logger.exception("INCY_SPAWN_FAIL — unexpected exception")
        return None

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        logger.warning("INCY_TIMEOUT — node took >10s, killed")
        return None

    if proc.returncode != 0:
        err_text = (stderr or b"").decode("utf-8", "replace").strip()
        # The two stderr signatures that mean «package isn't installed»
        # — flip the kill-switch so we don't try again every request.
        if (
            "ERR_MODULE_NOT_FOUND" in err_text
            or "Cannot find package" in err_text
            or "Cannot find module" in err_text
        ):
            _mark_disabled(f"@incy/link-encoder not installed: {err_text[:120]}")
            return None
        logger.warning("INCY_NODE_RC=%s stderr=%s", proc.returncode, err_text[:200])
        return None

    out = (stdout or b"").decode("utf-8", "replace").strip()
    if not out.startswith(DEEP_LINK_PREFIX):
        logger.warning("INCY_BAD_OUTPUT: %r", out[:120])
        return None
    return out


async def to_incy_link(url: Optional[str]) -> Optional[str]:
    """Wrap a plain subscription URL into `incy://crypt1/<payload>`.

    Returns None on:
      • empty/None input;
      • node missing, package missing, sub-process timeout / bad output;
      • the `_disabled` kill-switch having been flipped by an earlier call.
    """
    if not url:
        return None
    if _disabled:
        return None
    if not _SCRIPT_PATH.is_file():
        _mark_disabled(f"sidecar not found at {_SCRIPT_PATH}")
        return None
    return await _spawn(url)


__all__ = [
    "DEEP_LINK_PREFIX",
    "is_available",
    "to_incy_link",
]
