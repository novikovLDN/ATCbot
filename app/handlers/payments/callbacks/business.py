"""Бизнес-тарифы: каталог и выбор страны.

ЧТО ЗДЕСЬ
    Вход в каталог бизнес-тарифов и экран периодов с ценами для выбранной
    страны.

ПОЧЕМУ ВЫДЕЛЕНО
    У бизнес-тарифов свой шаг, которого нет у остальных, — страна, и своя
    формула цены (множитель страны). Дальше поток тот же, что и у обычной
    покупки.

ЧТО ЛЕГКО СЛОМАТЬ
    Страна кладётся в FSM и оттуда же читается при расчёте цены на шаге
    периода. Потеряете — человек увидит цену одной страны, а заплатит по
    другой.

    Кнопки каталога ведут на «tariff:biz_*», то есть в общий обработчик
    выбора тарифа: он сам разворачивает бизнес-ветку по is_biz_tariff.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import config
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text, validate_callback_data
from app.handlers.common.states import PurchaseState
from app.handlers.payments.callbacks.tariff_meta import _period_badge

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "corporate_access_request")
async def callback_corporate_access_request(callback: CallbackQuery, state: FSMContext):
    """
    🏢 BUSINESS TARIFF CATALOG

    Entry point: User taps "Для бизнеса" button.
    Shows 6 business server tiers to choose from.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    await state.set_state(PurchaseState.choose_biz_tier)
    await state.update_data(purchase_id=None, tariff_type=None, period_days=None)

    text = i18n_get_text(language, "buy.biz_screen_title")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "buy.biz_starter_btn"), callback_data="tariff:biz_starter")],
        [InlineKeyboardButton(text=i18n_get_text(language, "buy.biz_team_btn"), callback_data="tariff:biz_team")],
        [InlineKeyboardButton(text=i18n_get_text(language, "buy.biz_business_btn"), callback_data="tariff:biz_business")],
        [InlineKeyboardButton(text=i18n_get_text(language, "buy.biz_pro_btn"), callback_data="tariff:biz_pro")],
        [InlineKeyboardButton(text=i18n_get_text(language, "buy.biz_enterprise_btn"), callback_data="tariff:biz_enterprise")],
        [InlineKeyboardButton(text=i18n_get_text(language, "buy.biz_ultimate_btn"), callback_data="tariff:biz_ultimate")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_buy_vpn")],
    ])

    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    logger.debug(f"Business catalog shown for user {telegram_id}")


@router.callback_query(
    F.data.startswith("biz_country:"),
    StateFilter(PurchaseState.choose_country),
)
async def callback_biz_country_selected(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 3 (бизнес) — После выбора страны → показать периоды с ценами для этой страны."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not validate_callback_data(callback.data):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    country_code = callback.data.split(":")[1]
    if country_code not in config.BIZ_COUNTRIES:
        await callback.answer("Invalid country", show_alert=True)
        return

    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    if not tariff_type or tariff_type not in config.TARIFFS:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        return

    await state.update_data(country=country_code)
    await state.set_state(PurchaseState.choose_period)

    country_info = config.BIZ_COUNTRIES[country_code]
    text = i18n_get_text(language, f"buy.tariff_{tariff_type}_desc")
    text += f"\n\n{country_info['flag']} Регион: {country_info['name']}"

    buttons = []
    periods = config.TARIFFS[tariff_type]
    for period_days in periods:
        price = config.get_biz_price(tariff_type, period_days, country_code)

        if period_days == 730:
            period_text = i18n_get_text(language, "buy.period_24_months")
        else:
            months = period_days // 30
            if months == 1:
                period_text = i18n_get_text(language, "buy.period_1")
            elif months in [2, 3, 4]:
                period_text = i18n_get_text(language, "buy.period_2_4", months=months)
            else:
                period_text = i18n_get_text(language, "buy.period_5_plus", months=months)

        badge = _period_badge(period_days)
        button_text = f"{price:,} ₽ — {period_text}".replace(",", " ")
        if badge:
            button_text = f"{button_text} {badge}"
        buttons.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"period:{tariff_type}:{period_days}"
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=f"tariff:{tariff_type}"
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)


# Здесь был обработчик callback_corporate_access_confirm (callback_data
# "corporate_access_confirm"): он слал админу заявку на корпоративный доступ и
# показывал пользователю «запрос принят».
#
# Удалён как недостижимый. Достижимости не было по двум независимым причинам:
#   1) ни одна клавиатура во всём репозитории (включая dashboard/ на TypeScript)
#      не создаёт кнопку с callback_data "corporate_access_confirm";
#   2) обработчик стоял под StateFilter(CorporateAccessRequest.waiting_for_confirmation),
#      а это состояние нигде не выставляется — класс CorporateAccessRequest
#      встречался только в определении в states.py и в самом фильтре.
#
# Живой корпоративный сценарий сейчас другой: кнопка "corporate_access_request"
# → callback_corporate_access_request (каталог бизнес-тарифов) → "tariff:biz_*"
# → выбор страны → обычная оплата. Заявка админу в нём не нужна.
#
# Если сценарий «оставить заявку» понадобится снова: текст ответа лежит в
# ключе buy.corporate_request_accepted (все семь языков), тип уведомления
# админу — "corporate_access_request" в admin_notifications.

