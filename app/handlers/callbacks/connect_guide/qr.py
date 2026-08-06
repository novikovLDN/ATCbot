"""QR-коды подписки: выбор типа ключа, выбор приложения, сам код.

ЧТО ЗДЕСЬ
    Три экрана подряд и генератор картинки:

        setup_qr:{платформа}                 обычные сервера или обход
        setup_qr_standard|bypass:{платформа} Happ или Incy
        setup_qr_app:{клиент}:{вид}:{платф.} сам QR

ПОЧЕМУ ВЫДЕЛЕНО
    Единственная часть инструкции, которая рисует картинку на лету и тянет
    qrcode с криптообёртками. Остальные экраны — только текст и кнопки.

ЧТО ЛЕГКО СЛОМАТЬ
    Формат callback'а последнего экрана — ровно четыре части. Проверка
    len(parts) != 4 и белые списки client/kind стоят не для красоты: данные
    приходят снаружи, и подделанный callback иначе дошёл бы до генератора.
    Обратите внимание: при неверном формате обработчик молча выходит —
    кнопка не отвечает, но и не падает.

    Кнопка «Назад» собирается как setup_qr_{вид}:{платформа}. Переименуете
    обработчики выбора приложения — «Назад» уведёт в никуда.

    Префиксы фильтров: setup_qr: и setup_qr_standard: НЕ пересекаются
    только потому, что после setup_qr идёт разный символ (двоеточие против
    подчёркивания). Заведёте setup_qr_x без подчёркивания в фильтре —
    экраны начнут перехватывать друг друга.

    Оба клиента деградируют сами: happ_crypto отдаёт сырую ссылку при
    ошибке шифрования, incy_crypto — incy://add/... при мёртвом сайдкаре.
    Экран обязан отрисоваться в любом случае.
"""
import io
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text

qr_router = Router()
logger = logging.getLogger(__name__)


@qr_router.callback_query(F.data.startswith("setup_qr:"))
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


@qr_router.callback_query(F.data.startswith("setup_qr_standard:"))
async def callback_setup_qr_standard(callback: CallbackQuery):
    """Выбор приложения (Happ / Incy) для обычных серверов."""
    try:
        await callback.answer()
    except Exception:
        pass

    platform = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)
    await _show_qr_app_choice(callback, platform, "standard", language)


@qr_router.callback_query(F.data.startswith("setup_qr_bypass:"))
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


@qr_router.callback_query(F.data.startswith("setup_qr_app:"))
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
            # Тот же инцидент, что и на экране ключей, только другим входом:
            # QR рисуется из той же ссылки. subscription непустой означает
            # активную оплаченную строку (database.get_subscription фильтрует
            # по status='active' AND expires_at > now) — значит ссылку не
            # отдала выдача, а не «человек не покупал».
            if subscription:
                logger.error(
                    "CONNECT_QR_EMPTY_FOR_ACTIVE user=%s platform=%s client=%s "
                    "expires_at=%s — оплачено, ссылки для QR нет",
                    telegram_id, platform, client, subscription.get("expires_at"),
                )
            else:
                logger.info(
                    "CONNECT_QR_EMPTY user=%s platform=%s client=%s — подписки нет",
                    telegram_id, platform, client,
                )
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
