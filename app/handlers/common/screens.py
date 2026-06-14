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
from app.constants.loyalty import get_loyalty_screen_attachment, tier_emoji_html, tier_genitive
from app.utils.date_utils import format_date_ru

logger = logging.getLogger(__name__)

# ── Screen photos ──────────────────────────────────────────────────────
# file_ids are bot-specific (a file_id uploaded via one bot token won't
# resolve on another).  These were uploaded via the production bot;
# `_send_screen_photo` falls back to a plain text message on ANY
# send_photo failure (stale id / wrong bot / caption too long), so a
# bad file_id never breaks a screen — it just degrades to text.
PROFILE_PHOTO_FILE_ID = "AgACAgQAAxkBAAFU06RqGqSy7ZznGSzMqpWqKob_ly-ByQACYA9rGxA30FBNWYvPRln8OgEAAwIAA3kAAzsE"

SUPPORT_PHOTO_FILE_ID = "AgACAgQAAxkBAAFU07dqGqVLNGYWl3jMGShmNxuNUgvkpAACGw5rG4Qv2VBVBIqM5lqnCgEAAwIAA3kAAzsE"

CONTACTS_PHOTO_FILE_ID = "AgACAgQAAxkBAAFaMrhqIIn_mXiy0317JBGMgFkHl6d9DQACvhZrG8kkCVH3VeBvZR6bxAEAAwIAA3kAAzsE"

SHOP_PHOTO_FILE_ID = "AgACAgQAAxkBAAFU08RqGqWH5bytFQj3dTputnGpYJzHEAACHA5rG4Qv2VAe5eXMo4mvpAEAAwIAA3kAAzsE"

GIFT_PHOTO_FILE_ID = "AgACAgQAAxkBAAFU08dqGqW7fM71f6jxAAHg0TqaIRev3jAAAh0OaxuEL9lQeDYgAjezwKoBAAMCAAN5AAM7BA"

GAMES_PHOTO_FILE_ID = "AgACAgQAAxkBAAFU09FqGqX9Jn5MUCs5Umhem0uAzq_wNwACHg5rG4Qv2VCtTQ2_vzbH5gEAAwIAA3kAAzsE"

# Telegram caps photo captions at 1024 chars (vs 4096 for plain text).
# The profile screen with the bypass-traffic section + keys can exceed
# that, so when the caption is too long we send a plain text message
# instead of erroring out.
_TG_CAPTION_LIMIT = 1024


async def _send_screen_photo(
    bot,
    chat_id: int,
    photo_file_id: str,
    caption: str,
    reply_markup=None,
    parse_mode: str = "HTML",
):
    """Send a photo-with-caption screen, degrading gracefully:

      * caption longer than the Telegram caption limit → send as a plain
        text message (no photo) so the user still gets the full screen;
      * send_photo fails for any other reason (stale file_id, wrong bot
        token on stage, network) → fall back to a plain text message.

    Never raises — always returns the sent Message or None.
    """
    if caption and len(caption) > _TG_CAPTION_LIMIT:
        # Too long to be a caption — text-only render.
        return await bot.send_message(
            chat_id=chat_id, text=caption,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
    try:
        return await bot.send_photo(
            chat_id=chat_id, photo=photo_file_id, caption=caption,
            reply_markup=reply_markup, parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning(
            "SCREEN_PHOTO_FALLBACK_TEXT chat=%s err=%s", chat_id, e,
        )
        try:
            return await bot.send_message(
                chat_id=chat_id, text=caption,
                reply_markup=reply_markup, parse_mode=parse_mode,
            )
        except Exception as e2:
            logger.error("SCREEN_PHOTO_FALLBACK_TEXT_FAILED chat=%s err=%s", chat_id, e2)
            return None



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


async def _open_help_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """Help menu (FAQ / Instructions / Operator). Reusable for callback and /help command.

    Photo screen: always sends a fresh photo via `_send_screen_photo` (which
    degrades to plain text if the file_id is unusable). When invoked from a
    callback we delete the previous message first, which handles every
    transition uniformly — photo→photo, text→photo, fresh-command.
    """
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass
        chat_id = event.message.chat.id
        try:
            await event.message.delete()
        except Exception:
            pass
    else:
        chat_id = event.chat.id

    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "help.menu_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Ответы на частые вопросы", callback_data="faq")],
        [InlineKeyboardButton(text="📲 Инструкции по сервису", callback_data="connect_instruction")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="help_contacts")],
        [InlineKeyboardButton(text="💬 Помощь", url="https://t.me/atlas_suppbot")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await _send_screen_photo(
        bot, chat_id, SUPPORT_PHOTO_FILE_ID, text,
        reply_markup=keyboard, parse_mode="HTML",
    )


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
            await bot.send_message(chat_id, err_text, parse_mode="HTML")
    
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
        
        # Генерируем реферальную ссылку для share URL
        bot_info = await bot.get_me()
        referral_link = await build_referral_link(telegram_id, bot_info.username)
        from urllib.parse import quote
        share_url = f"https://t.me/share/url?url={quote(referral_link)}"

        # Структурированный текст: статус-блок + прогресс + ссылка.
        # Тон по уровню: новичку — приветствие, среднему — азарт прогресса,
        # амбассадору — фиксация статуса.
        tier_glyph = tier_emoji_html(current_level_name)
        is_max = not next_level_name or remaining_connections <= 0
        is_new = total_invited == 0 and active_paid_referrals == 0

        # 1. Header — бренд
        text = "🎖 <b>Круг Амбассадоров</b>\n\n"

        # 2. Hero-line по контексту юзера
        if is_new:
            text += (
                "Ты на первой ступени. Делись ссылкой → друг покупает "
                "подписку → ты получаешь <b>кэшбэк</b> на баланс.\n\n"
            )
        elif is_max:
            text += (
                "Это вершина. <b>Зафиксировано бессрочно.</b> "
                "Тебя меньше 1%.\n\n"
            )

        # 3. Статус-блок
        status_block = (
            f"{tier_glyph} <b>{current_level_name}</b> · <b>{cashback_percent}%</b> "
            f"с каждой покупки\n"
            f"💰 Заработано: <b>{total_cashback:.2f} ₽</b>"
        )
        text += f"<blockquote>{status_block}</blockquote>\n\n"

        # 4. Прогресс к следующему уровню (если не максимум)
        if not is_max:
            # Найти процент следующего тира для конкретики
            next_pct_map = {
                "Хранитель": 20, "Инсайдер": 30, "Лидер": 40, "Амбассадор": 45,
            }
            next_pct = next_pct_map.get(next_level_name, "?")
            progress_block = (
                f"📈 До <b>{tier_genitive(next_level_name)}</b> ({next_pct}%) — "
                f"<b>{remaining_connections}</b> купивших.\n"
                f"Уровень только растёт и не падает."
            )
            text += f"<blockquote>{progress_block}</blockquote>\n\n"

        # 5. Реферальная ссылка
        text += (
            f"🔗 <b>Твоя ссылка</b> <i>(нажми — скопируется)</i>\n"
            f"<blockquote expandable><code>{referral_link}</code></blockquote>"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "referral.share_button"),
                url=share_url,
                style="success",
            )],
            [
                InlineKeyboardButton(
                    text=i18n_get_text(language, "referral.stats_button"),
                    callback_data="referral_stats",
                ),
                InlineKeyboardButton(
                    text=i18n_get_text(language, "referral.how_it_works"),
                    callback_data="referral_how_it_works",
                ),
            ],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
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
                    parse_mode="HTML",
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
    """Показать профиль пользователя (фото-экран с балансом и трафиком).

    The profile is a PHOTO screen.  Whatever the current message is
    (photo, text, or a fresh /profile command), we delete it (when it's
    a callback) and send a fresh photo message via `_send_screen_photo`,
    which degrades to plain text if the caption is too long or the
    file_id is unusable.  This delete+resend pattern makes navigation
    to/from the profile screen safe regardless of the previous screen's
    type.
    """
    telegram_id = None
    send_func = None

    try:
        if isinstance(message_or_query, Message):
            telegram_id = message_or_query.from_user.id
            chat_id = message_or_query.chat.id
            bot = message_or_query.bot
        else:
            telegram_id = message_or_query.from_user.id
            chat_id = message_or_query.message.chat.id
            bot = message_or_query.bot
            # Drop the previous screen's message (any type) before sending
            # the fresh profile photo.
            try:
                await message_or_query.message.delete()
            except Exception:
                pass

        async def send_func(text, reply_markup=None, parse_mode="HTML"):
            return await _send_screen_photo(
                bot, chat_id, PROFILE_PHOTO_FILE_ID, text,
                reply_markup=reply_markup, parse_mode=parse_mode,
            )
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
        # Header — имя + Telegram ID
        text = f"👤 <b>{display_name}</b>\n"
        text += f"🆔 ID: <code>{telegram_id}</code>\n\n"

        is_trial = sub_type == "trial"
        is_combo = subscription.get("is_combo", False) if subscription else False
        is_bypass_only = subscription.get("is_bypass_only", False) if subscription else False

        # Блок «Подписка» — собираем строки, потом оборачиваем в blockquote
        sub_lines = []
        if has_active_subscription and expires_at:
            date_str = format_date_ru(expires_at)
            if is_bypass_only:
                sub_lines.append(i18n_get_text(language, "profile.subscription_inactive"))
                sub_lines.append(i18n_get_text(language, "profile.tariff_none"))
            else:
                sub_lines.append(i18n_get_text(language, "profile.subscription_active", date=date_str))
                if config.is_biz_tariff(sub_type):
                    tariff_label = "Business"
                elif sub_type == "plus":
                    tariff_label = "Комбо Plus" if is_combo else "Plus"
                elif is_trial:
                    tariff_label = "Trial"
                else:
                    tariff_label = "Комбо Basic" if is_combo else "Basic"
                sub_lines.append(i18n_get_text(language, "profile.tariff", tariff=tariff_label))
                if auto_renew:
                    renewal_window = timedelta(hours=6)
                    next_renewal = expires_at - renewal_window
                    sub_lines.append(i18n_get_text(language, "profile.auto_renew_on", date=format_date_ru(next_renewal)))
                else:
                    sub_lines.append(i18n_get_text(language, "profile.auto_renew_off"))
        else:
            sub_lines.append(i18n_get_text(language, "profile.subscription_inactive"))
            sub_lines.append(i18n_get_text(language, "profile.tariff_none"))
            sub_lines.append(i18n_get_text(language, "profile.auto_renew_none"))

        text += "<blockquote>" + "\n".join(sub_lines) + "</blockquote>\n\n"

        # Блок «Баланс»
        text += "<blockquote>" + i18n_get_text(language, "profile.balance", amount=balance_str) + "</blockquote>"

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

                    from app.services import happ_crypto
                    sub_url = happ_crypto.format_for_user(
                        traffic.get("subscriptionUrl", "")
                    )

                    traffic_block = (
                        f"<tg-emoji emoji-id=\"5190806721286657692\">📊</tg-emoji> <b>Обход блокировок</b> 🇷🇺\n"
                        f"<tg-emoji emoji-id=\"5443127283898405358\">📥</tg-emoji> {_fmt(used)} / {_fmt(limit_bytes)}\n"
                        f"{_bar(used, limit_bytes)} {pct}%"
                    )
                    text += f"\n\n<blockquote>{traffic_block}</blockquote>"
                    if sub_url:
                        text += f"\n\n<tg-emoji emoji-id=\"5271604874419647061\">🔗</tg-emoji> <b>Ключ обхода</b> <i>(нажми — скопируется)</i>\n<blockquote expandable><code>{sub_url}</code></blockquote>"

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
                text += "\n\n<blockquote><tg-emoji emoji-id=\"5190806721286657692\">📊</tg-emoji> <b>Обход блокировок</b> 🇷🇺\n⏳ Настраиваем… Зайдите через несколько секунд.</blockquote>"

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
                await message_or_query.message.answer(error_text, parse_mode="HTML")
            elif isinstance(message_or_query, Message):
                await message_or_query.answer(error_text, parse_mode="HTML")
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")
            # Последняя попытка - отправить простой текст без локализации
            try:
                language = await resolve_user_language(telegram_id)
                error_text = i18n_get_text(language, "errors.profile_load")
                if isinstance(message_or_query, CallbackQuery):
                    await message_or_query.message.answer(error_text, parse_mode="HTML")
                elif isinstance(message_or_query, Message):
                    await message_or_query.answer(error_text, parse_mode="HTML")
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
        f"💎 <b>Выберите тариф</b>\n\n"
        f"{i18n_get_text(language, 'buy.tariff_basic')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_plus')}\n\n"
        f"🚀 <b>Комбо</b> — VPN + обход в одном пакете\n"
        f"<blockquote>Трафик обхода включён · от 329 ₽/мес</blockquote>"
    )

    # Получаем текущую подписку для динамических кнопок
    subscription = await database.get_subscription(telegram_id)
    is_bypass_only_sub = bool(subscription and subscription.get("is_bypass_only"))
    current_tariff = subscription.get("subscription_type") if subscription and not is_bypass_only_sub else None

    if is_bypass_only_sub:
        # Bypass-only: show special header
        text = (
            f"🌐 <b>У вас активен обход блокировок</b>\n\n"
            f"Для основной подписки выберите тариф:\n\n"
            f"{i18n_get_text(language, 'buy.tariff_basic')}\n\n"
            f"{i18n_get_text(language, 'buy.tariff_plus')}\n\n"
            f"🚀 <b>Комбо</b> — VPN + обход в одном пакете\n"
            f"<blockquote>Трафик обхода включён · от 329 ₽/мес</blockquote>"
        )

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
            text="🚀 Комбо (VPN + обход)",
            callback_data="buy_combo"
        )],
        [InlineKeyboardButton(
            text="🎟 У меня промокод",
            callback_data="enter_promo"
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main"
        )],
    ])
    
    # If message is a photo (e.g. no-sub main screen), delete and send new
    if isinstance(event, Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    elif msg.photo:
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
