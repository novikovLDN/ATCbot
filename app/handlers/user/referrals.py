"""
User command: /referral
"""
import logging

from aiogram import Router, F
from aiogram.types import Message, Bot, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from app.handlers.common.guards import ensure_db_ready_message
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.screens import _open_referral_screen
import database
import logging

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("referral"))
async def cmd_referral(message: Message, bot: Bot):
    """Обработчик команды /referral — открывает экран программы лояльности"""
    if not await ensure_db_ready_message(message):
        return
    await _open_referral_screen(message, bot)


@user_router.callback_query(F.data == "menu_referral")
async def callback_referral(callback: CallbackQuery):
    """Экран «Программа лояльности». Entry from inline button."""
    from app.handlers.common.screens import _open_referral_screen
    await _open_referral_screen(callback, callback.bot)


@user_router.callback_query(F.data == "share_referral_link")
@user_router.callback_query(F.data == "copy_referral_link")
async def callback_copy_referral_link(callback: CallbackQuery):
    """Поделиться реферальной ссылкой - отправляет ссылку отдельным сообщением"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)
    
    try:
        # Получаем username бота для реферальной ссылки
        bot_info = await callback.bot.get_me()
        bot_username = bot_info.username
        # Реферальная ссылка: https://t.me/<bot_username>?start=ref_<telegram_id>
        referral_link = f"https://t.me/{bot_username}?start=ref_{telegram_id}"
        
        # Отправляем ссылку отдельным сообщением для копирования (одно нажатие в Telegram)
        await callback.message.answer(
            f"<code>{referral_link}</code>",
            parse_mode="HTML"
        )
        
        # Показываем toast уведомление
        await callback.answer(i18n_get_text(language, "referral.link_copied"), show_alert=False)
        
        logger.info(f"Referral link sent to user: {telegram_id}")
        
    except Exception as e:
        logger.exception(f"Error in share_referral_link handler: user={telegram_id}: {e}")
        await callback.answer(i18n_get_text(language, "errors.profile_load"), show_alert=True)


@user_router.callback_query(F.data == "referral_stats")
async def callback_referral_stats(callback: CallbackQuery):
    """Экран «Подробнее» — расширенный презентационный текст. Delete + answer to support navigation from photo (loyalty screen)."""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)
    
    try:
        try:
            await callback.message.delete()
        except Exception:
            pass
        
        stats = await database.get_referral_statistics(telegram_id)
        total_invited = stats.get("total_invited", 0)
        current_level_name = stats.get("current_level_name", "Silver Access")
        next_level_name = stats.get("next_level_name")
        remaining_connections = stats.get("remaining_connections", 0)
        
        if next_level_name and remaining_connections > 0:
            status_footer = i18n_get_text(
                language,
                "referral.status_footer",
                remaining_invites=remaining_connections
            )
        else:
            status_footer = i18n_get_text(language, "referral.max_level_reached")
        
        bot_info = await callback.bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
        text = i18n_get_text(
            language,
            "referral.stats_screen",
            referral_link=referral_link,
            current_status_name=current_level_name,
            status_footer=status_footer
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_referral"
            )]
        ])
        
        await callback.bot.send_message(callback.message.chat.id, text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error in referral_stats handler: user={telegram_id}: {e}")
        await callback.answer(i18n_get_text(language, "errors.profile_load"), show_alert=True)


@user_router.callback_query(F.data == "referral_how_it_works")
async def callback_referral_how_it_works(callback: CallbackQuery):
    """Экран «Как работает программа» для реферальной программы"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)
    
    try:
        text = i18n_get_text(language, "referral.how_it_works_text")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_referral"
            )],
        ])
        
        await safe_edit_text(callback.message, text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"Error in referral_how_it_works handler: user={telegram_id}: {e}")
        await callback.answer(i18n_get_text(language, "errors.profile_load"), show_alert=True)
