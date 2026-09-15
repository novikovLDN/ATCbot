"""
Payment-related callback handlers: topup, pay:balance, pay:card, pay:sbp, pay:stars, pay:crypto.
"""
import asyncio
import logging
import math
import time

import config
import database
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, Message
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions import service as subscription_service
from app.services.broadcast_offer_prices import fsm_offer_key
from app.services.subscriptions.service import is_subscription_active
from app.core.rate_limit import check_rate_limit
from app.services import provisioning_flags
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import (
    safe_edit_text,
    safe_edit_reply_markup,
    get_promo_session,
    get_applied_promo_code,
    clear_promo_session,
)
from app.handlers.common.keyboards import (
    get_payment_success_keyboard,
)
from app.handlers.common.states import TopUpStates, PurchaseState
from app.handlers.common.emoji import CE

payments_router = Router()
logger = logging.getLogger(__name__)

# --- Invoice auto-deletion after timeout ---
INVOICE_TIMEOUT = config.INVOICE_TIMEOUT_SECONDS  # 15 минут

# Файл-id картинки, которую вешаем на экран «🏦 Оплата через СБП» (Wata).
# Стабильный file_id из истории бота — загружать заново не надо.
_WATA_INVOICE_PHOTO_ID = "AgACAgQAAxkBAAGAJRZqgECFrnKCZZWmXbWSjK2-PK1sWQACXRBrGwG9AVC_2M3k-snqYwEAAwIAA3cAAz0E"


def _is_combo(fsm_data: dict) -> bool:
    return (fsm_data.get("combo_bypass_gb") or 0) > 0


def _invoice_back(fsm_data: dict, tariff_type: str) -> str:
    """«Назад» from an invoice screen → the period screen of THIS purchase
    (08 M14: a Combo fell back to the Basic/Plus periods, SBP to the buy root)."""
    if _is_combo(fsm_data):
        from app.services import tariffs
        return f"combo_tariff:combo_{tariffs.normalize_tier(tariff_type) or 'basic'}"
    return f"tariff:{tariff_type}"


def _tariff_label(fsm_data: dict, tariff_type: str, language: str) -> str:
    """Tariff name for invoice descriptions — «Combo Basic» for a Combo (08 M16: was «Basic»)."""
    from app.services import tariffs
    tier = tariffs.normalize_tier(tariff_type) or "basic"
    key = f"combo_{tier}" if _is_combo(fsm_data) else tier
    return i18n_get_text(language, f"tariff.name_{key}")


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, invoice_message: Message, timeout: int = INVOICE_TIMEOUT):
    """Удаляет сообщение с инвойсом через timeout секунд."""
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=invoice_message.message_id)
        logger.info(f"INVOICE_EXPIRED: deleted invoice message_id={invoice_message.message_id} chat_id={chat_id}")
    except Exception as e:
        logger.debug(f"Failed to delete expired invoice: chat_id={chat_id}, error={e}")
    finally:
        # Даже если удаление упало (уже удалено), почистим карту, чтобы
        # не держать в памяти висячий mapping.
        for pid, entry in list(_invoice_messages.items()):
            if entry == (chat_id, invoice_message.message_id):
                _invoice_messages.pop(pid, None)


# ── Провайдер-agnostic реестр «invoice-экранов» ────────────────────────
#
# Ключ — purchase_id, значение — (chat_id, message_id) экрана «Ждём
# платёж».  Заполняется хендлерами callback_pay_wata / callback_topup_wata
# при создании экрана и вычитывается из _send_confirmation после успешного
# уведомления: экран старой «оплаты» удаляется, чтобы юзер не видел
# устаревший текст рядом с «Платёж успешно обработан».
_invoice_messages: dict[str, tuple[int, int]] = {}


async def delete_invoice_message_for_purchase(bot: Bot, purchase_id: str) -> None:
    """Best-effort удаление экрана «Ждём платёж» после успешного платежа."""
    entry = _invoice_messages.pop(purchase_id, None)
    if not entry:
        return
    chat_id, message_id = entry
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "delete_invoice_message: chat=%s msg=%s err=%s (ok if уже удалили)",
            chat_id, message_id, e,
        )


async def delete_all_invoice_messages_for_user(bot: Bot, telegram_id: int) -> int:
    """Снести все залипшие invoice-экраны юзера (в т.ч. native TG-инвойсы).

    Используется когда юзер стартует новый flow (/buy, «Купить трафик»
    и т.п.): чтобы предыдущий open-invoice не мозолил глаз рядом с
    новым экраном выбора тарифа/пакета.  Возвращает число снесённых
    записей."""
    stale = [
        (pid, entry) for pid, entry in list(_invoice_messages.items())
        if entry and entry[0] == telegram_id
    ]
    if not stale:
        return 0
    for pid, (chat_id, message_id) in stale:
        _invoice_messages.pop(pid, None)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("stale invoice cleanup: chat=%s msg=%s err=%s", chat_id, message_id, e)
    return len(stale)


@payments_router.callback_query(F.data == "topup_balance")
async def callback_topup_balance(callback: CallbackQuery, state: FSMContext = None):
    """Пополнить баланс"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    # «Отмена» of the custom-amount input lands here: leave that input state.
    if state is not None and await state.get_state() == TopUpStates.waiting_for_amount:
        await state.clear()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Показываем экран выбора суммы
    balance = await database.get_user_balance(telegram_id)
    text = i18n_get_text(language, "main.topup_balance_select_amount", balance=f"{balance:.2f}")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="250 ₽",
            callback_data="topup_amount:250",
            icon_custom_emoji_id=CE["wallet"],
            style="success",
        )],
        [InlineKeyboardButton(
            text="750 ₽",
            callback_data="topup_amount:750",
            icon_custom_emoji_id=CE["wallet"],
            style="success",
        )],
        [InlineKeyboardButton(
            text="999 ₽",
            callback_data="topup_amount:999",
            icon_custom_emoji_id=CE["wallet"],
            style="success",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.topup_custom_amount"),
            callback_data="topup_custom",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_profile",
            icon_custom_emoji_id=CE["back"],
            style="primary",
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
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    amount_str = parts[1]
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
    
    # One method list for preset and custom amounts: one button per cash desk,
    # SBP only with Platega and with its real markup (08 #12, #14, #20).
    from app.handlers.common.payment_labels import topup_method_rows
    buttons = topup_method_rows(language, amount)
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="topup_balance",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@payments_router.callback_query(F.data.startswith("topup_stars:"))
async def callback_topup_stars(callback: CallbackQuery):
    """Оплата пополнения баланса через Telegram Stars"""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
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

    # Конвертируем рубли в Stars (+70% наценка)
    # amount — рубли, конвертируем: amount * 1.7 / 1.85 (примерный курс), округляем вверх
    stars_amount = math.ceil(amount * 1.7 / 1.85)

    timestamp = int(time.time())
    payload = f"balance_topup_{telegram_id}_{amount}_{timestamp}"

    try:
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=i18n_get_text(language, "main.topup_invoice_title"),
            description=i18n_get_text(language, "main.topup_invoice_description", amount=amount),
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=i18n_get_text(language, "payment.stars_invoice_label"), amount=stars_amount)]
        )
        await callback.bot.send_message(chat_id=telegram_id, text=i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending Stars invoice for balance topup: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


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
    
    # Отправляем сообщение с инструкцией (+ «Отмена», 08 M17: there was no way out)
    text = i18n_get_text(language, "main.topup_enter_amount")
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=i18n_get_text(language, "payment.btn_cancel"),
        callback_data="topup_balance",
        style="primary",
    )]])
    await callback.message.answer(text, reply_markup=cancel_kb, parse_mode="HTML")


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
        # 08 M17: an alert cannot carry a button — a short message with «Пополнить баланс».
        try:
            await callback.message.answer(
                i18n_get_text(language, "errors.insufficient_balance_topup_hint"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text=i18n_get_text(language, "main.btn_topup_balance"),
                    callback_data="topup_balance",
                    icon_custom_emoji_id=CE["wallet"],
                    style="success",
                )]]),
                parse_mode="HTML",
            )
        except Exception as hint_err:  # noqa: BLE001
            logger.debug("insufficient-balance hint failed: %s", hint_err)
        logger.info(f"Insufficient balance for payment: user={telegram_id}, balance={balance_rubles:.2f} RUB, required={final_price_rubles:.2f} RUB")
        return
    
    # P0 guard (9c497027) on the balance path too: a Combo flag in FSM with a price
    # below the Combo price (stale / forged state) is refused before any debit.
    if (fsm_data.get("combo_bypass_gb") or 0) > 0:
        _promo = await get_promo_session(state)
        try:
            await subscription_service.ensure_combo_price_not_below(
                telegram_id, tariff_type, period_days, final_price_kopecks,
                promo_code=_promo.get("promo_code") if _promo else None,
                combo_offer_key=fsm_offer_key(fsm_data),
            )
        except subscription_service.InvalidTariffError:
            await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
            await state.set_state(None)
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
    
    # КРИТИЧНО: processing_payment ставится ДО callback.answer() — раньше
    # второй колбэк двойного тапа проскакивал проверку выше, пока шёл
    # answer(), и баланс списывался дважды (независимо от флага outbox).
    await state.set_state(PurchaseState.processing_payment)
    try:
        await callback.answer()
    except Exception as e:
        logger.warning("BALANCE_PAY_CALLBACK_ANSWER_FAILED user=%s err=%s", telegram_id, e)

    # КРИТИЧНО: Формируем данные для активации подписки
    months = period_days // 30
    if tariff_type == "basic":
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
        promo_code_from_session = await get_applied_promo_code(state)  # only a code that won
        
        result = await database.finalize_balance_purchase(
            telegram_id=telegram_id,
            tariff_type=tariff_type,
            period_days=period_days,
            amount_rubles=final_price_rubles,
            description=transaction_description,
            promo_code=promo_code_from_session,  # CRITICAL: Промокод потребляется внутри транзакции
            # T10: combo → тариф combo_* в outbox (при выключенном флаге игнорируется)
            is_combo=(fsm_data.get("combo_bypass_gb") or 0) > 0,
        )

        if not result or not result.get("success"):
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.message.answer(error_text, parse_mode="HTML")
            await state.set_state(None)
            return

        # T10: finalize пошёл через provisioning outbox — premium, ГБ и is_combo
        # уже в транзакции списания; панель здесь не трогаем.
        _outbox = bool(result.get("provisioning_job_id"))
        if _outbox:
            _outbox_combo_gb = fsm_data.get("combo_bypass_gb") or 0
            if _outbox_combo_gb > 0:
                # Ledger row once, before any early return below.
                try:
                    await database.record_traffic_purchase(telegram_id, _outbox_combo_gb, 0)
                except Exception as ledger_err:
                    logger.warning(
                        "COMBO_TRAFFIC_LEDGER_FAIL_BALANCE user=%s gb=%s err=%s",
                        telegram_id, _outbox_combo_gb, ledger_err,
                    )

        # Оплата прошла — сносим экран выбора способа оплаты, чтобы
        # юзер остался с одним активным сообщением-подтверждением.
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete payment-method (balance) failed: %s", _e)

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
        # The referrer is notified by the cashback accrual itself, once, after
        # the billing transaction commits (app.services.notifications.referral_cashback).

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
                    callback_data="menu_profile",
                    icon_custom_emoji_id=CE["profile"],
                    style="primary",
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
        # КРИТИЧНО: Читаем combo данные из FSM ДО очистки
        _combo_gb_from_fsm = 0
        _bypass_gb_from_fsm = 0
        try:
            _pre_clear_fsm = await state.get_data()
            _combo_gb_from_fsm = _pre_clear_fsm.get("combo_bypass_gb", 0)
            _bypass_gb_from_fsm = _pre_clear_fsm.get("bypass_only_gb", 0)
        except Exception:
            pass

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
        
        # One success message for every payment path (08_payments_ux #3): tariff
        # incl. Combo (was «Тариф: Basic» for Combo, #17), period, end date, GB,
        # connect keyboard in the user's language; admin dashboard text if set.
        from app.services.payments.success_message import build_purchase_success
        text, keyboard = await build_purchase_success(
            language,
            subscription_type=subscription_type,
            is_combo=_combo_gb_from_fsm > 0,
            period_days=period_days,
            expires_at=expires_at,
            is_renewal=is_renewal,
            is_upgrade=is_upgrade,
            telegram_id=telegram_id,
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

        try:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            logger.info(
                f"NOTIFICATION_SENT [type=balance_purchase, payment_id={payment_id}, user={telegram_id}, "
                f"scenario={'renewal' if is_renewal else 'first_purchase'}]"
            )
        except Exception as e_send:
            logger.error(f"Failed to send success message: user={telegram_id}, error={e_send}")
        
        logger.info(
            f"Subscription activated from balance: user={telegram_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"amount={final_price_rubles:.2f} RUB, "
            f"scenario={'renewal' if is_renewal else 'first_purchase'}"
        )

        if _outbox:
            # T10: no renew_remnawave_user_bg / add_bypass_traffic / FSM GB top-up —
            # the outbox job (balance:{payment_id}) owns premium + GB.
            if _bypass_gb_from_fsm > 0:
                # bypass_only_gb is set nowhere in the bot today; never top up here.
                logger.error(
                    "BALANCE_BYPASS_ONLY_IGNORED_OUTBOX user=%s gb=%s payment_id=%s",
                    telegram_id, _bypass_gb_from_fsm, payment_id,
                )
        else:
            # Fire-and-forget: create or renew Remnawave bypass user.
            # Not for Combo (like the Telegram path): Combo gets its table GB
            # below (add_bypass_traffic), no base 10 GB — and both are
            # read-modify-write on the same bypass limit, so running them
            # together raced (T0-BAL-COMBO-RENEW: Combo + 10, or one lost).
            try:
                from app.services.remnawave_service import renew_remnawave_user_bg
                if expires_at and subscription_type != "trial" and _combo_gb_from_fsm <= 0:
                    renew_remnawave_user_bg(telegram_id, subscription_type, expires_at, period_days=period_days)
            except Exception as rmn_err:
                logger.warning("REMNAWAVE_HOOK_FAIL: balance tg=%s %s", telegram_id, rmn_err)

            # Combo/Bypass: начисляем трафик обхода если покупка через комбо или bypass-only
            combo_bypass_gb = _combo_gb_from_fsm
            bypass_only_gb = _bypass_gb_from_fsm

            if combo_bypass_gb > 0 or bypass_only_gb > 0:
                from app.services import remnawave_service
                gb = combo_bypass_gb or bypass_only_gb
                traffic_bytes = gb * 1024**3

                try:
                    rmn_success = await remnawave_service.add_bypass_traffic(
                        telegram_id,
                        traffic_bytes,
                        subscription_type=subscription_type,
                        subscription_end=expires_at,
                        period_days=period_days,
                    )
                    if not rmn_success:
                        logger.warning(f"COMBO_BYPASS_TRAFFIC_FAIL_BALANCE user={telegram_id} gb={gb}")
                    await database.record_traffic_purchase(telegram_id, gb, 0)
                    logger.info(f"COMBO_BYPASS_TRAFFIC_ADDED_BALANCE user={telegram_id} gb={gb}")
                except Exception as traffic_err:
                    logger.warning(f"COMBO_BYPASS_TRAFFIC_ERROR_BALANCE user={telegram_id}: {traffic_err}")

                # Mark subscription as combo (OUTSIDE traffic try block)
                if combo_bypass_gb > 0:
                    try:
                        await database.set_combo_flag(telegram_id, True)
                        logger.info(f"COMBO_FLAG_SET_BALANCE user={telegram_id}")
                    except Exception as flag_err:
                        logger.warning(f"COMBO_FLAG_FAIL_BALANCE user={telegram_id}: {flag_err}")

                # Bypass-only: mark flag + activate trial if available.
                # T15: before, activate_trial did not exist (AttributeError
                # swallowed → no trial). Now only under the "trial" outbox flag;
                # after the purchase commit, never raises (log + admin alert).
                if bypass_only_gb > 0:
                    try:
                        await database.set_bypass_only_flag(telegram_id, True)
                    except Exception:
                        pass
                    if provisioning_flags.is_on("trial"):
                        from app.services.trials import service as trial_service
                        await trial_service.activate_trial_safely(
                            telegram_id, bot=callback.bot, where=f"balance:{payment_id}",
                        )

    except database.DuplicateBalancePurchase as e:
        # P1-2: second tap of a double tap — refused before any debit. The first
        # tap is still finishing: leave the FSM to it, no admin alert.
        logger.warning("BALANCE_PAY_DUPLICATE_TAP user=%s: %s", telegram_id, e)
        try:
            await callback.answer(
                i18n_get_text(language, "errors.session_expired_processing"), show_alert=True,
            )
        except Exception as answer_err:  # already answered above — nothing else to show
            logger.debug("BALANCE_PAY_DUPLICATE_ANSWER_FAILED user=%s: %s", telegram_id, answer_err)
    except Exception as e:
        logger.exception(f"CRITICAL: Unexpected error in callback_pay_balance: {e}")
        if isinstance(e, ValueError) and "PROMO" in str(e).upper():
            # 08 #13: the promo code ran out between the price screen and the
            # payment — say so (was «Ошибка обработки платежа»), drop the session.
            await clear_promo_session(state)
            try:
                await callback.message.answer(
                    i18n_get_text(language, "errors.promo_no_longer_valid"), parse_mode="HTML",
                )
            except Exception as msg_err:  # noqa: BLE001
                logger.debug("promo-invalid message failed: %s", msg_err)
        else:
            error_text = i18n_get_text(language, "errors.payment_processing")
            await callback.answer(error_text, show_alert=True)
        await state.set_state(None)
        # ValueError = insufficient balance / bad input: user-side, nothing debited.
        # Anything else is a failed money transaction → the admin must know
        # (docs/audit/03_payment_matrix.md, alert coverage).
        if not isinstance(e, ValueError):
            try:
                from app.services.admin_alerts import alert_payment_failure
                await alert_payment_failure(
                    callback.bot, "balance", telegram_id, f"balance:{tariff_type}_{period_days}", e,
                    is_transient=False, amount_rubles=final_price_rubles,
                    tariff=tariff_type, period_days=period_days,
                )
            except Exception as alert_err:
                logger.warning("BALANCE_PAY_ALERT_FAILED user=%s err=%s", telegram_id, alert_err)


@payments_router.callback_query(F.data == "pay:card")
async def callback_pay_card(callback: CallbackQuery, state: FSMContext):
    """ЭКРАН 4B — Оплата картой (Telegram Payments / ЮKassa)

    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через Telegram Payments
    - Переводит в processing_payment
    """
    # «Банковская карта» → универсальный инвойс Wata (юзер сам выбирает
    # карта/СБП/T-Pay на странице Wata). Fallback на Telegram Payments,
    # если Wata не сконфигурирована.
    import wata_service
    if wata_service.is_enabled():
        return await callback_pay_wata(callback, state)

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

    # КРИТИЧНО: Получаем промо-сессию для сохранения в pending_purchase
    promo_session = await get_promo_session(state)
    promo_code = await get_applied_promo_code(state)  # only a code that won as the largest discount

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
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
            combo_offer_key=fsm_offer_key(fsm_data),
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
        tariff_name = _tariff_label(fsm_data, tariff_type, language)
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
        # Регистрируем нативный TG-invoice в общем реестре, чтобы
        # _send_confirmation снёс его при успешной оплате.
        _invoice_messages[purchase_id] = (telegram_id, invoice_msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete payment-method (card) failed: %s", _e)

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


@payments_router.callback_query(F.data == "pay:stars")
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

    if not tariff_type or not period_days:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM for stars: user={telegram_id}")
        await state.set_state(None)
        return

    # Цена в Stars: basic/plus — TARIFFS_STARS, combo — из рублёвой цены combo
    # (tariffs.stars_price). Раньше combo брал цену basic/plus (HOW_IT_WORKS P1-2).
    # Скидка (промокод, −15 %, офферы, персональная — действует наибольшая), с
    # которой экран показал «К оплате: N ₽», переводится в звёзды тем же правилом
    # RUB→Stars (tariffs.stars_for_purchase); раньше Stars брал полный прайс.
    from app.services import tariffs as _tariffs
    is_combo = (fsm_data.get("combo_bypass_gb") or 0) > 0
    final_price_kopecks = int(fsm_data.get("final_price_kopecks") or 0)
    try:
        tariff_key = _tariffs.tariff_key(tariff_type, is_combo)
        list_price_kopecks = _tariffs.renewal_price_rub(tariff_key, period_days) * 100
        is_discounted = 0 < final_price_kopecks < list_price_kopecks
        # pending_purchases.price_kopecks — рублёвая цена (не звёзды × 100):
        # successful_payment переводит оплаченные звёзды в эти рубли, поэтому
        # payments.amount, выручка и кэшбэк считаются в рублях.
        price_kopecks = final_price_kopecks if is_discounted else list_price_kopecks
        stars_price = _tariffs.stars_for_purchase(tariff_key, period_days, price_kopecks)
    except _tariffs.TariffConfigError as e:
        error_text = i18n_get_text(language, "errors.tariff")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Stars tariff not found: tariff={tariff_type}, period={period_days}, combo={is_combo}: {e}")
        return

    try:
        # Промокод пишется в покупку (и списывается при финализации) только когда
        # он и дал показанную цену — как у остальных способов оплаты.
        promo_code = await get_applied_promo_code(state) if is_discounted else None
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=price_kopecks,
            promo_code=promo_code,
            is_combo=is_combo,
            combo_offer_key=fsm_offer_key(fsm_data),
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
        tariff_name = _tariff_label(fsm_data, tariff_type, language)
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
        _invoice_messages[purchase_id] = (telegram_id, invoice_msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete payment-method (stars) failed: %s", _e)

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


async def _start_platega_payment(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    method: int,
    apply_markup,
    i18n_key: str,
    log_tag: str,
):
    """Common entry path for any Platega payment method (SBP / Card / Intl).

    `apply_markup(price_kopecks) -> price_kopecks` returns the price with the
    method's markup applied (returns the same value if markup is 0).
    `i18n_key` is the prefix used for {key}_waiting / {key}_pay_button /
    {key}_unavailable lookups.
    """
    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:{log_tag}: user={telegram_id}, state={current_state}")
        await state.set_state(None)
        return

    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")

    promo_session = await get_promo_session(state)
    promo_code = await get_applied_promo_code(state)  # only a code that won as the largest discount

    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM for {log_tag}: user={telegram_id}")
        await state.set_state(None)
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, f"payment.{i18n_key}_unavailable"), show_alert=True)
        logger.error("Platega not configured")
        return

    try:
        marked_price_kopecks = apply_markup(final_price_kopecks)

        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=marked_price_kopecks,
            promo_code=promo_code,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
            combo_offer_key=fsm_offer_key(fsm_data),
        )

        await state.update_data(purchase_id=purchase_id)

        logger.info(
            f"Purchase created for {log_tag} payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"base_price={final_price_kopecks}, marked_price={marked_price_kopecks}"
        )

        marked_price_rubles = marked_price_kopecks / 100.0

        tx_data = await platega_service.create_transaction(
            amount_rubles=marked_price_rubles,
            description=f"Atlas Secure VPN — {_tariff_label(fsm_data, tariff_type, 'en')} {period_days}d",
            purchase_id=purchase_id,
            method=method,
            telegram_id=telegram_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id), provider="platega")
        except Exception as e:
            logger.error(f"Failed to save transaction_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"invoice_created: provider=platega, method={method}, user={telegram_id}, "
            f"purchase_id={purchase_id}, transaction_id={transaction_id}, "
            f"price={marked_price_rubles:.2f}"
        )

        text = i18n_get_text(language, f"payment.{i18n_key}_waiting", amount=marked_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, f"payment.{i18n_key}_pay_button"),
                url=redirect_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                # То же поведение что и на Wata-экране: назад → выбор
                # периода того же тарифа, а не в главное меню.
                callback_data=_invoice_back(fsm_data, tariff_type),
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )]
        ])

        msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        _invoice_messages[purchase_id] = (telegram_id, msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg))
        # Убираем экран выбора способа оплаты — юзер смотрит только на
        # активный invoice, чат не забит устаревшими экранами.
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete payment-method screen (%s) failed: %s", log_tag, _e)
        await callback.answer()

        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating Platega {log_tag} transaction: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)


@payments_router.callback_query(F.data == "pay:card_pl")
async def callback_pay_card_pl(callback: CallbackQuery, state: FSMContext):
    """Оплата банковской картой через Platega (paymentMethod=11)."""
    import platega_service
    await _start_platega_payment(
        callback, state,
        method=platega_service.PAYMENT_METHOD_CARD,
        apply_markup=platega_service.apply_card_markup,
        i18n_key="card_pl",
        log_tag="card_pl",
    )


@payments_router.callback_query(F.data == "pay:intl_pl")
async def callback_pay_intl_pl(callback: CallbackQuery, state: FSMContext):
    """Международные платежи через Platega (paymentMethod=12)."""
    import platega_service
    await _start_platega_payment(
        callback, state,
        method=platega_service.PAYMENT_METHOD_INTL,
        apply_markup=platega_service.apply_intl_markup,
        i18n_key="intl_pl",
        log_tag="intl_pl",
    )


@payments_router.callback_query(F.data == "pay:sbp")
async def callback_pay_sbp(callback: CallbackQuery, state: FSMContext):
    """Оплата через СБП. Провайдер (Platega / Wata) выбирается через
    runtime-настройку в дашборде (см. app.services.sbp_router).

    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase с ценой (+11% при Platega SBP)
    - Создает транзакцию у выбранного провайдера
    - Отправляет payment URL пользователю
    """
    telegram_id = callback.from_user.id

    # Живой выбор провайдера — прозрачно для пользователя.
    from app.services import sbp_router
    provider = await sbp_router.resolve_provider(telegram_id)
    if provider == "wata":
        logger.info(f"sbp_router: user {telegram_id} → wata (pay:sbp)")
        return await callback_pay_wata(callback, state)

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
        logger.warning(f"Invalid FSM state for pay:sbp: user={telegram_id}, state={current_state}")
        await state.set_state(None)
        return

    # КРИТИЧНО: Получаем данные из FSM state
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")

    # Получаем промо-сессию
    promo_session = await get_promo_session(state)
    promo_code = await get_applied_promo_code(state)  # only a code that won as the largest discount

    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM for sbp: user={telegram_id}")
        await state.set_state(None)
        return

    # Проверяем доступность Platega
    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        logger.error("Platega not configured")
        return

    try:
        # Применяем наценку +11% для СБП
        sbp_price_kopecks = platega_service.apply_sbp_markup(final_price_kopecks)

        # Создаем pending_purchase с ценой СБП (+11%)
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=sbp_price_kopecks,
            promo_code=promo_code,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
            combo_offer_key=fsm_offer_key(fsm_data),
        )

        await state.update_data(purchase_id=purchase_id)

        logger.info(
            f"Purchase created for SBP payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, "
            f"base_price={final_price_kopecks}, sbp_price={sbp_price_kopecks}"
        )

        sbp_price_rubles = sbp_price_kopecks / 100.0

        # Создаем транзакцию через Platega API
        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Atlas Secure VPN — {_tariff_label(fsm_data, tariff_type, 'en')} {period_days}d",
            purchase_id=purchase_id,
            telegram_id=telegram_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        # Сохраняем invoice_id в БД
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id), provider="platega")
        except Exception as e:
            logger.error(f"Failed to save transaction_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"invoice_created: provider=platega, user={telegram_id}, purchase_id={purchase_id}, "
            f"transaction_id={transaction_id}, sbp_price={sbp_price_rubles:.2f}"
        )

        # Отправляем пользователю ссылку на оплату
        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data=_invoice_back(fsm_data, tariff_type),
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )]
        ])

        msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        # 08 M15: registered like every other invoice screen — removed after the
        # payment (and after the timeout); the payment-method screen goes too.
        _invoice_messages[purchase_id] = (telegram_id, msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg))
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete payment-method (sbp) failed: %s", _e)
        await callback.answer()

        # Очищаем FSM state
        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating Platega SBP transaction: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)


@payments_router.callback_query(F.data == "pay:crypto")
async def callback_pay_crypto(callback: CallbackQuery, state: FSMContext):
    """Оплата через CryptoBot (криптовалюта)

    КРИТИЧНО:
    - Работает ТОЛЬКО в состоянии choose_payment_method
    - Создает pending_purchase
    - Создает invoice через CryptoBot API (fiat=RUB)
    - Отправляет payment URL пользователю
    """
    telegram_id = callback.from_user.id

    # Rate limiting
    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    # КРИТИЧНО: Проверяем FSM state
    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.warning(f"Invalid FSM state for pay:crypto: user={telegram_id}, state={current_state}")
        await state.set_state(None)
        return

    # Получаем данные из FSM state
    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")

    promo_session = await get_promo_session(state)
    promo_code = await get_applied_promo_code(state)  # only a code that won as the largest discount

    if not tariff_type or not period_days or not final_price_kopecks:
        error_text = i18n_get_text(language, "errors.session_expired")
        await callback.answer(error_text, show_alert=True)
        logger.error(f"Missing purchase data in FSM for crypto: user={telegram_id}")
        await state.set_state(None)
        return

    # Проверяем доступность CryptoBot
    import cryptobot_service
    if not cryptobot_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
        logger.error("CryptoBot not configured")
        return

    try:
        final_price_rubles = final_price_kopecks / 100.0

        # Создаем pending_purchase
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=final_price_kopecks,
            promo_code=promo_code,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
            combo_offer_key=fsm_offer_key(fsm_data),
        )

        await state.update_data(purchase_id=purchase_id, payment_method="crypto")

        logger.info(
            f"Purchase created for crypto payment: user={telegram_id}, purchase_id={purchase_id}, "
            f"tariff={tariff_type}, period_days={period_days}, price={final_price_rubles}"
        )

        # Формируем описание
        months = period_days // 30
        tariff_name = _tariff_label(fsm_data, tariff_type, language)

        description = f"Atlas Secure VPN — {tariff_name} {months}m"

        # Создаем invoice через CryptoBot API
        invoice_data = await cryptobot_service.create_invoice(
            amount_rubles=final_price_rubles,
            description=description,
            purchase_id=purchase_id,
        )

        invoice_id = invoice_data["invoice_id"]
        pay_url = invoice_data["pay_url"]

        # Сохраняем invoice_id в БД
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id), provider="cryptobot")
        except Exception as e:
            logger.error(f"Failed to save cryptobot invoice_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"invoice_created: provider=cryptobot, user={telegram_id}, purchase_id={purchase_id}, "
            f"invoice_id={invoice_id}, price={final_price_rubles:.2f}"
        )

        # Отправляем пользователю ссылку на оплату
        text = i18n_get_text(language, "payment.crypto_waiting", amount=final_price_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.crypto_pay_button"),
                url=pay_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data=_invoice_back(fsm_data, tariff_type),
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )]
        ])

        msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        _invoice_messages[purchase_id] = (telegram_id, msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg))
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete payment-method (crypto) failed: %s", _e)
        await callback.answer()

        # Очищаем FSM state
        await state.set_state(None)
        await state.clear()

    except Exception as e:
        logger.exception(f"Error creating CryptoBot invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)


@payments_router.callback_query(F.data == "pay:wata")
async def callback_pay_wata(callback: CallbackQuery, state: FSMContext):
    """Оплата подписки через Wata (admin-only beta).

    Тот же FSM-flow, что и остальные pay:*, но через wata_service.
    Итог: payment_url открывается в новой вкладке, webhook /webhooks/wata
    финализирует через generic process_confirmed_payment.
    """
    telegram_id = callback.from_user.id
    import wata_service
    if not wata_service.is_visible_to(telegram_id):
        await callback.answer(
            i18n_get_text(await resolve_user_language(telegram_id), "payment.wata_beta_only"), show_alert=True,
        )
        return

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
    language = await resolve_user_language(telegram_id)

    current_state = await state.get_state()
    if current_state != PurchaseState.choose_payment_method:
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.set_state(None)
        return

    fsm_data = await state.get_data()
    tariff_type = fsm_data.get("tariff_type")
    period_days = fsm_data.get("period_days")
    final_price_kopecks = fsm_data.get("final_price_kopecks")
    promo_session = await get_promo_session(state)
    promo_code = await get_applied_promo_code(state)  # only a code that won as the largest discount

    if not (tariff_type and period_days and final_price_kopecks):
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.set_state(None)
        return

    try:
        final_price_rubles = final_price_kopecks / 100.0
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=tariff_type,
            period_days=period_days,
            price_kopecks=final_price_kopecks,
            promo_code=promo_code,
            is_combo=fsm_data.get("combo_bypass_gb", 0) > 0,
            combo_offer_key=fsm_offer_key(fsm_data),
        )
        await state.update_data(purchase_id=purchase_id, payment_method="wata")

        months = period_days // 30
        tariff_name = _tariff_label(fsm_data, tariff_type, language)
        comment = f"Atlas Secure VPN — {tariff_name} {months}m"

        invoice = await wata_service.create_invoice(
            amount_rubles=final_price_rubles,
            purchase_id=purchase_id,
            comment=comment,
            user_id=telegram_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice["invoice_id"]), provider="wata")
        except Exception as e:
            logger.error(f"Failed to save wata invoice_id: {e}")

        logger.info(
            f"invoice_created: provider=wata, user={telegram_id}, purchase_id={purchase_id}, price={final_price_rubles:.2f}",
        )
        # 08 M8/M9: WATA is card / SBP / T-Pay — no longer «Оплата через СБП», and RU/EN.
        text = i18n_get_text(language, "payment.wata_waiting", amount=f"{final_price_rubles:.2f}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.wata_pay_button", amount=f"{final_price_rubles:.0f}"),
                url=invoice["payment_url"],
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.wata_check_button"),
                callback_data=f"pay:wata:check:{purchase_id}",
                style="success",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                # Назад с экрана «Оплата через СБП» ведёт обратно на выбор
                # периода для того же тарифа — экран периода живёт под
                # хендлером callback_tariff_type (F.data.startswith("tariff:")).
                callback_data=_invoice_back(fsm_data, tariff_type),
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )],
        ])
        msg = await callback.message.answer_photo(
            photo=_WATA_INVOICE_PHOTO_ID,
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        _invoice_messages[purchase_id] = (telegram_id, msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg))
        # Fast-path per-invoice polling: закрывает окно между Wata Paid и
        # приходом webhook'а (иногда 1-3 минуты). Гнать не чаще 30 сек —
        # это лимит Wata GET /links/{id}. Идемпотентность — row-level
        # lock в mark_pending_purchase_paid.
        asyncio.create_task(_poll_wata_invoice(
            callback.bot,
            telegram_id=telegram_id,
            purchase_id=purchase_id,
            invoice_id=str(invoice["invoice_id"]),
        ))
        # Удаляем экран выбора способа оплаты — теперь юзер смотрит только
        # на активный invoice.
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete payment-method screen failed: %s", _e)
        await callback.answer()
        await state.set_state(None)
        await state.clear()
    except Exception as e:
        logger.exception(f"Error creating Wata invoice: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
        await state.set_state(None)


# ── Fast-path per-invoice polling ───────────────────────────────────
#
# После создания Wata-инвойса запускаем фоновый polling: раз в 35 сек
# (>30-секундного лимита Wata GET /links/{id}) до финализации либо
# 10-минутного таймаута.  Устраняет 1-3-минутную задержку между Wata
# "Paid" и приходом webhook'а.  Reconciler (5 мин) остаётся защитой
# от рестарта контейнера — задачи в памяти теряются при рестарте.
#
# Идемпотентно: row-level lock в mark_pending_purchase_paid
# (UPDATE ... WHERE status='pending' RETURNING) гарантирует single-writer,
# даже если webhook, fast-poll и reconciler прилетят одновременно.
_WATA_POLL_INITIAL_DELAY_SEC = 40.0
_WATA_POLL_INTERVAL_SEC = 35.0
_WATA_POLL_MAX_ATTEMPTS = 15  # 15 × 35s ≈ 8.75 мин активного poll'а


async def _poll_wata_invoice(
    bot: Bot,
    *,
    telegram_id: int,
    purchase_id: str,
    invoice_id: str,
) -> None:
    """Фоновый poll конкретного Wata-инвойса до финализации или таймаута.

    Оплату ищем документированным GET /v2/transactions/?orderId=<purchase_id>
    (wata_reconciler.resolve_wata_payment: сверка суммы/валюты для VPN,
    429/сеть = «повторить позже»). invoice_id оставлен в сигнатуре для
    совместимости вызовов (traffic.py) и как fallback tx_id.
    """
    from app.workers import wata_reconciler as _wr
    from app.services.payments.confirmation import process_confirmed_payment

    try:
        await asyncio.sleep(_WATA_POLL_INITIAL_DELAY_SEC)
    except asyncio.CancelledError:
        return

    for attempt in range(_WATA_POLL_MAX_ATTEMPTS):
        try:
            purchase = await database.get_pending_purchase_any_status(purchase_id)
            if not purchase:
                return
            if str(purchase.get("status") or "") != "pending":
                # Webhook / reconciler / кнопка «Проверить» опередили — выходим.
                return

            res = await _wr.resolve_wata_payment(purchase_id, purchase, bot=bot)
            if res["outcome"] == _wr.LOOKUP_MISMATCH:
                # Сумма/валюта не сошлись — админ уже оповещён, не финализируем.
                return
            if res["outcome"] == _wr.LOOKUP_PAID:
                amount = res["amount"]
                tx_id = res["tx_id"] or invoice_id
                logger.info(
                    "wata_fast_poll_finalizing: user=%s purchase=%s tx=%s amount=%.2f attempt=%d",
                    telegram_id, purchase_id, tx_id, amount, attempt + 1,
                )
                await process_confirmed_payment(
                    provider="wata",
                    purchase_id=purchase_id,
                    amount_rubles=float(amount),
                    invoice_id=str(tx_id),
                    telegram_id=telegram_id,
                    bot=bot,
                )
                return
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "wata_fast_poll error: user=%s purchase=%s attempt=%d err=%s",
                telegram_id, purchase_id, attempt + 1, e,
            )

        try:
            await asyncio.sleep(_WATA_POLL_INTERVAL_SEC)
        except asyncio.CancelledError:
            return


# ── "Проверить платёж" — принудительная сверка Wata invoice ────────────
#
# Кнопка на экране оплаты (см. callback_pay_wata). При клике: ищем Paid-
# транзакцию через GET /v2/transactions/?orderId=<purchase_id>
# (wata_reconciler.resolve_wata_payment) и финализируем через тот же
# process_confirmed_payment, что и webhook. Идемпотентно благодаря
# row-level lock в mark_pending_purchase_paid.
#
# Rate limit: 30 сек на (user, purchase). Wata API сам ограничивает 1
# GET / 30с на объект — совпадает по цифре (+ троттл в lookup_paid_transaction).
_WATA_CHECK_COOLDOWN_SEC = 30
_wata_check_last_at: dict[tuple[int, str], float] = {}
_wata_check_lock = asyncio.Lock()


@payments_router.callback_query(F.data.startswith("pay:wata:check:"))
async def callback_pay_wata_check(callback: CallbackQuery):
    """Принудительная проверка Wata платежа (пользовательская кнопка)."""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        purchase_id = callback.data.split(":", 3)[3]
    except IndexError:
        await callback.answer(i18n_get_text(language, "payment.wata_check_error"), show_alert=True)
        return
    if not purchase_id:
        await callback.answer(i18n_get_text(language, "payment.wata_check_error"), show_alert=True)
        return

    now = time.time()
    key = (telegram_id, purchase_id)
    async with _wata_check_lock:
        last = _wata_check_last_at.get(key, 0.0)
        elapsed = now - last
        if elapsed < _WATA_CHECK_COOLDOWN_SEC:
            wait = max(1, int(_WATA_CHECK_COOLDOWN_SEC - elapsed))
            await callback.answer(
                i18n_get_text(language, "payment.wata_check_cooldown", seconds=wait),
                show_alert=False,
            )
            return
        _wata_check_last_at[key] = now

    from app.workers import wata_reconciler as _wr
    from app.services.payments.confirmation import process_confirmed_payment

    try:
        purchase = await database.get_pending_purchase(
            purchase_id, telegram_id, check_expiry=False,
        )
    except Exception as e:
        logger.warning("wata_check: get_pending_purchase failed purchase=%s err=%s", purchase_id, e)
        purchase = None

    if not purchase:
        # Row moved out of 'pending' (already processed) or doesn't belong to us.
        await callback.answer(
            i18n_get_text(language, "payment.wata_check_already"),
            show_alert=True,
        )
        try:
            await safe_edit_reply_markup(callback.message, None)
        except Exception:
            pass
        return

    # Документированный поиск по orderId (= purchase_id): GET
    # /v2/transactions/?orderId=… Сверка суммы/валюты для VPN — внутри.
    # 429 / сеть / троттл = «пока не видно оплаты», не ошибка.
    res = await _wr.resolve_wata_payment(purchase_id, purchase, bot=callback.bot)
    if res["outcome"] == _wr.LOOKUP_MISMATCH:
        await callback.answer(
            i18n_get_text(language, "payment.wata_check_error"),
            show_alert=True,
        )
        return
    if res["outcome"] != _wr.LOOKUP_PAID:
        await callback.answer(
            i18n_get_text(language, "payment.wata_check_not_paid"),
            show_alert=False,
        )
        return

    amount = res["amount"]
    tx_id = res["tx_id"]

    logger.info(
        "wata_check_user_initiated: user=%s purchase=%s tx=%s amount=%.2f",
        telegram_id, purchase_id, tx_id, amount,
    )

    try:
        result = await process_confirmed_payment(
            provider="wata",
            purchase_id=purchase_id,
            amount_rubles=float(amount),
            invoice_id=str(tx_id),
            telegram_id=telegram_id,
            bot=callback.bot,
        )
    except Exception as e:
        logger.exception("wata_check finalize failed purchase=%s: %s", purchase_id, e)
        await callback.answer(
            i18n_get_text(language, "payment.wata_check_error"),
            show_alert=True,
        )
        return

    outcome = (result or {}).get("status", "unknown")
    if outcome in ("ok", "already_processed"):
        toast_key = (
            "payment.wata_check_paid"
            if outcome == "ok"
            else "payment.wata_check_already"
        )
        await callback.answer(
            i18n_get_text(language, toast_key),
            show_alert=True,
        )
        try:
            await safe_edit_reply_markup(callback.message, None)
        except Exception:
            pass
    else:
        await callback.answer(
            i18n_get_text(language, "payment.wata_check_not_paid"),
            show_alert=False,
        )


@payments_router.callback_query(F.data.startswith("topup_sbp:"))
async def callback_topup_sbp(callback: CallbackQuery):
    """Пополнение баланса через СБП. Провайдер (Platega / Wata) выбирается
    через runtime-настройку в дашборде (см. app.services.sbp_router)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id

    # Живой выбор провайдера — прозрачно для пользователя.
    from app.services import sbp_router
    provider = await sbp_router.resolve_provider(telegram_id)
    if provider == "wata":
        logger.info(f"sbp_router: user {telegram_id} → wata (topup_sbp)")
        # topup_wata: ожидает те же данные из callback_data — подменяем
        # префикс через immutable-copy (CallbackQuery frozen).
        try:
            amount_part = callback.data.split(":", 1)[1]
        except IndexError:
            amount_part = "0"
        cb_wata = callback.model_copy(update={"data": f"topup_wata:{amount_part}"})
        return await callback_topup_wata(cb_wata)

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
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

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    try:
        # Наценка СБП (SBP_MARKUP_PERCENT): оплачивается, но на баланс
        # зачисляется только запрошенная сумма (решение владельца 2026-09-14).
        amount_kopecks = amount * 100
        sbp_amount_kopecks = platega_service.apply_sbp_markup(amount_kopecks)
        sbp_amount_rubles = sbp_amount_kopecks / 100.0

        purchase_id = await subscription_service.create_balance_topup_purchase(
            telegram_id=telegram_id,
            amount_kopecks=sbp_amount_kopecks,
            currency="RUB",
            credit_kopecks=amount_kopecks,
        )

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_amount_rubles,
            description=i18n_get_text(language, "main.topup_invoice_description", amount=amount),
            purchase_id=purchase_id,
            telegram_id=telegram_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id), provider="platega")
        except Exception as e:
            logger.error(f"Failed to save transaction_id to DB: purchase_id={purchase_id}, error={e}")

        logger.info(
            f"balance_topup_invoice_created: provider=platega, user={telegram_id}, "
            f"purchase_id={purchase_id}, base_amount={amount}, sbp_amount={sbp_amount_rubles:.2f}"
        )

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_amount_rubles)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="topup_balance",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )]
        ])

        msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        # Removed after the payment like the other invoice screens (08 #15).
        _invoice_messages[purchase_id] = (telegram_id, msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg))
        await callback.answer()

    except Exception as e:
        logger.exception(f"Error creating Platega SBP transaction for balance top-up: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@payments_router.callback_query(F.data.startswith("topup_wata:"))
async def callback_topup_wata(callback: CallbackQuery):
    """Пополнение баланса через Wata (карта/СБП/T-Pay). Admin-only beta."""
    if not await ensure_db_ready_callback(callback):
        return
    telegram_id = callback.from_user.id

    import wata_service
    if not wata_service.is_visible_to(telegram_id):
        await callback.answer(
            i18n_get_text(await resolve_user_language(telegram_id), "payment.wata_beta_only"), show_alert=True,
        )
        return

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(
            rate_limit_message or i18n_get_text(language, "common.rate_limit_message"),
            show_alert=True,
        )
        return
    language = await resolve_user_language(telegram_id)

    try:
        amount = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return
    if amount <= 0 or amount > 100000:
        await callback.answer(i18n_get_text(language, "errors.invalid_amount"), show_alert=True)
        return

    try:
        purchase_id = await subscription_service.create_balance_topup_purchase(
            telegram_id=telegram_id,
            amount_kopecks=amount * 100,
            currency="RUB",
        )
        invoice = await wata_service.create_invoice(
            amount_rubles=float(amount),
            purchase_id=purchase_id,
            comment=i18n_get_text(language, "main.topup_invoice_description", amount=amount),
            user_id=telegram_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice["invoice_id"]), provider="wata")
        except Exception as e:
            logger.error(f"Failed to save wata invoice_id: {e}")

        logger.info(
            f"balance_topup_invoice_created: provider=wata, user={telegram_id}, "
            f"purchase_id={purchase_id}, amount={amount}",
        )
        text = i18n_get_text(language, "payment.wata_waiting", amount=amount)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.wata_pay_button", amount=amount),
                url=invoice["payment_url"],
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.wata_check_button"),
                callback_data=f"pay:wata:check:{purchase_id}",
                style="success",
            )],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="topup_balance", icon_custom_emoji_id=CE["back"], style="primary")],
        ])
        msg = await callback.message.answer_photo(
            photo=_WATA_INVOICE_PHOTO_ID,
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        _invoice_messages[purchase_id] = (telegram_id, msg.message_id)
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg))
        asyncio.create_task(_poll_wata_invoice(
            callback.bot,
            telegram_id=telegram_id,
            purchase_id=purchase_id,
            invoice_id=str(invoice["invoice_id"]),
        ))
        try:
            await callback.message.delete()
        except Exception as _e:
            logger.debug("delete topup-picker screen failed: %s", _e)
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error creating Wata invoice for balance top-up: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@payments_router.callback_query(F.data.startswith("topup_card:"))
async def callback_topup_card(callback: CallbackQuery):
    """Оплата пополнения баланса картой"""
    if not await ensure_db_ready_callback(callback):
        return

    # «Банковская карта» → универсальный инвойс Wata. Fallback на карту, если выкл.
    import wata_service
    if wata_service.is_enabled():
        return await callback_topup_wata(callback)

    telegram_id = callback.from_user.id

    is_allowed, rate_limit_message = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(rate_limit_message or i18n_get_text(language, "common.rate_limit_message"), show_alert=True)
        return
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
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=i18n_get_text(language, "main.topup_invoice_title"),
            description=i18n_get_text(language, "main.topup_invoice_description", amount=amount),
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=i18n_get_text(language, "main.topup_invoice_label"), amount=amount_kopecks)]
        )
        await callback.bot.send_message(chat_id=telegram_id, text=i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg))
        await callback.answer()
    except Exception as e:
        logger.exception(f"Error sending invoice for balance topup: {e}")
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


