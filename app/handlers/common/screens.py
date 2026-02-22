"""
Pure presentation screen helpers. Reusable for callbacks and message commands.
No router decorators, no handler-level logic — only rendering and keyboard building.
"""
import logging
from datetime import timedelta
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
from app.utils.date_utils import format_date_ru

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
    """Инструкция. Reusable for callback and /instruction command. Uses platform buttons and tariff-based copy keys."""
    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    platform = detect_platform(event)
    subscription = await database.get_subscription(telegram_id)
    subscription_type = "basic"
    vpn_key = None
    if subscription:
        subscription_type = (subscription.get("subscription_type") or "basic").strip().lower()
        vpn_key = subscription.get("vpn_key")
    if subscription_type not in ("basic", "plus"):
        subscription_type = "basic"
    text = i18n_get_text(language, "instruction._text", "instruction_text")
    await safe_edit_text(
        msg, text,
        reply_markup=get_instruction_keyboard(language, platform, subscription_type=subscription_type, vpn_key=vpn_key),
        bot=bot
    )
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

        from_user = message_or_query.from_user
        display_name = (getattr(from_user, "first_name", None) or from_user.username or user.get("first_name") or user.get("username") or "Пользователь")

        # Получаем баланс
        balance_rubles = await database.get_user_balance(telegram_id)
        balance_str = f"{balance_rubles:.2f}"

        # Получаем информацию о подписке (активной или истекшей)
        subscription = await database.get_subscription_any(telegram_id)
        subscription_status = get_subscription_status(subscription)
        has_active_subscription = subscription_status.is_active
        expires_at = subscription_status.expires_at

        auto_renew = bool(subscription and subscription.get("auto_renew"))
        sub_type = (subscription.get("subscription_type") or "basic").strip().lower() if subscription else "basic"
        if sub_type not in ("basic", "plus"):
            sub_type = "basic"

        # Карточка профиля: единый формат
        text = (
            "Добро пожаловать в Atlas Secure!\n\n"
            f"👤 {display_name}\n\n"
            f"💰 Баланс: {balance_str} ₽\n"
        )
        if has_active_subscription and expires_at:
            date_str = format_date_ru(expires_at)
            text += f"📆 Подписка: активна до {date_str}\n"
            text += f"⭐️ Тариф: {'Plus' if sub_type == 'plus' else 'Basic'}\n"
            if auto_renew and expires_at:
                renewal_window = timedelta(hours=6)
                next_renewal = expires_at - renewal_window
                text += f"🔁 Автопродление: {format_date_ru(next_renewal)}"
            else:
                text += "🔁 Автопродление: выкл"
        else:
            text += "📆 Подписка: не активна\n"
            text += "⭐️ Тариф: —\n"
            text += "🔁 Автопродление: —"
        text += "\n\nПри продлении выбранный срок\nдобавляется к текущему автоматически"
        vpn_key = subscription.get("vpn_key") if subscription else None
        vpn_key_plus = subscription.get("vpn_key_plus") if subscription else None
        keyboard = get_profile_keyboard(
            language, has_active_subscription, auto_renew,
            subscription_type=sub_type, vpn_key=vpn_key, vpn_key_plus=vpn_key_plus
        )

        await send_func(text, reply_markup=keyboard, parse_mode="HTML")

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
