"""
Admin: samopis → Remnawave migration script controls.

Exposes two admin-dashboard buttons that execute the standalone migration
CLI in subprocess form and stream the captured output back to the admin:

  admin:migration_help     → `scripts/migrate_samopis_to_remnawave.py --help`
  admin:migration_dryrun   → `scripts/migrate_samopis_to_remnawave.py --limit 10`
                              (dry-run, NO --apply — read-only on Remnawave
                              and the bot DB)

The buttons run the same script the operator would run on a shell, so the
behaviour stays identical to a manual invocation.  Arguments are
hard-coded; nothing from the callback payload reaches the subprocess.

Only the configured ADMIN_TELEGRAM_ID can trigger these — enforced by
the @admin_only decorator on every callback.
"""
from __future__ import annotations

import asyncio
import html
import logging
import sys
from pathlib import Path
from typing import Sequence

from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text
from app.utils.security import admin_only

admin_migration_router = Router()
logger = logging.getLogger(__name__)

# Resolve script path once at module load.  __file__ is
#   /…/app/handlers/admin/migration.py
# so the repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "migrate_samopis_to_remnawave.py"

# Telegram caps a message body at 4096 chars; we leave room for the
# wrapper text + <pre> tags + a possible truncation marker.
_MAX_OUTPUT_CHARS = 3500
_HELP_TIMEOUT_SECONDS = 30
_DRYRUN_TIMEOUT_SECONDS = 300  # dry-run hits the DB but never the panel


async def _run_script(args: Sequence[str], timeout: int) -> tuple[int, str, str]:
    """Run the migration script with the given args. Returns (rc, stdout, stderr).

    Times out cleanly and never raises — failures are surfaced as a
    non-zero return code with the timeout marker in stderr so the admin
    can see what happened.
    """
    cmd = [sys.executable, "-u", str(_SCRIPT_PATH), *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        return 127, "", f"interpreter not found: {e}"
    except Exception as e:
        return 1, "", f"failed to spawn: {type(e).__name__}: {e}"

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            pass
        return 124, "", f"⏱ TIMEOUT after {timeout}s — process killed"

    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")


def _format_output(title: str, rc: int, stdout: str, stderr: str) -> str:
    """Compose a Telegram-friendly HTML message from script output."""
    rc_icon = "✅" if rc == 0 else "⚠️" if rc == 2 else "❌"
    header = f"{rc_icon} <b>{html.escape(title)}</b>\nexit code: <code>{rc}</code>"

    body_parts: list[str] = []
    if stdout.strip():
        body_parts.append(("stdout", stdout.rstrip()))
    if stderr.strip():
        body_parts.append(("stderr", stderr.rstrip()))
    if not body_parts:
        body_parts.append(("output", "(empty)"))

    # Telegram budget is shared across all blocks. Reserve a slice per
    # block proportional to its size, but always at least show the tail
    # which contains the summary line "Done. ok=N recovered=…".
    combined = "\n".join(f"--- {label} ---\n{text}" for label, text in body_parts)
    if len(combined) > _MAX_OUTPUT_CHARS:
        kept = combined[-_MAX_OUTPUT_CHARS:]
        truncation = f"\n\n…[truncated, showed last {_MAX_OUTPUT_CHARS} chars of {len(combined)}]"
        combined = kept + truncation

    return f"{header}\n\n<pre>{html.escape(combined)}</pre>"


# ── Callback handlers ──────────────────────────────────────────────────

@admin_migration_router.callback_query(F.data == "admin:migration_help")
@admin_only
async def callback_migration_help(callback: CallbackQuery):
    """Run `migrate_samopis_to_remnawave.py --help` and show output."""
    await callback.answer("⏳ Running --help...")
    placeholder = "⏳ <i>Running <code>scripts/migrate_samopis_to_remnawave.py --help</code>…</i>"
    await safe_edit_text(
        callback.message,
        placeholder,
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )

    rc, stdout, stderr = await _run_script(["--help"], timeout=_HELP_TIMEOUT_SECONDS)
    text = _format_output("migrate_samopis_to_remnawave.py --help", rc, stdout, stderr)

    logger.info(
        "ADMIN_MIGRATION_HELP: tg=%s rc=%s stdout_len=%s stderr_len=%s",
        callback.from_user.id, rc, len(stdout), len(stderr),
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )


@admin_migration_router.callback_query(F.data == "admin:migration_dryrun")
@admin_only
async def callback_migration_dryrun(callback: CallbackQuery):
    """Run `migrate_samopis_to_remnawave.py --limit 10` (dry-run by default)."""
    await callback.answer("⏳ Running dry-run (limit 10)...")
    placeholder = (
        "⏳ <i>Running <code>scripts/migrate_samopis_to_remnawave.py --limit 10</code>…</i>\n"
        "(dry-run mode — без <code>--apply</code>, ничего не пишется в Remnawave и БД)"
    )
    await safe_edit_text(
        callback.message,
        placeholder,
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )

    rc, stdout, stderr = await _run_script(["--limit", "10"], timeout=_DRYRUN_TIMEOUT_SECONDS)
    text = _format_output("migrate_samopis_to_remnawave.py --limit 10 (dry-run)", rc, stdout, stderr)

    logger.info(
        "ADMIN_MIGRATION_DRYRUN: tg=%s rc=%s stdout_len=%s stderr_len=%s",
        callback.from_user.id, rc, len(stdout), len(stderr),
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )


__all__ = ["admin_migration_router"]
