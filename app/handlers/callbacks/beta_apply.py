"""Обработчик кнопки «🧪 Оставить заявку» из broadcast-рассылки.

Callback data: `beta_apply:{broadcast_id}` (broadcast_id опциональный,
используется для аналитики какая рассылка привела юзера).

Flow:
  1. Проверяем, не заявлялся ли юзер уже (UNIQUE в БД).
  2. Если новая заявка → пишем в beta_applications, удаляем сообщение
     рассылки, отправляем подтверждение.
  3. Если повторный клик → toast «Заявка уже принята», сообщение
     всё равно удаляем чтобы не мешало.

Программа задаётся по callback_data:beta_apply:{broadcast_id}:{program}
(если program не указан, дефолт vpn_innovator).  Это оставляет
возможность добавлять новые beta-программы без ломки существующих.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import database
from database import beta_applications as _beta

logger = logging.getLogger(__name__)

beta_apply_router = Router()


_PROGRAM_LABELS = {
    "vpn_innovator": "VPN-Инноватор",
}


def _program_label(program: str) -> str:
    return _PROGRAM_LABELS.get(program, program)


@beta_apply_router.callback_query(F.data.startswith("beta_apply:"))
async def callback_beta_apply(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    username = callback.from_user.username or None
    first_name = callback.from_user.first_name or None

    # Формат callback: beta_apply:{broadcast_id?}:{program?}
    parts = (callback.data or "").split(":")
    source_broadcast_id: int | None = None
    program = "vpn_innovator"
    if len(parts) >= 2 and parts[1].isdigit():
        source_broadcast_id = int(parts[1])
    if len(parts) >= 3 and parts[2]:
        program = parts[2].strip() or "vpn_innovator"

    program_label = _program_label(program)

    result = await _beta.record_application(
        telegram_id,
        program=program,
        username=username,
        first_name=first_name,
        source_broadcast_id=source_broadcast_id,
    )

    status = result.get("status")
    if status == "error":
        try:
            await callback.answer(
                "Не удалось обработать заявку, попробуйте позже.",
                show_alert=True,
            )
        except Exception:
            pass
        return

    if status == "already_applied":
        try:
            await callback.answer(
                "Заявка уже принята ✅",
                show_alert=False,
            )
        except Exception:
            pass
        # Всё равно чистим сообщение рассылки — юзеру больше не нужно.
        try:
            await callback.message.delete()
        except Exception as e:  # noqa: BLE001
            logger.debug("beta_apply: cleanup old message failed: %s", e)
        return

    # status == "created"
    logger.info(
        "beta_apply_created: tg=%s username=%s program=%s source_broadcast=%s",
        telegram_id, username, program, source_broadcast_id,
    )

    # Удаляем сообщение рассылки, чтобы не оставалось со старой кнопкой.
    try:
        await callback.message.delete()
    except Exception as e:  # noqa: BLE001
        logger.debug("beta_apply: broadcast delete failed: %s", e)

    text = (
        f"✅ <b>Заявка принята</b>\n\n"
        f"Вы подали заявку на участие в бета-тестировании "
        f"<b>{program_label}</b>.\n\n"
        f"Ожидайте — как только запустим, свяжемся первыми."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/atlas_suppbot")],
    ])
    try:
        await callback.bot.send_message(
            chat_id=telegram_id, text=text,
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "beta_apply: confirmation send failed tg=%s err=%s",
            telegram_id, e,
        )
    try:
        await callback.answer("Готово ✅", show_alert=False)
    except Exception:
        pass


__all__ = ["beta_apply_router"]
