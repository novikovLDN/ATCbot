"""Оплата через Telegram: карта (ЮKassa) и Stars.

ЧТО ЗДЕСЬ
    Три экрана, у которых один и тот же способ выставления счёта —
    Bot API send_invoice: карта в рублях, Telegram Stars и старая кнопка
    «оплатить картой» из сценария нехватки баланса.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственные провайдеры, где счёт живёт сообщением в чате: его
    надо потом удалять по таймауту и переводить FSM в processing_payment.
    У внешних провайдеров (Platega, CryptoBot, Lava) счёт — ссылка, и
    поведение другое.

ЧТО ЛЕГКО СЛОМАТЬ
    Порядок «сначала pending_purchase, потом инвойс». Вебхук оплаты
    приходит по purchase_id; если записи ещё нет, деньги придут, а товара
    не будет — и починить это можно будет только руками.

    Минимальная сумма 64 ₽ (6400 копеек) — требование Telegram Payments,
    не наша выдумка. Ниже неё провайдер отклоняет счёт, поэтому проверка
    стоит ДО создания покупки.

    Stars: промокод намеренно НЕ передаётся в покупку. Прайс в звёздах
    фиксированный, скидку в него не заложить, а переданный код сгорал бы
    при финализации, не дав человеку ничего.

    Комбо в Stars считается отдельной веткой: в FSM комбо лежит под именем
    базового тарифа, а признак — в combo_bypass_gb. Без этой ветки комбо
    продавалось по цене обычной подписки, а пакет ГБ уходил бесплатно.
"""
import math

import asyncio
import logging

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.core.rate_limit import check_rate_limit
from app.handlers.common.utils import get_promo_session
from app.handlers.common.states import PurchaseState

# Автоудаление инвойса — общее для всех платёжных экранов.
from app.handlers.callbacks._invoice_cleanup import _schedule_invoice_deletion

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "pay:card")
async def callback_pay_card(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 4B — Оплата картой (Telegram Payments / ЮKassa)

    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через Telegram Payments
    - Переводит в processing_payment
    """
    telegram_id = callback.from_user.id

    # Rate limiting
    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    # КРИТИЧНО: Проверяем FSM state - должен быть choose_payment_method
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:card: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_payment_method")
        await state.set_state(None)
        return
    
    # КРИТИЧНО: Получаем данные из FSM state (единственный источник правды)
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    country = fsm_data.get("country")  # Страна для бизнес-тарифов

    # КРИТИЧНО: Получаем промо-сессию для сохранения в pending_purchase
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None

    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM: user={telegram_id}, tariff={tariff_type}, period={period_days}, price={final_price_kopecks}")
        await state.set_state(None)
        return

    # Проверяем наличие provider_token
    if not config.TG_PROVIDER_TOKEN:
        error_text = i18n_get_text(language, "errors.payments_unavailable")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"TG_PROVIDER_TOKEN not configured")
        return

    # КРИТИЧНО: Валидация минимальной суммы платежа (64 RUB = 6400 kopecks)
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if final_price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        error_text = i18n_get_text(language, "errors.payment_min_amount")
        await callback.answer(error_text, show_alert=True)
        logger.warning(
            f"payment_blocked_min_amount: user={telegram_id}, tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}, min_required={MIN_PAYMENT_AMOUNT_KOPECKS}"
        )
        return
    
    try:
        # КРИТИЧНО: Создаем pending_purchase ТОЛЬКО при выборе оплаты картой
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=final_price_kopecks,
            promo_code=promo_code,
            country=country,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
        )

        # КРИТИЧНО: Сохраняем purchase_id в FSM state
        await state.update_data(purchase_id=purchase_id)

        logger.info(
            f"Purchase created for card payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}"
        )
        
        # Формируем payload
        payload = f"purchase:{purchase_id}"
        
        # Формируем описание тарифа
        months = period_days // 30
        if config.is_biz_tariff(tariff_type):
            tariff_name = "Business"
        elif tariff_type == "basic":
            tariff_name = "Basic"
        else:
            tariff_name = "Plus"
        description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

        # Формируем prices (цена в копейках из FSM)
        prices = [LabeledPrice(label=i18n_get_text(language, "buy.invoice_label"), amount=final_price_kopecks)]
        
        # КРИТИЧНО: Создаем invoice через Telegram Payments
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        await callback.bot.send_message(chat_id=telegram_id, text=i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))

        # КРИТИЧНО: Переводим в состояние processing_payment
        await state.set_state(PurchaseState.processing_payment)

        logger.info(
            f"invoice_created: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}"
        )

        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error creating invoice for card payment: {e}")
        error_text = i18n_get_text(language, "errors.payment_create")
        await callback.answer(error_text, show_alert=True)
        await state.set_state(None)



@router.callback_query(F.data == "pay:stars")
async def callback_pay_stars(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 4D — Оплата Telegram Stars

    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase (с ценой в Stars)
    - Создает invoice через Telegram Payments с provider_token="" и currency="XTR"
    - Переводит в processing_payment
    """
    telegram_id = callback.from_user.id

    # Rate limiting
    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    # КРИТИЧНО: Проверяем FSM state - должен быть choose_payment_method
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:stars: user={telegram_id}, state={current_state}")
        await state.set_state(None)
        return

    # КРИТИЧНО: Получаем данные из FSM state
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    country = fsm_data.get("country")  # Страна для бизнес-тарифов

    if not tariff_type or not period_days:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM for stars: user={telegram_id}")
        await state.set_state(None)
        return

    # Цена в Stars.
    #
    # ВАЖНО ПРО КОМБО: в FSM комбо-покупка лежит под ИМЕНЕМ БАЗОВОГО ТАРИФА
    # ("basic"/"plus"), а признак комбо — в combo_bypass_gb (см.
    # app/handlers/callbacks/navigation.py, обработчик выбора периода комбо).
    # Таблица TARIFFS_STARS содержит только обычные тарифы, поэтому без
    # отдельной ветки комбо продавалось по цене обычной подписки, а пакет ГБ
    # обхода уходил бесплатно: например «Комбо Плюс» на месяц списывал 325⭐
    # вместо ~459⭐ и сверху отдавал 75 ГБ. Заодно в отчётность попадала
    # заниженная сумма, потому что price_kopecks пишется отсюда же.
    combo_bypass_gb = fsm_data.get("combo_bypass_gb", 0) or 0
    is_combo_purchase = combo_bypass_gb > 0

    if is_combo_purchase:
        combo_key = f"combo_{tariff_type}"
        combo_info = (config.COMBO_TARIFFS or {}).get(combo_key, {}).get(period_days)
        if not combo_info or not combo_info.get("price"):
            error_text = i18n_get_text(language, "errors.tariff")
            await callback.answer(error_text, show_alert=True)
            logger.error(
                "Stars combo tariff not found: combo=%s, period=%s", combo_key, period_days
            )
            return
        # Та же конверсия рубли→Stars, что и в подарках (app/handlers/callbacks/gift.py):
        # наценка 1.7 и курс 1.85 ₽ за звезду, округление вверх.
        stars_price = math.ceil(int(combo_info["price"]) * 1.7 / 1.85)
    else:
        if tariff_type not in config.TARIFFS_STARS or period_days not in config.TARIFFS_STARS[tariff_type]:
            error_text = i18n_get_text(language, "errors.tariff")
            await callback.answer(error_text, show_alert=True)
            logger.error(f"Stars tariff not found: tariff={tariff_type}, period={period_days}")
            return
        stars_price = config.TARIFFS_STARS[tariff_type][period_days]["price"]
    # Для бизнес-тарифов применяем множитель страны к Stars
    if country and config.is_biz_tariff(tariff_type):
        multiplier = config.BIZ_COUNTRIES.get(country, {}).get("multiplier", 1.0)
        stars_price = int(round(stars_price * multiplier))

    # Промокоды к оплате звёздами не применяются: прайс в Stars фиксированный
    # и скидку в него не заложить.
    #
    # Раньше код промокода всё равно записывался в покупку, и при финализации
    # платежа он потреблялся — сгорал, не дав пользователю ни рубля скидки.
    # Теперь он не передаётся: промокод остаётся неиспользованным и сработает
    # при оплате рублями.
    promo_session = await get_promo_session(state)
    unused_promo_code = promo_session.get("promo_code") if promo_session else None
    if unused_promo_code:
        logger.info(
            "STARS_PROMO_NOT_APPLIED user=%s promo=%s — скидка к Stars не применяется, "
            "код сохранён для оплаты рублями",
            telegram_id, unused_promo_code,
        )
    promo_code = None

    # Для Stars: цена в копейках = stars_price * 100 (для pending_purchase, хранение)
    # Но фактическая оплата идёт в Stars, не в рублях
    stars_price_kopecks = stars_price * 100

    try:
        # Создаем pending_purchase
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=stars_price_kopecks,
            promo_code=promo_code,
            country=country,
            # tariff остаётся базовым намеренно: в subscription_type комбо
            # не хранится, туда идёт уровень доступа, а комбо помечается флагом.
            is_combo=is_combo_purchase,
        )

        await state.update_data(purchase_id=purchase_id, payment_method="stars")

        logger.info(
            f"Purchase created for stars payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, stars_price={stars_price}"
        )

        # Формируем payload
        payload = f"purchase:{purchase_id}"

        # Формируем описание
        months = period_days // 30
        if config.is_biz_tariff(tariff_type):
            tariff_name = "Business"
        elif tariff_type == "basic":
            tariff_name = "Basic"
        else:
            tariff_name = "Plus"
        description = i18n_get_text(
            language, "payment.stars_invoice_description",
            tariff_name=tariff_name, months=months
        )

        # КРИТИЧНО: Для Stars — provider_token="", currency="XTR", amount = кол-во Stars
        prices = [LabeledPrice(
            label=i18n_get_text(language, "payment.stars_invoice_label"),
            amount=stars_price
        )]

        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices
        )
        await callback.bot.send_message(chat_id=telegram_id, text=i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))

        await state.set_state(PurchaseState.processing_payment)

        logger.info(
            f"stars_invoice_created: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, stars_price={stars_price}"
        )

        await callback.answer()

    except Exception as e:
        logger.exception(f"Error creating Stars invoice: {e}")
        error_text = i18n_get_text(language, "errors.payment_create")
        await callback.answer(error_text, show_alert=True)
        await state.set_state(None)


@router.callback_query(F.data.startswith("pay_tariff_card:"))
async def callback_pay_tariff_card(callback: CallbackQuery, state: FSMContext):
    """
    Оплата тарифа картой (когда баланса не хватает)
    
    DEPRECATED: Эта функция больше не должна вызываться напрямую.
    Invoice создается автоматически в process_tariff_purchase_selection.
    
    Оставлена для обратной совместимости со старыми кнопками.
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Получаем данные из FSM state (единственный источник правды)
    fsm_data = await state.get_data()
    purchase_id = fsm_data.get("purchase_id")
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    
    # Если данных нет в FSM - пытаемся извлечь из callback_data (fallback)
    if not purchase_id or not tariff_type or not period_days:
        try:
            callback_data_parts = callback.data.split(":")
            if len(callback_data_parts) >= 4:
                tariff_type = callback_data_parts[1]
                period_days = int(callback_data_parts[2])
                purchase_id = callback_data_parts[3]
        except (IndexError, ValueError) as e:
            logger.error(f"Invalid pay_tariff_card callback_data: {callback.data}, error={e}")
            error_text = i18n_get_text(language, "errors.session_expired")
            await callback.answer(error_text, show_alert=True)
            return
    
    if not purchase_id or not tariff_type or not period_days:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Missing purchase data in FSM: user={telegram_id}, purchase_id={purchase_id}, tariff={tariff_type}, period={period_days}")
        return
    
    # КРИТИЧНО: Получаем pending_purchase (единственный источник правды о цене)
    pending_purchase = await database.get_pending_purchase(purchase_id, telegram_id, check_expiry=False)
    
    if not pending_purchase:
        # Purchase отсутствует - сессия устарела
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Purchase not found in pay_tariff_card: user={telegram_id}, purchase_id={purchase_id}")
        return
    
    # КРИТИЧНО: Проверяем соответствие тарифа и периода
    if pending_purchase["tariff"] != tariff_type or pending_purchase["period_days"] != period_days:
        # Несоответствие - сессия устарела
        logger.error(
            f"Purchase mismatch in pay_tariff_card: user={telegram_id}, purchase_id={purchase_id}, "
            f"stored_tariff={pending_purchase['tariff']}, stored_period={pending_purchase['period_days']}, "
            f"expected_tariff={tariff_type}, expected_period={period_days}"
        )
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        return
    
    # КРИТИЧНО: Purchase валиден - используем его цену для invoice
    logger.info(f"Using existing purchase in pay_tariff_card: user={telegram_id}, purchase_id={purchase_id}")
    
    # Проверяем наличие provider_token
    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    # Используем данные из pending purchase (а не из FSM)
    amount_rubles = pending_purchase["price_kopecks"] / 100.0
    final_price_kopecks = pending_purchase["price_kopecks"]
    
    # КРИТИЧНО: Валидация минимальной суммы платежа (64 RUB = 6400 kopecks)
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if final_price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        # Отменяем pending purchase с невалидной ценой
        await database.cancel_pending_purchases(telegram_id, "min_amount_validation_failed")

        error_text = i18n_get_text(language, "errors.payment_min_amount")
        logger.warning(
            f"payment_blocked_min_amount: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"final_price_kopecks={final_price_kopecks}, min_required={MIN_PAYMENT_AMOUNT_KOPECKS}"
        )
        await callback.answer(error_text, show_alert=True)
        return
    
    # Используем purchase_id в payload
    payload = f"purchase:{purchase_id}"
    
    # Формируем описание тарифа
    months = period_days // 30
    if config.is_biz_tariff(tariff_type):
        tariff_name = "Business"
    elif tariff_type == "basic":
        tariff_name = "Basic"
    else:
        tariff_name = "Plus"
    description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

    # Формируем prices (цена в копейках)
    prices = [LabeledPrice(label=i18n_get_text(language, "buy.invoice_label"), amount=final_price_kopecks)]

    logger.info(
        f"invoice_created: user={telegram_id}, purchase_id={purchase_id}, "
        f"tariff={tariff_type}, period_days={period_days}, "
        f"final_price_kopecks={final_price_kopecks}, amount_rubles={amount_rubles:.2f}"
    )
    
    try:
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        await callback.bot.send_message(chat_id=telegram_id, text=i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
