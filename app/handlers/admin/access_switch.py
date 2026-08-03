"""Смена тарифа пользователю админом: Basic ↔ Plus.

Отдельно от выдачи доступа: здесь срок не меняется, меняется уровень.
Сценарий из трёх шагов — выбор, подтверждение, уведомление, — и
подтверждение обязательно: смена тарифа задним числом меняет то, за что
человек заплатил.
"""
import logging
import config
import database
import uuid
from aiogram import Router, F
from aiogram import Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from app.i18n import get_text as i18n_get_text
from app.services.admin import service as admin_service
from app.services.language_service import resolve_user_language
from app.handlers.admin.keyboards import (
    get_admin_user_keyboard,
)

admin_switch_router = Router()
logger = logging.getLogger(__name__)


def _admin_switch_confirm_keyboard(user_id: int, tariff: str, language: str = "ru"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_switch_confirm:{tariff}:{user_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin:show_user:{user_id}"),
        ],
    ])


def _admin_switch_notify_keyboard(user_id: int, tariff: str, language: str = "ru"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"admin_switch_notify:yes:{user_id}:{tariff}")],
        [InlineKeyboardButton(text="🔕 Нет", callback_data=f"admin_switch_notify:no:{user_id}:{tariff}")],
    ])


@admin_switch_router.callback_query(F.data.startswith("admin_switch_plus:"))
async def callback_admin_switch_plus(callback: CallbackQuery):
    """Перевести пользователя с Basic на Plus — показать подтверждение."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text("ru", "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        text = (
            f"Перевести пользователя {user_id} с Basic на Plus?\n"
            "📅 Срок подписки не изменится.\n\n"
            "✅ Подтвердить   ❌ Отмена"
        )
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=_admin_switch_confirm_keyboard(user_id, "plus", language), parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_plus: {e}")
        await callback.answer("Ошибка", show_alert=True)


@admin_switch_router.callback_query(F.data.startswith("admin_switch_basic:"))
async def callback_admin_switch_basic(callback: CallbackQuery):
    """Перевести пользователя с Plus на Basic — показать подтверждение."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text("ru", "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        user_id = int(callback.data.split(":")[1])
        text = (
            f"Перевести пользователя {user_id} с Plus на Basic?\n"
            "📅 Срок подписки не изменится.\n"
            "⚠️ Ключ будет ротирован с выделенного сервера на базовый.\n\n"
            "✅ Подтвердить   ❌ Отмена"
        )
        language = await resolve_user_language(callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=_admin_switch_confirm_keyboard(user_id, "basic", language), parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_basic: {e}")
        await callback.answer("Ошибка", show_alert=True)


@admin_switch_router.callback_query(F.data.startswith("admin_switch_confirm:"))
async def callback_admin_switch_confirm(callback: CallbackQuery, bot: Bot):
    """Выполнить смену тарифа: VPN API + БД, затем спросить про уведомление."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer(i18n_get_text("ru", "admin.access_denied"), show_alert=True)
        return
    await callback.answer()
    try:
        parts = callback.data.split(":")
        tariff = parts[1]
        user_id = int(parts[2])
        subscription = await database.get_subscription(user_id)
        if not subscription or not subscription.get("uuid"):
            await callback.answer("Нет активной подписки или UUID", show_alert=True)
            return
        uuid_val = subscription["uuid"].strip()
        language = await resolve_user_language(callback.from_user.id)
        # Tariffs are bot-side metadata only — Remnawave entity is the
        # same for basic and plus.  Drop the legacy Xray upgrade /
        # remove_plus_inbound calls (they 404 after the Remnawave
        # cut-over) and flip subscription_type directly.  Existing
        # vpn_key_plus is preserved (it's the bypass URL, tariff-agnostic).
        if tariff == "plus":
            await database.admin_switch_tariff(user_id, "plus")
            await database._log_audit_event_atomic_standalone("ADMIN_SWITCH_TO_PLUS", callback.from_user.id, user_id, "Tariff switched to Plus")
        else:
            await database.admin_switch_tariff(user_id, "basic")
            await database._log_audit_event_atomic_standalone("ADMIN_SWITCH_TO_BASIC", callback.from_user.id, user_id, "Tariff switched to Basic")
        tariff_label = "Plus" if tariff == "plus" else "Basic"
        text = f"✅ Готово. Тариф изменён на {tariff_label}\n\nУведомить пользователя?"
        await callback.message.edit_text(text, reply_markup=_admin_switch_notify_keyboard(user_id, tariff, language), parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_confirm: {e}")
        await callback.answer("Ошибка смены тарифа", show_alert=True)


@admin_switch_router.callback_query(F.data.startswith("admin_switch_notify:"))
async def callback_admin_switch_notify(callback: CallbackQuery, bot: Bot):
    """После смены тарифа: уведомить пользователя или нет, затем вернуть к карточке."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer()
        return
    await callback.answer()
    try:
        parts = callback.data.split(":")
        # admin_switch_notify:yes:{user_id}:{tariff} → parts[1]=yes/no, parts[2]=user_id, parts[3]=tariff
        notify_yes = parts[1].lower() == "yes"
        user_id = int(parts[2])
        tariff = parts[3]
        tariff_label = "Plus" if tariff == "plus" else "Basic"
        if notify_yes:
            sub = await database.get_subscription(user_id)
            msg = f"🔄 Ваш тариф изменён на {tariff_label}\n📅 Срок подписки не изменился."
            try:
                await bot.send_message(user_id, msg, parse_mode="HTML")
                if tariff == "plus" and sub and sub.get("vpn_key_plus"):
                    await bot.send_message(user_id, f"<code>{sub['vpn_key_plus']}</code>", parse_mode="HTML")
            except Exception as e:
                logger.exception(f"Error sending switch notify to user {user_id}: {e}")
        overview = await admin_service.get_admin_user_overview(user_id)
        sub_type = (overview.subscription.get("subscription_type") or "basic").strip().lower() if overview.subscription else "basic"
        if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
            sub_type = "basic"
        keyboard = get_admin_user_keyboard(
            has_active_subscription=overview.subscription_status.is_active,
            user_id=user_id,
            has_discount=overview.user_discount is not None,
            is_vip=overview.is_vip,
            subscription_type=sub_type,
            language=await resolve_user_language(callback.from_user.id),
            has_traffic_discount=overview.user_traffic_discount is not None,
        )
        text = f"✅ Тариф изменён на {tariff_label}."
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Error in callback_admin_switch_notify: {e}")
        await callback.answer("Ошибка", show_alert=True)
