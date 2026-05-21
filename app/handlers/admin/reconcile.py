"""
Admin: reconcile premium subscription expiry between our DB and Remnawave.

Premium VPN users carry `remnawave_premium_uuid`. Their Remnawave `expireAt`
must match our DB `expires_at`. This panel lists mismatches and can push the
DB date into Remnawave (renew_premium_user) for every mismatched user.
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
import database
from app.services import remnawave_api, remnawave_premium
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_reconcile_router = Router()
logger = logging.getLogger(__name__)

# Tolerance: differences below this are treated as equal (clock skew / rounding).
_TOLERANCE_SECONDS = 3600
# Max users scanned per run (safety ceiling for the API loop).
_MAX_SCAN = 2000
# Concurrent Remnawave API calls.
_CONCURRENCY = 10
# Seconds between live progress updates of the admin message.
_PROGRESS_INTERVAL = 4
# Last reconciliation result per admin id — feeds the "Исправить" button.
_last_mismatches: dict[int, list] = {}


def _parse_rmn_dt(value) -> "datetime | None":
    """Parse a Remnawave ISO-8601 expireAt string into a UTC-aware datetime."""
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


async def _scan_mismatches(progress: "dict | None" = None) -> "tuple[int, list]":
    """Compare DB expires_at vs Remnawave expireAt for premium VPN users.

    Returns (checked_count, mismatches). Each mismatch is a dict with
    telegram_id, db_expires_at, rmn_expires_at (datetime or None), reason.

    If `progress` is given, its "total" / "done" keys are kept up to date so a
    caller can render a live progress indicator while the scan runs.
    """
    subs = await database.get_all_active_subscriptions()
    premium = [
        s for s in subs
        if s.get("remnawave_premium_uuid") and s.get("expires_at")
    ][:_MAX_SCAN]

    if progress is not None:
        progress["total"] = len(premium)
        progress["done"] = 0

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _check(sub: dict) -> "dict | None":
        telegram_id = sub.get("telegram_id")
        db_expires = sub.get("expires_at")
        uuid = sub.get("remnawave_premium_uuid")
        async with sem:
            try:
                rmn_user = await remnawave_api.get_user(uuid)
            except Exception as e:
                logger.warning("RECONCILE: get_user failed tg=%s: %s", telegram_id, e)
                return None
        if rmn_user is None:
            return {
                "telegram_id": telegram_id,
                "db_expires_at": db_expires,
                "rmn_expires_at": None,
                "reason": "нет в Remnawave",
            }
        rmn_expires = _parse_rmn_dt(rmn_user.get("expireAt"))
        if rmn_expires is None:
            return {
                "telegram_id": telegram_id,
                "db_expires_at": db_expires,
                "rmn_expires_at": None,
                "reason": "нет даты в Remnawave",
            }
        if abs((db_expires - rmn_expires).total_seconds()) > _TOLERANCE_SECONDS:
            return {
                "telegram_id": telegram_id,
                "db_expires_at": db_expires,
                "rmn_expires_at": rmn_expires,
                "reason": "дата не совпадает",
            }
        return None

    async def _check_counted(sub: dict) -> "dict | None":
        try:
            return await _check(sub)
        finally:
            if progress is not None:
                progress["done"] += 1

    results = await asyncio.gather(*[_check_counted(s) for s in premium])
    mismatches = [r for r in results if r is not None]
    return len(premium), mismatches


def _format_report(checked: int, mismatches: list) -> str:
    lines = [
        "🔄 <b>Сверка premium-подписок с Remnawave</b>",
        "",
        f"Проверено premium-подписок: <b>{checked}</b>",
        f"Расхождений: <b>{len(mismatches)}</b>",
    ]
    if not mismatches:
        lines.append("\n✅ Все даты совпадают.")
        return "\n".join(lines)

    lines.append("")
    shown = mismatches[:30]
    for i, m in enumerate(shown, 1):
        db_str = m["db_expires_at"].strftime("%Y-%m-%d %H:%M")
        rmn = m["rmn_expires_at"]
        rmn_str = rmn.strftime("%Y-%m-%d %H:%M") if rmn else "—"
        lines.append(
            f"{i}. <code>{m['telegram_id']}</code> · {m['reason']}\n"
            f"   БД: {db_str} · Remnawave: {rmn_str}"
        )
    if len(mismatches) > len(shown):
        lines.append(f"\n…и ещё {len(mismatches) - len(shown)}.")
    lines.append("\nКнопка «Исправить» подтянет дату Remnawave под дату нашей БД.")
    return "\n".join(lines)


@admin_reconcile_router.callback_query(F.data == "admin:rmn_reconcile")
async def callback_rmn_reconcile(callback: CallbackQuery):
    """Run the DB↔Remnawave premium expiry reconciliation and show a report."""
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
            "🔄 <b>Сверка с Remnawave</b>\n\nRemnawave отключён в конфиге.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    await safe_edit_text(
        callback.message,
        "🔄 Сверяю premium-подписки с Remnawave…\n"
        "При большой базе это может занять несколько минут — дождитесь отчёта.",
        bot=callback.bot, parse_mode="HTML",
    )

    progress: dict = {"total": 0, "done": 0}
    try:
        scan_task = asyncio.create_task(_scan_mismatches(progress))
        while not scan_task.done():
            await asyncio.sleep(_PROGRESS_INTERVAL)
            total = progress.get("total") or 0
            if total and not scan_task.done():
                await safe_edit_text(
                    callback.message,
                    "🔄 Сверяю premium-подписки с Remnawave…\n\n"
                    f"Проверено: <b>{progress.get('done', 0)}</b> / {total}",
                    bot=callback.bot, parse_mode="HTML",
                )
        checked, mismatches = await scan_task
    except Exception as e:
        logger.exception("RECONCILE: scan failed: %s", e)
        await safe_edit_text(
            callback.message,
            f"❌ Ошибка при сверке: {e}",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    _last_mismatches[callback.from_user.id] = mismatches

    rows = []
    if mismatches:
        rows.append([InlineKeyboardButton(
            text=f"🔧 Исправить ({len(mismatches)})",
            callback_data="admin:rmn_fix",
        )])
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])

    await safe_edit_text(
        callback.message,
        _format_report(checked, mismatches),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_reconcile_router.callback_query(F.data == "admin:rmn_fix")
async def callback_rmn_fix(callback: CallbackQuery):
    """Push DB expires_at into Remnawave for every mismatched premium user."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    mismatches = _last_mismatches.get(callback.from_user.id)
    if not mismatches:
        await safe_edit_text(
            callback.message,
            "🔄 <b>Сверка с Remnawave</b>\n\nНечего исправлять — сначала запустите сверку.",
            reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
        )
        return

    total = len(mismatches)
    await safe_edit_text(
        callback.message,
        f"🔧 Исправляю {total} расхождений…",
        bot=callback.bot, parse_mode="HTML",
    )

    fixed = 0
    failed = 0
    for i, m in enumerate(mismatches, 1):
        try:
            ok = await remnawave_premium.renew_premium_user(
                m["telegram_id"], m["db_expires_at"],
            )
            if ok:
                fixed += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning("RECONCILE_FIX: tg=%s failed: %s", m["telegram_id"], e)
            failed += 1
        if i % 20 == 0 and i != total:
            await safe_edit_text(
                callback.message,
                f"🔧 Исправляю расхождения…\n\n"
                f"Обработано: <b>{i}</b> / {total}",
                bot=callback.bot, parse_mode="HTML",
            )

    _last_mismatches.pop(callback.from_user.id, None)

    text = (
        "🔧 <b>Исправление расхождений</b>\n\n"
        f"✅ Исправлено: <b>{fixed}</b>\n"
        f"❌ Не удалось: <b>{failed}</b>\n\n"
        "Дата Remnawave подтянута под дату нашей БД.\n"
        "Запустите сверку повторно, чтобы убедиться."
    )
    await safe_edit_text(
        callback.message, text,
        reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
    )
