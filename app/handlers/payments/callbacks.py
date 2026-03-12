"""
Payment-related callback handlers: buy, tariff selection, payment methods, admin payment approval.
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
    safe_edit_reply_markup,
    get_promo_session,
    validate_callback_data,
)
from app.handlers.common.keyboards import get_connect_keyboard
from app.handlers.common.states import PromoCodeInput, CorporateAccessRequest, PurchaseState
from app.core.structured_logger import log_event

payments_callbacks_router = Router()
logger = logging.getLogger(__name__)



@payments_callbacks_router.callback_query(F.data == "menu_buy_vpn")
async def callback_buy_vpn(callback: CallbackQuery, state: FSMContext):
    """Купить VPN - выбор типа тарифа (Basic/Plus). Entry from inline button."""
    if not await ensure_db_ready_callback(callback):
        return
    await _open_buy_screen(callback, callback.bot, state)


@payments_callbacks_router.callback_query(
    F.data.startswith("tariff:"),
    StateFilter(PurchaseState.choose_tariff, PurchaseState.choose_period, default_state),
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
    valid_states = (PurchaseState.choose_tariff.state, PurchaseState.choose_period.state, None)
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
        
        months = period_days // 30
        
        # Формируем правильное склонение периода
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
    
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_buy_vpn"
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
    
    # КРИТИЧНО: Используем ЕДИНУЮ функцию расчета цены
    try:
        price_info = await subscription_service.calculate_price(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            promo_code=promo_code
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
                [InlineKeyboardButton(text="✅ Да, перейти на Basic", callback_data="downgrade_confirm_basic")],
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


@payments_callbacks_router.callback_query(F.data == "crypto_disabled")
async def callback_crypto_disabled(callback: CallbackQuery):
    """Обработчик неактивной кнопки крипты"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return


@payments_callbacks_router.callback_query(F.data == "promo_back")
async def callback_promo_back(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Назад' при ошибке промокода - возвращает на экран выбора тарифа"""
    # CRITICAL FIX: Очищаем FSM state при выходе с экрана ввода промокода
    await state.clear()
    
    # CRITICAL FIX: Используем каноничный экран тарифов вместо локального render
    await show_tariffs_main_screen(callback, state)


# Старый обработчик tariff_* удалён - теперь используется новый флоу tariff_type -> tariff_period


@payments_callbacks_router.callback_query(F.data == "payment_test")
async def callback_payment_test(callback: CallbackQuery):
    """Тестовая оплата (не работает)"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Тестовая оплата не работает - возвращаем назад
    await callback.answer(i18n_get_text(language, "errors.function_disabled"), show_alert=True)
    text = i18n_get_text(language, "main.select_payment", "select_payment")
    await safe_edit_text(callback.message, text, reply_markup=get_payment_method_keyboard(language))


@payments_callbacks_router.callback_query(F.data == "payment_sbp")
async def callback_payment_sbp(callback: CallbackQuery, state: FSMContext):
    """Оплата через СБП"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    data = await state.get_data()
    tariff_key = data.get("tariff_type", "basic")
    period_days = data.get("period_days", 30)

    if tariff_key not in config.TARIFFS:
        error_msg = f"Invalid tariff_key '{tariff_key}' for user {telegram_id}. Valid tariffs: {list(config.TARIFFS.keys())}"
        logger.error(error_msg)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    if period_days not in config.TARIFFS[tariff_key]:
        period_days = 30

    if period_days not in config.TARIFFS[tariff_key]:
        error_msg = f"Period {period_days} days not found in tariff '{tariff_key}' for user {telegram_id}"
        logger.error(error_msg)
        await callback.answer(i18n_get_text(language, "errors.tariff"), show_alert=True)
        return

    tariff_data = config.TARIFFS[tariff_key][period_days]
    base_price = tariff_data["price"]
    
    # Рассчитываем цену с учетом скидки (та же логика, что в create_payment)
    # ПРИОРИТЕТ 1: VIP-статус
    is_vip = await database.is_vip_user(telegram_id)
    
    if is_vip:
        amount = int(base_price * 0.70)  # 30% скидка
    else:
        # ПРИОРИТЕТ 2: Персональная скидка
        personal_discount = await database.get_user_discount(telegram_id)
        
        if personal_discount:
            discount_percent = personal_discount["discount_percent"]
            amount = int(base_price * (1 - discount_percent / 100))
        else:
            # Без скидки
            amount = base_price
    
    # Формируем текст с реквизитами
    text = i18n_get_text(language, "main.sbp_payment_text", amount=amount)
    
    await safe_edit_text(callback.message, text, reply_markup=get_sbp_payment_keyboard(language))


@payments_callbacks_router.callback_query(F.data == "payment_paid")
async def callback_payment_paid(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал 'Я оплатил'"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    data = await state.get_data()
    tariff_type = data.get("tariff_type", "basic")
    period_days = data.get("period_days", 30)
    tariff_key = f"{tariff_type}_{period_days}"

    # Проверяем наличие pending платежа перед созданием
    existing_payment = await database.get_pending_payment_by_user(telegram_id)
    if existing_payment:
        text = i18n_get_text(language, "payment.pending", "payment_pending")
        await safe_edit_text(callback.message, text, reply_markup=get_pending_payment_keyboard(language))
        await callback.answer(i18n_get_text(language, "errors.pending_payment_exists"), show_alert=True)
        await state.clear()
        return
    
    # Создаем платеж
    payment_id = await database.create_payment(telegram_id, tariff_key)
    
    if payment_id is None:
        # Это не должно произойти, так как мы проверили выше, но на всякий случай
        text = i18n_get_text(language, "payment.pending", "payment_pending")
        await safe_edit_text(callback.message, text, reply_markup=get_pending_payment_keyboard(language))
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.clear()
        return
    
    # Получаем данные платежа, чтобы показать реальную сумму администратору
    payment = await database.get_payment(payment_id)
    if payment:
        actual_amount = payment["amount"] / 100.0  # Конвертируем из копеек
    else:
        # Fallback: используем базовую цену тарифа basic 30 дней
        if "basic" in config.TARIFFS and 30 in config.TARIFFS["basic"]:
            actual_amount = config.TARIFFS["basic"][30]["price"]
        else:
            actual_amount = 149  # Дефолтная цена
    
    # Отправляем сообщение пользователю
    text = i18n_get_text(language, "payment.pending", "payment_pending")
    await safe_edit_text(callback.message, text, reply_markup=get_pending_payment_keyboard(language))
    
    # Safe username extraction: can be None
    user_lang = await resolve_user_language(telegram_id)
    username = (callback.from_user.username if callback.from_user else None) or i18n_get_text(user_lang, "common.username_not_set")

    # Admin notification: admin always sees Russian (ADMIN RU ALLOWED)
    admin_text = i18n_get_text(
        "ru",
        "admin.payment_notification",
        username=username,
        telegram_id=telegram_id,
        tariff=tariff_key,
        price=actual_amount
    )
    
    try:
        await callback.bot.send_message(
            config.ADMIN_TELEGRAM_ID,
            admin_text,
            reply_markup=get_admin_payment_keyboard(payment_id, "ru")
        )
    except Exception as e:
        logging.error(f"Error sending admin notification: {e}")
    
    await state.clear()


@payments_callbacks_router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery):
    """Админ подтвердил платеж"""
    try:
        await callback.answer()
    except Exception:
        pass

    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized approve attempt by user {callback.from_user.id}")
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(f"APPROVE pressed by admin {callback.from_user.id}, payment_id={payment_id}")
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment:
            logging.warning(f"Payment {payment_id} not found for approve")
            await callback.answer("Платеж не найден", show_alert=True)
            return
        
        if payment["status"] != "pending":
            logging.warning(
                f"Attempt to approve already processed payment {payment_id}, status={payment['status']}"
            )
            await callback.answer("Платеж уже обработан", show_alert=True)
            # Удаляем кнопки даже если платеж уже обработан
            await safe_edit_reply_markup(callback.message, reply_markup=None)
            return
        
        telegram_id = payment["telegram_id"]
        tariff_key = payment["tariff"]
        
        # Парсим tariff_key (формат: "basic_30" или "plus_90")
        if "_" in tariff_key:
            tariff_type, period_str = tariff_key.split("_", 1)
            try:
                period_days = int(period_str)
            except ValueError:
                logger.error(f"Invalid period in tariff_key '{tariff_key}' for payment {payment_id}")
                period_days = 30
        else:
            # Fallback: используем basic 30 дней
            tariff_type = "basic"
            period_days = 30
            logger.warning(f"Invalid tariff_key format '{tariff_key}', using fallback: basic_30")
        
        # Получаем данные тарифа
        if tariff_type in config.TARIFFS and period_days in config.TARIFFS[tariff_type]:
            tariff_data = config.TARIFFS[tariff_type][period_days]
        elif "basic" in config.TARIFFS and 30 in config.TARIFFS["basic"]:
            tariff_data = config.TARIFFS["basic"][30]
            logger.warning(f"Using fallback tariff 'basic' 30 days for tariff_key '{tariff_key}'")
        else:
            error_msg = f"CRITICAL: Cannot find valid tariff data for tariff_key '{tariff_key}'"
            logger.error(error_msg)
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.invalid_tariff"), show_alert=True)
            return
        
        # Атомарно подтверждаем платеж и создаем/продлеваем подписку
        # VPN-ключ создается через Xray API
        admin_telegram_id = callback.from_user.id
        # Пересчитываем months из period_days для совместимости со старой функцией
        months = period_days // 30
        result = await database.approve_payment_atomic(
            payment_id, 
            months,  # Используем пересчитанное значение из period_days
            admin_telegram_id,
            bot=callback.bot  # Передаём бот для отправки уведомлений рефереру
        )
        expires_at, is_renewal, vpn_key = result
        
        if expires_at is None or vpn_key is None:
            logging.error(f"Failed to approve payment {payment_id} atomically")
            user = await database.get_user(callback.from_user.id)
            language = await resolve_user_language(callback.from_user.id)
            await callback.answer(i18n_get_text(language, "errors.vpn_key_creation"), show_alert=True)
            return
        
        # E) PURCHASE FLOW: Send referral notification for admin-approved payments
        # process_referral_reward was called in approve_payment_atomic, get reward details
        if not is_renewal:
            try:
                # Get payment amount
                payment_row = await database.get_payment(payment_id)
                if payment_row:
                    payment_amount_rubles = (payment_row.get("amount", 0) or 0) / 100.0
                    
                    # Get referral reward from referral_rewards table
                    pool = await database.get_pool()
                    async with pool.acquire() as conn:
                        purchase_id_str = f"admin_approve_{payment_id}"
                        reward_row = await conn.fetchrow(
                            """SELECT referrer_id, percent, reward_amount, 
                               (SELECT COUNT(DISTINCT referred_user_id) FROM referrals 
                                WHERE referrer_user_id = referral_rewards.referrer_id 
                                AND first_paid_at IS NOT NULL) as paid_count
                               FROM referral_rewards 
                               WHERE buyer_id = $1 AND purchase_id = $2
                               ORDER BY id DESC LIMIT 1""",
                            telegram_id, purchase_id_str
                        )
                        
                        if reward_row:
                            referrer_id = reward_row.get("referrer_id")
                            cashback_percent = reward_row.get("percent", 0)
                            cashback_amount = (reward_row.get("reward_amount", 0) or 0) / 100.0
                            paid_referrals_count = reward_row.get("paid_count", 0) or 0
                            
                            # Calculate referrals needed
                            if paid_referrals_count < 25:
                                referrals_needed = 25 - paid_referrals_count
                            elif paid_referrals_count < 50:
                                referrals_needed = 50 - paid_referrals_count
                            else:
                                referrals_needed = 0
                            
                            # Format subscription period
                            subscription_period = f"{months} месяц" + ("а" if months in [2, 3, 4] else ("ев" if months > 4 else ""))
                            
                            # Send notification
                            notification_sent = await send_referral_cashback_notification(
                                bot=callback.bot,
                                referrer_id=referrer_id,
                                referred_id=telegram_id,
                                purchase_amount=payment_amount_rubles,
                                cashback_amount=cashback_amount,
                                cashback_percent=cashback_percent,
                                paid_referrals_count=paid_referrals_count,
                                referrals_needed=referrals_needed,
                                action_type="purchase",
                                subscription_period=subscription_period
                            )
                            if notification_sent:
                                logger.info(f"REFERRAL_NOTIFICATION_SENT [admin_approve, referrer={referrer_id}, referred={telegram_id}, payment_id={payment_id}]")
                            else:
                                logger.warning(
                                    "NOTIFICATION_FAILED",
                                    extra={
                                        "type": "admin_approve_referral",
                                        "referrer": referrer_id,
                                        "referred": telegram_id,
                                        "payment_id": payment_id,
                                        "error": "send_referral_cashback_notification returned False"
                                    }
                                )
            except Exception as e:
                logger.warning(
                    "NOTIFICATION_FAILED",
                    extra={
                        "type": "admin_approve_referral",
                        "payment_id": payment_id,
                        "referrer": referrer_id if 'referrer_id' in locals() else None,
                        "referred": telegram_id if 'telegram_id' in locals() else None,
                        "error": str(e)
                    }
                )
        
        # Логируем продление, если было
        if is_renewal:
            logging.info(f"Subscription renewed for user {telegram_id}, payment_id={payment_id}, expires_at={expires_at}")
        else:
            logging.info(f"New subscription created for user {telegram_id}, payment_id={payment_id}, expires_at={expires_at}")
        
        # Уведомляем пользователя: сообщение + кнопка «Подключиться» (Mini App)
        language = await resolve_user_language(telegram_id)
        expires_str = expires_at.strftime("%d.%m.%Y")
        tariff_label = "Plus" if tariff_type == "plus" else "Basic"
        if is_renewal:
            text = f"✅ Подписка продлена\n📦/⭐️ Тариф: {tariff_label}\n📅 До: {expires_str}"
        elif tariff_type == "plus":
            text = f"🎉 Добро пожаловать в Atlas Secure!\n⭐️ Тариф: Plus\n📅 До: {expires_str}"
        else:
            text = f"🎉 Добро пожаловать в Atlas Secure!\n📦 Тариф: Basic\n📅 До: {expires_str}"
        keyboard = get_connect_keyboard()
        try:
            await callback.bot.send_message(
                telegram_id,
                text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            logging.info(f"Approval message (connect button) sent to user {telegram_id} for payment {payment_id}")
        except Exception as e:
            logging.error(f"Error sending approval message to user {telegram_id}: {e}")
        
        await safe_edit_text(callback.message, f"✅ Платеж {payment_id} подтвержден")
        # Удаляем inline-кнопки после обработки
        await safe_edit_reply_markup(callback.message, reply_markup=None)
        
    except Exception as e:
        logging.exception(f"Error in approve_payment callback for payment_id={payment_id if 'payment_id' in locals() else 'unknown'}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@payments_callbacks_router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    """Админ отклонил платеж"""
    try:
        await callback.answer()
    except Exception:
        pass

    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        logging.warning(f"Unauthorized reject attempt by user {callback.from_user.id}")
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        payment_id = int(callback.data.split(":")[1])
        
        logging.info(f"REJECT pressed by admin {callback.from_user.id}, payment_id={payment_id}")
        
        # Получить платеж из БД
        payment = await database.get_payment(payment_id)
        
        if not payment:
            logging.warning(f"Payment {payment_id} not found for reject")
            await callback.answer("Платеж не найден", show_alert=True)
            return
        
        if payment["status"] != "pending":
            logging.warning(
                f"Attempt to reject already processed payment {payment_id}, status={payment['status']}"
            )
            await callback.answer("Платеж уже обработан", show_alert=True)
            # Удаляем кнопки даже если платеж уже обработан
            await safe_edit_reply_markup(callback.message, reply_markup=None)
            return
        
        telegram_id = payment["telegram_id"]
        admin_telegram_id = callback.from_user.id
        
        # Обновляем статус платежа на rejected (аудит записывается внутри функции)
        await database.update_payment_status(payment_id, "rejected", admin_telegram_id)
        logging.info(f"Payment {payment_id} rejected for user {telegram_id}")
        
        # Уведомляем пользователя
        language = await resolve_user_language(telegram_id)
        
        text = i18n_get_text(language, "payment.rejected", "payment_rejected")
        
        # Создаем inline клавиатуру для UX
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "buy.renew_button", "buy_renew_button"),
                callback_data="menu_buy_vpn"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.support_button", "support_button"),
                callback_data="menu_support"
            )]
        ])
        
        try:
            await callback.bot.send_message(telegram_id, text, reply_markup=keyboard)
            logging.info(f"Rejection message sent to user {telegram_id} for payment {payment_id}")
        except Exception as e:
            logging.error(f"Error sending rejection message to user {telegram_id}: {e}")
        
        await callback.message.edit_text(f"❌ Платеж {payment_id} отклонен")
        # Удаляем inline-кнопки после обработки
        await safe_edit_reply_markup(callback.message, reply_markup=None)
        
    except Exception as e:
        logging.exception(f"Error in reject_payment callback for payment_id={payment_id if 'payment_id' in locals() else 'unknown'}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@payments_callbacks_router.callback_query(F.data == "corporate_access_request")
async def callback_corporate_access_request(callback: CallbackQuery, state: FSMContext):
    """
    🧩 CORPORATE ACCESS REQUEST FLOW
    
    Entry point: User taps "Корпоративный доступ" button.
    Shows confirmation screen with consent text.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Set FSM state
    await state.set_state(CorporateAccessRequest.waiting_for_confirmation)
    
    # Show confirmation screen with consent text
    consent_text = i18n_get_text(language, "buy.corporate_consent")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.corporate_confirm"),
            callback_data="corporate_access_confirm"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.corporate_back"),
            callback_data="menu_buy_vpn"
        )],
    ])
    
    await safe_edit_text(callback.message, consent_text, reply_markup=keyboard)
    
    logger.debug(f"FSM: CorporateAccessRequest.waiting_for_confirmation set for user {telegram_id}")


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
        # Get user data (safe: username can be None)
        username = callback.from_user.username if callback.from_user else None
        username_display = f"@{username}" if username else i18n_get_text(language, "common.username_not_set")
        
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
