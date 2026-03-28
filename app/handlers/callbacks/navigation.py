"""
Simple navigation callbacks: menu_main, back_to_main, settings, about, support, etc.
"""
import asyncio
import io
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.filters import StateFilter

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.screens import show_profile
from app.handlers.common.keyboards import (
    get_main_menu_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
    get_connect_keyboard,
)
router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "menu_main")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    """Главное меню. Delete + answer to support navigation from photo message (loyalty screen)."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    # Clear all FSM state on navigation (withdrawal, promo, etc.)
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()

    try:
        await callback.message.delete()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(callback.from_user.id)

    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, callback.from_user.id)
    await callback.bot.send_message(callback.message.chat.id, text, reply_markup=keyboard)


@router.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню с экрана выдачи ключа"""
    try:
        await callback.answer()
    except Exception:
        pass

    await state.clear()
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)
    await safe_edit_text(callback.message, text, reply_markup=keyboard)


async def _get_main_text(telegram_id: int, language: str) -> str:
    """Определяет текст главного экрана: обычный или бизнес."""
    try:
        sub = await database.get_subscription(telegram_id)
        sub_type = (sub.get("subscription_type") or "basic").strip().lower() if sub else "basic"
        if config.is_biz_tariff(sub_type):
            return i18n_get_text(language, "biz.main_screen")
    except Exception:
        pass
    text = i18n_get_text(language, "main.welcome")
    return await format_text_with_incident(text, language)


@router.callback_query(F.data == "menu_ecosystem")
async def callback_ecosystem(callback: CallbackQuery):
    """⚪️ Наша экосистема"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    title = i18n_get_text(language, "main.ecosystem_title", "main.ecosystem_title")
    text = i18n_get_text(language, "main.ecosystem_text", "main.ecosystem_text")
    full_text = f"{title}\n\n{text}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "main.about"), callback_data="menu_about")],
        [InlineKeyboardButton(text="✍️ Трекер Only", url="https://t.me/ItsOnlyWbot")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await safe_edit_text(callback.message, full_text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "biz_profile")
async def callback_biz_profile(callback: CallbackQuery):
    """🏢 Мой бизнес — профиль бизнес-подписчика"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    await show_profile(callback, language)


@router.callback_query(F.data == "biz_ecosystem")
async def callback_biz_ecosystem(callback: CallbackQuery):
    """🌐 Экосистема для бизнес-пользователей"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "biz.ecosystem_text")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "biz_control_panel")
async def callback_biz_control_panel(callback: CallbackQuery):
    """🎛 Панель управления"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "biz.control_panel_title")

    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        text += f"\n\n🔗 Ваша ссылка подключения готова."

    from app.handlers.common.keyboards import get_biz_control_panel_keyboard
    keyboard = get_biz_control_panel_keyboard(language)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "biz_copy_login")
async def callback_biz_copy_login(callback: CallbackQuery):
    """📋 Скопировать логин (VPN ключ)"""
    telegram_id = callback.from_user.id
    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        await callback.answer("Скопируйте ссылку выше")
    else:
        await callback.answer("Ключ не найден", show_alert=True)


@router.callback_query(F.data == "biz_copy_password")
async def callback_biz_copy_password(callback: CallbackQuery):
    """🔑 Скопировать пароль (VPN ключ Plus)"""
    telegram_id = callback.from_user.id
    sub = await database.get_subscription(telegram_id)
    vpn_key = sub.get("vpn_key", "") if sub else ""
    if vpn_key:
        await callback.message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        await callback.answer("Скопируйте ссылку выше")
    else:
        await callback.answer("Ключ не найден", show_alert=True)


@router.callback_query(F.data == "menu_settings")
async def callback_settings(callback: CallbackQuery):
    """⚙️ Настройки"""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    title = i18n_get_text(language, "main.settings_title", "main.settings_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "lang.change"), callback_data="change_language")],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "main.ecosystem", "main.ecosystem"),
            callback_data="menu_ecosystem"
        )],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    await safe_edit_text(callback.message, title, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "menu_about")
async def callback_about(callback: CallbackQuery):
    """О сервисе. Entry from ecosystem."""
    from app.handlers.common.screens import _open_about_screen
    await _open_about_screen(callback, callback.bot)


@router.callback_query(F.data == "menu_service_status")
async def callback_service_status(callback: CallbackQuery):
    """Статус сервиса"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "main.service_status_text", "service_status_text")

    incident = await database.get_incident_settings()
    if incident["is_active"]:
        incident_text = incident.get("incident_text") or i18n_get_text(language, "main.incident_banner", "incident_banner")
        warning = i18n_get_text(language, "main.incident_status_warning", incident_text=incident_text)
        text = text + warning

    await safe_edit_text(callback.message, text, reply_markup=get_service_status_keyboard(language), bot=callback.bot)


@router.callback_query(F.data == "about_privacy")
async def callback_privacy(callback: CallbackQuery):
    """Политика конфиденциальности"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "main.privacy_policy_text", "privacy_policy_text")
    await safe_edit_text(callback.message, text, reply_markup=get_about_keyboard(language), parse_mode="HTML", bot=callback.bot)


@router.callback_query(F.data == "special_offer_buy")
async def callback_special_offer_buy(callback: CallbackQuery, state: FSMContext):
    """Спецпредложение -15% — перенаправляет на экран покупки."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id

    # Проверяем что спецпредложение еще активно
    special_offer = await database.get_special_offer_info(telegram_id)
    if not special_offer:
        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            "⏰ Срок спецпредложения истёк. Вы можете приобрести подписку по обычной цене."
        )
        return

    # Открываем экран покупки — скидка 15% применится автоматически через calculate_final_price
    from app.handlers.common.screens import _open_buy_screen
    await _open_buy_screen(callback, callback.bot, state)


@router.callback_query(F.data == "trial_discount_15")
async def callback_trial_discount_15(callback: CallbackQuery, state: FSMContext):
    """Скидка 15% из уведомления за 3 часа до окончания триала — автоматически применяет скидку"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    try:
        from datetime import timedelta, timezone
        from datetime import datetime as dt
        expires_at = dt.now(timezone.utc) + timedelta(days=7)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=15,
            expires_at=expires_at,
            created_by=0,  # system
        )
        await callback.message.answer(
            "🎁 Скидка 15% автоматически применена! Действует 7 дней.\n\nВыберите тариф:"
        )
    except Exception as e:
        logger.warning(f"Failed to apply trial discount for {telegram_id}: {e}")

    from app.handlers.common.screens import _open_buy_screen
    await _open_buy_screen(callback, callback.bot, state)


@router.callback_query(F.data == "paid_discount_15")
async def callback_paid_discount_15(callback: CallbackQuery, state: FSMContext):
    """Скидка 15% из уведомления за 3 часа до окончания платной подписки"""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    try:
        from datetime import timedelta, timezone
        from datetime import datetime as dt
        expires_at = dt.now(timezone.utc) + timedelta(days=7)
        await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=15,
            expires_at=expires_at,
            created_by=0,  # system
        )
        await callback.message.answer(
            "🎁 Скидка 15% автоматически применена! Действует 7 дней.\n\nВыберите тариф:"
        )
    except Exception as e:
        logger.warning(f"Failed to apply paid discount for {telegram_id}: {e}")

    from app.handlers.common.screens import _open_buy_screen
    await _open_buy_screen(callback, callback.bot, state)


@router.callback_query(F.data == "menu_instruction")
@router.callback_query(F.data == "instruction")
async def callback_instruction(callback: CallbackQuery):
    """Инструкция. Entry from main menu (menu_instruction) or profile (instruction)."""
    from app.handlers.common.screens import _open_instruction_screen
    await _open_instruction_screen(callback, callback.bot)



@router.callback_query(F.data == "go_profile", StateFilter(default_state))
@router.callback_query(F.data == "go_profile")
async def callback_go_profile(callback: CallbackQuery, state: FSMContext):
    """Переход в профиль с экрана выдачи ключа - работает независимо от FSM состояния"""
    telegram_id = callback.from_user.id
    
    # Немедленная обратная связь пользователю
    await callback.answer()
    
    # Очищаем FSM состояние, если пользователь был в каком-то процессе
    try:
        current_state = await state.get_state()
        if current_state is not None:
            await state.clear()
            logger.debug(f"Cleared FSM state for user {telegram_id}, was: {current_state}")
    except Exception as e:
        logger.debug(f"FSM state clear failed (may be already clear): {e}")
    
    try:
        logger.info(f"Opening profile via go_profile for user {telegram_id}")
        
        language = await resolve_user_language(telegram_id)
        
        await show_profile(callback, language)
        
        logger.info(f"Profile opened successfully via go_profile for user {telegram_id}")
    except Exception as e:
        logger.exception(f"Error opening profile via go_profile for user {telegram_id}: {e}")
        # Пытаемся отправить сообщение об ошибке
        try:
            user = await database.get_user(telegram_id)
            language = await resolve_user_language(callback.from_user.id)
            error_text = i18n_get_text(language, "errors.profile_load")
            await callback.message.answer(error_text)
        except Exception as e2:
            logger.exception(f"Error sending error message to user {telegram_id}: {e2}")


@router.callback_query(F.data.in_({"copy_key_menu", "copy_key", "copy_key_plus", "copy_vpn_key"}))
async def callback_connect_instead_of_copy(callback: CallbackQuery):
    """Ключи больше не отправляются в боте; показываем кнопку «Подключиться» (Mini App)."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return
    language = await resolve_user_language(callback.from_user.id)
    await callback.message.answer(
        i18n_get_text(language, "connect.press_button"),
        parse_mode="HTML",
        reply_markup=get_connect_keyboard(language),
    )


@router.callback_query(F.data == "get_sub_key")
async def callback_get_sub_key(callback: CallbackQuery):
    """Отправить ключ подписки с инструкцией по подключению."""
    try:
        await callback.answer()
    except Exception:
        pass

    if not await ensure_db_ready_callback(callback, allow_readonly_in_stage=True):
        return

    telegram_id = callback.from_user.id
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        language = await resolve_user_language(telegram_id)
        await callback.message.answer(
            i18n_get_text(language, "get_key.no_subscription", "❌ У вас нет активной подписки."),
        )
        return

    language = await resolve_user_language(telegram_id)
    from vpn_utils import build_sub_url
    sub_url = build_sub_url(telegram_id)

    text = i18n_get_text(language, "get_key.instruction_text",
        "📖 <b>Инструкция по подключению</b>\n\n"
        "<b>Happ</b> — откройте приложение → внизу нажмите на буфер обмена 🗒️ → ключ добавится автоматически\n\n"
        "<b>V2RayTun</b> — откройте приложение → в правом верхнем углу нажмите <b>+</b> → «Импорт из буфера обмена»\n\n"
        "⸻\n\n"
        "👇 Скопируйте ключ одним нажатием:")

    full_text = f"{text}\n\n<code>{sub_url}</code>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.device_button"),
            callback_data="setup_device",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])

    await safe_edit_text(callback.message, full_text, reply_markup=keyboard, bot=callback.bot)


# ── Device setup flow ──────────────────────────────────────────────

_DOWNLOAD_LINKS = {
    "ios": {
        "happ": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
        "v2raytun": "https://apps.apple.com/tr/app/v2raytun/id6476628951",
        "hiddify": "https://apps.apple.com/tr/app/hiddify-proxy-vpn/id6596777532",
    },
    "android": {
        "happ": "https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
        "v2raytun": "https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru",
        "hiddify": "https://play.google.com/store/apps/details?id=app.hiddify.com&hl=ru",
    },
    "macos": {
        "happ": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
        "v2raytun": "https://apps.apple.com/tr/app/v2raytun/id6476628951",
        "hiddify": "https://apps.apple.com/tr/app/hiddify-proxy-vpn/id6596777532",
    },
    "windows": {
        "hiddify": "https://github.com/hiddify/hiddify-app/releases/latest",
        "v2rayn": "https://github.com/2dust/v2rayN/releases/latest",
    },
}


@router.callback_query(F.data == "setup_device")
async def callback_setup_device(callback: CallbackQuery):
    """Выбор устройства для настройки."""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
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

    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data.startswith("setup_platform:"))
async def callback_setup_platform(callback: CallbackQuery):
    """Экран 1: инструкция для устройства + кнопки скачать клиенты + кнопка Далее."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)

    text = i18n_get_text(language, f"setup.instruction_{platform}")

    buttons = []
    links = _DOWNLOAD_LINKS.get(platform, {})
    for client, url in links.items():
        label = i18n_get_text(language, f"setup.download_{client}")
        buttons.append([InlineKeyboardButton(text=label, url=url)])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.qr_button"),
        callback_data=f"setup_qr:{platform}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.next_button"),
        callback_data=f"setup_key:{platform}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="setup_device",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data.startswith("setup_key:"))
async def callback_setup_key(callback: CallbackQuery):
    """Экран 2: ключ подписки + кнопки авто-настройки."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if subscription:
        from vpn_utils import build_sub_url
        sub_url = build_sub_url(telegram_id)
    else:
        sub_url = None

    if not sub_url:
        text = i18n_get_text(language, "get_key.no_subscription")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_platform:{platform}",
        )]])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        return

    connect_text = i18n_get_text(language, f"setup.connect_{platform}")
    key_label = i18n_get_text(language, "setup.copy_key_label")
    text = f"{connect_text}\n\n{key_label}\n<code>{sub_url}</code>"

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.help_button"),
            url="https://t.me/Atlas_SupportSecurity",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_platform:{platform}",
        )],
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)


@router.callback_query(F.data == "setup_done")
async def callback_setup_done(callback: CallbackQuery, state: FSMContext):
    """Готово — отправить 🎉 и через 2 сек показать главный экран."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id

    # 1. Удаляем старый экран (инструкции)
    try:
        await callback.message.delete()
    except Exception:
        pass

    # 2. Отправляем 🎉
    msg = await callback.bot.send_message(chat_id=telegram_id, text="🎉")

    # 3. Ждём 2 секунды
    await asyncio.sleep(2)

    # 4. Удаляем 🎉
    try:
        await msg.delete()
    except Exception:
        pass

    # 5. Отправляем главное меню
    language = await resolve_user_language(telegram_id)
    text = i18n_get_text(language, "main.welcome")
    text = await format_text_with_incident(text)
    keyboard = await get_main_menu_keyboard(telegram_id, language)

    await callback.bot.send_message(
        chat_id=telegram_id,
        text=text,
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("setup_qr:"))
async def callback_setup_qr(callback: CallbackQuery):
    """Генерация QR-кода подписки и отправка в чат."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if subscription:
        from vpn_utils import build_sub_url
        sub_url = build_sub_url(telegram_id)
    else:
        sub_url = None

    if not sub_url:
        text = i18n_get_text(language, "get_key.no_subscription")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_platform:{platform}",
        )]])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        return

    # Generate QR code
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(sub_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    qr_text = i18n_get_text(language, "setup.qr_instruction")

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.help_button"),
            url="https://t.me/Atlas_SupportSecurity",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_platform:{platform}",
        )],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Delete current message and send photo with QR
    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.bot.send_photo(
        chat_id=telegram_id,
        photo=BufferedInputFile(buf.read(), filename="subscription_qr.png"),
        caption=qr_text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
