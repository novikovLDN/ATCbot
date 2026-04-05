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


def _format_bytes(b: int) -> str:
    """Format bytes to human-readable GB/MB string."""
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} ГБ"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} МБ"
    return f"{b / 1024:.0f} КБ"


def _progress_bar(used: int, limit: int, length: int = 20) -> str:
    if limit <= 0:
        return "░" * length
    ratio = min(used / limit, 1.0)
    filled = int(ratio * length)
    return "█" * filled + "░" * (length - filled)


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
    if sub_type == "trial":
        text = i18n_get_text(language, "traffic.trial_no_bypass")
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

    rmn_uuid = await database.get_remnawave_uuid(telegram_id)
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
    devices_online = traffic.get("onlineDevices", 0)
    device_limit = traffic.get("deviceLimit", _get_device_limit(sub_type))
    remaining = max(0, limit - used)
    pct = int(used / limit * 100) if limit > 0 else 0

    expires_at = subscription.get("expires_at")
    expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "—"

    bar = _progress_bar(used, limit)
    warning = ""
    if remaining <= 500 * 1024**2:
        warning = "\n\n❗️ " + i18n_get_text(language, "traffic.warning_critical")
    elif remaining <= 3 * 1024**3:
        warning = "\n\n⚠️ " + i18n_get_text(language, "traffic.warning_low", remaining=_format_bytes(remaining))

    # Subscription URL comes directly from Remnawave API response
    sub_url = traffic.get("subscriptionUrl", "")

    text = i18n_get_text(
        language,
        "traffic.info",
        used=_format_bytes(used),
        limit=_format_bytes(limit),
        bar=bar,
        pct=pct,
        devices=devices_online,
        device_limit=device_limit,
        expires=expires_str,
        sub_url=sub_url,
    ) + warning

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_traffic_btn"),
            callback_data="buy_traffic",
        )],
        [InlineKeyboardButton(text="🔄", callback_data="traffic_refresh")],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")


def _get_device_limit(sub_type: str) -> int:
    return config.DEVICE_LIMITS.get(sub_type, 3)


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

    # Build pack buttons
    buttons = []
    for gb, pack in config.TRAFFIC_PACKS.items():
        label = f"{gb} ГБ — {pack['price']} ₽"
        if pack["discount"]:
            label += f"  {pack['discount']}"
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"buy_traffic_pack:{gb}",
        )])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="traffic_info",
    )])

    text = i18n_get_text(language, "traffic.buy_title")
    await safe_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), bot=callback.bot)


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

    pack = config.TRAFFIC_PACKS.get(gb)
    if not pack:
        return

    balance = await database.get_user_balance(telegram_id)
    price = pack["price"]

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

    pack = config.TRAFFIC_PACKS.get(gb)
    if not pack:
        return

    price = pack["price"]
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

    pack = config.TRAFFIC_PACKS.get(gb)
    if not pack:
        return

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    price = pack["price"]
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

    pack = config.TRAFFIC_PACKS.get(gb)
    if not pack:
        return

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer(i18n_get_text(language, "payment.sbp_unavailable"), show_alert=True)
        return

    price = pack["price"]
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
