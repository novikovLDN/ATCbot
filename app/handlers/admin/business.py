"""
Admin business management handlers.

Управление бизнес-подписчиками из админ-панели:
- Список пользователей с бизнес-тарифами
- Просмотр деталей: тариф, активные клиенты, лимиты
- Расширение лимита подключений
"""
import logging

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext

import config
import database
from app.handlers.common.utils import safe_edit_text
from app.handlers.common.states import AdminBizLimit
from app.services.language_service import resolve_user_language

admin_business_router = Router()
logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_TELEGRAM_IDS


# ── Список бизнес-подписчиков ──────────────────────────────────────

@admin_business_router.callback_query(F.data == "admin:business")
async def callback_admin_business(callback: CallbackQuery, state: FSMContext):
    """Главный экран управления бизнесом — список подписчиков."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    try:
        await callback.answer()
    except Exception:
        pass

    await state.clear()

    # Получаем все активные подписки и фильтруем бизнес-клиентские
    all_subs = await database.get_all_active_subscriptions()
    biz_subs = [
        s for s in all_subs
        if config.is_biz_client_tariff(
            (s.get("subscription_type") or "").strip().lower()
        )
    ]

    if not biz_subs:
        text = "🏢 <b>Управление бизнесом</b>\n\nНет активных бизнес-подписчиков."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="admin:main")],
        ])
        await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)
        return

    text = f"🏢 <b>Управление бизнесом</b>\n\nАктивных подписчиков: <b>{len(biz_subs)}</b>"

    buttons = []
    for sub in biz_subs:
        tid = sub["telegram_id"]
        sub_type = (sub.get("subscription_type") or "").strip().lower()
        client_info = config.BIZ_CLIENT_TARIFFS.get(sub_type, {})
        max_clients = client_info.get("max_clients_per_day", "?")
        buttons.append([InlineKeyboardButton(
            text=f"👤 {tid} · {max_clients} кл/день",
            callback_data=f"admin:biz_user:{tid}",
        )])

    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="admin:main")])

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        bot=callback.bot,
    )


# ── Детали бизнес-пользователя ─────────────────────────────────────

@admin_business_router.callback_query(F.data.startswith("admin:biz_user:"))
async def callback_admin_biz_user(callback: CallbackQuery, state: FSMContext):
    """Детали конкретного бизнес-подписчика."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    try:
        await callback.answer()
    except Exception:
        pass

    try:
        telegram_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка ID", show_alert=True)
        return

    sub = await database.get_subscription(telegram_id)
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    sub_type = (sub.get("subscription_type") or "").strip().lower()
    client_info = config.BIZ_CLIENT_TARIFFS.get(sub_type, {})
    tariff_label = client_info.get("label", sub_type)

    # Аналитика ключей
    analytics = await database.get_biz_analytics(telegram_id)

    # Кастомный лимит (может отличаться от тарифного)
    custom_limit = await database.get_biz_max_clients(telegram_id)

    # Дата окончания
    expires_at = sub.get("expires_at")
    if expires_at:
        date_str = expires_at.strftime("%d.%m.%Y %H:%M")
    else:
        date_str = "—"

    text = (
        f"🏢 <b>Бизнес-подписчик</b>\n\n"
        f"👤 ID: <code>{telegram_id}</code>\n"
        f"📋 Тариф: <b>{tariff_label}</b>\n"
        f"📅 Активна до: <b>{date_str}</b>\n\n"
        f"━━━ Клиентские ключи ━━━\n"
        f"✅ Активных сейчас: <b>{analytics['active_now']}</b>\n"
        f"📅 Создано сегодня: <b>{analytics['created_today']}</b>\n"
        f"🎟 Лимит в день: <b>{custom_limit}</b>\n"
        f"📊 Осталось сегодня: <b>{analytics['remaining_today']}</b>\n"
        f"📈 Всего создано: <b>{analytics['total_created']}</b>"
    )

    buttons = [
        [InlineKeyboardButton(
            text="📈 Расширить лимит",
            callback_data=f"admin:biz_expand:{telegram_id}",
        )],
        [InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"admin:biz_user:{telegram_id}",
        )],
        [InlineKeyboardButton(text="← Назад", callback_data="admin:business")],
    ]

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        bot=callback.bot,
    )


# ── Расширение лимита: выбор значения ──────────────────────────────

@admin_business_router.callback_query(F.data.startswith("admin:biz_expand:"))
async def callback_admin_biz_expand(callback: CallbackQuery, state: FSMContext):
    """Выбор нового лимита для бизнес-пользователя."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    try:
        await callback.answer()
    except Exception:
        pass

    try:
        telegram_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка ID", show_alert=True)
        return

    current_limit = await database.get_biz_max_clients(telegram_id)

    text = (
        f"📈 <b>Расширение лимита</b>\n\n"
        f"👤 ID: <code>{telegram_id}</code>\n"
        f"🎟 Текущий лимит: <b>{current_limit} кл/день</b>\n\n"
        f"Выберите новый лимит или введите число вручную:"
    )

    buttons = [
        [
            InlineKeyboardButton(text="25", callback_data=f"admin:biz_set:{telegram_id}:25"),
            InlineKeyboardButton(text="50", callback_data=f"admin:biz_set:{telegram_id}:50"),
            InlineKeyboardButton(text="100", callback_data=f"admin:biz_set:{telegram_id}:100"),
        ],
        [
            InlineKeyboardButton(text="150", callback_data=f"admin:biz_set:{telegram_id}:150"),
            InlineKeyboardButton(text="250", callback_data=f"admin:biz_set:{telegram_id}:250"),
            InlineKeyboardButton(text="500", callback_data=f"admin:biz_set:{telegram_id}:500"),
        ],
        [
            InlineKeyboardButton(text="750", callback_data=f"admin:biz_set:{telegram_id}:750"),
            InlineKeyboardButton(text="1000", callback_data=f"admin:biz_set:{telegram_id}:1000"),
        ],
        [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data=f"admin:biz_custom:{telegram_id}")],
        [InlineKeyboardButton(text="← Назад", callback_data=f"admin:biz_user:{telegram_id}")],
    ]

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        bot=callback.bot,
    )


# ── Установка лимита кнопкой ───────────────────────────────────────

@admin_business_router.callback_query(F.data.startswith("admin:biz_set:"))
async def callback_admin_biz_set_limit(callback: CallbackQuery):
    """Установить конкретный лимит из кнопки."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    try:
        parts = callback.data.split(":")
        telegram_id = int(parts[2])
        new_limit = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Ошибка данных", show_alert=True)
        return

    await database.set_biz_max_clients(telegram_id, new_limit)
    await callback.answer(f"✅ Лимит установлен: {new_limit} кл/день", show_alert=True)

    logger.info(f"ADMIN_BIZ_LIMIT: admin={callback.from_user.id}, user={telegram_id}, new_limit={new_limit}")

    # Возвращаемся к деталям пользователя
    callback.data = f"admin:biz_user:{telegram_id}"
    await callback_admin_biz_user(callback, FSMContext)


# ── Ручной ввод лимита ─────────────────────────────────────────────

@admin_business_router.callback_query(F.data.startswith("admin:biz_custom:"))
async def callback_admin_biz_custom(callback: CallbackQuery, state: FSMContext):
    """Запрос ручного ввода лимита."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    try:
        await callback.answer()
    except Exception:
        pass

    try:
        telegram_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка ID", show_alert=True)
        return

    await state.update_data(biz_limit_user_id=telegram_id)
    await state.set_state(AdminBizLimit.waiting_new_limit)

    text = (
        f"✏️ Введите новый лимит клиентов в день для пользователя "
        f"<code>{telegram_id}</code>:\n\n"
        f"Допустимо: от 1 до 10 000"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin:biz_user:{telegram_id}")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML", bot=callback.bot)


@admin_business_router.message(AdminBizLimit.waiting_new_limit)
async def handle_biz_custom_limit(message: Message, state: FSMContext):
    """Обработка введённого числа — нового лимита."""
    if not _is_admin(message.from_user.id):
        return

    data = await state.get_data()
    telegram_id = data.get("biz_limit_user_id")
    if not telegram_id:
        await message.answer("Ошибка. Попробуйте снова из панели.")
        await state.clear()
        return

    text_input = (message.text or "").strip()
    try:
        new_limit = int(text_input)
    except ValueError:
        await message.answer("❌ Введите целое число.")
        return

    if new_limit < 1 or new_limit > 10000:
        await message.answer("❌ Лимит должен быть от 1 до 10 000.")
        return

    await database.set_biz_max_clients(telegram_id, new_limit)
    await state.clear()

    logger.info(f"ADMIN_BIZ_LIMIT: admin={message.from_user.id}, user={telegram_id}, new_limit={new_limit}")

    await message.answer(
        f"✅ Лимит для <code>{telegram_id}</code> установлен: <b>{new_limit} кл/день</b>",
        parse_mode="HTML",
    )
