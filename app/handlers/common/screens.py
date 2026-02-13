"""
Pure presentation screen helpers. Reusable for callbacks and message commands.
No router decorators, no handler-level logic — only rendering and keyboard building.
"""
import logging
from typing import Union

import database
from aiogram import Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.subscriptions.service import (
    get_subscription_status,
    check_and_disable_expired_subscription as check_subscription_expiry_service,
)
from app.handlers.common.utils import safe_edit_text, detect_platform
from app.handlers.common.keyboards import (
    get_about_keyboard,
    get_instruction_keyboard,
    get_support_keyboard,
    get_profile_keyboard,
)
from app.handlers.common.states import PurchaseState
from app.constants.loyalty import get_loyalty_screen_attachment

logger = logging.getLogger(__name__)


async def _open_about_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """О сервисе. Reusable for callback and /info command."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    title = i18n_get_text(language, "main.about_title")
    text = i18n_get_text(language, "main.about_text", "about_text")
    full_text = f"{title}\n\n{text}"
    await safe_edit_text(msg, full_text, reply_markup=get_about_keyboard(language), parse_mode="HTML", bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


async def _open_instruction_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """Инструкция. Reusable for callback and /instruction command."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    platform = detect_platform(event)
    text = i18n_get_text(language, "instruction._text", "instruction_text")
    await safe_edit_text(msg, text, reply_markup=get_instruction_keyboard(language, platform), bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


async def _open_support_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """Поддержка. Reusable for callback and /help command."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "main.support_text", "support_text")
    await safe_edit_text(msg, text, reply_markup=get_support_keyboard(language), bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


async def _open_referral_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """
    Экран «Программа лояльности». Reusable for callback and /referral command.
    Sends new message (photo or text), does not edit.
    """
    from datetime import datetime
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    
    async def _send_error(err_text: str):
        if isinstance(event, CallbackQuery):
            await event.answer(err_text, show_alert=True)
        else:
            await bot.send_message(chat_id, err_text)
    
    try:
        stats = await database.get_referral_statistics(telegram_id)
        total_invited = stats.get("total_invited", 0)
        active_paid_referrals = stats.get("active_paid_referrals", 0)
        total_cashback = stats.get("total_cashback_earned", 0.0)
        current_level_name = stats.get("current_level_name", "Silver Access")
        cashback_percent = stats.get("cashback_percent", 10)
        next_level_name = stats.get("next_level_name")
        remaining_connections = stats.get("remaining_connections", 0)
        last_activity_at = stats.get("last_activity_at")
        
        last_activity_str = "—"
        if last_activity_at:
            if isinstance(last_activity_at, str):
                try:
                    last_activity_at = datetime.fromisoformat(last_activity_at.replace("Z", "+00:00"))
                except Exception:
                    pass
            if isinstance(last_activity_at, datetime):
                last_activity_str = last_activity_at.strftime("%d.%m.%Y")
        
        # Формируем строку "До следующего уровня"
        if next_level_name and remaining_connections > 0:
            next_level_line = i18n_get_text(
                language,
                "referral.next_level_line",
                next_status_name=next_level_name,
                remaining_invites=remaining_connections
            )
        else:
            next_level_line = i18n_get_text(language, "referral.max_level_reached")
        
        # Новый формат текста с разделёнными метриками
        text = (
            f"{i18n_get_text(language, 'referral.screen_title')}\n\n"
            f"{i18n_get_text(language, 'referral.total_invited', count=total_invited)}\n"
            f"{i18n_get_text(language, 'referral.active_with_subscription', count=active_paid_referrals)}\n\n"
            f"{i18n_get_text(language, 'referral.current_status', status=current_level_name)}\n"
            f"{i18n_get_text(language, 'referral.cashback_level', percent=cashback_percent)}\n\n"
            f"{next_level_line}\n\n"
            f"{i18n_get_text(language, 'referral.rewards_earned', amount=total_cashback)}\n"
            f"{i18n_get_text(language, 'referral.last_activity', date=last_activity_str)}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "referral.share_button"),
                callback_data="share_referral_link"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "referral.stats_button"),
                callback_data="referral_stats"
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main"
            )],
        ])
        
        file_id = get_loyalty_screen_attachment(current_level_name)
        if file_id:
            await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=None,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )
        if isinstance(event, CallbackQuery):
            await event.answer()
        logger.debug(
            f"Referral screen opened: user={telegram_id}, "
            f"total_invited={total_invited}, active_paid={active_paid_referrals}, "
            f"level={current_level_name}, percent={cashback_percent}%, "
            f"cashback={total_cashback:.2f} RUB, remaining={remaining_connections}, with_photo={bool(file_id)}"
        )
    except Exception as e:
        logger.exception(f"Error in referral screen handler: user={telegram_id}: {e}")
        await _send_error(i18n_get_text(language, "errors.profile_load"))


async def show_profile(message_or_query, language: str):
    """Показать профиль пользователя (обновленная версия с балансом)"""
    telegram_id = None
    send_func = None

    try:
        if isinstance(message_or_query, Message):
            telegram_id = message_or_query.from_user.id
            send_func = message_or_query.answer
        else:
            telegram_id = message_or_query.from_user.id
            send_func = message_or_query.message.edit_text
    except AttributeError as e:
        logger.error(f"Invalid message_or_query type in show_profile: {type(message_or_query)}, error: {e}")
        raise

    # REAL-TIME EXPIRATION CHECK: Проверяем и отключаем истекшие подписки сразу
    if telegram_id:
        await check_subscription_expiry_service(telegram_id)

    try:
        # Дополнительная защита: проверка истечения подписки
        await check_subscription_expiry_service(telegram_id)

        # Получаем данные пользователя
        user = await database.get_user(telegram_id)
        if not user:
            logger.warning(f"User not found: {telegram_id}")
            error_text = i18n_get_text(language, "errors.profile_load")
            await send_func(error_text)
            return

        username = user.get("username") if user else None
        if not username:
            username = f"ID: {telegram_id}"

        # Получаем баланс
        balance_rubles = await database.get_user_balance(telegram_id)

        # Получаем информацию о подписке (активной или истекшей)
        subscription = await database.get_subscription_any(telegram_id)

        # Формируем текст профиля
        text = i18n_get_text(language, "profile.welcome_full", username=username, balance=round(balance_rubles, 2))

        # Определяем статус подписки используя subscription service
        subscription_status = get_subscription_status(subscription)
        has_active_subscription = subscription_status.is_active
        has_any_subscription = subscription_status.has_subscription
        activation_status = subscription_status.activation_status
        expires_at = subscription_status.expires_at

        # PART E.8: Profile logic - active + pending → show "Activation in progress"
        # PART E.8: NEVER show "no subscription" if activation_status=pending
        # PART E.9: Clear explanation, no contradictions
        if activation_status == "pending" or (has_any_subscription and activation_status == "pending"):
            # PART E.8: Show "Activation in progress" for pending activations
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            text += "\n" + i18n_get_text(language, "profile.subscription_pending", date=expires_str)
        elif has_active_subscription:
            # Подписка активна
            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
            text += "\n" + i18n_get_text(language, "profile.subscription_active", date=expires_str)
        else:
            # Подписка неактивна (истекла или отсутствует)
            text += "\n" + i18n_get_text(language, "profile.subscription_inactive")

        # Получаем статус автопродления и добавляем информацию
        auto_renew = False
        if subscription:
            auto_renew = subscription.get("auto_renew", False)

        # Добавляем информацию об автопродлении (только для активных подписок)
        if subscription_status.is_active:
            if auto_renew:
                # Автопродление включено - next_billing_date = expires_at
                if subscription_status.expires_at:
                    next_billing_str = subscription_status.expires_at.strftime("%d.%m.%Y")
                else:
                    next_billing_str = "N/A"
                text += "\n" + i18n_get_text(language, "profile.auto_renew_enabled", next_billing_date=next_billing_str)
            else:
                # Автопродление выключено
                text += "\n" + i18n_get_text(language, "profile.auto_renew_disabled")

        # Добавляем подсказку о продлении (для активных и истекших подписок - по требованиям)
        if has_any_subscription:
            text += "\n\n" + i18n_get_text(language, "profile.renewal_hint")

        # Добавляем подсказку о покупке, если подписки нет
        if not has_any_subscription:
            text += "\n\n" + i18n_get_text(language, "profile.buy_hint")

        # Показываем кнопку "Продлить доступ" если есть подписка (активная или истекшая) - по требованиям
        keyboard = get_profile_keyboard(language, has_any_subscription, auto_renew)

        # Отправляем сообщение
        await send_func(text, reply_markup=keyboard)

    except Exception as e:
        logger.exception(f"Error in show_profile for user {telegram_id}: {e}")
        # Пытаемся отправить сообщение об ошибке с безопасной обработкой
        try:
            error_text = i18n_get_text(language, "errors.profile_load")

            if isinstance(message_or_query, CallbackQuery):
                await message_or_query.message.answer(error_text)
            elif isinstance(message_or_query, Message):
                await message_or_query.answer(error_text)
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")
            # Последняя попытка - отправить простой текст без локализации
            try:
                language = await resolve_user_language(telegram_id)
                error_text = i18n_get_text(language, "errors.profile_load")
                if isinstance(message_or_query, CallbackQuery):
                    await message_or_query.message.answer(error_text)
                elif isinstance(message_or_query, Message):
                    await message_or_query.answer(error_text)
            except Exception as e3:
                logger.exception(f"Critical: Failed to send error message to user {telegram_id}: {e3}")


async def _open_buy_screen(event: Union[Message, CallbackQuery], bot: Bot, state: FSMContext):
    """
    Купить VPN - выбор типа тарифа (Basic/Plus). Reusable for callback and /buy command.
    
    CANONICAL TARIFF SCREEN BUILDER - единственный источник правды для экрана тарифов.
    Используется везде: после промокода, при нажатии "Купить доступ", и т.д.
    """
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    
    await state.update_data(purchase_id=None, tariff_type=None, period_days=None)
    await database.cancel_pending_purchases(telegram_id, "new_purchase_started")
    await state.set_state(PurchaseState.choose_tariff)
    
    text = (
        f"💎 Тарифы Atlas Secure\n\n\n"
        f"{i18n_get_text(language, 'buy.tariff_basic')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_plus')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_corporate')}"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.select_basic_button"),
            callback_data="tariff:basic"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.select_plus_button"),
            callback_data="tariff:plus"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.enter_promo"),
            callback_data="enter_promo"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.corporate_button"),
            callback_data="corporate_access_request"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])
    
    await safe_edit_text(msg, text, reply_markup=keyboard, bot=bot)
    if isinstance(event, CallbackQuery):
        await event.answer()


async def show_tariffs_main_screen(event: Union[Message, CallbackQuery], state: FSMContext):
    """
    CANONICAL TARIFF SCREEN - единый builder для экрана тарифов.
    
    Используется после применения промокода и везде, где нужно показать экран тарифов.
    Гарантирует единообразие UI и отсутствие дублирования кода.
    
    Args:
        event: Message или CallbackQuery объект
        state: FSM context
    """
    bot = event.bot if isinstance(event, CallbackQuery) else event.bot
    await _open_buy_screen(event, bot, state)
