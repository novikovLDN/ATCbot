"""
Admin Farm Storm console.

Shows the next scheduled storm + last 5 events with counters, plus tools
to schedule the storm N hours out (with an immediate push to every user
who has growing plots) or to force the current pending storm to run on
the next worker tick.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from database.core import get_pool
from app.handlers.common.utils import safe_edit_text

admin_farm_storm_router = Router()
logger = logging.getLogger(__name__)

# Quick presets for "schedule in N hours" (must match what we accept in the callback)
_HOUR_PRESETS = [1, 3, 6, 12, 24, 48]


def _kb(extra_rows=None):
    rows = list(extra_rows or [])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:storm")])
    rows.append([InlineKeyboardButton(text="🔙 В админку", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render(callback: CallbackQuery):
    pool = await get_pool()
    async with pool.acquire() as conn:
        pending = await conn.fetchrow(
            "SELECT id, scheduled_at, announced_at FROM farm_storms "
            "WHERE executed_at IS NULL ORDER BY scheduled_at ASC LIMIT 1"
        )
        recent = await conn.fetch(
            "SELECT id, scheduled_at, executed_at, killed_count, shielded_count, "
            "auto_harvested_count, auto_harvested_rub "
            "FROM farm_storms WHERE executed_at IS NOT NULL "
            "ORDER BY executed_at DESC LIMIT 5"
        )

    lines = ["🌪 <b>Управление штормами</b>\n"]

    if pending is None:
        lines.append("Следующий шторм: <b>не запланирован</b>")
    else:
        sched = pending["scheduled_at"]
        if sched.tzinfo is None:
            sched = sched.replace(tzinfo=timezone.utc)
        delta = sched - datetime.now(timezone.utc)
        h = int(delta.total_seconds() // 3600)
        if h >= 24:
            eta = f"{h // 24} д {h % 24} ч"
        else:
            eta = f"{max(0, h)} ч"
        announced = "да" if pending["announced_at"] else "нет"
        lines.append(
            f"Следующий шторм: #{pending['id']}\n"
            f"  📅 {sched.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"  ⏳ через ≈ {eta}\n"
            f"  📣 объявлен юзерам: {announced}"
        )

    lines.append("\n<b>Последние 5 штормов:</b>")
    if not recent:
        lines.append("  (ещё не было)")
    else:
        for r in recent:
            ex = r["executed_at"]
            if ex and ex.tzinfo is None:
                ex = ex.replace(tzinfo=timezone.utc)
            lines.append(
                f"  #{r['id']}: {ex.strftime('%m-%d %H:%M') if ex else '—'}  "
                f"💀{r['killed_count']}  🛡{r['shielded_count']}  "
                f"🚜{r['auto_harvested_count']} (+{r['auto_harvested_rub']} ₽)"
            )

    extra = [
        [InlineKeyboardButton(
            text="🗓 Запланировать через…", callback_data="admin:storm:plan",
        )],
        [InlineKeyboardButton(
            text="🔍 Аудит эксплойта", callback_data="admin:storm:audit",
        )],
    ]
    if pending is not None:
        extra.append([InlineKeyboardButton(
            text="⚡ Форсировать сейчас", callback_data="admin:storm:force",
        )])

    await safe_edit_text(callback.message, "\n".join(lines),
                         reply_markup=_kb(extra), parse_mode="HTML")


def _plan_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    # Two presets per row
    for i in range(0, len(_HOUR_PRESETS), 2):
        chunk = _HOUR_PRESETS[i:i + 2]
        rows.append([
            InlineKeyboardButton(text=f"через {h} ч", callback_data=f"admin:storm:plan:{h}")
            for h in chunk
        ])
    rows.append([InlineKeyboardButton(text="🔙 К шторму", callback_data="admin:storm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_farm_storm_router.callback_query(F.data == "admin:storm:plan")
async def callback_admin_storm_plan_menu(callback: CallbackQuery):
    """Show "in N hours" presets."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    text = (
        "🗓 <b>Запланировать шторм</b>\n\n"
        "Выберите, через сколько часов он пройдёт.\n"
        "Уведомление полетит всем юзерам с растущими грядками сразу.\n\n"
        "<i>Это заменит текущий ожидающий шторм; уже купленные плёнки сохранятся.</i>"
    )
    await safe_edit_text(callback.message, text,
                         reply_markup=_plan_menu_kb(), parse_mode="HTML")
    await callback.answer()


@admin_farm_storm_router.callback_query(F.data.startswith("admin:storm:plan:"))
async def callback_admin_storm_plan_apply(callback: CallbackQuery):
    """Apply: reschedule + immediate broadcast to everyone with growing plots."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        hours = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Неверное значение", show_alert=True)
        return
    if hours not in _HOUR_PRESETS:
        await callback.answer("Неверное значение", show_alert=True)
        return

    scheduled_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    try:
        storm_id = await database.replace_pending_storm_at(scheduled_at, announce_now=True)
    except Exception as e:
        logger.exception("ADMIN_STORM_PLAN_FAIL: %s", e)
        await callback.answer(f"Ошибка: {type(e).__name__}", show_alert=True)
        return

    # Immediate broadcast — bypass the 30-min worker tick.
    from app.workers.farm_notifications import broadcast_storm_announce
    try:
        users = await database.list_users_with_growing_plots()
        sent = await broadcast_storm_announce(callback.bot, users, scheduled_at)
    except Exception as e:
        logger.exception("ADMIN_STORM_BROADCAST_FAIL: %s", e)
        sent = 0

    logger.info(
        "ADMIN_STORM_PLANNED admin=%s storm_id=%s in_h=%s announce_sent=%s",
        callback.from_user.id, storm_id, hours, sent,
    )
    await callback.answer(
        f"⛈ Шторм через {hours} ч. Уведомлено {sent} юзеров.",
        show_alert=True,
    )
    await _render(callback)


@admin_farm_storm_router.callback_query(F.data == "admin:storm")
async def callback_admin_storm(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await _render(callback)
    await callback.answer()


@admin_farm_storm_router.callback_query(F.data == "admin:storm:force")
async def callback_admin_storm_force(callback: CallbackQuery):
    """Pull scheduled_at to now() so the next worker tick fires both
    announce and execute back-to-back."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await conn.execute(
            "UPDATE farm_storms SET scheduled_at = CURRENT_TIMESTAMP, "
            "announced_at = COALESCE(announced_at, CURRENT_TIMESTAMP) "
            "WHERE executed_at IS NULL"
        )
    logger.info("ADMIN_STORM_FORCED admin=%s result=%s", callback.from_user.id, updated)
    await callback.answer("⚡ Шторм сдвинут на сейчас. Воркер исполнит на следующей итерации.", show_alert=True)
    await _render(callback)


# ────────────────────────────────────────────────────────────────────────
# Audit: pre-fix early-harvest exploit
# ────────────────────────────────────────────────────────────────────────

# Detects the dig-up + replant + early-harvest loop that was possible
# between commit 1326a8e (storm handlers) and commit bfb6b93 (planting
# blocked during storm).  After bfb6b93 this query naturally returns 0
# rows because replant during storm is impossible — there is no legit
# way for the same (user, storm, plot) to receive two early-harvest
# events in one storm window.
_AUDIT_SQL = """
WITH storms AS (
    SELECT id AS storm_id, announced_at,
           COALESCE(executed_at, NOW()) AS window_end
      FROM farm_storms
     WHERE announced_at IS NOT NULL
),
early_harvests AS (
    SELECT user_id, amount, created_at, description,
           NULLIF((regexp_match(description, 'plot (\\d+)'))[1], '')::int AS plot_id
      FROM balance_transactions
     WHERE source = 'farm_early_harvest'
),
in_window AS (
    SELECT s.storm_id, eh.*
      FROM early_harvests eh
      JOIN storms s
        ON eh.created_at >= s.announced_at
       AND eh.created_at <  s.window_end
),
per_plot AS (
    SELECT user_id, storm_id, plot_id,
           COUNT(*)                                AS events,
           SUM(amount)                             AS total_kop,
           SUM(amount) - MIN(amount)               AS exploit_kop
      FROM in_window
     GROUP BY user_id, storm_id, plot_id
    HAVING COUNT(*) > 1
)
SELECT user_id,
       SUM(exploit_kop)               AS exploit_kop_total,
       SUM(events - 1)                AS extra_harvests,
       COUNT(DISTINCT storm_id)       AS storms_affected,
       array_agg(DISTINCT storm_id)   AS storm_ids
  FROM per_plot
 GROUP BY user_id
 ORDER BY exploit_kop_total DESC
 LIMIT 50
"""


@admin_farm_storm_router.callback_query(F.data == "admin:storm:audit")
async def callback_admin_storm_audit(callback: CallbackQuery):
    """Show top users that triggered the early-harvest exploit pre-fix."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer()
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_AUDIT_SQL)
    except Exception as e:
        logger.exception("ADMIN_AUDIT_FAIL: %s", e)
        await safe_edit_text(
            callback.message,
            f"❌ Не удалось выполнить аудит: <code>{type(e).__name__}</code>",
            reply_markup=_kb([]), parse_mode="HTML",
        )
        return

    lines = ["🔍 <b>Аудит эксплойта раннего сбора</b>\n"]
    lines.append(
        "<i>Эксплойт = одна и та же грядка собрана незрелым ≥2 раз "
        "в одном штормовом окне. После фикса (bfb6b93) такие записи "
        "появляться больше не должны.</i>\n"
    )

    if not rows:
        lines.append("✅ <b>Эксплойт не зафиксирован.</b> Подозрительных пользователей нет.")
    else:
        total_kop = sum(r["exploit_kop_total"] or 0 for r in rows)
        lines.append(
            f"❗ Подозрительных юзеров: <b>{len(rows)}</b>  "
            f"общий объём эксплойта: <b>{total_kop / 100:.2f} ₽</b>\n"
        )
        lines.append("<b>Топ-50:</b>")
        lines.append("<code>")
        lines.append(f"{'user_id':>12}  {'эксплойт':>10}  {'лишних':>6}  штормов")
        for r in rows:
            uid = r["user_id"]
            rub = (r["exploit_kop_total"] or 0) / 100
            extra = r["extra_harvests"] or 0
            storms = r["storms_affected"] or 0
            lines.append(f"{uid:>12}  {rub:>8.2f} ₽  {extra:>6}  {storms}")
        lines.append("</code>")

    await safe_edit_text(callback.message, "\n".join(lines),
                         reply_markup=_kb([]), parse_mode="HTML")
