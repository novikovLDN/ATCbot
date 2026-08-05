"""Шаг «поставьте приложение»: ссылки на клиенты под выбранную платформу.

ЧТО ЗДЕСЬ
    callback_setup_step1     текущий шаг 1 инструкции
    callback_setup_platform  объединённый экран (скачать + авто-настройка)
    callback_setup_key       старый адрес кнопки, редирект на объединённый

ПОЧЕМУ ВЫДЕЛЕНО
    Один вопрос — «где взять приложение» — и один источник данных
    (catalog._DOWNLOAD_LINKS). Ключи, QR и завершение живут отдельно.

ЧТО ЛЕГКО СЛОМАТЬ
    Incy показываем не везде. У Incy нет клиента под Windows, поэтому
    кнопки только для ios/android/macos. Добавите Windows — человек нажмёт
    и попадёт в пустоту.

    callback_setup_key переписывает callback.data и зовёт обработчик
    объединённого экрана НАПРЯМУЮ. Это не опечатка: старые сообщения в
    чатах живут вечно и до сих пор шлют setup_key:. Уберёте — кнопка в
    старом сообщении молча перестанет отвечать.

    Экран шага 1 удаляет предыдущее сообщение и шлёт новое: если у
    платформы нет картинки (Windows), уходит текст. Замените удаление на
    редактирование — картинка перестанет меняться между шагами.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text
from app.handlers.callbacks.connect_guide.catalog import (
    _DOWNLOAD_LINKS,
    _IOS_HAPP_LINKS,
    _get_photo_id,
)

install_app_router = Router()


@install_app_router.callback_query(F.data.startswith("setup_step1:"))
async def callback_setup_step1(callback: CallbackQuery):
    """Step 1: Install Happ app — shows photo + download buttons."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "setup.install_app")

    buttons = []

    if platform in ("ios", "macos"):
        # Incy — на первом месте (по запросу). Для iOS и macOS одна и
        # та же App Store ссылка (Apple Silicon Mac умеет ставить iOS-
        # приложения).
        incy_url = _DOWNLOAD_LINKS.get(platform, {}).get("incy")
        if incy_url:
            buttons.append([InlineKeyboardButton(
                text="📲 Скачать Incy",
                url=incy_url,
            )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.install_happ_ru"),
            url=_IOS_HAPP_LINKS["ru"],
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.install_happ_global"),
            url=_IOS_HAPP_LINKS["global"],
        )])
    elif platform == "android":
        links = _DOWNLOAD_LINKS.get("android", {})
        if "happ" in links:
            buttons.append([InlineKeyboardButton(
                text="📲 Установить Happ",
                url=links["happ"],
            )])
        # Incy для Android — Play Market ссылка
        incy_url = links.get("incy")
        if incy_url:
            buttons.append([InlineKeyboardButton(
                text="📲 Скачать Incy",
                url=incy_url,
            )])
    elif platform == "windows":
        buttons.append([InlineKeyboardButton(
            text="📲 Скачать Happ",
            url="https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.next_step"),
        callback_data=f"setup_step2:{platform}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="connect_instruction",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Platform-specific photo (no photo for Windows)
    photo_key = {
        "ios": "install_app_ios",
        "macos": "install_app_ios",
        "android": "install_app_android",
    }.get(platform)
    photo_id = _get_photo_id(photo_key) if photo_key else ""

    try:
        await callback.message.delete()
    except Exception:
        pass

    if photo_id:
        await callback.bot.send_photo(
            chat_id=telegram_id,
            photo=photo_id,
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await callback.bot.send_message(
            chat_id=telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@install_app_router.callback_query(F.data.startswith("setup_platform:"))
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
        from app.services.user_subscription_links import get_user_primary_subscription_url
        sub_url = await get_user_primary_subscription_url(telegram_id)

    # Bypass key: available independently of main subscription.
    # Goes through the helper so cache misses + missing entities
    # auto-recover (lazy-provision creates the bypass entity if the
    # user has an active subscription but no remnawave_uuid yet).
    if config.REMNAWAVE_ENABLED:
        from app.services.user_subscription_links import get_user_bypass_url
        bypass_url = await get_user_bypass_url(telegram_id)

    # Build text
    text = i18n_get_text(language, f"setup.combined_{platform}")

    buttons = []

    # === Download links FIRST ===
    links = _DOWNLOAD_LINKS.get(platform, {})
    if platform in ("ios", "android", "macos"):
        # Happ — отдельная строка
        if "happ" in links:
            buttons.append([InlineKeyboardButton(
                text=i18n_get_text(language, "setup.download_happ"),
                url=links["happ"],
            )])
        # Incy — отдельной строкой (iOS / Android / macOS).
        if "incy" in links:
            buttons.append([InlineKeyboardButton(
                text="📲 Скачать Incy",
                url=links["incy"],
            )])
    else:
        # Windows: download buttons in pairs
        download_row = []
        for client, url in links.items():
            label = i18n_get_text(language, f"setup.download_{client}")
            download_row.append(InlineKeyboardButton(text=label, url=url))
            if len(download_row) == 2:
                buttons.append(download_row)
                download_row = []
        if download_row:
            buttons.append(download_row)

    # === Auto-setup buttons (if user has subscription) ===
    if sub_url:
        from urllib.parse import quote, urlparse
        if config.PUBLIC_BASE_URL:
            base_url = config.PUBLIC_BASE_URL
        else:
            parsed = urlparse(config.WEBHOOK_URL)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Incy на iOS показывается только если Node-сайдкар жив. Сразу
        # пробрасываем флаг сюда, чтобы не плодить нерабочих кнопок.
        from app.services import incy_crypto
        ios_clients = ["happ"]
        if incy_crypto.is_available():
            ios_clients.append("incy")

        _platform_clients = {
            "ios": ios_clients,
            "android": ["happ"],
            "macos": ["happ"],
            "windows": ["happ"],
        }
        _client_deeplink = {
            "happ": "happ",
            "incy": "incy",
        }
        _client_names = {
            "happ": "Happ",
            "incy": "Incy",
        }

        # Decorative separator
        buttons.append([InlineKeyboardButton(
            text="Установка ключа в одно нажатие 👇",
            callback_data="noop",
        )])

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


@install_app_router.callback_query(F.data.startswith("setup_key:"))
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
