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
# Concurrent panel calls during apply.
# Lowered to 4 (was 8) — Remnawave starts dropping/queueing after a few
# hundred requests on a loaded panel, which manifested as the apply
# stalling at ~300/1347. Lower concurrency + small throttle keeps the
# panel responsive end-to-end.
_FIX_CONCURRENCY = 4
# Sleep after each record. Gives the panel breathing room AND yields
# the event loop to the bot's other workers / aiogram callbacks.
_FIX_THROTTLE_S = 0.3
# Per-HTTP-call timeout. Wrapped around each individual GET/PATCH so a
# stuck call can't hold its semaphore slot for minutes.
# IMPORTANT: this wait_for is INSIDE the worker, AFTER it has the
# semaphore — so no race like the previous version where all 1k tasks
# were timing out at once while queued behind a Semaphore(1).
_FIX_HTTP_TIMEOUT_S = 10
# Seconds between live progress updates.
_PROGRESS_INTERVAL = 4
# Hard ceiling on scan size (sanity guard).
_MAX_SCAN = 100_000

# Per-admin scan result, feeds the "Apply" button. Lost on bot restart —
# that's fine, just rescan.
_last_plan: dict[int, list] = {}

# ── Background-job plumbing ───────────────────────────────────────────
# The scan (paced panel stream) and the apply both run for MINUTES — far
# beyond the 25s webhook-handler budget (WEBHOOK_HANDLER_TIMEOUT). So the
# callbacks NEVER await the work: they spawn a detached task (survives the
# handler being cancelled) and return immediately; the task edits the
# message itself as it progresses and on completion.
_bg_tasks: set = set()
# Per-admin job guard: "scan" | "apply" | None — prevents двойной запуск.
_job_state: dict[int, str] = {}


def _spawn_bg(coro) -> None:
    t = asyncio.ensure_future(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def _edit(bot, chat_id: int, msg_id: int, text: str, reply_markup=None) -> None:
    """Edit the recovery message from a background task (no live Message obj)."""
    try:
        await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=msg_id,
            reply_markup=reply_markup, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        pass


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


async def _scan_panel_first(progress: "dict | None" = None):
    """PANEL-FIRST scan — the correct source of truth.

    Stream the WHOLE panel (paced, rate-limit-safe) and take EVERY
    tg_{id}_premium whose expireAt > NOW+5y — the real 10-year phantoms,
    regardless of what our DB says. Then compute each user's correct end from
    our purchase signals (subscription_history / paid pending_purchases /
    activated gifts / payments-join), MAX across them, floored to tomorrow if
    already in the past.

    Returns (panel_seen, plan) or (0, None) if the panel is unreachable.
    Each plan item carries the panel entity's REAL uuid/id for the patch.
    """
    import re
    from app.services import remnawave_api

    cutoff = datetime.now(timezone.utc) + timedelta(days=365 * 5)
    rx = re.compile(r"^tg_(\d+)_premium$")

    def _cb(collected, total):
        if progress is not None:
            progress["panel_seen"] = collected
            if total:
                progress["panel_total"] = total

    all_users = await remnawave_api.get_all_users(
        page_delay=0.7, max_retries=6, progress_cb=_cb,
    )
    if not all_users:
        return 0, None

    phantoms: list = []  # (tg, panel_uuid, panel_id)
    for u in all_users:
        m = rx.match((u.get("username") or "").strip())
        if not m:
            continue
        dt = _parse_rmn_dt(u.get("expireAt"))
        if dt is None or dt <= cutoff:
            continue
        phantoms.append((
            int(m.group(1)),
            u.get("uuid") or u.get("vlessUuid"),
            u.get("id"),
        ))

    if progress is not None:
        progress["phase"] = "compute"
        progress["phantoms"] = len(phantoms)

    tg_ids = [t for (t, _, _) in phantoms]
    history_ends = await database.get_max_subscription_end_bulk(tg_ids)
    paid = await database.get_paid_subscription_history_bulk(tg_ids)
    gifts = await database.get_activated_gifts_bulk(tg_ids)
    payments_hist = await database.get_paid_payments_via_purchases_bulk(tg_ids)

    now = datetime.now(timezone.utc)
    floor_end = now + timedelta(days=1)
    plan: list = []
    for tg, p_uuid, p_id in phantoms:
        signals: list = []
        he = history_ends.get(tg)
        if he is not None:
            if he.tzinfo is None:
                he = he.replace(tzinfo=timezone.utc)
            signals.append(("history", he))
        ge = _compute_real_end([
            {"created_at": g["activated_at"], "period_days": g["period_days"]}
            for g in gifts.get(tg, [])
        ])
        if ge is not None:
            signals.append(("gift", ge))
        pe = _compute_real_end(paid.get(tg, []))
        if pe is not None:
            signals.append(("paid", pe))
        pye = _compute_real_end(payments_hist.get(tg, []))
        if pye is not None:
            signals.append(("payments", pye))

        if signals:
            best_source, real_end = max(signals, key=lambda s: s[1])
            if real_end < floor_end:
                real_end = floor_end
                source = "%s+floor" % best_source
            else:
                source = best_source
        else:
            real_end = floor_end
            source = "none"

        plan.append({
            "telegram_id": tg, "panel_uuid": p_uuid, "panel_id": p_id,
            "real_end": real_end, "source": source,
            "action": "patch", "verified": True,
        })

    return len(all_users), plan


def _format_dry_run(panel_seen: int, plan: list) -> str:
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
        "🩹 <b>Откат premium-подписок (Dry-run, panel-first)</b>",
        "",
        f"📡 Стрим панели вернул: <b>{panel_seen}</b> энтити",
        f"🎯 РЕАЛЬНЫХ фантомов (tg_*_premium, expireAt &gt; 5 лет): <b>{n_total}</b>",
        "<i>Взято прямо из панели по username; сверено с покупками.</i>",
    ]
    lines += [
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
        lines.append("\n✅ Реальных фантомов нет — откатывать нечего.")
    else:
        lines.append(
            f"\nПри подтверждении: <b>{n_total}</b> реальных фантомов "
            "(все — tg_*_premium, проверены поштучно) откатятся к корректной "
            "дате. Bypass entities <b>не трогаются</b>. Idempotent."
        )
        eta_min = max(1, int(n_total * 0.5 / _FIX_CONCURRENCY / 60))
        lines.append(
            f"\n⏱ Apply ~{eta_min} мин ({_FIX_CONCURRENCY} parallel)."
        )

    return "\n".join(lines)


async def _run_scan_bg(bot, chat_id: int, msg_id: int, admin_id: int) -> None:
    """Background: PANEL-FIRST scan (stream panel → tg_*_premium >5y → compute
    real end from purchases) → edit the message with the report. Detached so
    the 25s webhook budget can't kill it."""
    progress: dict = {"phase": "stream", "panel_seen": 0}
    stop = asyncio.Event()

    async def _tick():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=_PROGRESS_INTERVAL)
                break
            except asyncio.TimeoutError:
                pass
            seen = progress.get("panel_seen", 0)
            pt = progress.get("panel_total")
            tail = f" / ~{pt}" if pt else ""
            if progress.get("phase") == "compute":
                txt = (
                    "🩹 Стрим панели завершён — считаю корректные сроки по покупкам…\n\n"
                    f"Фантомов найдено: <b>{progress.get('phantoms', 0)}</b>\n"
                    f"Просмотрено энтити: <b>{seen}</b>{tail}"
                )
            else:
                txt = (
                    "🩹 Тяну ВСЕХ tg_*_premium из панели (медленный стрим, без упора "
                    "в лимит)…\n\n"
                    f"Просмотрено энтити: <b>{seen}</b>{tail}\n"
                    "<i>Паузы между страницами — это норма, не зависание.</i>"
                )
            await _edit(bot, chat_id, msg_id, txt)

    tick = asyncio.ensure_future(_tick())
    try:
        panel_seen, plan = await _scan_panel_first(progress)
        if plan is None:
            stop.set()
            await _edit(
                bot, chat_id, msg_id,
                "❌ Панель недоступна (стрим не удался) — попробуйте позже.",
                get_admin_back_keyboard(),
            )
            return
        _last_plan[admin_id] = plan

        actionable = [p for p in plan if p["action"] == "patch"]
        rows = []
        if actionable:
            rows.append([InlineKeyboardButton(
                text=f"🩹 Применить ({len(actionable)} фантомов)",
                callback_data="admin:premium_recovery_apply",
            )])
        rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])
        stop.set()
        await _edit(
            bot, chat_id, msg_id,
            _format_dry_run(panel_seen, plan),
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as e:
        logger.exception("PREMIUM_RECOVERY: scan bg failed: %s", e)
        stop.set()
        await _edit(bot, chat_id, msg_id, f"❌ Ошибка сверки: {e}", get_admin_back_keyboard())
    finally:
        stop.set()
        try:
            await tick
        except Exception:
            pass
        _job_state.pop(admin_id, None)


@admin_premium_recovery_router.callback_query(F.data == "admin:premium_recovery")
async def callback_premium_recovery(callback: CallbackQuery):
    """Dry-run: kick off the scan in the BACKGROUND and return immediately —
    the work takes minutes, far past the 25s webhook budget."""
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

    admin_id = callback.from_user.id
    if _job_state.get(admin_id):
        await safe_edit_text(
            callback.message,
            f"🩹 Уже идёт задача (<b>{_job_state[admin_id]}</b>) в фоне — "
            "дождитесь её завершения (сообщение обновится само).",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    _job_state[admin_id] = "scan"
    await safe_edit_text(
        callback.message,
        "🩹 Запустил сверку в <b>фоне</b>.\n\n"
        "БД → медленный стрим панели (без упора в лимит), несколько минут. "
        "Это сообщение обновлю автоматически по ходу и по завершении — "
        "можно свернуть чат.",
        bot=callback.bot, parse_mode="HTML",
    )
    _spawn_bg(_run_scan_bg(
        callback.bot, callback.message.chat.id, callback.message.message_id, admin_id,
    ))


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

    if _job_state.get(callback.from_user.id):
        await safe_edit_text(
            callback.message,
            f"🩹 Уже идёт задача (<b>{_job_state[callback.from_user.id]}</b>) — "
            "дождитесь завершения.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    _job_state[callback.from_user.id] = "apply"
    await safe_edit_text(
        callback.message,
        f"🩹 Запустил применение отката для {total} записей в <b>фоне</b>.\n\n"
        "Bypass entities не трогаются. Сообщение обновлю по ходу и по завершении — "
        "можно свернуть чат.",
        bot=callback.bot, parse_mode="HTML",
    )
    _spawn_bg(_run_apply_bg(
        callback.bot, callback.message.chat.id, callback.message.message_id,
        callback.from_user.id, actionable,
    ))


async def _run_apply_bg(bot, chat_id: int, msg_id: int, admin_id: int, actionable: list) -> None:
    """Background: PATCH every entity in the plan (detached from the 25s
    webhook budget). Only tg_*_premium with far-future expireAt are touched."""
    total = len(actionable)
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
            tg = p["telegram_id"]
            expected_username = f"tg_{tg}_premium"
            # GET/PATCH by the entity's NUMERIC id (from the stream). get_user
            # by UUID doesn't work in 3.x — it resolves via our (stale) DB →
            # None → everything looked "gone". Numeric id hits the panel直接.
            ref = p.get("panel_id")
            if ref is None:
                ref = p.get("panel_uuid")
            ref_disp = str(ref)

            try:
                # SAFETY CHECK 1: GET live entity by numeric id.
                try:
                    user = await asyncio.wait_for(
                        remnawave_api.get_user(ref), timeout=_FIX_HTTP_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    progress["failed"] += 1
                    logger.warning("PREMIUM_RECOVERY_TIMEOUT_GET tg=%s ref=%s", tg, ref_disp)
                    return False
                except Exception as e:
                    progress["failed"] += 1
                    logger.warning("PREMIUM_RECOVERY_GET_ERR tg=%s ref=%s %s: %s",
                                   tg, ref_disp, type(e).__name__, e)
                    return False

                if user is None:
                    progress["gone"] += 1
                    logger.info("PREMIUM_RECOVERY_GONE tg=%s ref=%s (panel returned none)", tg, ref_disp)
                    return True

                # SAFETY CHECK 2: STRICTLY tg_{id}_premium — re-verify live and
                # log every entity we touch or skip.
                actual_username = (user.get("username") or "").strip()
                if actual_username != expected_username:
                    progress["skipped"] += 1
                    logger.warning(
                        "PREMIUM_RECOVERY_SKIP_WRONG_USERNAME tg=%s ref=%s "
                        "expected=%s got=%r — NOT premium, не трогаем",
                        tg, ref_disp, expected_username, actual_username,
                    )
                    return False

                # SAFETY CHECK 3: current panel expireAt must still be > NOW+5y.
                old_expire = user.get("expireAt") or ""
                try:
                    s = old_expire[:-1] + "+00:00" if old_expire.endswith("Z") else old_expire
                    existing_dt = datetime.fromisoformat(s) if s else None
                    if existing_dt is not None and existing_dt.tzinfo is None:
                        existing_dt = existing_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    existing_dt = None
                if existing_dt is None or existing_dt < datetime.now(timezone.utc) + timedelta(days=365 * 5):
                    progress["skipped"] += 1
                    logger.info(
                        "PREMIUM_RECOVERY_SKIP_NOT_AFFECTED tg=%s username=%s "
                        "expireAt=%s — already within sane range",
                        tg, actual_username, old_expire,
                    )
                    return False

                # All 3 checks passed → PATCH by the entity's own numeric id.
                fields = {"expireAt": _iso_z(p["real_end"]), "status": "ACTIVE"}
                if external_squad:
                    fields["externalSquadUuid"] = external_squad
                patch_ref = user.get("id") if user.get("id") is not None else ref
                try:
                    result = await asyncio.wait_for(
                        remnawave_api.update_user(patch_ref, **fields),
                        timeout=_FIX_HTTP_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    progress["failed"] += 1
                    logger.warning("PREMIUM_RECOVERY_TIMEOUT_PATCH tg=%s ref=%s", tg, ref_disp)
                    return False
                except Exception as e:
                    progress["failed"] += 1
                    logger.warning("PREMIUM_RECOVERY_PATCH_ERR tg=%s ref=%s %s: %s",
                                   tg, ref_disp, type(e).__name__, e)
                    return False

                if result is not None:
                    progress["ok"] += 1
                    logger.info(
                        "PREMIUM_RECOVERY_PATCHED tg=%s username=%s id=%s from=%s to=%s source=%s",
                        tg, actual_username, patch_ref, old_expire,
                        p["real_end"].isoformat(), p["source"],
                    )
                    return True
                progress["failed"] += 1
                logger.warning("PREMIUM_RECOVERY_FAIL tg=%s username=%s (PATCH rejected)", tg, actual_username)
                return False
            finally:
                progress["done"] += 1
                try:
                    await asyncio.sleep(_FIX_THROTTLE_S)
                except Exception:
                    pass

    async def _run_all_fixes():
        return await asyncio.gather(*[_fix_one(p) for p in actionable])

    try:
        fix_task = asyncio.ensure_future(_run_all_fixes())
        while not fix_task.done():
            await asyncio.sleep(_PROGRESS_INTERVAL)
            if fix_task.done():
                break
            await _edit(
                bot, chat_id, msg_id,
                "🩹 Применяю откат…\n\n"
                f"Обработано: <b>{progress['done']}</b> / {total}\n"
                f"  ✅ Откатано: {progress['ok']}\n"
                f"  👻 Уже отсутствует на панели: {progress['gone']}\n"
                f"  🛡 Не тронуто (защита): {progress['skipped']}\n"
                f"  ❌ Сбой: {progress['failed']}",
            )
        await fix_task
        _last_plan.pop(admin_id, None)

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
        await _edit(bot, chat_id, msg_id, text, get_admin_back_keyboard())
    except Exception as e:
        logger.exception("PREMIUM_RECOVERY: apply bg failed: %s", e)
        await _edit(bot, chat_id, msg_id, f"❌ Ошибка применения: {e}", get_admin_back_keyboard())
    finally:
        _job_state.pop(admin_id, None)
