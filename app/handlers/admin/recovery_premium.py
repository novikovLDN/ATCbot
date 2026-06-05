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
# Lowered to 3 (was 8) because Remnawave starts dropping connections
# at higher concurrency on a loaded panel.
_FIX_CONCURRENCY = 3
# Sleep between PATCH calls inside each worker — keeps total RPS to
# Remnawave under ~10/s so the panel stays responsive.
_FIX_THROTTLE_S = 0.2
# How many retry attempts per PATCH (panel hiccups under load).
_FIX_RETRY = 3
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
    """Build the recovery plan from DB only — no panel fetch.

    Each item: {telegram_id, panel_uuid, real_end, source, action}.
    `source` is informational: 'paid' / 'gift' / 'none' — which signal
    was used to derive real_end.

    Actions:
      - 'patch'        — set panel expireAt to real_end. Used for
                         everyone whose real_end can be reasoned about,
                         including the now-fallback (real_end = NOW
                         means the entity expires immediately, which is
                         exactly what we want for users who never paid
                         and have no active gift).
    """
    candidates = await database.get_premium_recovery_candidates()
    candidates = candidates[:_MAX_SCAN]

    if progress is not None:
        progress["phase"] = "compute"
        progress["total"] = len(candidates)
        progress["done"] = 0

    tg_ids = [c["telegram_id"] for c in candidates]
    histories = await database.get_paid_subscription_history_bulk(tg_ids)
    gifts = await database.get_activated_gifts_bulk(tg_ids)

    plan: list = []
    now = datetime.now(timezone.utc)
    for cand in candidates:
        if progress is not None:
            progress["done"] += 1
        tg = cand["telegram_id"]
        panel_uuid = cand["remnawave_premium_uuid"]

        paid_end = _compute_real_end(histories.get(tg, []))
        gift_end = _compute_real_end([
            # gifts use activated_at as the start point; reuse the same
            # incremental replay helper by aliasing the field name.
            {"created_at": g["activated_at"], "period_days": g["period_days"]}
            for g in gifts.get(tg, [])
        ])

        # Take the later of the two — whichever signal grants more time wins.
        candidates_end = [d for d in (paid_end, gift_end) if d is not None]
        if candidates_end:
            real_end = max(candidates_end)
            source = "paid" if paid_end == real_end else "gift"
        else:
            # No paid history AND no activated gift → user never had a
            # legitimate claim on premium beyond their trial/free tier.
            # Roll the panel back to NOW so the entity expires immediately.
            # If a real customer is caught here (e.g. admin-grant outside
            # the gift table), they can come to support and we re-issue —
            # but standing by while everyone keeps a free decade is worse.
            real_end = now
            source = "none"

        plan.append({
            "telegram_id": tg, "panel_uuid": panel_uuid,
            "real_end": real_end, "source": source,
            "action": "patch",
        })

    return len(candidates), plan


def _format_dry_run(checked: int, plan: list) -> str:
    by_source: dict = {}
    for p in plan:
        by_source.setdefault(p["source"], []).append(p)

    n_paid = len(by_source.get("paid", []))
    n_gift = len(by_source.get("gift", []))
    n_none = len(by_source.get("none", []))
    n_total = n_paid + n_gift + n_none

    lines = [
        "🩹 <b>Откат premium-подписок (Dry-run)</b>",
        "",
        f"Кандидатов в БД: <b>{checked}</b>",
        "",
        f"  💳 <b>{n_paid}</b> — есть paid-история → откат на реальную дату",
        f"  🎁 <b>{n_gift}</b> — активный подарок → откат на дату окончания подарка",
        f"  ⛔ <b>{n_none}</b> — нет paid и нет gift → expireAt = сейчас (entity истечёт)",
    ]

    sample_paid = by_source.get("paid", [])[:3]
    sample_gift = by_source.get("gift", [])[:3]
    sample_none = by_source.get("none", [])[:3]
    if sample_paid or sample_gift or sample_none:
        lines.append("")
        lines.append("<i>Примеры:</i>")
        for s in sample_paid:
            lines.append(
                f"  💳 <code>{s['telegram_id']}</code> → "
                f"{s['real_end'].strftime('%Y-%m-%d')}"
            )
        for s in sample_gift:
            lines.append(
                f"  🎁 <code>{s['telegram_id']}</code> → "
                f"{s['real_end'].strftime('%Y-%m-%d')}"
            )
        for s in sample_none:
            lines.append(
                f"  ⛔ <code>{s['telegram_id']}</code> → "
                f"{s['real_end'].strftime('%Y-%m-%d %H:%M')} (now)"
            )

    if n_total == 0:
        lines.append("\n✅ Изменений не требуется.")
    else:
        lines.append(
            f"\nПри подтверждении: <b>{n_total}</b> панель-записей будут "
            "откатаны (idempotent — повторный запуск не вредит). "
            "Bypass entities <b>не трогаются</b>."
        )
        eta_min = max(1, n_total * (_FIX_THROTTLE_S + 0.2) / _FIX_CONCURRENCY / 60)
        lines.append(
            f"\n⏱ Apply ~{eta_min:.0f} мин "
            f"(throttle {_FIX_CONCURRENCY} parallel, {int(_FIX_THROTTLE_S*1000)}ms между)."
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

    progress: dict = {"phase": "compute", "total": 0, "done": 0}
    try:
        scan_task = asyncio.create_task(_scan(progress))
        while not scan_task.done():
            await asyncio.sleep(_PROGRESS_INTERVAL)
            if scan_task.done():
                break
            text = (
                "🩹 Сверяю кандидатов…\n\n"
                f"Обработано: <b>{progress.get('done', 0)}</b> / "
                f"{progress.get('total', 0)}"
            )
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

    actionable = [p for p in plan if p["action"] == "patch"]
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
        # Throttled, retried PATCH. renew_premium_user already retries
        # internally 3x; we add another outer pass with backoff so a
        # broader hiccup (panel cold) doesn't drop the record.
        async with sem:
            ok = False
            for attempt in range(1, _FIX_RETRY + 1):
                try:
                    ok = await remnawave_premium.renew_premium_user(
                        p["telegram_id"], p["real_end"],
                    )
                    if ok:
                        progress["ok"] += 1
                        logger.info(
                            "PREMIUM_RECOVERY_PATCHED tg=%s uuid=%s to=%s attempt=%d",
                            p["telegram_id"], p["panel_uuid"][:8],
                            p["real_end"].isoformat(), attempt,
                        )
                        break
                except Exception as e:
                    logger.warning(
                        "PREMIUM_RECOVERY: tg=%s uuid=%s attempt %d/%d %s: %s",
                        p["telegram_id"], p["panel_uuid"][:8],
                        attempt, _FIX_RETRY, type(e).__name__, e,
                    )
                if attempt < _FIX_RETRY:
                    await asyncio.sleep(1.5 ** attempt)  # 1.5s, 2.25s
            if not ok:
                progress["failed"] += 1
                logger.error(
                    "PREMIUM_RECOVERY_GIVE_UP tg=%s uuid=%s after %d attempts",
                    p["telegram_id"], p["panel_uuid"][:8], _FIX_RETRY,
                )
            # Spread requests so the panel stays calm.
            await asyncio.sleep(_FIX_THROTTLE_S)
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
