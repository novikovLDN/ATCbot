"""
Admin UI for the winback "2-day gift + 20% discount" campaign.

Two screens:
  - ``admin:winback_2d`` — preview: shows cohort size (raw + bypass-filtered),
    explains what the campaign will do, exposes "Run" button.
  - ``admin:winback_2d_run`` — executes the campaign and posts a stats
    summary back into the admin chat.

The heavy lifting lives in ``app/services/winback.py``; this file only
renders the UI and dispatches.
"""
import asyncio
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.winback import (
    preview_winback_audience,
    run_winback_2d_campaign,
    is_campaign_running,
    SEND_CONCURRENCY,
    PER_MESSAGE_SLEEP,
)
from app.handlers.common.utils import safe_edit_text

admin_winback_router = Router()
logger = logging.getLogger(__name__)


def _admin_only(callback: CallbackQuery) -> bool:
    return callback.from_user.id == config.ADMIN_TELEGRAM_ID


@admin_winback_router.callback_query(F.data == "admin:winback_2d")
async def callback_admin_winback_preview(callback: CallbackQuery):
    """Show the campaign preview with current cohort size + Run button."""
    if not _admin_only(callback):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    language = await resolve_user_language(callback.from_user.id)

    # If a campaign is already mid-flight, show that instead of doing
    # another preview (which would re-pay the Remnawave bypass-balance
    # API cost for nothing).
    if is_campaign_running():
        await safe_edit_text(
            callback.message,
            "⏳ <b>Кампания сейчас идёт</b>\n\nДождитесь отчёта — он придёт в этот чат, когда рассылка завершится.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
            ]),
            bot=callback.bot,
            parse_mode="HTML",
        )
        return

    try:
        audience = await preview_winback_audience()
    except Exception as e:
        logger.exception("WINBACK_PREVIEW_FAILED %s", e)
        await safe_edit_text(
            callback.message,
            f"❌ Не смог посчитать кандидатов: {type(e).__name__}: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
            ]),
            bot=callback.bot,
        )
        return

    title = i18n_get_text(language, "winback.admin_preview_title")

    if audience["filtered_count"] == 0:
        text = title + "\n\n" + i18n_get_text(language, "winback.admin_no_candidates")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
        ])
    else:
        stats = i18n_get_text(
            language, "winback.admin_preview_stats",
            raw=audience["raw_count"],
            filtered=audience["filtered_count"],
            dropped=audience["dropped_by_bypass"],
        )
        text = title + "\n\n" + stats
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "winback.admin_run_button"),
                callback_data="admin:winback_2d_run",
            )],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
        ])

    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@admin_winback_router.callback_query(F.data == "admin:winback_2d_run")
async def callback_admin_winback_run(callback: CallbackQuery):
    """Execute the campaign and post the result back to the admin chat."""
    if not _admin_only(callback):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    # Guard: refuse a second click while a campaign is in flight.
    if is_campaign_running():
        await callback.answer("Кампания уже идёт — дождитесь отчёта", show_alert=True)
        return

    await callback.answer("Запущено", show_alert=False)
    language = await resolve_user_language(callback.from_user.id)
    admin_telegram_id = callback.from_user.id
    chat_id = callback.message.chat.id

    # Resolve the cohort ONCE here (one DB query + one round of Remnawave
    # bypass-balance checks) — we'll pass the survivors straight into the
    # campaign so it doesn't re-pay the API cost.  Fresh resolve at click
    # time guarantees we operate on the current cohort even if the admin
    # opened the preview an hour ago.
    try:
        audience = await preview_winback_audience()
    except Exception as e:
        logger.exception("WINBACK_PREVIEW_AT_RUN_FAILED %s", e)
        await safe_edit_text(
            callback.message,
            f"❌ Не смог посчитать кандидатов перед запуском: {type(e).__name__}: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
            ]),
            bot=callback.bot,
        )
        return

    count = audience["filtered_count"]
    raw_count = audience["raw_count"]
    survivors = audience["survivors"]

    if count == 0:
        await safe_edit_text(
            callback.message,
            i18n_get_text(language, "winback.admin_no_candidates"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
            ]),
            bot=callback.bot,
            parse_mode="HTML",
        )
        return

    # Per-batch ETA: every SEND_CONCURRENCY users runs in parallel, then
    # we sleep PER_MESSAGE_SLEEP per message.  Order-of-magnitude only;
    # grant_access calls the VPN API for new-issuance users which can
    # dominate for cohorts that mostly expired with no prior UUID.
    eta_sec = max(int(count * PER_MESSAGE_SLEEP / SEND_CONCURRENCY), 1)
    await safe_edit_text(
        callback.message,
        i18n_get_text(language, "winback.admin_running", count=count, eta_sec=eta_sec),
        reply_markup=None,
        bot=callback.bot,
        parse_mode="HTML",
    )

    # Run the campaign in the background so we don't keep the callback
    # blocked on a long-running operation.  When done, send the report as
    # a new message in the same chat.  The module-level lock inside
    # ``run_winback_2d_campaign`` is the source of truth — this UI guard
    # just gives the admin instant feedback.
    async def _run_and_report():
        try:
            stats = await run_winback_2d_campaign(
                callback.bot, admin_telegram_id,
                prefiltered_survivors=survivors,
                raw_count_hint=raw_count,
            )
        except Exception as e:
            logger.exception("WINBACK_CAMPAIGN_CRASHED %s", e)
            try:
                await callback.bot.send_message(
                    chat_id,
                    f"❌ Кампания упала: {type(e).__name__}: {e}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        if stats.get("skipped_already_running"):
            try:
                await callback.bot.send_message(
                    chat_id,
                    "⚠️ Кампания уже была запущена другим путём — пропустил, чтобы не дублировать рассылку.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        try:
            await callback.bot.send_message(
                chat_id,
                i18n_get_text(
                    language, "winback.admin_done",
                    raw=stats["raw_candidates"],
                    filtered=stats["after_bypass_filter"],
                    delivered=stats["delivered"],
                    gift_failed=stats["gift_failed"],
                    send_failed=stats["send_failed"],
                    duration=stats["duration_seconds"],
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 В меню рассылок", callback_data="admin:notifications")],
                ]),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("WINBACK_REPORT_FAILED")

    asyncio.create_task(_run_and_report())
