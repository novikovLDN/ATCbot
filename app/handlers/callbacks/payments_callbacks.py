"""
Payment-related callback handlers: topup, withdraw, pay:balance, pay:card, pay:crypto.
"""
import logging
import time

import config
import database
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.services.subscriptions.service import is_subscription_active
from app.handlers.notifications import send_referral_cashback_notification
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback, ensure_db_ready_message
from app.handlers.common.utils import (
    safe_edit_text,
    safe_edit_reply_markup,
    get_promo_session,
    clear_promo_session,
)
from app.handlers.common.keyboards import (
    get_profile_keyboard,
    get_payment_success_keyboard,
)
from app.handlers.common.screens import show_profile
from app.handlers.common.states import TopUpStates, WithdrawStates, PurchaseState

payments_router = Router()
logger = logging.getLogger(__name__)

# --- User withdrawal flow ---
MIN_WITHDRAW_RUBLES = 500


@payments_router.callback_query(F.data == "topup_balance")
async def callback_topup_balance(callback: CallbackQuery):
    """Пополнить баланс"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Показываем экран выбора суммы
    text = i18n_get_text(language, "main.topup_balance_select_amount")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="250 ₽",
            callback_data="topup_amount:250"
        )],
        [InlineKeyboardButton(
            text="750 ₽",
            callback_data="topup_amount:750"
        )],
        [InlineKeyboardButton(
            text="999 ₽",
            callback_data="topup_amount:999"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.topup_custom_amount"),
            callback_data="topup_custom"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_profile"
        )],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@payments_router.callback_query(F.data.startswith("topup_amount:"))
async def callback_topup_amount(callback: CallbackQuery):
    """Обработка выбора суммы пополнения - показываем экран выбора способа оплаты"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Извлекаем сумму из callback_data
    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    # Показываем экран выбора способа оплаты
    text = i18n_get_text(language, "main.topup_select_payment_method", amount=amount)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data=f"topup_card:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_crypto"),
            callback_data=f"topup_crypto:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="topup_balance"
        )],
    ])
    
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@payments_router.callback_query(F.data == "topup_custom")
async def callback_topup_custom(callback: CallbackQuery, state: FSMContext):
    """Ввод произвольной суммы пополнения баланса"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    await callback.answer()
    
    # Переводим пользователя в состояние ввода суммы
    await state.set_state(TopUpStates.waiting_for_amount)
    
    # Отправляем сообщение с инструкцией
    text = i18n_get_text(language, "main.topup_enter_amount")
    
    await callback.message.answer(text)


@payments_router.callback_query(F.data == "withdraw_start")
async def callback_withdraw_start(callback: CallbackQuery, state: FSMContext):
    """Начало вывода средств"""
    if not await ensure_db_ready_callback(callback):
        return
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "withdraw.amount_prompt")
    await state.set_state(WithdrawStates.withdraw_amount)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_profile")]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@payments_router.callback_query(F.data == "withdraw_confirm_amount", StateFilter(WithdrawStates.withdraw_confirm))
async def callback_withdraw_confirm_amount(callback: CallbackQuery, state: FSMContext):
    """Подтверждение суммы → переход к вводу реквизитов"""
    language = await resolve_user_language(callback.from_user.id)
    await state.set_state(WithdrawStates.withdraw_requisites)
    text = i18n_get_text(language, "withdraw.requisites_prompt")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="withdraw_back_to_amount")]
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@payments_router.callback_query(F.data == "withdraw_final_confirm", StateFilter(WithdrawStates.withdraw_final_confirm))
async def callback_withdraw_final_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Финальное подтверждение: списание, создание заявки, уведомление админу"""
    if not await ensure_db_ready_callback(callback):
        return
    language = await resolve_user_language(callback.from_user.id)
    telegram_id = callback.from_user.id
    data = await state.get_data()
    amount = data.get("withdraw_amount")
    requisites = data.get("withdraw_requisites", "")
    if not amount or not requisites:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return
    amount_kopecks = int(amount * 100)
    username = callback.from_user.username
    wid = await database.create_withdrawal_request(telegram_id, username, amount_kopecks, requisites)
    if not wid:
        await callback.answer(i18n_get_text(language, "withdraw.insufficient_funds"), show_alert=True)
        await state.clear()
        return
    await state.clear()
    await callback.answer()
    in_progress_text = i18n_get_text(language, "withdraw.in_progress")
    has_any_sub, auto_renew = False, False
    try:
        sub = await database.get_subscription(telegram_id)
        has_any_sub = bool(sub and sub.get("expires_at"))
        auto_renew = bool(sub and sub.get("auto_renew"))
    except Exception:
        pass
    await safe_edit_text(callback.message, in_progress_text, reply_markup=get_profile_keyboard(language, has_any_sub, auto_renew), bot=callback.bot)
    try:
        balance = await database.get_user_balance(telegram_id)
        subscription = await database.get_subscription(telegram_id)
        has_active = is_subscription_active(subscription) if subscription else False
        sub_text = i18n_get_text(language, "profile.status_active") if has_active else i18n_get_text(language, "profile.status_inactive")
        admin_text = (
            f"💸 Новая заявка на вывод #{wid}\n\n"
            f"👤 Пользователь: @{username or '—'} (ID: {telegram_id})\n"
            f"📊 Баланс: {balance:.2f} ₽\n"
            f"💰 Сумма: {amount:.2f} ₽\n"
            f"📶 Подписка: {sub_text}\n"
            f"🏦 Реквизиты: {requisites[:200]}"
        )
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"withdraw_approve:{wid}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"withdraw_reject:{wid}")],
        ])
        await bot.send_message(config.ADMIN_TELEGRAM_ID, admin_text, reply_markup=admin_kb)
        logger.info(f"ADMIN_NOTIFICATION_SENT withdrawal_id={wid} user={telegram_id} amount={amount:.2f} RUB")
    except Exception as e:
        logger.error(f"CRITICAL: Failed to send withdrawal notification to admin: withdrawal_id={wid} user={telegram_id} error={e}", exc_info=True)
        try:
            await database._log_audit_event_atomic_standalone(
                "withdrawal_admin_notify_failed", telegram_id, None,
                f"withdrawal_id={wid} amount={amount:.2f} error={e}"
            )
        except Exception:
            pass


@payments_router.callback_query(F.data == "withdraw_cancel")
@payments_router.callback_query(F.data == "withdraw_back_to_amount")
@payments_router.callback_query(F.data == "withdraw_back_to_requisites")
async def callback_withdraw_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена или назад в выводе средств"""
    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    await show_profile(callback, language)
    await callback.answer()


@payments_router.callback_query(F.data.startswith("withdraw_approve:"))
async def callback_withdraw_approve(callback: CallbackQuery, bot: Bot):
    """Админ: подтвердить вывод средств"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        wid = int(callback.data.split(":")[1])
        wr = await database.get_withdrawal_request(wid)
        if not wr or wr["status"] != "pending":
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        ok = await database.approve_withdrawal_request(wid, callback.from_user.id)
        if ok:
            lang = await resolve_user_language(wr["telegram_id"])
            text = i18n_get_text(lang, "withdraw.approved")
            try:
                await bot.send_message(wr["telegram_id"], text)
            except Exception as e:
                logger.warning(f"Failed to send withdrawal approved notification to {wr['telegram_id']}: {e}")
            await callback.answer("✅ Подтверждено", show_alert=True)
            await safe_edit_reply_markup(callback.message, reply_markup=None)
        else:
            await callback.answer("Ошибка подтверждения", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in withdraw_approve: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@payments_router.callback_query(F.data.startswith("withdraw_reject:"))
async def callback_withdraw_reject(callback: CallbackQuery, bot: Bot):
    """Админ: отклонить вывод (возврат средств)"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    try:
        wid = int(callback.data.split(":")[1])
        wr = await database.get_withdrawal_request(wid)
        if not wr or wr["status"] != "pending":
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        ok = await database.reject_withdrawal_request(wid, callback.from_user.id)
        if ok:
            lang = await resolve_user_language(wr["telegram_id"])
            text = i18n_get_text(lang, "withdraw.rejected")
            try:
                await bot.send_message(wr["telegram_id"], text)
            except Exception as e:
                logger.warning(f"Failed to send withdrawal rejected notification to {wr['telegram_id']}: {e}")
            await callback.answer("❌ Отклонено", show_alert=True)
            await safe_edit_reply_markup(callback.message, reply_markup=None)
        else:
            await callback.answer("Ошибка отклонения", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in withdraw_reject: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@payments_router.callback_query(F.data == "pay:balance")
async def callback_pay_balance(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 4A — Оплата балансом
    
    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Списывает баланс и активирует подписку в ОДНОЙ транзакции
    - Rollback при любой ошибке
    - Начисляет реферальный кешбэк
    - Отправляет VPN ключ пользователю
    """
    telegram_id = callback.from_user.id
    
    # STEP 6 — F3: RATE LIMITING (HUMAN & BOT SAFETY)
    # Rate limit payment initiation
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
        logger.warning(f"Invalid FSM state for pay:balance: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_payment_method")
        await state.set_state(None)
        return
    
    # КРИТИЧНО: Получаем данные из FSM state (единственный источник правды)
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    
    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM: user={telegram_id}, tariff={tariff_type}, period={period_days}, price={final_price_kopecks}")
        await state.set_state(None)
        return
    
    # Получаем баланс пользователя
    balance_rubles = await database.get_user_balance(telegram_id)
    final_price_rubles = final_price_kopecks / 100.0
    
    # Проверяем, хватает ли баланса
    if balance_rubles < final_price_rubles:
        # Баланса не хватает - показываем alert
        shortage = final_price_rubles - balance_rubles
        error_text = i18n_get_text(
            language,
            "errors.insufficient_balance",
            amount=final_price_rubles,
            balance=balance_rubles,
            shortage=shortage
        )
        await callback.answer(error_text, show_alert=True)
        logger.info(f"Insufficient balance for payment: user={telegram_id}, balance={balance_rubles:.2f} RUB, required={final_price_rubles:.2f} RUB")
        return
    
    # КРИТИЧНО: ИДЕМПОТЕНТНОСТЬ - Проверяем FSM state и предотвращаем повторное списание
    # Если уже в processing_payment - значит оплата уже обрабатывается
    current_state = await state.get_state()
    if current_state == PurchaseState.processing_payment:
        logger.warning(
            f"IDEMPOTENCY_CHECK: Duplicate payment attempt blocked: user={telegram_id}, "
            f"current_state={current_state}, reason=already_processing_payment"
        )
        error_text = i18n_get_text(language, "errors.session_expired_processing")
        await callback.answer(error_text, show_alert=True)
        return
    
    # Баланса хватает - списываем и активируем подписку в ОДНОЙ транзакции
    await callback.answer()
    
    # КРИТИЧНО: Переходим в состояние processing_payment ПЕРЕД списанием баланса
    # Это блокирует повторные клики до завершения транзакции
    await state.set_state(PurchaseState.processing_payment)
    
    # КРИТИЧНО: Формируем данные для активации подписки
    months = period_days // 30
    tariff_name = "Basic" if tariff_type == "basic" else "Plus"
    
    try:
        # КРИТИЧНО: Проверяем, была ли активная подписка ДО платежа
        # Это нужно для определения сценария: первая покупка vs продление
        existing_subscription = await database.get_subscription(telegram_id)
        had_active_subscription_before_payment = is_subscription_active(existing_subscription) if existing_subscription else False
        
        # КРИТИЧНО: Все финансовые операции выполняются атомарно в одной транзакции
        # через finalize_balance_purchase
        months = period_days // 30
        tariff_name = "Basic" if tariff_type == "basic" else "Plus"
        transaction_description = f"Оплата подписки {tariff_name} на {months} месяц(ев)"
        
        # CRITICAL FIX: Получаем промокод из промо-сессии для передачи в finalize_balance_purchase
        promo_session = await get_promo_session(state)
        promo_code_from_session = promo_session.get("promo_code") if promo_session else None
        
        result = await database.finalize_balance_purchase(
            telegram_id=telegram_id,
            tariff_type=tariff_type,
            period_days=period_days,
            amount_rubles=final_price_rubles,
            description=transaction_description,
            promo_code=promo_code_from_session  # CRITICAL: Промокод потребляется внутри транзакции
        )
        
        if not result or not result.get("success"):
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.message.answer(error_text)
            await state.set_state(None)
            return
        
        # Извлекаем результаты
        payment_id = result["payment_id"]
        expires_at = result["expires_at"]
        vpn_key = result["vpn_key"]
        vpn_key_plus = result.get("vpn_key_plus")
        is_renewal = result["is_renewal"]
        subscription_type = (result.get("subscription_type") or "basic").strip().lower()
        if subscription_type not in ("basic", "plus"):
            subscription_type = "basic"
        is_upgrade = result.get("is_basic_to_plus_upgrade", False)
        referral_reward_result = result.get("referral_reward")
        
        # Отправляем уведомление о кешбэке (если начислен)
        if referral_reward_result and referral_reward_result.get("success"):
            try:
                notification_sent = await send_referral_cashback_notification(
                    bot=callback.message.bot,
                    referrer_id=referral_reward_result.get("referrer_id"),
                    referred_id=telegram_id,
                    purchase_amount=final_price_rubles,
                    cashback_amount=referral_reward_result.get("reward_amount"),
                    cashback_percent=referral_reward_result.get("percent"),
                    paid_referrals_count=referral_reward_result.get("paid_referrals_count", 0),
                    referrals_needed=referral_reward_result.get("referrals_needed", 0),
                    action_type="purchase" if not is_renewal else "renewal"
                )
                if notification_sent:
                    logger.info(f"Referral cashback processed for balance payment: user={telegram_id}, amount={final_price_rubles} RUB")
            except Exception as e:
                logger.warning(
                    "NOTIFICATION_FAILED",
                    extra={
                        "type": "balance_payment_referral",
                        "user": telegram_id,
                        "referrer": referral_reward_result.get("referrer_id") if referral_reward_result else None,
                        "error": str(e)
                    }
                )
        
        # ЗАЩИТА ОТ РЕГРЕССА: Валидируем VLESS ссылку перед отправкой
        # Для продлений vpn_key может быть пустым - получаем из подписки
        if is_renewal and not vpn_key:
            subscription = await database.get_subscription(telegram_id)
            if subscription and subscription.get("vpn_key"):
                vpn_key = subscription["vpn_key"]
        
        # Проверяем статус активации подписки
        subscription_check = await database.get_subscription_any(telegram_id)
        is_pending_activation = (
            subscription_check and 
            subscription_check.get("activation_status") == "pending" and
            not is_renewal
        )
        
        # Если активация отложена - показываем информационное сообщение
        if is_pending_activation:
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            pending_text = i18n_get_text(language, "payment.pending_activation", date=expires_str)
            
            # Клавиатура с кнопками профиля и поддержки
            pending_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "main.profile"),
                    callback_data="menu_profile"
                )],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "main.support"),
                    callback_data="menu_support"
                )]
            ])
            
            try:
                await callback.message.answer(
                    pending_text,
                    reply_markup=pending_keyboard,
                    parse_mode="HTML"
                )
                logger.info(
                    f"Pending activation message sent: user={telegram_id}, payment_id={payment_id}, expires_at={expires_str}"
                )
            except Exception as e:
                logger.error(f"Failed to send pending activation message: user={telegram_id}, error={e}")
            
            # Помечаем уведомление как отправленное
            try:
                sent = await database.mark_payment_notification_sent(payment_id)
                if sent:
                    logger.info(
                        f"NOTIFICATION_SENT [type=balance_purchase_pending, payment_id={payment_id}, user={telegram_id}]"
                    )
            except Exception as e:
                logger.error(f"Failed to mark pending activation notification as sent: {e}")
            
            await state.set_state(None)
            await state.clear()
            return
        
        # API is source of truth — vpn_key from API, no local validation
        # КРИТИЧНО: Удаляем промо-сессию после успешной оплаты
        await clear_promo_session(state)
        
        # ИДЕМПОТЕНТНОСТЬ: Проверяем, было ли уже отправлено уведомление
        notification_already_sent = await database.is_payment_notification_sent(payment_id)
        
        if notification_already_sent:
            logger.info(
                f"NOTIFICATION_IDEMPOTENT_SKIP [type=balance_purchase, payment_id={payment_id}, user={telegram_id}, "
                f"scenario={'renewal' if is_renewal else 'first_purchase'}]"
            )
            await state.set_state(None)
            await state.clear()
            return
        
        # КРИТИЧНО: Очищаем FSM после успешной активации
        await state.set_state(None)
        await state.clear()
        
        # Один компактный экран: текст + кнопки копирования и профиль (без отдельной отправки ключей)
        expires_str = expires_at.strftime("%d.%m.%Y")
        keyboard = get_payment_success_keyboard(language, subscription_type=subscription_type, is_renewal=is_renewal)

        if is_upgrade:
            text = (
                f"⭐️ Апгрейд до Platinum!\n"
                f"📅 До: {expires_str}\n\n"
                f"📲 Чтобы новые конфигурации появились в приложении:\n"
                f"V2rayTUN — нажмите 🔄 (обновить подписку)\n"
                f"Streisand — потяните экран вниз для обновления"
            )
            try:
                await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send upgrade message: user={telegram_id}, error={e}")
        else:
            if is_renewal:
                tariff_label = "Plus" if subscription_type == "plus" else "Basic"
                tariff_icon = "⭐️" if subscription_type == "plus" else "📦"
                text = i18n_get_text(
                    language,
                    "payment.success_renewal_compact",
                    tariff_icon=tariff_icon,
                    tariff=tariff_label,
                    date=expires_str,
                )
            else:
                if subscription_type == "plus":
                    text = i18n_get_text(language, "payment.success_welcome_plus", date=expires_str)
                else:
                    text = i18n_get_text(language, "payment.success_welcome_basic", date=expires_str)
            try:
                await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send success message: user={telegram_id}, error={e}")

        # ИДЕМПОТЕНТНОСТЬ: Помечаем уведомление как отправленное (после успешной отправки)
        try:
            sent = await database.mark_payment_notification_sent(payment_id)
            if sent:
                logger.info(
                    f"NOTIFICATION_SENT [type=balance_purchase, payment_id={payment_id}, user={telegram_id}, "
                    f"scenario={'renewal' if is_renewal else 'first_purchase'}]"
                )
            else:
                logger.warning(
                    f"NOTIFICATION_FLAG_ALREADY_SET [type=balance_purchase, payment_id={payment_id}, user={telegram_id}]"
                )
        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
            )
        
        logger.info(
            f"Subscription activated from balance: user={telegram_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"amount={final_price_rubles:.2f} RUB, "
            f"scenario={'renewal' if is_renewal else 'first_purchase'}"
        )
        
    except Exception as e:
        logger.exception(f"CRITICAL: Unexpected error in callback_pay_balance: {e}")
        error_text = i18n_get_text(language, "errors.payment_processing")
        await callback.answer(error_text, show_alert=True)
        await state.set_state(None)


@payments_router.callback_query(F.data == "pay:card")
async def callback_pay_card(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 4B — Оплата картой (Telegram Payments / ЮKassa)
    
    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через Telegram Payments
    - Переводит в processing_payment
    """
    telegram_id = callback.from_user.id
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
            promo_code=promo_code
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
        tariff_name = "Basic" if tariff_type == "basic" else "Plus"
        description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

        # Формируем prices (цена в копейках из FSM)
        prices = [LabeledPrice(label=i18n_get_text(language, "buy.invoice_label"), amount=final_price_kopecks)]
        
        # КРИТИЧНО: Создаем invoice через Telegram Payments
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        
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


@payments_router.callback_query(F.data == "pay:crypto")
async def callback_pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата криптовалютой через CryptoBot
    
    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через CryptoBot API
    - Отправляет payment URL пользователю
    - Использует polling для проверки статуса (NO WEBHOOKS)
    """
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # КРИТИЧНО: Проверяем FSM state - должен быть choose_payment_method
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:crypto: user={telegram_id}, state={current_state}, expected=PurchaseState.choose_payment_method")
        await state.set_state(None)
        return
    
    # КРИТИЧНО: Получаем данные из FSM state
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    
    # Получаем промо-сессию
    promo_session = await get_promo_session(state)
    promo_code = promo_session.get("promo_code") if promo_session else None
    
    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM: user={telegram_id}, tariff={tariff_type}, period={period_days}, price={final_price_kopecks}")
        await state.set_state(None)
        return
    
    # Проверяем наличие CryptoBot конфигурации
    try:
        from payments import cryptobot
        if not cryptobot.is_enabled():
            error_text = i18n_get_text(language, "payment.crypto_unavailable")
            await callback.answer(error_text, show_alert=True)
            logger.error(f"CryptoBot not configured")
            return
    except ImportError:
        error_text = i18n_get_text(language, "payment.crypto_unavailable")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"CryptoBot module not found")
        return
    
    try:
        # Создаем pending_purchase
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=final_price_kopecks,
            promo_code=promo_code
        )
        
        # Сохраняем purchase_id в FSM state
        await state.update_data(purchase_id=purchase_id)
        
        logger.info(
            f"Purchase created for crypto payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, final_price_kopecks={final_price_kopecks}"
        )
        
        # Формируем сумму в рублях
        final_price_rubles = final_price_kopecks / 100.0
        
        # Формируем описание тарифа
        months = period_days // 30
        tariff_name = "Basic" if tariff_type == "basic" else "Plus"
        description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

        # Формируем payload (храним purchase_id для идентификации)
        payload = f"purchase:{purchase_id}"

        # Создаем invoice через CryptoBot API
        invoice_data = await cryptobot.create_invoice(
            amount_rub=final_price_rubles,
            description=description,
            payload=payload
        )
        
        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["pay_url"]
        
        # КРИТИЧНО: Сохраняем invoice_id в FSM state для последующей проверки статуса
        await state.update_data(cryptobot_invoice_id=invoice_id)
        
        # Сохраняем invoice_id в БД для автоматической проверки платежей
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error(f"Failed to save invoice_id to DB: purchase_id={purchase_id}, invoice_id={invoice_id}, error={e}")
        
        logger.info(
            f"invoice_created: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, invoice_id={invoice_id}, "
            f"final_price_rubles={final_price_rubles:.2f}"
        )
        
        # Отправляем пользователю сообщение с payment URL
        text = i18n_get_text(language, "payment.crypto_waiting", amount=final_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.crypto_pay_button"),
                url=payment_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_buy_vpn"
            )]
        ])
        
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
        
        # Очищаем FSM state после создания invoice
        await state.set_state(None)
        await state.clear()
        
    except Exception as e:
        logger.exception(f"Error creating CryptoBot invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)


@payments_router.callback_query(F.data.startswith("topup_crypto:"))
async def callback_topup_crypto(callback: CallbackQuery):
    """Пополнение баланса через CryptoBot"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    # Извлекаем сумму из callback_data
    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    # Проверяем доступность CryptoBot
    from payments import cryptobot
    if not cryptobot.is_enabled():
        await callback.answer(
            i18n_get_text(language, "payment.crypto_unavailable"),
            show_alert=True
        )
        return
    
    try:
        # Создаем pending purchase для пополнения баланса (отдельный flow, без subscription logic)
        amount_kopecks = amount * 100
        purchase_id = await subscription_service.create_balance_topup_purchase(
            telegram_id=telegram_id,
            amount_kopecks=amount_kopecks,
            currency="RUB"
        )
        
        # Формируем описание
        description = f"Пополнение баланса на {amount} ₽"
        
        # Формируем payload (храним purchase_id для идентификации)
        payload = f"purchase:{purchase_id}"
        
        # Создаем invoice через CryptoBot API
        invoice_data = await cryptobot.create_invoice(
            amount_rub=float(amount),
            description=description,
            payload=payload
        )
        
        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["pay_url"]
        
        # Сохраняем invoice_id в БД для автоматической проверки платежей
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error(f"Failed to save invoice_id to DB: purchase_id={purchase_id}, invoice_id={invoice_id}, error={e}")
        
        logger.info(
            f"balance_topup_invoice_created: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
            f"amount={amount} RUB, invoice_id={invoice_id}"
        )
        
        # Отправляем пользователю сообщение с payment URL
        text = i18n_get_text(language, "main.balance_topup_waiting", amount=amount)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "main.crypto_pay_button"),
                url=payment_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="topup_balance"
            )]
        ])
        
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error creating CryptoBot invoice for balance top-up: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@payments_router.callback_query(F.data.startswith("topup_card:"))
async def callback_topup_card(callback: CallbackQuery):
    """Оплата пополнения баланса картой"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    
    amount_str = callback.data.split(":")[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    
    # Создаем invoice через Telegram Payments
    timestamp = int(time.time())
    payload = f"balance_topup_{telegram_id}_{amount}_{timestamp}"
    amount_kopecks = amount * 100
    
    try:
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=i18n_get_text(language, "main.topup_invoice_title"),
            description=i18n_get_text(language, "main.topup_invoice_description", amount=amount),
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=i18n_get_text(language, "main.topup_invoice_label"), amount=amount_kopecks)]
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice for balance topup: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@payments_router.callback_query(F.data.startswith("pay_tariff_card:"))
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
    tariff_name = "Basic" if tariff_type == "basic" else "Plus"
    description = i18n_get_text(language, "buy.invoice_description", tariff_name=tariff_name, months=months)

    # Формируем prices (цена в копейках)
    prices = [LabeledPrice(label=i18n_get_text(language, "buy.invoice_label"), amount=final_price_kopecks)]

    logger.info(
        f"invoice_created: user={telegram_id}, purchase_id={purchase_id}, "
        f"tariff={tariff_type}, period_days={period_days}, "
        f"final_price_kopecks={final_price_kopecks}, amount_rubles={amount_rubles:.2f}"
    )
    
    try:
        # Отправляем invoice
        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Atlas Secure VPN",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices
        )
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@payments_router.callback_query(F.data.startswith("crypto_pay:tariff:"))
async def callback_crypto_pay_tariff(callback: CallbackQuery, state: FSMContext):
    """Оплата тарифа криптой - ОТКЛЮЧЕНА"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return


@payments_router.callback_query(F.data.startswith("pay_crypto_asset:"))
async def callback_pay_crypto_asset(callback: CallbackQuery, state: FSMContext):
    """Оплата криптой (выбор актива) - ОТКЛЮЧЕНА"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return


@payments_router.callback_query(F.data.startswith("crypto_pay:balance:"))
async def callback_crypto_pay_balance(callback: CallbackQuery):
    """Оплата пополнения баланса криптой - ОТКЛЮЧЕНА"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    logger.warning(f"crypto_payment_disabled: user={telegram_id}, callback_data={callback.data}")

    await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
    return
