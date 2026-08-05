"""Управление подпиской и смена тарифного плана.

ЧТО ЗДЕСЬ
    Три экрана: «Управление подпиской» (для тех, у кого подписка уже
    есть), список тарифов на смену и карточка нового тарифа с периодами.

ПОЧЕМУ ВЫДЕЛЕНО
    Это ветка для ДЕЙСТВУЮЩЕГО подписчика. Соседний purchase_flow.py —
    обычная покупка с нуля; смешивать их в одном файле мешало обоим.

ЧТО ЛЕГКО СЛОМАТЬ
    Первый экран разветвляется: без подписки, с триалом или bypass-only
    человек должен попасть на обычный экран тарифов, а не на «Управление».
    Уберёте проверку — новичок увидит «Продлить», не имея что продлевать.

    Цены на карточке нового тарифа считаются тем же
    calculate_price, что и на обычной покупке. Если оставить здесь цену
    из конфига, человек увидит одну сумму, а заплатит другую.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.screens import _open_buy_screen
from app.handlers.common.utils import safe_edit_text, get_promo_session
from app.handlers.common.states import PurchaseState
from app.handlers.payments.callbacks.tariff_meta import (
    _TARIFF_META,
    _current_tariff_key,
    _period_badge,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "menu_buy_vpn")
async def callback_buy_vpn(callback: CallbackQuery, state: FSMContext):
    """Управление подпиской: продлить текущий / сменить тарифный план."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    sub = await database.get_subscription(telegram_id)
    current_key = _current_tariff_key(sub)

    # Пользователи без подписки, trial или bypass-only — стандартный экран тарифов
    is_bypass_only = bool(sub and sub.get("is_bypass_only"))
    if not sub or is_bypass_only or current_key not in _TARIFF_META:
        await _open_buy_screen(callback, callback.bot, state)
        return

    try:
        await callback.answer()
    except Exception:
        pass

    meta = _TARIFF_META[current_key]

    text = (
        f"📦 <b>Управление подпиской</b>\n\n"
        f"Ваш текущий тариф:\n\n"
        f"{i18n_get_text(language, meta['desc_key'])}\n\n"
        f"Выберите действие:"
    )

    # Кнопка продления текущего тарифа
    if current_key.startswith("combo_"):
        renew_cb = f"combo_tariff:{current_key}"
    else:
        renew_cb = f"tariff:{current_key}"

    buttons = [
        [InlineKeyboardButton(
            text=f"🔄 Продлить {meta['name']}",
            callback_data=renew_cb,
        )],
        [InlineKeyboardButton(
            text="📦 Сменить тарифный план",
            callback_data="switch_tariff_menu",
        )],
        [InlineKeyboardButton(
            text="Купить ГБ обхода",
            callback_data="buy_traffic",
            icon_custom_emoji_id="5199785165735367039",  # ⚡️
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_profile",
        )],
    ]

    await state.update_data(purchase_id=None, tariff_type=None, period_days=None)
    await state.set_state(PurchaseState.choose_tariff)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(
    F.data == "switch_tariff_menu",
    StateFilter(PurchaseState.choose_tariff, PurchaseState.choose_period, default_state),
)
async def callback_switch_tariff_menu(callback: CallbackQuery, state: FSMContext):
    """Меню смены тарифа — показываем все доступные тарифы кроме текущего."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    sub = await database.get_subscription(telegram_id)
    current_key = _current_tariff_key(sub)

    text = (
        "📦 <b>Сменить тарифный план</b>\n\n"
        "Новый тариф начнёт действовать после окончания текущей подписки.\n\n"
        "Доступные тарифы:"
    )

    buttons = []
    for key, meta in _TARIFF_META.items():
        if key == current_key:
            continue
        buttons.append([InlineKeyboardButton(
            text=f"{meta['icon']} {meta['name']}",
            callback_data=f"switch_tariff:{key}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_buy_vpn",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(
    F.data.startswith("switch_tariff:"),
    StateFilter(PurchaseState.choose_tariff, PurchaseState.choose_period, default_state),
)
async def callback_switch_tariff(callback: CallbackQuery, state: FSMContext):
    """Экран нового тарифа с описанием и выбором периода."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    new_tariff = callback.data.split(":")[1]
    if new_tariff not in _TARIFF_META:
        return

    meta = _TARIFF_META[new_tariff]
    is_combo = new_tariff.startswith("combo_")

    desc_text = i18n_get_text(language, meta['desc_key'])

    if is_combo:
        # Для комбо — показываем преимущества комбо подписки
        combo_benefits = (
            "\n\n💡 <b>Преимущества комбо:</b>\n"
            "✅ Трафик обхода уже включён в стоимость\n"
            "✅ Не нужно покупать ГБ отдельно\n"
            "✅ Экономия до 30% по сравнению с раздельной покупкой"
        )
        text = (
            f"{meta['icon']} <b>Переход на {meta['name']}</b>\n\n"
            f"{desc_text}"
            f"{combo_benefits}\n\n"
            f"Новый тариф начнёт действовать после окончания текущей подписки.\n"
            f"Выберите период:"
        )
    else:
        text = (
            f"{meta['icon']} <b>Переход на {meta['name']}</b>\n\n"
            f"{desc_text}\n\n"
            f"Новый тариф начнёт действовать после окончания текущей подписки.\n"
            f"Выберите период:"
        )

    buttons = []

    if is_combo:
        # Комбо-тариф: берём цены из COMBO_TARIFFS + применяем цепочку скидок
        tariff_data = config.COMBO_TARIFFS.get(new_tariff, {})
        period_keys = {30: "combo.period_1", 90: "combo.period_3", 180: "combo.period_6", 365: "combo.period_12", 730: "combo.period_24"}
        promo_session = await get_promo_session(state)
        promo_code = promo_session.get("promo_code") if promo_session else None
        for period_days, info in tariff_data.items():
            try:
                price_info = await subscription_service.calculate_price(
                    telegram_id=telegram_id,
                    tariff=info["base_tariff"],
                    period_days=period_days,
                    promo_code=promo_code,
                    base_price_override_rubles=info["price"],
                )
                price_rub = price_info["final_price_kopecks"] // 100
            except Exception:
                price_rub = info["price"]
            btn_text = i18n_get_text(language, period_keys.get(period_days, "combo.period_1"), gb=info["gb"], price=price_rub)
            buttons.append([InlineKeyboardButton(
                text=btn_text,
                callback_data=f"combo_period:{new_tariff}:{period_days}",
            )])
    else:
        # Обычный тариф: берём цены из TARIFFS + calculate_price
        promo_session = await get_promo_session(state)
        promo_code = promo_session.get("promo_code") if promo_session else None

        await state.update_data(tariff_type=new_tariff, purchase_id=None, period_days=None)
        await state.set_state(PurchaseState.choose_period)

        periods = config.TARIFFS.get(new_tariff, {})
        for period_days, period_data in periods.items():
            try:
                price_info = await subscription_service.calculate_price(
                    telegram_id=telegram_id,
                    tariff=new_tariff,
                    period_days=period_days,
                    promo_code=promo_code
                )
            except Exception as e:
                # Период молча пропадал с экрана покупки: пользователь видел
                # не весь список тарифов и не понимал, куда делся годовой.
                # Пропускать всё равно приходится — показать кнопку без цены
                # нельзя, — но теперь это видно в логах и разбирается.
                logger.error(
                    "PRICE_CALC_FAILED tariff=%s period_days=%s user=%s: %s — "
                    "период не показан на экране покупки",
                    new_tariff, period_days, telegram_id, e,
                )
                continue

            base_price_rubles = price_info["base_price_kopecks"] / 100.0
            final_price_rubles = price_info["final_price_kopecks"] / 100.0
            has_discount = price_info["discount_percent"] > 0

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

            price_int = int(final_price_rubles)
            badge = _period_badge(period_days)

            if has_discount:
                if badge:
                    button_text = i18n_get_text(
                        language, "buy.button_price_discount_badge",
                        base=int(base_price_rubles), final=price_int, period=period_text, badge=badge,
                    )
                else:
                    button_text = i18n_get_text(
                        language, "buy.button_price_discount",
                        base=int(base_price_rubles), final=price_int, period=period_text,
                    )
            else:
                if badge:
                    button_text = i18n_get_text(
                        language, "buy.button_price_badge",
                        price=price_int, period=period_text, badge=badge,
                    )
                else:
                    button_text = i18n_get_text(
                        language, "buy.button_price",
                        price=price_int, period=period_text,
                    )

            buttons.append([InlineKeyboardButton(
                text=button_text,
                callback_data=f"period:{new_tariff}:{period_days}"
            )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="switch_tariff_menu"
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")
