"""
Bypass-gift dedicated setup flow.

Reached only from the gift-link redemption success message via the
"🌐 Подключить Обход" button. Provides a 3-step instruction tailored to
bypass GB delivery:

  1. Device select (iOS / Android / Mac / Windows)
  2. Install app (per-platform photo + download links — same content as
     the standard setup_step1, but isolated callbacks so the gift flow
     can be evolved without affecting the regular onboarding path)
  3. Connect screen with the Remnawave Happ Crypto link (test variant —
     fall back to the standard subscription URL if the panel doesn't
     expose a crypto link).

This screen is intentionally NOT registered to the global menu — it's
only opened by `bgift_setup` callback emitted from /start bgift_<CODE>.
"""
import html
import logging
from urllib.parse import quote, urlparse

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

bgift_setup_router = Router()
logger = logging.getLogger(__name__)


# ── Photo / link helpers ──────────────────────────────────────────────
# We re-use the same file_id constants as the regular setup flow. The
# imports happen inside callbacks to avoid import cycles at module load.

def _setup_photo(key: str) -> str:
    from app.handlers.callbacks.navigation import _SETUP_PHOTOS  # type: ignore
    env_key = "prod" if config.IS_PROD else "stage"
    return _SETUP_PHOTOS.get(key, {}).get(env_key, "")


def _device_select_photo() -> str:
    from app.handlers.callbacks.navigation import _DEVICE_SELECT_PHOTO  # type: ignore
    return _DEVICE_SELECT_PHOTO.get("prod" if config.IS_PROD else "stage", "")


def _ios_happ_links() -> dict:
    from app.handlers.callbacks.navigation import _IOS_HAPP_LINKS  # type: ignore
    return _IOS_HAPP_LINKS


def _download_links() -> dict:
    from app.handlers.callbacks.navigation import _DOWNLOAD_LINKS  # type: ignore
    return _DOWNLOAD_LINKS


def _resolve_public_base_url() -> str:
    """Return https://host base for /open/<client> redirect endpoint.

    Returns "" if neither PUBLIC_BASE_URL nor a parseable WEBHOOK_URL is
    available — caller MUST skip deeplink buttons in that case (an empty
    or malformed url= breaks Telegram's send_message).
    """
    pub = (config.PUBLIC_BASE_URL or "").strip()
    if pub:
        return pub.rstrip("/")
    wh = (getattr(config, "WEBHOOK_URL", "") or "").strip()
    if wh:
        try:
            parsed = urlparse(wh)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            pass
    return ""


SUPPORT_URL = "https://t.me/Atlas_SupportSecurity"


# ── Step 0: device select ─────────────────────────────────────────────

@bgift_setup_router.callback_query(F.data == "bgift_setup")
async def callback_bgift_setup(callback: CallbackQuery):
    """Entry point from the gift-link success message."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Idempotent safety net: bump Remnawave expiry to far future for users
    # whose account was created before the +10y fix (commit 6c89ae3).
    if config.REMNAWAVE_ENABLED:
        try:
            from app.services.remnawave_service import extend_remnawave_for_bypass_bg
            extend_remnawave_for_bypass_bg(telegram_id)
        except Exception as e:
            logger.warning("BGIFT_EXTEND_BYPASS_FAIL user=%s err=%s", telegram_id, e)

    text = i18n_get_text(language, "bgift_setup.select_device")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 iPhone / iPad", callback_data="bgift_step1:ios"),
            InlineKeyboardButton(text="🤖 Android", callback_data="bgift_step1:android"),
        ],
        [
            InlineKeyboardButton(text="🍎 Mac", callback_data="bgift_step1:macos"),
            InlineKeyboardButton(text="🪟 Windows", callback_data="bgift_step1:windows"),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])

    photo_id = _device_select_photo()
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


# ── Step 1: install app (per-platform) ───────────────────────────────

@bgift_setup_router.callback_query(F.data.startswith("bgift_step1:"))
async def callback_bgift_step1(callback: CallbackQuery):
    """Install-app screen (per-platform photo + download buttons)."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":", 1)[1]
    if platform not in ("ios", "android", "macos", "windows"):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    text = i18n_get_text(language, "setup.install_app")
    if platform != "windows":
        text += i18n_get_text(language, "setup.install_app_v2ray_hint")

    buttons: list = []
    ios_links = _ios_happ_links()
    dl = _download_links()

    if platform in ("ios", "macos"):
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.install_happ_ru"),
            url=ios_links["ru"],
        )])
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "setup.install_happ_global"),
            url=ios_links["global"],
        )])
    elif platform == "android":
        links = dl.get("android", {})
        if "happ" in links:
            buttons.append([InlineKeyboardButton(
                text="📲 Установить Happ",
                url=links["happ"],
            )])
        if "v2raytun" in links:
            buttons.append([InlineKeyboardButton(
                text="📲 Установить V2RayTun",
                url=links["v2raytun"],
            )])
    elif platform == "windows":
        buttons.append([InlineKeyboardButton(
            text="📲 Скачать Happ",
            url="https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
        )])
        buttons.append([InlineKeyboardButton(
            text="📲 Скачать V2RayTun",
            url="https://github.com/mdf45/v2raytun/releases/tag/v3.7.10",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "setup.next_step"),
        callback_data=f"bgift_step2:{platform}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="bgift_setup",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    photo_key = {
        "ios": "install_app_ios",
        "macos": "install_app_ios",
        "android": "install_app_android",
    }.get(platform)
    photo_id = _setup_photo(photo_key) if photo_key else ""

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


# ── Step 2: connect (bypass key + tailored instructions) ─────────────

async def _fetch_subscription_url(telegram_id: int) -> str:
    """Return the user's Remnawave subscription URL, or "" if unavailable."""
    if not config.REMNAWAVE_ENABLED:
        return ""
    try:
        from app.services import remnawave_api
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        if not rmn_uuid:
            return ""
        traffic = await remnawave_api.get_user_traffic(rmn_uuid)
        return ((traffic or {}).get("subscriptionUrl") or "").strip()
    except Exception as e:
        logger.warning("BGIFT_FETCH_SUB_URL_FAIL user=%s err=%s", telegram_id, e)
        return ""


@bgift_setup_router.callback_query(F.data.startswith("bgift_step2:"))
async def callback_bgift_step2(callback: CallbackQuery):
    """Connect screen — bypass subscription URL + import instructions."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":", 1)[1]
    if platform not in ("ios", "android", "macos", "windows"):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    sub_url = await _fetch_subscription_url(telegram_id)

    if sub_url:
        # HTML-escape — subscription URLs may contain "&" in query strings
        # which would break <code>/<blockquote> rendering otherwise.
        text = i18n_get_text(
            language, "bgift_setup.connect_screen",
            sub_url=html.escape(sub_url, quote=False),
        )
    else:
        text = i18n_get_text(language, "bgift_setup.connect_no_key")

    buttons: list = []
    base_url = _resolve_public_base_url()

    # One-tap import button via /open/<client> redirect endpoint. We only
    # show these if we have a real public base URL AND a sub_url to pass.
    if base_url and sub_url:
        try:
            buttons.append([InlineKeyboardButton(
                text="🌐 Добавить ключ в Happ",
                url=f"{base_url}/open/happ?url={quote(sub_url, safe='')}",
            )])
            buttons.append([InlineKeyboardButton(
                text="🌐 Добавить ключ в V2RayTun",
                url=f"{base_url}/open/v2raytun?url={quote(sub_url, safe='')}",
            )])
        except Exception as e:
            logger.warning("BGIFT_DEEPLINK_BUILD_FAIL user=%s err=%s", telegram_id, e)
            buttons = []  # drop partial deeplink rows on failure

    buttons.append([InlineKeyboardButton(
        text="✅ Готово",
        callback_data="setup_done",
    )])
    buttons.append([InlineKeyboardButton(
        text="💬 Поддержка",
        url=SUPPORT_URL,
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=f"bgift_step1:{platform}",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    photo_id = _setup_photo("install_keys")
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Telegram caption limit is 1024; text-message limit is 4096. Long
    # subscription URLs can push the caption over — fall back to text-only.
    CAPTION_LIMIT = 1024
    if photo_id and len(text) <= CAPTION_LIMIT:
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
