"""«Купить со скидкой» / «Продлить со скидкой» from a sales-funnel message.

The discount was granted when the message was sent (app/services/sales_funnel):
this button only tells what is active and opens the purchase screen — it never
creates or extends a discount, so pressing it again changes nothing. Prices on
the tariff screens come from calculate_final_price (the same largest discount).
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.handlers.common.guards import ensure_db_ready_callback
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

logger = logging.getLogger(__name__)

funnel_router = Router()


@funnel_router.callback_query(F.data.startswith("funnel_buy:"))
async def callback_funnel_buy(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    from app.services.sales_funnel import service as funnel

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    try:
        percent, deadline = await funnel.effective_discount(telegram_id)
    except Exception as e:  # noqa: BLE001 — the screen still opens
        logger.warning("SALES_FUNNEL_BUY_DISCOUNT_READ_FAILED user=%s: %s", telegram_id, type(e).__name__)
        percent, deadline = 0, None
    if percent > 0:
        note = i18n_get_text(language, "funnel.discount_active_note", percent=percent,
                             deadline=funnel.format_deadline(language, deadline))
    else:
        note = i18n_get_text(language, "funnel.discount_expired_note")
    if callback.message is not None:
        await callback.message.answer(note, parse_mode="HTML")
    logger.info("SALES_FUNNEL_BUY_CLICK user=%s data=%s percent=%s", telegram_id, callback.data, percent)

    from app.handlers.common.screens import show_tariffs_main_screen
    await show_tariffs_main_screen(callback, state, force_new_message=True)
