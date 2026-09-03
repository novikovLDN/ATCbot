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
from app.constants.loyalty import tier_emoji_html, tier_genitive
from app.utils.date_utils import format_date_ru

logger = logging.getLogger(__name__)

# ── Screen photos ──────────────────────────────────────────────────────
# file_ids are bot-specific (a file_id uploaded via one bot token won't
# resolve on another).  These were uploaded via the production bot;
# `_send_screen_photo` falls back to a plain text message on ANY
# send_photo failure (stale id / wrong bot / caption too long), so a
# bad file_id never breaks a screen — it just degrades to text.
PROFILE_PHOTO_FILE_ID = "AgACAgQAAxkBAAF_0eZqfhvhiUZBdALxqV1bT5M-U0GPGgAC6BFrG3NR8VOGYtduypInugEAAwIAA3cAAz0E"

# Фото экрана «❓ Помощь» (_open_help_screen). Сейчас МОДЕРАЦИОННОЕ фото.
# После модерации вернуть на "без модерации":
# AgACAgQAAxkBAAGIL6NqmSBPEGLHLGql0JtCj85HJAerwQACfBBrGzE2yFCoJX6favSQxQEAAwIAA3kAAz0E
# (см. docs/MODERATION_VPN_BYPASS_CHANGESET.md)
SUPPORT_PHOTO_FILE_ID = "AgACAgQAAxkBAAGIL6ZqmSBb2kWmNnZwEz4dec4wlhJ4NQACfRBrGzE2yFC1E_y6lGWUTAEAAwIAA3kAAz0E"

CONTACTS_PHOTO_FILE_ID = "AgACAgQAAxkBAAFaMrhqIIn_mXiy0317JBGMgFkHl6d9DQACvhZrG8kkCVH3VeBvZR6bxAEAAwIAA3kAAzsE"

SHOP_PHOTO_FILE_ID = "AgACAgQAAxkBAAF_0glqfh1UN3qWxjF1pBnx0kSISew9xAAC6hFrG3NR8VNECE_tmcgrxwEAAwIAA3cAAz0E"

GIFT_PHOTO_FILE_ID = "AgACAgQAAxkBAAFU08dqGqW7fM71f6jxAAHg0TqaIRev3jAAAh0OaxuEL9lQeDYgAjezwKoBAAMCAAN5AAM7BA"

GAMES_PHOTO_FILE_ID = "AgACAgQAAxkBAAF_2dZqfoYdU3sXHJZKfj4vZBAto5VdwwACVxJrG3NR8VM0TS46VXdjAQEAAwIAA3kAAz0E"

MY_SUBSCRIPTION_PHOTO_FILE_ID = "AgACAgQAAxkBAAF_0btqfhnsntISOSSa4HeiUMBkOoaLeQAC2RFrG3NR8VP0xTJDQxtIZgEAAwIAA3cAAz0E"

# Telegram caps photo captions at 1024 chars (vs 4096 for plain text).
# The profile screen with the bypass-traffic section + keys can exceed
# that, so when the caption is too long we send a plain text message
# instead of erroring out.
_TG_CAPTION_LIMIT = 1024


def _fmt_bytes_pretty(b: int) -> str:
    """Human-readable bytes → 'X ГБ' / 'X МБ' / 'X КБ' / '0'. Стабильная точность."""
    b = max(0, int(b or 0))
    if b == 0:
        return "0"
    if b >= 1024 ** 3:
        gb = b / 1024 ** 3
        return f"{gb:.2f} ГБ" if gb < 10 else f"{gb:.1f} ГБ"
    if b >= 1024 ** 2:
        mb = b / 1024 ** 2
        return f"{mb:.1f} МБ" if mb < 10 else f"{mb:.0f} МБ"
    kb = b / 1024
    return f"{kb:.0f} КБ"


async def _render_bypass_line(
    telegram_id: int,
    language: str,
    *,
    none_key: str = "main.my_sub_bypass_none",
    left_key: str = "main.my_sub_bypass_left",
    none_default: str = "Трафик: —",
    left_default: str = "Осталось трафика: {remaining} из {limit}",
) -> str:
    """Единый рендер строки «Трафик обхода» для profile и my_subscription.

    Берём bypass entity из панели через remnawave_uuid, показываем:
      • «Трафик обхода: —»                — нет entity в БД / панели.
      • «Трафик обхода: X ГБ из Y ГБ»     — юзер купил Y ГБ, потратил Y-X.
      • «Трафик обхода: безлимит»         — trafficLimitBytes=0 (админ-гифт).

    Форматирование `_fmt_bytes_pretty` — стабильные единицы (ГБ / МБ / КБ),
    remaining и limit всегда в единицах наибольшего (см. `_fmt`).
    """
    if not config.REMNAWAVE_ENABLED:
        return i18n_get_text(language, none_key, none_default)
    try:
        # get_bypass_traffic_safe гарантированно возвращает BYPASS entity
        # (проверяет username=str(tg)). Если DB-кеш указывает на premium
        # из-за legacy backfill-бага — self-heal чинит DB и re-resolve
        # через username.
        from app.services import remnawave_api
        traffic = await remnawave_api.get_bypass_traffic_safe(telegram_id)
        if not traffic:
            return i18n_get_text(language, none_key, none_default)
        used = int(traffic.get("usedTrafficBytes") or 0)
        limit_bytes = int(traffic.get("trafficLimitBytes") or 0)
        # trafficLimitBytes=0 в Remnawave = БЕЗЛИМИТ. Bypass с безлимитом
        # — редкий admin-gift кейс; показываем "безлимит" вместо
        # некорректного "0 КБ из 0 КБ".
        if limit_bytes == 0:
            return i18n_get_text(
                language, "main.my_sub_bypass_unlimited",
                "Трафик: безлимит",
            )
        remaining = max(0, limit_bytes - used)
        return i18n_get_text(
            language, left_key, left_default,
            remaining=_fmt_bytes_pretty(remaining),
            limit=_fmt_bytes_pretty(limit_bytes),
        )
    except Exception as e:
        logger.warning("bypass_line fetch failed for %s: %s", telegram_id, e)
        return i18n_get_text(language, none_key, none_default)


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
    from app.handlers.common.emoji import CE
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "help.faq_button", "📖 Ответы на частые вопросы"), callback_data="faq", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "help.instructions_button", "📲 Инструкции по сервису"), callback_data="connect_instruction", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "help.contacts_button", "📞 Контакты"), callback_data="help_contacts", style="primary")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.help_button", "💬 Помощь"), url="https://t.me/atlas_suppbot", style="danger")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main", icon_custom_emoji_id=CE["back"], style="primary")],
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

        # 1. Header — бренд + слоган-подзаголовок
        text = (
            "🎖 <b>Круг Амбассадоров</b>\n"
            "<i>От проводника до амбассадора</i>\n\n"
        )

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

        from app.handlers.common.emoji import CE
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
                    style="primary",
                ),
                InlineKeyboardButton(
                    text=i18n_get_text(language, "referral.how_it_works"),
                    callback_data="referral_how_it_works",
                    style="primary",
                ),
            ],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
                icon_custom_emoji_id=CE["back"],
                style="primary",
            )],
        ])
        
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
            f"cashback={total_cashback:.2f} RUB, remaining={remaining_connections}"
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

        # Собираем инфо-строки (одна колонка, без blockquote)
        info_lines = []
        if has_active_subscription and expires_at and not is_bypass_only:
            date_str = format_date_ru(expires_at)
            info_lines.append(i18n_get_text(language, "profile.info_active_until", "📆 Подписка: активна до {date}", date=date_str))
            if config.is_biz_tariff(sub_type):
                tariff_label = "Business"
            elif sub_type == "plus":
                tariff_label = "Комбо Plus" if is_combo else "Plus"
            elif is_trial:
                tariff_label = "Trial"
            else:
                tariff_label = "Комбо Basic" if is_combo else "Basic"
            info_lines.append(i18n_get_text(language, "profile.info_tariff", "⭐️ Тариф: {tariff}", tariff=tariff_label))
        else:
            info_lines.append(i18n_get_text(language, "profile.info_inactive", "📆 Подписка: не активна"))
            info_lines.append(i18n_get_text(language, "profile.info_tariff_none", "⭐️ Тариф: —"))

        # Трафик обхода — единый helper (правильно обрабатывает безлимит,
        # согласованные единицы, кейсы missing entity).
        bypass_line = await _render_bypass_line(
            telegram_id, language,
            none_key="profile.info_bypass_none",
            left_key="profile.info_bypass_left",
            none_default="💎 Трафик: —",
            left_default="💎 Осталось трафика: {remaining} из {limit}",
        )
        info_lines.append(bypass_line)

        # Автопродление
        if has_active_subscription and not is_bypass_only:
            info_lines.append("")  # пустая строка перед служебными полями
            info_lines.append(
                i18n_get_text(language, "profile.info_auto_renew_on", "🔁 Автопродление: включено")
                if auto_renew
                else i18n_get_text(language, "profile.info_auto_renew_none", "🔁 Автопродление: —")
            )
        else:
            info_lines.append("")
            info_lines.append(i18n_get_text(language, "profile.info_auto_renew_none", "🔁 Автопродление: —"))

        info_lines.append(i18n_get_text(language, "profile.info_balance", "💰 Баланс: {balance} ₽", balance=balance_str))

        # Приглашено друзей — счётчик по реф-ссылке
        try:
            ref_stats = await database.get_referral_stats(telegram_id)
            invited_count = ref_stats.get("total_referred", 0) if ref_stats else 0
        except Exception as e:
            logger.warning(f"profile: referral count fetch failed for {telegram_id}: {e}")
            invited_count = 0
        info_lines.append("")
        info_lines.append(i18n_get_text(language, "profile.info_invited_friends", "👥 Приглашено друзей: {count}", count=invited_count))

        text += "\n".join(info_lines)

        # Bypass entity auto-provision (fire-and-forget) —
        # трафик уже показан выше цифрами, здесь только гарантируем,
        # что у пользователя вообще есть entity в Remnawave.
        show_traffic = False
        if config.REMNAWAVE_ENABLED:
            rmn_uuid_prov = await database.get_remnawave_uuid(telegram_id)
            if rmn_uuid_prov:
                show_traffic = True
                from app.services import remnawave_service as _rmn_svc
                _rmn_svc._fire_and_forget(_rmn_svc.ensure_squad(telegram_id))
            elif has_active_subscription and expires_at and sub_type in ("basic", "plus", "trial"):
                from app.services import remnawave_service as _rmn_svc
                # Trial → TRIAL_BYPASS_MB (default 500 MB, per ТЗ),
                # paid → 10 GB старт-пак. Раньше был баг: trial=5GB —
                # profile.show fallback выдавал в 10× больше, чем
                # provision_subscription при первичной активации.
                if is_trial:
                    trial_mb = int(getattr(config, "TRIAL_BYPASS_MB", 500)) or 500
                    override = trial_mb * (1024 ** 2)
                else:
                    override = 10 * 1024**3
                _rmn_svc._fire_and_forget(
                    _rmn_svc.create_remnawave_user(
                        telegram_id, sub_type, expires_at,
                        traffic_limit_override=override,
                    )
                )

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


async def _open_buy_screen(
    event: Union[Message, CallbackQuery],
    bot: Bot,
    state: FSMContext,
    *,
    force_new_message: bool = False,
):
    """
    Купить VPN - выбор типа тарифа (Basic/Plus). Reusable for callback and /buy command.

    CANONICAL TARIFF SCREEN BUILDER - единственный источник правды для экрана тарифов.
    Используется везде: после промокода, при нажатии "Купить доступ", и т.д.

    force_new_message: если True — экран тарифов уходит ОТДЕЛЬНЫМ сообщением
    даже когда инвок пришёл из CallbackQuery. Нужно для broadcast-кнопок
    («Купить со скидкой» и т.п.): юзер должен видеть саму рассылку рядом
    с экраном тарифов, а не вместо неё. По умолчанию False — стандартная
    in-place навигация (edit или delete-and-resend).
    """
    from app.handlers.common.keyboards import CE

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
    # Снести залипшие invoice-экраны от предыдущих покупок (Wata «Ждём
    # платёж», Lava/Platega, нативный Telegram Payments invoice и т.п.).
    # Иначе они болтаются в чате рядом с новым «Выберите тариф» и путают
    # юзера — он видит старый 89 ₽ invoice + новый экран тарифов.
    try:
        from app.handlers.callbacks.payments_callbacks import delete_all_invoice_messages_for_user
        await delete_all_invoice_messages_for_user(bot, telegram_id)
    except Exception:
        pass
    await state.set_state(PurchaseState.choose_tariff)
    
    _tariffs_block = (
        f"{i18n_get_text(language, 'buy.tariff_basic')}\n\n"
        f"{i18n_get_text(language, 'buy.tariff_plus')}"
    )
    text = i18n_get_text(
        language, "buy.select_tariff_new",
        (
            f'<tg-emoji emoji-id="5427168083074628963">💎</tg-emoji> <b>Выберите тариф</b>\n\n'
            f"{_tariffs_block}\n\n"
            f'<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji> <b>Комбо</b> — VPS + трафик в одном пакете\n'
            f"<blockquote>Трафик включён · от 329 ₽/мес</blockquote>"
        ),
        tariffs=_tariffs_block,
    )

    # Получаем текущую подписку для динамических кнопок
    subscription = await database.get_subscription(telegram_id)
    is_bypass_only_sub = bool(subscription and subscription.get("is_bypass_only"))
    current_tariff = subscription.get("subscription_type") if subscription and not is_bypass_only_sub else None

    if is_bypass_only_sub:
        # Bypass-only: show special header
        text = i18n_get_text(
            language, "buy.select_tariff_bypass_active",
            (
                f"🌐 <b>У вас активен Pro-режим</b>\n\n"
                f"Для основной подписки выберите тариф:\n\n"
                f"{_tariffs_block}\n\n"
                f'<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji> <b>Комбо</b> — VPS + трафик в одном пакете\n'
                f"<blockquote>Трафик включён · от 329 ₽/мес</blockquote>"
            ),
            tariffs=_tariffs_block,
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

    # Определим стили для basic/plus в зависимости от контекста (renew → success + renew)
    basic_is_renew = current_tariff == "basic"
    plus_is_renew = current_tariff == "plus"
    basic_extra = {"icon_custom_emoji_id": CE["renew"], "style": "success"} if basic_is_renew else {"style": "primary"}
    plus_extra = {"icon_custom_emoji_id": CE["renew"], "style": "success"} if plus_is_renew else {"style": "primary"}

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, basic_btn_key),
            callback_data="tariff:basic",
            **basic_extra,
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, plus_btn_key),
            callback_data="tariff:plus",
            **plus_extra,
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.combo_button", "🚀 Комбо (VPS + трафик)"),
            callback_data="buy_combo",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "shop.mt_proxy_button", "Купить Telegram MT Прокси"),
            callback_data="proxy_menu",
            icon_custom_emoji_id=CE["proxy"],
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "buy.have_promo_button", "У меня промокод"),
            callback_data="enter_promo",
            icon_custom_emoji_id=CE["promo"],
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ])
    
    # Если caller явно попросил «новое сообщение» (broadcast-CTA, где
    # сообщение рассылки должно остаться) — шлём в чат свежим, ничего
    # не редактируя и не удаляя. Все остальные ветки сохраняют прежнее
    # поведение in-place навигации.
    if force_new_message:
        chat_id = msg.chat.id if msg else telegram_id
        await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")
    elif msg.photo:
        # In-place: для фото-сообщений edit_text сломан Telegram'ом
        # (caption-limit 1024, текст тарифов часто длиннее), поэтому
        # стандартный fallback — delete + send.
        try:
            await msg.delete()
        except Exception:
            pass
        await bot.send_message(msg.chat.id, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await safe_edit_text(msg, text, reply_markup=keyboard, bot=bot)


async def show_tariffs_main_screen(
    event: Union[Message, CallbackQuery],
    state: FSMContext,
    *,
    force_new_message: bool = False,
):
    """
    CANONICAL TARIFF SCREEN - единый builder для экрана тарифов.

    Используется после применения промокода и везде, где нужно показать экран тарифов.
    Гарантирует единообразие UI и отсутствие дублирования кода.

    Args:
        event: Message или CallbackQuery объект
        state: FSM context
        force_new_message: см. _open_buy_screen — True для broadcast-CTA,
            чтобы оригинальное сообщение рассылки осталось в чате.
    """
    bot = event.bot if isinstance(event, CallbackQuery) else event.bot
    await _open_buy_screen(event, bot, state, force_new_message=force_new_message)


async def _open_my_subscription_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """«Моя подписка» — info-экран с тарифом, датой активности и остатком ГБ обхода.

    Кнопки: Подключить VPN, Продлить подписку, Пополнить ГБ Обхода, Назад.
    """
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass

    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription_any(telegram_id)
    subscription_status = get_subscription_status(subscription)
    has_active_subscription = subscription_status.is_active
    expires_at = subscription_status.expires_at
    sub_type = (subscription.get("subscription_type") or "basic").strip().lower() if subscription else "basic"
    is_bypass_only = bool(subscription and subscription.get("is_bypass_only"))
    is_combo = bool(subscription and subscription.get("is_combo"))
    is_trial = sub_type == "trial"

    # Тариф
    if has_active_subscription and not is_bypass_only:
        if config.is_biz_tariff(sub_type):
            tariff_label = "Business"
        elif sub_type == "plus":
            tariff_label = "Комбо Plus" if is_combo else "Plus"
        elif is_trial:
            tariff_label = "Trial"
        else:
            tariff_label = "Комбо Basic" if is_combo else "Basic"
        active_line = i18n_get_text(language, "main.my_sub_active_until", "Активна до: {date}", date=format_date_ru(expires_at))
    else:
        tariff_label = "—"
        active_line = i18n_get_text(language, "main.my_sub_active_until_none", "Активна до: —")

    # Трафик обхода (остаток / лимит) — единый helper.
    bypass_line = await _render_bypass_line(
        telegram_id, language,
        none_key="main.my_sub_bypass_none",
        left_key="main.my_sub_bypass_left",
        none_default="Трафик: —",
        left_default="Осталось трафика: {remaining} из {limit}",
    )

    # Есть ли доступ к обходу (bypass entity с трафиком)? Если строка НЕ
    # «none»-вариант — у юзера есть bypass-ключ, значит есть что подключать
    # даже без основной подписки.
    _none_line = i18n_get_text(language, "main.my_sub_bypass_none", "Трафик: —")
    has_bypass_access = bypass_line != _none_line

    _title = i18n_get_text(language, "main.my_sub_title", "<b>Информация о подписке</b>")
    _tariff_line = i18n_get_text(language, "profile.info_tariff", "⭐️ Тариф: {tariff}", tariff=tariff_label)
    text = (
        f"{_title}\n\n"
        f"{_tariff_line}\n"
        f"📆 {active_line}\n"
        f"💎 {bypass_line}"
    )

    has_proxy = False
    try:
        has_proxy = await database.has_purchased_proxy(telegram_id)
    except Exception as e:
        logger.warning(f"my_subscription: proxy check failed for {telegram_id}: {e}")

    from app.handlers.common.keyboards import CE

    kb_rows = []
    # Кнопка «Подключить» — если есть основная подписка ИЛИ bypass-трафик.
    # Bypass-only юзеру (трафик есть, тарифа нет) тоже нужен ключ обхода.
    if (has_active_subscription and not is_bypass_only) or has_bypass_access:
        kb_rows.append([InlineKeyboardButton(
            text=i18n_get_text(language, "main.my_sub_btn_connect", "Подключить VPS"),
            callback_data="connect_instruction",
            icon_custom_emoji_id=CE["connect"],
            style="danger",
        )])
    # Продлить подписку (🔄) / Купить VPN (🛒)
    if has_active_subscription and not is_bypass_only:
        renew_text = i18n_get_text(language, "main.my_sub_btn_renew", "Продлить подписку")
        renew_icon = CE["renew"]
    else:
        renew_text = i18n_get_text(language, "main.my_sub_btn_buy_vpn", "Купить VPS")
        renew_icon = CE["buy"]
    kb_rows.append([InlineKeyboardButton(
        text=renew_text,
        callback_data="menu_buy_vpn",
        icon_custom_emoji_id=renew_icon,
        style="success",
    )])
    kb_rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "main.my_sub_btn_topup_gb", "Пополнить ГБ Обхода"),
        callback_data="buy_traffic",
        icon_custom_emoji_id=CE["traffic"],
        style="success",
    )])
    kb_rows.append([InlineKeyboardButton(
        text=(
            i18n_get_text(language, "main.my_sub_btn_my_proxy", "Мой прокси") if has_proxy
            else i18n_get_text(language, "main.my_sub_btn_mt_proxy", "Telegram MT Прокси")
        ),
        callback_data="proxy_menu",
        icon_custom_emoji_id=CE["proxy"],
        style="primary",
    )])
    kb_rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back", "Назад"),
        callback_data="menu_main",
        icon_custom_emoji_id=CE["back"],
        style="primary",
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    # Фото-экран: удаляем текущее сообщение и шлём photo+caption через
    # _send_screen_photo (fallback на text при устаревшем file_id или
    # caption > 1024).
    chat_id = msg.chat.id if hasattr(msg, "chat") else telegram_id
    if isinstance(event, CallbackQuery):
        try:
            await msg.delete()
        except Exception:
            pass
    await _send_screen_photo(
        bot, chat_id, MY_SUBSCRIPTION_PHOTO_FILE_ID, text,
        reply_markup=keyboard, parse_mode="HTML",
    )


async def _open_legal_screen(event: Union[Message, CallbackQuery], bot: Bot):
    """«Правила» — выбор правового документа (соглашение / политика конфиденциальности)."""
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass

    msg = event.message if isinstance(event, CallbackQuery) else event
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)

    from app.handlers.common.keyboards import CE

    text = i18n_get_text(language, "main.legal_title", "📰 <b>Правовые документы</b>\n\nВыберите документ для ознакомления:")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.legal_terms_btn", "Пользовательское соглашение"),
            url="https://telegra.ph/Polzovatelskoe-soglashenie-09-03-24",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.legal_privacy_btn", "Политика конфиденциальности"),
            url="https://telegra.ph/Politika-konfidencialnosti-09-03-71",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back", "Назад"),
            callback_data="menu_profile",
            icon_custom_emoji_id=CE["back"],
            style="primary",
        )],
    ])
    await safe_edit_text(msg, text, reply_markup=keyboard, parse_mode="HTML", bot=bot)
