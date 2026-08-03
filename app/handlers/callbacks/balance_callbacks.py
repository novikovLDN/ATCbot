"""Пополнение баланса и вывод средств.

ЧТО ЗДЕСЬ ЕСТЬ
    Экраны пополнения баланса всеми способами (карта, СБП, Lava, звёзды,
    произвольная сумма) и заявки на вывод средств с подтверждением админом.

БАЛАНС — ЭТО ДЕНЬГИ
    Пополнение зачисляется только после подтверждения провайдером, никогда
    по факту нажатия кнопки. Вывод проходит два шага: пользователь создаёт
    заявку, админ подтверждает или отклоняет. Списание при выводе делает
    сама заявка, а не обработчик кнопки — иначе двойное нажатие увело бы
    деньги дважды.

НАЦЕНКА ПРОВАЙДЕРА
    У части способов оплаты есть комиссия. Она должна закладываться в сумму
    к оплате, а не вычитаться из зачисления: иначе пользователь платит
    условные 1000 рублей, а на балансе видит 890 и пишет в поддержку.
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

balance_router = Router()
logger = logging.getLogger(__name__)

# Планировщик удаления инвойса общий со всеми платёжными экранами: инвойс
# живёт ограниченное время, и после истечения сообщение убирается, чтобы
# пользователь не нажал на протухшую кнопку оплаты.
from app.handlers.callbacks._invoice_cleanup import (  # noqa: E402
    _schedule_invoice_deletion,
)


@balance_router.callback_query(F.data == "topup_balance")
async def callback_topup_balance(callback: CallbackQuery):
    """Пополнить баланс"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return
    
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Показываем экран выбора суммы
    balance = await database.get_user_balance(telegram_id)
    text = i18n_get_text(language, "main.topup_balance_select_amount", balance=f"{balance:.2f}")
    
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


@balance_router.callback_query(F.data.startswith("topup_amount:"))
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
    
    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.pay_with_card"),
            callback_data=f"topup_card:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.sbp"),
            callback_data=f"topup_sbp:{amount}"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "payment.stars"),
            callback_data=f"topup_stars:{amount}"
        )],
    ]
    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.lava"),
            callback_data=f"topup_lava:{amount}"
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="topup_balance"
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
    await callback.answer()


@balance_router.callback_query(F.data.startswith("topup_stars:"))
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


@balance_router.callback_query(F.data == "topup_custom")
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
    
    await callback.message.answer(text, parse_mode="HTML")


@balance_router.callback_query(F.data == "withdraw_start")
async def callback_withdraw_start(callback: CallbackQuery, state: FSMContext):
    """Вывод средств — заглушка, направляем в поддержку"""
    await callback.answer(
        "Обратитесь в техподдержку для создания заявки на вывод средств.",
        show_alert=True,
    )


@balance_router.callback_query(F.data == "withdraw_confirm_amount", StateFilter(WithdrawStates.withdraw_confirm))
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


@balance_router.callback_query(F.data == "withdraw_final_confirm", StateFilter(WithdrawStates.withdraw_final_confirm))
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
    amount_kopecks = round(amount * 100)
    raw_username = callback.from_user.username
    sanitized_username = sanitize_display_name(raw_username) if raw_username else None
    wid = await database.create_withdrawal_request(telegram_id, sanitized_username or raw_username, amount_kopecks, requisites)
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

        # Разбивка по происхождению денег. Заявку одобряет человек, и он
        # обязан видеть, сколько на балансе намайнено в мини-играх: сама
        # заявка такие деньги уже не пропустит, но остаток на балансе всё
        # равно полезен при разборе спорных случаев.
        try:
            breakdown = await database.get_balance_breakdown(telegram_id)
            game_line = (
                f"🎮 Из них игровые: {breakdown['game_locked'] / 100:.2f} ₽ "
                f"(к выводу: {breakdown['withdrawable'] / 100:.2f} ₽)\n"
            ) if breakdown["game_locked"] > 0 else ""
        except Exception as breakdown_err:
            logger.warning("WITHDRAW_BREAKDOWN_FAILED user=%s: %s", telegram_id, breakdown_err)
            game_line = ""

        admin_text = (
            f"💸 Новая заявка на вывод #{wid}\n\n"
            f"👤 Пользователь: @{sanitized_username or '—'} (ID: {telegram_id})\n"
            f"📊 Баланс: {balance:.2f} ₽\n"
            f"{game_line}"
            f"💰 Сумма: {amount:.2f} ₽\n"
            f"📶 Подписка: {sub_text}\n"
            f"🏦 Реквизиты: {requisites[:200]}"
        )
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"withdraw_approve:{wid}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"withdraw_reject:{wid}")],
        ])
        await bot.send_message(config.ADMIN_TELEGRAM_ID, admin_text, reply_markup=admin_kb, parse_mode="HTML")
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


@balance_router.callback_query(F.data == "withdraw_cancel")
@balance_router.callback_query(F.data == "withdraw_back_to_amount")
@balance_router.callback_query(F.data == "withdraw_back_to_requisites")
async def callback_withdraw_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена или назад в выводе средств"""
    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    await show_profile(callback, language)
    await callback.answer()


@balance_router.callback_query(F.data.startswith("withdraw_approve:"))
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
                await bot.send_message(wr["telegram_id"], text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to send withdrawal approved notification to {wr['telegram_id']}: {e}")
            await callback.answer("✅ Подтверждено", show_alert=True)
            await safe_edit_reply_markup(callback.message, reply_markup=None)
        else:
            await callback.answer("Ошибка подтверждения", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in withdraw_approve: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)


@balance_router.callback_query(F.data.startswith("withdraw_reject:"))
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
                await bot.send_message(wr["telegram_id"], text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to send withdrawal rejected notification to {wr['telegram_id']}: {e}")
            await callback.answer("❌ Отклонено", show_alert=True)
            await safe_edit_reply_markup(callback.message, reply_markup=None)
        else:
            await callback.answer("Ошибка отклонения", show_alert=True)
    except Exception as e:
        logger.exception(f"Error in withdraw_reject: {e}")
        await callback.answer("Ошибка. Проверь логи.", show_alert=True)
