"""
User commands: /connect, /white, /main, /hwadd.
"""
import logging

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

import config
import database
from app.handlers.common.keyboards import get_main_menu_keyboard
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

user_router = Router()
logger = logging.getLogger(__name__)


@user_router.message(Command("connect"))
async def cmd_connect(message: Message):
    """Подключиться → сразу выбор устройства."""
    if message.chat.type != "private":
        return
    language = await resolve_user_language(message.from_user.id)
    text = i18n_get_text(language, "setup.select_device")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 iOS", callback_data="setup_platform:ios"),
            InlineKeyboardButton(text="🤖 Android", callback_data="setup_platform:android"),
        ],
        [
            InlineKeyboardButton(text="🍎 macOS", callback_data="setup_platform:macos"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="setup_platform:windows"),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@user_router.message(Command("white"))
async def cmd_white(message: Message):
    """Показать экран «Мой трафик»."""
    if message.chat.type != "private":
        return

    from app.handlers.traffic import show_traffic_info_message
    await show_traffic_info_message(message)


@user_router.message(Command("hwadd"))
async def cmd_hwadd(message: Message):
    """📲 Добавить устройство → экран выбора типа подключения."""
    if message.chat.type != "private":
        return

    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "get_key.no_subscription")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )]])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        return

    # Check if bypass is available
    has_bypass = False
    sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
    if config.REMNAWAVE_ENABLED and sub_type in ("basic", "plus"):
        from app.services import remnawave_api
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if rmn_uuid:
            traffic = await remnawave_api.get_user_traffic(rmn_uuid)
            if traffic and traffic.get("subscriptionUrl"):
                has_bypass = True

    text = i18n_get_text(language, "setup.qr_choose_type")
    buttons = [
        [InlineKeyboardButton(
            text="🌐 " + i18n_get_text(language, "setup.qr_standard_btn"),
            callback_data="setup_qr_standard:ios",
        )],
    ]
    if has_bypass:
        buttons.append([InlineKeyboardButton(
            text="🤍 " + i18n_get_text(language, "setup.qr_bypass_btn"),
            callback_data="setup_qr_bypass:ios",
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@user_router.message(Command("main"))
async def cmd_main(message: Message):
    """Вернуться на главный экран."""
    if message.chat.type != "private":
        return

    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)

    from app.handlers.callbacks.navigation import _get_main_text
    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)

    sub = await database.get_subscription(telegram_id)
    if not sub:
        await message.answer_photo(
            photo="AgACAgQAAxkBAAIdb2nTSHdR3Nb0qtBvdSXPO60hsAH8AAKrDGsbZbqgUoydQVuMzuNKAQADAgADeQADOwQ",
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await message.answer(text, reply_markup=keyboard)
