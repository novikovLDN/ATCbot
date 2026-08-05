"""Оплата подписки деньгами с внутреннего баланса.

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ
    Это единственный платёжный экран, где деньги списываются прямо здесь, а
    не у внешнего провайдера: 434 строки, самая длинная функция всего
    платёжного слоя. Её правят по другим причинам, чем выставление инвойса
    в Платеге или Lava, рядом с которыми она лежала.

ЧТО ЛЕГКО СЛОМАТЬ
    Списание идёт через database.finalize_balance_purchase — атомарно, под
    advisory-локом. Никакой проверки баланса «заранее», отдельно от
    списания, здесь быть не должно: между проверкой и списанием пользователь
    успевает нажать кнопку второй раз.
"""
import asyncio
import logging
import math
import time

import config
import database
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, Message
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.services.subscriptions.service import is_subscription_active
from app.handlers.notifications import notify_referral_cashback
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback, ensure_db_ready_message
from app.handlers.common.utils import (
    safe_edit_text,
    safe_edit_reply_markup,
    get_promo_session,
    clear_promo_session,
    sanitize_display_name,
)
from app.handlers.common.keyboards import (
    get_profile_keyboard,
    get_payment_success_keyboard,
)
from app.handlers.common.screens import show_profile
from app.handlers.common.states import TopUpStates, WithdrawStates, PurchaseState

# Автоудаление инвойса — общее для всех платёжных экранов.
from app.handlers.callbacks._invoice_cleanup import (
    INVOICE_TIMEOUT,
    _schedule_invoice_deletion,
)

pay_balance_router = Router()
logger = logging.getLogger(__name__)


@pay_balance_router.callback_query(F.data == "pay:balance")
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
    country = fsm_data.get("country")  # Страна для бизнес-тарифов
    # Комбо и bypass-only читаем здесь, пока FSM цел: ниже он очищается, а
    # признак комбо нужен ещё и для записи в базу (finalize_balance_purchase).
    _combo_gb_from_fsm = fsm_data.get("combo_bypass_gb", 0) or 0
    _bypass_gb_from_fsm = fsm_data.get("bypass_only_gb", 0) or 0

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
    if config.is_biz_tariff(tariff_type):
        tariff_name = "Business"
    elif tariff_type == "basic":
        tariff_name = "Basic"
    else:
        tariff_name = "Plus"
    
    try:
        # КРИТИЧНО: Проверяем, была ли активная подписка ДО платежа
        # Это нужно для определения сценария: первая покупка vs продление
        existing_subscription = await database.get_subscription(telegram_id)
        had_active_subscription_before_payment = is_subscription_active(existing_subscription) if existing_subscription else False
        
        # КРИТИЧНО: Все финансовые операции выполняются атомарно в одной транзакции
        # через finalize_balance_purchase
        months = period_days // 30
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
            promo_code=promo_code_from_session,  # CRITICAL: Промокод потребляется внутри транзакции
            country=country,
            # Признак комбо обязан попасть в базу. Раньше он жил только в FSM:
            # бот перезапустился или человек открыл другое меню — и следа
            # покупки комбо не оставалось нигде, доначислить ГБ было неоткуда.
            is_combo=_combo_gb_from_fsm > 0,
        )
        
        if not result or not result.get("success"):
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.message.answer(error_text, parse_mode="HTML")
            await state.set_state(None)
            return
        
        # Извлекаем результаты
        payment_id = result["payment_id"]
        expires_at = result["expires_at"]
        vpn_key = result["vpn_key"]
        vpn_key_plus = result.get("vpn_key_plus")
        is_renewal = result["is_renewal"]
        subscription_type = (result.get("subscription_type") or "basic").strip().lower()
        if subscription_type not in config.VALID_SUBSCRIPTION_TYPES:
            subscription_type = "basic"
        is_upgrade = result.get("is_basic_to_plus_upgrade", False)
        referral_reward_result = result.get("referral_reward")
        
        # Уведомление рефереру о кешбэке — общий хелпер (см.
        # app/handlers/notifications.py): он сам проверяет success и
        # форматирует срок подписки одинаково на всех путях оплаты.
        await notify_referral_cashback(
            callback.message.bot,
            referral_reward_result,
            referred_id=telegram_id,
            purchase_amount=final_price_rubles,
            action_type="renewal" if is_renewal else "purchase",
            period_days=period_days,
            context="balance_payment",
        )

        # Site sync (fire-and-forget)
        try:
            from app.services.site_sync import full_sync_after_payment, is_enabled as _site_sync_on
            if _site_sync_on():
                asyncio.ensure_future(full_sync_after_payment(
                    telegram_id, period_days, tariff_type, final_price_rubles,
                    f"balance_{payment_id}",
                ))
        except Exception:
            pass

        # ЗАЩИТА ОТ РЕГРЕССА: Валидируем VLESS ссылку перед отправкой
        # Для продлений vpn_key может быть пустым - получаем из подписки
        if is_renewal and not vpn_key:
            subscription = await database.get_subscription(telegram_id)
            if subscription and subscription.get("vpn_key"):
                vpn_key = subscription["vpn_key"]
        
        # Комбо и bypass-only: гигабайты обхода.
        #
        # Начисляем ЗДЕСЬ, до всех ранних выходов ниже. Раньше блок стоял в
        # самом конце обработчика, за двумя return'ами — по отложенной
        # активации и по «уведомление уже отправлено». Оба срабатывали при
        # успешной оплате, и человек оставался с подпиской и нулём гигабайтов.
        #
        # Отличие от карточных путей: у покупки с баланса нет строки в
        # pending_purchases, поэтому activation_worker её не подхватит и
        # доначислить будет некому — начисляем сразу, даже если активация
        # подписки отложена (add_bypass_traffic сам создаёт bypass-сущность).
        _is_combo = bool(result.get("is_combo") or _combo_gb_from_fsm > 0)
        if _is_combo:
            try:
                from app.services.combo_traffic import grant_combo_traffic
                await grant_combo_traffic(
                    telegram_id,
                    subscription_type,
                    period_days,
                    is_combo=True,
                    purchase_id=f"balance_purchase_{payment_id}",
                    subscription_end=expires_at,
                    source="balance",
                )
            except Exception as traffic_err:
                logger.error(
                    "COMBO_TRAFFIC_UNEXPECTED user=%s payment_id=%s: %s",
                    telegram_id, payment_id, traffic_err,
                )

        # Bypass-only — отдельный продукт (пакет ГБ без подписки): объём
        # приходит из FSM, в COMBO_TARIFFS его нет.
        if _bypass_gb_from_fsm > 0:
            try:
                from app.services import remnawave_service
                rmn_success = await remnawave_service.add_bypass_traffic(
                    telegram_id,
                    _bypass_gb_from_fsm * 1024**3,
                    subscription_type=subscription_type,
                    subscription_end=expires_at,
                    period_days=period_days,
                )
                if rmn_success:
                    await database.record_traffic_purchase(telegram_id, _bypass_gb_from_fsm, 0)
                    logger.info(
                        "BYPASS_ONLY_TRAFFIC_ADDED_BALANCE user=%s gb=%s",
                        telegram_id, _bypass_gb_from_fsm,
                    )
                else:
                    logger.error(
                        "BYPASS_ONLY_TRAFFIC_FAIL_BALANCE user=%s gb=%s — оплачено, не начислено",
                        telegram_id, _bypass_gb_from_fsm,
                    )
                await database.set_bypass_only_flag(telegram_id, True)
                from app.services.trials import service as trial_service
                if await trial_service.is_trial_available(telegram_id):
                    await trial_service.activate_trial(telegram_id)
            except Exception as bypass_err:
                logger.error(
                    "BYPASS_ONLY_TRAFFIC_ERROR_BALANCE user=%s gb=%s: %s",
                    telegram_id, _bypass_gb_from_fsm, bypass_err,
                )

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
                    url="https://t.me/atlas_suppbot"
                )]
            ])
            
            # ИДЕМПОТЕНТНОСТЬ: mark-before-send pattern
            try:
                sent = await database.mark_payment_notification_sent(payment_id)
                if not sent:
                    logger.warning(
                        f"NOTIFICATION_FLAG_ALREADY_SET [type=balance_purchase_pending, payment_id={payment_id}, user={telegram_id}]"
                    )
                    await state.set_state(None)
                    await state.clear()
                    return
            except Exception as e:
                logger.error(f"Failed to mark pending activation notification as sent: {e}")

            try:
                await callback.message.answer(
                    pending_text,
                    reply_markup=pending_keyboard,
                    parse_mode="HTML"
                )
                logger.info(
                    f"NOTIFICATION_SENT [type=balance_purchase_pending, payment_id={payment_id}, user={telegram_id}, expires_at={expires_str}]"
                )
            except Exception as e:
                logger.error(f"Failed to send pending activation message: user={telegram_id}, error={e}")
            
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

        # Ключ автоуведомления, если текст берётся из реестра. Апгрейд и
        # Business идут по своим текстам — им логировать нечего.
        _autonotif_key_to_log = None

        if is_upgrade:
            if _is_combo:
                upgrade_label = "Комбо Plus" if subscription_type == "plus" else "Комбо Basic"
            else:
                upgrade_label = "Plus" if subscription_type == "plus" else "Basic"
            text = (
                f"✅ Ваш тариф изменён на <b>{upgrade_label}</b>\n"
                f"📅 До: {expires_str}"
            )
            # Отправка — одна, общая, ниже по функции. Здесь стояла своя,
            # без return после неё, поэтому при апгрейде тарифа человек
            # получал два одинаковых сообщения подряд: это и следом ещё
            # раз тот же text с той же клавиатурой.
        else:
            if config.is_biz_tariff(subscription_type):
                tariff_label, tariff_icon = "Business", "🏢"
            elif subscription_type == "plus" and _is_combo:
                tariff_label, tariff_icon = "Комбо Plus", "🚀"
            elif subscription_type == "plus":
                tariff_label, tariff_icon = "Plus", "💎"
            elif _is_combo:
                tariff_label, tariff_icon = "Комбо Basic", "🚀"
            else:
                tariff_label, tariff_icon = "Basic", "🏆"
            # Автоуведомления: админ может переопределить текст через
            # дашборд. Payment success — критичный UX, никогда не
            # пропускаем отправку: если admin выключил override,
            # шлём i18n-дефолт как раньше. Toggle-off влияет ТОЛЬКО
            # на кастомный текст, но факт отправки — всегда.
            from app.services.automated_notifications import (
                is_notification_enabled as _autonotif_enabled,
                get_notification_text as _autonotif_text,
            )
            _key = None
            _params: dict = {}
            if is_renewal:
                _key = "payment.success_renewal_compact"
                _params = {
                    "tariff_icon": tariff_icon,
                    "tariff": tariff_label,
                    "date": expires_str,
                }
            else:
                if subscription_type == "plus":
                    _key = "payment.success_welcome_plus"
                    _params = {"date": expires_str}
                elif config.is_biz_tariff(subscription_type):
                    _key = None  # business — свой сценарий, не через реестр
                    text = f"🎉 Добро пожаловать в Atlas Secure!\n🏢 Тариф: Business\n📅 До: {expires_str}"
                else:
                    _key = "payment.success_welcome_basic"
                    _params = {"date": expires_str}
            if _key is not None:
                _use_custom = await _autonotif_enabled(_key)
                # language обязателен: в automated_notifications хранится только
                # русский текст. Без него нерусский покупатель получал бы
                # русское подтверждение вместо своего перевода.
                _custom = (
                    await _autonotif_text(_key, language=language, params=_params)
                ) if _use_custom else None
                text = _custom or i18n_get_text(language, _key, **_params)
                # Статистику пишем ПОСЛЕ отправки, по её результату —
                # см. блок отправки ниже. Здесь запись стояла раньше и
                # врала дважды: статус брался из того, включён ли
                # кастомный текст, а не из того, дошло ли сообщение.
                # При выключенном переопределении писалось
                # skipped_disabled, хотя сообщение уходило всегда;
                # при включённом — sent, даже если отправка падала.
                _autonotif_key_to_log = _key
        # К этому моменту premium и bypass уже созданы в Remnawave, поэтому обе
        # ссылки показываем прямо в тексте успеха — покупателю не нужен лишний
        # переход. При продлении блок не нужен: ссылки не меняются.
        if not is_renewal:
            try:
                sub_row = await database.get_subscription_any(telegram_id)
                premium_url = (sub_row or {}).get("vpn_key") or ""
                bypass_url = (sub_row or {}).get("vpn_key_plus") or ""
                links_block_parts = []
                if premium_url:
                    links_block_parts.append(
                        f"🌍 <b>Premium</b> (основные серверы):\n<code>{premium_url}</code>"
                    )
                if bypass_url:
                    links_block_parts.append(
                        f"🚧 <b>Bypass</b> (обходы LTE):\n<code>{bypass_url}</code>"
                    )
                if links_block_parts:
                    text = text + "\n\n" + "\n\n".join(links_block_parts)
            except Exception as e:
                logger.warning(
                    "PURCHASE_FLOW_LINKS_RENDER_FAIL user=%s err=%s", telegram_id, e,
                )

        # ИДЕМПОТЕНТНОСТЬ: mark-before-send pattern
        try:
            sent = await database.mark_payment_notification_sent(payment_id)
            if not sent:
                logger.warning(
                    f"NOTIFICATION_FLAG_ALREADY_SET [type=balance_purchase, payment_id={payment_id}, user={telegram_id}]"
                )
                return
        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to mark notification as sent: payment_id={payment_id}, user={telegram_id}, error={e}"
            )

        delivered = False
        try:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            delivered = True
            logger.info(
                f"NOTIFICATION_SENT [type=balance_purchase, payment_id={payment_id}, user={telegram_id}, "
                f"scenario={'renewal' if is_renewal else 'first_purchase'}]"
            )
        except Exception as e_send:
            logger.error(f"Failed to send success message: user={telegram_id}, error={e_send}")

        # Статистика автоуведомлений — по факту доставки.
        if _autonotif_key_to_log:
            try:
                from app.services.automated_notifications import (
                    log_notification_send as _autonotif_log,
                )
                await _autonotif_log(
                    _autonotif_key_to_log, telegram_id,
                    status="sent" if delivered else "failed",
                )
            except Exception:
                # Статистика не должна влиять на покупку: подписка уже
                # выдана, сообщение уже отправлено.
                pass
        
        logger.info(
            f"Subscription activated from balance: user={telegram_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"amount={final_price_rubles:.2f} RUB, "
            f"scenario={'renewal' if is_renewal else 'first_purchase'}"
        )

        # Fire-and-forget: create or renew Remnawave bypass user
        try:
            from app.services.remnawave_service import renew_remnawave_user_bg
            if expires_at and subscription_type not in ("trial",) + config.BIZ_TARIFFS:
                renew_remnawave_user_bg(telegram_id, subscription_type, expires_at, period_days=period_days)
        except Exception as rmn_err:
            logger.warning("REMNAWAVE_HOOK_FAIL: balance tg=%s %s", telegram_id, rmn_err)

    except Exception as e:
        logger.exception(f"CRITICAL: Unexpected error in callback_pay_balance: {e}")
        error_text = i18n_get_text(language, "errors.payment_processing")
        await callback.answer(error_text, show_alert=True)
        await state.set_state(None)
