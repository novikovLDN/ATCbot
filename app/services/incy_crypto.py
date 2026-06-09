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
    the Incy button.

    Today `to_incy_link()` is pure-Python (no Node, no encryption —
    just `incy://add/<plain_url>`), so we always return True. The
    sidecar / `_disabled` machinery is kept around for the future
    crypt1 path; once that comes back online we can re-introduce a
    real availability check, e.g. by routing through
    `to_incy_link_crypt1()` and checking the kill-switch flag."""
    return True


async def selftest() -> bool:
    """One-shot smoke test — encode a known URL and verify the result
    looks like a valid incy://crypt1/ link. Called from main.py on
    startup so any deployment-time breakage (no node, missing package,
    bad cwd) shows up loud in the log immediately, not after the first
    user taps a broken button.

    Returns True on success, False on any failure (and flips _disabled
    via the underlying _spawn machinery). Doesn't raise."""
    if _disabled:
        return False
    if not _SCRIPT_PATH.is_file():
        _mark_disabled(f"sidecar not found at {_SCRIPT_PATH}")
        return False
    try:
        sample = await _spawn("https://selftest.atlassecure.ru/sub/00000000")
    except Exception:
        logger.exception("INCY_SELFTEST_CRASH")
        return False
    if not sample:
        # _spawn already set _disabled if the cause was permanent;
        # transient timeouts/etc fall through here without disabling,
        # so the next real call gets another chance.
        logger.warning("INCY_SELFTEST_FAIL: no output (see WARNING above)")
        return False
    logger.info(
        "INCY_SELFTEST_OK: produced link of len=%d (sample=%s…)",
        len(sample), sample[:40],
    )
    return True


async def _spawn(url: str) -> Optional[str]:
    """Run the Node sidecar once and return stdout, or None on any
    expected-by-the-TZ failure mode. Unexpected failures still return
    None but get a logger.exception trail for debugging."""
    cwd = str(_SCRIPT_PATH.parent.parent)  # repo root so npm resolves node_modules
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(_SCRIPT_PATH),
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except FileNotFoundError:
        _mark_disabled(
            "node binary not on PATH — apt-get install nodejs needed "
            f"(cwd={cwd}, script={_SCRIPT_PATH})"
        )
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
    """Wrap a plain subscription URL into an Incy-importable deep link.

    Right now we return `incy://add/<plain_url>`:

      • Universal across every shipped Incy version (iOS, Android,
        Desktop). incy.gitbook.io docs say: «if the data after
        incy://add/ is an http(s) URL, the profile is downloaded
        from this URL» — no decoder version required, no shared
        keymat required.
      • Doesn't need the Node sidecar → no fragile shell-out, can't
        be killed by a missing node_modules, broken docker layer,
        unrooted .dockerignore, npm registry hiccup, etc.

    Trade-off: the subscription URL is plain-text inside the deep
    link. For Happ we use the sealed `happ://crypt4/<base64>`,
    so plain-text on Incy is slightly worse for DPI-evasion. We
    accept that until a published Incy client release actually
    decodes incy://crypt1/ — the npm @incy/link-encoder package's
    keymat fingerprint b6bf708471cc… must match the keymat baked
    into the client app for crypt1 to decode at all. INCY-DEV
    published the npm package on 2026-06-06 but the App Store
    iOS client (v2.2.1 in user's screenshot) didn't ship that
    keymat yet, so crypt1 links surface as
    «Could not determine link type».

    Crypt1 plumbing below (_spawn, selftest, to_incy_link_crypt1)
    is kept callable so the switch is a one-liner once a known-good
    Incy client release lands.
    """
    from urllib.parse import quote
    if not url:
        return None
    safe = quote(url, safe="/:?&=@%+")
    return f"incy://add/{safe}"


async def to_incy_link_crypt1(url: Optional[str]) -> Optional[str]:
    """Original crypt1 variant via the Node sidecar. Kept for the day
    Incy ships a client with the same keymat as `@incy/link-encoder`.
    See `to_incy_link` for the production path."""
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
    "to_incy_link_crypt1",
]
