"""
Admin Farm Storm console.

Shows the next scheduled storm + last 5 events with counters, plus a
"Force now" button that pulls scheduled_at to the present so the worker
picks it up on its next iteration.
"""
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from database.core import get_pool
from app.handlers.common.utils import safe_edit_text

admin_farm_storm_router = Router()
logger = logging.getLogger(__name__)


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

    extra = []
    if pending is not None:
        extra.append([InlineKeyboardButton(
            text="⚡ Форсировать сейчас", callback_data="admin:storm:force",
        )])

    await safe_edit_text(callback.message, "\n".join(lines),
                         reply_markup=_kb(extra), parse_mode="HTML")


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
