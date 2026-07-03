"""Bypass-setup screen — «Установка обхода блокировок».

Куда приходит: по кнопке «🌐 Включить обход» из уведомления
`trial.bypass_activated` (шлётся через ~5 минут после активации триала,
см. trial_notifications.py), либо ручным навигационным путём в будущем.

Что показывает:
  • Инструкцию по установке в Happ / Incy.
  • Кнопки-URL «➕ Добавить обход в Happ / Incy» — deeplink через наш
    же `/open/{client}?url=<bypass_url>` (тот же механизм, что для VPN
    Add-Device); Telegram открывает браузер → браузер уходит в
    happ:// / incy:// схему → клиент импортирует ключ.
  • «🔑 Показать ключ вручную» — раскрываемый blockquote с bypass-URL,
    юзер копирует и вставляет в клиент из буфера.

Ссылка обхода конкретного юзера берётся через `get_user_bypass_url`
(app/services/user_subscription_links.py). Если ключ ещё не готов
(fresh trial без Remnawave-entity) — показываем «⏳ Загляни позже».
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services.user_subscription_links import get_user_bypass_url
from app.handlers.common.utils import safe_edit_text

logger = logging.getLogger(__name__)

bypass_setup_router = Router()


def _redirect_url(client: str, bypass_url: str) -> str:
    """Build our own `/open/{client}?url=<bypass_url>` redirect link.

    Telegram блокирует custom URL-схемы (`happ://`, `incy://`) в inline-
    кнопках, поэтому URL-кнопка ведёт на наш HTML-редирект, который
    уже уводит браузер в клиент. См. app/api/deeplink_redirect.py.
    """
    base = getattr(config, "DEEPLINK_BASE_URL", None) or (
        f"https://{getattr(config, 'HOST', 'atlassecure.ru')}"
    )
    return f"{base.rstrip('/')}/open/{client}?url={quote(bypass_url, safe='')}"


@bypass_setup_router.callback_query(F.data == "bypass_setup_open")
async def callback_bypass_setup_open(callback: CallbackQuery):
    """Главный экран установки обхода."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    bypass_url = await get_user_bypass_url(telegram_id)
    if not bypass_url:
        # У свежего триала bypass-энтити может создаваться с задержкой.
        # Показываем понятный fallback вместо пустого экрана.
        text = i18n_get_text(language, "bypass_setup.no_key_yet")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")
        return

    text = i18n_get_text(language, "bypass_setup.title")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "bypass_setup.add_happ_btn"),
            url=_redirect_url("happ", bypass_url),
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "bypass_setup.add_incy_btn"),
            url=_redirect_url("incy", bypass_url),
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "bypass_setup.manual_btn"),
            callback_data="bypass_setup_manual",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")


@bypass_setup_router.callback_query(F.data == "bypass_setup_manual")
async def callback_bypass_setup_manual(callback: CallbackQuery):
    """Ручной показ ключа обхода — раскрываемый blockquote с URL."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    bypass_url = await get_user_bypass_url(telegram_id)
    if not bypass_url:
        text = i18n_get_text(language, "bypass_setup.no_key_yet")
    else:
        text = i18n_get_text(language, "bypass_setup.manual_screen", key=bypass_url)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="bypass_setup_open",
        )],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")
