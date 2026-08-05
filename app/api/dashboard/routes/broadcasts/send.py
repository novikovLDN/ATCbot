"""Рассылки: всё, что реально уходит людям.

ЧТО ЗДЕСЬ
    Модель запроса и валидация, тестовая отправка себе, создание рассылки
    с запуском фоновой отправки и удаление уже разосланных сообщений из
    чатов.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственный записывающий кусок рассылок: отсюда сообщения летят
    живым людям и отсюда же их можно стереть. Цена ошибки не та, что у
    читающих эндпоинтов.

ЧТО ЛЕГКО СЛОМАТЬ
    Отправка идёт фоновой задачей — HTTP-ответ возвращается сразу, до
    того как ушло хоть одно сообщение. Дождаться её здесь нельзя: запрос
    провисит десятки минут и отвалится по таймауту.

    `/test-self` намеренно зовёт Bot API напрямую, без безопасной
    обёртки: админу нужна ТОЧНАЯ причина отказа Telegram («can't parse
    entities»), а обёртка глотает ошибку и возвращает None.

    broadcast_id в тестовой отправке — 0, поэтому скидочные кнопки у
    админа не сработают. Это ожидаемо: проверяется рендер, а не флоу.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

import database
from app.api.dashboard.deps import require_admin
from app.api.dashboard.routes.broadcasts.common import _get_bot
from app.api.dashboard.routes.broadcasts.keyboard import (
    _BUTTON_TYPES,
    _build_reply_markup,
    normalize_premium_emoji,
)
from app.events import bus

logger = logging.getLogger(__name__)

router = APIRouter()


_GIFT_REVEAL_PERCENT_CHOICES = (20, 25, 30, 35, 40)


class BroadcastCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    segment: str = Field(..., min_length=1, max_length=60)
    photo_file_id: Optional[str] = Field(None, max_length=300)
    # GIF/MP4 animation — мутуально-эксклюзивно с photo_file_id.
    # Если заданы оба — в бэкенде отдаётся приоритет animation.
    animation_file_id: Optional[str] = Field(None, max_length=300)
    buttons: list[str] = Field(default_factory=list)
    discount_percent: Optional[int] = Field(None, ge=1, le=100)
    discount_hours: Optional[int] = Field(None, gt=0, le=8760)
    discount_label: Optional[str] = Field(None, max_length=60)
    # Процент для кнопки «👀 Посмотреть подарок». Пресеты 20/25/30/35/40.
    # Действует 48ч после клика (продолжительность зашита в коде callback'а).
    gift_reveal_percent: Optional[int] = Field(None, ge=20, le=40)

    @field_validator("buttons")
    @classmethod
    def _valid_buttons(cls, v: list[str]) -> list[str]:
        if not v:
            return v
        for b in v:
            if b not in _BUTTON_TYPES:
                raise ValueError(f"unknown button type: {b}")
        return v

    @field_validator("gift_reveal_percent")
    @classmethod
    def _valid_gift_reveal_percent(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v not in _GIFT_REVEAL_PERCENT_CHOICES:
            raise ValueError(
                f"gift_reveal_percent must be one of "
                f"{_GIFT_REVEAL_PERCENT_CHOICES}, got {v}"
            )
        return v


@router.post("/{broadcast_id}/delete-from-users")
async def broadcast_delete_from_users(
    broadcast_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Start deleting every message of this broadcast from each user's
    chat.

    Background task — returns 202 immediately. Subscribe to
    `broadcast:delete_progress` / `broadcast:delete_done` /
    `broadcast:delete_cancelled` events on the WS for live progress.
    Use POST /broadcasts/{id}/delete-from-users/cancel to stop it
    mid-flight.
    """
    bot = _get_bot()

    from app.services import broadcast_deleter
    if broadcast_deleter.is_running(broadcast_id):
        raise HTTPException(409, "delete_already_running")

    try:
        pairs = await database.get_broadcast_message_ids(broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"fetch_pairs_failed: {e}")
    if not pairs:
        raise HTTPException(
            404, "no_messages_to_delete (broadcast log empty)",
        )

    task = asyncio.create_task(broadcast_deleter.delete_broadcast_from_users(
        bot=bot,
        broadcast_id=broadcast_id,
        admin_telegram_id=int(admin["sub"]),
    ))
    broadcast_deleter.register_task(broadcast_id, task)

    bus.publish({
        "type": "broadcast:delete_started",
        "broadcast_id": broadcast_id,
        "total": len(pairs),
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "broadcast_id": broadcast_id,
        "total_messages": len(pairs),
    }


@router.post("/{broadcast_id}/delete-from-users/cancel")
async def broadcast_delete_cancel(
    broadcast_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Stop an in-progress delete-from-users run. Already-deleted
    messages stay deleted; the rest are left in their original state.
    Publishes broadcast:delete_cancelled."""
    from app.services import broadcast_deleter
    cancelled = broadcast_deleter.cancel_running(broadcast_id)
    if not cancelled:
        raise HTTPException(409, "not_running")
    bus.publish({
        "type": "broadcast:delete_cancelled",
        "broadcast_id": broadcast_id,
        "by": admin.get("sub"),
    })
    return {"ok": True}


@router.post("/test-self")
async def broadcast_test_self(
    body: BroadcastCreateRequest,
    admin: dict = Depends(require_admin),
):
    """Отправить тестовое сообщение ТОЛЬКО админу — для проверки текста,
    разметки, кнопок и фото перед массовой рассылкой.

    Не создаёт row в `broadcasts`, не пишет в `broadcast_send_log`,
    не публикует bus-события. Сегмент игнорируется. Скидка — тоже
    (кнопки строятся, но broadcast_id передаётся как 0, поэтому
    callback на скидочной кнопке у админа просто не сработает — это
    ок для теста, нам важен только рендер).
    """
    bot = _get_bot()
    admin_id = int(admin["sub"])

    message_html = normalize_premium_emoji(body.message)
    reply_markup = _build_reply_markup(
        body.buttons, 0, body.discount_percent,
    )

    # Прямой вызов Bot API — без batch-обёртки, которая глотает
    # Telegram-ошибки и возвращает None. Здесь нам важно показать админу
    # ТОЧНУЮ причину отказа («can't parse entities: …», «message is too
    # long», «PHOTO_INVALID_DIMENSIONS» и т.д.), чтобы он сразу понял,
    # что чинить в разметке.
    #
    # send_with_long_caption_fallback автоматически сплитит на 2
    # сообщения (фото + текст), если caption у фото вылез за 1024
    # символа — иначе длинные тексты с blockquote expandable не
    # помещаются.
    from aiogram.exceptions import (
        TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter,
    )
    from app.utils.telegram_send import send_with_long_caption_fallback

    try:
        message_ids = await send_with_long_caption_fallback(
            bot,
            admin_id,
            message_html,
            photo_file_id=body.photo_file_id,
            animation_file_id=body.animation_file_id,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        raise HTTPException(400, f"Telegram отклонил сообщение: {e.message}")
    except TelegramForbiddenError:
        raise HTTPException(
            403, "Бот заблокирован у админа — разблокируй и попробуй снова",
        )
    except TelegramRetryAfter as e:
        raise HTTPException(429, f"flood_wait: подожди {e.retry_after}с")
    except Exception as e:
        raise HTTPException(500, f"send_failed: {type(e).__name__}: {e}")

    return {
        "ok": True,
        "message_ids": message_ids,
        "split": len(message_ids) > 1,
        "to": admin_id,
    }


# Без декоратора намеренно. Путь этого эндпоинта — пустой (POST на
# /dashboard/api/broadcasts, без хвостового слеша), а FastAPI не даёт
# подключить подроутер, у которого и префикс, и путь маршрута пустые:
# «Prefix and path cannot be both empty». Поэтому маршрут вешается прямо
# на роутер пакета — см. __init__.py, строка router.post("")(...).
# Ставить сюда "/" нельзя: адрес сменится на /broadcasts/, а дашборд шлёт
# POST на /broadcasts, и редирект 307 на POST — отдельная беда.
async def broadcast_create(
    body: BroadcastCreateRequest,
    admin: dict = Depends(require_admin),
):
    bot = _get_bot()

    # Нормализуем premium-эмодзи (Markdown → HTML) — см. normalize_premium_emoji.
    message_html = normalize_premium_emoji(body.message)

    try:
        user_ids = await database.get_users_by_segment(body.segment)
    except Exception as e:
        raise HTTPException(400, f"invalid_segment: {e}")
    if not user_ids:
        raise HTTPException(400, "empty_audience")

    try:
        broadcast_id = await database.create_broadcast(
            title=body.title,
            message=message_html,
            broadcast_type="custom",
            segment=body.segment,
            sent_by=int(admin["sub"]),
            photo_file_id=body.photo_file_id,
            animation_file_id=body.animation_file_id,
            buttons=list(body.buttons) if body.buttons else None,
        )
    except Exception as e:
        raise HTTPException(500, f"create_broadcast_failed: {e}")

    # Discount metadata for promo buttons
    if (
        ("promo_buy" in body.buttons or "promo_traffic" in body.buttons)
        and body.discount_percent
    ):
        try:
            await database.save_broadcast_discount(
                broadcast_id,
                body.discount_percent,
                body.discount_hours or 168,
                body.discount_label or f"{body.discount_hours or 168} часов",
            )
        except Exception as e:
            logger.warning("DISCOUNT_SAVE_FAIL broadcast_id=%s err=%s", broadcast_id, e)

    # gift_reveal-скидка (админ выбрал 20/25/30/35/40 в дашборд-визарде).
    # Отдельная колонка broadcast_discounts.gift_reveal_percent — не
    # конфликтует с promo_buy-скидкой выше. Fallback 20% если админ
    # не выбрал (то же поведение, что было до фичи).
    if "gift_reveal" in body.buttons:
        _gr_pct = body.gift_reveal_percent or 20
        try:
            await database.save_broadcast_gift_reveal_percent(broadcast_id, _gr_pct)
        except Exception as e:
            logger.warning(
                "GIFT_REVEAL_PERSIST_FAIL broadcast_id=%s err=%s "
                "(fallback to 20%% at click-time)",
                broadcast_id, e,
            )

    reply_markup = _build_reply_markup(
        body.buttons, broadcast_id, body.discount_percent,
    )

    # Background task — don't block the HTTP response on the send.
    from app.services.broadcast_sender import send_broadcast
    asyncio.create_task(send_broadcast(
        bot=bot,
        broadcast_id=broadcast_id,
        user_ids=list(user_ids),
        message=message_html,
        reply_markup=reply_markup,
        photo_file_id=body.photo_file_id,
        animation_file_id=body.animation_file_id,
        admin_telegram_id=int(admin["sub"]),
    ))

    bus.publish({
        "type": "broadcast:created",
        "broadcast_id": broadcast_id,
        "audience": len(user_ids),
        "by": admin.get("sub"),
    })

    return {
        "ok": True,
        "broadcast_id": broadcast_id,
        "audience": len(user_ids),
    }
