"""Инструкции подключения: как поставить приложение и добавить ключ.

ЧТО ЗДЕСЬ ЕСТЬ
    Пошаговые экраны подключения VPN — выбор устройства, установка
    приложения, добавление ключа — и автоматическая настройка по кнопке.

ЭКРАНЫ СОБИРАЮТСЯ ИЗ КАРТИНОК
    Каждый шаг показывается фотографией с подписью. Идентификаторы файлов
    лежат в словарях _SETUP_PHOTOS и _DEVICE_SELECT_PHOTO: Telegram хранит
    загруженные картинки по file_id, поэтому повторная отправка не грузит
    файл заново. Если file_id протух, экран деградирует до текста — это
    предусмотрено, ронять инструкцию из-за картинки нельзя.

ПОЧЕМУ ОТДЕЛЬНЫМ ФАЙЛОМ
    Выделено из navigation.py. Инструкции правят при смене клиентского
    приложения, а не при работе над меню, и держать их вместе не было причин.
"""
import asyncio
import io
import logging
import os

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
from app.handlers.callbacks.language import MAIN_PHOTO_FILE_ID as _MAIN_PHOTO_ID
from app.handlers.common.utils import format_text_with_incident, safe_edit_text
from app.handlers.common.screens import show_profile, _open_help_screen
from app.handlers.common.keyboards import (
    get_main_menu_keyboard,
    get_about_keyboard,
    get_service_status_keyboard,
    get_connect_keyboard,
)
router = Router()


# ── Connect instruction ──────────────────────────────────────────

_DEVICE_SELECT_PHOTO = {
    "prod": "AgACAgQAAxkBAAFU07NqGqUXEmVZ5SivuY0gwUhd7TBCeAACXw9rGxA30FCkvieRMzznwwEAAwIAA3kAAzsE",
    "stage": "AgACAgQAAxkBAAIhc2oZ_tiD1jsG8eB-9HrSgTTiyjEUAAJfD2sbEDfQUDPuD983y47VAQADAgADeQADOwQ",
}


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
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])

    # Always send photo + text for device selection
    _ds_photo = _DEVICE_SELECT_PHOTO.get("prod" if config.IS_PROD else "stage", "")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_photo(
        chat_id=telegram_id,
        photo=_ds_photo,
        caption=text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# ── Step 1: Install App ──────────────────────────────────────────

def _get_photo_id(key: str) -> str:
    """Get photo file_id based on environment."""
    env_key = "prod" if config.IS_PROD else "stage"
    return _SETUP_PHOTOS.get(key, {}).get(env_key, "")


@router.callback_query(F.data.startswith("setup_step1:"))
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


# ── Step 2: Install Keys ────────────────────────────────────────

@router.callback_query(F.data.startswith("setup_step2:"))
async def callback_setup_step2(callback: CallbackQuery):
    """Step 2: Copy & import VPN keys into app."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Get subscription keys
    subscription = await database.get_subscription(telegram_id)
    sub_url = ""
    bypass_url = ""
    if subscription:
        from app.services.user_subscription_links import get_user_primary_subscription_url
        sub_url = await get_user_primary_subscription_url(telegram_id)

    # Bypass key: available independently of main subscription
    if config.REMNAWAVE_ENABLED:
        from app.services import remnawave_api
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if rmn_uuid:
            traffic = await remnawave_api.get_user_traffic(rmn_uuid)
            if traffic:
                bypass_url = traffic.get("subscriptionUrl", "") or ""

    text = i18n_get_text(language, "setup.key_install_title")

    buttons = []

    # === Auto-setup deeplinks ===
    # Layout:
    #   [Happ VPN]       [Incy VPN]       — primary | success
    #   [Happ Обход]     [Incy Обход]     — primary | success
    #   [Готово]                           — danger
    #   [Установить вручную]               — primary
    #   [Нужна помощь]
    #   [Назад]
    # Incy-кнопки — на iOS/Android/macOS. Windows не показываем: у Incy
    # нет Windows-клиента, deeplink incy://crypt1/... там не откроется.
    # Без bypass_url вторая строка отсутствует. Без sub_url — обе строки
    # отсутствуют (вообще не должно случаться, но safeguard).
    if sub_url:
        from urllib.parse import quote, urlparse
        if config.PUBLIC_BASE_URL:
            base_url = config.PUBLIC_BASE_URL
        else:
            parsed = urlparse(config.WEBHOOK_URL)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        show_incy = platform in ("ios", "android", "macos")

        # Ряд 1: VPN-ключ (Happ + Incy)
        row_vpn = [InlineKeyboardButton(
            text="Happ VPN",
            url=f"{base_url}/open/happ?url={quote(sub_url, safe='')}",
            style="primary",
        )]
        if show_incy:
            row_vpn.append(InlineKeyboardButton(
                text="Incy VPN",
                url=f"{base_url}/open/incy?url={quote(sub_url, safe='')}",
                style="success",
            ))
        buttons.append(row_vpn)

        # Ряд 2: Обход (Happ + Incy) — только если есть bypass_url
        if bypass_url:
            row_bypass = [InlineKeyboardButton(
                text="Happ Обход",
                url=f"{base_url}/open/happ?url={quote(bypass_url, safe='')}",
                style="primary",
            )]
            if show_incy:
                row_bypass.append(InlineKeyboardButton(
                    text="Incy Обход",
                    url=f"{base_url}/open/incy?url={quote(bypass_url, safe='')}",
                    style="success",
                ))
            buttons.append(row_bypass)

    # === Bottom buttons ===
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.btn_done"),
        callback_data="setup_done",
        style="danger",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.btn_manual"),
        callback_data=f"setup_manual:{platform}",
        style="primary",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.btn_need_help"),
        url="https://t.me/atlas_suppbot",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=f"setup_step1:{platform}",
    )])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Send photo + text
    photo_id = _get_photo_id("install_keys")
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


# ── Device setup flow (legacy + auto-setup) ──────────────────────

# Photo file IDs for setup screens
_SETUP_PHOTOS = {
    "install_app_ios": {
        "prod": "AgACAgQAAxkBAAEsTydp2K_IyYzWcQLdTzcx8R69LXkQPgAC6wxrG6gtyVKbKj2nQnrQggEAAwIAA3kAAzsE",
        "stage": "AgACAgQAAxkBAAIelmnYsCB_mV2UUCsZQxtCAUv6HfJkAALrDGsbqC3JUsb1k8gTRdgCAQADAgADeQADOwQ",
    },
    "install_app_android": {
        "prod": "AgACAgQAAxkBAAEsVZ9p2WKsEhB1jDTAYdA3TXJdqENHcAACzwxrG9Np0VKr7b7MS293SQEAAwIAA3cAAzsE",
        "stage": "AgACAgQAAxkBAAIeyGnZYtm7bZWgWSbQzaPQK9jDFIjxAALPDGsb02nRUmA2_j7leNc1AQADAgADdwADOwQ",
    },
    "install_keys": {
        "prod": "AgACAgQAAxkBAAEsTzVp2LGqLrhvY1TRSdQdmp_vmS_tEwAC7AxrG6gtyVLmvPzPSqNEwAEAAwIAA3cAAzsE",
        "stage": "AgACAgQAAxkBAAIeumnZWPxaNMkJApJ3JerkNYLX_kJbAALsDGsbqC3JUlRy7JVisnaVAQADAgADdwADOwQ",
    },
}

_IOS_HAPP_LINKS = {
    # 2026-XX: старая ссылка happ-proxy-utility/id6783623643 перестала
    # быть актуальной — App Store переехал на «Happ Proxy Utility Plus»
    # id6788279553. Единая точка правды для iOS-инсталляции и всех
    # broadcast-кнопок «Happ iOS».
    "ru": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6788279553?l=en-GB",
    "global": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
}

_INCY_IOS_URL = "https://apps.apple.com/ru/app/incy/id6756943388?l=en-GB"
_INCY_ANDROID_URL = "https://play.google.com/store/apps/details?id=llc.itdev.incy&hl=en_IE"

_DOWNLOAD_LINKS = {
    # 2026-06-08: V2RayTun снят со всех платформ, Hiddify тоже снят.
    # 2026-07-07: Incy добавлен для Android и macOS — раньше был
    # только iOS. macOS использует ту же App Store ссылку что и iOS
    # (Mac с Apple Silicon умеет ставить iOS-приложения из App Store).
    "ios": {
        "happ": _IOS_HAPP_LINKS["ru"],
        "incy": _INCY_IOS_URL,
    },
    "android": {
        "happ": "https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
        "incy": _INCY_ANDROID_URL,
    },
    "macos": {
        # macOS ставит iOS-приложение Incy через App Store — та же ссылка.
        "happ": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
        "incy": _INCY_IOS_URL,
    },
    "windows": {
        "happ": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
    },
}

# Допускаем override через env — чтобы не пересобирать образ ради смены
# App Store ссылки.
_incy_ios_env = os.getenv("INCY_IOS_APP_URL")
if _incy_ios_env:
    _DOWNLOAD_LINKS["ios"]["incy"] = _incy_ios_env
    _DOWNLOAD_LINKS["macos"]["incy"] = _incy_ios_env
_incy_android_env = os.getenv("INCY_ANDROID_APP_URL")
if _incy_android_env:
    _DOWNLOAD_LINKS["android"]["incy"] = _incy_android_env


@router.callback_query(F.data == "setup_device")
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
        from app.services.user_subscription_links import get_user_primary_subscription_url
        sub_url = await get_user_primary_subscription_url(telegram_id)

    # Bypass key: available independently of main subscription.
    # Goes through the helper so cache misses + missing entities
    # auto-recover (lazy-provision creates the bypass entity if the
    # user has an active subscription but no remnawave_uuid yet).
    if config.REMNAWAVE_ENABLED:
        from app.services.user_subscription_links import get_user_bypass_url
        bypass_url = await get_user_bypass_url(telegram_id)

    connect_text = i18n_get_text(language, f"setup.connect_{platform}")

    # Build keys section.
    # — Happ-ключи (sealed crypt4) для всех платформ;
    # — Incy-ключи (crypt1) для iOS/Android/macOS. Windows не показываем:
    #   Incy-клиента под Windows нет, incy://crypt1/... deep-link
    #   там некому обрабатывать.
    # Все ключи в свёрнутой цитате (blockquote expandable) — экран
    # компактный по умолчанию, юзер раскрывает только нужный ключ.
    from app.services import happ_crypto, incy_crypto

    def _happ_key_block(label_key: str, raw_url: str) -> str:
        happ_link = happ_crypto.format_for_user(raw_url)
        return (
            "\n" + i18n_get_text(language, label_key) + "\n"
            f"<blockquote expandable><code>{happ_link}</code></blockquote>"
        )

    async def _incy_key_block(label_key: str, raw_url: str) -> str:
        incy_link = await incy_crypto.to_incy_link(raw_url)
        # Если sidecar не вернул даже fallback — пропускаем блок.
        if not incy_link:
            return ""
        return (
            "\n" + i18n_get_text(language, label_key) + "\n"
            f"<blockquote expandable><code>{incy_link}</code></blockquote>"
        )

    keys_section = ""
    if sub_url:
        keys_section += _happ_key_block("setup.key_vpn_label", sub_url)
    if bypass_url:
        keys_section += _happ_key_block("setup.key_bypass_label", bypass_url)
    if platform in ("ios", "android", "macos"):
        if sub_url:
            keys_section += await _incy_key_block("setup.key_vpn_incy_label", sub_url)
        if bypass_url:
            keys_section += await _incy_key_block("setup.key_bypass_incy_label", bypass_url)

    if keys_section:
        text = f"{connect_text}\n{keys_section}"
    else:
        text = connect_text

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_step2:{platform}",
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
    msg = await callback.bot.send_message(
        chat_id=telegram_id,
        # parse_mode=HTML обязателен — иначе Telegram отдаст
        # текст вместо premium-эмодзи. Fallback внутри тега
        # (⚡️) увидят non-premium юзеры и старые клиенты.
        text='<tg-emoji emoji-id="5456140674028019486">⚡️</tg-emoji>',
        parse_mode="HTML",
    )

    # 3. Ждём 2 секунды
    await asyncio.sleep(2)

    # 4. Удаляем 🎉
    try:
        await msg.delete()
    except Exception:
        pass

    # 5. Отправляем главное меню с фото
    language = await resolve_user_language(telegram_id)
    # Главный экран собирается в navigation — импорт локальный, чтобы не
    # замкнуть модули друг на друга: navigation тоже ссылается сюда.
    from app.handlers.callbacks.navigation import _get_main_text

    text = await _get_main_text(telegram_id, language)
    keyboard = await get_main_menu_keyboard(language, telegram_id)

    await callback.bot.send_photo(
        chat_id=telegram_id,
        photo=_MAIN_PHOTO_ID,
        caption=text,
        reply_markup=keyboard,
        parse_mode="HTML",
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
    """Выбор приложения (Happ / Incy) для обычных серверов."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)
    await _show_qr_app_choice(callback, platform, "standard", language)


@router.callback_query(F.data.startswith("setup_qr_bypass:"))
async def callback_setup_qr_bypass(callback: CallbackQuery):
    """Выбор приложения (Happ / Incy) для обхода белых списков."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)
    await _show_qr_app_choice(callback, platform, "bypass", language)


async def _show_qr_app_choice(callback: CallbackQuery, platform: str, kind: str, language: str):
    """Экран «Выберите приложение» — Incy / Happ.

    kind: 'standard' (обычные сервера) либо 'bypass' (обход).
    Кнопки ведут на единый хендлер setup_qr_app:{client}:{kind}:{platform},
    который уже забирает URL подписки и рендерит QR через _send_qr_screen."""
    text = i18n_get_text(language, "setup.qr_choose_app")

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.qr_app_btn_incy"),
            callback_data=f"setup_qr_app:incy:{kind}:{platform}",
            style="success",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.qr_app_btn_happ"),
            callback_data=f"setup_qr_app:happ:{kind}:{platform}",
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"setup_qr:{platform}",
        )],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")


@router.callback_query(F.data.startswith("setup_qr_app:"))
async def callback_setup_qr_app(callback: CallbackQuery):
    """QR-код подписки для выбранного приложения (Happ / Incy).

    Формат callback'а: setup_qr_app:{client}:{kind}:{platform}
      client  — 'happ' | 'incy'
      kind    — 'standard' (обычные сервера) | 'bypass' (обход)
      platform — ios/android/macos/windows (для back-навигации)
    """
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    _, client, kind, platform = parts
    if client not in ("happ", "incy") or kind not in ("standard", "bypass"):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    if kind == "standard":
        subscription = await database.get_subscription(telegram_id)
        if subscription:
            from app.services.user_subscription_links import get_user_primary_subscription_url
            url = await get_user_primary_subscription_url(telegram_id)
        else:
            url = None

        if not url:
            text = i18n_get_text(language, "get_key.no_subscription")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data=f"setup_qr_standard:{platform}",
            )]])
            await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot)
            return

        label_key = "setup.key_vpn_incy_label" if client == "incy" else "setup.key_vpn_label"
    else:
        url = None
        if config.REMNAWAVE_ENABLED:
            from app.services import remnawave_api
            rmn_uuid = await database.get_remnawave_uuid(telegram_id)
            if rmn_uuid:
                traffic = await remnawave_api.get_user_traffic(rmn_uuid)
                if traffic:
                    url = traffic.get("subscriptionUrl", "") or None

        if not url:
            text = i18n_get_text(language, "setup.qr_bypass_unavailable")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data=f"setup_qr_bypass:{platform}",
            )]])
            await safe_edit_text(callback.message, text, reply_markup=keyboard, bot=callback.bot, parse_mode="HTML")
            return

        label_key = "setup.key_bypass_incy_label" if client == "incy" else "setup.key_bypass_label"

    await _send_qr_screen(
        callback, platform, url, language,
        label_key=label_key, client=client, kind=kind,
    )


async def _send_qr_screen(
    callback: CallbackQuery,
    platform: str,
    url: str,
    language: str,
    label_key: str,
    client: str = "happ",
    kind: str = "standard",
):
    """Генерация QR-кода и отправка экрана с инструкцией.

    Happ → `happ://crypt4/<base64>` (pure-Python RSA-4096 sealing).
    Incy → `incy://crypt1/<payload>` (AES-256-GCM через Node sidecar;
    при недоступности sidecar'а incy_crypto само деградирует до
    `incy://add/<plain_url>` — экран всё равно живой)."""
    telegram_id = callback.from_user.id

    if client == "incy":
        # incy_crypto.to_incy_link сам кэширует, делает graceful
        # fallback и ловит исключения от sidecar'а. На совсем редкий
        # случай (например, наша обёртка кинула TypeError) — ловим
        # тут, чтобы экран всё равно отрендерился с сырой ссылкой.
        from app.services import incy_crypto
        try:
            wrapped = await incy_crypto.to_incy_link(url)
        except Exception:
            wrapped = None
        crypt_url = wrapped or url
        instruction_key = "setup.qr_instruction_incy"
    else:
        # format_for_user сам падает в raw URL при ошибке шифрования,
        # экран всегда отдаст рабочую ссылку.
        from app.services import happ_crypto
        crypt_url = happ_crypto.format_for_user(url) or url
        instruction_key = "setup.qr_instruction"

    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(crypt_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    qr_text = i18n_get_text(language, instruction_key)
    # <blockquote expandable> сворачивает длинную (~700 char) ссылку
    # до одной строки с «Show more» — тап по <code> копирует.
    qr_text += (
        "\n\n" + i18n_get_text(language, label_key) + "\n"
        f"<blockquote expandable><code>{crypt_url}</code></blockquote>"
    )

    # Back → возвращаемся на экран выбора приложения (тот же handler
    # setup_qr_standard/bypass, что теперь рендерит app-picker).
    back_cb = f"setup_qr_{kind}:{platform}"

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "setup.done_button"),
            callback_data="setup_done",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=back_cb,
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
