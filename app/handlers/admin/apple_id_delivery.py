"""
Apple ID key-delivery flow.

После оплаты Apple ID-пополнения бот шлёт админу нотификацию с
кнопкой «🔑 Отправить ключ». По клику:

    1. Бот просит админа отправить ключ следующим сообщением.
    2. Админ шлёт текст ключа.
    3. Бот собирает готовое сообщение по шаблону
       (флаг + номинал + ключ + ссылка на инструкцию) и показывает
       админу превью с кнопками «Подтвердить» / «Изменить» / «Отмена».
    4. Подтвердить → бот шлёт готовое сообщение пользователю.
       Изменить → админ вводит полностью свой текст, тот и уйдёт юзеру.

FSM хранит buyer_id/region/nominal в state.data, поэтому несколько
параллельных сессий у одного админа невозможны (aiogram FSM
привязан к user+chat) — что нам как раз и нужно.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

import config

apple_id_delivery_router = Router()
logger = logging.getLogger(__name__)


class AdminAppleKey(StatesGroup):
    waiting_for_key = State()
    waiting_for_edit = State()


# Флаг для шаблона (без страны — страна уже читается по флагу).
_FLAG_BY_REGION = {"usa": "🇺🇸", "turkey": "🇹🇷"}
# Как показать валюту в сообщении для юзера. TRY читаемее чем TL.
_CURRENCY_LABEL = {"usa": "$", "turkey": "TRY"}
# Полное человеко-читаемое название региона (для страховки в превью).
_REGION_LABEL = {"usa": "USA", "turkey": "Turkey"}

_APPLE_INSTRUCTION_URL = "https://support.apple.com/en-us/118242"


def _admin_only(callback: CallbackQuery) -> bool:
    """Позволяем работать только конфигурному ADMIN_TELEGRAM_ID.
    Callback-data admin_apple_key намеренно не раскрывает никаких
    приватных данных, но лишний слой не помешает."""
    return callback.from_user and callback.from_user.id == config.ADMIN_TELEGRAM_ID


def _fmt_nominal(region: str, nominal: int) -> str:
    """5$ / 500TRY — в зависимости от региона."""
    cur = _CURRENCY_LABEL.get(region, "$")
    if cur == "$":
        return f"{nominal}$"
    return f"{nominal} {cur}"


def _build_user_text(region: str, nominal: int, key: str) -> str:
    """HTML-шаблон готового сообщения для пользователя.

    <b>{флаг} {номинал}</b> идёт жирным акцентом.
    <code>{ключ}</code> — моноширинный, tap-to-copy в Telegram.
    """
    flag = _FLAG_BY_REGION.get(region, "🌐")
    nominal_str = _fmt_nominal(region, nominal)
    key_escaped = escape(key.strip())
    return (
        "Здравствуйте!\n\n"
        f"Ваш код пополнения Apple ID <b>{flag} {nominal_str}</b>:\n\n"
        f"<code>{key_escaped}</code>\n\n"
        "<b>Инструкция использования:</b>\n"
        f"{_APPLE_INSTRUCTION_URL}"
    )


def build_apple_admin_keyboard(
    buyer_id: int, region: str, nominal: int,
) -> InlineKeyboardMarkup:
    """Клавиатура под admin-нотификацией об Apple ID покупке.
    Callback-data: apple_send_key:{buyer_id}:{region}:{nominal}."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔑 Отправить ключ",
            callback_data=f"apple_send_key:{buyer_id}:{region}:{nominal}",
        )],
        [InlineKeyboardButton(
            text="💬 Написать пользователю",
            callback_data="admin:chat",
        )],
    ])


# ─── Шаг 1: админ жмёт «Отправить ключ» ─────────────────────────────────

@apple_id_delivery_router.callback_query(F.data.startswith("apple_send_key:"))
async def cb_start_key_entry(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback):
        try:
            await callback.answer("Только для админа", show_alert=True)
        except Exception:
            pass
        return
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    try:
        buyer_id = int(parts[1])
        region = parts[2]
        nominal = int(parts[3])
    except ValueError:
        return

    await state.set_state(AdminAppleKey.waiting_for_key)
    await state.update_data(
        buyer_id=buyer_id, region=region, nominal=nominal,
    )
    flag = _FLAG_BY_REGION.get(region, "🌐")
    nominal_str = _fmt_nominal(region, nominal)
    try:
        await callback.message.answer(
            f"🔑 Пришли ключ для покупки {flag} <b>{nominal_str}</b>\n"
            f"Юзер: <code>{buyer_id}</code>\n\n"
            "Просто отправь текст ключа следующим сообщением. "
            "Можно отменить: /cancel",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("APPLE_KEY_PROMPT_FAILED: %s", e)


# ─── Шаг 2: админ прислал ключ → собираем превью ────────────────────────

@apple_id_delivery_router.message(
    StateFilter(AdminAppleKey.waiting_for_key), F.text,
)
async def msg_receive_key(message: Message, state: FSMContext):
    if message.from_user and message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return  # чужие сообщения игнор — FSM не должен применяться
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена", "cancel"):
        await state.clear()
        await message.answer("❌ Отменено.")
        return
    if not text:
        await message.answer("Пустое сообщение — пришли текст ключа.")
        return
    if len(text) > 400:
        await message.answer(
            "Ключ подозрительно длинный (>400 символов). Пришли ещё раз "
            "или /cancel."
        )
        return

    data = await state.get_data()
    buyer_id = int(data.get("buyer_id") or 0)
    region = str(data.get("region") or "usa")
    nominal = int(data.get("nominal") or 0)
    if not buyer_id:
        await state.clear()
        await message.answer("⚠️ Потерян контекст покупки. Начни заново.")
        return

    preview = _build_user_text(region, nominal, text)
    await state.update_data(prepared_text=preview)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подтвердить и отправить",
            callback_data=f"apple_key_confirm:{buyer_id}",
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить сообщение",
            callback_data=f"apple_key_edit:{buyer_id}",
        )],
        [InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="apple_key_cancel",
        )],
    ])
    header = (
        f"🧾 <b>Превью сообщения юзеру</b> "
        f"<code>{buyer_id}</code> "
        f"({_REGION_LABEL.get(region, region)} · {_fmt_nominal(region, nominal)}):\n\n"
        "———\n\n"
    )
    await message.answer(header + preview, parse_mode="HTML")
    await message.answer(
        "Так и отправить пользователю?",
        reply_markup=kb,
    )


# ─── Шаг 3a: подтвердить → шлём юзеру ────────────────────────────────────

@apple_id_delivery_router.callback_query(F.data.startswith("apple_key_confirm:"))
async def cb_confirm(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback):
        try:
            await callback.answer("Только для админа", show_alert=True)
        except Exception:
            pass
        return
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    try:
        buyer_id = int(parts[1])
    except ValueError:
        return

    data = await state.get_data()
    text = str(data.get("prepared_text") or "").strip()
    if not text:
        await callback.message.answer(
            "⚠️ Нет подготовленного текста. Нажми «Отправить ключ» заново."
        )
        await state.clear()
        return

    try:
        await callback.bot.send_message(
            chat_id=buyer_id, text=text, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.exception("APPLE_KEY_SEND_TO_USER_FAILED buyer=%s: %s", buyer_id, e)
        await callback.message.answer(
            f"❌ Не удалось отправить пользователю <code>{buyer_id}</code>: "
            f"{escape(str(e))}",
            parse_mode="HTML",
        )
        return

    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"✅ Ключ отправлен пользователю <code>{buyer_id}</code>.",
        parse_mode="HTML",
    )


# ─── Шаг 3b: изменить → админ пришлёт свой текст ─────────────────────────

@apple_id_delivery_router.callback_query(F.data.startswith("apple_key_edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback):
        try:
            await callback.answer("Только для админа", show_alert=True)
        except Exception:
            pass
        return
    try:
        await callback.answer()
    except Exception:
        pass

    parts = callback.data.split(":")
    if len(parts) != 2:
        return
    try:
        buyer_id = int(parts[1])
    except ValueError:
        return

    await state.set_state(AdminAppleKey.waiting_for_edit)
    await state.update_data(buyer_id=buyer_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "✏️ Пришли полный текст сообщения для пользователя.\n"
        "Можно с HTML-разметкой (&lt;b&gt;, &lt;code&gt;, &lt;i&gt;).\n"
        "Отменить: /cancel",
        parse_mode="HTML",
    )


@apple_id_delivery_router.message(
    StateFilter(AdminAppleKey.waiting_for_edit), F.text,
)
async def msg_receive_edit(message: Message, state: FSMContext):
    if message.from_user and message.from_user.id != config.ADMIN_TELEGRAM_ID:
        return
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена", "cancel"):
        await state.clear()
        await message.answer("❌ Отменено.")
        return
    if not text:
        await message.answer("Пустое сообщение — пришли текст или /cancel.")
        return
    if len(text) > 4000:
        await message.answer("Сообщение слишком длинное (>4000). Сократи.")
        return

    data = await state.get_data()
    buyer_id = int(data.get("buyer_id") or 0)
    if not buyer_id:
        await state.clear()
        await message.answer("⚠️ Потерян контекст покупки.")
        return

    await state.update_data(prepared_text=text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подтвердить и отправить",
            callback_data=f"apple_key_confirm:{buyer_id}",
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить ещё раз",
            callback_data=f"apple_key_edit:{buyer_id}",
        )],
        [InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="apple_key_cancel",
        )],
    ])
    await message.answer(
        f"🧾 <b>Обновлённое превью</b> для <code>{buyer_id}</code>:\n\n"
        "———\n\n" + text,
        parse_mode="HTML",
    )
    await message.answer("Отправить пользователю?", reply_markup=kb)


# ─── Отмена ─────────────────────────────────────────────────────────────

@apple_id_delivery_router.callback_query(F.data == "apple_key_cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    if not _admin_only(callback):
        try:
            await callback.answer("Только для админа", show_alert=True)
        except Exception:
            pass
        return
    await state.clear()
    try:
        await callback.answer("Отменено")
    except Exception:
        pass
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await callback.message.answer("❌ Доставка ключа отменена.")
    except Exception:
        pass
