"""
Personal cabinet → My Devices.

Two-profile device manager backed by Remnawave HWID device-tracking:
  • Основные сервера   — remnawave_uuid          (squad bypass, GB-limited)
  • Обход белых списков — remnawave_premium_uuid (squad main, unlimited)

Per-device listing requires HWID Device Limit to be enabled in the
panel's Subscription Settings. Without it, /api/hwid/devices/{uuid}
returns an empty list and the UI gracefully shows "нет устройств".
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import database
from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.handlers.common.utils import safe_edit_text
from app.services import remnawave_api

user_router = Router()
logger = logging.getLogger(__name__)

# tier slug → (UI name, database getter name)
_TIERS: dict[str, Tuple[str, str]] = {
    "basic":   ("Основные сервера",       "get_remnawave_uuid"),
    "premium": ("Обход белых списков",    "get_remnawave_premium_uuid"),
}


async def _tier_uuid(telegram_id: int, tier: str) -> Optional[str]:
    getter = getattr(database, _TIERS[tier][1], None)
    if getter is None:
        return None
    return await getter(telegram_id)


def _fmt_relative(dt) -> str:
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt[:16]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return "только что"
    if secs < 3600:
        return f"{secs // 60} мин назад"
    if secs < 86400:
        return f"{secs // 3600} ч назад"
    if secs < 86400 * 7:
        return f"{secs // 86400} дн назад"
    return dt.strftime("%d.%m.%Y")


def _device_label(d: dict) -> str:
    model = d.get("deviceModel") or d.get("platform") or "Устройство"
    os_ver = d.get("osVersion")
    if os_ver and os_ver not in model:
        return f"{model} · {os_ver}"
    return model


# ── Main devices screen ────────────────────────────────────────────────

@user_router.callback_query(F.data == "user:devices")
async def callback_devices_main(callback: CallbackQuery):
    await callback.answer()
    text, kb = await _build_main_view(callback.from_user.id)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


async def _build_main_view(telegram_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    text = (
        "<tg-emoji emoji-id=\"6019503133288304110\">🧑‍💻</tg-emoji> <b>Мои устройства</b>\n\n"
        "Выбери профиль, чтобы увидеть подключённые устройства и при необходимости отключить лишние.\n\n"
        "<blockquote>Примечание: Если вы отключили устройство, оно будет автоматически подключено заново при следующем использовании. Перед удалением убедитесь, что вы удалили подписку из приложения.</blockquote>"
    )
    rows = []
    for tier, (name, _) in _TIERS.items():
        uuid = await _tier_uuid(telegram_id, tier)
        if not uuid:
            rows.append([InlineKeyboardButton(
                text=f"{name} · нет подписки",
                callback_data="user:devices:noop",
            )])
            continue
        devices = await remnawave_api.get_user_hwid_devices(uuid)
        count = len(devices) if devices else 0
        label = f"{name} · {count}" if count > 0 else name
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"user:devices:tier:{tier}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu_profile")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# ── Tier device list ───────────────────────────────────────────────────

@user_router.callback_query(F.data.startswith("user:devices:tier:"))
async def callback_devices_tier(callback: CallbackQuery):
    tier = callback.data.split(":")[-1]
    if tier not in _TIERS:
        await callback.answer()
        return
    await callback.answer()
    telegram_id = callback.from_user.id
    uuid = await _tier_uuid(telegram_id, tier)
    if not uuid:
        await callback.answer("Нет подписки на этот профиль", show_alert=True)
        return
    text, kb = await _build_tier_view(telegram_id, tier, uuid)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


async def _build_tier_view(telegram_id: int, tier: str, uuid: str) -> Tuple[str, InlineKeyboardMarkup]:
    name = _TIERS[tier][0]
    devices = await remnawave_api.get_user_hwid_devices(uuid)
    if devices is None:
        text = (
            f"<b>{name}</b>\n\n"
            "⚠️ Не удалось получить список устройств. Попробуй ещё раз через минуту."
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"user:devices:tier:{tier}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="user:devices")],
        ])

    if not devices:
        text = (
            f"<b>{name}</b>\n\n"
            "Подключённых устройств пока нет.\n\n"
            "Если ты только что добавил подписку в приложение — обнови экран через минуту."
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"user:devices:tier:{tier}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="user:devices")],
        ])

    text = f"<b>{name}</b> · подключено {len(devices)}\n\n"
    rows = []
    for idx, d in enumerate(devices):
        label = _device_label(d)
        last_seen = _fmt_relative(d.get("updatedAt") or d.get("createdAt"))
        ip = d.get("requestIp") or "—"
        text += f"📱 <b>{label}</b>\n    <i>{last_seen} · {ip}</i>\n\n"
        rows.append([InlineKeyboardButton(
            text=f"🗑 Удалить · {label}",
            callback_data=f"user:devices:del:{tier}:{idx}",
        )])
    if len(devices) > 1:
        rows.append([InlineKeyboardButton(
            text="🗑 Отключить все",
            callback_data=f"user:devices:dela:{tier}",
        )])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"user:devices:tier:{tier}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="user:devices")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# ── Delete one (confirm → execute) ─────────────────────────────────────

@user_router.callback_query(F.data.startswith("user:devices:del:"))
async def callback_devices_confirm_delete(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    tier, idx_s = parts[3], parts[4]
    if tier not in _TIERS:
        await callback.answer()
        return
    try:
        idx = int(idx_s)
    except ValueError:
        await callback.answer()
        return
    await callback.answer()
    telegram_id = callback.from_user.id
    uuid = await _tier_uuid(telegram_id, tier)
    if not uuid:
        return
    devices = await remnawave_api.get_user_hwid_devices(uuid)
    if not devices or idx >= len(devices):
        await callback.answer("Список изменился — обнови экран", show_alert=True)
        return
    label = _device_label(devices[idx])
    text = (
        "<b>Удалить устройство?</b>\n\n"
        f"📱 {label}\n\n"
        "После удаления устройство потеряет доступ к VPN. Чтобы вернуть — переподключи подписку в приложении на этом устройстве."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, удалить", callback_data=f"user:devices:cdel:{tier}:{idx}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"user:devices:tier:{tier}")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


@user_router.callback_query(F.data.startswith("user:devices:cdel:"))
async def callback_devices_do_delete(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    tier, idx_s = parts[3], parts[4]
    if tier not in _TIERS:
        await callback.answer()
        return
    try:
        idx = int(idx_s)
    except ValueError:
        await callback.answer()
        return
    telegram_id = callback.from_user.id
    uuid = await _tier_uuid(telegram_id, tier)
    if not uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    devices = await remnawave_api.get_user_hwid_devices(uuid)
    if not devices or idx >= len(devices):
        await callback.answer("Список изменился — обнови экран", show_alert=True)
        return
    hwid = devices[idx].get("hwid")
    if not hwid:
        await callback.answer("У устройства нет HWID", show_alert=True)
        return
    ok = await remnawave_api.delete_user_hwid_device(uuid, hwid)
    if ok:
        await callback.answer("Устройство отключено")
        logger.info("HWID_DELETE: tg=%s tier=%s hwid=%s...", telegram_id, tier, hwid[:8])
    else:
        await callback.answer("Не получилось удалить, попробуй ещё раз", show_alert=True)
    text, kb = await _build_tier_view(telegram_id, tier, uuid)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


# ── Delete all (confirm → execute) ─────────────────────────────────────

@user_router.callback_query(F.data.startswith("user:devices:dela:"))
async def callback_devices_confirm_delete_all(callback: CallbackQuery):
    tier = callback.data.split(":")[-1]
    if tier not in _TIERS:
        await callback.answer()
        return
    await callback.answer()
    name = _TIERS[tier][0]
    text = (
        f"<b>Отключить все устройства профиля «{name}»?</b>\n\n"
        "Все подключённые устройства потеряют доступ к VPN. Чтобы вернуть — переподключи подписку на нужных устройствах."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, отключить все", callback_data=f"user:devices:cdela:{tier}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"user:devices:tier:{tier}")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


@user_router.callback_query(F.data.startswith("user:devices:cdela:"))
async def callback_devices_do_delete_all(callback: CallbackQuery):
    tier = callback.data.split(":")[-1]
    if tier not in _TIERS:
        await callback.answer()
        return
    telegram_id = callback.from_user.id
    uuid = await _tier_uuid(telegram_id, tier)
    if not uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave_api.delete_all_user_hwid_devices(uuid)
    if ok:
        await callback.answer("Все устройства отключены")
        logger.info("HWID_DELETE_ALL: tg=%s tier=%s", telegram_id, tier)
    else:
        await callback.answer("Не получилось, попробуй ещё раз", show_alert=True)
    text, kb = await _build_tier_view(telegram_id, tier, uuid)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


# ── No-op (greyed-out tier row) ────────────────────────────────────────

@user_router.callback_query(F.data == "user:devices:noop")
async def callback_devices_noop(callback: CallbackQuery):
    await callback.answer("Нет активной подписки на этот профиль", show_alert=True)
