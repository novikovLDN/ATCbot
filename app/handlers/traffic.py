"""
Traffic display and traffic pack purchase handlers.

Callbacks:
- traffic_info       — show traffic usage (progress bar, devices, etc.)
- traffic_refresh     — refresh traffic info
- buy_traffic        — show available traffic packs
- buy_traffic_pack:N — confirm purchase of N GB pack
- traffic_pay_balance:N — pay for N GB pack from balance
- traffic_pay_card:N — pay for N GB pack via YooKassa (card)
- traffic_pay_sbp:N  — pay for N GB pack via SBP (Platega)
"""
import asyncio
import logging
import math

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.services import remnawave_api, remnawave_service
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.utils import safe_edit_text

traffic_router = Router()
logger = logging.getLogger(__name__)

LAVA_INVOICE_TIMEOUT = 15 * 60  # 15 minutes


async def _auto_delete_lava_msg(bot, chat_id: int, msg):
    """Delete Lava invoice message after timeout."""
    try:
        await asyncio.sleep(LAVA_INVOICE_TIMEOUT)
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass


@traffic_router.callback_query(F.data == "buy_bypass_only")
async def callback_buy_bypass_only(callback: CallbackQuery):
    """Экран покупки только обхода белых списков (ГБ пакеты)."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    # Build pack buttons (2 per row)
    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_bypass_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text="📦 Больше объёма →",
        callback_data="buy_bypass_extended",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main",
    )])

    text = i18n_get_text(language, "bypass.buy_title")
    # Add trial bonus text if trial is available
    from app.services.trials import service as trial_service
    trial_available = await trial_service.is_trial_available(telegram_id)
    if trial_available:
        text += i18n_get_text(language, "bypass.buy_title_trial")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    # Main screen may be a photo — delete and send new message
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")


@traffic_router.callback_query(F.data == "buy_bypass_extended")
async def callback_buy_bypass_extended(callback: CallbackQuery):
    """Расширенные пакеты обхода (300+ ГБ)."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS_EXTENDED.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_bypass_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_bypass_only",
    )])

    text = i18n_get_text(language, "traffic.buy_title_extended")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@traffic_router.callback_query(F.data.startswith("buy_bypass_pack:"))
async def callback_buy_bypass_pack(callback: CallbackQuery):
    """Подтверждение покупки bypass-only пакета."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    balance = await database.get_user_balance(telegram_id)
    base_price = pack["price"]
    if discount_pct > 0:
        final_price = math.ceil(base_price * (1 - discount_pct / 100))
    else:
        final_price = base_price

    text = i18n_get_text(language, "traffic.confirm_purchase", gb=gb, price=final_price, balance=balance)

    buttons = []
    if balance >= final_price:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_balance", price=final_price),
            callback_data=f"bypass_pay_balance:{gb}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.card"),
        callback_data=f"bypass_pay_card:{gb}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.sbp"),
        callback_data=f"bypass_pay_sbp:{gb}",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "payment.stars"),
        callback_data=f"bypass_pay_stars:{gb}",
    )])

    import cryptobot_service
    if cryptobot_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.crypto"),
            callback_data=f"bypass_pay_crypto:{gb}",
        )])

    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "payment.lava"),
            callback_data=f"bypass_pay_lava:{gb}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_bypass_only",
    )])

    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@traffic_router.callback_query(F.data.startswith("bypass_pay_balance:"))
async def callback_bypass_pay_balance(callback: CallbackQuery):
    """Оплата bypass-only пакета с баланса. Выдаёт только обход + триал основных серверов."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    base_price = pack["price"]
    final_price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price

    balance = await database.get_user_balance(telegram_id)
    if balance < final_price:
        text = i18n_get_text(language, "traffic.insufficient_balance")
        buttons = [[InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="buy_bypass_only",
        )]]
        await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")
        return

    # Deduct balance
    await database.decrease_balance(telegram_id, final_price, source="bypass_traffic", description=f"Bypass traffic {gb} GB")

    # Ensure subscription row exists for bypass-only user
    await database.ensure_bypass_only_subscription(telegram_id)

    # Ensure Remnawave user exists (creates if needed)
    traffic_bytes = gb * 1024**3
    rmn_success = False
    rmn_uuid = await database.get_remnawave_uuid(telegram_id)
    if rmn_uuid:
        rmn_success = await remnawave_service.add_traffic(telegram_id, traffic_bytes)
    if not rmn_success:
        # No UUID or stale UUID (404) — clear and create fresh Remnawave user
        if rmn_uuid:
            await database.clear_remnawave_uuid(telegram_id)
        from datetime import datetime, timezone, timedelta
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        try:
            await remnawave_service.create_remnawave_user(
                telegram_id, "basic", far_future, traffic_limit_override=traffic_bytes
            )
            rmn_success = True
            logger.info(f"BYPASS_REMNAWAVE_USER_CREATED user={telegram_id} gb={gb}")
        except Exception as e:
            logger.error(f"BYPASS_REMNAWAVE_CREATE_FAIL user={telegram_id} gb={gb}: {e}")
    if not rmn_success:
        logger.warning(f"TRAFFIC_PURCHASE_REMNAWAVE_FAIL user={telegram_id} gb={gb}")

    # Record traffic purchase
    await database.record_traffic_purchase(telegram_id, gb, final_price)

    # Activate 3-day trial of basic servers if eligible
    trial_activated = False
    from app.services.trials import service as trial_service
    trial_available = await trial_service.is_trial_available(telegram_id)
    if trial_available:
        try:
            await trial_service.activate_trial(telegram_id)
            trial_activated = True
            logger.info(f"Auto-activated trial for bypass-only buyer {telegram_id}")
        except Exception as e:
            logger.warning(f"Failed to activate trial for bypass buyer {telegram_id}: {e}")

    text = "✅ <b>Обход блокировок активирован!</b>\n\n"
    text += f"📦 +{gb} ГБ трафика начислено\n"
    if trial_activated:
        text += "\n🎁 <b>Бонус:</b> Пробный период VPN (3 дня) активирован!\n"
    text += "\n💡 <i>Трафик не сгорает — накапливается между покупками.</i>\n"
    text += "\nОткройте <b>Личный кабинет</b> чтобы получить ключ подключения."

    buttons = [
        [InlineKeyboardButton(text="👤 Личный кабинет", callback_data="menu_profile")],
        [InlineKeyboardButton(text="🌐 Купить ещё ГБ", callback_data="buy_traffic")],
        [InlineKeyboardButton(text="← На главную", callback_data="menu_main")],
    ]

    # Главный экран без подписки — это фото, его нельзя edit в текст. Удаляем и шлём новое.
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


def _strikethrough(text: str) -> str:
    """Apply Unicode strikethrough to text (works in Telegram button labels)."""
    return "".join(ch + "\u0336" for ch in str(text))


def _format_bytes(b: int) -> str:
    """Format bytes to human-readable GB/MB string."""
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} ГБ"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} МБ"
    return f"{b / 1024:.0f} КБ"


def _progress_bar(used: int, limit: int, length: int = 10) -> str:
    if limit <= 0:
        return "🤍" * length
    ratio = min(used / limit, 1.0)
    filled = int(ratio * length)
    return "🤍" * filled + "🩶" * (length - filled)


@traffic_router.callback_query(F.data.in_({"traffic_info", "traffic_refresh"}))
async def callback_traffic_info(callback: CallbackQuery):
    """Show traffic usage screen."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Check active subscription
    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "traffic.no_subscription")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_subscription"),
                callback_data="menu_buy_vpn",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
    is_trial = sub_type == "trial"

    rmn_uuid = await database.get_remnawave_uuid(telegram_id)
    if not rmn_uuid:
        # Auto-provision in background, show "provisioning" screen
        expires_at = subscription.get("expires_at")
        if expires_at and config.REMNAWAVE_ENABLED:
            override = 5 * 1024**3 if is_trial else 10 * 1024**3
            remnawave_service._fire_and_forget(
                remnawave_service.create_remnawave_user(
                    telegram_id, sub_type, expires_at,
                    traffic_limit_override=override,
                )
            )
            text = "⏳ Настраиваем обход блокировок...\nНажмите 🔄 через несколько секунд."
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="traffic_refresh")],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "common.back"),
                    callback_data="menu_main",
                )],
            ])
            await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
            return
    else:
        # Ensure squad is assigned for existing users (fire-and-forget)
        remnawave_service._fire_and_forget(
            remnawave_service.ensure_squad(telegram_id)
        )
    if not rmn_uuid:
        text = i18n_get_text(language, "traffic.not_provisioned")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    # Fetch traffic from Remnawave
    traffic = await remnawave_api.get_user_traffic(rmn_uuid)
    if not traffic:
        text = i18n_get_text(language, "traffic.fetch_error")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    used = traffic["usedTrafficBytes"]
    limit = traffic["trafficLimitBytes"]

    remaining = max(0, limit - used)
    pct = int(used / limit * 100) if limit > 0 else 0

    expires_at = subscription.get("expires_at")
    expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "—"

    bar = _progress_bar(used, limit)
    warning = ""
    if remaining <= 500 * 1024**2:
        warning += "\n\n❗️ " + i18n_get_text(language, "traffic.warning_critical")
    elif remaining <= 3 * 1024**3:
        warning += "\n\n⚠️ " + i18n_get_text(language, "traffic.warning_low", remaining=_format_bytes(remaining))

    # Subscription URL comes directly from Remnawave API response
    sub_url = traffic.get("subscriptionUrl", "")
    happ_url = traffic.get("happ_url", "")

    text = i18n_get_text(
        language,
        "traffic.info",
        used=_format_bytes(used),
        limit=_format_bytes(limit),
        bar=bar,
        pct=pct,
        expires=expires_str,
        sub_url=sub_url,
        happ_url=happ_url,
    ) + warning

    if is_trial:
        text += "\n\n💎 " + i18n_get_text(language, "traffic.trial_upgrade_hint")

    buttons = []
    if is_trial:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_subscription"),
            callback_data="menu_buy_vpn",
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_traffic_btn"),
            callback_data="buy_traffic",
        )])
    buttons.append([InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")


async def show_traffic_info_message(message):
    """Show traffic info as a new message (for /white command)."""
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "traffic.no_subscription")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_subscription"),
                callback_data="menu_buy_vpn",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
    is_trial = sub_type == "trial"

    rmn_uuid = await database.get_remnawave_uuid(telegram_id)
    if not rmn_uuid:
        expires_at = subscription.get("expires_at")
        if expires_at and config.REMNAWAVE_ENABLED:
            override = 5 * 1024**3 if is_trial else 10 * 1024**3
            remnawave_service._fire_and_forget(
                remnawave_service.create_remnawave_user(
                    telegram_id, sub_type, expires_at,
                    traffic_limit_override=override,
                )
            )
            text = "⏳ Настраиваем обход блокировок...\nНажмите 🔄 через несколько секунд."
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="traffic_refresh")],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "common.back"),
                    callback_data="menu_main",
                )],
            ])
            await message.answer(text, reply_markup=kb)
            return
        text = i18n_get_text(language, "traffic.not_provisioned")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return
    else:
        remnawave_service._fire_and_forget(
            remnawave_service.ensure_squad(telegram_id)
        )

    traffic = await remnawave_api.get_user_traffic(rmn_uuid)
    if not traffic:
        text = i18n_get_text(language, "traffic.fetch_error")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
        return

    used = traffic["usedTrafficBytes"]
    limit = traffic["trafficLimitBytes"]
    remaining = max(0, limit - used)
    pct = int(used / limit * 100) if limit > 0 else 0
    expires_at = subscription.get("expires_at")
    expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "—"
    bar = _progress_bar(used, limit)
    warning = ""
    if remaining <= 500 * 1024**2:
        warning += "\n\n❗️ " + i18n_get_text(language, "traffic.warning_critical")
    elif remaining <= 3 * 1024**3:
        warning += "\n\n⚠️ " + i18n_get_text(language, "traffic.warning_low", remaining=_format_bytes(remaining))

    sub_url = traffic.get("subscriptionUrl", "")
    happ_url = traffic.get("happ_url", "")
    text = i18n_get_text(
        language, "traffic.info",
        used=_format_bytes(used), limit=_format_bytes(limit),
        bar=bar, pct=pct, expires=expires_str, sub_url=sub_url,
        happ_url=happ_url,
    ) + warning

    if is_trial:
        text += "\n\n💎 " + i18n_get_text(language, "traffic.trial_upgrade_hint")

    buttons = []
    if is_trial:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_subscription"),
            callback_data="menu_buy_vpn",
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_traffic_btn"),
            callback_data="buy_traffic",
        )])
    buttons.append([InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="menu_main",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Buy traffic ────────────────────────────────────────────────────────

@traffic_router.callback_query(F.data == "buy_traffic")
async def callback_buy_traffic(callback: CallbackQuery):
    """Show traffic pack options."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    subscription = await database.get_subscription(telegram_id)
    if not subscription:
        text = i18n_get_text(language, "traffic.no_subscription")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_subscription"),
                callback_data="menu_buy_vpn",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ])
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)
        return

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    # Build pack buttons (2 per row)
    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_traffic_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text="📦 Больше объёма →",
        callback_data="buy_traffic_extended",
    )])
    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="traffic_info",
    )])

    text = i18n_get_text(language, "traffic.buy_title")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@traffic_router.callback_query(F.data == "buy_traffic_extended")
async def callback_buy_traffic_extended(callback: CallbackQuery):
    """Show extended traffic packs (300+GB)."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    buttons = []
    row = []
    for gb, pack in config.TRAFFIC_PACKS_EXTENDED.items():
        base_price = pack["price"]
        if discount_pct > 0:
            final_price = math.ceil(base_price * (1 - discount_pct / 100))
            label = f"{gb} ГБ — {final_price} ₽"
        else:
            label = f"{gb} ГБ — {base_price} ₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"buy_traffic_pack:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_traffic",
    )])

    text = i18n_get_text(language, "traffic.buy_title_extended")
    if discount_pct > 0:
        text += f"\n\n🎁 Промо-скидка {discount_pct}% активна!"
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot, parse_mode="HTML")


@traffic_router.callback_query(F.data.startswith("buy_traffic_pack:"))
async def callback_buy_traffic_pack(callback: CallbackQuery):
    """Confirm traffic pack purchase."""
    if not await ensure_db_ready_callback(callback):
        return
    await callback.answer()

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    # Check for active traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0

    balance = await database.get_user_balance(telegram_id)
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price

    # SBP price with markup
    sbp_price_kopecks = math.ceil(price * 100 * (1 + config.SBP_MARKUP_PERCENT / 100.0))
    sbp_price = sbp_price_kopecks / 100.0

    text = i18n_get_text(
        language,
        "traffic.confirm_purchase",
        gb=gb,
        price=price,
        balance=f"{balance:.0f}",
    )
    if discount_pct > 0:
        text += f"\n🎁 Скидка {discount_pct}%: {_strikethrough(str(base_price))} ₽ → {price} ₽"

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_balance", price=price),
            callback_data=f"traffic_pay_balance:{gb}",
        )],
    ]

    # Card (YooKassa) button — requires TG_PROVIDER_TOKEN
    if config.TG_PROVIDER_TOKEN:
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_card", price=price),
            callback_data=f"traffic_pay_card:{gb}",
        )])

    # SBP (Platega) button
    import platega_service
    if platega_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_sbp", price=f"{sbp_price:.0f}"),
            callback_data=f"traffic_pay_sbp:{gb}",
        )])

    # Lava (card) button
    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_lava", price=price),
            callback_data=f"traffic_pay_lava:{gb}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="buy_traffic",
    )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


@traffic_router.callback_query(F.data.startswith("traffic_pay_balance:"))
async def callback_traffic_pay_balance(callback: CallbackQuery):
    """Pay for traffic pack from balance."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    # Apply traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price

    balance = await database.get_user_balance(telegram_id)

    if balance < price:
        await callback.answer(
            i18n_get_text(language, "traffic.insufficient_balance"),
            show_alert=True,
        )
        return

    await callback.answer()

    # Deduct balance
    try:
        await database.decrease_balance(telegram_id, price)
        await database.log_balance_transaction(
            telegram_id=telegram_id,
            amount=-price,
            transaction_type="traffic_purchase",
            description=f"Покупка {gb} ГБ трафика обхода",
        )
    except Exception as e:
        logger.error("TRAFFIC_PURCHASE_BALANCE_ERROR: tg=%s %s", telegram_id, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"))
        return

    # Record purchase
    await database.record_traffic_purchase(telegram_id, gb, price, "balance")

    # Add traffic in Remnawave
    success = await remnawave_service.add_traffic(telegram_id, pack["bytes"])

    if success:
        # Fetch updated traffic info
        rmn_uuid = await database.get_remnawave_uuid(telegram_id)
        new_info = ""
        if rmn_uuid:
            traffic = await remnawave_api.get_user_traffic(rmn_uuid)
            if traffic:
                used = traffic["usedTrafficBytes"]
                new_limit = traffic["trafficLimitBytes"]
                new_remaining = max(0, new_limit - used)
                bar = _progress_bar(used, new_limit)
                pct = int(used / new_limit * 100) if new_limit > 0 else 0
                new_info = f"\n\n📊 {_format_bytes(used)} / {_format_bytes(new_limit)}\n{bar} {pct}%"

        text = i18n_get_text(
            language,
            "traffic.purchase_success",
            gb=gb,
            price=price,
        ) + new_info
    else:
        # Refund on failure
        await database.increase_balance(telegram_id, price)
        await database.log_balance_transaction(
            telegram_id=telegram_id,
            amount=price,
            transaction_type="traffic_refund",
            description=f"Возврат за {gb} ГБ (ошибка Remnawave)",
        )
        text = i18n_get_text(language, "traffic.purchase_failed")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.back_to_traffic"),
            callback_data="traffic_info",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot)


# ── Card payment (YooKassa via Telegram Payments) ────────────────────

@traffic_router.callback_query(F.data.startswith("traffic_pay_card:"))
async def callback_traffic_pay_card(callback: CallbackQuery):
    """Pay for traffic pack via card (Telegram Payments / YooKassa)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    # Apply traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    price_kopecks = price * 100

    # Minimum Telegram payment: 64 RUB = 6400 kopecks
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        await callback.answer(i18n_get_text(language, "errors.payment_min_amount"), show_alert=True)
        return

    try:
        # Create pending_purchase with purchase_type='traffic_pack'
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"traffic_{gb}gb",
            period_days=0,
            price_kopecks=price_kopecks,
            purchase_type="traffic_pack",
        )

        payload = f"purchase:{purchase_id}"
        description = f"Atlas Secure — {gb} GB traffic"
        prices = [LabeledPrice(label=f"{gb} GB", amount=price_kopecks)]

        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Atlas Secure — {gb} GB",
            description=description,
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
        )

        logger.info(
            "TRAFFIC_CARD_INVOICE_SENT user=%s purchase_id=%s gb=%s price=%s",
            telegram_id, purchase_id, gb, price,
        )
        await callback.answer()

    except Exception as e:
        logger.exception("TRAFFIC_CARD_INVOICE_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


# ── SBP payment (Platega) ────────────────────────────────────────────

@traffic_router.callback_query(F.data.startswith("traffic_pay_sbp:"))
async def callback_traffic_pay_sbp(callback: CallbackQuery):
    """Pay for traffic pack via SBP (Platega, +11% markup)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    # Apply traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    price_kopecks = price * 100

    try:
        # Apply SBP markup (+11%)
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)

        # Create pending_purchase with purchase_type='traffic_pack'
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"traffic_{gb}gb",
            period_days=0,
            price_kopecks=sbp_price_kopecks,
            purchase_type="traffic_pack",
        )

        sbp_price_rubles = sbp_price_kopecks / 100.0

        # Create Platega transaction
        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Atlas Secure — {gb} GB traffic",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        # Save invoice_id
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception as e:
            logger.error("Failed to save SBP transaction_id: purchase_id=%s error=%s", purchase_id, e)

        logger.info(
            "TRAFFIC_SBP_INVOICE_SENT user=%s purchase_id=%s gb=%s sbp_price=%.2f tx=%s",
            telegram_id, purchase_id, gb, sbp_price_rubles, transaction_id,
        )

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.sbp_pay_button"),
                url=redirect_url,
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="buy_traffic",
            )],
        ])

        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("TRAFFIC_SBP_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@traffic_router.callback_query(F.data.startswith("traffic_pay_lava:"))
async def callback_traffic_pay_lava(callback: CallbackQuery):
    """Pay for traffic pack via Lava (card)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    pack = config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)
    if not pack:
        return

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.lava_unavailable"), show_alert=True)
        return

    # Apply traffic promo discount
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    price_kopecks = price * 100

    try:
        # Create pending_purchase with purchase_type='traffic_pack'
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"traffic_{gb}gb",
            period_days=0,
            price_kopecks=price_kopecks,
            purchase_type="traffic_pack",
        )

        price_rubles = price_kopecks / 100.0

        # Create Lava invoice
        invoice_data = await lava_service.create_invoice(
            amount_rubles=price_rubles,
            purchase_id=purchase_id,
            comment=f"Atlas Secure — {gb} GB traffic",
        )

        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["payment_url"]

        # Save invoice_id
        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error("Failed to save Lava invoice_id: purchase_id=%s error=%s", purchase_id, e)

        logger.info(
            "TRAFFIC_LAVA_INVOICE_SENT user=%s purchase_id=%s gb=%s price=%.2f invoice=%s",
            telegram_id, purchase_id, gb, price_rubles, invoice_id,
        )

        text = i18n_get_text(language, "payment.lava_waiting", amount=price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "payment.lava_pay_button"),
                url=payment_url,
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="buy_traffic",
            )],
        ])

        lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        asyncio.create_task(_auto_delete_lava_msg(callback.bot, telegram_id, lava_msg))
        await callback.answer()

    except Exception as e:
        logger.exception("TRAFFIC_LAVA_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


# ── Bypass-only payment handlers ─────────────────────────────────────

def _get_bypass_pack(gb: int):
    """Get bypass pack from TRAFFIC_PACKS or TRAFFIC_PACKS_EXTENDED."""
    return config.TRAFFIC_PACKS.get(gb) or config.TRAFFIC_PACKS_EXTENDED.get(gb)


async def _bypass_price(telegram_id: int, gb: int):
    """Calculate bypass pack price with discount."""
    pack = _get_bypass_pack(gb)
    if not pack:
        return None, None
    traffic_discount = await database.get_user_traffic_discount(telegram_id)
    discount_pct = traffic_discount["discount_percent"] if traffic_discount else 0
    base_price = pack["price"]
    price = math.ceil(base_price * (1 - discount_pct / 100)) if discount_pct > 0 else base_price
    return price, pack


@traffic_router.callback_query(F.data.startswith("bypass_pay_card:"))
async def callback_bypass_pay_card(callback: CallbackQuery):
    """Pay for bypass-only pack via card (Telegram Payments)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    price_kopecks = price * 100
    MIN_PAYMENT_AMOUNT_KOPECKS = 6400
    if price_kopecks < MIN_PAYMENT_AMOUNT_KOPECKS:
        await callback.answer(i18n_get_text(language, "errors.payment_min_amount"), show_alert=True)
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price_kopecks,
            purchase_type="traffic_pack",
        )

        payload = f"purchase:{purchase_id}"
        prices = [LabeledPrice(label=f"Bypass {gb} GB", amount=price_kopecks)]

        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Atlas Secure — Bypass {gb} GB",
            description=f"Bypass whitelist traffic — {gb} GB",
            payload=payload,
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
        )
        logger.info("BYPASS_CARD_INVOICE_SENT user=%s purchase_id=%s gb=%s price=%s", telegram_id, purchase_id, gb, price)
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_CARD_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@traffic_router.callback_query(F.data.startswith("bypass_pay_sbp:"))
async def callback_bypass_pay_sbp(callback: CallbackQuery):
    """Pay for bypass-only pack via SBP (Platega, +11%)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    price_kopecks = price * 100

    try:
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)

        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=sbp_price_kopecks,
            purchase_type="traffic_pack",
        )

        sbp_price_rubles = sbp_price_kopecks / 100.0

        tx_data = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Atlas Secure — Bypass {gb} GB",
            purchase_id=purchase_id,
        )

        transaction_id = tx_data["transaction_id"]
        redirect_url = tx_data["redirect_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(transaction_id))
        except Exception as e:
            logger.error("Failed to save SBP tx_id: purchase_id=%s error=%s", purchase_id, e)

        text = i18n_get_text(language, "payment.sbp_waiting", amount=sbp_price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "payment.sbp_pay_button"), url=redirect_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_bypass_only")],
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_SBP_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@traffic_router.callback_query(F.data.startswith("bypass_pay_stars:"))
async def callback_bypass_pay_stars(callback: CallbackQuery):
    """Pay for bypass-only pack via Telegram Stars."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    # Convert RUB to Stars (+70% markup, ~1.85 RUB per star)
    price_stars = math.ceil(price * 1.7 / 1.85)
    if price_stars < 1:
        price_stars = 1

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price_stars,
            purchase_type="traffic_pack",
        )

        payload = f"purchase:{purchase_id}"
        prices = [LabeledPrice(label=f"Bypass {gb} GB", amount=price_stars)]

        await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Atlas Secure — Bypass {gb} GB",
            description=f"Bypass whitelist traffic — {gb} GB",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
        )
        logger.info("BYPASS_STARS_INVOICE_SENT user=%s purchase_id=%s gb=%s stars=%s", telegram_id, purchase_id, gb, price_stars)
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_STARS_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@traffic_router.callback_query(F.data.startswith("bypass_pay_crypto:"))
async def callback_bypass_pay_crypto(callback: CallbackQuery):
    """Pay for bypass-only pack via CryptoBot."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    import cryptobot_service
    if not cryptobot_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.crypto_unavailable"), show_alert=True)
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price * 100,
            purchase_type="traffic_pack",
        )

        invoice = await cryptobot_service.create_invoice(
            amount_rubles=float(price),
            description=f"Atlas Secure — Bypass {gb} GB",
            purchase_id=purchase_id,
        )

        pay_url = invoice.get("pay_url") or invoice.get("bot_invoice_url")
        if not pay_url:
            raise ValueError("No pay_url in CryptoBot response")

        text = i18n_get_text(language, "payment.crypto_waiting", amount=float(price))
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "payment.crypto_pay_button"), url=pay_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_bypass_only")],
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_CRYPTO_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)


@traffic_router.callback_query(F.data.startswith("bypass_pay_lava:"))
async def callback_bypass_pay_lava(callback: CallbackQuery):
    """Pay for bypass-only pack via Lava (card)."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        gb = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return

    price, pack = await _bypass_price(telegram_id, gb)
    if not price:
        return

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.lava_unavailable"), show_alert=True)
        return

    try:
        purchase_id = await database.create_pending_purchase(
            telegram_id=telegram_id,
            tariff=f"bypass_{gb}gb",
            period_days=0,
            price_kopecks=price * 100,
            purchase_type="traffic_pack",
        )

        price_rubles = float(price)

        invoice_data = await lava_service.create_invoice(
            amount_rubles=price_rubles,
            purchase_id=purchase_id,
            comment=f"Atlas Secure — Bypass {gb} GB",
        )

        invoice_id = invoice_data["invoice_id"]
        payment_url = invoice_data["payment_url"]

        try:
            await database.update_pending_purchase_invoice_id(purchase_id, str(invoice_id))
        except Exception as e:
            logger.error("Failed to save Lava invoice_id: purchase_id=%s error=%s", purchase_id, e)

        text = i18n_get_text(language, "payment.lava_waiting", amount=price_rubles)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=i18n_get_text(language, "payment.lava_pay_button"), url=payment_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="buy_bypass_only")],
        ])
        lava_msg = await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        asyncio.create_task(_auto_delete_lava_msg(callback.bot, telegram_id, lava_msg))
        await callback.answer()

    except Exception as e:
        logger.exception("BYPASS_LAVA_ERROR user=%s gb=%s: %s", telegram_id, gb, e)
        await callback.answer(i18n_get_text(language, "errors.payment_create"), show_alert=True)
