"""
Telegram Stars purchase flow.

Screens:
1. Choose star pack (60-10000 stars, 2 per row)
2. Choose recipient (Себе / Подарить)
3. Enter username (@username)
4. Choose payment method (card)
5. Payment → success + admin notification
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import config
import database
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.core.rate_limit import check_rate_limit
from app.handlers.common.guards import ensure_db_ready_callback
from app.handlers.common.states import TelegramStarsState
from app.handlers.common.utils import safe_edit_text

stars_purchase_router = Router()
logger = logging.getLogger(__name__)

MAX_USERNAME_ATTEMPTS = 7
_USERNAME_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$")

STARS_PACKS = {
    60:    {"price": 115},
    125:   {"price": 239},
    250:   {"price": 479},
    500:   {"price": 949},
    1000:  {"price": 1899},
    2000:  {"price": 3799},
    3000:  {"price": 5699},
    5000:  {"price": 9499},
    7000:  {"price": 13299},
    10000: {"price": 18999},
}

INVOICE_TIMEOUT = getattr(config, "INVOICE_TIMEOUT_SECONDS", 900)


def _is_safe_text(text: str) -> bool:
    if not text or len(text) > 64:
        return False
    if any(ord(c) < 32 and c not in ("\n", "\r", "\t") for c in text):
        return False
    if re.search(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]", text):
        return False
    return True


def _is_valid_username(text: str) -> bool:
    if not _is_safe_text(text):
        return False
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return bool(_USERNAME_RE.match(text))


async def _schedule_invoice_deletion(bot: Bot, chat_id: int, message_id: int, timeout: int = INVOICE_TIMEOUT):
    try:
        await asyncio.sleep(timeout)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ─── Screen 1: Choose star pack ───

@stars_purchase_router.callback_query(F.data == "stars_buy")
async def callback_stars_buy(callback: CallbackQuery, state: FSMContext):
    if not await ensure_db_ready_callback(callback):
        return
    try:
        await callback.answer()
    except Exception:
        pass

    language = await resolve_user_language(callback.from_user.id)
    await state.clear()
    await state.set_state(TelegramStarsState.choose_pack)

    text = "⭐ <b>Купить Telegram Stars</b>\n\nВыберите количество звёзд:"

    rows = []
    row = []
    for stars, info in STARS_PACKS.items():
        price = info["price"]
        label = f"{stars}⭐ | {price}₽"
        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"stars_pack:{stars}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data="mini_shop",
    )])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    has_photo = getattr(callback.message, "photo", None) and len(callback.message.photo) > 0
    if has_photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=kb, parse_mode="HTML",
        )
    else:
        await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")


# ─── Screen 2: Choose recipient ───

@stars_purchase_router.callback_query(
    F.data.startswith("stars_pack:"),
    StateFilter(
        TelegramStarsState.choose_pack,
        TelegramStarsState.choose_recipient,
        TelegramStarsState.waiting_for_username,
        TelegramStarsState.choose_payment_method,
    ),
)
async def callback_stars_pack(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    try:
        stars = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        return

    pack = STARS_PACKS.get(stars)
    if not pack:
        return

    language = await resolve_user_language(callback.from_user.id)
    await state.update_data(stars_amount=stars, stars_price=pack["price"])
    await state.set_state(TelegramStarsState.choose_recipient)

    text = (
        f"⭐ <b>{stars} Telegram Stars — {pack['price']} ₽</b>\n\n"
        f"Выберите получателя звёзд:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Себе", callback_data="stars_recipient:self")],
        [InlineKeyboardButton(text="🎁 Подарить", callback_data="stars_recipient:gift")],
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data="stars_buy",
        )],
    ])
    await safe_edit_text(callback.message, text, reply_markup=kb, bot=callback.bot, parse_mode="HTML")


# ─── Screen 3: Enter username ───

@stars_purchase_router.callback_query(
    F.data.startswith("stars_recipient:"),
    StateFilter(TelegramStarsState.choose_recipient),
)
async def callback_stars_recipient(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    recipient_type = callback.data.split(":")[1]
    language = await resolve_user_language(callback.from_user.id)

    await state.update_data(
        stars_recipient_type=recipient_type,
        stars_attempts=MAX_USERNAME_ATTEMPTS,
    )
    await state.set_state(TelegramStarsState.waiting_for_username)

    data = await state.get_data()
    stars = data.get("stars_amount", 0)
    price = data.get("stars_price", 0)

    if recipient_type == "self":
        hint = "Введите свой username Telegram:"
    else:
        hint = "Введите username друга:"

    text = (
        f"⭐ <b>Купить {stars} Telegram Stars — {price} ₽</b>\n\n"
        f"{hint}\n\n"
        f"⚠️ Обязательно через <b>@</b>\n"
        f"Пример: <code>@username</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "common.back"),
            callback_data=f"stars_pack:{stars}",
        )],
    ])

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        callback.from_user.id, text, reply_markup=kb, parse_mode="HTML",
    )


# ─── Screen 3b: Process username input ───

@stars_purchase_router.message(StateFilter(TelegramStarsState.waiting_for_username))
async def process_stars_username(message: Message, state: FSMContext):
    telegram_id = message.from_user.id
    language = await resolve_user_language(telegram_id)
    text = (message.text or "").strip()

    if not text.startswith("@"):
        return

    if not _is_valid_username(text):
        data = await state.get_data()
        attempts = data.get("stars_attempts", MAX_USERNAME_ATTEMPTS) - 1
        await state.update_data(stars_attempts=attempts)

        if attempts <= 0:
            await state.clear()
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
            ])
            await message.answer(
                "❌ Слишком много неверных попыток. Попробуйте позже.",
                reply_markup=kb, parse_mode="HTML",
            )
        return

    username = text
    await state.update_data(stars_username=username)
    await state.set_state(TelegramStarsState.choose_payment_method)

    data = await state.get_data()
    stars = data.get("stars_amount", 0)
    price = data.get("stars_price", 0)

    confirm_text = (
        f"⭐ <b>Купить Telegram Stars</b>\n\n"
        f"📦 Количество: <b>{stars}⭐</b>\n"
        f"👤 Получатель: <b>{username}</b>\n"
        f"💰 К оплате: <b>{price} ₽</b>\n\n"
        f"Выберите способ оплаты:"
    )

    balance = await database.get_user_balance(telegram_id)
    buttons = []
    if balance >= price:
        buttons.append([InlineKeyboardButton(
            text=f"💰 Баланс ({balance:.2f} ₽)",
            callback_data="stars_pay:balance",
        )])
    if config.TG_PROVIDER_TOKEN:
        buttons.append([InlineKeyboardButton(text="💳 Банковская карта", callback_data="stars_pay:card")])

    import lava_service
    if lava_service.is_enabled():
        buttons.append([InlineKeyboardButton(text="💳 Карта (Lava)", callback_data="stars_pay:lava")])

    if config.PLATEGA_MERCHANT_ID:
        import math
        sbp_price = math.ceil(price * (1 + config.SBP_MARKUP_PERCENT / 100))
        buttons.append([InlineKeyboardButton(text=f"📱 СБП ({sbp_price} ₽)", callback_data="stars_pay:sbp")])

    buttons.append([InlineKeyboardButton(
        text=i18n_get_text(language, "common.back"),
        callback_data=f"stars_pack:{stars}",
    )])
    await message.answer(confirm_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


# ─── Shared: extract & validate FSM data ───

async def _get_stars_fsm_data(callback: CallbackQuery, state: FSMContext):
    """Extract stars purchase data from FSM. Returns (username, stars, price, language) or None."""
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
    username = data.get("stars_username")
    stars = data.get("stars_amount")
    price = data.get("stars_price")

    if not all([username, stars, price]):
        await callback.answer(i18n_get_text(language, "errors.session_expired"), show_alert=True)
        await state.clear()
        return None
    return username, stars, price, language


async def _create_stars_purchase(telegram_id, username, stars, price):
    """Create pending purchase and return purchase_id."""
    price_kopecks = price * 100
    purchase_id = await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff="telegram_stars",
        period_days=0,
        price_kopecks=price_kopecks,
        purchase_type="telegram_stars",
        country=f"{username}|{stars}",
    )
    logger.info("STARS_PURCHASE_CREATED user=%s purchase_id=%s stars=%s price=%s", telegram_id, purchase_id, stars, price)
    return purchase_id, price_kopecks


# ─── Payment: Balance ───

@stars_purchase_router.callback_query(F.data == "stars_pay:balance", StateFilter(TelegramStarsState.choose_payment_method))
async def callback_stars_pay_balance(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    result = await _get_stars_fsm_data(callback, state)
    if not result:
        return
    username, stars, price, language = result
    telegram_id = callback.from_user.id

    balance = await database.get_user_balance(telegram_id)
    if balance < price:
        await callback.message.answer("❌ Недостаточно средств на балансе.", parse_mode="HTML")
        return

    try:
        purchase_id, price_kopecks = await _create_stars_purchase(telegram_id, username, stars, price)
        await database.decrease_balance(telegram_id, price, source="stars_purchase", description=f"Telegram Stars {stars}⭐ → {username}")
        await database.mark_pending_purchase_paid(purchase_id)
        await send_stars_success(callback.bot, telegram_id, purchase_id)
    except Exception as e:
        logger.exception("STARS_BALANCE_ERROR user=%s error=%s", telegram_id, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")
        return

    await state.clear()


# ─── Payment: Card (TG Payments) ───

@stars_purchase_router.callback_query(F.data == "stars_pay:card", StateFilter(TelegramStarsState.choose_payment_method))
async def callback_stars_pay_card(callback: CallbackQuery, state: FSMContext):
    result = await _get_stars_fsm_data(callback, state)
    if not result:
        return
    username, stars, price, language = result
    telegram_id = callback.from_user.id

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer(i18n_get_text(language, "errors.payments_unavailable"), show_alert=True)
        return

    try:
        purchase_id, price_kopecks = await _create_stars_purchase(telegram_id, username, stars, price)
        await state.update_data(stars_purchase_id=purchase_id)

        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title="Telegram Stars",
            description=f"Telegram Stars — {stars}⭐ для {username}",
            payload=f"purchase:{purchase_id}",
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=f"{stars} Telegram Stars", amount=price_kopecks)],
        )
        await callback.bot.send_message(telegram_id, i18n_get_text(language, "payment.invoice_timeout"), parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, invoice_msg.message_id))
        await state.set_state(TelegramStarsState.processing_payment)
    except Exception as e:
        logger.exception("STARS_CARD_ERROR user=%s error=%s", telegram_id, e)
        await callback.answer(i18n_get_text(language, "errors.payment_processing"), show_alert=True)

    try:
        await callback.answer()
    except Exception:
        pass


# ─── Payment: Lava (card) ───

@stars_purchase_router.callback_query(F.data == "stars_pay:lava", StateFilter(TelegramStarsState.choose_payment_method))
async def callback_stars_pay_lava(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    result = await _get_stars_fsm_data(callback, state)
    if not result:
        return
    username, stars, price, language = result
    telegram_id = callback.from_user.id

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer("Оплата временно недоступна", show_alert=True)
        return

    try:
        purchase_id, price_kopecks = await _create_stars_purchase(telegram_id, username, stars, price)
        invoice = await lava_service.create_invoice(
            amount=float(price),
            order_id=purchase_id,
            description=f"Telegram Stars {stars}⭐ → {username}",
        )
        if not invoice or not invoice.get("url"):
            await callback.message.answer("❌ Ошибка создания платежа.", parse_mode="HTML")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=invoice["url"])],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
        ])
        msg = await callback.bot.send_message(telegram_id, i18n_get_text(language, "payment.invoice_timeout"), reply_markup=kb, parse_mode="HTML")
        asyncio.create_task(_schedule_invoice_deletion(callback.bot, telegram_id, msg.message_id))
        await state.set_state(TelegramStarsState.processing_payment)
    except Exception as e:
        logger.exception("STARS_LAVA_ERROR user=%s error=%s", telegram_id, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")


# ─── Payment: SBP (Platega) ───

@stars_purchase_router.callback_query(F.data == "stars_pay:sbp", StateFilter(TelegramStarsState.choose_payment_method))
async def callback_stars_pay_sbp(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass

    result = await _get_stars_fsm_data(callback, state)
    if not result:
        return
    username, stars, price, language = result
    telegram_id = callback.from_user.id

    import math
    sbp_price = math.ceil(price * (1 + config.SBP_MARKUP_PERCENT / 100))
    price_kopecks = sbp_price * 100

    try:
        purchase_id, _ = await _create_stars_purchase(telegram_id, username, stars, sbp_price)

        from app.services.payments import platega_service
        transaction = await platega_service.create_transaction(
            amount_kopecks=price_kopecks,
            order_id=purchase_id,
            description=f"Telegram Stars {stars}⭐ → {username}",
            payment_method=2,
        )
        if not transaction or not transaction.get("url"):
            await callback.message.answer("❌ Ошибка создания платежа СБП.", parse_mode="HTML")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📱 Оплатить через СБП", url=transaction["url"])],
            [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="mini_shop")],
        ])
        await callback.bot.send_message(telegram_id, i18n_get_text(language, "payment.invoice_timeout"), reply_markup=kb, parse_mode="HTML")
        await state.set_state(TelegramStarsState.processing_payment)
    except Exception as e:
        logger.exception("STARS_SBP_ERROR user=%s error=%s", telegram_id, e)
        await callback.message.answer(i18n_get_text(language, "errors.payment_processing"), parse_mode="HTML")


# ─── Post-payment: success + admin notification ───

async def send_stars_success(
    bot: Bot,
    telegram_id: int,
    purchase_id: str,
    purchase: dict | None = None,
):
    language = await resolve_user_language(telegram_id)

    if not purchase:
        purchase = await database.get_pending_purchase_by_id(purchase_id)
    if not purchase:
        logger.error("STARS_SUCCESS_NO_PURCHASE purchase_id=%s", purchase_id)
        return

    price_kopecks = purchase.get("price_kopecks", 0)
    price_rubles = price_kopecks / 100

    country_field = purchase.get("country") or ""
    if "|" in country_field:
        username, stars_str = country_field.split("|", 1)
        stars = int(stars_str)
    else:
        username = country_field
        stars = 0

    price_str = f"{price_rubles:,.0f}".replace(",", " ")

    text = (
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"⭐ Товар: Telegram Stars\n"
        f"📦 Количество: {stars}⭐\n"
        f"👤 Получатель: {username}\n"
        f"💳 Сумма: {price_str} ₽\n\n"
        f"⏳ Ожидайте получения звёзд в течение <b>5–15 минут</b>.\n\n"
        f"Если звёзды не поступили, напишите нам:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/Atlas_SupportSecurity")],
        [InlineKeyboardButton(text=i18n_get_text(language, "common.back"), callback_data="menu_main")],
    ])
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error("STARS_SUCCESS_MSG_FAILED user=%s error=%s", telegram_id, e)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    admin_text = (
        f"⭐ <b>ПОКУПКА TELEGRAM STARS</b>\n\n"
        f"👤 Покупатель: <code>{telegram_id}</code>\n"
        f"📦 Количество: {stars}⭐\n"
        f"👤 Получатель: {username}\n"
        f"💳 Сумма: {price_str} ₽\n"
        f"🕐 Дата: {now_str}\n\n"
        f"💬 <i>Выдайте звёзды получателю</i>"
    )
    try:
        await bot.send_message(config.ADMIN_TELEGRAM_ID, admin_text, parse_mode="HTML")
        logger.info(
            "STARS_ADMIN_NOTIFIED buyer=%s username=%s stars=%s price=%s",
            telegram_id, username, stars, price_str,
        )
    except Exception as e:
        logger.error("STARS_ADMIN_NOTIFY_FAILED error=%s", e)
