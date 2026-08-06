"""
Language-related callback handlers: change_language, lang_*, start_lang_*.
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.keyboards import get_language_keyboard, get_main_menu_keyboard

language_router = Router()
logger = logging.getLogger(__name__)

import config as _cfg
MAIN_PHOTO_FILE_ID = (
    "AgACAgQAAxkBAAFU05tqGqRjuvf8dvqvfbY2oFk6alXedwACXg9rGxA30FCAHo8JfpwoZwEAAwIAA3kAAzsE"
    if _cfg.IS_PROD else
    "AgACAgQAAxkBAAIhcWoZ_p3HPwnRbry9fgbsOMMREvaVAAJeD2sbEDfQUDIWtf_E5Dx0AQADAgADeQADOwQ"
)

# Фото язык-picker'а на /start (2026-08).
START_LANG_PHOTO_FILE_ID = "AgACAgQAAxkBAAF-HCtqdNZHr6Mc4RuRslZA_PBJFPwThQACgw5rGxmsqFMBim5BIpxLDwEAAwIAA3kAAz0E"


@language_router.callback_query(F.data == "change_language")
async def callback_change_language(callback: CallbackQuery):
    """Изменить язык"""
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Экран выбора языка (канонический вид)
    text = i18n_get_text(language, "lang.select")
    # Если текущее сообщение — фото (главный экран без подписки), удаляем и отправляем новое
    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(
            callback.message.chat.id,
            text,
            reply_markup=get_language_keyboard(language),
            parse_mode="HTML",
        )
    else:
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=get_language_keyboard(language),
            bot=callback.bot
        )
    await callback.answer()


@language_router.callback_query(F.data.in_({"start_lang_ru", "start_lang_en"}))
async def callback_start_language(callback: CallbackQuery):
    """/start язык-picker → сохранить язык → показать главное меню.

    Триал-активация и экран согласия здесь НЕ выполняются — пробный период
    юзер активирует уже из главного меню кнопкой «🎁 Пробный период 3 дня»
    (обычный существующий flow callback_activate_trial)."""
    if not await ensure_db_ready_callback(callback):
        return

    lang_code = callback.data.split("_")[-1]
    if lang_code not in ("ru", "en"):
        lang_code = "ru"
    telegram_id = callback.from_user.id

    await database.update_user_language(telegram_id, lang_code)
    try:
        await callback.answer()
    except Exception:
        pass

    keyboard = await get_main_menu_keyboard(lang_code, telegram_id)
    from app.handlers.callbacks.navigation import _get_main_text
    text = await _get_main_text(telegram_id, lang_code)
    try:
        await callback.message.delete()
    except Exception:
        pass
    try:
        await callback.bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=MAIN_PHOTO_FILE_ID,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:
        await callback.bot.send_message(
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@language_router.callback_query(F.data.startswith("lang_"))
async def callback_language(callback: CallbackQuery):
    """Смена языка из настроек / кнопки «Изменить язык»."""
    if not await ensure_db_ready_callback(callback):
        return

    lang_code = callback.data.split("_")[1]
    if lang_code not in ("ru", "en"):
        lang_code = "ru"
    language = lang_code
    telegram_id = callback.from_user.id

    await database.update_user_language(telegram_id, language)

    keyboard = await get_main_menu_keyboard(language, telegram_id)

    from app.handlers.callbacks.navigation import _get_main_text
    text = await _get_main_text(telegram_id, language)

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=MAIN_PHOTO_FILE_ID,
        caption=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    await callback.answer(
        i18n_get_text(language, "lang.changed_toast"),
        show_alert=False
    )
