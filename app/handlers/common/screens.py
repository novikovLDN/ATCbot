"""
Pure presentation screen helpers. Reusable for callbacks and message commands.
No router decorators, no handler-level logic — only rendering and keyboard building.
"""
import logging
from datetime import timedelta
from typing import Union

import config
import database
from aiogram import Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text
from app.utils.referral_link import build_referral_link
from app.services.language_service import resolve_user_language
from app.services.subscriptions.service import (
    get_subscription_status,
    check_and_disable_expired_subscription as check_subscription_expiry_service,
)
from app.handlers.common.utils import safe_edit_text, sanitize_display_name
from app.handlers.common.keyboards import (
    get_about_keyboard,
    get_instruction_keyboard,
    get_profile_keyboard,
)
from app.handlers.common.states import PurchaseState
from app.constants.loyalty import get_loyalty_screen_attachment
from app.utils.date_utils import format_date_ru

logger = logging.getLogger(__name__)


async def _open_about_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """О сервисе. Reusable for callback and /info command."""
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass

    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    title = i18n_get_text(language, "main.about_title")
    text = i18n_get_text(language, "main.about_text", "about_text")
    full_text = f"{title}\n\n{text}"
    await safe_edit_text(msg, full_text, reply_markup=get_about_keyboard(language), parse_mode="HTML", bot=bot)


async def _open_instruction_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """Инструкция. Reusable for callback and /instruction command. Directs user to mini app guide."""
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass

    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "instruction._text", "instruction_text")
    await safe_edit_text(
        msg, text,
        reply_markup=get_instruction_keyboard(language),
        bot=bot
    )



async def _open_referral_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """
    Экран «Программа лояльности». Reusable for callback and /referral command.
    Sends new message (photo or text), does not edit.
    """
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass

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
        
        # Генерируем реферальную ссылку для share URL
        bot_info = await bot.get_me()
        referral_link = await build_referral_link(telegram_id, bot_info.username)
        from urllib.parse import quote
        share_url = f"https://t.me/share/url?url={quote(referral_link)}"

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
                url=share_url
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
        photo_sent = False
        if file_id:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=text,
                    reply_markup=keyboard,
                    parse_mode=None,
                )
                photo_sent = True
            except Exception as photo_err:
                logger.warning(f"Failed to send loyalty photo for user={telegram_id}, falling back to text: {photo_err}")
        if not photo_sent:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
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
        raw_name = getattr(from_user, "first_name", None) or from_user.username or user.get("first_name") or user.get("username")
        # Санитизация имени: запрещённые слова → «Пользователь»
        if raw_name:
            sanitized = sanitize_display_name(raw_name)
            display_name = sanitized if sanitized else i18n_get_text(language, "common.user")
        else:
            display_name = i18n_get_text(language, "common.user")

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
        if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
            sub_type = "basic"

        # Бизнес-профиль: специальный экран для biz_* подписок
        if config.is_biz_tariff(sub_type) and has_active_subscription:
            from app.handlers.common.keyboards import get_biz_profile_keyboard
            specs = config.BIZ_TIER_SPECS.get(sub_type, {})
            country_code = subscription.get("country") or "nl"
            country_info = config.BIZ_COUNTRIES.get(country_code, config.BIZ_COUNTRIES["nl"])
            tariff_names = {
                "biz_starter": "Starter", "biz_team": "Team", "biz_business": "Business",
                "biz_pro": "Pro", "biz_enterprise": "Enterprise", "biz_ultimate": "Ultimate",
            }
            tariff_label = tariff_names.get(sub_type, "Business")
            date_str = format_date_ru(expires_at)
            text = i18n_get_text(language, "biz.profile_title") + "\n\n"
            text += i18n_get_text(language, "biz.profile_welcome", name=display_name) + "\n\n"
            text += i18n_get_text(language, "biz.profile_info",
                date=date_str,
                tariff=tariff_label,
                balance=balance_str,
                country=f"{country_info['flag']} {country_info['name']}",
                cpu=specs.get("cpu", "?"),
                ram=specs.get("ram", "?"),
                traffic=specs.get("traffic", "?"),
            )
            keyboard = get_biz_profile_keyboard(language)
            try:
                await send_func(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception:
                await send_func(text, reply_markup=keyboard)
            return

        # Карточка профиля: единый формат (профиль + трафик)
        text = f"👤 {display_name}\n\n"
        text += f"{i18n_get_text(language, 'profile.balance', amount=balance_str)}\n"

        is_trial = sub_type == "trial"
        is_combo = subscription.get("is_combo", False) if subscription else False
        is_bypass_only = subscription.get("is_bypass_only", False) if subscription else False

        if has_active_subscription and expires_at:
            date_str = format_date_ru(expires_at)

            if is_bypass_only:
                # Bypass-only: подписки нет, показываем только обход
                text += i18n_get_text(language, "profile.subscription_inactive") + "\n"
                text += i18n_get_text(language, "profile.tariff_none") + "\n"
            else:
                text += i18n_get_text(language, "profile.subscription_active", date=date_str) + "\n"
                if config.is_biz_tariff(sub_type):
                    tariff_label = "Business"
                elif sub_type == "plus":
                    tariff_label = "Комбо Plus" if is_combo else "Plus"
                elif is_trial:
                    tariff_label = "Trial"
                else:
                    tariff_label = "Комбо Basic" if is_combo else "Basic"
                text += i18n_get_text(language, "profile.tariff", tariff=tariff_label) + "\n"

            if not is_bypass_only:
                if auto_renew and expires_at:
                    renewal_window = timedelta(hours=6)
                    next_renewal = expires_at - renewal_window
                    text += i18n_get_text(language, "profile.auto_renew_on", date=format_date_ru(next_renewal))
                else:
                    text += i18n_get_text(language, "profile.auto_renew_off")
        else:
            text += i18n_get_text(language, "profile.subscription_inactive") + "\n"
            text += i18n_get_text(language, "profile.tariff_none") + "\n"
            text += i18n_get_text(language, "profile.auto_renew_none")

        # --- Traffic section: show if Remnawave enabled and user has remnawave_uuid ---
        # Traffic must be visible regardless of main subscription status (bypass GB always work)
        show_traffic = False
        if config.REMNAWAVE_ENABLED:
            rmn_uuid = await database.get_remnawave_uuid(telegram_id)
            if rmn_uuid:
                show_traffic = True
            elif has_active_subscription and sub_type in ("basic", "plus", "trial"):
                show_traffic = True  # will auto-provision below
                rmn_uuid = None
        if show_traffic:
            from app.services import remnawave_api, remnawave_service
            if rmn_uuid:
                remnawave_service._fire_and_forget(
                    remnawave_service.ensure_squad(telegram_id)
                )
                traffic = await remnawave_api.get_user_traffic(rmn_uuid)
                if traffic:
                    used = traffic["usedTrafficBytes"]
                    limit_bytes = traffic["trafficLimitBytes"]
                    pct = int(used / limit_bytes * 100) if limit_bytes > 0 else 0

                    def _fmt(b):
                        if b >= 1024**3:
                            return f"{b / 1024**3:.1f} ГБ"
                        if b >= 1024**2:
                            return f"{b / 1024**2:.0f} МБ"
                        return f"{b / 1024:.0f} КБ"

                    def _bar(u, l, length=10):
                        if l <= 0:
                            return "🤍" * length
                        ratio = min(u / l, 1.0)
                        filled = int(ratio * length)
                        return "🤍" * filled + "🩶" * (length - filled)

                    sub_url = traffic.get("subscriptionUrl", "")

                    text += f"\n\n📊 <b>Обход блокировок</b> 🇷🇺\n\n"
                    text += f"📥 {_fmt(used)} / {_fmt(limit_bytes)}\n"
                    text += f"{_bar(used, limit_bytes)} {pct}%\n\n"
                    if sub_url:
                        text += f"🔗 <b>Ключ обхода</b> <i>(нажми — скопируется)</i>\n<blockquote><code>{sub_url}</code></blockquote>"

                    if is_trial:
                        text += "\n\n💎 " + i18n_get_text(language, "traffic.trial_upgrade_hint")
            elif expires_at:
                # Auto-provision (fire-and-forget)
                override = 5 * 1024**3 if is_trial else 10 * 1024**3
                remnawave_service._fire_and_forget(
                    remnawave_service.create_remnawave_user(
                        telegram_id, sub_type, expires_at,
                        traffic_limit_override=override,
                    )
                )
                text += "\n\n📊 <b>Обход блокировок</b> 🇷🇺\n\n⏳ Настраиваем... Зайдите через несколько секунд."

        keyboard = get_profile_keyboard(
            language, has_active_subscription, auto_renew,
            subscription_type=sub_type, show_traffic=show_traffic,
            is_trial=is_trial,
            is_combo=is_combo,
            is_bypass_only=is_bypass_only,
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
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass

    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    
    await state.update_data(purchase_id=None, tariff_type=None, period_days=None)
    await database.cancel_pending_purchases(telegram_id, "new_purchase_started")
    await state.set_state(PurchaseState.choose_tariff)
    
    text = (
        f"💎 <b>Тарифы Atlas Secure</b>\n\n"
        f"{i18n_get_text(language, 'buy.tariff_basic')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_plus')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_business')}"
    )
    
    # Получаем текущую подписку для динамических кнопок
    subscription = await database.get_subscription(telegram_id)
    current_tariff = subscription.get("subscription_type") if subscription else None

    if current_tariff == "basic":
        basic_btn_key = "buy.select_basic_renew"
    elif current_tariff == "plus":
        basic_btn_key = "buy.select_basic_switch"
    else:
        basic_btn_key = "buy.select_basic_new"

    if current_tariff == "plus":
        plus_btn_key = "buy.select_plus_renew"
    elif current_tariff == "basic":
        plus_btn_key = "buy.select_plus_switch"
    else:
        plus_btn_key = "buy.select_plus_new"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, basic_btn_key),
            callback_data="tariff:basic"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, plus_btn_key),
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
    
    # If message is a photo (e.g. no-sub main screen), delete and send new
    if msg.photo:
        try:
            await msg.delete()
        except Exception:
            pass
        await bot.send_message(msg.chat.id, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(msg, text, reply_markup=keyboard, bot=bot)


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
