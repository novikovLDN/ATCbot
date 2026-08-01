"""
Shop product: Spotify Premium (EG-регион, аккаунты индивидуальные + для двоих).

Модель доставки: как Steam / Apple ID — бот принимает оплату + данные
аккаунта (email, пароль), админ вручную активирует Premium, бот
уведомляет юзера.

User flow:
    mini_shop → 🎧 Spotify Premium
      → disclaimer (нельзя продлевать заранее)
      → info (как это работает, blockquote-инструкция)
      → choose plan (individual | duo)
      → choose duration
      → email input
      → email confirmation (Подтвердить / Изменить)
      → password input
      → password confirmation (Подтвердить / Изменить)
      → order review (тариф + срок + email + пароль + цена)
      → payment method (карта / СБП / crypto / с баланса)
      → invoice → на webhook: user "delivery within 1h", admin — full order

Хранение данных: email → pending_purchases.country,
пароль → pending_purchases.promo_code (те же трюк, что в Steam с логином).
Обе колонки — свободные текстовые, миграции не требуется.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, Message,
)

import config
import database
from app.handlers.common.states import SpotifyPurchaseState

spotify_purchase_router = Router()
logger = logging.getLogger(__name__)

SUPPORT_URL = "https://t.me/atlas_suppbot"
INSTRUCTION_UNLINK_GOOGLE = (
    "https://support.spotify.com/us/article/change-your-password/"
)

# ── Tariffs & pricing ─────────────────────────────────────────────────

# key: (label, description, {duration_months: price_rub})
_PLANS: dict[str, dict] = {
    "ind": {
        "label": "🎧 Индивидуальная",
        "flag": "🇪🇬",
        "short": (
            "🇪🇬 <b>Регион: Египет</b> — предоплаченный Premium "
            "на 1 аккаунт.\n\n"
            "✅ Без рекламы  ·  📥 оффлайн  ·  🎵 до 320 кбит/с\n"
            "❌ Нет Lossless  ·  ❌ нет AI DJ"
        ),
        "prices": {1: 349, 3: 1199, 6: 1399, 12: 2499},
    },
    "duo": {
        "label": "👥 Для двоих",
        "flag": "🇪🇬",
        "short": (
            "🇪🇬 <b>Регион: Египет</b> — Premium на 2 аккаунта.\n\n"
            "✅ Без рекламы  ·  📥 оффлайн  ·  🎬 Canvas  ·  🎵 до 320 кбит/с\n"
            "❌ Нет Lossless  ·  ❌ нет AI DJ  ·  ❌ расширить нельзя"
        ),
        "prices": {3: 1299, 6: 1999, 12: 3399},
    },
}


def _plan_meta(plan: str) -> dict:
    return _PLANS.get(plan) or _PLANS["ind"]


def _price_rub(plan: str, months: int) -> Optional[int]:
    return _plan_meta(plan)["prices"].get(months)


def _duration_label(months: int) -> str:
    if months == 12:
        return "1 год"
    if months == 1:
        return "1 мес"
    return f"{months} мес"


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _valid_email(text: str) -> bool:
    if not text or len(text) > 128:
        return False
    return bool(_EMAIL_RE.match(text.strip()))


def _valid_password(text: str) -> bool:
    # Spotify пароли — от 8 символов. Верхнюю границу ставим 128
    # (защита от накладок с текстом-полотном).
    if not text:
        return False
    t = text.strip()
    return 8 <= len(t) <= 128


# ── Screens ───────────────────────────────────────────────────────────

_TXT_DISCLAIMER = (
    "🎧 <b>Spotify Premium</b>\n\n"
    "<blockquote>⚠️ <b>Важно:</b> продлить уже действующую подписку "
    "нельзя. Не продлевайте её заранее — дождитесь окончания текущего "
    "периода Premium.</blockquote>\n\n"
    "Если ваш Premium уже истёк или его никогда не было — нажимайте "
    "<b>«Продолжить»</b> и переходите к оформлению."
)

_TXT_INFO = (
    "🎧 <b>Как это работает</b>\n\n"
    "<blockquote>1. Выберите тариф и срок подписки.\n"
    "2. Введите email и пароль от вашего Spotify-аккаунта.\n"
    "3. Если вход через Google/Apple — заранее привяжите пароль "
    "и отключите OAuth-логин.\n"
    "4. Если включена 2FA — временно отключите её.\n"
    "5. Оплатите заказ удобным способом.\n"
    "6. В течение <b>15–60 минут</b> оператор активирует Premium.</blockquote>\n\n"
    "<blockquote>🛡️ <b>Полностью безопасно</b> — мы работаем только "
    "со Spotify-аккаунтами.\n"
    "⌛ <b>Часы работы</b>: 10:00–00:00 МСК.\n"
    "💸 <b>Выгодно</b> — цены ниже официальных, кэшбэк на баланс.</blockquote>\n\n"
    "<i>Рекомендуем сменить пароль на случайный перед заказом и "
    "поменять его снова после активации.</i>"
)

_TXT_PLAN_TITLE = "🎧 <b>Выберите тариф Spotify</b>"

_TXT_EMAIL_PROMPT = (
    "📧 <b>Введите email от Spotify</b>\n\n"
    "Пришлите следующим сообщением email, привязанный к вашему аккаунту.\n\n"
    "<blockquote>Если вы входите через <b>Google</b> или <b>Apple</b> — "
    "сначала привяжите пароль в настройках Spotify и отключите OAuth-вход, "
    "иначе активация не пройдёт.</blockquote>\n\n"
    "Отмена: /cancel"
)

_TXT_PASSWORD_PROMPT = (
    "🔒 <b>Введите пароль от Spotify</b>\n\n"
    "Пришлите пароль следующим сообщением. Сообщение будет "
    "удалено сразу после подтверждения.\n\n"
    "<blockquote>🛡️ Обязательно смените пароль на случайный <b>до заказа</b> "
    "и снова смените его после активации Premium.</blockquote>\n\n"
    "Отмена: /cancel"
)


def _mask_password(pwd: str) -> str:
    if not pwd:
        return "—"
    if len(pwd) <= 2:
        return "•" * len(pwd)
    return pwd[0] + "•" * (len(pwd) - 2) + pwd[-1]


# ── Entry: mini_shop → Spotify disclaimer ─────────────────────────────

@spotify_purchase_router.callback_query(F.data == "spotify:start")
async def cb_start(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="spotify:info")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="mini_shop")],
    ])
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id, text=_TXT_DISCLAIMER,
        reply_markup=kb, parse_mode="HTML",
    )


@spotify_purchase_router.callback_query(F.data == "spotify:info")
async def cb_info(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 Выбрать тариф", callback_data="spotify:plans")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="spotify:start")],
    ])
    try:
        await callback.message.edit_text(_TXT_INFO, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.bot.send_message(
            chat_id=callback.from_user.id, text=_TXT_INFO,
            reply_markup=kb, parse_mode="HTML",
        )


# ── Choose plan (individual / duo) ─────────────────────────────────────

@spotify_purchase_router.callback_query(F.data == "spotify:plans")
async def cb_plans(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_PLANS["ind"]["label"], callback_data="spotify:plan:ind")],
        [InlineKeyboardButton(text=_PLANS["duo"]["label"], callback_data="spotify:plan:duo")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="spotify:info")],
    ])
    text = (
        f"{_TXT_PLAN_TITLE}\n\n"
        f"<b>{_PLANS['ind']['label']}</b>\n{_PLANS['ind']['short']}\n\n"
        f"<b>{_PLANS['duo']['label']}</b>\n{_PLANS['duo']['short']}"
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.bot.send_message(
            chat_id=callback.from_user.id, text=text,
            reply_markup=kb, parse_mode="HTML",
        )


# ── Choose duration ────────────────────────────────────────────────────

@spotify_purchase_router.callback_query(F.data.startswith("spotify:plan:"))
async def cb_choose_plan(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    plan = callback.data.split(":")[2]
    if plan not in _PLANS:
        return
    await state.update_data(spotify_plan=plan)
    meta = _plan_meta(plan)
    rows = []
    row: list[InlineKeyboardButton] = []
    for months, price in sorted(meta["prices"].items()):
        row.append(InlineKeyboardButton(
            text=f"{_duration_label(months)} — {price}₽",
            callback_data=f"spotify:dur:{plan}:{months}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="spotify:plans")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    text = (
        f"<b>{meta['label']}</b>\n\n"
        f"{meta['short']}\n\n"
        "Выберите срок подписки:"
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.bot.send_message(
            chat_id=callback.from_user.id, text=text,
            reply_markup=kb, parse_mode="HTML",
        )


# ── Choose duration → prompt email ─────────────────────────────────────

@spotify_purchase_router.callback_query(F.data.startswith("spotify:dur:"))
async def cb_choose_duration(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    plan = parts[2]
    try:
        months = int(parts[3])
    except ValueError:
        return
    price = _price_rub(plan, months)
    if price is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    await state.update_data(
        spotify_plan=plan, spotify_months=months, spotify_price=price,
    )
    await state.set_state(SpotifyPurchaseState.waiting_for_email)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔙 Изменить тариф", callback_data=f"spotify:plan:{plan}",
        )],
    ])
    try:
        await callback.message.edit_text(
            _TXT_EMAIL_PROMPT, reply_markup=kb, parse_mode="HTML",
        )
    except Exception:
        await callback.bot.send_message(
            chat_id=callback.from_user.id, text=_TXT_EMAIL_PROMPT,
            reply_markup=kb, parse_mode="HTML",
        )


# ── Email input → confirmation ─────────────────────────────────────────

@spotify_purchase_router.message(
    StateFilter(SpotifyPurchaseState.waiting_for_email), F.text,
)
async def msg_email(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена", "cancel"):
        await state.clear()
        await message.answer("❌ Заказ отменён.")
        return
    if not _valid_email(text):
        await message.answer(
            "❌ Не похоже на email. Пример: <code>user@gmail.com</code>",
            parse_mode="HTML",
        )
        return
    await state.update_data(spotify_email=text)
    await state.set_state(SpotifyPurchaseState.confirming_email)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Всё верно, дальше",
            callback_data="spotify:email:ok",
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить email",
            callback_data="spotify:email:edit",
        )],
    ])
    await message.answer(
        f"📧 Ваш email: <code>{text}</code>\n\n"
        "Проверьте его внимательно — ошибка приведёт к невозможности активации.",
        reply_markup=kb, parse_mode="HTML",
    )


@spotify_purchase_router.callback_query(F.data == "spotify:email:edit")
async def cb_email_edit(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    await state.set_state(SpotifyPurchaseState.waiting_for_email)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="✏️ Пришлите email заново.",
    )


@spotify_purchase_router.callback_query(F.data == "spotify:email:ok")
async def cb_email_ok(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    await state.set_state(SpotifyPurchaseState.waiting_for_password)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id, text=_TXT_PASSWORD_PROMPT,
        parse_mode="HTML",
    )


# ── Password input → confirmation ──────────────────────────────────────

@spotify_purchase_router.message(
    StateFilter(SpotifyPurchaseState.waiting_for_password), F.text,
)
async def msg_password(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена", "cancel"):
        await state.clear()
        await message.answer("❌ Заказ отменён.")
        return
    if not _valid_password(text):
        await message.answer(
            "❌ Пароль должен быть от 8 до 128 символов. Пришлите ещё раз.",
        )
        return
    await state.update_data(spotify_password=text)
    await state.set_state(SpotifyPurchaseState.confirming_password)

    # Удаляем сообщение с паролем сразу — оно не должно висеть в чате.
    try:
        await message.delete()
    except Exception:
        pass

    masked = _mask_password(text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Всё верно, к оплате",
            callback_data="spotify:pass:ok",
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить пароль",
            callback_data="spotify:pass:edit",
        )],
    ])
    await message.answer(
        f"🔒 Пароль (замаскирован): <code>{masked}</code>\n"
        f"Длина: {len(text)} символов.\n\n"
        "Проверьте, что вводили корректно.",
        reply_markup=kb, parse_mode="HTML",
    )


@spotify_purchase_router.callback_query(F.data == "spotify:pass:edit")
async def cb_pass_edit(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    await state.set_state(SpotifyPurchaseState.waiting_for_password)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="✏️ Пришлите пароль заново.",
    )


# ── Order review ───────────────────────────────────────────────────────

@spotify_purchase_router.callback_query(F.data == "spotify:pass:ok")
async def cb_pass_ok(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    data = await state.get_data()
    plan = str(data.get("spotify_plan") or "")
    months = int(data.get("spotify_months") or 0)
    email = str(data.get("spotify_email") or "")
    password = str(data.get("spotify_password") or "")
    price = int(data.get("spotify_price") or 0)
    if not (plan and months and email and password and price):
        await state.clear()
        await callback.message.answer("⚠️ Сессия устарела — начните заново.")
        return
    await state.set_state(SpotifyPurchaseState.reviewing)

    meta = _plan_meta(plan)
    text = (
        "🧾 <b>Проверьте заказ Spotify</b>\n\n"
        f"<b>Тариф:</b> {meta['label']}\n"
        f"<b>Срок:</b> {_duration_label(months)}\n"
        f"<b>Email:</b> <code>{email}</code>\n"
        f"<b>Пароль:</b> <code>{_mask_password(password)}</code> "
        f"({len(password)} симв.)\n\n"
        f"<b>К оплате:</b> {price}₽\n\n"
        "<blockquote>После оплаты данные уйдут оператору. Активация — "
        "в течение 15–60 минут (10:00–00:00 МСК).</blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 К способам оплаты",
            callback_data="spotify:pay",
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить пароль",
            callback_data="spotify:pass:edit",
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить email",
            callback_data="spotify:email:edit",
        )],
        [InlineKeyboardButton(
            text="🔙 Начать заново",
            callback_data="spotify:start",
        )],
    ])
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Payment method selection ───────────────────────────────────────────

@spotify_purchase_router.callback_query(F.data == "spotify:pay")
async def cb_payment_methods(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    data = await state.get_data()
    plan = str(data.get("spotify_plan") or "")
    months = int(data.get("spotify_months") or 0)
    price = int(data.get("spotify_price") or 0)
    if not (plan and months and price):
        await callback.message.answer("⚠️ Сессия устарела — начните заново.")
        await state.clear()
        return
    await state.set_state(SpotifyPurchaseState.choose_payment_method)

    rows = []
    if config.TG_PROVIDER_TOKEN:
        rows.append([InlineKeyboardButton(
            text="💳 Банковская карта",
            callback_data=f"spotify_pay:card:{plan}:{months}",
        )])
    rows.append([InlineKeyboardButton(
        text="📱 СБП 3%",
        callback_data=f"spotify_pay:lava:{plan}:{months}",
    )])
    rows.append([InlineKeyboardButton(
        text="📱 СБП",
        callback_data=f"spotify_pay:sbp:{plan}:{months}",
    )])
    # Wata — admin-only beta
    try:
        import wata_service
        if wata_service.is_visible_to(callback.from_user.id):
            rows.append([InlineKeyboardButton(
                text="💳 Wata (тест)",
                callback_data=f"spotify_pay:wata:{plan}:{months}",
            )])
    except Exception:
        pass
    rows.append([InlineKeyboardButton(
        text="🔙 К заказу",
        callback_data="spotify:pass:ok",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    meta = _plan_meta(plan)
    text = (
        "💳 <b>Выберите способ оплаты</b>\n\n"
        f"{meta['label']} · {_duration_label(months)}\n"
        f"К оплате: <b>{price}₽</b>"
    )
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Invoice creation dispatch ──────────────────────────────────────────

async def _create_pending(
    telegram_id: int, plan: str, months: int,
    price_kopecks: int, email: str, password: str,
) -> str:
    """Создаёт pending_purchase, кладёт email в country + пароль в promo_code.
    Такой же приём используется в Steam (login → country)."""
    tariff = f"spotify_{plan}_{months}"
    return await database.create_pending_purchase(
        telegram_id=telegram_id,
        tariff=tariff,
        period_days=months * 30,
        price_kopecks=price_kopecks,
        purchase_type="spotify",
        country=email,          # email хранится тут
        promo_code=password,    # пароль хранится тут
    )


async def _get_flow_data(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan = str(data.get("spotify_plan") or "")
    months = int(data.get("spotify_months") or 0)
    price = int(data.get("spotify_price") or 0)
    email = str(data.get("spotify_email") or "")
    password = str(data.get("spotify_password") or "")
    if not (plan and months and price and email and password):
        await callback.message.answer("⚠️ Сессия устарела — начните заново.")
        await state.clear()
        return None
    return plan, months, price, email, password


@spotify_purchase_router.callback_query(F.data.startswith("spotify_pay:card:"))
async def cb_pay_card(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    flow = await _get_flow_data(callback, state)
    if not flow:
        return
    plan, months, price, email, password = flow
    telegram_id = callback.from_user.id
    price_kopecks = price * 100

    if not config.TG_PROVIDER_TOKEN:
        await callback.answer("Карта временно недоступна", show_alert=True)
        return
    if price_kopecks < 6400:
        await callback.answer("Сумма ниже минимальной для карты (64₽)", show_alert=True)
        return

    purchase_id = await _create_pending(
        telegram_id, plan, months, price_kopecks, email, password,
    )
    label = f"Spotify {_plan_meta(plan)['label']} {_duration_label(months)}"
    try:
        invoice_msg = await callback.bot.send_invoice(
            chat_id=telegram_id,
            title=f"Spotify Premium · {_duration_label(months)}",
            description=f"{label} — активация в течение 15–60 мин.",
            payload=f"purchase:{purchase_id}",
            provider_token=config.TG_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=label, amount=price_kopecks)],
        )

        async def _del(bot, cid, msg):
            try:
                await asyncio.sleep(15 * 60)
                await bot.delete_message(chat_id=cid, message_id=msg.message_id)
            except Exception:
                pass
        asyncio.create_task(_del(callback.bot, telegram_id, invoice_msg))
    except Exception as e:
        logger.exception("SPOTIFY_CARD_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Ошибка создания платежа", show_alert=True)


@spotify_purchase_router.callback_query(F.data.startswith("spotify_pay:lava:"))
async def cb_pay_lava(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    flow = await _get_flow_data(callback, state)
    if not flow:
        return
    plan, months, price, email, password = flow
    telegram_id = callback.from_user.id
    price_kopecks = price * 100

    import lava_service
    if not lava_service.is_enabled():
        await callback.answer("Оплата картой временно недоступна", show_alert=True)
        return

    purchase_id = await _create_pending(
        telegram_id, plan, months, price_kopecks, email, password,
    )
    label = f"Spotify {_plan_meta(plan)['label']} {_duration_label(months)}"
    try:
        invoice_data = await lava_service.create_invoice(
            amount_rubles=float(price),
            purchase_id=purchase_id,
            comment=label,
        )
        try:
            await database.update_pending_purchase_invoice_id(
                purchase_id, str(invoice_data["invoice_id"]),
            )
        except Exception:
            pass
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=invoice_data["payment_url"])],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="mini_shop")],
        ])
        await callback.message.answer(
            f"💳 Счёт создан: <b>{price}₽</b>\n\nОплатите по кнопке ниже.",
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("SPOTIFY_LAVA_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Ошибка создания счёта", show_alert=True)


@spotify_purchase_router.callback_query(F.data.startswith("spotify_pay:wata:"))
async def cb_pay_wata(callback: CallbackQuery, state: FSMContext):
    """Spotify — Wata (admin-only beta)."""
    try:
        await callback.answer()
    except Exception:
        pass
    telegram_id = callback.from_user.id
    import wata_service
    if not wata_service.is_visible_to(telegram_id):
        await callback.answer("Wata пока в закрытой бете", show_alert=True)
        return
    flow = await _get_flow_data(callback, state)
    if not flow:
        return
    plan, months, price, email, password = flow
    price_kopecks = price * 100
    purchase_id = await _create_pending(
        telegram_id, plan, months, price_kopecks, email, password,
    )
    label = f"Spotify {_plan_meta(plan)['label']} {_duration_label(months)}"
    try:
        invoice = await wata_service.create_invoice(
            amount_rubles=float(price),
            purchase_id=purchase_id,
            comment=label,
            user_id=telegram_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(
                purchase_id, str(invoice["invoice_id"]),
            )
        except Exception:
            pass
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=invoice["payment_url"])],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="mini_shop")],
        ])
        await callback.message.answer(
            f"💳 <b>Wata (тест)</b> · {label}\nК оплате: <b>{price}₽</b>",
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("SPOTIFY_WATA_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Ошибка создания счёта Wata", show_alert=True)


@spotify_purchase_router.callback_query(F.data.startswith("spotify_pay:sbp:"))
async def cb_pay_sbp(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception:
        pass
    flow = await _get_flow_data(callback, state)
    if not flow:
        return
    plan, months, price, email, password = flow
    telegram_id = callback.from_user.id
    price_kopecks = price * 100

    import platega_service
    if not platega_service.is_enabled():
        await callback.answer("СБП временно недоступен", show_alert=True)
        return

    try:
        sbp_price_kopecks = platega_service.apply_sbp_markup(price_kopecks)
        sbp_price_rubles = sbp_price_kopecks / 100.0

        purchase_id = await _create_pending(
            telegram_id, plan, months, sbp_price_kopecks, email, password,
        )
        tx = await platega_service.create_transaction(
            amount_rubles=sbp_price_rubles,
            description=f"Spotify {_plan_meta(plan)['label']} {_duration_label(months)}",
            purchase_id=purchase_id,
        )
        try:
            await database.update_pending_purchase_invoice_id(
                purchase_id, str(tx["transaction_id"]),
            )
        except Exception:
            pass
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"📱 Оплатить {sbp_price_rubles:.0f}₽",
                url=tx["redirect_url"],
            )],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="mini_shop")],
        ])
        await callback.message.answer(
            f"📱 <b>СБП</b>\nК оплате: <b>{sbp_price_rubles:.0f}₽</b> "
            "(включена комиссия 3%).",
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("SPOTIFY_SBP_ERROR user=%s: %s", telegram_id, e)
        await callback.answer("Ошибка создания платежа СБП", show_alert=True)


# ── Post-payment: user + admin notifications ───────────────────────────

async def send_spotify_success(
    bot: Bot, telegram_id: int, purchase_id: str,
    purchase: Optional[dict] = None,
):
    """Вызывается из confirmation.py после webhook об успешной оплате.
    Юзеру — «ожидайте активацию», админу — весь заказ + кнопка «Выполнено»."""
    if not purchase:
        purchase = await database.get_pending_purchase_by_id(purchase_id)
    if not purchase:
        logger.error("SPOTIFY_SUCCESS_NO_PURCHASE purchase_id=%s", purchase_id)
        return

    tariff = str(purchase.get("tariff") or "")
    parts = tariff.split("_")
    plan = parts[1] if len(parts) >= 3 else "ind"
    try:
        months = int(parts[2]) if len(parts) >= 3 else 0
    except ValueError:
        months = 0
    email = str(purchase.get("country") or "—")
    password = str(purchase.get("promo_code") or "—")
    price_kopecks = int(purchase.get("price_kopecks") or 0)
    price_rub = price_kopecks // 100
    meta = _plan_meta(plan)

    # User: waiting screen
    user_text = (
        "✅ <b>Оплата прошла успешно!</b>\n\n"
        f"🎧 Spotify Premium · {meta['label']}\n"
        f"⏳ Срок: <b>{_duration_label(months)}</b>\n"
        f"📧 Аккаунт: <code>{email}</code>\n\n"
        "<blockquote>⏳ Доставка заказа — в течение <b>15–60 минут</b> "
        "(рабочие часы: 10:00–00:00 МСК). "
        "Как только оператор активирует Premium — придёт уведомление.</blockquote>\n\n"
        "Если что-то пошло не так — пишите в поддержку."
    )
    user_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="🔙 В магазин", callback_data="mini_shop")],
    ])
    try:
        await bot.send_message(
            telegram_id, user_text, reply_markup=user_kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.error("SPOTIFY_USER_NOTIFY_FAILED user=%s: %s", telegram_id, e)

    # Admin: full order dump + «Выполнено» button
    try:
        user = await database.get_user(telegram_id)
        buyer_username = f"@{user['username']}" if user and user.get("username") else "—"
    except Exception:
        buyer_username = "—"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    from html import escape as _e
    admin_text = (
        "🎧 <b>ПОКУПКА SPOTIFY PREMIUM</b>\n\n"
        f"👤 Покупатель: <code>{telegram_id}</code>\n"
        f"📛 Username: {buyer_username}\n"
        f"🎧 Тариф: {meta['label']}\n"
        f"⏳ Срок: {_duration_label(months)}\n"
        f"💰 Оплата: {price_rub}₽\n"
        f"🕐 Дата: {now_str}\n\n"
        f"📧 <b>Email:</b> <code>{_e(email)}</code>\n"
        f"🔒 <b>Пароль:</b> <code>{_e(password)}</code>\n\n"
        "После активации Premium нажмите <b>«Выполнено»</b> — юзер получит "
        "уведомление."
    )
    try:
        from app.handlers.admin.spotify_delivery import build_spotify_admin_keyboard
        admin_kb = build_spotify_admin_keyboard(telegram_id)
        await bot.send_message(
            config.ADMIN_TELEGRAM_ID, admin_text,
            reply_markup=admin_kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("SPOTIFY_ADMIN_NOTIFY_FAILED user=%s: %s", telegram_id, e)
