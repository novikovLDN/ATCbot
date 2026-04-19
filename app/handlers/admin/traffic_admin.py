"""
Admin traffic management: view, grant, reduce Remnawave bypass traffic.

Callbacks:
- admin:traffic:{user_id}          — view user traffic
- admin:traffic_add:{user_id}      — start grant traffic flow
- admin:traffic_reduce:{user_id}   — start reduce traffic flow
- admin:traffic_confirm:{user_id}  — confirm traffic change
- admin:traffic_cancel:{user_id}   — cancel and return to user card
"""
import logging

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from app.handlers.common.states import AdminTrafficEdit
from app.handlers.common.utils import safe_edit_text
from app.services import remnawave_api, remnawave_service
from app.services.language_service import resolve_user_language

admin_traffic_router = Router()
logger = logging.getLogger(__name__)


def _format_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} ГБ"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} МБ"
    return f"{b / 1024:.0f} КБ"


# ── View traffic ─────────────────────────────────────────────────────

@admin_traffic_router.callback_query(F.data.startswith("admin:traffic:"))
async def callback_admin_traffic_view(callback: CallbackQuery):
    """Show user's Remnawave traffic info."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Access denied", show_alert=True)
        return
    await callback.answer()

    try:
        user_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Invalid user ID", show_alert=True)
        return

    text, kb = await _build_traffic_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


async def _build_traffic_view(user_id: int):
    """Build traffic info text and keyboard for admin."""
    rmn_uuid = await database.get_remnawave_uuid(user_id)

    if not rmn_uuid:
        text = (
            f"📊 <b>Трафик пользователя</b> {user_id}\n\n"
            "❌ Remnawave аккаунт не создан.\n"
            "Пользователь получит его при следующем продлении подписки."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin:show_user:{user_id}")],
        ])
        return text, kb

    traffic = await remnawave_api.get_user_traffic(rmn_uuid)
    if not traffic:
        text = (
            f"📊 <b>Трафик пользователя</b> {user_id}\n\n"
            f"UUID: <code>{rmn_uuid}</code>\n"
            "⚠️ Не удалось получить данные из Remnawave."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:traffic:{user_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin:show_user:{user_id}")],
        ])
        return text, kb

    used = traffic.get("usedTrafficBytes", 0)
    limit = traffic.get("trafficLimitBytes", 0)
    remaining = max(0, limit - used)
    pct = int(used / limit * 100) if limit > 0 else 0
    devices = traffic.get("onlineDevices", 0)
    device_limit = traffic.get("deviceLimit", 0)
    status = traffic.get("status", "UNKNOWN")
    sub_url = traffic.get("subscriptionUrl", "")

    status_emoji = "✅" if status == "ACTIVE" else "⛔"

    text = (
        f"📊 <b>Трафик пользователя</b> {user_id}\n\n"
        f"📥 {_format_bytes(used)} / {_format_bytes(limit)}\n"
        f"📉 Осталось: {_format_bytes(remaining)} ({100 - pct}%)\n"
        f"📱 Устройств: {devices} / {device_limit}\n"
        f"🔄 Статус: {status_emoji} {status}\n\n"
        f"UUID: <code>{rmn_uuid}</code>"
    )
    if sub_url:
        text += f"\n🔗 <code>{sub_url}</code>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data=f"admin:traffic_add:{user_id}"),
            InlineKeyboardButton(text="➖ Уменьшить", callback_data=f"admin:traffic_reduce:{user_id}"),
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin:traffic:{user_id}")],
        [InlineKeyboardButton(text="◀️ К пользователю", callback_data=f"admin:show_user:{user_id}")],
    ])
    return text, kb


# ── Grant traffic ────────────────────────────────────────────────────

@admin_traffic_router.callback_query(F.data.startswith("admin:traffic_add:"))
async def callback_admin_traffic_add(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Access denied", show_alert=True)
        return
    await callback.answer()

    user_id = int(callback.data.split(":")[2])
    await state.set_state(AdminTrafficEdit.waiting_for_amount)
    await state.update_data(target_user_id=user_id, action="add")

    text = (
        f"➕ <b>Добавить трафик</b> — {user_id}\n\n"
        "Введите количество ГБ для добавления (целое число):"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin:traffic_cancel:{user_id}")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


@admin_traffic_router.callback_query(F.data.startswith("admin:traffic_reduce:"))
async def callback_admin_traffic_reduce(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Access denied", show_alert=True)
        return
    await callback.answer()

    user_id = int(callback.data.split(":")[2])
    await state.set_state(AdminTrafficEdit.waiting_for_amount)
    await state.update_data(target_user_id=user_id, action="reduce")

    text = (
        f"➖ <b>Уменьшить трафик</b> — {user_id}\n\n"
        "Введите количество ГБ для уменьшения (целое число):"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin:traffic_cancel:{user_id}")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


@admin_traffic_router.message(StateFilter(AdminTrafficEdit.waiting_for_amount))
async def process_traffic_amount(message: Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return

    data = await state.get_data()
    user_id = data["target_user_id"]
    action = data["action"]

    try:
        gb = int(message.text.strip())
        if gb <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное целое число ГБ.", parse_mode="HTML")
        return

    await state.update_data(gb_amount=gb)
    await state.set_state(AdminTrafficEdit.waiting_for_confirm)

    action_text = "добавить" if action == "add" else "уменьшить на"
    emoji = "➕" if action == "add" else "➖"

    text = (
        f"{emoji} <b>Подтверждение</b>\n\n"
        f"Пользователь: {user_id}\n"
        f"Действие: {action_text} {gb} ГБ\n\n"
        "Подтвердить?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=f"admin:traffic_confirm:{user_id}"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"admin:traffic_cancel:{user_id}"),
        ],
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@admin_traffic_router.callback_query(F.data.startswith("admin:traffic_confirm:"))
async def callback_admin_traffic_confirm(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Access denied", show_alert=True)
        return
    await callback.answer()

    data = await state.get_data()
    user_id = data.get("target_user_id")
    action = data.get("action")
    gb = data.get("gb_amount")
    await state.clear()

    if not user_id or not action or not gb:
        await callback.message.answer("❌ Данные сессии потеряны. Попробуйте заново.", parse_mode="HTML")
        return

    extra_bytes = gb * 1024**3

    rmn_uuid = await database.get_remnawave_uuid(user_id)
    if not rmn_uuid:
        await callback.message.edit_text(
            f"❌ У пользователя {user_id} нет Remnawave аккаунта.",
            parse_mode="HTML",
        )
        return

    user_data = await remnawave_api.get_user(rmn_uuid)
    if not user_data:
        await callback.message.edit_text(
            f"❌ Не удалось получить данные из Remnawave для {user_id}.",
            parse_mode="HTML",
        )
        return

    api_uuid = user_data.get("uuid") or rmn_uuid
    current_limit = user_data.get("trafficLimitBytes", 0)

    if action == "add":
        new_limit = current_limit + extra_bytes
    else:
        new_limit = max(0, current_limit - extra_bytes)

    result = await remnawave_api.update_user(api_uuid, trafficLimitBytes=new_limit)

    if result is not None:
        action_text = "добавлено" if action == "add" else "уменьшено на"
        logger.info(
            "ADMIN_TRAFFIC_%s: admin=%s user=%s gb=%d new_limit=%d",
            action.upper(), callback.from_user.id, user_id, gb, new_limit,
        )
        await database._log_audit_event_atomic_standalone(
            f"admin_traffic_{action}",
            callback.from_user.id,
            user_id,
            f"Traffic {action}: {gb} GB, new limit: {_format_bytes(new_limit)}",
        )
        text = (
            f"✅ <b>Трафик обновлён</b>\n\n"
            f"Пользователь: {user_id}\n"
            f"Действие: {action_text} {gb} ГБ\n"
            f"Новый лимит: {_format_bytes(new_limit)}"
        )
    else:
        text = f"❌ Ошибка обновления трафика для {user_id}. Проверьте логи."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Трафик", callback_data=f"admin:traffic:{user_id}")],
        [InlineKeyboardButton(text="◀️ К пользователю", callback_data=f"admin:show_user:{user_id}")],
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ── Cancel ───────────────────────────────────────────────────────────

@admin_traffic_router.callback_query(F.data.startswith("admin:traffic_cancel:"))
async def callback_admin_traffic_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Access denied", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    user_id = int(callback.data.split(":")[2])
    text, kb = await _build_traffic_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
