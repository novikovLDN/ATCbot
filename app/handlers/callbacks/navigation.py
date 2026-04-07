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

    # Для пользователей без подписки — отправляем фото с текстом
    sub = await database.get_subscription(telegram_id)
    if not sub:
        await callback.bot.send_photo(
            chat_id=callback.message.chat.id,
            photo="AgACAgQAAxkBAAEpZhtp1AAB3Y9P6v5KtnNI5W2KLXLRGeAAAqsMaxtluqBSqDLmexoxay0BAAMCAAN5AAM7BA",
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await callback.bot.send_message(callback.message.chat.id, text, reply_markup=keyboard, parse_mode="HTML")


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

    sub = await database.get_subscription(telegram_id)
    if not sub:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_photo(
            chat_id=callback.message.chat.id,
            photo="AgACAgQAAxkBAAEpZhtp1AAB3Y9P6v5KtnNI5W2KLXLRGeAAAqsMaxtluqBSqDLmexoxay0BAAMCAAN5AAM7BA",
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await safe_edit_text(callback.message, text, reply_markup=keyboard)


async def _get_main_text(telegram_id: int, language: str) -> str:
    """Определяет текст главного экрана: обычный, бизнес, bypass-only или без подписки."""
    try:
        sub = await database.get_subscription(telegram_id)
        sub_type = (sub.get("subscription_type") or "basic").strip().lower() if sub else None
        if sub and sub_type and config.is_biz_tariff(sub_type):
            return i18n_get_text(language, "biz.main_screen")
        if not sub:
            text = i18n_get_text(language, "main.welcome_no_sub")
            return await format_text_with_incident(text, language)
        if sub and sub.get("is_bypass_only"):
            text = i18n_get_text(language, "main.welcome_bypass")
            return await format_text_with_incident(text, language)
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


# ── Connect instruction ──────────────────────────────────────────

@router.callback_query(F.data == "connect_instruction")
async def callback_connect_instruction(callback: CallbackQuery):
    """Подключиться → сразу выбор устройства."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
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
                if expires_at:
                    override = 5 * 1024**3 if sub_type == "trial" else 10 * 1024**3
                    remnawave_service._fire_and_forget(
                        remnawave_service.create_remnawave_user(
                            telegram_id, sub_type, expires_at,
                            traffic_limit_override=override,
                        )
                    )
        else:
            remnawave_service._fire_and_forget(
                remnawave_service.ensure_squad(telegram_id)
            )

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
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


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

    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_platform:"))
async def callback_setup_platform(callback: CallbackQuery):
    """Единый экран: скачать приложение + авто-настройка с кнопками."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Get subscription and keys
    subscription = await database.get_subscription(telegram_id)
    sub_url = None
    bypass_url = None
    if subscription:
        from vpn_utils import build_sub_url
        sub_url = build_sub_url(telegram_id)

        sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
        if config.REMNAWAVE_ENABLED and sub_type in ("basic", "plus"):
            from app.services import remnawave_api
            rmn_uuid = await database.get_remnawave_uuid(telegram_id)
            if rmn_uuid:
                traffic = await remnawave_api.get_user_traffic(rmn_uuid)
                if traffic:
                    bypass_url = traffic.get("subscriptionUrl", "") or None

    # Build text
    text = i18n_get_text(language, f"setup.combined_{platform}")

    buttons = []

    # Auto-setup buttons (if user has subscription)
    if sub_url:
        from urllib.parse import quote, urlparse
        if config.PUBLIC_BASE_URL:
            base_url = config.PUBLIC_BASE_URL
        else:
            parsed = urlparse(config.WEBHOOK_URL)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        _platform_clients = {
            "ios": ["happ", "v2raytun", "hiddify"],
            "android": ["happ", "v2raytun", "hiddify"],
            "macos": ["happ", "v2raytun", "hiddify"],
            "windows": ["hiddify", "v2rayn"],
        }
        _client_deeplink = {
            "happ": "happ", "v2raytun": "v2raytun",
            "hiddify": "hiddify", "v2rayn": "hiddify",
        }
        _client_names = {
            "happ": "Happ", "v2raytun": "V2RayTun",
            "hiddify": "Hiddify", "v2rayn": "v2rayN",
        }

        clients = _platform_clients.get(platform, [])
        for client in clients:
            dl = _client_deeplink[client]
            name = _client_names[client]
            row = [InlineKeyboardButton(
                text=f"\U0001f310 {name}",
                url=f"{base_url}/open/{dl}?url={quote(sub_url, safe='')}",
            )]
            if bypass_url:
                row.append(InlineKeyboardButton(
                    text=f"\U0001f90d {name}",
                    url=f"{base_url}/open/{dl}?url={quote(bypass_url, safe='')}",
                ))
            buttons.append(row)

    # Download links
    links = _DOWNLOAD_LINKS.get(platform, {})
    download_row = []
    for client, url in links.items():
        label = i18n_get_text(language, f"setup.download_{client}")
        download_row.append(InlineKeyboardButton(text=label, url=url))
        if len(download_row) == 2:
            buttons.append(download_row)
            download_row = []
    if download_row:
        buttons.append(download_row)

    # Manual setup + QR
    buttons.append([
        InlineKeyboardButton(
            text=i18n_get_text(language, "setup.manual_button"),
            callback_data=f"setup_manual:{platform}",
        ),
        InlineKeyboardButton(
            text=i18n_get_text(language, "setup.qr_button"),
            callback_data=f"setup_qr:{platform}",
        ),
    ])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.done_button"),
        callback_data="setup_done",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="connect_instruction",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_key:"))
async def callback_setup_key(callback: CallbackQuery):
    """Legacy redirect — перенаправляем на объединённый экран."""
    try:
        await callback.answer()
    except Exception:
        pass
    platform = callback.data.split(":")[1]
    # Rewrite callback data and redirect
    callback.data = f"setup_platform:{platform}"
    await callback_setup_platform(callback)


@router.callback_query(F.data.startswith("setup_manual:"))
async def callback_setup_manual(callback: CallbackQuery):
    """Экран подробной инструкции по ручной настройке (стандарт + обход)."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    sub_url = None
    bypass_url = None
    if subscription:
        from vpn_utils import build_sub_url
        sub_url = build_sub_url(telegram_id)

        sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
        if config.REMNAWAVE_ENABLED and sub_type in ("basic", "plus"):
            from app.services import remnawave_api
            rmn_uuid = await database.get_remnawave_uuid(telegram_id)
            if rmn_uuid:
                traffic = await remnawave_api.get_user_traffic(rmn_uuid)
                if traffic:
                    bypass_url = traffic.get("subscriptionUrl", "") or None

    connect_text = i18n_get_text(language, f"setup.connect_{platform}")

    # Build keys section
    keys_section = ""
    if sub_url:
        keys_section += i18n_get_text(language, "setup.key_vpn_label") + "\n<blockquote><code>" + sub_url + "</code></blockquote>"
    if bypass_url:
        keys_section += "\n" + i18n_get_text(language, "setup.key_bypass_label") + "\n<blockquote><code>" + bypass_url + "</code></blockquote>"

    if keys_section:
        text = f"{connect_text}\n\n{keys_section}"
    else:
        text = connect_text

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_platform:{platform}",
        )],
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


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
    text = await format_text_with_incident(text, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)

    await callback.bot.send_message(
        chat_id=telegram_id,
        text=text,
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("setup_qr:"))
async def callback_setup_qr(callback: CallbackQuery):
    """Экран выбора: QR обычных серверов или обхода белых списков."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "get_key.no_subscription")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_platform:{platform}",
        )]])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
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
            callback_data=f"setup_qr_standard:{platform}",
        )],
    ]
    if has_bypass:
        buttons.append([InlineKeyboardButton(
            text="🤍 " + i18n_get_text(language, "setup.qr_bypass_btn"),
            callback_data=f"setup_qr_bypass:{platform}",
        )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=f"setup_platform:{platform}",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_qr_standard:"))
async def callback_setup_qr_standard(callback: CallbackQuery):
    """QR-код обычных серверов."""
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
            callback_data=f"setup_qr:{platform}",
        )]])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
        return

    await _send_qr_screen(callback, platform, sub_url, language, label_key="setup.key_vpn_label")


@router.callback_query(F.data.startswith("setup_qr_bypass:"))
async def callback_setup_qr_bypass(callback: CallbackQuery):
    """QR-код обхода белых списков."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    bypass_url = None
    if subscription:
        sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
        if config.REMNAWAVE_ENABLED and sub_type in ("basic", "plus"):
            from app.services import remnawave_api
            rmn_uuid = await database.get_remnawave_uuid(telegram_id)
            if rmn_uuid:
                traffic = await remnawave_api.get_user_traffic(rmn_uuid)
                if traffic:
                    bypass_url = traffic.get("subscriptionUrl", "") or None

    if not bypass_url:
        text = i18n_get_text(language, "setup.qr_bypass_unavailable")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_qr:{platform}",
        )]])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")
        return

    await _send_qr_screen(callback, platform, bypass_url, language, label_key="setup.key_bypass_label")


async def _send_qr_screen(callback: CallbackQuery, platform: str, url: str, language: str, label_key: str):
    """Генерация QR-кода и отправка экрана с инструкцией."""
    telegram_id = callback.from_user.id

    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    qr_text = i18n_get_text(language, "setup.qr_instruction")
    qr_text += "\n\n" + i18n_get_text(language, label_key) + "\n<blockquote><code>" + url + "</code></blockquote>"

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_qr:{platform}",
        )],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

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


# ===================== COMBO SUBSCRIPTION =====================

@router.callback_query(F.data == "buy_combo")
async def callback_buy_combo(callback: CallbackQuery):
    """Экран выбора комбо-тарифа (Basic/Plus)."""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)

    text = i18n_get_text(language, "combo.screen_title")
    text += "\n\n" + i18n_get_text(language, "combo.tariff_basic")
    text += "\n\n" + i18n_get_text(language, "combo.tariff_plus")

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "combo.select_basic"),
            callback_data="combo_tariff:combo_basic",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "combo.select_plus"),
            callback_data="combo_tariff:combo_plus",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    # Main screen may be a photo — delete and send new message
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("combo_tariff:"))
async def callback_combo_tariff(callback: CallbackQuery):
    """Выбор периода комбо-тарифа."""
    try:
        await callback.answer()
    except Exception:
        pass

    combo_type = callback.data.split(":")[1]  # combo_basic or combo_plus
    if combo_type not in config.COMBO_TARIFFS:
        return

    language = await resolve_user_language(callback.from_user.id)
    tariff = config.COMBO_TARIFFS[combo_type]

    if combo_type == "combo_basic":
        text = i18n_get_text(language, "combo.tariff_basic")
    else:
        text = i18n_get_text(language, "combo.tariff_plus")

    text += "\n\nВыберите период:"

    buttons = []
    period_keys = {30: "combo.period_1", 90: "combo.period_3", 180: "combo.period_6", 365: "combo.period_12"}
    for period_days, info in tariff.items():
        btn_text = i18n_get_text(language, period_keys[period_days], gb=info["gb"], price=info["price"])
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"combo_period:{combo_type}:{period_days}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_combo",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("combo_period:"))
async def callback_combo_period(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и оплата комбо-тарифа — используем стандартный экран оплаты."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    combo_type = parts[1]
    try:
        period_days = int(parts[2])
    except (ValueError, IndexError):
        return

    if combo_type not in config.COMBO_TARIFFS:
        return
    tariff = config.COMBO_TARIFFS[combo_type]
    if period_days not in tariff:
        return

    info = tariff[period_days]
    base_tariff = info["base_tariff"]
    price_kopecks = info["price"] * 100
    gb = info["gb"]

    # Сохраняем данные в FSM для стандартного платёжного потока
    await state.update_data(
        tariff_type=base_tariff,
        period_days=period_days,
        final_price_kopecks=price_kopecks,
        combo_bypass_gb=gb,
    )
    from app.handlers.common.states import PurchaseState
    await state.set_state(PurchaseState.choose_payment_method)

    from handlers import show_payment_method_selection
    await show_payment_method_selection(callback, base_tariff, period_days, price_kopecks)


@router.callback_query(F.data.startswith("combo_pay_balance:"))
async def callback_combo_pay_balance(callback: CallbackQuery):
    """Оплата комбо с баланса: активация подписки + начисление трафика обхода."""
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    combo_type = parts[1]
    try:
        period_days = int(parts[2])
    except (ValueError, IndexError):
        return

    if combo_type not in config.COMBO_TARIFFS:
        return
    info = config.COMBO_TARIFFS[combo_type].get(period_days)
    if not info:
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    price = info["price"]
    gb = info["gb"]
    base_tariff = info["base_tariff"]

    balance = await database.get_user_balance(telegram_id)
    if balance < price:
        text = i18n_get_text(language, "traffic.insufficient_balance")
        buttons = [[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"combo_period:{combo_type}:{period_days}",
        )]]
        await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")
        return

    # 1. Create pending purchase with base tariff
    from app.services.subscriptions import service as subscription_service
    price_kopecks = price * 100
    try:
        purchase_id = await subscription_service.create_subscription_purchase(
            telegram_id=telegram_id,
            tariff=base_tariff,
            period_days=period_days,
            price_kopecks=price_kopecks,
            is_combo=True,
        )
    except Exception as e:
        logger.error(f"Combo purchase creation failed: {e}")
        text = "❌ Ошибка создания покупки. Попробуйте позже."
        buttons = [[InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_combo")]]
        await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")
        return

    # 2. Deduct balance
    await database.decrease_balance(telegram_id, price, source="combo_purchase", description=f"Combo {base_tariff} {period_days}d + {gb}GB bypass")

    # 3. Finalize purchase (activates subscription, creates VPN key, etc.)
    try:
        result = await subscription_service.finalize_purchase(
            purchase_id=purchase_id,
            payment_provider="balance",
            amount_rubles=float(price),
        )
        if not result.get("success"):
            logger.error(f"Combo finalize failed: {result}")
    except Exception as e:
        logger.error(f"Combo finalize error: {e}")

    # 4. Add bypass traffic
    from app.services import remnawave_service
    traffic_bytes = gb * 1024**3
    try:
        rmn_success = await remnawave_service.add_traffic(telegram_id, traffic_bytes)
        if not rmn_success:
            logger.warning(f"COMBO_PAY_BALANCE_TRAFFIC_FAIL user={telegram_id} gb={gb}")
    except Exception as traffic_err:
        logger.warning(f"COMBO_PAY_BALANCE_TRAFFIC_ERROR user={telegram_id}: {traffic_err}")

    # 5. Record traffic purchase + mark as combo
    await database.record_traffic_purchase(telegram_id, gb, 0)
    await database.set_combo_flag(telegram_id, True)

    months = period_days // 30
    text = (
        f"✅ <b>Комбо-подписка активирована!</b>\n\n"
        f"📦 Тариф: <b>Комбо {base_tariff.capitalize()}</b> · {months} мес.\n"
        f"🌐 Обход: <b>{gb} ГБ</b> начислено\n\n"
        f"Нажмите «Подключиться» чтобы настроить устройство."
    )
    buttons = [
        [InlineKeyboardButton(text="📲 Подключиться", callback_data="connect_instruction")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ]
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")
