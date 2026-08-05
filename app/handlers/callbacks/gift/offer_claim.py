"""Кнопка «Купить со скидкой 20%» из уведомления о подарке.

ЧТО ЗДЕСЬ
    Один обработчик: выдаёт персональную скидку 20% на три дня и
    открывает главное меню, где скидка применится сама.

ПОЧЕМУ ВЫДЕЛЕНО
    К самим подарочным подпискам отношения не имеет — это CTA под
    админским уведомлением (app/handlers/admin/bonus.py::_gift_keyboard).
    Лежал в одном файле с мастером покупки просто по соседству темы.

ЧТО ЛЕГКО СЛОМАТЬ
    Повторные нажатия должны быть безвредны: если у человека уже есть
    действующая скидка, её НЕ перезаписывают — иначе более выгодная
    скидка молча сменится на 20%, и человек заплатит больше.
"""
import logging
from datetime import datetime, timedelta, timezone

import config
import database
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

router = Router()
logger = logging.getLogger(__name__)


_GIFT_OFFER_PERCENT = 20
_GIFT_OFFER_DAYS = 3


@router.callback_query(F.data == "gift_offer:claim")
async def callback_gift_offer_claim(callback: CallbackQuery, state: FSMContext):
    """Activate the 20% personal discount tied to a gift notification."""
    telegram_id = callback.from_user.id

    try:
        await callback.answer()
    except Exception:
        pass

    # If the user already has an active personal discount, don't downgrade
    # them or shorten an existing offer — just remind that it's already on.
    try:
        existing = await database.get_user_discount(telegram_id)
    except Exception as e:
        logger.exception("GIFT_OFFER_DISCOUNT_LOOKUP_FAIL user=%s err=%s", telegram_id, e)
        existing = None

    if existing:
        existing_percent = int(existing.get("discount_percent") or 0)
        existing_expires = existing.get("expires_at")
        if existing_expires:
            try:
                if existing_expires.tzinfo is None:
                    existing_expires = existing_expires.replace(tzinfo=timezone.utc)
                hours = max(0, int((existing_expires - datetime.now(timezone.utc)).total_seconds() // 3600))
                tail = f" (осталось ≈{hours} ч)"
            except Exception:
                tail = ""
        else:
            tail = " (без срока)"
        await callback.answer(
            f"У вас уже активна персональная скидка {existing_percent}%{tail}. "
            "Можно сразу выбрать тариф.",
            show_alert=True,
        )
        return

    # Fresh activation: 20% for 3 days.
    expires_at = datetime.now(timezone.utc) + timedelta(days=_GIFT_OFFER_DAYS)
    try:
        ok = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=_GIFT_OFFER_PERCENT,
            expires_at=expires_at,
            created_by=config.ADMIN_TELEGRAM_ID,
        )
    except Exception as e:
        logger.exception("GIFT_OFFER_DISCOUNT_CREATE_FAIL user=%s err=%s", telegram_id, e)
        ok = False

    if not ok:
        await callback.answer(
            "Не удалось активировать скидку. Попробуйте позже или напишите в поддержку.",
            show_alert=True,
        )
        return

    logger.info(
        "GIFT_OFFER_DISCOUNT_ACTIVATED user=%s percent=%s days=%s expires_at=%s",
        telegram_id, _GIFT_OFFER_PERCENT, _GIFT_OFFER_DAYS, expires_at.isoformat(),
    )

    text = (
        f"🎉 <b>Скидка {_GIFT_OFFER_PERCENT}% активирована!</b>\n\n"
        f"Действует {_GIFT_OFFER_DAYS} дня — успейте оформить подписку.\n"
        "Скидка применится автоматически при оплате."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Выбрать тариф", callback_data="menu_main")],
    ])
    try:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.warning("GIFT_OFFER_ACK_SEND_FAIL user=%s err=%s", telegram_id, e)
