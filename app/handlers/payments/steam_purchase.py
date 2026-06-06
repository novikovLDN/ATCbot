"""
Shop product: «Пополнить Steam».

Same delivery model as Apple ID and Telegram Premium/Stars — the bot
is a vitrine + payment terminal, the actual top-up is performed by the
admin manually after the bot pings them with the buyer details.

User flow:
  mini_shop → 🎮 Пополнить Steam
    → disclaimer (countries supported, internal Steam rate)
    → ✅ Ознакомлен
    → choose amount (200…12000 ₽, step 200, carousel 12 per page)
    → enter Steam login
    → choose payment method (Card / SBP / Lava / CryptoBot / Stars / Balance)
    → invoice → on success: user gets confirmation, admin gets notif

Pricing: user_chosen_amount + STEAM_FEE_RUB (=110) → final price.
"""
import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from aiogram import Router, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.handlers.common.states import SteamPurchaseState
from app.core.rate_limit import check_rate_limit

steam_purchase_router = Router()
logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

STEAM_FEE_RUB = 110               # service fee added on top of user-chosen amount
STEAM_AMOUNT_STEP = 200
STEAM_AMOUNT_MIN = 200
STEAM_AMOUNT_MAX = 12000
STEAM_AMOUNTS_PER_PAGE = 12       # 2 rows × 6 buttons
SUPPORT_URL = "https://t.me/atlas_suppbot"

# Steam login: 3-32 chars, [A-Za-z0-9_-]. Steam allows additional characters
# in display names but the login (account name) follows this stricter rule.
_STEAM_LOGIN_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

INVOICE_TIMEOUT = getattr(config, "INVOICE_TIMEOUT_SECONDS", 900)


def _all_amounts() -> list:
    """Generated list of all selectable amounts (200, 400, …, 12000)."""
    return list(range(STEAM_AMOUNT_MIN, STEAM_AMOUNT_MAX + 1, STEAM_AMOUNT_STEP))


def _total_pages() -> int:
    n = len(_all_amounts())
    return (n + STEAM_AMOUNTS_PER_PAGE - 1) // STEAM_AMOUNTS_PER_PAGE


def _calc_price(amount: int) -> int:
    """Final price = chosen amount + service fee (₽)."""
    return amount + STEAM_FEE_RUB


def _is_valid_steam_login(text: str) -> bool:
    if not text or len(text) > 64:
        return False
    return bool(_STEAM_LOGIN_RE.match(text))


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, message_id: int, timeout: int = INVOICE_TIMEOUT):
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ── Keyboards ─────────────────────────────────────────────────────────

def _get_disclaimer_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "shop.steam_disclaimer_ack_btn"),
            callback_data="steam:ack",
        )],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="mini_shop",
        )],
    ])


def _get_amount_keyboard(page: int, language: str) -> InlineKeyboardMarkup:
    """Carousel: 2 rows × 6 buttons per page, arrows for navigation."""
    amounts = _all_amounts()
    total_pages = _total_pages()
    page = max(0, min(page, total_pages - 1))

    start = page * STEAM_AMOUNTS_PER_PAGE
    end = start + STEAM_AMOUNTS_PER_PAGE
    chunk = amounts[start:end]

    rows: list = []
    row: list = []
    for i, amount in enumerate(chunk):
        row.append(InlineKeyboardButton(
            text=f"{amount} ₽",
            callback_data=f"steam:amt:{amount}",
        ))
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Carousel navigation
    nav_row: list = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"steam:page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}", callback_data="noop",
    ))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"steam:page:{page + 1}"))
    rows.append(nav_row)

    rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="steam:disclaimer",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_login_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="steam:back_to_amount",
        )],
    ])


def _get_payment_method_keyboard(language: str, price_rub: int, balance: float) -> InlineKeyboardMarkup:
    buttons: list = []

    # NOTE: balance payment is disabled for Steam top-ups by policy.
    # Card / SBP only.  Keeping the `balance` arg for API stability and
    # in case we re-enable it later.
    _ = balance  # intentionally unused

    # Card via TG Payments / YooKassa
    if config.TG_PROVIDER_TOKEN:
        buttons.append([InlineKeyboardButton(
            text="💳 Банковская карта",
            callback_data="steam:pay:card",
        )])

    # Lava
    try:
        import lava_service
        if lava_service.is_enabled():
            buttons.append([InlineKeyboardButton(
                text="💳 Карта (Lava)",
                callback_data="steam:pay:lava",
            )])
    except Exception:
        pass

    # SBP via Platega (+markup)
    if getattr(config, "PLATEGA_MERCHANT_ID", None):
        sbp_price = math.ceil(price_rub * (1 + config.SBP_MARKUP_PERCENT / 100))
        buttons.append([InlineKeyboardButton(
            text=f"📱 СБП ({sbp_price} ₽)",
            callback_data="steam:pay:sbp",
        )])

    # CryptoBot
    try:
        import cryptobot_service
        if cryptobot_service.is_enabled():
            buttons.append([InlineKeyboardButton(
                text="🪙 Крипто (CryptoBot)",
                callback_data="steam:pay:crypto",
            )])
    except Exception:
        pass

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="steam:back_to_amount",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Entry: shop button → disclaimer screen ────────────────────────────

@steam_purchase_router.callback_query(F.data == "steam:disclaimer")
async def callback_steam_disclaimer(callback: CallbackQuery, state: FSMContext):
    """Disclaimer screen — countries + internal-rate notice."""
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    # Reset flow: a fresh entry should clear stale FSM data.
    await state.clear()
    await state.set_state(SteamPurchaseState.waiting_for_disclaimer_ack)

    text = i18n_get_text(language, "shop.steam_disclaimer")
    keyboard = _get_disclaimer_keyboard(language)

    has_photo = bool(getattr(callback.message, "photo", None))
    if has_photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(
            chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML",
        )
    else:
        try:
            await callback.message.edit_text(
                text, reply_markup=keyboard, parse_mode="HTML",
            )
        except Exception:
            await callback.bot.send_message(
                chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML",
            )


# ── Step 2: ack → amount carousel ─────────────────────────────────────

@steam_purchase_router.callback_query(F.data == "steam:ack")
async def callback_steam_ack(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    await _show_amount_screen(callback, state, page=0)


@steam_purchase_router.callback_query(F.data.startswith("steam:page:"))
async def callback_steam_page(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0
    await _show_amount_screen(callback, state, page=page)


@steam_purchase_router.callback_query(F.data == "steam:back_to_amount")
async def callback_steam_back_to_amount(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    await _show_amount_screen(callback, state, page=0)


async def _show_amount_screen(callback: CallbackQuery, state: FSMContext, page: int):
    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    await state.set_state(SteamPurchaseState.choose_amount)

    text = i18n_get_text(language, "shop.steam_amount_title")
    keyboard = _get_amount_keyboard(page, language)

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.bot.send_message(
            chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML",
        )


# ── Step 3: amount selected → ask login ───────────────────────────────

@steam_purchase_router.callback_query(F.data.startswith("steam:amt:"))
async def callback_steam_amount(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    telegram_id = callback.from_user.id
    language = await resolve_user_language(telegram_id)

    try:
        amount = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Неверная сумма", show_alert=True)
        return

    if amount < STEAM_AMOUNT_MIN or amount > STEAM_AMOUNT_MAX or amount % STEAM_AMOUNT_STEP != 0:
        await callback.answer("Сумма вне допустимого диапазона", show_alert=True)
        return

    await state.update_data(steam_amount=amount)
    await state.set_state(SteamPurchaseState.waiting_for_login)

    text = i18n_get_text(language, "shop.steam_login_prompt", amount=amount)
    keyboard = _get_login_keyboard(language)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.bot.send_message(
            chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="HTML",
        )


# ── Step 4: login received → payment method screen ────────────────────

@steam_purchase_router.message(SteamPurchaseState.waiting_for_login)
async def message_steam_login(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)
    login = (message.text or "").strip()

    if not _is_valid_steam_login(login):
        await message.answer(
            i18n_get_text(language, "shop.steam_invalid_login"),
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    amount = data.get("steam_amount")
    if not amount:
        await message.answer("Сессия устарела. Откройте магазин заново.", parse_mode="HTML")
        await state.clear()
        return

    price = _calc_price(amount)
    await state.update_data(steam_login=login, steam_price=price)
    await state.set_state(SteamPurchaseState.choose_payment_method)

    balance = await database.get_user_balance(telegram_id)
    text = i18n_get_text(
        language, "shop.steam_choose_payment",
        amount=amount, login=login, price=price, fee=STEAM_FEE_RUB,
    )
    keyboard = _get_payment_method_keyboard(language, price, balance)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ── Shared FSM extraction ─────────────────────────────────────────────

async def _get_steam_fsm(callback: CallbackQuery, state: FSMContext) -> Optional[Tuple[int, str, int, str]]:
    """Returns (amount, login, price, language) or None on stale FSM."""
    telegram_id = callback.from_user.id
    is_allowed, rate_limit_msg = check_rate_limit(telegram_id, "payment_init")
    if not is_allowed:
        language = await resolve_user_language(telegram_id)
        await callback.answer(
            rate_limit_msg or i18n_get_text(language, "common.rate_limit_message"),
            show_alert=True,
        )
        return None

    language = await resolve_user_language(telegram_id)
    data = await state.get_data()
    amount = data.get("steam_amount")
    login = data.get("steam_login")
    price = data.get("steam_price")
    if not all([amount, login, price]):
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return None
    return int(amount), login, int(price), language


async def _create_pending_purchase(telegram_id: int, login: str, amount: int, price: int) -> Tuple[str, int]:
    """Create steam pending_purchase. Returns (purchase_id, price_kopecks).

    Convention:
      tariff       = "steam_<amount>"     (e.g. steam_1500)
      country      = "<login>"            (reused field, like Apple/Premium)
      price_kopecks = <price> * 100        (final price including fee)
    """
    price_kopecks = price * 100
    purchase_id = await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff=f"steam_{amount}",
        period_days=0,
        price_kopecks=price_kopecks,
        purchase_type="steam",
        country=login,
    )
    logger.info(
        "STEAM_PURCHASE_CREATED user=%s purchase_id=%s amount=%s login=%s price=%s",
        telegram_id, purchase_id, amount, login, price,
    )
    return purchase_id, price_kopecks


# ── Payment: Balance ──────────────────────────────────────────────────

@steam_purchase_router.callback_query(
    F.data == "steam:pay:balance",
    StateFilter(SteamPurchaseState.choose_payment_method),
)
async def callback_steam_pay_balance(callback: CallbackQuery, state: FSMContext):
    # Balance payment is disabled by policy for Steam top-ups.  The UI no
    # longer surfaces this button, but the route stays here as a guard
    # against hand-crafted callback_data.
    try:
        await callback.answer(
            "Оплата с баланса для пополнения Steam недоступна. "
            "Выберите карту или СБП.",
            show_alert=True,
        )
    except Exception:
        pass


# ── Payment: Card via Telegram Payments (YooKassa) ────────────────────

@steam_purchase_router.callback_query(
    F.data == "steam:pay:card",
    StateFilter(SteamPurchaseState.choose_payment_method),
)
async def callback_steam_pay_card(callback: CallbackQuery, state: FSMContext):
    res = await _get_steam_fsm(callback, state)
    if not res:
        return
    amount, login, price, language = res
    telegram_id = callback.from_user.id

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    # Telegram Payments minimum: 64 RUB. Steam min is 200+110=310, well above.
    MIN_KOPECKS = 6400
    price_kopecks = price * 100
    if price_kopecks < MIN_KOPECKS:
        await callback.answer("Сумма ниже минимальной для оплаты картой", show_alert=True)
        return

    try:
        purchase_id, _ = await _create_pending_purchase(telegram_id, login, amount, price)
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Пополнение Steam {amount} ₽",
            description=f"Steam {login} — {amount} ₽ + комиссия {STEAM_FEE_RUB} ₽",
            payload=f"purchase:{purchase_id}",
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=f"Steam {amount} ₽", amount=price_kopecks)],
        )
        await callback.bot.send_message(
            telegram_id, i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML",
        )
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg.message_id))
        await state.set_state(SteamPurchaseState.processing_payment)
    except Exception as e:
        logger.exception("STEAM_CARD_ERROR user=%s err=%s", telegram_id, e)
        await callback.answer(i18n_get_text(language, "errors.payment_processing"), show_alert=True)

    try:
        await callback.answer()
    except Exception:
        pass


# ── Payment: Lava ─────────────────────────────────────────────────────

@steam_purchase_router.callback_query(
    F.data == "steam:pay:lava",
    StateFilter(SteamPurchaseState.choose_payment_method),
)
async def callback_steam_pay_lava(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    res = await _get_steam_fsm(callback, state)
    if not res:
        return
    amount, login, price, language = res
    telegram_id = callback.from_user.id

    try:
        import lava_service
    except ImportError:
        await callback.answer("Оплата недоступна", show_alert=True)
        return
    if not lava_service.is_enabled():
        await callback.answer("Оплата временно недоступна", show_alert=True)
        return

    try:
        purchase_id, _ = await _create_pending_purchase(telegram_id, login, amount, price)
        invoice = await lava_service.create_invoice(
            amount_rubles=float(price),
            purchase_id=purchase_id,
            comment=f"Steam {login} — {amount} ₽",
        )
        pay_url = (invoice or {}).get("url") or (invoice or {}).get("payment_url") or ""
        if not pay_url:
            await callback.message.answer("❌ Ошибка создания платежа.", parse_mode="HTML")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
        ])
        msg = await callback.bot.send_message(
            telegram_id, i18n_get_text(language, "payment.invoice_timeout"),
            reply_markup=kb, parse_mode="HTML",
        )
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg.message_id))
        await state.set_state(SteamPurchaseState.processing_payment)
    except Exception as e:
        logger.exception("STEAM_LAVA_ERROR user=%s err=%s", telegram_id, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")


# ── Payment: SBP via Platega ──────────────────────────────────────────

@steam_purchase_router.callback_query(
    F.data == "steam:pay:sbp",
    StateFilter(SteamPurchaseState.choose_payment_method),
)
async def callback_steam_pay_sbp(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    res = await _get_steam_fsm(callback, state)
    if not res:
        return
    amount, login, price, language = res
    telegram_id = callback.from_user.id

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer("СБП временно недоступен", show_alert=True)
        return

    sbp_price = math.ceil(price * (1 + config.SBP_MARKUP_PERCENT / 100))

    try:
        # Persist the SBP-marked-up price so the admin sees what the user actually paid.
        purchase_id, _ = await _create_pending_purchase(telegram_id, login, amount, sbp_price)

        transaction = await platega_service.create_transaction(
            amount_rubles=float(sbp_price),
            description=f"Steam {login} — {amount} ₽",
            purchase_id=purchase_id,
        )
        pay_url = (transaction or {}).get("redirect_url") or (transaction or {}).get("url") or ""
        if not pay_url:
            await callback.message.answer("❌ Ошибка создания платежа СБП.", parse_mode="HTML")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📱 Оплатить через СБП", url=pay_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
        ])
        await callback.bot.send_message(
            telegram_id, i18n_get_text(language, "payment.invoice_timeout"),
            reply_markup=kb, parse_mode="HTML",
        )
        await state.set_state(SteamPurchaseState.processing_payment)
    except Exception as e:
        logger.exception("STEAM_SBP_ERROR user=%s err=%s", telegram_id, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")


# ── Payment: CryptoBot ────────────────────────────────────────────────

@steam_purchase_router.callback_query(
    F.data == "steam:pay:crypto",
    StateFilter(SteamPurchaseState.choose_payment_method),
)
async def callback_steam_pay_crypto(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    res = await _get_steam_fsm(callback, state)
    if not res:
        return
    amount, login, price, language = res
    telegram_id = callback.from_user.id

    try:
        import cryptobot_service
    except ImportError:
        await callback.answer("CryptoBot недоступен", show_alert=True)
        return
    if not cryptobot_service.is_enabled():
        await callback.answer("CryptoBot временно недоступен", show_alert=True)
        return

    try:
        purchase_id, _ = await _create_pending_purchase(telegram_id, login, amount, price)
        invoice = await cryptobot_service.create_invoice(
            amount_rubles=float(price),
            description=f"Steam {login} — {amount} ₽",
            purchase_id=purchase_id,
        )
        pay_url = (invoice or {}).get("pay_url") or (invoice or {}).get("url") or ""
        if not pay_url:
            await callback.message.answer("❌ Ошибка создания крипто-платежа.", parse_mode="HTML")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🪙 Оплатить", url=pay_url)],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
        ])
        msg = await callback.bot.send_message(
            telegram_id, i18n_get_text(language, "payment.invoice_timeout"),
            reply_markup=kb, parse_mode="HTML",
        )
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg.message_id))
        await state.set_state(SteamPurchaseState.processing_payment)
    except Exception as e:
        logger.exception("STEAM_CRYPTO_ERROR user=%s err=%s", telegram_id, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")


# ── Post-payment: success message + admin notification ───────────────

async def send_steam_success(
    bot: Bot,
    telegram_id: int,
    purchase_id: str,
    purchase: Optional[dict] = None,
):
    """Notify user (waiting screen) and admin (manual top-up instruction)."""
    language = await resolve_user_language(telegram_id)

    if not purchase:
        purchase = await database.get_pending_purchase_by_id(purchase_id)
    if not purchase:
        logger.error("STEAM_SUCCESS_NO_PURCHASE purchase_id=%s", purchase_id)
        return

    tariff = purchase.get("tariff") or ""
    # tariff = "steam_<amount>"
    try:
        amount = int(tariff.split("_", 1)[1]) if "_" in tariff else 0
    except (ValueError, IndexError):
        amount = 0

    price_kopecks = int(purchase.get("price_kopecks") or 0)
    price_rubles = price_kopecks // 100
    login = purchase.get("country") or "—"

    # User-side success
    user_text = i18n_get_text(
        language, "shop.steam_success",
        login=login, amount=amount, price=price_rubles,
    )
    user_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_URL)],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="menu_main",
        )],
    ])
    try:
        await bot.send_message(telegram_id, user_text, reply_markup=user_kb, parse_mode="HTML")
    except Exception as e:
        logger.error("STEAM_SUCCESS_MSG_FAILED user=%s err=%s", telegram_id, e)

    # Admin-side notification
    try:
        user = await database.get_user(telegram_id)
        buyer_username = f"@{user['username']}" if user and user.get("username") else "—"
    except Exception:
        buyer_username = "—"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    admin_text = i18n_get_text(
        "ru", "shop.steam_admin",
        buyer_id=telegram_id,
        buyer_username=buyer_username,
        login=login,
        amount=amount,
        price=price_rubles,
        date=now_str,
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать пользователю", callback_data="admin:chat")],
    ])
    try:
        await bot.send_message(
            config.ADMIN_TELEGRAM_ID, admin_text, reply_markup=admin_kb, parse_mode="HTML",
        )
        logger.info(
            "STEAM_ADMIN_NOTIFIED buyer=%s login=%s amount=%s price=%s",
            telegram_id, login, amount, price_rubles,
        )
    except Exception as e:
        logger.error("STEAM_ADMIN_NOTIFY_FAILED err=%s", e)
