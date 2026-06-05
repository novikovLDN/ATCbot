"""
Admin: audit & repair of subscriptions.expires_at for the
fast_expiry_cleanup +10y leak.

PROBLEM
-------
database/subscriptions.py:283 (transition to bypass-only) sets
expires_at = NOW + 10 years on the row as a "bypass-only is
time-unlimited" marker. The row's premium_uuid is NOT cleared. If
the user later buys premium again through `provision_subscription`,
that code path doesn't UPDATE expires_at — so the DB row keeps the
+10y marker, the bot UI shows "expires in 10 years", and the
recovery tool that we wrote earlier only patched the panel, not the
DB.

THIS TOOL
---------
1. Scan only suspicious rows: status='active' AND expires_at > NOW + 2y.
2. Compute each user's real expected_end from all paid sources
   (subscription_history MAX + paid pending_purchases + payments
   JOIN purchases + activated gifts). MAX wins.
3. Compare with the row's current expires_at. Bucket the result.
4. Operator can:
   - 📋 Download the full list as a CSV (telegram_id, current_expires_at,
     expected_end, expected_source, action) to review by hand.
   - 🔧 Apply fix: first ship a CSV backup of the BEFORE state to the
     admin (so a manual rollback is possible), then bulk-UPDATE
     subscriptions.expires_at.

GUARDRAILS
----------
- Audit + fix are background tasks. Admin can close the chat and
  poll status later.
- Concurrency 3, throttle 400ms (matches the other tools).
- 10s per-HTTP-call timeout (there is no HTTP here — purely DB —
  but kept for consistency).
- Fix updates ONLY subscriptions.expires_at. is_bypass_only,
  remnawave_premium_uuid, status, etc. are left exactly as-is. The
  bypass entity on the panel is not touched at all.
- CSV backup is sent BEFORE the UPDATE. If anything is wrong, the
  admin can rebuild old values from the file.
"""
import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

import config
import database
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_audit_db_dates_router = Router()
logger = logging.getLogger(__name__)

# Anything beyond NOW + this is suspicious.
_SUSPICIOUS_THRESHOLD_DAYS = 365 * 2
# How close panel/DB needs to be to "expected" to count as OK.
_TOLERANCE_SECONDS = 24 * 3600
# Seconds between live progress edits.
_PROGRESS_INTERVAL = 5
# Hard ceiling on scan size.
_MAX_SCAN = 100_000

# In-memory state of the running audit per admin id.
_audits: dict[int, dict] = {}


def _compute_real_end(rows: list) -> Optional[datetime]:
    end: Optional[datetime] = None
    for row in rows:
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


async def _audit_worker(admin_id: int):
    state = _audits[admin_id]
    try:
        subs = await database.get_subscriptions_with_far_future_expires()
        subs = subs[:_MAX_SCAN]
        state["total"] = len(subs)
        state["done"] = 0

        tg_ids = [s["telegram_id"] for s in subs]
        history_ends = await database.get_max_subscription_end_bulk(tg_ids)
        paid = await database.get_paid_subscription_history_bulk(tg_ids)
        gifts = await database.get_activated_gifts_bulk(tg_ids)
        payments_hist = await database.get_paid_payments_via_purchases_bulk(tg_ids)

        now = datetime.now(timezone.utc)
        floor_end = now + timedelta(days=1)
        plan: list = []
        buckets: dict = {}

        for sub in subs:
            tg = sub["telegram_id"]
            db_expires_at = sub["expires_at"]
            if db_expires_at and db_expires_at.tzinfo is None:
                db_expires_at = db_expires_at.replace(tzinfo=timezone.utc)

            # Gather every signal.
            signals: list = []
            hist = history_ends.get(tg)
            if hist is not None:
                if hist.tzinfo is None:
                    hist = hist.replace(tzinfo=timezone.utc)
                signals.append(("history", hist))
            for source_name, src in (
                ("gift", [{"created_at": g["activated_at"],
                          "period_days": g["period_days"]}
                         for g in gifts.get(tg, [])]),
                ("paid", paid.get(tg, [])),
                ("payments", payments_hist.get(tg, [])),
            ):
                computed = _compute_real_end(src)
                if computed is not None:
                    signals.append((source_name, computed))

            if signals:
                expected_source, expected_end = max(signals, key=lambda s: s[1])
                # If real end is already in the past, the column should
                # also not be in the past — bot UI would show "expired"
                # and fast_expiry_cleanup would touch the row. Floor
                # to tomorrow.
                if expected_end < floor_end:
                    expected_end = floor_end
                    expected_source = f"{expected_source}+floor"
            else:
                expected_end = floor_end
                expected_source = "none"

            # Bucket.
            delta_days = (db_expires_at - expected_end).days if db_expires_at else None
            if delta_days is None:
                bucket = "db_no_expire"
            elif abs((db_expires_at - expected_end).total_seconds()) <= _TOLERANCE_SECONDS:
                bucket = "ok"
            elif db_expires_at > expected_end + timedelta(seconds=_TOLERANCE_SECONDS):
                bucket = "db_ahead_of_paid"
            else:
                bucket = "db_behind_paid"

            buckets[bucket] = buckets.get(bucket, 0) + 1
            plan.append({
                "telegram_id": tg,
                "current_expires_at": db_expires_at,
                "expected_end": expected_end,
                "expected_source": expected_source,
                "subscription_type": sub.get("subscription_type"),
                "is_bypass_only": sub.get("is_bypass_only"),
                "delta_days": delta_days,
                "bucket": bucket,
            })
            state["done"] += 1

        state["plan"] = plan
        state["buckets"] = buckets
        state["status"] = "done"
        logger.info(
            "AUDIT_DB_DATES_DONE admin=%s checked=%s buckets=%s",
            admin_id, state["done"], buckets,
        )
    except Exception as e:
        logger.exception("AUDIT_DB_DATES_FAILED admin=%s: %s", admin_id, e)
        state["status"] = "failed"
        state["error"] = f"{type(e).__name__}: {e}"


_BUCKET_LABELS = {
    "ok": "✅ БД совпадает с оплатами",
    "db_ahead_of_paid": "🚨 БД ВПЕРЕДИ оплаты (нужно исправить)",
    "db_behind_paid": "⚠️ БД ПОЗАДИ оплаты (юзер потерял время)",
    "db_no_expire": "⚠️ В БД active без expires_at",
}


def _format_report(state: dict, full_list: bool = False) -> str:
    total = state.get("total", 0)
    done = state.get("done", 0)
    buckets = state.get("buckets", {})

    lines = [
        "🗃 <b>Аудит БД expires_at (+10y маркер)</b>",
        "",
        f"Проверено подозрительных строк: <b>{done}</b> / {total}",
        f"Статус: {state.get('status', 'running')}",
        "",
    ]
    if state.get("status") == "failed":
        lines.append(f"❌ Ошибка: <code>{state.get('error') or '—'}</code>")
        return "\n".join(lines)

    if not state.get("plan") and state.get("status") == "done":
        lines.append("✅ Подозрительных строк не найдено — БД чистая.")
        return "\n".join(lines)

    for key in ("ok", "db_ahead_of_paid", "db_behind_paid", "db_no_expire"):
        n = buckets.get(key, 0)
        if n == 0:
            continue
        lines.append(f"  {_BUCKET_LABELS.get(key, key)}: <b>{n}</b>")

    actionable = [p for p in state.get("plan", []) if p["bucket"] == "db_ahead_of_paid"]

    if full_list:
        lines.append("")
        lines.append(f"<i>🚨 БД ВПЕРЕДИ оплаты — все {len(actionable)}:</i>")
        for p in actionable[:80]:
            tg = p["telegram_id"]
            cur = p["current_expires_at"].strftime("%Y-%m-%d") if p["current_expires_at"] else "—"
            exp = p["expected_end"].strftime("%Y-%m-%d") if p["expected_end"] else "—"
            src = p["expected_source"]
            tariff = p.get("subscription_type") or "?"
            lines.append(
                f"  <code>{tg}</code> · {tariff} · "
                f"БД:{cur} → {exp} (src:{src})"
            )
        if len(actionable) > 80:
            lines.append(f"  …и ещё {len(actionable) - 80} — выгрузи CSV.")
        return "\n".join(lines)

    # Brief samples.
    if actionable:
        lines.append("")
        lines.append("<i>Примеры (исправят expires_at):</i>")
        for p in actionable[:5]:
            tg = p["telegram_id"]
            cur = p["current_expires_at"].strftime("%Y-%m-%d") if p["current_expires_at"] else "—"
            exp = p["expected_end"].strftime("%Y-%m-%d") if p["expected_end"] else "—"
            lines.append(
                f"  <code>{tg}</code> · БД:{cur} → {exp}"
            )

    return "\n".join(lines)


def _build_csv(plan: list, *, before_fix: bool = False) -> bytes:
    """Serialise the plan as a CSV bytes payload."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    if before_fix:
        writer.writerow([
            "telegram_id", "subscription_type", "is_bypass_only",
            "old_expires_at_utc", "new_expires_at_utc", "expected_source",
        ])
    else:
        writer.writerow([
            "telegram_id", "subscription_type", "is_bypass_only",
            "current_expires_at_utc", "expected_end_utc",
            "expected_source", "delta_days", "bucket",
        ])
    for p in plan:
        cur = p["current_expires_at"].isoformat() if p["current_expires_at"] else ""
        exp = p["expected_end"].isoformat() if p["expected_end"] else ""
        row = [
            p["telegram_id"], p.get("subscription_type") or "",
            "1" if p.get("is_bypass_only") else "0",
        ]
        if before_fix:
            row += [cur, exp, p["expected_source"]]
        else:
            row += [cur, exp, p["expected_source"],
                    p.get("delta_days") if p.get("delta_days") is not None else "",
                    p["bucket"]]
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


@admin_audit_db_dates_router.callback_query(F.data == "admin:audit_db_dates")
async def callback_audit_db_dates(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    admin_id = callback.from_user.id
    existing = _audits.get(admin_id)

    if existing and existing.get("status") == "running":
        await safe_edit_text(
            callback.message, _format_report(existing),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin:audit_db_dates")],
                [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
            ]),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    if existing and existing.get("status") in ("done", "failed"):
        plan = existing.get("plan", [])
        actionable = [p for p in plan if p["bucket"] == "db_ahead_of_paid"]
        rows = []
        if plan:
            rows.append([InlineKeyboardButton(
                text="📋 Полный список",
                callback_data="admin:audit_db_dates_list",
            )])
            rows.append([InlineKeyboardButton(
                text="📤 Скачать CSV (все)",
                callback_data="admin:audit_db_dates_csv",
            )])
        if actionable:
            rows.append([InlineKeyboardButton(
                text=f"🔧 Исправить ({len(actionable)})",
                callback_data="admin:audit_db_dates_fix",
            )])
        rows.append([InlineKeyboardButton(text="🔁 Запустить заново", callback_data="admin:audit_db_dates_start")])
        rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])
        await safe_edit_text(
            callback.message, _format_report(existing),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    await _start_audit(callback, admin_id)


@admin_audit_db_dates_router.callback_query(F.data == "admin:audit_db_dates_start")
async def callback_audit_db_dates_start(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await _start_audit(callback, callback.from_user.id)


@admin_audit_db_dates_router.callback_query(F.data == "admin:audit_db_dates_list")
async def callback_audit_db_dates_list(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass
    state = _audits.get(callback.from_user.id)
    if not state or state.get("status") != "done":
        await safe_edit_text(
            callback.message, "Сначала запустите аудит.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return
    await safe_edit_text(
        callback.message, _format_report(state, full_list=True),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 CSV", callback_data="admin:audit_db_dates_csv")],
            [InlineKeyboardButton(text="◀ К отчёту", callback_data="admin:audit_db_dates")],
        ]),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_audit_db_dates_router.callback_query(F.data == "admin:audit_db_dates_csv")
async def callback_audit_db_dates_csv(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass
    state = _audits.get(callback.from_user.id)
    if not state or state.get("status") != "done":
        await callback.answer("Сначала запустите аудит.", show_alert=True)
        return
    plan = state.get("plan", [])
    if not plan:
        await callback.answer("Список пуст.", show_alert=True)
        return
    csv_bytes = _build_csv(plan, before_fix=False)
    file = BufferedInputFile(
        csv_bytes,
        filename=f"audit_db_dates_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv",
    )
    await callback.bot.send_document(
        callback.from_user.id, file,
        caption=f"📤 Аудит БД expires_at — {len(plan)} строк.",
    )


@admin_audit_db_dates_router.callback_query(F.data == "admin:audit_db_dates_fix")
async def callback_audit_db_dates_fix(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    state = _audits.get(callback.from_user.id)
    if not state or state.get("status") != "done":
        await safe_edit_text(
            callback.message, "Сначала запустите аудит.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    actionable = [p for p in state.get("plan", []) if p["bucket"] == "db_ahead_of_paid"]
    if not actionable:
        await safe_edit_text(
            callback.message, "✅ Исправлять нечего.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    if state.get("fix_status") == "running":
        await callback.answer("Уже исправляется.", show_alert=True)
        return

    # 1. CSV backup BEFORE the UPDATE.
    backup_bytes = _build_csv(actionable, before_fix=True)
    backup_file = BufferedInputFile(
        backup_bytes,
        filename=f"audit_db_dates_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv",
    )
    try:
        await callback.bot.send_document(
            callback.from_user.id, backup_file,
            caption=(
                f"🛡 Бэкап ПЕРЕД UPDATE: {len(actionable)} строк "
                "(telegram_id, old/new expires_at).\n"
                "Если что-то пойдёт не так — даты восстанавливаются "
                "из колонки old_expires_at_utc."
            ),
        )
    except Exception as e:
        await safe_edit_text(
            callback.message,
            f"❌ Не удалось отправить CSV-бэкап: <code>{e}</code>\n\n"
            "UPDATE НЕ выполнен. Попробуйте ещё раз.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    state["fix_status"] = "running"
    state["fix_total"] = len(actionable)
    state["fix_ok"] = 0
    state["fix_failed"] = 0
    asyncio.create_task(_fix_worker(callback.from_user.id, actionable))

    await safe_edit_text(
        callback.message,
        f"🔧 <b>Исправление БД expires_at запущено</b>\n\n"
        f"Записей: <b>{len(actionable)}</b>\n"
        "CSV-бэкап отправлен выше — сохрани на всякий случай.\n\n"
        "Обновятся только subscriptions.expires_at. Bypass, panel, "
        "статусы — не трогаются.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:audit_db_dates_fix_status")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_audit_db_dates_router.callback_query(F.data == "admin:audit_db_dates_fix_status")
async def callback_audit_db_dates_fix_status(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    state = _audits.get(callback.from_user.id)
    if not state or not state.get("fix_status"):
        await safe_edit_text(
            callback.message, "Нет данных по исправлению.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    status = state.get("fix_status")
    total = state.get("fix_total", 0)
    ok = state.get("fix_ok", 0)
    failed = state.get("fix_failed", 0)

    if status == "running":
        text = (
            "🔧 <b>Исправление БД в процессе</b>\n\n"
            f"  ✅ Обновлено: {ok} / {total}\n"
            f"  ❌ Сбой: {failed}"
        )
        rows = [
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:audit_db_dates_fix_status")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]
    else:
        text = (
            "🔧 <b>Исправление БД завершено</b>\n\n"
            f"✅ Обновлено: <b>{ok}</b> / {total}\n"
            f"❌ Сбой: <b>{failed}</b>\n\n"
            "<i>UPDATE затронул только subscriptions.expires_at.</i>\n"
            "Запустите аудит повторно, чтобы убедиться."
        )
        rows = [
            [InlineKeyboardButton(text="🔁 Повторный аудит", callback_data="admin:audit_db_dates_start")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )


async def _fix_worker(admin_id: int, actionable: list):
    state = _audits[admin_id]
    try:
        updates = [
            {
                "telegram_id": p["telegram_id"],
                "new_expires_at": p["expected_end"],
            }
            for p in actionable
        ]
        # One bulk transaction — DB updates are cheap, no need to throttle.
        n = await database.update_subscription_expires_at_bulk(updates)
        state["fix_ok"] = n
        state["fix_failed"] = max(0, len(actionable) - n)
        state["fix_status"] = "done"
        logger.info(
            "AUDIT_DB_DATES_FIX_DONE admin=%s updated=%s",
            admin_id, n,
        )
    except Exception as e:
        state["fix_status"] = "failed"
        state["fix_failed"] = len(actionable) - state.get("fix_ok", 0)
        logger.exception("AUDIT_DB_DATES_FIX_FAILED admin=%s: %s", admin_id, e)


async def _start_audit(callback: CallbackQuery, admin_id: int):
    state = {
        "status": "running",
        "total": 0,
        "done": 0,
        "plan": [],
        "buckets": {},
        "error": None,
        "fix_status": None,
        "fix_total": 0,
        "fix_ok": 0,
        "fix_failed": 0,
    }
    _audits[admin_id] = state
    asyncio.create_task(_audit_worker(admin_id))

    await safe_edit_text(
        callback.message,
        "🗃 <b>Аудит БД expires_at запущен</b>\n\n"
        "Сканирую active subscriptions с expires_at дальше чем NOW + 2 года.\n"
        "Это сверка с оплатами, триалом и подарками. "
        "Изменений в БД не вносится — только отчёт.\n\n"
        "Нажми «🔄 Обновить статус» через минуту.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin:audit_db_dates")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]),
        bot=callback.bot, parse_mode="HTML",
    )
