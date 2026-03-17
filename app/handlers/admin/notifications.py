"""
Admin notification center: promo templates, retention, referral x2 cashback, subscription info.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramRetryAfter

import config
import database
from database.core import _to_db_utc
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.utils import safe_edit_text
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.utils.referral_link import build_referral_link

admin_notifications_router = Router()
logger = logging.getLogger(__name__)

BROADCAST_CONCURRENCY = 15


# === FSM States ===

class AdminPromoNotif(StatesGroup):
    choose_template = State()
    enter_discount = State()
    choose_period = State()
    choose_segment = State()
    confirm = State()


class AdminRetentionNotif(StatesGroup):
    choose_template = State()
    choose_segment = State()
    confirm = State()


class AdminReferralPromo(StatesGroup):
    choose_period = State()
    confirm = State()


# === PROMO TEMPLATES ===

PROMO_TEMPLATES = [
    {"key": "flash_sale", "title_key": "promo_tpl.flash_sale_title", "text_key": "promo_tpl.flash_sale", "max_discount": 25},
    {"key": "weekend_deal", "title_key": "promo_tpl.weekend_deal_title", "text_key": "promo_tpl.weekend_deal", "max_discount": 20},
    {"key": "loyalty_reward", "title_key": "promo_tpl.loyalty_reward_title", "text_key": "promo_tpl.loyalty_reward", "max_discount": 20},
    {"key": "new_year_offer", "title_key": "promo_tpl.new_year_offer_title", "text_key": "promo_tpl.new_year_offer", "max_discount": 30},
    {"key": "security_alert", "title_key": "promo_tpl.security_alert_title", "text_key": "promo_tpl.security_alert", "max_discount": 20},
    {"key": "friend_bonus", "title_key": "promo_tpl.friend_bonus_title", "text_key": "promo_tpl.friend_bonus", "max_discount": 15},
    {"key": "comeback_offer", "title_key": "promo_tpl.comeback_offer_title", "text_key": "promo_tpl.comeback_offer", "max_discount": 25},
    {"key": "annual_savings", "title_key": "promo_tpl.annual_savings_title", "text_key": "promo_tpl.annual_savings", "max_discount": 20},
    {"key": "speed_upgrade", "title_key": "promo_tpl.speed_upgrade_title", "text_key": "promo_tpl.speed_upgrade", "max_discount": 15},
    {"key": "privacy_day", "title_key": "promo_tpl.privacy_day_title", "text_key": "promo_tpl.privacy_day", "max_discount": 20},
]

RETENTION_TEMPLATES = [
    {"key": "inactive_3d", "title_key": "retention.inactive_3d_title", "text_key": "retention.inactive_3d"},
    {"key": "inactive_7d", "title_key": "retention.inactive_7d_title", "text_key": "retention.inactive_7d"},
    {"key": "inactive_14d", "title_key": "retention.inactive_14d_title", "text_key": "retention.inactive_14d"},
    {"key": "expired_no_renew", "title_key": "retention.expired_no_renew_title", "text_key": "retention.expired_no_renew", "has_btn": True},
    {"key": "usage_tip", "title_key": "retention.usage_tip_title", "text_key": "retention.usage_tip"},
    {"key": "milestone_30d", "title_key": "retention.milestone_30d_title", "text_key": "retention.milestone_30d"},
    {"key": "feature_update", "title_key": "retention.feature_update_title", "text_key": "retention.feature_update"},
]

PERIOD_OPTIONS = {
    "1d": ("1 день", 1),
    "3d": ("3 дня", 3),
    "7d": ("7 дней", 7),
}

SEGMENT_OPTIONS = {
    "all_users": "🌍 Все пользователи",
    "active_subscriptions": "🔐 С активной подпиской",
    "no_subscription": "🚫 Без подписки",
}


# ==================== MAIN NOTIFICATION CENTER ====================

@admin_notifications_router.callback_query(F.data == "admin:notifications")
async def callback_admin_notifications(callback: CallbackQuery, state: FSMContext):
    """Main notification center"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.notif_section_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notif_custom"), callback_data="admin:broadcast")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notif_promo"), callback_data="admin:notif_promo")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notif_retention"), callback_data="admin:notif_retention")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notif_referral"), callback_data="admin:notif_referral")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.notif_subscription"), callback_data="admin:notif_subscription")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# ==================== PROMO NOTIFICATIONS ====================

@admin_notifications_router.callback_query(F.data == "admin:notif_promo")
async def callback_admin_notif_promo(callback: CallbackQuery, state: FSMContext):
    """Show promo templates list"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.notif_promo_title")

    buttons = []
    for idx, tpl in enumerate(PROMO_TEMPLATES):
        title = i18n_get_text(language, tpl["title_key"])
        buttons.append([InlineKeyboardButton(
            text=f"{idx+1}. {title}",
            callback_data=f"admin:promo_tpl:{tpl['key']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")])

    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@admin_notifications_router.callback_query(F.data.startswith("admin:promo_tpl:"))
async def callback_admin_promo_template(callback: CallbackQuery, state: FSMContext):
    """Select a promo template"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    template_key = callback.data.split(":")[2]
    tpl = next((t for t in PROMO_TEMPLATES if t["key"] == template_key), None)
    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    await state.update_data(promo_template_key=template_key, promo_max_discount=tpl["max_discount"])
    await state.set_state(AdminPromoNotif.enter_discount)

    language = await resolve_user_language(callback.from_user.id)
    await safe_edit_text(
        callback.message,
        i18n_get_text(language, "admin.notif_promo_choose_discount") + f"\n\n(макс. {tpl['max_discount']}%)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:notif_promo")]
        ])
    )
    await callback.answer()


@admin_notifications_router.message(AdminPromoNotif.enter_discount)
async def process_promo_discount(message: Message, state: FSMContext):
    """Process discount input"""
    if message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return

    data = await state.get_data()
    max_discount = data.get("promo_max_discount", 30)

    try:
        discount = int(message.text.strip())
        if not (5 <= discount <= max_discount):
            await message.answer(f"Введите число от 5 до {max_discount}:")
            return
    except (ValueError, AttributeError):
        await message.answer(f"Введите число от 5 до {max_discount}:")
        return

    await state.update_data(promo_discount=discount)
    await state.set_state(AdminPromoNotif.choose_period)

    language = await resolve_user_language(message.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 день", callback_data="admin:promo_period:1d"),
            InlineKeyboardButton(text="3 дня", callback_data="admin:promo_period:3d"),
            InlineKeyboardButton(text="7 дней", callback_data="admin:promo_period:7d"),
        ],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:notif_promo")]
    ])
    await message.answer(i18n_get_text(language, "admin.notif_promo_choose_period"), reply_markup=keyboard)


@admin_notifications_router.callback_query(F.data.startswith("admin:promo_period:"), AdminPromoNotif.choose_period)
async def callback_promo_period(callback: CallbackQuery, state: FSMContext):
    """Select promo period"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    period_key = callback.data.split(":")[2]
    period_label, period_days = PERIOD_OPTIONS.get(period_key, ("3 дня", 3))

    await state.update_data(promo_period_key=period_key, promo_period_label=period_label, promo_period_days=period_days)
    await state.set_state(AdminPromoNotif.choose_segment)

    language = await resolve_user_language(callback.from_user.id)
    buttons = []
    for seg_key, seg_label in SEGMENT_OPTIONS.items():
        buttons.append([InlineKeyboardButton(text=seg_label, callback_data=f"admin:promo_seg:{seg_key}")])
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:notif_promo")])

    await safe_edit_text(
        callback.message,
        i18n_get_text(language, "admin.notif_promo_choose_segment"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@admin_notifications_router.callback_query(F.data.startswith("admin:promo_seg:"), AdminPromoNotif.choose_segment)
async def callback_promo_segment(callback: CallbackQuery, state: FSMContext):
    """Select segment and show preview"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    segment = callback.data.split(":")[2]
    await state.update_data(promo_segment=segment)
    await state.set_state(AdminPromoNotif.confirm)

    data = await state.get_data()
    template_key = data["promo_template_key"]
    discount = data["promo_discount"]
    period_label = data["promo_period_label"]

    tpl = next((t for t in PROMO_TEMPLATES if t["key"] == template_key), None)
    language = await resolve_user_language(callback.from_user.id)

    preview_text = i18n_get_text(language, tpl["text_key"], discount=discount, period=period_label)
    segment_label = SEGMENT_OPTIONS.get(segment, segment)

    text = i18n_get_text(
        language, "admin.notif_promo_preview",
        preview=preview_text, discount=discount, period=period_label, segment=segment_label
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="admin:promo_send")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:notif_promo")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_notifications_router.callback_query(F.data == "admin:promo_send", AdminPromoNotif.confirm)
async def callback_promo_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Send promo notification to users"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer("Отправка...")

    data = await state.get_data()
    template_key = data["promo_template_key"]
    discount = data["promo_discount"]
    period_label = data["promo_period_label"]
    period_days = data["promo_period_days"]
    segment = data["promo_segment"]

    tpl = next((t for t in PROMO_TEMPLATES if t["key"] == template_key), None)
    language = await resolve_user_language(callback.from_user.id)

    await state.clear()

    try:
        user_ids = await database.get_users_by_segment(segment)
        total = len(user_ids)

        await safe_edit_text(
            callback.message,
            f"📤 Отправка промо-уведомления...\n👥 Получателей: {total}",
            reply_markup=None
        )

        semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
        sent_count = 0
        failed_count = 0

        for user_id in user_ids:
            try:
                user_lang = await resolve_user_language(user_id)
                text = i18n_get_text(user_lang, tpl["text_key"], discount=discount, period=period_label)

                # Create discount for user
                expires_at = datetime.now(timezone.utc) + timedelta(days=period_days)
                await database.create_user_discount(
                    telegram_id=user_id,
                    discount_percent=discount,
                    expires_at=expires_at,
                    created_by=config.ADMIN_TELEGRAM_ID,
                )

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"🛒 Купить со скидкой {discount}%", callback_data="menu_buy_vpn")],
                ])

                async with semaphore:
                    try:
                        await bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="HTML")
                        sent_count += 1
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 1)
                        await bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="HTML")
                        sent_count += 1
                    except Exception:
                        failed_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed_count += 1

        result_text = i18n_get_text(language, "admin.notif_promo_sent", sent=sent_count, failed=failed_count)
        await safe_edit_text(
            callback.message, result_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")]
            ])
        )

        await database._log_audit_event_atomic_standalone(
            "admin_promo_notification_sent",
            callback.from_user.id,
            None,
            f"template={template_key}, discount={discount}%, period={period_label}, segment={segment}, sent={sent_count}, failed={failed_count}"
        )

    except Exception as e:
        logger.exception(f"Error sending promo notification: {e}")
        await safe_edit_text(
            callback.message, f"❌ Ошибка отправки: {str(e)[:100]}",
            reply_markup=get_admin_back_keyboard(language)
        )


# ==================== RETENTION NOTIFICATIONS ====================

@admin_notifications_router.callback_query(F.data == "admin:notif_retention")
async def callback_admin_notif_retention(callback: CallbackQuery, state: FSMContext):
    """Show retention templates"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await state.clear()
    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.notif_retention_title")

    buttons = []
    for idx, tpl in enumerate(RETENTION_TEMPLATES):
        title = i18n_get_text(language, tpl["title_key"])
        buttons.append([InlineKeyboardButton(
            text=f"{idx+1}. {title}",
            callback_data=f"admin:ret_tpl:{tpl['key']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")])

    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@admin_notifications_router.callback_query(F.data.startswith("admin:ret_tpl:"))
async def callback_retention_template(callback: CallbackQuery, state: FSMContext):
    """Select retention template, go to segment"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    template_key = callback.data.split(":")[2]
    tpl = next((t for t in RETENTION_TEMPLATES if t["key"] == template_key), None)
    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    await state.update_data(ret_template_key=template_key, ret_has_btn=tpl.get("has_btn", False))
    await state.set_state(AdminRetentionNotif.choose_segment)

    language = await resolve_user_language(callback.from_user.id)
    buttons = []
    for seg_key, seg_label in SEGMENT_OPTIONS.items():
        buttons.append([InlineKeyboardButton(text=seg_label, callback_data=f"admin:ret_seg:{seg_key}")])
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:notif_retention")])

    await safe_edit_text(
        callback.message,
        i18n_get_text(language, "admin.notif_retention_choose_segment"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@admin_notifications_router.callback_query(F.data.startswith("admin:ret_seg:"), AdminRetentionNotif.choose_segment)
async def callback_retention_segment(callback: CallbackQuery, state: FSMContext):
    """Confirm and show preview"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    segment = callback.data.split(":")[2]
    await state.update_data(ret_segment=segment)
    await state.set_state(AdminRetentionNotif.confirm)

    data = await state.get_data()
    template_key = data["ret_template_key"]
    tpl = next((t for t in RETENTION_TEMPLATES if t["key"] == template_key), None)

    language = await resolve_user_language(callback.from_user.id)
    preview_text = i18n_get_text(language, tpl["text_key"])
    segment_label = SEGMENT_OPTIONS.get(segment, segment)

    text = f"📋 <b>Предпросмотр</b>\n\n{preview_text}\n\n👥 Аудитория: {segment_label}\n\nОтправить?"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="admin:ret_send")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:notif_retention")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_notifications_router.callback_query(F.data == "admin:ret_send", AdminRetentionNotif.confirm)
async def callback_retention_send(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Send retention notification"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await callback.answer("Отправка...")

    data = await state.get_data()
    template_key = data["ret_template_key"]
    segment = data["ret_segment"]
    has_btn = data.get("ret_has_btn", False)

    tpl = next((t for t in RETENTION_TEMPLATES if t["key"] == template_key), None)
    language = await resolve_user_language(callback.from_user.id)
    await state.clear()

    try:
        user_ids = await database.get_users_by_segment(segment)
        total = len(user_ids)

        await safe_edit_text(
            callback.message,
            f"📤 Отправка...\n👥 Получателей: {total}",
            reply_markup=None
        )

        semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
        sent_count = 0
        failed_count = 0

        for user_id in user_ids:
            try:
                user_lang = await resolve_user_language(user_id)
                text = i18n_get_text(user_lang, tpl["text_key"])

                reply_markup = None
                if has_btn:
                    btn_text = i18n_get_text(user_lang, "retention.expired_no_renew_btn")
                    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=btn_text, callback_data="menu_buy_vpn")]
                    ])

                async with semaphore:
                    try:
                        await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="HTML")
                        sent_count += 1
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 1)
                        await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="HTML")
                        sent_count += 1
                    except Exception:
                        failed_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed_count += 1

        result_text = i18n_get_text(language, "admin.notif_retention_sent", sent=sent_count, failed=failed_count)
        await safe_edit_text(
            callback.message, result_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")]
            ])
        )

        await database._log_audit_event_atomic_standalone(
            "admin_retention_notification_sent",
            callback.from_user.id,
            None,
            f"template={template_key}, segment={segment}, sent={sent_count}, failed={failed_count}"
        )

    except Exception as e:
        logger.exception(f"Error sending retention notification: {e}")
        await safe_edit_text(
            callback.message, f"❌ Ошибка: {str(e)[:100]}",
            reply_markup=get_admin_back_keyboard(language)
        )


# ==================== REFERRAL x2 CASHBACK ====================

@admin_notifications_router.callback_query(F.data == "admin:notif_referral")
async def callback_admin_notif_referral(callback: CallbackQuery, state: FSMContext):
    """Referral promo section"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await state.clear()
    language = await resolve_user_language(callback.from_user.id)

    # Check if there's an active cashback promo
    active_promo = None
    try:
        pool = await database.get_pool()
        async with pool.acquire() as conn:
            active_promo = await conn.fetchrow(
                "SELECT * FROM cashback_promotions WHERE is_active = TRUE AND ends_at > NOW() ORDER BY id DESC LIMIT 1"
            )
    except Exception:
        pass

    text = i18n_get_text(language, "admin.notif_referral_title")

    if active_promo:
        start = active_promo["starts_at"].strftime("%d.%m.%Y")
        end = active_promo["ends_at"].strftime("%d.%m.%Y")
        text += "\n\n" + i18n_get_text(language, "admin.notif_referral_active", start=start, end=end)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "admin.notif_referral_start_x2"),
            callback_data="admin:referral_x2_start"
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@admin_notifications_router.callback_query(F.data == "admin:referral_x2_start")
async def callback_referral_x2_start(callback: CallbackQuery, state: FSMContext):
    """Choose period for x2 cashback"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminReferralPromo.choose_period)

    language = await resolve_user_language(callback.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=i18n_get_text(language, "referral.cashback_x2_admin_3d"), callback_data="admin:referral_x2_period:3"),
            InlineKeyboardButton(text=i18n_get_text(language, "referral.cashback_x2_admin_7d"), callback_data="admin:referral_x2_period:7"),
        ],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin:notif_referral")],
    ])
    await safe_edit_text(
        callback.message,
        i18n_get_text(language, "referral.cashback_x2_admin_title") + "\n\n" + i18n_get_text(language, "referral.cashback_x2_admin_period"),
        reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@admin_notifications_router.callback_query(F.data.startswith("admin:referral_x2_period:"), AdminReferralPromo.choose_period)
async def callback_referral_x2_period(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Start x2 cashback promo and notify subscribers"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    try:
        days = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные", show_alert=True)
        return
    if days not in (3, 7):
        await callback.answer("Допустимый период: 3 или 7 дней", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    starts_at = now
    ends_at = now + timedelta(days=days)

    await callback.answer("Запуск акции...")
    await state.clear()

    language = await resolve_user_language(callback.from_user.id)

    try:
        pool = await database.get_pool()

        # Guard: prevent starting duplicate campaign while one is active
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM cashback_promotions WHERE is_active = TRUE AND ends_at > NOW() LIMIT 1"
            )
            if existing:
                await safe_edit_text(
                    callback.message,
                    "⚠️ Акция x2 кешбэк уже активна. Дождитесь окончания текущей.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")]
                    ])
                )
                return

        # Create the promotion
        async with pool.acquire() as conn:
            promo_id = await conn.fetchval(
                "INSERT INTO cashback_promotions (multiplier, starts_at, ends_at, created_by) "
                "VALUES (2, $1, $2, $3) RETURNING id",
                _to_db_utc(starts_at), _to_db_utc(ends_at), callback.from_user.id
            )

        # Get all users with active subscriptions
        user_ids = await database.get_users_by_segment("active_subscriptions")

        # Activate x2 cashback for each user
        async with pool.acquire() as conn:
            for user_id in user_ids:
                await conn.execute(
                    "INSERT INTO user_cashback_multipliers (telegram_id, multiplier, promo_id, starts_at, ends_at) "
                    "VALUES ($1, 2, $2, $3, $4)",
                    user_id, promo_id, _to_db_utc(starts_at), _to_db_utc(ends_at)
                )

        # Send notifications
        start_date_str = starts_at.strftime("%d.%m")
        end_date_str = ends_at.strftime("%d.%m")
        semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
        sent_count = 0

        bot_info = await bot.get_me()
        bot_username = bot_info.username

        for user_id in user_ids:
            try:
                user_lang = await resolve_user_language(user_id)
                text = i18n_get_text(
                    user_lang, "referral.cashback_x2_notification",
                    start_date=start_date_str, end_date=end_date_str
                )

                referral_link = await build_referral_link(user_id, bot_username)
                share_url = f"https://t.me/share/url?url={quote(referral_link)}"

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=i18n_get_text(user_lang, "referral.cashback_x2_btn_invite"),
                        url=share_url
                    )],
                ])

                async with semaphore:
                    try:
                        await bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="HTML")
                        sent_count += 1
                    except Exception:
                        pass
                await asyncio.sleep(0.05)
            except Exception:
                pass

        result_text = i18n_get_text(
            language, "referral.cashback_x2_admin_started",
            start_date=starts_at.strftime("%d.%m.%Y"), end_date=ends_at.strftime("%d.%m.%Y")
        )
        result_text += f"\n\n📤 Уведомлений отправлено: {sent_count}"

        await safe_edit_text(
            callback.message, result_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")]
            ])
        )

        await database._log_audit_event_atomic_standalone(
            "admin_referral_x2_started",
            callback.from_user.id,
            None,
            f"promo_id={promo_id}, days={days}, users_notified={sent_count}"
        )

    except Exception as e:
        logger.exception(f"Error starting referral x2 promo: {e}")
        await safe_edit_text(
            callback.message, f"❌ Ошибка: {str(e)[:100]}",
            reply_markup=get_admin_back_keyboard(language)
        )


# ==================== SUBSCRIPTION NOTIFICATIONS INFO ====================

@admin_notifications_router.callback_query(F.data == "admin:notif_subscription")
async def callback_admin_notif_subscription(callback: CallbackQuery, state: FSMContext):
    """Show subscription notification configuration info"""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    await state.clear()

    language = await resolve_user_language(callback.from_user.id)
    text = i18n_get_text(language, "admin.notif_sub_title")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:notifications")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()
