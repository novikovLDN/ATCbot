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
# Matches reconcile's pattern — high enough to finish in minutes,
# low enough to keep Remnawave responsive.
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
    """Build the recovery plan from DB only — no panel fetch.

    Source of truth is `subscription_history.end_date` (MAX per user)
    — it's the ledger that ALL subscription paths write into:
    purchases, renewals, gift activations, admin grants. Falling back
    to paid_purchases + gifts only catches a subset; subscription_history
    catches them all.

    Each item: {telegram_id, panel_uuid, real_end, source, action}.
    `source`:
      - 'history'  — used MAX(end_date) from subscription_history
      - 'gift'     — fallback to activated gift (history empty)
      - 'paid'     — fallback to pending_purchases paid replay
      - 'none'     — no signal anywhere → real_end = NOW (entity expires)
    """
    candidates = await database.get_premium_recovery_candidates()
    candidates = candidates[:_MAX_SCAN]

    if progress is not None:
        progress["phase"] = "compute"
        progress["total"] = len(candidates)
        progress["done"] = 0

    tg_ids = [c["telegram_id"] for c in candidates]
    history_ends = await database.get_max_subscription_end_bulk(tg_ids)
    paid = await database.get_paid_subscription_history_bulk(tg_ids)
    gifts = await database.get_activated_gifts_bulk(tg_ids)
    payments_hist = await database.get_paid_payments_via_purchases_bulk(tg_ids)

    plan: list = []
    now = datetime.now(timezone.utc)
    # Remnawave panel won't accept a past expireAt — set tomorrow as the
    # floor so an entity that "should be expired" still gets a valid
    # date the panel will swallow. One extra day grace; the next
    # fast_expiry_cleanup tick will expire the row anyway.
    floor_end = now + timedelta(days=1)

    for cand in candidates:
        if progress is not None:
            progress["done"] += 1
        tg = cand["telegram_id"]
        panel_uuid = cand["remnawave_premium_uuid"]

        # Gather every signal we can find — we'll take the MAX so we
        # never accidentally shorten a user who paid through any path.
        signals: list = []

        hist_end = history_ends.get(tg)
        if hist_end is not None:
            if hist_end.tzinfo is None:
                hist_end = hist_end.replace(tzinfo=timezone.utc)
            signals.append(("history", hist_end))

        gift_end = _compute_real_end([
            {"created_at": g["activated_at"], "period_days": g["period_days"]}
            for g in gifts.get(tg, [])
        ])
        if gift_end is not None:
            signals.append(("gift", gift_end))

        paid_end = _compute_real_end(paid.get(tg, []))
        if paid_end is not None:
            signals.append(("paid", paid_end))

        payments_end = _compute_real_end(payments_hist.get(tg, []))
        if payments_end is not None:
            signals.append(("payments", payments_end))

        if signals:
            # Take the MAX across all sources so we never accidentally
            # cut a user short. Whichever signal gave the latest date wins.
            best_source, real_end = max(signals, key=lambda s: s[1])
            # If the user's real end is already in the past, the panel
            # won't accept it either — floor at tomorrow.
            if real_end < floor_end:
                real_end = floor_end
                source = "%s+floor" % best_source
            else:
                source = best_source
        else:
            # No paid/gift/history/payments signal anywhere — user has
            # no legitimate claim. Panel won't accept a past date, so
            # set expireAt to tomorrow (one-day grace).
            real_end = floor_end
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

    # Group raw counts by the primary source (strip +floor suffix).
    def _base(src: str) -> str:
        return src.replace("+floor", "")

    n_hist = sum(1 for p in plan if _base(p["source"]) == "history")
    n_paid = sum(1 for p in plan if _base(p["source"]) == "paid")
    n_gift = sum(1 for p in plan if _base(p["source"]) == "gift")
    n_pay = sum(1 for p in plan if _base(p["source"]) == "payments")
    n_none = sum(1 for p in plan if p["source"] == "none")
    n_floored = sum(1 for p in plan if p["source"].endswith("+floor"))
    n_total = n_hist + n_paid + n_gift + n_pay + n_none

    lines = [
        "🩹 <b>Откат premium-подписок (Dry-run)</b>",
        "",
        f"Кандидатов в БД: <b>{checked}</b>",
        "",
        "<i>По источнику истины (берём MAX по всем):</i>",
        f"  📜 <b>{n_hist}</b> — subscription_history (главный ledger)",
        f"  💳 <b>{n_paid}</b> — pending_purchases (paid)",
        f"  🧾 <b>{n_pay}</b> — payments table (через join purchase_id)",
        f"  🎁 <b>{n_gift}</b> — gift_subscriptions (activated)",
        f"  ⛔ <b>{n_none}</b> — нет ни одного сигнала",
        "",
        f"<i>Из них <b>{n_floored}</b> подтянуты к завтрашней дате</i>",
        "<i>(их реальный срок уже в прошлом; Remnawave не принимает</i>",
        "<i>прошлое в expireAt, ставим +1 день как минимум).</i>",
    ]

    # Group samples by primary source label.
    samples_by: dict = {"history": [], "paid": [], "payments": [],
                        "gift": [], "none": []}
    for p in plan:
        base = _base(p["source"])
        if base in samples_by and len(samples_by[base]) < 3:
            samples_by[base].append(p)
    samples: list = []
    for src, emoji in (("history", "📜"), ("paid", "💳"),
                       ("payments", "🧾"), ("gift", "🎁"),
                       ("none", "⛔")):
        for s in samples_by[src]:
            samples.append((emoji, s))
    if samples:
        lines.append("")
        lines.append("<i>Примеры:</i>")
        for emoji, s in samples:
            stamp = s["real_end"].strftime("%Y-%m-%d")
            extra = ""
            if s["source"].endswith("+floor"):
                extra = " (floored)"
            elif s["source"] == "none":
                extra = " (tomorrow)"
            lines.append(f"  {emoji} <code>{s['telegram_id']}</code> → {stamp}{extra}")

    if n_total == 0:
        lines.append("\n✅ Изменений не требуется.")
    else:
        lines.append(
            f"\nПри подтверждении: <b>{n_total}</b> панель-записей будут "
            "откатаны (idempotent). Bypass entities <b>не трогаются</b>."
        )
        eta_min = max(1, int(n_total * 0.5 / _FIX_CONCURRENCY / 60))
        lines.append(
            f"\n⏱ Apply ~{eta_min} мин ({_FIX_CONCURRENCY} parallel)."
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

    actionable = [p for p in plan if p["action"] == "patch"]
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
    progress: dict = {"done": 0, "ok": 0, "gone": 0,
                      "skipped": 0, "failed": 0}

    # Direct update_user. We deliberately bypass renew_premium_user
    # here because its internal "try 3 times across 5 endpoints"
    # behaviour is great for one-off renews but kills throughput on
    # 1k+ records: ~15 seconds per missing entity. update_user does
    # one pass over the 5 endpoints (with caching after first hit)
    # — None means "no endpoint accepted it" which almost always
    # means the entity is gone from the panel.
    external_squad = getattr(
        config, "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", None,
    ) or None

    def _iso_z(dt) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _fix_one(p: dict) -> bool:
        async with sem:
            uuid = p["panel_uuid"]
            tg = p["telegram_id"]
            expected_username = f"tg_{tg}_premium"

            # SAFETY CHECK 1: GET the entity to verify it's the premium
            # one for THIS user. Two reasons:
            #   (a) If BD has a stale remnawave_premium_uuid that now
            #       points at a different entity (bypass / someone
            #       else's premium / migrated), we MUST NOT patch it.
            #   (b) If panel returned 404, the entity is already gone
            #       — count as already-achieved and skip the PATCH.
            try:
                user = await remnawave_api.get_user(uuid)
            except Exception as e:
                progress["failed"] += 1
                progress["done"] += 1
                logger.warning(
                    "PREMIUM_RECOVERY: GET tg=%s uuid=%s %s: %s",
                    tg, uuid[:8], type(e).__name__, e,
                )
                return False

            if user is None:
                # 404 / not found on the panel.
                progress["gone"] += 1
                progress["done"] += 1
                logger.info(
                    "PREMIUM_RECOVERY_GONE tg=%s uuid=%s (not found on panel)",
                    tg, uuid[:8],
                )
                return True

            actual_username = (user.get("username") or "").strip()
            if actual_username != expected_username:
                # NOT our premium entity for this user. Refuse to touch.
                progress["skipped"] += 1
                progress["done"] += 1
                logger.warning(
                    "PREMIUM_RECOVERY_SKIP_WRONG_USERNAME tg=%s uuid=%s "
                    "expected=%s got=%r — entity not patched",
                    tg, uuid[:8], expected_username, actual_username,
                )
                return False

            # SAFETY CHECK 2 (paranoid double-check): if the entity has
            # an `expireAt` that ISN'T in the +10y far-future bucket
            # already, then somehow it's already fine — skip the PATCH
            # to avoid shortening a legitimately-active subscription.
            try:
                existing_expire_str = user.get("expireAt") or ""
                if existing_expire_str:
                    s = existing_expire_str
                    if s.endswith("Z"):
                        s = s[:-1] + "+00:00"
                    existing_dt = datetime.fromisoformat(s)
                    if existing_dt.tzinfo is None:
                        existing_dt = existing_dt.replace(tzinfo=timezone.utc)
                    # If existing expireAt is < NOW+5y, this entity was
                    # NOT one we accidentally extended. Leave alone.
                    if existing_dt < datetime.now(timezone.utc) + timedelta(days=365 * 5):
                        progress["skipped"] += 1
                        progress["done"] += 1
                        logger.info(
                            "PREMIUM_RECOVERY_SKIP_NOT_AFFECTED tg=%s uuid=%s "
                            "expireAt=%s — already within sane range",
                            tg, uuid[:8], existing_expire_str,
                        )
                        return False
            except Exception:
                pass

            # All clear — patch.
            fields = {"expireAt": _iso_z(p["real_end"]), "status": "ACTIVE"}
            if external_squad:
                fields["externalSquadUuid"] = external_squad
            result = None
            try:
                result = await remnawave_api.update_user(uuid, **fields)
            except Exception as e:
                logger.warning(
                    "PREMIUM_RECOVERY: PATCH tg=%s uuid=%s %s: %s",
                    tg, uuid[:8], type(e).__name__, e,
                )
            if result is not None:
                progress["ok"] += 1
                logger.info(
                    "PREMIUM_RECOVERY_PATCHED tg=%s uuid=%s username=%s to=%s source=%s",
                    tg, uuid[:8], actual_username,
                    p["real_end"].isoformat(), p["source"],
                )
            else:
                progress["failed"] += 1
                logger.warning(
                    "PREMIUM_RECOVERY_FAIL tg=%s uuid=%s username=%s (PATCH rejected)",
                    tg, uuid[:8], actual_username,
                )
        progress["done"] += 1
        return True

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
                f"  👻 Уже отсутствует на панели: {progress['gone']}\n"
                f"  🛡 Не тронуто (защита): {progress['skipped']}\n"
                f"  ❌ Сбой: {progress['failed']}",
                bot=callback.bot, parse_mode="HTML",
            )
        except Exception:
            pass

    await fix_task
    _last_plan.pop(callback.from_user.id, None)

    text = (
        "🩹 <b>Откат premium-подписок завершён</b>\n\n"
        f"✅ Откатано на панели: <b>{progress['ok']}</b> / {total}\n"
        f"👻 Уже отсутствует на панели: <b>{progress['gone']}</b>\n"
        f"🛡 Не тронуто (защита username/дата): <b>{progress['skipped']}</b>\n"
        f"❌ Сбой (ручной разбор): <b>{progress['failed']}</b>\n\n"
        "<i>Bypass entities остались нетронутыми.</i>\n"
        "<i>Защита по username: трогаем только entity вида tg_&lt;id&gt;_premium.</i>\n"
        "Запустите повторно, чтобы убедиться (idempotent)."
    )
    await safe_edit_text(
        callback.message, text,
        reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
    )
