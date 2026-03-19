"""
Business client key management handlers.

Бизнес-подписчики могут создавать временные VPN-ключи для клиентов:
- Создание ключа (имя клиента + время жизни 10мин–24ч)
- QR-код + ссылка отправляются владельцу
- Аналитика: имя клиента, оставшееся время, управление ключами
- Продление / досрочный отзыв ключей
- Уведомление за 30 мин до истечения
"""
import io
import logging
from datetime import datetime, timezone

import qrcode
from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.fsm.context import FSMContext

import config
import database
import vpn_utils
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import BizKeyCreate, BizKeyExtend

biz_clients_router = Router()
logger = logging.getLogger(__name__)


def _time_remaining(expires_at: datetime) -> str:
    """Форматировать оставшееся время."""
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        from datetime import timezone as tz
        expires_at = expires_at.replace(tzinfo=tz.utc)
    delta = expires_at - now
    if delta.total_seconds() <= 0:
        return "истёк"
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    if hours > 0:
        return f"{hours}ч {minutes}мин"
    return f"{minutes}мин"


def _generate_qr_bytes(data: str) -> bytes:
    """Генерация QR-кода в PNG."""
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _check_biz_access(callback: CallbackQuery) -> bool:
    """Проверить что пользователь — бизнес-подписчик."""
    if not await ensure_db_ready_callback(callback):
        return False
    telegram_id = callback.from_user.id
    sub = await database.get_subscription(telegram_id)
    if not sub:
        await callback.answer("У вас нет активной подписки", show_alert=True)
        return False
    sub_type = (sub.get("subscription_type") or "basic").strip().lower()
    if not config.is_biz_tariff(sub_type):
        await callback.answer("Эта функция доступна только для бизнес-подписки", show_alert=True)
        return False
    return True


# ── Панель управления клиентами ──────────────────────────────────────

@biz_clients_router.callback_query(F.data == "biz_clients")
async def callback_biz_clients(callback: CallbackQuery, state: FSMContext):
    """Главный экран управления клиентскими ключами."""
    if not await _check_biz_access(callback):
        return
    await callback.answer()
    await state.clear()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    analytics = await database.get_biz_analytics(telegram_id)

    text = (
        "🔑 <b>Управление клиентами</b>\n\n"
        f"📊 Активных ключей: <b>{analytics['active_now']}</b>\n"
        f"📅 Создано сегодня: <b>{analytics['created_today']}</b> / {analytics['max_per_day']}\n"
        f"📈 Всего создано: <b>{analytics['total_created']}</b>\n"
        f"🎟 Осталось на сегодня: <b>{analytics['remaining_today']}</b>"
    )

    buttons = [
        [InlineKeyboardButton(
            text="➕ Создать ключ для клиента",
            callback_data="biz_create_key",
        )],
        [InlineKeyboardButton(
            text="📋 Активные ключи",
            callback_data="biz_active_keys",
        )],
        [InlineKeyboardButton(
            text="📊 Аналитика",
            callback_data="biz_analytics",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ]

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        bot=callback.bot,
    )


# ── Создание ключа: шаг 1 — имя клиента ─────────────────────────────

@biz_clients_router.callback_query(F.data == "biz_create_key")
async def callback_create_key_start(callback: CallbackQuery, state: FSMContext):
    """Начало создания ключа — запрос имени клиента."""
    if not await _check_biz_access(callback):
        return

    telegram_id = callback.from_user.id
    analytics = await database.get_biz_analytics(telegram_id)
    if analytics["remaining_today"] <= 0:
        await callback.answer(
            f"Лимит ключей на сегодня исчерпан ({analytics['max_per_day']})",
            show_alert=True,
        )
        return

    await callback.answer()
    await state.set_state(BizKeyCreate.waiting_client_name)

    language = await resolve_user_language(telegram_id)
    text = (
        "✏️ <b>Создание ключа</b>\n\n"
        "Введите имя клиента или название ключа:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="biz_clients")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)


@biz_clients_router.message(BizKeyCreate.waiting_client_name)
async def handle_client_name(message: Message, state: FSMContext):
    """Получено имя клиента — запрашиваем время жизни ключа."""
    client_name = message.text.strip()[:100] if message.text else "Клиент"
    await state.update_data(client_name=client_name)
    await state.set_state(BizKeyCreate.waiting_duration)

    text = (
        f"👤 Клиент: <b>{client_name}</b>\n\n"
        "⏱ Введите время жизни ключа.\n\n"
        "Формат: число + <b>мин</b> или <b>ч</b>\n"
        "Например: <code>30 мин</code>, <code>2 ч</code>, <code>45 мин</code>\n\n"
        "Допустимо: от 10 минут до 24 часов"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="30 мин", callback_data="biz_dur:30"),
            InlineKeyboardButton(text="1 ч", callback_data="biz_dur:60"),
            InlineKeyboardButton(text="2 ч", callback_data="biz_dur:120"),
        ],
        [
            InlineKeyboardButton(text="4 ч", callback_data="biz_dur:240"),
            InlineKeyboardButton(text="8 ч", callback_data="biz_dur:480"),
            InlineKeyboardButton(text="24 ч", callback_data="biz_dur:1440"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="biz_clients")],
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ── Создание ключа: шаг 2 — быстрый выбор времени ───────────────────

@biz_clients_router.callback_query(F.data.startswith("biz_dur:"))
async def callback_duration_quick(callback: CallbackQuery, state: FSMContext):
    """Быстрый выбор длительности из кнопок."""
    current_state = await state.get_state()
    if current_state != BizKeyCreate.waiting_duration.state:
        await callback.answer("Начните создание ключа заново", show_alert=True)
        return

    minutes = int(callback.data.split(":")[1])
    await callback.answer()
    await _create_key_final(callback, state, minutes)


@biz_clients_router.message(BizKeyCreate.waiting_duration)
async def handle_duration_text(message: Message, state: FSMContext):
    """Пользователь ввёл произвольное время текстом."""
    text = (message.text or "").strip().lower()

    minutes = _parse_duration(text)
    if minutes is None or minutes < 10 or minutes > 1440:
        await message.answer(
            "❌ Неверный формат. Введите от 10 до 1440 минут.\n"
            "Примеры: <code>30 мин</code>, <code>2 ч</code>, <code>90 мин</code>",
            parse_mode="HTML",
        )
        return

    await _create_key_final(message, state, minutes)


def _parse_duration(text: str) -> int | None:
    """Парсинг введённого пользователем времени."""
    import re
    text = text.replace(",", ".").strip()

    # «2 ч», «2ч», «2 часа»
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:ч|час)", text)
    if m:
        return int(float(m.group(1)) * 60)

    # «30 мин», «30мин», «30 минут»
    m = re.match(r"^(\d+)\s*(?:м|мин)", text)
    if m:
        return int(m.group(1))

    # Просто число — считаем минутами
    m = re.match(r"^(\d+)$", text)
    if m:
        return int(m.group(1))

    return None


async def _create_key_final(event, state: FSMContext, duration_minutes: int):
    """Финальное создание ключа: VPN + QR + отправка."""
    data = await state.get_data()
    client_name = data.get("client_name", "Клиент")
    await state.clear()

    if isinstance(event, CallbackQuery):
        telegram_id = event.from_user.id
        bot = event.bot
        chat_id = event.message.chat.id
    else:
        telegram_id = event.from_user.id
        bot = event.bot
        chat_id = event.chat.id

    # Проверка лимита
    analytics = await database.get_biz_analytics(telegram_id)
    if analytics["remaining_today"] <= 0:
        await bot.send_message(chat_id, "❌ Лимит ключей на сегодня исчерпан.")
        return

    # Создаём VPN user через API
    try:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        sub_end = now + timedelta(minutes=duration_minutes)
        new_uuid = database._generate_subscription_uuid()
        vpn_result = await vpn_utils.add_vless_user(
            telegram_id=telegram_id,
            subscription_end=sub_end,
            uuid=new_uuid,
            tariff="basic",
        )
        vless_url = vpn_result["vless_url"]
        uuid = vpn_result["uuid"]
    except Exception as e:
        logger.exception(f"Failed to create VPN key for biz client: {e}")
        await bot.send_message(chat_id, "❌ Ошибка создания VPN ключа. Попробуйте позже.")
        return

    # Сохраняем в БД
    key_record = await database.create_biz_client_key(
        owner_telegram_id=telegram_id,
        client_name=client_name,
        vless_url=vless_url,
        uuid=uuid,
        duration_minutes=duration_minutes,
    )

    # Генерируем QR-код
    try:
        qr_bytes = _generate_qr_bytes(vless_url)
    except Exception as e:
        logger.warning(f"QR generation failed: {e}")
        qr_bytes = None

    # Формируем время
    if duration_minutes >= 60:
        h = duration_minutes // 60
        m = duration_minutes % 60
        dur_str = f"{h}ч {m}мин" if m else f"{h}ч"
    else:
        dur_str = f"{duration_minutes}мин"

    text = (
        f"✅ <b>Ключ создан!</b>\n\n"
        f"👤 Клиент: <b>{client_name}</b>\n"
        f"⏱ Время жизни: <b>{dur_str}</b>\n"
        f"⏰ Истекает: <b>{_time_remaining(key_record['expires_at'])}</b>\n\n"
        f"🔗 Ссылка для подключения:\n"
        f"<code>{vless_url}</code>\n\n"
        f"Отправьте QR-код или ссылку клиенту."
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔑 Управление ключами",
            callback_data="biz_clients",
        )],
        [InlineKeyboardButton(
            text="➕ Создать ещё",
            callback_data="biz_create_key",
        )],
    ])

    if qr_bytes:
        photo = BufferedInputFile(qr_bytes, filename=f"key_{key_record['id']}.png")
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

    logger.info(
        f"BIZ_KEY_CREATED: owner={telegram_id}, client={client_name}, "
        f"duration={duration_minutes}min, key_id={key_record['id']}"
    )


# ── Активные ключи ──────────────────────────────────────────────────

@biz_clients_router.callback_query(F.data == "biz_active_keys")
async def callback_active_keys(callback: CallbackQuery):
    """Список активных ключей."""
    if not await _check_biz_access(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    keys = await database.get_biz_active_keys(telegram_id)

    if not keys:
        text = "📋 <b>Активные ключи</b>\n\nНет активных ключей."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать ключ", callback_data="biz_create_key")],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="biz_clients")],
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
        return

    text = "📋 <b>Активные ключи</b>\n\n"
    buttons = []
    for key in keys[:20]:
        remaining = _time_remaining(key["expires_at"])
        name = key["client_name"] or f"Ключ #{key['id']}"
        text += f"• <b>{name}</b> — ⏱ {remaining}\n"
        buttons.append([InlineKeyboardButton(
            text=f"🔧 {name} ({remaining})",
            callback_data=f"biz_key:{key['id']}",
        )])

    buttons.append([InlineKeyboardButton(text="➕ Создать ключ", callback_data="biz_create_key")])
    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="biz_clients")])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        bot=callback.bot,
    )


# ── Управление конкретным ключом ─────────────────────────────────────

@biz_clients_router.callback_query(F.data.startswith("biz_key:"))
async def callback_key_detail(callback: CallbackQuery):
    """Детали и управление конкретным ключом."""
    if not await _check_biz_access(callback):
        return
    await callback.answer()

    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    key = await database.get_biz_key_by_id(key_id, telegram_id)
    if not key:
        await callback.answer("Ключ не найден", show_alert=True)
        return

    name = key["client_name"] or f"Ключ #{key['id']}"
    remaining = _time_remaining(key["expires_at"])
    created = key["created_at"].strftime("%d.%m.%Y %H:%M")
    extended = key["extended_count"]

    text = (
        f"🔑 <b>{name}</b>\n\n"
        f"📅 Создан: {created}\n"
        f"⏱ Осталось: <b>{remaining}</b>\n"
        f"🔄 Продлений: {extended}\n"
    )

    is_active = key["revoked_at"] is None and key["expires_at"] > datetime.now(timezone.utc)

    buttons = []
    if is_active:
        buttons.append([InlineKeyboardButton(
            text="⏳ Продлить",
            callback_data=f"biz_extend:{key_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="🚫 Отозвать",
            callback_data=f"biz_revoke:{key_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="📋 Показать QR",
            callback_data=f"biz_qr:{key_id}",
        )])
    else:
        text += "\n❌ Ключ неактивен"

    buttons.append([InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="biz_active_keys")])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        bot=callback.bot,
    )


# ── Отзыв ключа ─────────────────────────────────────────────────────

@biz_clients_router.callback_query(F.data.startswith("biz_revoke:"))
async def callback_revoke_key(callback: CallbackQuery):
    """Досрочный отзыв ключа."""
    if not await _check_biz_access(callback):
        return

    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id

    key = await database.get_biz_key_by_id(key_id, telegram_id)
    if not key:
        await callback.answer("Ключ не найден", show_alert=True)
        return

    # Удаляем пользователя из VPN API
    try:
        await vpn_utils.remove_vless_user(key["uuid"])
    except Exception as e:
        logger.warning(f"Failed to remove VPN user {key['uuid']} on revoke: {e}")

    success = await database.revoke_biz_key(key_id, telegram_id)
    if success:
        await callback.answer("✅ Ключ отозван", show_alert=True)
        logger.info(f"BIZ_KEY_REVOKED: owner={telegram_id}, key_id={key_id}")
    else:
        await callback.answer("Ошибка отзыва ключа", show_alert=True)

    # Возвращаемся к списку
    await callback_active_keys(callback)


# ── Продление ключа ─────────────────────────────────────────────────

@biz_clients_router.callback_query(F.data.startswith("biz_extend:"))
async def callback_extend_key(callback: CallbackQuery, state: FSMContext):
    """Выбор времени продления ключа."""
    if not await _check_biz_access(callback):
        return
    await callback.answer()

    key_id = int(callback.data.split(":")[1])
    await state.update_data(extend_key_id=key_id)

    language = await resolve_user_language(callback.from_user.id)

    text = "⏳ На сколько продлить ключ?"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="15 мин", callback_data="biz_ext_do:15"),
            InlineKeyboardButton(text="30 мин", callback_data="biz_ext_do:30"),
        ],
        [
            InlineKeyboardButton(text="1 ч", callback_data="biz_ext_do:60"),
            InlineKeyboardButton(text="2 ч", callback_data="biz_ext_do:120"),
        ],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"biz_key:{key_id}",
        )],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)


@biz_clients_router.callback_query(F.data.startswith("biz_ext_do:"))
async def callback_extend_do(callback: CallbackQuery, state: FSMContext):
    """Подтверждение продления."""
    data = await state.get_data()
    key_id = data.get("extend_key_id")
    if not key_id:
        await callback.answer("Ошибка. Попробуйте снова.", show_alert=True)
        return

    minutes = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id

    result = await database.extend_biz_key(key_id, telegram_id, minutes)
    if result:
        await callback.answer(f"✅ Ключ продлён на {minutes} мин", show_alert=True)
        logger.info(f"BIZ_KEY_EXTENDED: owner={telegram_id}, key_id={key_id}, +{minutes}min")
    else:
        await callback.answer("Ошибка продления", show_alert=True)

    await state.clear()
    # Показываем детали ключа
    callback.data = f"biz_key:{key_id}"
    await callback_key_detail(callback)


# ── QR повторно ──────────────────────────────────────────────────────

@biz_clients_router.callback_query(F.data.startswith("biz_qr:"))
async def callback_show_qr(callback: CallbackQuery):
    """Показать QR-код ключа повторно."""
    if not await _check_biz_access(callback):
        return
    await callback.answer()

    key_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id

    key = await database.get_biz_key_by_id(key_id, telegram_id)
    if not key:
        await callback.answer("Ключ не найден", show_alert=True)
        return

    vless_url = key["vless_url"]
    name = key["client_name"] or f"Ключ #{key['id']}"

    try:
        qr_bytes = _generate_qr_bytes(vless_url)
        photo = BufferedInputFile(qr_bytes, filename=f"key_{key_id}.png")
        text = (
            f"🔑 <b>{name}</b>\n\n"
            f"🔗 <code>{vless_url}</code>"
        )
        await callback.bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photo,
            caption=text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"QR show failed: {e}")
        await callback.bot.send_message(
            callback.message.chat.id,
            f"🔗 Ссылка для <b>{name}</b>:\n<code>{vless_url}</code>",
            parse_mode="HTML",
        )


# ── Аналитика ────────────────────────────────────────────────────────

@biz_clients_router.callback_query(F.data == "biz_analytics")
async def callback_analytics(callback: CallbackQuery):
    """Экран аналитики по клиентским ключам."""
    if not await _check_biz_access(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)
    analytics = await database.get_biz_analytics(telegram_id)
    keys = await database.get_biz_active_keys(telegram_id)

    text = (
        "📊 <b>Аналитика клиентских ключей</b>\n\n"
        f"📈 Всего создано: <b>{analytics['total_created']}</b>\n"
        f"✅ Активных сейчас: <b>{analytics['active_now']}</b>\n"
        f"📅 Создано сегодня: <b>{analytics['created_today']}</b> / {analytics['max_per_day']}\n"
        f"🎟 Осталось на сегодня: <b>{analytics['remaining_today']}</b>\n"
    )

    if keys:
        text += "\n<b>Активные ключи:</b>\n"
        for key in keys[:25]:
            name = key["client_name"] or f"Ключ #{key['id']}"
            remaining = _time_remaining(key["expires_at"])
            text += f"  • {name} — ⏱ {remaining}\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="biz_clients")],
    ])
    await safe_edit_text(
        callback.message, text,
        reply_markup=keyboard,
        parse_mode="HTML",
        bot=callback.bot,
    )


# ── Витрина тарифов «Для бизнеса · Клиенты» ─────────────────────────

@biz_clients_router.callback_query(F.data == "biz_client_tariffs")
async def callback_biz_client_tariffs(callback: CallbackQuery):
    """Экран выбора клиентского бизнес-тарифа."""
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)

    text = (
        "🏢 <b>Для бизнеса</b>\n\n"
        "Готовое решение для вашего бизнеса — выдавайте "
        "клиентам VPN-доступ без лишней работы и волокиты.\n\n"
        "Просто создайте временный ключ, отправьте QR-код "
        "клиенту — и он подключится за секунды. Никакой "
        "регистрации, всё управление через ваш аккаунт.\n\n"
        "Выберите тариф по количеству клиентов в день:"
    )

    tariffs = config.BIZ_CLIENT_TARIFFS
    buttons = []
    for key in ("biz_client_25", "biz_client_50", "biz_client_100",
                "biz_client_150", "biz_client_250", "biz_client_500"):
        t = tariffs[key]
        price_30 = t[30]["price"]
        buttons.append([InlineKeyboardButton(
            text=f"{t['label']} — {price_30} ₽/мес",
            callback_data=f"biz_cl_info:{key}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_buy_vpn",
    )])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        bot=callback.bot,
    )


@biz_clients_router.callback_query(F.data.startswith("biz_cl_info:"))
async def callback_biz_client_tariff_info(callback: CallbackQuery, state: FSMContext):
    """Подробности о выбранном клиентском бизнес-тарифе + выбор периода для покупки."""
    try:
        await callback.answer()
    except Exception:
        pass

    tariff_key = callback.data.split(":")[1]
    if tariff_key not in config.BIZ_CLIENT_TARIFFS:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)
    t = config.BIZ_CLIENT_TARIFFS[tariff_key]

    text = (
        f"💼 <b>{t['label']}</b>\n\n"
        f"Лимит генераций ключей: <b>{t['max_clients_per_day']}</b> в день\n\n"
        f"<b>Что входит:</b>\n"
        f"  • Создание временных ключей (от 10 мин до 24 ч)\n"
        f"  • QR-код и ссылка для каждого клиента\n"
        f"  • Аналитика и управление ключами\n"
        f"  • Уведомления об истечении\n"
        f"  • Продление и досрочный отзыв\n\n"
        f"Выберите период:"
    )

    # Кнопки выбора периода — стандартный purchase flow
    from app.handlers.common.states import PurchaseState

    await state.update_data(tariff_type=tariff_key)
    await state.set_state(PurchaseState.choose_period)

    periods = config.TARIFFS[tariff_key]
    buttons = []
    for period_days, period_data in periods.items():
        price = period_data["price"]
        months = period_days // 30
        if months == 1:
            period_text = i18n_get_text(language, "buy.period_1")
        elif months in [2, 3, 4]:
            period_text = i18n_get_text(language, "buy.period_2_4", months=months)
        else:
            period_text = i18n_get_text(language, "buy.period_5_plus", months=months)

        button_text = f"{price:,} ₽ — {period_text}".replace(",", " ")
        buttons.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"period:{tariff_key}:{period_days}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="biz_client_tariffs",
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await safe_edit_text(
        callback.message, text,
        reply_markup=keyboard,
        parse_mode="HTML",
        bot=callback.bot,
    )
