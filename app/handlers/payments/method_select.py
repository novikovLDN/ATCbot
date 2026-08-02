"""Экран выбора способа оплаты — третий шаг покупки подписки.

ЧТО ЭТО
    После выбора тарифа и периода человек попадает сюда и решает, чем
    платить: картой через Платегу или Lava, СБП, международной картой,
    звёздами Telegram, криптой или с внутреннего баланса.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ
    Раньше функция жила в корневом handlers.py — файле на тысячу строк, из
    которого 32 функции из 35 дословно повторяли app/handlers/common/*.
    Наружу из него использовалась ровно эта одна функция, поэтому дубли
    удалены, а она переехала туда, где ей место: рядом с остальным
    платёжным потоком.

ЧТО ЛЕГКО СЛОМАТЬ
    Набор кнопок зависит от того, какие провайдеры включены в конфиге:
    выключенный провайдер не должен появляться на экране, иначе человек
    упрётся в ошибку уже после выбора. Проверки делаются в момент показа,
    а не на импорте, — конфиг может измениться без перезапуска.
"""
import logging


from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text

logger = logging.getLogger(__name__)


async def show_payment_method_selection(
    callback: CallbackQuery,
    tariff_type: str,
    period_days: int,
    final_price_kopecks: int,
) -> None:
    """Показать экран выбора способа оплаты.

    Args:
        tariff_type: тариф, который покупают (basic/plus/combo_*/biz_*).
        period_days: срок подписки — нужен вызывающему коду, здесь только
            для полноты контекста покупки.
        final_price_kopecks: итоговая цена со всеми скидками, в копейках.
            Делится на 100 при показе: в базе деньги всегда в копейках.
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    balance_rubles = await database.get_user_balance(telegram_id)
    final_price_rubles = final_price_kopecks / 100.0

    text = i18n_get_text(language, "payment.select_method", price=final_price_rubles)

    # Провайдеры проверяются в момент показа: выключенный не должен попасть
    # на экран, иначе человек выберет способ и упрётся в ошибку.
    import platega_service
    import lava_service
    import cryptobot_service

    platega_on = platega_service.is_enabled()
    lava_on = lava_service.is_enabled()
    crypto_on = cryptobot_service.is_enabled()

    btn_card_pl = InlineKeyboardButton(text=i18n_get_text(language, "payment.card_pl"), callback_data="pay:card_pl")
    btn_sbp = InlineKeyboardButton(text=i18n_get_text(language, "payment.sbp"), callback_data="pay:sbp")
    btn_card = InlineKeyboardButton(text=i18n_get_text(language, "payment.card"), callback_data="pay:card")
    btn_lava = InlineKeyboardButton(text=i18n_get_text(language, "payment.lava"), callback_data="pay:lava")
    btn_intl = InlineKeyboardButton(text=i18n_get_text(language, "payment.intl_pl"), callback_data="pay:intl_pl")
    btn_stars = InlineKeyboardButton(text=i18n_get_text(language, "payment.stars"), callback_data="pay:stars")
    btn_crypto = InlineKeyboardButton(text=i18n_get_text(language, "payment.crypto"), callback_data="pay:crypto")

    buttons: list[list[InlineKeyboardButton]] = []

    if platega_on:
        buttons.append([btn_card_pl, btn_sbp])

    row_card = [btn_card]
    if lava_on:
        row_card.append(btn_lava)
    buttons.append(row_card)

    if platega_on:
        buttons.append([btn_intl])

    row_stars = [btn_stars]
    if crypto_on:
        row_stars.append(btn_crypto)
    buttons.append(row_stars)

    # Баланс — последним: он показывает сумму, и её удобнее читать рядом
    # с кнопкой «Назад», а не среди внешних провайдеров.
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.balance", balance=balance_rubles),
        callback_data="pay:balance",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_buy_vpn",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
    except Exception as e:
        logger.exception("Error showing payment method selection: %s", e)
        await callback.answer(
            i18n_get_text(language, "errors.payment_processing"),
            show_alert=True,
        )


__all__ = ["show_payment_method_selection"]
