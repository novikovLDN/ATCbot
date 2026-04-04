"""
Traffic display and purchase handlers for Remnawave (Yandex node).

Callbacks:
    traffic_info         — show traffic usage stats
    buy_traffic          — show traffic pack selection
    buy_traffic:<gb>     — select a specific pack, initiate payment
"""
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services import remnawave_api
from app.services.language_service import resolve_user_language
from app.handlers.common.guards import ensure_db_ready_callback

traffic_router = Router()
logger = logging.getLogger(__name__)

GB = 1024 * 1024 * 1024


def _progress_bar(used: int, limit: int, width: int = 20) -> str:
    """Generate a text progress bar."""
    if limit <= 0:
        return "\u2588" * width
    pct = min(used / limit, 1.0)
    filled = int(pct * width)
    empty = width - filled
    return "\u2588" * filled + "\u2591" * empty


def _format_bytes_gb(b: int) -> str:
    """Format bytes as GB with 1 decimal."""
    return f"{b / GB:.1f}"


# =========================================================================
# Traffic info screen
# =========================================================================

@traffic_router.callback_query(F.data == "traffic_info")
async def callback_traffic_info(callback: CallbackQuery):
    """Show traffic usage for Yandex node."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        await callback.answer()
    except Exception:
        pass

    if not config.REMNAWAVE_ENABLED:
        await callback.message.edit_text(
            i18n_get_text(language, "traffic.unavailable"),
            reply_markup=_back_keyboard(language),
        )
        return

    # Get Remnawave UUID
    uuid = await database.get_remnawave_uuid(telegram_id)
    if not uuid:
        await callback.message.edit_text(
            i18n_get_text(language, "traffic.no_account"),
            reply_markup=_back_keyboard(language),
        )
        return

    # Get traffic stats
    traffic = await remnawave_api.get_user_traffic(uuid)
    if not traffic:
        await callback.message.edit_text(
            i18n_get_text(language, "traffic.fetch_error"),
            reply_markup=_back_keyboard(language),
        )
        return

    used = traffic["usedTrafficBytes"]
    limit = traffic["trafficLimitBytes"]
    remaining = max(0, limit - used)
    pct = int((used / limit) * 100) if limit > 0 else 100
    bar = _progress_bar(used, limit)

    # Warning icon based on remaining
    if remaining <= 0:
        warn = "\U0001f6ab"  # 🚫
    elif remaining <= 512 * 1024 * 1024:
        warn = "\u2757\ufe0f"  # ❗️
    elif remaining <= 1 * GB:
        warn = "\U0001f534"  # 🔴
    elif remaining <= 3 * GB:
        warn = "\u26a0\ufe0f"  # ⚠️
    else:
        warn = "\u2705"  # ✅

    text = i18n_get_text(
        language, "traffic.info",
        used=_format_bytes_gb(used),
        limit=_format_bytes_gb(limit),
        bar=bar,
        pct=str(pct),
        remaining=_format_bytes_gb(remaining),
        warn=warn,
    )

    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_button"),
            callback_data="buy_traffic",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ]
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# =========================================================================
# Traffic purchase screen
# =========================================================================

@traffic_router.callback_query(F.data == "buy_traffic")
async def callback_buy_traffic(callback: CallbackQuery):
    """Show traffic pack selection."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        await callback.answer()
    except Exception:
        pass

    if not config.REMNAWAVE_ENABLED:
        await callback.message.edit_text(
            i18n_get_text(language, "traffic.unavailable"),
            reply_markup=_back_keyboard(language),
        )
        return

    # Get current traffic for display
    uuid = await database.get_remnawave_uuid(telegram_id)
    remaining_text = ""
    if uuid:
        traffic = await remnawave_api.get_user_traffic(uuid)
        if traffic:
            remaining = max(0, traffic["trafficLimitBytes"] - traffic["usedTrafficBytes"])
            limit = traffic["trafficLimitBytes"]
            remaining_text = i18n_get_text(
                language, "traffic.current_remaining",
                remaining=_format_bytes_gb(remaining),
                limit=_format_bytes_gb(limit),
            )

    # Build pack buttons
    packs = sorted(config.TRAFFIC_PACKS.items())
    buttons = []
    row = []
    for gb, info in packs:
        row.append(InlineKeyboardButton(
            text=info["label"],
            callback_data=f"buy_traffic:{gb}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="traffic_info",
    )])

    header = i18n_get_text(language, "traffic.buy_header")
    text = f"{header}\n\n{remaining_text}" if remaining_text else header

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# =========================================================================
# Traffic pack selection → initiate payment
# =========================================================================

@traffic_router.callback_query(F.data.startswith("buy_traffic:"))
async def callback_buy_traffic_pack(callback: CallbackQuery):
    """User selected a traffic pack — initiate payment via balance."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        await callback.answer()
    except Exception:
        pass

    # Parse GB amount
    try:
        gb = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        return

    pack = config.TRAFFIC_PACKS.get(gb)
    if not pack:
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        return

    price = pack["price"]

    # Show confirmation
    text = i18n_get_text(
        language, "traffic.confirm_purchase",
        gb=str(gb),
        price=str(price),
    )
    buttons = [
        [InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.pay_balance", price=str(price)),
            callback_data=f"traffic_pay_balance:{gb}",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="buy_traffic",
        )],
    ]
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# =========================================================================
# Pay for traffic from balance
# =========================================================================

@traffic_router.callback_query(F.data.startswith("traffic_pay_balance:"))
async def callback_traffic_pay_balance(callback: CallbackQuery):
    """Deduct balance and add traffic."""
    if not await ensure_db_ready_callback(callback):
        return

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        await callback.answer()
    except Exception:
        pass

    # Parse
    try:
        gb = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        return

    pack = config.TRAFFIC_PACKS.get(gb)
    if not pack:
        await callback.answer(i18n_get_text(language, "errors.generic"), show_alert=True)
        return

    price = pack["price"]

    # Check balance
    balance = await database.get_user_balance(telegram_id)
    if balance is None or balance < price:
        await callback.message.edit_text(
            i18n_get_text(language, "traffic.insufficient_balance", price=str(price)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "traffic.topup_button"),
                    callback_data="topup_balance",
                )],
                [InlineKeyboardButton(
                    text=i18n_get_text(language, "common.back"),
                    callback_data="buy_traffic",
                )],
            ]),
        )
        return

    # Deduct balance
    ok = await database.decrease_balance(
        telegram_id, price,
        source="traffic_pack",
        description=f"Traffic pack: {gb} GB",
    )
    if not ok:
        await callback.message.edit_text(
            i18n_get_text(language, "traffic.payment_failed"),
            reply_markup=_back_keyboard(language),
        )
        return

    # Record purchase
    await database.record_traffic_purchase(telegram_id, gb, price)

    # Add traffic in Remnawave
    from app.services.remnawave_service import add_traffic
    new_traffic = await add_traffic(telegram_id, gb)

    if new_traffic:
        used = new_traffic["usedTrafficBytes"]
        limit = new_traffic["trafficLimitBytes"]
        remaining = max(0, limit - used)
        pct = int((used / limit) * 100) if limit > 0 else 0
        bar = _progress_bar(used, limit)

        text = i18n_get_text(
            language, "traffic.purchase_success",
            gb=str(gb),
            remaining=_format_bytes_gb(remaining),
            limit=_format_bytes_gb(limit),
            bar=bar,
            pct=str(pct),
        )
    else:
        text = i18n_get_text(language, "traffic.purchase_success_no_stats", gb=str(gb))

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.view_traffic"),
                callback_data="traffic_info",
            )],
            [InlineKeyboardButton(
                text=i18n_get_text(language, "common.back"),
                callback_data="menu_main",
            )],
        ]),
    )


# =========================================================================
# Helpers
# =========================================================================

def _back_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])
