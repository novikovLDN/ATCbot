"""Шаг «добавьте ключ»: диплинки в один тап и ручная установка.

ЧТО ЗДЕСЬ
    callback_setup_step2    кнопки-диплинки, ставящие ключ в приложение
    callback_setup_manual   те же ключи текстом, если диплинк не сработал

ПОЧЕМУ ВЫДЕЛЕНО
    Оба экрана делают одно и то же — отдают человеку ключ подписки и ключ
    обхода, — просто разными способами. И оба ходят за ссылками в одни и те
    же сервисы. Держать их рядом с витриной приложений было незачем.

ЧТО ЛЕГКО СЛОМАТЬ
    Экран достижим БЕЗ подписки: menu_help → «Инструкции по сервису» →
    выбор устройства → «Дальше». Уберёте ветку «пока нечего подключать» —
    человек упрётся в экран с одним «Назад», без единой рабочей кнопки.

    Ключ обхода живёт отдельно от подписки: он может быть, когда подписки
    нет. Поэтому две независимые проверки, а не одна вложенная.

    Incy только для ios/android/macos: под Windows клиента нет, и
    incy://crypt1/... там некому обработать.

    Ключи отдаются через happ_crypto/incy_crypto. Обе обёртки при сбое
    шифрования деградируют до сырой ссылки — экран обязан остаться живым,
    человек уже заплатил.
"""
import asyncio

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.keyboards import get_main_menu_keyboard
from app.handlers.callbacks.language import MAIN_PHOTO_FILE_ID as _MAIN_PHOTO_ID
from app.handlers.callbacks.connect_guide.catalog import _get_photo_id

keys_router = Router()


@keys_router.callback_query(F.data.startswith("setup_step2:"))
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

    # Экран достижим без подписки: menu_help → «Инструкции по сервису» →
    # выбор устройства → «Дальше». Без ключей все кнопки-диплинки ниже
    # отваливаются, и человек упирался в экран с одним «Назад» — тупик
    # вместо инструкции. Показываем честное состояние и путь дальше.
    if not sub_url and not bypass_url:
        await safe_edit_text(
            callback.message,
            i18n_get_text(
                language, "setup.no_subscription",
                "🔑 <b>Пока нечего подключать</b>\n\n"
                "Ключ появится сразу после оформления подписки — тогда этот "
                "экран установит приложение и настроит его в два нажатия.",
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "main.buy", "Купить подписку"),
                    callback_data="menu_buy_vpn",
                )],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "common.back"),
                    callback_data="connect_instruction",
                )],
            ]),
            parse_mode="HTML",
            bot=callback.bot,
        )
        return

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


@keys_router.callback_query(F.data.startswith("setup_manual:"))
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


# ── Завершение установки ─────────────────────────────────────────────
#
# Последний шаг пути: человек добавил ключ и нажал «Готово». Экран
# инструкции удаляется, показывается короткая анимация и главное меню.
#
# Живёт рядом с экранами ключей потому, что это конец того же пути:
# setup_step2 (ключи) → setup_done. Отдельный модуль на один обработчик
# только заставил бы искать его в третьем месте.

@keys_router.callback_query(F.data == "setup_done")
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
