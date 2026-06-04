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
# Max premium subscriptions scanned per run (safety ceiling).
_MAX_SCAN = 100_000
# Seconds between live progress updates of the admin message.
_PROGRESS_INTERVAL = 4
# Concurrent Remnawave PATCH calls while fixing mismatches (no bulk endpoint).
_FIX_CONCURRENCY = 16
# Last reconciliation result per admin id — feeds the "Исправить" button.
_last_mismatches: dict[int, list] = {}


# Mismatch reason buckets. The fix path branches on these — date-only diffs
# get a cheap PATCH, missing-entity ones need a full re-provision.
_REASON_DATE = "date_mismatch"
_REASON_MISSING = "missing_on_panel"


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

    Fetches the whole Remnawave user base once via paginated GET /api/users,
    then compares locally — a handful of API calls instead of one per user.

    Returns (checked_count, mismatches). Each mismatch is a dict with
    telegram_id, db_expires_at, rmn_expires_at (datetime or None), reason.

    Raises RuntimeError if Remnawave cannot return the user list, so the
    caller reports a clear error instead of acting on partial data.

    If `progress` is given, its "phase" / "total" / "done" keys are kept
    current so the caller can render a live progress indicator.
    """
    subs = await database.get_all_active_subscriptions()
    premium = [
        s for s in subs
        if s.get("remnawave_premium_uuid") and s.get("expires_at")
    ][:_MAX_SCAN]

    if progress is not None:
        progress["phase"] = "fetch"
        progress["total"] = len(premium)
        progress["done"] = 0
        progress["fetched"] = 0
        progress["fetch_total"] = None

    def _on_fetch(collected: int, total):
        if progress is not None:
            progress["fetched"] = collected
            progress["fetch_total"] = total

    all_users = await remnawave_api.get_all_users(progress_cb=_on_fetch)
    if all_users is None:
        raise RuntimeError(
            "Remnawave не отдал список пользователей (GET /api/users)."
        )

    by_uuid: dict = {}
    for u in all_users:
        uid = u.get("uuid")
        if uid:
            by_uuid[uid] = u

    if progress is not None:
        progress["phase"] = "compare"

    mismatches = []
    for sub in premium:
        if progress is not None:
            progress["done"] += 1
        telegram_id = sub.get("telegram_id")
        db_expires = sub.get("expires_at")
        uuid = sub.get("remnawave_premium_uuid")
        tariff = (sub.get("subscription_type") or "basic").strip().lower()

        rmn_user = by_uuid.get(uuid)
        if rmn_user is None:
            mismatches.append({
                "telegram_id": telegram_id,
                "db_expires_at": db_expires,
                "rmn_expires_at": None,
                "reason": _REASON_MISSING,
                "tariff": tariff,
            })
            continue
        rmn_expires = _parse_rmn_dt(rmn_user.get("expireAt"))
        if rmn_expires is None:
            mismatches.append({
                "telegram_id": telegram_id,
                "db_expires_at": db_expires,
                "rmn_expires_at": None,
                "reason": _REASON_MISSING,
                "tariff": tariff,
            })
            continue
        if abs((db_expires - rmn_expires).total_seconds()) > _TOLERANCE_SECONDS:
            mismatches.append({
                "telegram_id": telegram_id,
                "db_expires_at": db_expires,
                "rmn_expires_at": rmn_expires,
                "reason": _REASON_DATE,
                "tariff": tariff,
            })

    return len(premium), mismatches


_REASON_LABEL = {
    _REASON_MISSING: "нет на панели",
    _REASON_DATE: "дата не совпадает",
}


def _format_report(checked: int, mismatches: list) -> str:
    missing = sum(1 for m in mismatches if m["reason"] == _REASON_MISSING)
    date_diff = sum(1 for m in mismatches if m["reason"] == _REASON_DATE)
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
    lines.append(f"  • Нет на панели: <b>{missing}</b> — нужен полный provision")
    lines.append(f"  • Дата не совпадает: <b>{date_diff}</b> — нужен PATCH expireAt")
    lines.append("")
    shown = mismatches[:20]
    for i, m in enumerate(shown, 1):
        db_str = m["db_expires_at"].strftime("%Y-%m-%d %H:%M")
        rmn = m["rmn_expires_at"]
        rmn_str = rmn.strftime("%Y-%m-%d %H:%M") if rmn else "—"
        reason_label = _REASON_LABEL.get(m["reason"], m["reason"])
        lines.append(
            f"{i}. <code>{m['telegram_id']}</code> · {reason_label}\n"
            f"   БД: {db_str} · Remnawave: {rmn_str}"
        )
    if len(mismatches) > len(shown):
        lines.append(f"\n…и ещё {len(mismatches) - len(shown)}.")
    lines.append(
        "\n«Исправить» догонит панель под БД: для отсутствующих — пересоздаст "
        "entity, для расходящихся дат — обновит expireAt."
    )
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
        "🔄 Сверяю premium-подписки с Remnawave…\nДождитесь отчёта.",
        bot=callback.bot, parse_mode="HTML",
    )

    progress: dict = {"phase": "fetch", "total": 0, "done": 0}
    try:
        scan_task = asyncio.create_task(_scan_mismatches(progress))
        while not scan_task.done():
            await asyncio.sleep(_PROGRESS_INTERVAL)
            if scan_task.done():
                break
            if progress.get("phase") == "compare" and progress.get("total"):
                text = (
                    "🔄 Сверяю даты…\n\n"
                    f"Проверено: <b>{progress.get('done', 0)}</b> / {progress['total']}"
                )
            else:
                fetched = progress.get("fetched", 0)
                fetch_total = progress.get("fetch_total")
                if fetched:
                    if fetch_total:
                        text = (
                            "🔄 Выгружаю пользователей из Remnawave…\n\n"
                            f"Получено: <b>{fetched}</b> / {fetch_total}"
                        )
                    else:
                        text = (
                            "🔄 Выгружаю пользователей из Remnawave…\n\n"
                            f"Получено: <b>{fetched}</b>"
                        )
                else:
                    text = "🔄 Выгружаю пользователей из Remnawave…"
            try:
                await safe_edit_text(
                    callback.message, text, bot=callback.bot, parse_mode="HTML",
                )
            except Exception as ui_err:
                logger.debug("RECONCILE: progress edit suppressed: %s", ui_err)
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

    sem = asyncio.Semaphore(_FIX_CONCURRENCY)
    progress: dict = {"done": 0, "fixed_date": 0, "fixed_provision": 0, "failed": 0}

    async def _fix_one(m: dict) -> bool:
        async with sem:
            ok = False
            try:
                if m["reason"] == _REASON_DATE:
                    ok = await remnawave_premium.renew_premium_user(
                        m["telegram_id"], m["db_expires_at"],
                    )
                    if ok:
                        progress["fixed_date"] += 1
                elif m["reason"] == _REASON_MISSING:
                    # Recreate the panel entity end-to-end. provision_subscription
                    # is idempotent: it adopts an existing entity if one exists
                    # under our requested uuid, otherwise creates a new one and
                    # writes the new uuid/url back into the DB.
                    from app.services import purchase_flow
                    pd_seconds = (m["db_expires_at"] - datetime.now(timezone.utc)).total_seconds()
                    period_days = max(1, int(pd_seconds // 86400))
                    await purchase_flow.provision_subscription(
                        m["telegram_id"],
                        tariff=m.get("tariff") or "basic",
                        subscription_end=m["db_expires_at"],
                        period_days=period_days,
                        is_trial=False,
                    )
                    ok = True
                    progress["fixed_provision"] += 1
            except Exception as e:
                logger.warning(
                    "RECONCILE_FIX: tg=%s reason=%s failed: %s",
                    m["telegram_id"], m["reason"], e,
                )
                ok = False
            if not ok and m["reason"] != _REASON_DATE:
                # PATCH path tracks its own success in fixed_date — only count
                # other failures here to avoid double-counting.
                pass
            if not ok:
                progress["failed"] += 1
        progress["done"] += 1
        return bool(ok)

    async def _run_all_fixes():
        return await asyncio.gather(*[_fix_one(m) for m in mismatches])

    fix_task = asyncio.create_task(_run_all_fixes())
    while not fix_task.done():
        await asyncio.sleep(_PROGRESS_INTERVAL)
        if fix_task.done():
            break
        try:
            await safe_edit_text(
                callback.message,
                "🔧 Исправляю расхождения…\n\n"
                f"Обработано: <b>{progress['done']}</b> / {total}\n"
                f"  ✅ Дата подтянута: {progress['fixed_date']}\n"
                f"  ✅ Entity создан: {progress['fixed_provision']}\n"
                f"  ❌ Не удалось: {progress['failed']}",
                bot=callback.bot, parse_mode="HTML",
            )
        except Exception as ui_err:
            logger.debug("RECONCILE_FIX: progress edit suppressed: %s", ui_err)
    results = await fix_task
    fixed = sum(1 for r in results if r)
    failed = total - fixed

    _last_mismatches.pop(callback.from_user.id, None)

    text = (
        "🔧 <b>Исправление расхождений</b>\n\n"
        f"✅ Исправлено всего: <b>{fixed}</b> / {total}\n"
        f"  • Дата подтянута: <b>{progress['fixed_date']}</b>\n"
        f"  • Entity создан: <b>{progress['fixed_provision']}</b>\n"
        f"❌ Не удалось: <b>{failed}</b>\n\n"
        "Запустите сверку повторно, чтобы убедиться."
    )
    await safe_edit_text(
        callback.message, text,
        reply_markup=get_admin_back_keyboard(), bot=callback.bot, parse_mode="HTML",
    )
