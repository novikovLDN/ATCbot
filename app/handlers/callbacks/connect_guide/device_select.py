"""Вход в инструкцию: экран выбора устройства.

ЧТО ЗДЕСЬ
    Точка входа в пошаговую установку — та самая, куда ведут кнопка
    «Подключиться», команда /instruction и уведомление о перевыпуске ключа.
    Дальше человек уходит в setup_step1:{платформа}.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственный экран инструкции, который зовут ИЗВНЕ: функция
    _open_connect_screen нужна команде /instruction (app/handlers/user/
    support.py). Её потеря = мёртвая команда, поэтому она должна быть
    видна, а не лежать на 65-й строке файла на тысячу строк.

ЧТО ЛЕГКО СЛОМАТЬ
    _open_connect_screen принимает и CallbackQuery, и Message. Отсюда
    getattr(event, "message", None): у команды удалять нечего, сообщение
    принадлежит человеку. Начнёте обращаться к event.message напрямую —
    /instruction упадёт, а кнопка продолжит работать, и дефект уедет в прод.

    Экран ВСЕГДА отправляется картинкой заново, а старый удаляется:
    заменить фото в уже отправленном сообщении Telegram не даёт. Замените
    удаление на редактирование — экран перестанет обновляться.

    Провижининг Remnawave тут fire-and-forget и намеренно не ждёт ответа:
    человек пришёл читать инструкцию, а не смотреть на спиннер.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.keyboards import MINI_APP_URL
from app.handlers.callbacks.connect_guide.catalog import _DEVICE_SELECT_PHOTO

device_select_router = Router()


@device_select_router.callback_query(F.data == "connect_instruction")
async def callback_connect_instruction(callback: CallbackQuery):
    """Подключиться → сразу выбор устройства."""
    try:
        await callback.answer()
    except Exception:
        pass
    await _open_connect_screen(callback, callback.bot)


async def _open_connect_screen(event, bot):
    """Экран выбора устройства — общий для кнопки и команды /instruction.

    Принимает и CallbackQuery, и Message: инструкция должна открываться
    одинаково, откуда бы человек ни пришёл. Раньше у команды был свой,
    более бедный экран.
    """
    telegram_id = event.from_user.id
    language = await resolve_user_language(telegram_id)

    # Auto-provision Remnawave user for existing subscribers + ensure squad (fire-and-forget)
    if config.REMNAWAVE_ENABLED:
        from app.services import remnawave_service
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            subscription = await database.get_subscription(telegram_id)
            if subscription:
                sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
                expires_at = subscription.get("expires_at")
                if expires_at and sub_type == "trial":
                    override = 2 * 1024**3  # Trial: 2 GB bypass
                    remnawave_service._fire_and_forget(
                        remnawave_service.create_remnawave_user(
                            telegram_id, sub_type, expires_at,
                            traffic_limit_override=override,
                        )
                    )
                elif expires_at and sub_type != "trial":
                    override = 1 * 1024**3  # 1 GB starter pack
                    remnawave_service._fire_and_forget(
                        remnawave_service.create_remnawave_user(
                            telegram_id, sub_type, expires_at,
                            traffic_limit_override=override,
                        )
                    )
        else:
            # Existing Remnawave user — ensure expiry is far future (bypass works by GB, not date)
            remnawave_service._fire_and_forget(
                remnawave_service.extend_remnawave_for_bypass(telegram_id)
            )
            remnawave_service._fire_and_forget(
                remnawave_service.ensure_squad(telegram_id)
            )

    text = i18n_get_text(language, "setup.select_device")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 iPhone / iPad", callback_data="setup_step1:ios"),
            InlineKeyboardButton(text="🤖 Android", callback_data="setup_step1:android"),
        ],
        [
            InlineKeyboardButton(text="🍎 Mac", callback_data="setup_step1:macos"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="setup_step1:windows"),
        ],
        # Мини-приложение с визуальной инструкцией. Раньше на него вёл
        # отдельный экран «Инструкция» — промежуточная страница из одной
        # строки текста и одной кнопки. Попасть на неё можно было только
        # командой /instruction, которую надо знать: в меню её не было.
        # Кнопку перенесли сюда, промежуточный экран убрали.
        [InlineKeyboardButton(
            text=i18n_get_text(language, "instruction._open_guide", "📖 Инструкция по установке"),
            web_app=WebAppInfo(url=f"{MINI_APP_URL}?startapp=guide"),
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])

    # Экран всегда отправляется картинкой с подписью.
    _ds_photo = _DEVICE_SELECT_PHOTO.get("prod" if config.IS_PROD else "stage", "")

    # Экран, с которого пришли по кнопке, убираем: заменить картинку в
    # уже отправленном сообщении нельзя. При заходе командой удалять
    # нечего — там сообщение самого человека.
    prev = getattr(event, "message", None)
    if prev is not None:
        try:
            await prev.delete()
        except Exception:
            pass

    await bot.send_photo(
        chat_id=telegram_id,
        photo=_ds_photo,
        caption=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@device_select_router.callback_query(F.data == "setup_device")
async def callback_setup_device(callback: CallbackQuery):
    """Выбор устройства для настройки."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "setup.select_device")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 iPhone / iPad", callback_data="setup_step1:ios"),
            InlineKeyboardButton(text="🤖 Android", callback_data="setup_step1:android"),
        ],
        [
            InlineKeyboardButton(text="🍎 Mac", callback_data="setup_step1:macos"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="setup_step1:windows"),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])

    has_photo = getattr(callback.message, "photo", None) and len(callback.message.photo) > 0
    if has_photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(
            chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML",
        )
    else:
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")
