"""
Admin: samopis → Remnawave migration script controls.

Five dashboard actions plus a download fallback:

  admin:mig_dryrun50        → --limit 50          (dry-run, no writes)
  admin:mig_dryrun_full     → no limit            (dry-run, no writes)
  admin:mig_apply1_input    → FSM: ask for tg_id, then --apply --telegram-id N --limit 1
  admin:mig_apply10         → --apply --limit 10  (direct, real Remnawave + DB writes)
  admin:mig_apply_all       → confirm dialog → --apply (everything; final cutover)
  admin:mig_apply_all_yes   → 2nd-step "yes I'm sure" → runs --apply
  admin:mig_apply_all_no    → cancel confirm
  admin:migration_download  → DM the most recent /tmp/migration_log.csv

After every run (--apply OR --dry-run) the handler auto-attaches the
freshly-written migration_log.csv as a Telegram document so the operator
doesn't have to tap a second button.

Args to the subprocess are hard-coded per callback — nothing from the
callback payload reaches the process.  The FSM-collected telegram_id
input is validated as a positive int before substitution.

Only the configured ADMIN_TELEGRAM_ID can trigger any of these.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.states import AdminMigrationApply
from app.handlers.common.utils import safe_edit_text
from app.utils.security import admin_only

admin_migration_router = Router()
logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────

# __file__ is /…/app/handlers/admin/migration.py — repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "migrate_samopis_to_remnawave.py"

# Single source of truth for "where the script writes" — must match the
# default in scripts/migrate_samopis_to_remnawave.py.
_LOG_DIR = Path(os.environ.get("MIGRATION_LOG_DIR") or "/tmp")
_LOG_FILE = _LOG_DIR / "migration_log.csv"


# ── Limits ─────────────────────────────────────────────────────────────

# Telegram message body cap (4096 chars). We reserve ~600 for header /
# wrapper text / truncation marker.
_MAX_OUTPUT_CHARS = 3500
# Telegram bot-API document upload cap is 50 MB. Below this we send the
# CSV automatically; above we point the operator at out-of-band fetches.
_MAX_DOWNLOAD_BYTES = 49 * 1024 * 1024

# Per-button subprocess timeouts. Dry-run never hits the panel and is
# DB-only, so it's fast even at full scale.  Apply at 5 RPS preflight
# + 5 RPS POST = ~10 API calls per row; 4200 rows ≈ 14 min minimum so
# we keep a generous 90-min ceiling for Apply ALL.
_TIMEOUT_DRYRUN_LIMITED = 5 * 60      # --limit 50
_TIMEOUT_DRYRUN_FULL = 30 * 60        # ~4200 candidates, DB-only
_TIMEOUT_APPLY_SINGLE = 2 * 60        # one row
_TIMEOUT_APPLY_LIMITED = 10 * 60      # --apply --limit 10
_TIMEOUT_APPLY_FULL = 90 * 60         # full cutover


# ── Subprocess plumbing ────────────────────────────────────────────────

async def _run_script(args: Sequence[str], timeout: int) -> tuple[int, str, str]:
    """Run the migration script with the given args. Returns (rc, stdout, stderr).

    Times out cleanly and never raises — failures are surfaced as a
    non-zero return code with a marker in stderr so the admin can see
    what happened.
    """
    # Preflight: catch deployment misconfiguration (scripts/ excluded by
    # .dockerignore) up-front with an actionable message.
    if not _SCRIPT_PATH.is_file():
        return 127, "", (
            f"script not found at {_SCRIPT_PATH}\n"
            "deployment check: ensure .dockerignore does NOT exclude "
            "scripts/migrate_samopis_to_remnawave.py from the image, "
            "then redeploy."
        )

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

    body_parts: list[tuple[str, str]] = []
    if stdout.strip():
        body_parts.append(("stdout", stdout.rstrip()))
    if stderr.strip():
        body_parts.append(("stderr", stderr.rstrip()))
    if not body_parts:
        body_parts.append(("output", "(empty)"))

    combined = "\n".join(f"--- {label} ---\n{text}" for label, text in body_parts)
    if len(combined) > _MAX_OUTPUT_CHARS:
        kept = combined[-_MAX_OUTPUT_CHARS:]
        truncation = f"\n\n…[truncated, showed last {_MAX_OUTPUT_CHARS} chars of {len(combined)}]"
        combined = kept + truncation

    return f"{header}\n\n<pre>{html.escape(combined)}</pre>"


async def _send_csv_if_available(
    *,
    bot,
    chat_id: int,
    caption_prefix: str,
) -> Optional[str]:
    """Attach the most recent migration_log.csv if it exists.

    Returns a short status string for inclusion in the result message,
    or None if the file was just sent successfully (nothing to add).
    """
    if not _LOG_FILE.is_file():
        return f"no log file at <code>{html.escape(str(_LOG_FILE))}</code>"
    size = _LOG_FILE.stat().st_size
    if size > _MAX_DOWNLOAD_BYTES:
        return (
            f"log too large for Telegram ({size / 1024 / 1024:.1f} MB > 50 MB) — "
            f"fetch via scp/docker cp from <code>{html.escape(str(_LOG_FILE))}</code>"
        )
    try:
        await bot.send_document(
            chat_id,
            FSInputFile(str(_LOG_FILE), filename="migration_log.csv"),
            caption=(
                f"📊 {caption_prefix} — migration_log.csv ({size / 1024:.1f} KB)\n"
                f"path: <code>{html.escape(str(_LOG_FILE))}</code>"
            ),
            parse_mode="HTML",
        )
        return None
    except Exception as e:
        logger.exception("MIG_AUTO_CSV_FAIL: chat=%s", chat_id)
        return f"auto-attach failed: {html.escape(type(e).__name__)}"


class _MessageEntryShim:
    """Adapter that lets a plain Message drive _run_and_report.

    The flow expects a `CallbackQuery` interface (answer, message, bot,
    from_user) — but the Apply-1 path enters from a text message after
    FSM input.  Wrapping the status Message in this shim avoids
    duplicating _run_and_report for the two entry points.

    Note: `status_msg.from_user` is the BOT (it's the outgoing
    placeholder we just sent), so the caller must pass the admin's
    real id explicitly.
    """

    def __init__(self, status_msg: Message, admin_tg_id: int):
        from types import SimpleNamespace
        self.message = status_msg
        self.bot = status_msg.bot
        self.from_user = SimpleNamespace(id=admin_tg_id)

    async def answer(self, *args, **kwargs):
        # No-op: not a callback; nothing to acknowledge.
        return None


async def _run_and_report(
    callback,  # CallbackQuery or _MessageEntryShim
    *,
    title: str,
    args: Sequence[str],
    timeout: int,
    placeholder_text: str,
) -> None:
    """Single end-to-end flow used by every action button.

    1. Replaces the dashboard message with a placeholder.
    2. Runs the subprocess.
    3. Edits the placeholder with stdout/stderr summary.
    4. DMs the CSV log (when present) as a follow-up document.
    """
    await callback.answer("⏳ Running...")
    await safe_edit_text(
        callback.message,
        placeholder_text,
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )

    rc, stdout, stderr = await _run_script(args, timeout=timeout)
    text = _format_output(title, rc, stdout, stderr)

    csv_note = await _send_csv_if_available(
        bot=callback.bot,
        chat_id=callback.from_user.id,
        caption_prefix=title,
    )
    if csv_note:
        text += f"\n\n<i>📎 CSV: {csv_note}</i>"

    logger.info(
        "ADMIN_MIGRATION_RUN: tg=%s title=%s rc=%s args=%s",
        callback.from_user.id, title, rc, list(args),
    )
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )


# ── Action buttons ─────────────────────────────────────────────────────

@admin_migration_router.callback_query(F.data == "admin:mig_dryrun50")
@admin_only
async def callback_dryrun_50(callback: CallbackQuery):
    await _run_and_report(
        callback,
        title="dry-run --limit 50",
        args=["--limit", "50"],
        timeout=_TIMEOUT_DRYRUN_LIMITED,
        placeholder_text=(
            "⏳ <i>Dry-run with <code>--limit 50</code>…</i>\n"
            "(read-only — ничего не пишется ни в Remnawave, ни в БД)"
        ),
    )


@admin_migration_router.callback_query(F.data == "admin:mig_dryrun_full")
@admin_only
async def callback_dryrun_full(callback: CallbackQuery):
    await _run_and_report(
        callback,
        title="dry-run FULL",
        args=[],  # no --limit → all candidates
        timeout=_TIMEOUT_DRYRUN_FULL,
        placeholder_text=(
            "⏳ <i>Dry-run for ALL candidates (no <code>--limit</code>)…</i>\n"
            "(read-only — может занять до 5 минут на ~4k кандидатов)"
        ),
    )


@admin_migration_router.callback_query(F.data == "admin:mig_apply10")
@admin_only
async def callback_apply_10(callback: CallbackQuery):
    await _run_and_report(
        callback,
        title="--apply --limit 10",
        args=["--apply", "--limit", "10"],
        timeout=_TIMEOUT_APPLY_LIMITED,
        placeholder_text=(
            "⏳ <i>Apply on first 10 candidates…</i>\n"
            "<b>WRITES to Remnawave + DB.</b>"
        ),
    )


# ── Apply 1 (test) — FSM: ask for telegram_id ──────────────────────────

@admin_migration_router.callback_query(F.data == "admin:mig_apply1_input")
@admin_only
async def callback_apply_1_input(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AdminMigrationApply.waiting_for_telegram_id)
    await safe_edit_text(
        callback.message,
        (
            "🎯 <b>Apply 1 (single user)</b>\n\n"
            "Send the Telegram ID of the user to migrate.\n"
            "Reply with a plain number (e.g. <code>210948123</code>).\n"
            "Send <code>cancel</code> to abort."
        ),
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_migration_router.message(AdminMigrationApply.waiting_for_telegram_id)
@admin_only
async def message_apply_1_id(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.lower() in {"cancel", "отмена", "/cancel"}:
        await state.clear()
        await message.answer("❌ Cancelled.", reply_markup=get_admin_back_keyboard("ru"))
        return

    try:
        tg_id = int(raw)
        if tg_id <= 0:
            raise ValueError("telegram_id must be positive")
    except (TypeError, ValueError):
        await message.answer(
            "⚠️ Not a valid Telegram ID. Send a positive integer or <code>cancel</code>.",
            parse_mode="HTML",
        )
        return

    await state.clear()

    # Send a fresh status message to use as the placeholder canvas, then
    # run through the same flow as the callback path via a thin shim.
    status_msg = await message.answer("⏳ <i>preparing…</i>", parse_mode="HTML")
    shim = _MessageEntryShim(status_msg, admin_tg_id=message.from_user.id)
    await _run_and_report(
        shim,
        title=f"--apply --telegram-id {tg_id} --limit 1",
        args=["--apply", "--telegram-id", str(tg_id), "--limit", "1"],
        timeout=_TIMEOUT_APPLY_SINGLE,
        placeholder_text=(
            f"⏳ <i>Apply on tg_id=<code>{tg_id}</code>…</i>\n"
            "<b>WRITES to Remnawave + DB.</b>"
        ),
    )


# ── Apply ALL — two-step confirm ───────────────────────────────────────

def _apply_all_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Да, мигрировать ВСЕХ", callback_data="admin:mig_apply_all_yes"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin:mig_apply_all_no"),
        ],
    ])


@admin_migration_router.callback_query(F.data == "admin:mig_apply_all")
@admin_only
async def callback_apply_all_confirm(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminMigrationApply.confirm_apply_all)
    await safe_edit_text(
        callback.message,
        (
            "🚨 <b>Apply ALL — финальная миграция</b>\n\n"
            "Это создаст премиум-entity в Remnawave для <u>всех</u> "
            "активных платных подписок, у которых нет ещё "
            "<code>remnawave_premium_uuid</code>.\n\n"
            "Действие необратимо без ручной чистки в панели.\n"
            "До 90 минут на ~4k кандидатов при 5 RPS.\n\n"
            "Точно поехали?"
        ),
        reply_markup=_apply_all_confirm_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_migration_router.callback_query(F.data == "admin:mig_apply_all_no")
@admin_only
async def callback_apply_all_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit_text(
        callback.message,
        "❌ <i>Cancelled.</i>",
        reply_markup=get_admin_back_keyboard("ru"),
        parse_mode="HTML",
    )
    await callback.answer("Cancelled")


@admin_migration_router.callback_query(F.data == "admin:mig_apply_all_yes")
@admin_only
async def callback_apply_all_yes(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _run_and_report(
        callback,
        title="--apply (FULL)",
        args=["--apply"],
        timeout=_TIMEOUT_APPLY_FULL,
        placeholder_text=(
            "⏳ <i>Apply on ALL candidates…</i>\n"
            "<b>Не закрывай вкладку</b> — обновлю когда отработает. "
            "Лимит времени 90 мин."
        ),
    )


# ── Download log — manual fallback (auto-send already attaches it) ─────

@admin_migration_router.callback_query(F.data == "admin:migration_download")
@admin_only
async def callback_migration_download(callback: CallbackQuery):
    """Send the migration_log.csv produced by the most recent run."""
    await callback.answer("📥 Sending log...")
    if not _LOG_FILE.is_file():
        await safe_edit_text(
            callback.message,
            (
                "❌ <b>No migration log found</b>\n\n"
                f"Expected at: <code>{html.escape(str(_LOG_FILE))}</code>\n"
                "Run a dry-run (🔍 button) first to generate it."
            ),
            reply_markup=get_admin_back_keyboard("ru"),
            parse_mode="HTML",
        )
        return

    size = _LOG_FILE.stat().st_size
    if size > _MAX_DOWNLOAD_BYTES:
        await safe_edit_text(
            callback.message,
            (
                "❌ <b>Log file too large for Telegram</b>\n\n"
                f"Path: <code>{html.escape(str(_LOG_FILE))}</code>\n"
                f"Size: {size / 1024 / 1024:.1f} MB (max 50 MB)\n"
                "Pull it manually from the host (scp / kubectl cp / "
                "docker cp) and consider archiving older runs."
            ),
            reply_markup=get_admin_back_keyboard("ru"),
            parse_mode="HTML",
        )
        return

    try:
        document = FSInputFile(str(_LOG_FILE), filename="migration_log.csv")
        await callback.bot.send_document(
            callback.from_user.id,
            document,
            caption=(
                f"📊 migration_log.csv ({size / 1024:.1f} KB)\n"
                f"path: <code>{html.escape(str(_LOG_FILE))}</code>"
            ),
            parse_mode="HTML",
        )
        logger.info(
            "ADMIN_MIGRATION_DOWNLOAD: tg=%s path=%s size=%s",
            callback.from_user.id, _LOG_FILE, size,
        )
        await safe_edit_text(
            callback.message,
            f"✅ <b>migration_log.csv отправлен</b> ({size / 1024:.1f} KB)",
            reply_markup=get_admin_back_keyboard("ru"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("ADMIN_MIGRATION_DOWNLOAD_FAIL: tg=%s", callback.from_user.id)
        await safe_edit_text(
            callback.message,
            f"❌ <b>Send failed:</b>\n<pre>{html.escape(str(e))[:500]}</pre>",
            reply_markup=get_admin_back_keyboard("ru"),
            parse_mode="HTML",
        )


__all__ = ["admin_migration_router"]
