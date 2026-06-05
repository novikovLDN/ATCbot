"""
Admin: rollback premium expireAt for users mistakenly pushed to ~10 years.

WHAT THIS FIXES
---------------
A previous run of the "Сверка с Remnawave" tool treated bypass-only rows
in the `subscriptions` table as if they were live premium subscriptions
and PATCHed their Remnawave entities' expireAt to ~2036 (the +10-year
marker that fast_expiry_cleanup writes into the row when a paid sub
expires but the user still has a bypass entity). The result: users who
paid for one month walked away with ten years of premium.

THIS TOOL
---------
1. Scans the DB for affected users: is_bypass_only = TRUE AND
   remnawave_premium_uuid IS NOT NULL AND expires_at > NOW + 5 years.
2. Computes each user's REAL last paid premium end date from
   pending_purchases (paid status, real subscription tariffs, period_days
   summed incrementally to respect renewal stacking).
3. Compares to the panel's current expireAt for that uuid.
4. (Dry-run) shows what would change.
5. (Apply) PATCHes the panel back to the real date. Bypass entities are
   NEVER touched — this tool only knows about the premium uuid.

The DB rows themselves are left as they are — the +10-year value in
`expires_at` is a legitimate bypass-only marker that other code relies
on. Only the panel's premium expireAt is rolled back.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
import database
from app.services import remnawave_api, remnawave_premium
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_premium_recovery_router = Router()
logger = logging.getLogger(__name__)

# Tolerance for "the panel matches DB-real" — within an hour we treat it
# as already correct, no patch needed.
_TOLERANCE_SECONDS = 3600
# Concurrent panel PATCH calls during apply.
_FIX_CONCURRENCY = 8
# Seconds between live progress updates.
_PROGRESS_INTERVAL = 4
# Hard ceiling on scan size (sanity guard).
_MAX_SCAN = 100_000

# Per-admin scan result, feeds the "Apply" button. Lost on bot restart —
# that's fine, just rescan.
_last_plan: dict[int, list] = {}


def _parse_rmn_dt(value) -> "datetime | None":
    """Parse a Remnawave ISO-8601 expireAt string into UTC datetime."""
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _compute_real_end(history: list) -> "datetime | None":
    """Replay paid subscription purchases to derive the user's real end.

    Respects renewal stacking: if a renewal arrives while the previous
    period is still running, its days are added to the existing end;
    otherwise the new period starts at created_at.

    Returns None if there's no paid history at all (i.e. user never had
    a real paid premium subscription).
    """
    end: "datetime | None" = None
    for row in history:
        created = row["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        days = int(row["period_days"] or 0)
        if days <= 0:
            continue
        if end is None or created >= end:
            end = created + timedelta(days=days)
        else:
            end = end + timedelta(days=days)
    return end


async def _scan(progress: "dict | None" = None) -> "tuple[int, list]":
    """Build the recovery plan.

    Each item:
      {telegram_id, panel_uuid, panel_expires, real_end, action}
    where `action` is one of:
      - 'patch'    — panel expireAt > real_end + tolerance → roll back
      - 'expire'   — real_end already in the past → set panel to real_end
                     (the entity will simply be expired on the panel)
      - 'ok'       — panel is already aligned, no work
      - 'no_history'  — no paid premium history found; user shouldn't
                        have premium at all. Skipped from auto-fix
                        (manual review).
      - 'panel_missing' — entity not found on the panel anymore. Skipped.
    """
    candidates = await database.get_premium_recovery_candidates()
    candidates = candidates[:_MAX_SCAN]

    if progress is not None:
        progress["phase"] = "fetch"
        progress["total"] = len(candidates)
        progress["done"] = 0
        progress["fetched"] = 0
        progress["fetch_total"] = len(candidates)

    # Fetch panel state per UUID with bounded concurrency.
    #
    # Earlier version did a full paginated `get_all_users` pull — but
    # the panel stalls/loops past ~10k entities and we don't need the
    # other ~9k anyway. Direct GET /api/users/{uuid} per candidate is
    # both cheaper and bounded by our own input size.
    sem = asyncio.Semaphore(8)
    panel_by_uuid: dict = {}

    async def _fetch_one(uuid: str):
        async with sem:
            try:
                user = await remnawave_api.get_user(uuid)
            except Exception as e:
                logger.warning("PREMIUM_RECOVERY: panel fetch failed for %s: %s", uuid[:8], e)
                user = None
        panel_by_uuid[uuid] = user
        if progress is not None:
            progress["fetched"] = len(panel_by_uuid)

    await asyncio.gather(*[_fetch_one(c["remnawave_premium_uuid"]) for c in candidates])

    if progress is not None:
        progress["phase"] = "compute"
        progress["total"] = len(candidates)
        progress["done"] = 0

    # ONE bulk query for the entire candidate list instead of N
    # roundtrips — the per-user version was overloading the pool and
    # stalling the scan on large cohorts (1k+).
    tg_ids = [c["telegram_id"] for c in candidates]
    histories = await database.get_paid_subscription_history_bulk(tg_ids)

    plan: list = []
    now = datetime.now(timezone.utc)
    for cand in candidates:
        if progress is not None:
            progress["done"] += 1
        tg = cand["telegram_id"]
        panel_uuid = cand["remnawave_premium_uuid"]

        rmn = panel_by_uuid.get(panel_uuid)
        if rmn is None:
            plan.append({
                "telegram_id": tg, "panel_uuid": panel_uuid,
                "panel_expires": None, "real_end": None,
                "action": "panel_missing",
            })
            continue
        panel_expires = _parse_rmn_dt(rmn.get("expireAt"))

        history = histories.get(tg, [])
        real_end = _compute_real_end(history)

        if real_end is None:
            plan.append({
                "telegram_id": tg, "panel_uuid": panel_uuid,
                "panel_expires": panel_expires, "real_end": None,
                "action": "no_history",
            })
            continue

        # Already aligned?
        if panel_expires and abs((panel_expires - real_end).total_seconds()) <= _TOLERANCE_SECONDS:
            plan.append({
                "telegram_id": tg, "panel_uuid": panel_uuid,
                "panel_expires": panel_expires, "real_end": real_end,
                "action": "ok",
            })
            continue

        # Panel further than reality → roll back.
        if panel_expires and panel_expires > real_end + timedelta(seconds=_TOLERANCE_SECONDS):
            action = "expire" if real_end < now else "patch"
        else:
            # Panel earlier than DB-real (rare) — leave alone, not this
            # tool's job to grant time.
            action = "ok"

        plan.append({
            "telegram_id": tg, "panel_uuid": panel_uuid,
            "panel_expires": panel_expires, "real_end": real_end,
            "action": action,
        })

    return len(candidates), plan


def _format_dry_run(checked: int, plan: list) -> str:
    by_action: dict = {}
    for p in plan:
        by_action.setdefault(p["action"], []).append(p)

    n_patch = len(by_action.get("patch", []))
    n_expire = len(by_action.get("expire", []))
    n_ok = len(by_action.get("ok", []))
    n_no_hist = len(by_action.get("no_history", []))
    n_missing = len(by_action.get("panel_missing", []))

    lines = [
        "🩹 <b>Откат premium-подписок (Dry-run)</b>",
        "",
        f"Кандидатов в БД: <b>{checked}</b>",
        "",
        f"  • <b>{n_patch}</b> — будет уменьшено expireAt (в будущее, но < 2036)",
        f"  • <b>{n_expire}</b> — реальный срок уже истёк → expireAt = реальная дата",
        f"  • <b>{n_ok}</b> — уже в порядке, пропускаем",
        f"  • <b>{n_no_hist}</b> — нет paid-истории (требуют ручного решения)",
        f"  • <b>{n_missing}</b> — entity не найдена на панели (пропускаем)",
    ]

    sample = (by_action.get("patch", []) + by_action.get("expire", []))[:10]
    if sample:
        lines.append("")
        lines.append("<i>Примеры (до 10):</i>")
        for s in sample:
            tg = s["telegram_id"]
            from_ = s["panel_expires"].strftime("%Y-%m-%d") if s["panel_expires"] else "—"
            to_ = s["real_end"].strftime("%Y-%m-%d") if s["real_end"] else "—"
            lines.append(f"  <code>{tg}</code>: {from_} → {to_}")

    if n_patch + n_expire == 0:
        lines.append("\n✅ Изменений не требуется.")
    else:
        lines.append(
            f"\nПри подтверждении: <b>{n_patch + n_expire}</b> панель-записей "
            "будут откатаны под их реальный paid-срок. Bypass entities "
            "<b>не трогаются</b>."
        )

    return "\n".join(lines)


@admin_premium_recovery_router.callback_query(F.data == "admin:premium_recovery")
async def callback_premium_recovery(callback: CallbackQuery):
    """Dry-run: scan, compute the plan, show the report."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    if not config.REMNAWAVE_ENABLED:
        await safe_edit_text(
            callback.message,
            "🩹 <b>Откат premium-подписок</b>\n\nRemnawave отключён в конфиге.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    await safe_edit_text(
        callback.message,
        "🩹 Считаю план отката…\nЭто dry-run, пока ничего не меняется.",
        bot=callback.bot, parse_mode="HTML",
    )

    progress: dict = {"phase": "fetch", "total": 0, "done": 0,
                     "fetched": 0, "fetch_total": None}
    try:
        scan_task = asyncio.create_task(_scan(progress))
        while not scan_task.done():
            await asyncio.sleep(_PROGRESS_INTERVAL)
            if scan_task.done():
                break
            if progress.get("phase") == "compute" and progress.get("total"):
                text = (
                    "🩹 Сверяю кандидатов…\n\n"
                    f"Обработано: <b>{progress.get('done', 0)}</b> / {progress['total']}"
                )
            else:
                fetched = progress.get("fetched", 0)
                ftotal = progress.get("fetch_total")
                if fetched:
                    if ftotal:
                        text = (
                            "🩹 Выгружаю кандидатов из Remnawave…\n\n"
                            f"Получено: <b>{fetched}</b> / {ftotal}"
                        )
                    else:
                        text = (
                            "🩹 Выгружаю кандидатов из Remnawave…\n\n"
                            f"Получено: <b>{fetched}</b>"
                        )
                else:
                    text = "🩹 Выгружаю кандидатов из Remnawave…"
            try:
                await safe_edit_text(
                    callback.message, text, bot=callback.bot, parse_mode="HTML",
                )
            except Exception:
                pass
        checked, plan = await scan_task
    except Exception as e:
        logger.exception("PREMIUM_RECOVERY: scan failed: %s", e)
        await safe_edit_text(
            callback.message,
            f"❌ Ошибка при сканировании: {e}",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    _last_plan[callback.from_user.id] = plan

    actionable = [p for p in plan if p["action"] in ("patch", "expire")]
    rows = []
    if actionable:
        rows.append([InlineKeyboardButton(
            text=f"🩹 Применить ({len(actionable)})",
            callback_data="admin:premium_recovery_apply",
        )])
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])

    await safe_edit_text(
        callback.message,
        _format_dry_run(checked, plan),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_premium_recovery_router.callback_query(F.data == "admin:premium_recovery_apply")
async def callback_premium_recovery_apply(callback: CallbackQuery):
    """Apply: PATCH the panel for everyone in the plan with action ∈ {patch, expire}."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    plan = _last_plan.get(callback.from_user.id)
    if not plan:
        await safe_edit_text(
            callback.message,
            "🩹 <b>Откат premium-подписок</b>\n\nНет плана — сначала запустите Dry-run.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    actionable = [p for p in plan if p["action"] in ("patch", "expire")]
    total = len(actionable)
    if total == 0:
        await safe_edit_text(
            callback.message,
            "🩹 <b>Откат premium-подписок</b>\n\nИзменений не требуется.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    await safe_edit_text(
        callback.message,
        f"🩹 Применяю откат для {total} записей…\n\n"
        "Bypass entities не трогаются.",
        bot=callback.bot, parse_mode="HTML",
    )

    sem = asyncio.Semaphore(_FIX_CONCURRENCY)
    progress: dict = {"done": 0, "ok": 0, "failed": 0}

    async def _fix_one(p: dict) -> bool:
        async with sem:
            ok = False
            try:
                ok = await remnawave_premium.renew_premium_user(
                    p["telegram_id"], p["real_end"],
                )
                if ok:
                    progress["ok"] += 1
                    logger.info(
                        "PREMIUM_RECOVERY_PATCHED tg=%s uuid=%s from=%s to=%s action=%s",
                        p["telegram_id"], p["panel_uuid"][:8],
                        p["panel_expires"].isoformat() if p["panel_expires"] else "—",
                        p["real_end"].isoformat() if p["real_end"] else "—",
                        p["action"],
                    )
            except Exception as e:
                logger.warning(
                    "PREMIUM_RECOVERY: tg=%s uuid=%s failed: %s",
                    p["telegram_id"], p["panel_uuid"][:8], e,
                )
                ok = False
            if not ok:
                progress["failed"] += 1
        progress["done"] += 1
        return bool(ok)

    async def _run_all_fixes():
        return await asyncio.gather(*[_fix_one(p) for p in actionable])

    fix_task = asyncio.create_task(_run_all_fixes())
    while not fix_task.done():
        await asyncio.sleep(_PROGRESS_INTERVAL)
        if fix_task.done():
            break
        try:
            await safe_edit_text(
                callback.message,
                "🩹 Применяю откат…\n\n"
                f"Обработано: <b>{progress['done']}</b> / {total}\n"
                f"  ✅ Откатано: {progress['ok']}\n"
                f"  ❌ Не удалось: {progress['failed']}",
                bot=callback.bot, parse_mode="HTML",
            )
        except Exception:
            pass

    await fix_task
    _last_plan.pop(callback.from_user.id, None)

    text = (
        "🩹 <b>Откат premium-подписок завершён</b>\n\n"
        f"✅ Откатано: <b>{progress['ok']}</b> / {total}\n"
        f"❌ Не удалось: <b>{progress['failed']}</b>\n\n"
        "<i>Bypass entities остались нетронутыми.</i>\n"
        "Запустите Dry-run повторно, чтобы убедиться."
    )
    await safe_edit_text(
        callback.message, text,
        reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
    )
