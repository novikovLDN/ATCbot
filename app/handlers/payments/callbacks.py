"""
Payment-related callback handlers: buy, tariff selection, payment methods.
"""
import logging
import time

from aiogram import Router, F, Bot
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
from app.handlers.common.screens import _open_buy_screen, show_tariffs_main_screen
from handlers import show_payment_method_selection
from app.handlers.common.utils import (
    safe_edit_text,
    get_promo_session,
    validate_callback_data,
    sanitize_display_name,
)
from app.handlers.common.keyboards import (
    get_connect_keyboard,
)
from app.handlers.common.states import PromoCodeInput, CorporateAccessRequest, PurchaseState
from app.core.structured_logger import log_event
from app.handlers.notifications import send_referral_cashback_notification

payments_callbacks_router = Router()
logger = logging.getLogger(__name__)



_TARIFF_META = {
    "basic":       {"icon": "⚡️", "name": "Basic",       "desc_key": "buy.tariff_basic_desc"},
    "plus":        {"icon": "👑", "name": "Plus",        "desc_key": "buy.tariff_plus_desc"},
    "combo_basic": {"icon": "🚀", "name": "Комбо Basic", "desc_key": "combo.tariff_basic"},
    "combo_plus":  {"icon": "🚀", "name": "Комбо Plus",  "desc_key": "combo.tariff_plus"},
}

def _current_tariff_key(sub) -> str:
    """Determine effective tariff key including combo flag."""
    if not sub:
        return ""
    sub_type = (sub.get("subscription_type") or "basic").strip().lower()
    is_combo = sub.get("is_combo", False)
    if is_combo:
        return f"combo_{sub_type}"  # combo_basic / combo_plus
    return sub_type  # basic / plus


@payments_callbacks_router.callback_query(F.data == "menu_buy_vpn")
async def callback_buy_vpn(callback: CallbackQuery, state: FSMContext):
    """Управление подпиской: продлить текущий / сменить тарифный план."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    sub = await database.get_subscription(telegram_id)
    current_key = _current_tariff_key(sub)

    # Пользователи без подписки или trial — стандартный экран тарифов
    if not sub or current_key not in _TARIFF_META:
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
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_profile",
        )],
    ]

    await state.update_data(purchase_id=None, tariff_type=None, period_days=None)
    await state.set_state(PurchaseState.choose_tariff)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@payments_callbacks_router.callback_query(
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


@payments_callbacks_router.callback_query(
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

    text = (
        f"{meta['icon']} <b>Переход на {meta['name']}</b>\n\n"
        f"{i18n_get_text(language, meta['desc_key'])}\n\n"
        f"Новый тариф начнёт действовать после окончания текущей подписки.\n"
        f"Выберите период:"
    )

    buttons = []

    if is_combo:
        # Комбо-тариф: берём цены из COMBO_TARIFFS
        tariff_data = config.COMBO_TARIFFS.get(new_tariff, {})
        period_keys = {30: "combo.period_1", 90: "combo.period_3", 180: "combo.period_6", 365: "combo.period_12"}
        for period_days, info in tariff_data.items():
            btn_text = i18n_get_text(language, period_keys.get(period_days, "combo.period_1"), gb=info["gb"], price=info["price"])
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
            except Exception:
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

            if has_discount:
                button_text = i18n_get_text(
                    language, "buy.button_price_discount",
                    base=int(base_price_rubles), final=int(final_price_rubles), period=period_text
                )
            else:
                button_text = i18n_get_text(
                    language, "buy.button_price",
                    price=int(final_price_rubles), period=period_text
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


@payments_callbacks_router.callback_query(
    F.data.startswith("tariff:"),
    StateFilter(PurchaseState.choose_tariff, PurchaseState.choose_biz_tier, PurchaseState.choose_period, default_state),
)
async def callback_tariff_type(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 1 — Выбор тарифа (Basic/Plus)
    
    КРИТИЧНО:
    - НЕ создает pending_purchase
    - Только сохраняет tariff_type в FSM
    - Переводит в choose_period
    - Показывает экран выбора периода
    """
    try:
        await callback.answer()
    except Exception:
        pass

    if not validate_callback_data(callback.data):
        logger.warning(
            "Invalid callback_data from user %s: %s",
            callback.from_user.id,
            (callback.data or "")[:50],
        )
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # CRITICAL FIX: Очищаем PromoCodeInput state при переходе к выбору тарифа
    # Это закрывает ввод промокода если пользователь был в этом состоянии
    current_state = await state.get_state()
    if current_state == PromoCodeInput.waiting_for_promo.state:
        await state.set_state(None)
        current_state = None
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_tariff, choose_period (назад) или None
    valid_states = (PurchaseState.choose_tariff.state, PurchaseState.choose_biz_tier.state, PurchaseState.choose_period.state, None)
    if current_state not in valid_states:
        log_event(
            logger,
            component="payments",
            operation="fsm_transition",
            outcome="failed",
            reason="invalid_state_for_tariff",
            correlation_id=str(telegram_id),
            level="warning",
        )
        await state.clear()
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        await show_tariffs_main_screen(callback, state)
        return
    
    # Парсим callback_data безопасно (формат: "tariff:basic" или "tariff:plus")
    try:
        parts = callback.data.split(":")
        if len(parts) < 2:
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
            return
        tariff_type = parts[1]  # "basic" или "plus"
    except (IndexError, ValueError) as e:
        logger.error(f"Invalid tariff callback_data: {callback.data}, error={e}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    # Валидация тарифа
    if tariff_type not in config.TARIFFS:
        logger.error(f"Invalid tariff_type: {tariff_type}")
        user = await database.get_user(callback.from_user.id)
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return
    
    log_event(
        logger,
        component="payments",
        operation="fsm_transition",
        outcome="success",
        reason="tariff_selected",
        correlation_id=str(telegram_id),
    )
    # КРИТИЧНО: Сохраняем tariff_type в FSM state
    # Промо-сессия НЕ сбрасывается при выборе тарифа - она независима от покупки
    await state.update_data(tariff_type=tariff_type)
    
    # КРИТИЧНО: Получаем промо-сессию (проверяет срок действия автоматически)
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    # КРИТИЧНО: НЕ создаем pending_purchase - только показываем кнопки периодов
    # Для бизнес-тарифов → сначала выбор страны
    if config.is_biz_tariff(tariff_type):
        await state.set_state(PurchaseState.choose_country)
        await state.update_data(tariff_type=tariff_type)
        text = i18n_get_text(language, f"buy.tariff_{tariff_type}_desc")
        text += "\n\n" + i18n_get_text(language, "buy.choose_country")
        buttons = []
        for code, info in config.BIZ_COUNTRIES.items():
            price = config.get_biz_price(tariff_type, 30, code)
            btn_text = f"{info['flag']} {info['name']} · от {price:,} ₽/мес".replace(",", " ")
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"biz_country:{code}")])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="corporate_access_request"
        )])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        return

    # Определяем описание тарифа в зависимости от типа
    if tariff_type == "basic":
        text = i18n_get_text(language, "buy.tariff_basic_desc")
    else:
        text = i18n_get_text(language, "buy.tariff_plus_desc")

    buttons = []

    # Получаем цены для выбранного тарифа с учетом скидок
    periods = config.TARIFFS[tariff_type]
    
    # КРИТИЧНО: Логируем контекст промо-сессии для диагностики
    if promo_session:
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        logger.info(
            f"Price calculation with promo session: user={telegram_id}, tariff={tariff_type}, "
            f"promo_code={promo_code}, discount={promo_session.get('discount_percent')}%, "
            f"expires_in={expires_in}s"
        )
    
    for period_days, period_data in periods.items():
        # КРИТИЧНО: Используем ЕДИНУЮ функцию расчета цены для отображения
        try:
            price_info = await subscription_service.calculate_price(
                telegram_id=telegram_id,
                tariff=tariff_type,
                period_days=period_days,
                promo_code=promo_code
            )
        except (subscription_service.InvalidTariffError, subscription_service.PriceCalculationError) as e:
            logger.error(f"Error calculating price: tariff={tariff_type}, period={period_days}, error={e}")
            continue  # Пропускаем этот период если ошибка расчета
        
        base_price_rubles = price_info["base_price_kopecks"] / 100.0
        final_price_rubles = price_info["final_price_kopecks"] / 100.0
        has_discount = price_info["discount_percent"] > 0
        
        # КРИТИЧНО: Логируем расчет цены для диагностики
        logger.debug(
            f"Price recalculated: tariff={tariff_type}, period={period_days}, "
            f"base={price_info['base_price_kopecks']}, discount={price_info['discount_percent']}%, "
            f"final={price_info['final_price_kopecks']}, promo_code={promo_code or 'none'}"
        )
        
        # Формируем правильное склонение периода
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
        
        # Формируем текст кнопки с зачеркнутой ценой (если есть скидка)
        if has_discount:
            button_text = i18n_get_text(
                language, "buy.button_price_discount",
                base=int(base_price_rubles), final=int(final_price_rubles), period=period_text
            )
        else:
            button_text = i18n_get_text(
                language, "buy.button_price",
                price=int(final_price_rubles), period=period_text
            )
        
        # КРИТИЧНО: callback_data БЕЗ purchase_id - только tariff и period
        buttons.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"period:{tariff_type}:{period_days}"
        )])
    
    # Кнопка назад: для бизнес-тарифов → каталог бизнес, для обычных → главный экран тарифов
    back_callback = "corporate_access_request" if config.is_biz_tariff(tariff_type) else "menu_buy_vpn"
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=back_callback
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    
    # КРИТИЧНО: Переходим в состояние choose_period
    await state.set_state(PurchaseState.choose_period)


@payments_callbacks_router.callback_query(
    F.data.startswith("period:"),
    StateFilter(PurchaseState.choose_period),
)
async def callback_tariff_period(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 2 — Выбор периода тарифа
    
    КРИТИЧНО:
    - НЕ создает pending_purchase
    - НЕ создает invoice
    - Только сохраняет period_days и final_price_kopecks в FSM
    - Переводит в choose_payment_method
    - Открывает экран выбора способа оплаты
    """
    try:
        await callback.answer()
    except Exception:
        pass

    if not validate_callback_data(callback.data):
        logger.warning(
            "Invalid callback_data from user %s: %s",
            callback.from_user.id,
            (callback.data or "")[:50],
        )
        return

    telegram_id = callback.from_user.id
    
    # CRITICAL FIX: Очищаем PromoCodeInput state при переходе к выбору периода
    # Это закрывает ввод промокода если пользователь был в этом состоянии
    current_state = await state.get_state()
    if current_state == PromoCodeInput.waiting_for_promo.state:
        await state.set_state(None)
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Парсим callback_data безопасно (формат: "period:basic:30")
    try:
        parts = callback.data.split(":")
        if len(parts) < 3:
            error_text = i18n_get_text(language, "errors.tariff")
            await callback.answer(error_text, show_alert=True)
            logger.error(f"Invalid period callback_data format: {callback.data}")
            return
        
        tariff_type = parts[1]  # "basic" или "plus"
        period_days = int(parts[2])
    except (IndexError, ValueError) as e:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid period callback_data: {callback.data}, error={e}")
        return
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_period
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_period.state:
        log_event(
            logger,
            component="payments",
            operation="fsm_transition",
            outcome="failed",
            reason="invalid_state_for_period",
            correlation_id=str(telegram_id),
            level="warning",
        )
        await state.clear()
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        await show_tariffs_main_screen(callback, state)
        return
    
    # Валидация тарифа и периода
    if tariff_type not in config.TARIFFS:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid tariff_type: {tariff_type}")
        return
    
    if period_days not in config.TARIFFS[tariff_type]:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid period_days: {period_days} for tariff {tariff_type}")
        return
    
    # КРИТИЧНО: Проверяем, что tariff_type в FSM соответствует выбранному
    fsm_data = await state.get_data()
    stored_tariff = fsm_data.get("tariff_type")
    if stored_tariff != tariff_type:
        logger.warning(f"Tariff mismatch: FSM={stored_tariff}, callback={tariff_type}, user={telegram_id}")
        # Обновляем tariff_type в FSM
        await state.update_data(tariff_type=tariff_type)
    
    # КРИТИЧНО: Получаем промо-сессию (проверяет срок действия автоматически)
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    # КРИТИЧНО: Логируем контекст промо-сессии для диагностики
    if promo_session:
        expires_at = promo_session.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time()))
        discount_percent = promo_session.get("discount_percent", 0)
        logger.info(
            f"Period selection with promo session: user={telegram_id}, tariff={tariff_type}, "
            f"period={period_days}, promo_code={promo_code}, discount={discount_percent}%, "
            f"expires_in={expires_in}s"
        )
    
    # Для бизнес-тарифов берём страну из FSM
    country = fsm_data.get("country") if config.is_biz_tariff(tariff_type) else None

    # КРИТИЧНО: Используем ЕДИНУЮ функцию расчета цены
    try:
        price_info = await subscription_service.calculate_price(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            promo_code=promo_code,
            country=country
        )
    except (subscription_service.InvalidTariffError, subscription_service.PriceCalculationError) as e:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Invalid tariff/period in calculate_price: user={telegram_id}, tariff={tariff_type}, period={period_days}, error={e}")
        return
    
    # Plus→Basic downgrade: show confirmation before proceeding
    if tariff_type == "basic":
        sub = await database.get_subscription(telegram_id)
        current_sub_type = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
        if sub and current_sub_type == "plus":
            await state.update_data(
                tariff_type=tariff_type,
                period_days=period_days,
                final_price_kopecks=price_info["final_price_kopecks"],
                discount_percent=price_info["discount_percent"]
            )
            downgrade_text = (
                "⚠️ Вы переходите с Plus на Basic.\n\n"
                "Ключ будет ротирован с выделенного сервера на базовый.\n\n"
                "Подтвердить переход?"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚡️ Да, перейти на Basic", callback_data="downgrade_confirm_basic")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="tariff:basic")]
            ])
            await safe_edit_text(callback.message, downgrade_text, reply_markup=keyboard)
            return
    
    # КРИТИЧНО: Сохраняем данные в FSM state (БЕЗ создания pending_purchase)
    # Промо-сессия НЕ сохраняется здесь - она уже в FSM и независима от покупки
    await state.update_data(
        tariff_type=tariff_type,
        period_days=period_days,
        final_price_kopecks=price_info["final_price_kopecks"],
        discount_percent=price_info["discount_percent"]
    )
    
    log_event(
        logger,
        component="payments",
        operation="fsm_transition",
        outcome="success",
        reason="period_selected",
        correlation_id=str(telegram_id),
    )
    logger.info(
        f"Period selected: user={telegram_id}, tariff={tariff_type}, period={period_days}, "
        f"base_price_kopecks={price_info['base_price_kopecks']}, final_price_kopecks={price_info['final_price_kopecks']}, "
        f"discount_percent={price_info['discount_percent']}%, discount_type={price_info['discount_type']}, "
        f"promo_code={promo_code or 'none'}"
    )
    
    # КРИТИЧНО: Переходим к выбору способа оплаты (НЕ создаем pending_purchase и invoice)
    await state.set_state(PurchaseState.choose_payment_method)
    await show_payment_method_selection(callback, tariff_type, period_days, price_info["final_price_kopecks"])


@payments_callbacks_router.callback_query(
    F.data == "downgrade_confirm_basic",
    StateFilter(PurchaseState.choose_period),
)
async def callback_downgrade_confirm_basic(callback: CallbackQuery, state: FSMContext):
    """Подтверждение перехода Plus→Basic: продолжаем поток оплаты Basic."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type", "basic")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    if period_days is None or final_price_kopecks is None:
        error_text = i18n_get_text(language, "errors.session_expired")
        try:
            await callback.answer(error_text, show_alert=True)
        except Exception:
            pass
        await show_tariffs_main_screen(callback, state)
        return
    await state.update_data(confirmed_downgrade=True)
    await state.set_state(PurchaseState.choose_payment_method)
    await show_payment_method_selection(callback, tariff_type, period_days, final_price_kopecks)


@payments_callbacks_router.callback_query(F.data == "enter_promo")
async def callback_enter_promo(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки ввода промокода"""
    try:
        await callback.answer()
    except Exception:
        pass

    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем активную промо-сессию
    promo_session = await get_promo_session(state)
    if promo_session:
        # Промокод уже применён - показываем сообщение
        text = i18n_get_text(language, "buy.promo_applied")
        await callback.message.answer(text)
        return

    # CRITICAL FIX: Очищаем предыдущие FSM состояния перед установкой нового
    # Это гарантирует, что пользователь не останется в "зависшем" состоянии
    await state.set_state(None)
    
    # Устанавливаем состояние ожидания промокода
    await state.set_state(PromoCodeInput.waiting_for_promo)

    text = i18n_get_text(language, "buy.enter_promo_text")
    await callback.message.answer(text)


@payments_callbacks_router.callback_query(F.data == "promo_back")
async def callback_promo_back(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Назад' при ошибке промокода - возвращает на экран выбора тарифа"""
    # CRITICAL FIX: Очищаем FSM state при выходе с экрана ввода промокода
    await state.clear()
    
    # CRITICAL FIX: Используем каноничный экран тарифов вместо локального render
    await show_tariffs_main_screen(callback, state)


# Старый обработчик tariff_* удалён - теперь используется новый флоу tariff_type -> tariff_period







@payments_callbacks_router.callback_query(F.data == "corporate_access_request")
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


@payments_callbacks_router.callback_query(
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

        button_text = f"{price:,} ₽ — {period_text}".replace(",", " ")
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


@payments_callbacks_router.callback_query(F.data == "corporate_access_confirm", StateFilter(CorporateAccessRequest.waiting_for_confirmation))
async def callback_corporate_access_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    🧩 CORPORATE ACCESS REQUEST FLOW
    
    On confirmation: Send admin notification and user confirmation.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    user = await database.get_user(telegram_id)

    try:
        # Get user data with sanitization
        raw_username = callback.from_user.username if callback.from_user else None
        if raw_username:
            sanitized = sanitize_display_name(raw_username)
            username_display = f"@{sanitized}" if sanitized else i18n_get_text(language, "common.user")
        else:
            username_display = i18n_get_text(language, "common.username_not_set")
        
        # Get subscription status
        subscription = await database.get_subscription(telegram_id)
        has_active_subscription = False
        if subscription:
            from app.services.subscriptions.service import get_subscription_status
            subscription_status = get_subscription_status(subscription)
            has_active_subscription = subscription_status.is_active
        
        subscription_status_text = "ДА" if has_active_subscription else "НЕТ"
        
        # Get registration date
        registration_date = "N/A"
        if user and user.get("created_at"):
            if isinstance(user["created_at"], str):
                from datetime import datetime
                registration_date = datetime.fromisoformat(user["created_at"]).strftime("%d.%m.%Y")
            else:
                registration_date = user["created_at"].strftime("%d.%m.%Y")
        
        # Current date
        from datetime import datetime, timezone
        request_date = datetime.now(timezone.utc).strftime("%d.%m.%Y")
        
        # Send admin notification using unified service
        import admin_notifications
        admin_message = (
            f"📩 Новый запрос на корпоративный доступ\n\n"
            f"ID: {telegram_id}\n"
            f"Username: {username_display}\n"
            f"Дата запроса: {request_date}\n\n"
            f"Активная подписка: {subscription_status_text}\n"
            f"Дата регистрации в боте: {registration_date}"
        )
        
        admin_notified = await admin_notifications.send_admin_notification(
            bot=bot,
            message=admin_message,
            notification_type="corporate_access_request",
            parse_mode=None
        )
        
        # Send user confirmation message
        user_confirmation_text = i18n_get_text(language, "buy.corporate_request_accepted")

        user_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.profile"),
                callback_data="menu_profile"
            )],
        ])

        await callback.message.answer(user_confirmation_text, reply_markup=user_keyboard)
        
        # Write audit log
        try:
            await database._log_audit_event_atomic_standalone(
                "corporate_access_request",
                telegram_id,
                None,
                f"Corporate access request: username={username_display}, has_active_subscription={has_active_subscription}, admin_notified={admin_notified}, requested_at={request_date}"
            )
        except Exception as e:
            logger.error(f"Failed to write audit log for corporate access request: {e}")
        
        # Clear FSM
        await state.clear()
        logger.debug(f"FSM: CorporateAccessRequest cleared after confirmation for user {telegram_id}")
        
    except Exception as e:
        logger.exception(f"Error in callback_corporate_access_confirm: {e}")
        # Still confirm user even if admin notification fails
        try:
            user_confirmation_text = i18n_get_text(language, "buy.corporate_request_accepted")
            await callback.message.answer(user_confirmation_text)
        except Exception:
            pass
        await state.clear()
        await callback.answer(i18n_get_text(language, "buy.corporate_request_accepted").split("\n")[0], show_alert=True)
