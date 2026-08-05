"""Покупка подписки: выбор тарифа → выбор периода → способ оплаты.

ЧТО ЗДЕСЬ
    Два основных шага покупки и подтверждение перехода Plus → Basic.

ПОЧЕМУ ВЫДЕЛЕНО
    Это главный денежный путь бота, и правится он чаще всего: цены,
    скидки, склонения периодов, кнопка «Назад».

ЧТО ЛЕГКО СЛОМАТЬ
    Здесь НЕ создаётся ни покупка, ни счёт — только состояние в FSM. Так
    и задумано: покупку создаёт экран способа оплаты, и создание её
    раньше приведёт к висящим неоплаченным записям на каждый клик.

    Проверки состояния FSM на каждом шаге. Кнопка из старого сообщения
    живёт в чате вечно; без проверки человек прыгнет в середину покупки
    со старой ценой.

    Кнопка «Назад» зависит от того, пришёл ли человек из рассылки
    (маркер from_broadcast). Потеряете ветку — из акции он уедет в
    «Управление подпиской» и акцию потеряет.

    Период, у которого не посчиталась цена, пропадает с экрана. Это
    осознанно (кнопка без цены хуже), но обязано попадать в лог: иначе
    «куда делся годовой тариф» не разобрать.
"""
import logging
import time

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
from app.handlers.common.screens import show_tariffs_main_screen
from app.handlers.payments.method_select import show_payment_method_selection
from app.handlers.common.utils import (
    safe_edit_text,
    get_promo_session,
    validate_callback_data,
)
from app.handlers.common.states import PromoCodeInput, PurchaseState
from app.core.structured_logger import log_event
from app.handlers.payments.callbacks.tariff_meta import _period_badge

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(
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

        # Admin-managed global-discount (migration 069): если оригинал
        # из config выше базы после нашего override — покажем страйк
        # от оригинала (юзеру видно «199 → 149»). Даже если у него
        # промо-кода нет.
        _orig_kop = price_info.get("original_config_price_kopecks")
        if _orig_kop and int(_orig_kop) > price_info["base_price_kopecks"]:
            base_price_rubles = int(_orig_kop) / 100.0
            has_discount = True

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
        
        # Traffic GB for this period
        traffic_gb = config.TRAFFIC_LIMITS_GB.get(tariff_type, {}).get(period_days, 0)

        badge = _period_badge(period_days)

        # Формируем текст кнопки с зачеркнутой ценой (если есть скидка)
        if has_discount:
            key = "buy.button_price_discount_badge" if badge else "buy.button_price_discount"
            button_text = i18n_get_text(
                language, key,
                base=int(base_price_rubles), final=int(final_price_rubles), period=period_text, gb=traffic_gb, badge=badge,
            )
        else:
            key = "buy.button_price_badge" if badge else "buy.button_price"
            button_text = i18n_get_text(
                language, key,
                price=int(final_price_rubles), period=period_text, gb=traffic_gb, badge=badge,
            )

        # КРИТИЧНО: callback_data БЕЗ purchase_id - только tariff и period
        buttons.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"period:{tariff_type}:{period_days}"
        )])
    
    # Кнопка назад:
    # — бизнес-тарифы → каталог бизнес;
    # — обычные → по умолчанию `menu_buy_vpn` (показывает либо экран
    #   «Управление подпиской» если есть активная подписка, либо
    #   выбор тарифа). В flow из рассылки (gift_reveal etc.) юзер
    #   уже на экране выбора тарифа, и «Назад» должна возвращать
    #   ровно туда же — иначе он попадает на управление подпиской.
    #   Маркер `from_broadcast` ставится при заходе из broadcast-CTA
    #   (callback_broadcast_gift_reveal) и переживает все клики
    #   tariff/period пока FSM-state не сброшен.
    fsm_data = await state.get_data()
    from_broadcast = bool(fsm_data.get("from_broadcast"))

    if config.is_biz_tariff(tariff_type):
        back_callback = "corporate_access_request"
    elif from_broadcast:
        back_callback = "broadcast_back_to_tariffs"
    else:
        back_callback = "menu_buy_vpn"

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=back_callback
    )])

    # Admin-managed global-discount notice (migration 069): если
    # активна глобальная скидка, добавим строку-подпись над кнопками.
    try:
        from app.services import pricing as _pricing
        _gd = await _pricing.get_global_discount()
        _pct = int(_gd.get("global_discount_percent") or 0)
        if _pct > 0:
            # Проверим что скидка не истекла
            _until_iso = _gd.get("discount_until_at")
            _active = True
            if _until_iso:
                try:
                    from datetime import datetime, timezone as _tz
                    _until_dt = datetime.fromisoformat(_until_iso.replace("Z", "+00:00"))
                    if _until_dt <= datetime.now(_tz.utc):
                        _active = False
                except Exception:
                    pass
            if _active:
                _reason = _gd.get("discount_reason") or "Спец-цены"
                _notice = f"\n\n🎁 <b>Скидка −{_pct}%</b> · {_reason}"
                if _until_iso:
                    try:
                        from datetime import datetime as _dt
                        _until_dt2 = _dt.fromisoformat(_until_iso.replace("Z", "+00:00"))
                        _notice += f" · до {_until_dt2.strftime('%d.%m')}"
                    except Exception:
                        pass
                text = (text or "") + _notice
    except Exception as _e:
        logger.warning("global-discount notice render failed: %s", _e)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)

    # КРИТИЧНО: Переходим в состояние choose_period
    await state.set_state(PurchaseState.choose_period)


@router.callback_query(
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


@router.callback_query(
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
