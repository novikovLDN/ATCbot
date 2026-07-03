"""Bypass-setup screen — «Установка обхода блокировок».

Заходит по кнопке «🌐 Включить обход» из уведомления
`trial.bypass_activated` (шлётся через 5 минут после активации триала).

Экран показывает:
  • Пошаговую инструкцию (форматирована, легко читаемая).
  • Кнопки-URL «➕ Добавить обход в Happ / Incy» — deeplink через наш
    /open/{client}?url=<bypass_url> редирект (`app/api/deeplink_redirect.py`),
    тот же механизм, что для VPN Add-Device в navigation.py:730-767.
  • «🔑 Показать ключ вручную» — раскрываемые blockquote'ы с обёрнутыми
    ключами (Happ → crypt4, Incy → crypt1). Формат идентичный
    существующему экрану setup_step2_manual.
"""
from __future__ import annotations

import logging
from urllib.parse import quote, urlparse

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
from app.i18n import get_text as i18n_get_text
from app.services import happ_crypto, incy_crypto
from app.services.language_service import resolve_user_language
from app.services.user_subscription_links import get_user_bypass_url
from app.handlers.common.utils import safe_edit_text

logger = logging.getLogger(__name__)

bypass_setup_router = Router()


def _resolve_base_url() -> str:
    """Public base URL для /open/-редиректа.

    Приоритет: `PUBLIC_BASE_URL` (специальный env для дашборда) → парсим
    scheme+host из `WEBHOOK_URL`. Ровно так же построен add-button в
    navigation.py:730-736 — держим единый источник правды."""
    if getattr(config, "PUBLIC_BASE_URL", None):
        return config.PUBLIC_BASE_URL.rstrip("/")
    parsed = urlparse(config.WEBHOOK_URL)
    return f"{parsed.scheme}://{parsed.netloc}"


def _redirect_url(client: str, sub_url: str) -> str:
    """`{base}/open/{happ|incy}?url=<encoded sub_url>` — как в navigation.py."""
    return f"{_resolve_base_url()}/open/{client}?url={quote(sub_url, safe='')}"


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
            style="primary",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "bypass_setup.add_incy_btn"),
            url=_redirect_url("incy", bypass_url),
            style="success",
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
    """Ручной показ ключа обхода — крипто-обёрнутый Happ (crypt4) и
    Incy (crypt1) URL в expandable blockquote-блоках. Юзер тапает по
    <code> — весь блок копируется в буфер, дальше вставляет в клиент.

    Формат совпадает с существующим экраном ручной установки
    setup_step2_manual (navigation.py:_send_qr_screen). Никакой новой
    UX-логики: тот же blockquote-паттерн, чтобы юзеры узнавали интерфейс.
    """
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    bypass_url = await get_user_bypass_url(telegram_id)
    if not bypass_url:
        text = i18n_get_text(language, "bypass_setup.no_key_yet")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="bypass_setup_open",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")
        return

    # Happ: RSA-4096 sealed crypt4 (pure-Python, всегда работает).
    happ_link = happ_crypto.format_for_user(bypass_url) or bypass_url

    # Incy: AES-256-GCM crypt1 через Node sidecar, с graceful fallback
    # на incy://add/<plain>. Если и это упало — блок скрывается.
    try:
        incy_link = await incy_crypto.to_incy_link(bypass_url)
    except Exception as e:
        logger.warning("bypass_setup_manual: incy link build failed user=%s: %s", telegram_id, e)
        incy_link = None

    # Собираем экран из блоков — Incy-блок опциональный (sidecar может
    # быть недоступен, тогда просто не показываем и не пугаем юзера
    # словом «Incy»).
    header = i18n_get_text(language, "bypass_setup.manual_screen_header")
    happ_block = i18n_get_text(
        language, "bypass_setup.manual_screen_happ_block", happ_key=happ_link,
    )
    parts = [header, happ_block]
    if incy_link:
        parts.append(
            i18n_get_text(
                language, "bypass_setup.manual_screen_incy_block", incy_key=incy_link,
            )
        )
    parts.append(i18n_get_text(language, "bypass_setup.manual_screen_footer"))
    text = "\n\n".join(parts)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="bypass_setup_open",
        )],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")
