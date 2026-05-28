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
),
exploit_totals AS (
    SELECT user_id,
           SUM(exploit_kop)         AS exploit_kop_total,
           SUM(events - 1)          AS extra_harvests,
           COUNT(DISTINCT storm_id) AS storms_affected
      FROM per_plot
     GROUP BY user_id
),
clawbacks AS (
    SELECT user_id, -SUM(amount) AS clawback_kop_total
      FROM balance_transactions
     WHERE source = 'farm_exploit_clawback'
     GROUP BY user_id
)
SELECT et.user_id,
       et.exploit_kop_total,
       et.extra_harvests,
       et.storms_affected,
       COALESCE(cb.clawback_kop_total, 0)                        AS clawback_kop_total,
       et.exploit_kop_total - COALESCE(cb.clawback_kop_total, 0) AS remaining_kop,
       COALESCE(u.balance, 0)                                    AS current_balance_kop
  FROM exploit_totals et
  LEFT JOIN clawbacks cb ON cb.user_id = et.user_id
  LEFT JOIN users u      ON u.telegram_id = et.user_id
 ORDER BY remaining_kop DESC NULLS LAST
 LIMIT 50
"""


async def _fetch_audit_rows():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(_AUDIT_SQL)


def _compute_clawback_plan(rows):
    """For each row, the actual amount we can take = min(remaining, balance).
    Returns (plan, total_to_clawback_kop, partial_count) where
    plan = [(user_id, take_kop, remaining_kop, balance_kop), ...] for entries with take>0."""
    plan = []
    total = 0
    partial = 0
    for r in rows:
        remaining = int(r["remaining_kop"] or 0)
        balance = int(r["current_balance_kop"] or 0)
        if remaining <= 0:
            continue
        take = min(remaining, max(balance, 0))
        if take <= 0:
            continue
        plan.append((r["user_id"], take, remaining, balance))
        total += take
        if take < remaining:
            partial += 1
    return plan, total, partial


@admin_farm_storm_router.callback_query(F.data == "admin:storm:audit")
async def callback_admin_storm_audit(callback: CallbackQuery):
    """Show top users that triggered the early-harvest exploit pre-fix."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer()
    try:
        rows = await _fetch_audit_rows()
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

    extra_buttons = []
    if not rows:
        lines.append("✅ <b>Эксплойт не зафиксирован.</b> Подозрительных пользователей нет.")
    else:
        exploit_kop = sum(int(r["exploit_kop_total"] or 0) for r in rows)
        clawed_kop = sum(int(r["clawback_kop_total"] or 0) for r in rows)
        remaining_kop = sum(int(r["remaining_kop"] or 0) for r in rows)
        lines.append(
            f"❗ Подозрительных юзеров: <b>{len(rows)}</b>\n"
            f"  объём эксплойта: <b>{exploit_kop / 100:.2f} ₽</b>\n"
            f"  уже списано:    <b>{clawed_kop / 100:.2f} ₽</b>\n"
            f"  осталось:       <b>{remaining_kop / 100:.2f} ₽</b>\n"
        )
        lines.append("<b>Топ-50 (по остатку):</b>")
        lines.append("<code>")
        lines.append(
            f"{'user_id':>10} {'эксплойт':>9} {'списано':>9} {'остаток':>9} {'баланс':>9}"
        )
        for r in rows:
            uid = r["user_id"]
            exp = int(r["exploit_kop_total"] or 0) / 100
            cb = int(r["clawback_kop_total"] or 0) / 100
            rem = int(r["remaining_kop"] or 0) / 100
            bal = int(r["current_balance_kop"] or 0) / 100
            lines.append(
                f"{uid:>10} {exp:>7.2f} ₽ {cb:>7.2f} ₽ {rem:>7.2f} ₽ {bal:>7.2f} ₽"
            )
        lines.append("</code>")

        # Offer clawback only when something is left to take
        plan, total_take, partial = _compute_clawback_plan(rows)
        if plan:
            extra_buttons.append([InlineKeyboardButton(
                text=f"↩ Откатить эксплойт — {total_take / 100:.2f} ₽ с {len(plan)} юзеров",
                callback_data="admin:storm:clawback:confirm",
            )])
        # Spending tracker — even if there's nothing left to claw back, the
        # admin may want to see WHERE the exploit money already went.
        extra_buttons.append([InlineKeyboardButton(
            text="📋 Что потратили эксплойтеры",
            callback_data="admin:storm:spend",
        )])

    await safe_edit_text(callback.message, "\n".join(lines),
                         reply_markup=_kb(extra_buttons), parse_mode="HTML")


@admin_farm_storm_router.callback_query(F.data == "admin:storm:clawback:confirm")
async def callback_admin_storm_clawback_confirm(callback: CallbackQuery):
    """Confirmation screen — recompute the plan at this exact moment so the
    admin sees what we'll really do, not a stale figure from the audit screen."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer()
    try:
        rows = await _fetch_audit_rows()
    except Exception as e:
        logger.exception("ADMIN_CLAWBACK_PREVIEW_FAIL: %s", e)
        await callback.answer(f"Ошибка: {type(e).__name__}", show_alert=True)
        return
    plan, total_take, partial = _compute_clawback_plan(rows)

    if not plan:
        await callback.answer(
            "Списывать нечего — у юзеров нулевой или отрицательный остаток.",
            show_alert=True,
        )
        await _render(callback)
        return

    lines = [
        "⚠️ <b>Подтверждение отката</b>\n",
        f"Будет списано <b>{total_take / 100:.2f} ₽</b> с <b>{len(plan)}</b> юзеров.",
    ]
    if partial:
        lines.append(
            f"  • Частично (на балансе меньше долга): <b>{partial}</b> юзеров — "
            f"спишем сколько есть, остаток долга останется висеть."
        )
    lines.append("\n<b>Превью (топ-15):</b>")
    lines.append("<code>")
    lines.append(f"{'user_id':>10} {'спишем':>9} {'из остатка':>11} {'баланс→0':>10}")
    for uid, take, remaining, balance in plan[:15]:
        new_bal = balance - take
        lines.append(
            f"{uid:>10} {take/100:>7.2f} ₽ {remaining/100:>9.2f} ₽ {new_bal/100:>8.2f} ₽"
        )
    lines.append("</code>")
    if len(plan) > 15:
        lines.append(f"\n…и ещё <b>{len(plan) - 15}</b> юзеров.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Применить откат", callback_data="admin:storm:clawback:apply")],
        [InlineKeyboardButton(text="❌ Отмена",           callback_data="admin:storm:audit")],
    ])
    await safe_edit_text(callback.message, "\n".join(lines), reply_markup=kb, parse_mode="HTML")


@admin_farm_storm_router.callback_query(F.data == "admin:storm:clawback:apply")
async def callback_admin_storm_clawback_apply(callback: CallbackQuery):
    """Atomically deduct the exploit gains.

    Each user runs in its own transaction with an advisory lock and a
    FOR UPDATE on the balance row.  Re-validates remaining_kop > 0 inside
    the txn so repeated clicks are safe (idempotent via 'farm_exploit_clawback'
    transactions accumulating in balance_transactions).
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer("Применяю откат…")
    try:
        rows = await _fetch_audit_rows()
    except Exception as e:
        logger.exception("ADMIN_CLAWBACK_FETCH_FAIL: %s", e)
        await callback.answer(f"Ошибка: {type(e).__name__}", show_alert=True)
        return
    plan, _, _ = _compute_clawback_plan(rows)

    pool = await get_pool()
    applied_total = 0
    applied_users = 0
    partial_users = 0
    skipped_users = 0

    for uid, take_estimated, remaining_estimated, _balance_estimated in plan:
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock($1)", uid)
                    # Recompute under the lock — guards against double-clicks
                    # and against the user spending balance between preview and apply.
                    bal_row = await conn.fetchrow(
                        "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                        uid,
                    )
                    if bal_row is None:
                        skipped_users += 1
                        continue
                    balance = int(bal_row["balance"] or 0)
                    # remaining_estimated was net of existing clawbacks at fetch time;
                    # a parallel admin click could only DECREASE it further, never grow,
                    # so capping at balance here is safe even under a stray double-click.
                    take = min(remaining_estimated, balance)
                    if take <= 0:
                        skipped_users += 1
                        continue
                    await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE telegram_id = $2",
                        take, uid,
                    )
                    await conn.execute(
                        """INSERT INTO balance_transactions
                           (user_id, amount, type, source, description)
                           VALUES ($1, $2, 'admin_adjustment', 'farm_exploit_clawback', $3)""",
                        uid, -take,
                        f"Clawback of early-harvest exploit "
                        f"(took {take} kop of {remaining_estimated} kop owed)",
                    )
                    applied_total += take
                    applied_users += 1
                    if take < remaining_estimated:
                        partial_users += 1
                    logger.info(
                        "ADMIN_CLAWBACK admin=%s user=%s took_kop=%s owed_kop=%s balance_after=%s",
                        callback.from_user.id, uid, take, remaining_estimated, balance - take,
                    )
        except Exception as e:
            logger.exception("ADMIN_CLAWBACK_USER_FAIL user=%s: %s", uid, e)
            skipped_users += 1

    lines = [
        "✅ <b>Откат эксплойта применён</b>\n",
        f"Списано всего: <b>{applied_total / 100:.2f} ₽</b>",
        f"Юзеров обработано: <b>{applied_users}</b>",
    ]
    if partial_users:
        lines.append(
            f"  • Частично покрыто (баланс кончился): <b>{partial_users}</b>"
        )
    if skipped_users:
        lines.append(
            f"  • Пропущено (нулевой баланс / ошибка): <b>{skipped_users}</b>"
        )
    lines.append(
        "\n<i>Пользователям сообщение НЕ отправлено. Если нужно — отправь "
        "массовую рассылку отдельно через центр уведомлений.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Открыть аудит",  callback_data="admin:storm:audit")],
        [InlineKeyboardButton(text="🌪 К шторму",        callback_data="admin:storm")],
        [InlineKeyboardButton(text="🔙 В админку",       callback_data="admin:main")],
    ])
    await safe_edit_text(callback.message, "\n".join(lines), reply_markup=kb, parse_mode="HTML")


# ────────────────────────────────────────────────────────────────────────
# Spending tracker: what did exploiters do with the stolen kopecks?
# ────────────────────────────────────────────────────────────────────────

# Full export: flat (exploiter × spend-event) rows for CSV.  No LIMIT — the
# admin asked for the complete picture; truncation only happens if/when
# Telegram won't accept the document (50 MB).  LEFT JOIN keeps exploiters
# who haven't spent anything yet (their row has NULLs for spend_*).
#
# Balance columns:
#   balance_now_kop          — users.balance, authoritative
#   balance_before_exploit   — reconstructed: sum of every balance_transactions
#                              row strictly before first_exploit_at.  Assumes
#                              every balance change is logged (which is what
#                              increase_balance / decrease_balance / payment
#                              callbacks do).  Mismatch with users.balance
#                              indicates a hand-edit or a legacy path that
#                              didn't log — see balance_recon_now_kop in the
#                              CSV to flag those users.
_SPEND_SQL = """
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
    SELECT user_id, storm_id, plot_id, COUNT(*) AS events,
           SUM(amount) - MIN(amount) AS exploit_kop,
           MIN(created_at) AS first_event_at
      FROM in_window
     GROUP BY user_id, storm_id, plot_id
    HAVING COUNT(*) > 1
),
exploiters AS (
    SELECT user_id,
           SUM(exploit_kop)    AS exploit_kop_total,
           MIN(first_event_at) AS first_exploit_at
      FROM per_plot
     GROUP BY user_id
),
exploiter_balances AS (
    SELECT e.*,
           COALESCE(u.balance, 0) AS balance_now_kop,
           COALESCE((
               SELECT SUM(bt.amount)
                 FROM balance_transactions bt
                WHERE bt.user_id = e.user_id
                  AND bt.created_at < e.first_exploit_at
           ), 0) AS balance_before_kop,
           COALESCE((
               SELECT SUM(bt.amount)
                 FROM balance_transactions bt
                WHERE bt.user_id = e.user_id
           ), 0) AS balance_recon_now_kop
      FROM exploiters e
      LEFT JOIN users u ON u.telegram_id = e.user_id
)
SELECT e.user_id,
       e.exploit_kop_total,
       e.first_exploit_at,
       e.balance_before_kop,
       e.balance_now_kop,
       e.balance_recon_now_kop,
       bt.created_at  AS spend_at,
       bt.source      AS spend_source,
       (-bt.amount)   AS spend_kop,
       bt.description AS spend_description
  FROM exploiter_balances e
  LEFT JOIN balance_transactions bt
    ON bt.user_id = e.user_id
   AND bt.amount < 0
   AND bt.source <> 'farm_exploit_clawback'
   AND bt.created_at >= e.first_exploit_at
 ORDER BY e.exploit_kop_total DESC, bt.created_at DESC
"""




@admin_farm_storm_router.callback_query(F.data == "admin:storm:spend")
async def callback_admin_storm_spend(callback: CallbackQuery):
    """Export, as a CSV document, every negative balance_transaction posted
    by every exploiter since their first exploit event.  One row per
    spend event; exploiters with zero spending get one row with empty
    spend_* columns so they still appear in the file."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer("Формирую CSV…")

    import csv
    import io
    import html as _html
    from datetime import datetime as _dt
    from aiogram.types import BufferedInputFile

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SPEND_SQL)
    except Exception as e:
        logger.exception("ADMIN_SPEND_SQL_FAIL: %s", e)
        try:
            await safe_edit_text(
                callback.message,
                f"❌ Не удалось получить траты: <code>{_html.escape(type(e).__name__)}</code>\n"
                f"<code>{_html.escape(str(e)[:300])}</code>",
                reply_markup=_kb([]), parse_mode="HTML", bot=callback.bot,
            )
        except Exception:
            await callback.answer(f"Ошибка SQL: {type(e).__name__}", show_alert=True)
        return

    if not rows:
        await safe_edit_text(
            callback.message,
            "📋 <b>Траты эксплойтеров</b>\n\n"
            "✅ Эксплойтеров не найдено — экспортировать нечего.",
            reply_markup=_kb([]), parse_mode="HTML", bot=callback.bot,
        )
        return

    # Aggregate stats for the caption + chat-side preview.
    unique_users = set()
    total_exploit_kop = 0
    total_spent_kop = 0
    spend_event_count = 0
    total_balance_before_kop = 0
    total_balance_now_kop = 0
    drift_user_count = 0  # users whose users.balance disagrees with reconstruction
    for r in rows:
        uid = r["user_id"]
        if uid not in unique_users:
            unique_users.add(uid)
            total_exploit_kop += int(r["exploit_kop_total"] or 0)
            total_balance_before_kop += int(r["balance_before_kop"] or 0)
            total_balance_now_kop += int(r["balance_now_kop"] or 0)
            if abs(int(r["balance_now_kop"] or 0) - int(r["balance_recon_now_kop"] or 0)) > 0:
                drift_user_count += 1
        if r["spend_kop"] is not None:
            total_spent_kop += int(r["spend_kop"])
            spend_event_count += 1

    # Build CSV in memory.  UTF-8 with BOM so Excel auto-detects encoding.
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "user_id",
        "exploit_rub_total",
        "first_exploit_at_utc",
        "balance_before_exploit_rub",   # reconstructed from balance_transactions
        "balance_now_rub",              # users.balance (source of truth)
        "balance_recon_now_rub",        # sum(transactions) — should match balance_now
        "recon_drift_rub",              # balance_now − balance_recon_now (≈0 if logs are clean)
        "spend_at_utc",
        "spend_source",
        "spend_rub",
        "spend_description",
    ])
    for r in rows:
        exp_kop = int(r["exploit_kop_total"] or 0)
        first_at = r["first_exploit_at"]
        spend_at = r["spend_at"]
        spend_kop = r["spend_kop"]
        bal_before = int(r["balance_before_kop"] or 0)
        bal_now = int(r["balance_now_kop"] or 0)
        bal_recon = int(r["balance_recon_now_kop"] or 0)
        writer.writerow([
            r["user_id"],
            f"{exp_kop / 100:.2f}",
            first_at.isoformat() if first_at else "",
            f"{bal_before / 100:.2f}",
            f"{bal_now / 100:.2f}",
            f"{bal_recon / 100:.2f}",
            f"{(bal_now - bal_recon) / 100:.2f}",
            spend_at.isoformat() if spend_at else "",
            r["spend_source"] or "",
            f"{int(spend_kop) / 100:.2f}" if spend_kop is not None else "",
            r["spend_description"] or "",
        ])
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")

    stamp = _dt.utcnow().strftime("%Y%m%d_%H%M%SZ")
    filename = f"farm_exploit_spend_{stamp}.csv"
    caption_lines = [
        "📋 <b>Аудит трат эксплойтеров</b>",
        f"  юзеров: <b>{len(unique_users)}</b>",
        f"  объём эксплойта: <b>{total_exploit_kop / 100:.2f} ₽</b>",
        f"  баланс ДО бага (суммарно): <b>{total_balance_before_kop / 100:.2f} ₽</b>",
        f"  баланс СЕЙЧАС (суммарно): <b>{total_balance_now_kop / 100:.2f} ₽</b>",
        f"  списаний после эксплойта: <b>{spend_event_count}</b> на "
        f"<b>{total_spent_kop / 100:.2f} ₽</b>",
        f"  всего строк в файле: <b>{len(rows)}</b>",
    ]
    if drift_user_count > 0:
        caption_lines.append(
            f"  ⚠️ юзеров с расхождением reconstruction vs users.balance: "
            f"<b>{drift_user_count}</b> — см. колонку recon_drift_rub"
        )
    caption = "\n".join(caption_lines)

    try:
        await callback.bot.send_document(
            chat_id=callback.from_user.id,
            document=BufferedInputFile(csv_bytes, filename=filename),
            caption=caption,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("ADMIN_SPEND_SEND_DOC_FAIL: %s", e)
        try:
            await safe_edit_text(
                callback.message,
                f"❌ Не удалось отправить файл: <code>{_html.escape(type(e).__name__)}</code>\n"
                f"<code>{_html.escape(str(e)[:300])}</code>",
                reply_markup=_kb([]), parse_mode="HTML", bot=callback.bot,
            )
        except Exception:
            await callback.answer(f"Ошибка отправки: {type(e).__name__}", show_alert=True)
        return

    # Confirm on the original screen so the admin sees "done" + can come back.
    await safe_edit_text(
        callback.message,
        f"📋 <b>Аудит трат эксплойтеров</b>\n\n"
        f"📎 Файл <code>{_html.escape(filename)}</code> отправлен в чат "
        f"({len(csv_bytes) // 1024} КБ, {len(rows)} строк).\n\n"
        f"<i>Открывается в Excel / Google Sheets / Numbers.</i>",
        reply_markup=_kb([]), parse_mode="HTML", bot=callback.bot,
    )
