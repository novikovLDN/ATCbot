"""
ЭКРАН 3 — выбор способа оплаты VPN (перенесено 1:1 из корневого handlers.py).
"""
import logging

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database
from app.services.language_service import resolve_user_language
from app.i18n import get_text as i18n_get_text

logger = logging.getLogger(__name__)

# Фото экрана выбора способа оплаты «💳 К оплате: N ₽» (2026-08).
PAYMENT_METHOD_PHOTO_FILE_ID = "AgACAgQAAxkBAAF-krxqdr7EFkK7vBafs-7eLasnInLCjAACfQ1rG2iAuVPYVBCZHdhHgQEAAwIAA3kAAz0E"


async def show_payment_method_selection(
    callback: CallbackQuery,
    tariff_type: str,
    period_days: int,
    final_price_kopecks: int,
    back_callback: str = "menu_buy_vpn",
):
    """ЭКРАН 3 — Выбор способа оплаты
    
    Показывает кнопки:
    - 💰 Баланс (доступно: N ₽)
    - 💳 Банковская карта
    - ⬅️ Назад
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Получаем баланс пользователя
    balance_rubles = await database.get_user_balance(telegram_id)
    final_price_rubles = final_price_kopecks / 100.0
    
    # Формируем текст
    text = i18n_get_text(language, "payment.select_method", price=final_price_rubles)
    
    # Формируем кнопки
    buttons = []

    import platega_service
    import wata_service
    import cryptobot_service
    platega_on = platega_service.is_enabled()
    wata_on = wata_service.is_enabled()
    crypto_on = cryptobot_service.is_enabled()

    btn_card_pl = InlineKeyboardButton(text=i18n_get_text(language, "payment.card_pl"), callback_data="pay:card_pl")
    # СБП — обратно через Platega (revert Wata-миграции по просьбе).
    from app.handlers.common.payment_labels import sbp_label
    btn_sbp = InlineKeyboardButton(text=sbp_label(language), callback_data="pay:sbp")
    btn_card = InlineKeyboardButton(text=i18n_get_text(language, "payment.card"), callback_data="pay:card")
    # WATA (карта/СБП/T-Pay) → pay:wata.
    btn_wata = InlineKeyboardButton(text=i18n_get_text(language, "payment.lava"), callback_data="pay:wata")
    btn_intl = InlineKeyboardButton(text=i18n_get_text(language, "payment.intl_pl"), callback_data="pay:intl_pl")
    btn_stars = InlineKeyboardButton(text=i18n_get_text(language, "payment.stars"), callback_data="pay:stars")
    btn_crypto = InlineKeyboardButton(text=i18n_get_text(language, "payment.crypto"), callback_data="pay:crypto")

    if platega_on:
        # [Карта Platega] [СБП Platega]
        buttons.append([btn_card_pl, btn_sbp])

    # One button per cash desk (08 #20): with WATA on, «Карта резерв» (pay:card)
    # opened the same WATA page as the WATA button — only the WATA button
    # («Карта / СБП») is shown then; the Telegram card invoice only without WATA.
    buttons.append([btn_wata] if wata_on else [btn_card])

    if platega_on:
        buttons.append([btn_intl])

    row4 = [btn_stars]
    if crypto_on:
        row4.append(btn_crypto)
    buttons.append(row4)

    balance_button_text = i18n_get_text(language, "payment.balance", balance=balance_rubles)
    buttons.append([InlineKeyboardButton(text=balance_button_text, callback_data="pay:balance")])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        # 08 M14: back to the period screen of this tariff (was the buy root)
        callback_data=back_callback,
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Экран выбора способа оплаты рендерится с новым фото — safe_edit_text
    # умеет только менять caption/text, но не саму картинку. Удаляем
    # предыдущее сообщение и отправляем новое photo+caption.
    try:
        try:
            await callback.message.delete()
        except Exception:
            pass
        try:
            await callback.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=PAYMENT_METHOD_PHOTO_FILE_ID,
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            # Fallback без фото — если file_id устарел на текущем боте.
            await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        await callback.answer()
    except Exception as e:
        logger.exception("Error showing payment method selection: %s", e)
        await callback.answer(
            i18n_get_text(language, "errors.payment_processing"),
            show_alert=True
        )
