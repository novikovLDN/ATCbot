"""
Spotify delivery flow (admin side).

После оплаты Spotify-подписки бот шлёт админу нотификацию с
полными данными заказа (тариф/срок/email/пароль/цена) и кнопкой
«✅ Выполнено». По клику:

    - юзеру приходит уведомление «подписка выдана, проверьте аккаунт»
      с кнопкой поддержки на случай проблем;
    - у админа сообщение помечается (клавиатура снимается) — двойная
      выдача исключена.

Модуль не хранит состояния — вся информация уже в pending_purchase
и передаётся через callback_data.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
)

import config

spotify_delivery_router = Router()
logger = logging.getLogger(__name__)

SUPPORT_URL = "https://t.me/atlas_suppbot"


def _admin_only(callback: CallbackQuery) -> bool:
    return callback.from_user and callback.from_user.id == config.ADMIN_TELEGRAM_ID


def build_spotify_admin_keyboard(buyer_id: int) -> InlineKeyboardMarkup:
    """Клавиатура под admin-нотификацией об оплате Spotify."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Выполнено — уведомить юзера",
            callback_data=f"spotify_done:{buyer_id}",
        )],
        [InlineKeyboardButton(
            text="💬 Написать пользователю",
            callback_data="admin:chat",
        )],
    ])


@spotify_delivery_router.callback_query(F.data.startswith("spotify_done:"))
async def cb_spotify_done(callback: CallbackQuery):
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

    user_text = (
        "🎉 <b>Ваш заказ Spotify выполнен!</b>\n\n"
        "Подписка активирована на указанный вами аккаунт. "
        "Проверьте, пожалуйста, статус Premium в приложении.\n\n"
        "<blockquote>Если что-то пошло не так — нажмите «Поддержка», "
        "мы быстро разберёмся.</blockquote>"
    )
    user_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_URL)],
        [InlineKeyboardButton(text="🔙 В магазин", callback_data="mini_shop")],
    ])
    try:
        await callback.bot.send_message(
            chat_id=buyer_id, text=user_text,
            reply_markup=user_kb, parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("SPOTIFY_DONE_NOTIFY_FAILED buyer=%s: %s", buyer_id, e)
        try:
            await callback.message.answer(
                f"❌ Не удалось уведомить <code>{buyer_id}</code>: "
                f"{escape(str(e))}",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await callback.message.answer(
            f"✅ Юзеру <code>{buyer_id}</code> отправлено уведомление о выполнении.",
            parse_mode="HTML",
        )
    except Exception:
        pass
