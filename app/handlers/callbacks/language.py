"""
Language-related callback handlers: change_language, lang_*.
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
    "AgACAgQAAxkBAAEyoahp3fnvRHMCD5foRXdnXO0HOWygrgACTA5rG1ik8VK4JmFF6VTnYAEAAwIAA3kAAzsE"
    if _cfg.IS_PROD else
    "AgACAgQAAxkBAAIfVmnd-cpk8g4zo39vumhaX4XENDtUAAJMDmsbWKTxUlPwG2HiC9EPAQADAgADeQADOwQ"
)


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
        )
    else:
        await safe_edit_text(
            callback.message,
            text,
            reply_markup=get_language_keyboard(language),
            bot=callback.bot
        )
    await callback.answer()


@language_router.callback_query(F.data.startswith("lang_"))
async def callback_language(callback: CallbackQuery):
    """Обработчик выбора языка"""
    # SAFE STARTUP GUARD: Проверка готовности БД
    if not await ensure_db_ready_callback(callback):
        return

    language = callback.data.split("_")[1]
    telegram_id = callback.from_user.id

    await database.update_user_language(telegram_id, language)

    keyboard = await get_main_menu_keyboard(language, telegram_id)

    sub = await database.get_subscription(telegram_id)
    if not sub:
        # Без подписки — фото + новый продающий текст
        text = i18n_get_text(language, "main.welcome_no_sub")
        text = await format_text_with_incident(text, language)
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
    else:
        # С подпиской — обычный текст
        text = i18n_get_text(language, "main.welcome")
        text = await format_text_with_incident(text, language)
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)

    await callback.answer(
        i18n_get_text(language, "lang.changed_toast"),
        show_alert=False
    )
