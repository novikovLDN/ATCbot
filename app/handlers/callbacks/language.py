"""
Language-related callback handlers: change_language, lang_*, start_lang_*.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

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

TERMS_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-08-06-50"


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
    """/start язык-picker → сохранить язык → активировать триал (если можно)
    → показать экран согласия (Подключиться + Пользовательское соглашение)."""
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

    # Триал активируем только если нет активной подписки И триал ещё не использован.
    # По спецификации: «если не было ранее подписки».
    is_eligible = False
    try:
        trial_ok = await database.is_eligible_for_trial(telegram_id)
        subscription = await database.get_subscription(telegram_id)
        is_eligible = trial_ok and subscription is None
    except Exception as e:
        logger.warning("start_lang: eligibility check failed user=%s err=%s", telegram_id, e)

    if is_eligible:
        await _activate_trial_and_show_consent(callback, lang_code)
    else:
        await _show_main_menu_after_lang(callback, lang_code)


async def _activate_trial_and_show_consent(callback: CallbackQuery, language: str) -> None:
    """Grant triaл через database.grant_access и показать экран согласия."""
    telegram_id = callback.from_user.id
    try:
        duration = timedelta(days=3)
        now = datetime.now(timezone.utc)
        trial_expires_at = now + duration

        result = await database.grant_access(
            telegram_id=telegram_id,
            duration=duration,
            source="trial",
            admin_telegram_id=None,
        )
        uuid = result.get("uuid")
        if not uuid:
            logger.error("start_lang: grant_access returned no uuid user=%s", telegram_id)
            await _show_main_menu_after_lang(callback, language)
            return

        mark_ok = await database.mark_trial_used(telegram_id, trial_expires_at)
        if not mark_ok:
            logger.error("start_lang: mark_trial_used failed user=%s", telegram_id)

        # Реферальная активация в фоне — не блокируем UI
        try:
            from app.handlers.callbacks.subscription import _activate_referral_and_notify
            asyncio.create_task(_activate_referral_and_notify(callback.bot, telegram_id))
        except Exception as e:
            logger.debug("start_lang: skip referral notify: %s", e)

        # Отложенное уведомление «обход подключён»
        try:
            from app.services.trials.bypass_activation_delay import (
                schedule_bypass_activated_notification,
            )
            schedule_bypass_activated_notification(callback.bot, telegram_id)
        except Exception as e:
            logger.warning("start_lang: schedule_bypass_activated failed user=%s: %s", telegram_id, e)

        logger.info("start_lang: trial activated user=%s expires=%s", telegram_id, trial_expires_at.isoformat())

    except Exception as e:
        logger.exception("start_lang: trial activation failed user=%s err=%s", telegram_id, e)
        # Даже если триал не активировался — показываем main menu, не блокируем юзера
        await _show_main_menu_after_lang(callback, language)
        return

    # Финальный экран согласия
    text = i18n_get_text(language, "start_trial.received")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "start_trial.btn_connect"),
            callback_data="connect_instruction",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "start_trial.btn_terms"),
            url=TERMS_URL,
        )],
    ])
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _show_main_menu_after_lang(callback: CallbackQuery, language: str) -> None:
    """Fallback для юзеров без права на триал (уже активировали ранее):
    просто показываем главное меню с фото."""
    telegram_id = callback.from_user.id
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    from app.handlers.callbacks.navigation import _get_main_text
    text = await _get_main_text(telegram_id, language)
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
