"""Broadcasts — history, send stats, audience segments, create+send.

The create endpoint (POST /) accepts a JSON payload describing the
broadcast and:
  1. resolves the segment to a user_id list
  2. creates a broadcasts row via database.create_broadcast
  3. optionally saves broadcast_discount info if a promo button is used
  4. kicks off app.services.broadcast_sender.send_broadcast as a
     background task — does NOT block the HTTP response
  5. publishes broadcast:created on the bus so the dashboard sees the
     new row appear without polling

Photo uploads are handled by POST /upload-photo: the file is sent to
the admin's Telegram chat and the returned file_id is stored on the
client side and POSTed back as part of the broadcast payload.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from app.branding import get_brand
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field, field_validator

import database
from app.api.dashboard.deps import require_admin
from app.api.dashboard.errors import server_error
from app.api.dashboard.idempotency import IdempotentRoute
from app.services.renewal_offer import service as renewal_offer
from app.utils.telegram_html import TEXT_LIMIT, telegram_html_errors
from database import segments as seg
from app.events import bus

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)], route_class=IdempotentRoute)


# ── READ ──────────────────────────────────────────────────────────────


@router.get("/recent")
async def broadcasts_recent(limit: int = Query(20, gt=0, le=500)):
    try:
        rows = await database.get_recent_broadcasts(limit)
    except Exception as e:
        raise server_error("broadcasts_failed") from e
    return [_with_segment_label(_serialize(r)) for r in rows]


# GET /segments counted every segment (full table scans) on every open of the
# broadcast wizard / the notification editor. Counts are cached server-side for
# this long (a failed count is not cached, it is retried on the next open).
SEGMENT_COUNTS_TTL_SECONDS = 60.0
_clock = time.monotonic
_segment_counts: dict = {}          # key → (counted at, count)


def reset_segment_counts_cache() -> None:
    _segment_counts.clear()


async def _segment_count(key: str) -> int:
    """Audience size of a full key (the send list: unreachable users dropped),
    cached per key for SEGMENT_COUNTS_TTL_SECONDS. -1 when the count failed
    (not cached, retried next time)."""
    now = _clock()
    cached = _segment_counts.get(key)
    if cached is not None and now - cached[0] < SEGMENT_COUNTS_TTL_SECONDS:
        return cached[1]
    try:
        n = await database.count_users_by_segment(key)
    except Exception as e:
        logger.warning("SEGMENT_COUNT_FAIL key=%s err=%s", key, e)
        return -1
    _segment_counts[key] = (now, n)
    return n


# /segments counts every listed segment; a few at a time instead of one by one.
_SEGMENT_COUNT_CONCURRENCY = 4


def _require_valid_segment(key: Optional[str]) -> str:
    try:
        return seg.validate_segment_key(key or "")
    except seg.SegmentKeyError as e:
        raise HTTPException(400, f"invalid_segment: {e}")


@router.get("/segments")
async def segments_list():
    """Available segments with current member counts + tooltip descriptions.

    Catalog: database/segments.py. Parametric entries (`parametric: true`)
    carry `default_window` / `direction` / `allow_any` and the count of
    `<key>:<default_window>`; the wizard composes the full key and asks
    GET /segments/count for other windows.
    """
    items = seg.catalog()
    sem = asyncio.Semaphore(_SEGMENT_COUNT_CONCURRENCY)

    async def one(item: dict) -> dict:
        full = f"{item['key']}:{item['default_window']}" if item.get("parametric") else item["key"]
        async with sem:
            count = await _segment_count(full)
        out = dict(item)
        out["count"] = count
        if item.get("parametric"):
            out["default_label"] = seg.segment_label(full)
        return out

    return list(await asyncio.gather(*(one(i) for i in items)))


@router.get("/segments/count")
async def segment_count(key: str = Query(..., min_length=1, max_length=60)):
    """Audience of one full key (`paid_ended:6m`, `trial_ended:any`, a fixed
    key…) + its human label. 400 on an unknown key / bad window."""
    _require_valid_segment(key)
    return {"key": key, "label": seg.segment_label(key), "count": await _segment_count(key)}


class RenewalOfferRequest(BaseModel):
    """Overview quick action «Предложить продление со скидкой»
    (app/services/renewal_offer/service.py). `message` may keep {discount} /
    {hours}; they are substituted here. `confirm` must be true — the sheet
    asks «Отправить N пользователям?» first."""
    message: str = Field(..., min_length=1, max_length=4000)
    discount_percent: int = renewal_offer.DEFAULT_DISCOUNT
    discount_hours: int = renewal_offer.DEFAULT_HOURS
    exclude_auto_renew: bool = True
    confirm: bool = False


@router.get("/renewal-offer")
async def renewal_offer_info():
    """Templates, discount choices and the audience of both variants (with /
    without the auto-renew ones) for the Overview sheet."""
    keys = {"all": renewal_offer.segment_key(False), "manual": renewal_offer.segment_key(True)}
    return {
        "templates": list(renewal_offer.TEMPLATES),
        "discount_choices": list(renewal_offer.DISCOUNT_CHOICES),
        "default_discount": renewal_offer.DEFAULT_DISCOUNT,
        "default_hours": renewal_offer.DEFAULT_HOURS,
        "max_hours": renewal_offer.MAX_HOURS,
        "window": renewal_offer.WINDOW,
        "segments": keys,
        "labels": {k: seg.segment_label(v) for k, v in keys.items()},
        "audience": {k: await _segment_count(v) for k, v in keys.items()},
    }


@router.post("/renewal-offer")
async def renewal_offer_send(
    body: RenewalOfferRequest,
    admin: dict = Depends(require_admin),
):
    """A normal broadcast to active paid subscriptions ending within 7 days
    with the existing promo_buy discount button."""
    err = renewal_offer.validate(body.discount_percent, body.discount_hours)
    if err:
        raise HTTPException(400, err)
    if not body.confirm:
        raise HTTPException(400, "confirm_required")
    message = normalize_premium_emoji(
        renewal_offer.render(body.message, body.discount_percent, body.discount_hours))
    problems = telegram_html_errors(message)
    if problems:
        raise HTTPException(400, f"invalid_html: {problems[0]}")
    if len(message) > TEXT_LIMIT:
        raise HTTPException(400, f"message_too_long: {len(message)} > {TEXT_LIMIT}")
    segment = renewal_offer.segment_key(body.exclude_auto_renew)
    request = BroadcastCreateRequest(
        title=renewal_offer.title(body.discount_percent, body.exclude_auto_renew),
        message=message,
        segment=segment,
        buttons=[renewal_offer.BUTTON],
        discount_percent=body.discount_percent,
        discount_hours=body.discount_hours,
        discount_label=renewal_offer.discount_label(body.discount_hours),
        tag="продление",
        tag_color="orange",
    )
    return await _launch_broadcast(request, admin)


# Declared before /{broadcast_id}: otherwise GET /scheduled matches the
# int path param first and answers 422.
@router.get("/scheduled")
async def broadcast_schedule_list(
    active_only: bool = Query(True),
    limit: int = Query(200, gt=0, le=500),
):
    """Список запланированных задач. active_only=true — только активные,
    active_only=false — вся история (в т.ч. cancelled/completed)."""
    try:
        rows = await database.list_scheduled_broadcasts(
            active_only=active_only, limit=limit,
        )
    except Exception as e:
        raise server_error("scheduled_list_failed") from e
    return [_with_segment_label(_serialize(r)) for r in rows]


@router.get("/scheduled/{sched_id}")
async def broadcast_schedule_get(sched_id: int = Path(..., gt=0)):
    try:
        row = await database.get_scheduled_broadcast(sched_id)
    except Exception as e:
        raise server_error("scheduled_get_failed") from e
    if not row:
        raise HTTPException(404, "scheduled broadcast not found")
    return _with_segment_label(_serialize(row))


@router.delete("/scheduled/{sched_id}")
async def broadcast_schedule_cancel(
    sched_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Отменить запланированное задание. Уже отработавшие запуски
    остаются в истории broadcasts."""
    try:
        ok = await database.cancel_scheduled_broadcast(
            sched_id, cancelled_by=int(admin["sub"]),
        )
    except Exception as e:
        raise server_error("scheduled_cancel_failed") from e
    if not ok:
        raise HTTPException(404, "not found or already inactive")
    bus.publish({
        "type": "broadcast:scheduled_cancelled",
        "sched_id": sched_id,
        "by": admin.get("sub"),
    })
    return {"ok": True}


@router.get("/{broadcast_id}")
async def broadcast_detail(broadcast_id: int = Path(..., gt=0)):
    """Full broadcast row + discount/gift_reveal — используется UI-ом
    «Отправить снова», чтоб предзаполнить визард всеми полями."""
    try:
        row = await database.get_broadcast(broadcast_id)
    except Exception as e:
        raise server_error("broadcast_detail_failed") from e
    if not row:
        raise HTTPException(404, "Broadcast not found")
    out = _with_segment_label(_serialize(row))
    # Присоединяем скидочные поля — они хранятся в broadcast_discounts,
    # а не в broadcasts. Fail-safe: если строки нет — пустые значения.
    try:
        disc = await database.get_broadcast_discount(broadcast_id)
    except Exception as e:
        logger.warning("BROADCAST_DETAIL_DISC_FAIL id=%s err=%s", broadcast_id, e)
        disc = None
    if disc:
        out["discount_percent"] = disc.get("discount_percent")
        out["discount_hours"] = disc.get("discount_hours")
        out["discount_label"] = disc.get("discount_label")
        out["gift_reveal_percent"] = disc.get("gift_reveal_percent")
    else:
        out["discount_percent"] = None
        out["discount_hours"] = None
        out["discount_label"] = None
        out["gift_reveal_percent"] = None
    return out


@router.get("/{broadcast_id}/stats")
async def broadcast_stats(broadcast_id: int = Path(..., gt=0)):
    try:
        stats = await database.get_broadcast_stats(broadcast_id)
    except Exception as e:
        raise server_error("broadcast_stats_failed") from e
    return _serialize(stats or {})


class BroadcastTagPatch(BaseModel):
    """PATCH body для тега рассылки. Пустая строка = снять тег."""
    tag: Optional[str] = Field(None, max_length=40)
    tag_color: Optional[str] = Field(None, max_length=16)

    @field_validator("tag_color")
    @classmethod
    def _valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.lower().strip()
        if v not in _VALID_TAG_COLORS:
            raise ValueError(f"tag_color must be one of {sorted(_VALID_TAG_COLORS)}")
        return v


@router.patch("/{broadcast_id}/tag")
async def broadcast_patch_tag(
    body: BroadcastTagPatch,
    broadcast_id: int = Path(..., gt=0),
):
    """Обновить/снять цветной тег уже существующей рассылки.
    Пустые значения → NULL (снять тег)."""
    try:
        ok = await database.update_broadcast_tag(
            broadcast_id,
            (body.tag or "").strip() or None,
            body.tag_color,
        )
    except Exception as e:
        raise server_error("tag_patch_failed") from e
    if not ok:
        raise HTTPException(404, "broadcast not found or migration 071 pending")
    return {"ok": True, "id": broadcast_id, "tag": body.tag,
            "tag_color": body.tag_color}


@router.get("/{broadcast_id}/analytics")
async def broadcast_analytics(broadcast_id: int = Path(..., gt=0)):
    """Расширенная аналитика рассылки: conversion / revenue / blocked.

    Возвращает счётчики sent/failed/deleted и окна конверсии
    (1д/3д/7д) — уникальные юзеры, купившие после отправки, и
    их суммарный доход.
    """
    try:
        data = await database.get_broadcast_analytics(broadcast_id)
    except Exception as e:
        raise server_error("broadcast_analytics_failed") from e
    return _serialize(data or {})


# ── PHOTO UPLOAD ─────────────────────────────────────────────────────


@router.post("/upload-photo")
async def upload_photo(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    """Echo the photo to the admin's Telegram chat to obtain a Telegram
    file_id, return it for the wizard to embed in the broadcast.

    Telegram requires that ANY file_id used to forward / send a photo
    come from a previous Telegram-side send/upload — there's no way
    to mint a file_id without first calling send_photo. We use the
    admin's own chat as the staging area; the message also serves as a
    visual confirmation that the upload worked."""
    bot = _get_bot()
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty_file")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "file_too_large_max_10MB")

    photo = BufferedInputFile(content, filename=file.filename or "photo.jpg")
    try:
        msg = await bot.send_photo(
            chat_id=int(admin["sub"]),
            photo=photo,
            caption="🖼 Загружено для рассылки",
        )
    except Exception as e:
        raise server_error("upload_to_telegram_failed") from e

    if not msg.photo:
        raise HTTPException(500, "telegram_returned_no_photo")
    return {"file_id": msg.photo[-1].file_id}


@router.post("/upload-animation")
async def upload_animation(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    """Загрузить GIF/MP4-animation → получить Telegram file_id.

    Механика та же что у /upload-photo: bot шлёт файл в чат админа
    как animation, Telegram возвращает file_id, который потом
    используется в broadcast для send_animation.

    Ограничение размера: 20 MB (Telegram Bot API лимит на animation).
    Accept: image/gif, video/mp4.
    """
    bot = _get_bot()
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty_file")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "file_too_large_max_20MB")

    filename = (file.filename or "animation.gif").lower()
    if not (filename.endswith(".gif") or filename.endswith(".mp4")):
        # По content-type тоже проверим на всякий случай
        ct = (file.content_type or "").lower()
        if ct not in ("image/gif", "video/mp4"):
            raise HTTPException(
                400, "only .gif or .mp4 accepted"
            )

    animation = BufferedInputFile(
        content, filename=file.filename or "animation.gif",
    )
    try:
        msg = await bot.send_animation(
            chat_id=int(admin["sub"]),
            animation=animation,
            caption="🎬 GIF загружен для рассылки",
        )
    except Exception as e:
        raise server_error("upload_to_telegram_failed") from e

    if not msg.animation:
        raise HTTPException(500, "telegram_returned_no_animation")
    return {"file_id": msg.animation.file_id}


# ── CREATE + SEND ────────────────────────────────────────────────────


# Telegram-клиент при копировании premium-эмодзи иногда вставляет их
# в Markdown image-синтаксисе  ![👑](tg://emoji?id=12345).  Бот шлёт
# broadcast только с parse_mode="HTML" — такой markdown отрисуется как
# plain text и сломает entity-парсер (отсюда 600/600 ошибок). Чтобы
# админ мог копи-пастить из любого источника, нормализуем оба формата
# к HTML-варианту  <tg-emoji emoji-id="12345">👑</tg-emoji>.
_MD_TG_EMOJI_RE = re.compile(r"!\[([^\]]+?)\]\(tg://emoji\?id=(\d+)\)")


def normalize_premium_emoji(text: str) -> str:
    """Convert Markdown `![emoji](tg://emoji?id=X)` → HTML `<tg-emoji>`.

    Idempotent on text that's already HTML.
    """
    if not text:
        return text
    return _MD_TG_EMOJI_RE.sub(
        lambda m: f'<tg-emoji emoji-id="{m.group(2)}">{m.group(1)}</tg-emoji>',
        text,
    )


_BUTTON_TYPES = {
    "buy",
    "promo_buy",
    "promo_traffic",
    "gift_reveal",
    "gift_1m",
    "gift_3m",
    "gift_1y_40",
    "support",
    "channel",
    "referral",
    "bypass",
    "happ_ios",
    "happ_android",
    "web_client",
    "buy_combo",
    "share_discount",
    "my_proxy",
    "gift_combo",
    "beta_apply",
    "trial_key",
}


_GIFT_REVEAL_PERCENT_CHOICES = (20, 25, 30, 35, 40)


_VALID_TAG_COLORS = {
    "gray", "red", "orange", "yellow", "green", "blue", "purple",
}


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
    # Опциональная цветная метка (migration 071)
    tag: Optional[str] = Field(None, max_length=40)
    tag_color: Optional[str] = Field(None, max_length=16)

    @field_validator("tag_color")
    @classmethod
    def _valid_tag_color(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.lower().strip()
        if v not in _VALID_TAG_COLORS:
            raise ValueError(
                f"tag_color must be one of {sorted(_VALID_TAG_COLORS)}",
            )
        return v

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
        raise server_error("fetch_pairs_failed") from e
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
        raise server_error("send_failed") from e

    return {
        "ok": True,
        "message_ids": message_ids,
        "split": len(message_ids) > 1,
        "to": admin_id,
    }


@router.post("")
async def broadcast_create(
    body: BroadcastCreateRequest,
    admin: dict = Depends(require_admin),
):
    return await _launch_broadcast(body, admin)


async def _launch_broadcast(body: BroadcastCreateRequest, admin: dict) -> dict:
    """Resolve the segment, create the broadcasts row (+ discount metadata),
    send in the background. Shared by POST / and POST /renewal-offer."""
    bot = _get_bot()
    _require_valid_segment(body.segment)

    # Нормализуем premium-эмодзи (Markdown → HTML) — см. normalize_premium_emoji.
    message_html = normalize_premium_emoji(body.message)

    try:
        user_ids = await database.get_users_by_segment(body.segment)
    except Exception as e:
        raise server_error("segment_resolve_failed") from e
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
            tag=body.tag,
            tag_color=body.tag_color,
        )
    except Exception as e:
        raise server_error("create_broadcast_failed") from e

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


# ── helpers ──────────────────────────────────────────────────────────


def _get_bot():
    """Pull the live aiogram Bot from the telegram_webhook module —
    set there by main.py at startup. Raises 503 if it isn't ready
    yet (extremely rare after startup but possible during deploy)."""
    from app.api import telegram_webhook
    bot = getattr(telegram_webhook, "_bot", None)
    if bot is None:
        raise HTTPException(503, "bot_not_ready")
    return bot


def _build_reply_markup(
    buttons: list[str],
    broadcast_id: int,
    discount: Optional[int],
) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for btn in buttons:
        if btn == "buy":
            rows.append([InlineKeyboardButton(text="🛒 Купить", callback_data="menu_buy_vpn")])
        elif btn == "promo_buy":
            label = f"🎁 Купить со скидкой {discount}%" if discount else "🎁 Купить со скидкой"
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"broadcast_promo_buy:{broadcast_id}",
            )])
        elif btn == "promo_traffic":
            label = (
                f"📊 Купить ГБ со скидкой {discount}%"
                if discount else "📊 Купить ГБ со скидкой"
            )
            rows.append([InlineKeyboardButton(
                text=label,
                callback_data=f"broadcast_promo_traffic:{broadcast_id}",
            )])
        elif btn == "gift_reveal":
            # «Посмотреть подарок» — теплично-CTA. Хардкоженная фишка:
            # 20% скидка на подписку, 48 часов. Параметры discount_percent /
            # discount_hours дашборда не используются — здесь свой реверс-
            # сюрприз flow с premium-эмодзи и delayed reveal в handler'е.
            # Красная кнопка задаётся явным style="danger" (см. monkey-patch
            # в app/utils/button_defaults.py — fallback по text-pattern
            # не сработает на эту фразу, передаём руками).
            rows.append([InlineKeyboardButton(
                text="Посмотреть подарок",
                callback_data=f"broadcast_gift_reveal:{broadcast_id}",
                style="danger",
                icon_custom_emoji_id="5210956306952758910",
            )])
        elif btn == "support":
            rows.append([InlineKeyboardButton(
                text="💬 Поддержка", url=get_brand().support_url,
            )])
        elif btn == "channel":
            rows.append([InlineKeyboardButton(
                text="📢 Наш канал", url=get_brand().channel_url,
            )])
        elif btn == "referral":
            rows.append([InlineKeyboardButton(
                text="👥 Пригласить друга", callback_data="menu_referral",
            )])
        elif btn == "bypass":
            rows.append([InlineKeyboardButton(
                text="🌐 Включить обход", callback_data="broadcast_bypass",
            )])
        elif btn == "happ_ios":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для iOS ⚡️",
                url="https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6788279553?l=en-GB",
            )])
        elif btn == "happ_android":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для Android 🤖",
                url="https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
            )])
        elif btn == "web_client":
            rows.append([InlineKeyboardButton(
                text="🌐 Веб-клиент QoDev", url="https://qodev.dev",
            )])
        elif btn == "buy_combo":
            rows.append([InlineKeyboardButton(text="🏆 Купить Комбо", callback_data="buy_combo")])
        elif btn == "gift_1m":
            rows.append([InlineKeyboardButton(
                text="🎁 −30% на 1 месяц",
                callback_data="broadcast_gift_1m",
            )])
        elif btn == "gift_3m":
            rows.append([InlineKeyboardButton(
                text="🎁 Скидка 30% на 3 месяца",
                callback_data="broadcast_gift_3m",
            )])
        elif btn == "gift_1y_40":
            # «🎁 1 год со скидкой 40%». Открывает 2-шаговый flow: тариф →
            # период. Скидка применяется ТОЛЬКО к 365-дневному плану,
            # остальные периоды по обычной цене. Реализация в
            # app/handlers/payments/broadcast_offers.py:callback_broadcast_gift_1y_40.
            rows.append([InlineKeyboardButton(
                text="🎁 1 год со скидкой 40%",
                callback_data="broadcast_gift_1y_40",
            )])
        elif btn == "share_discount":
            # Callback share_discount_open рендерится в referrals.py:
            # экран «Подари другу скидку 30%» + кнопка share с личной
            # refd-ссылкой получателя. broadcast_id здесь не нужен —
            # callback статический.
            rows.append([InlineKeyboardButton(
                text="🎁 Поделиться скидкой",
                callback_data="share_discount_open",
            )])
        elif btn == "gift_combo":
            # Персональный подарок Combo Basic 1 мес со скидкой (% и часы
            # из полей рассылки). Handler: callback_broadcast_gift_combo
            # в app/handlers/payments/broadcast_offers.py.
            rows.append([InlineKeyboardButton(
                text="🎁 Забрать подарок",
                callback_data=f"broadcast_gift_combo:{broadcast_id}",
            )])
        elif btn == "my_proxy":
            # Для рассылок владельцам прокси (сегмент bought_proxy):
            # callback proxy_open отрисует delivery-screen «Ваш Telegram-
            # прокси готов» + кнопку «🔌 Подключить прокси».
            rows.append([InlineKeyboardButton(
                text="🧩 Мой прокси",
                callback_data="proxy_open",
            )])
        elif btn == "beta_apply":
            # Бета-заявка «🧪 VPN-Инноватор»: запись в beta_applications с
            # source_broadcast_id, повторный клик → toast «Заявка уже
            # принята». Handler: app/handlers/callbacks/beta_apply.py.
            rows.append([InlineKeyboardButton(
                text="🧪 Оставить заявку",
                callback_data=f"beta_apply:{broadcast_id}",
            )])
        elif btn == "trial_key":
            # «🎁 Получить пробный ключ»: +1 день подписки и +1 ГБ обхода.
            # Один раз на рассылку (broadcast_id, telegram_id). После выдачи —
            # «Подарок активирован» + экран подключения устройства через ~1.5с.
            # Handler: app/handlers/callbacks/broadcast_trial_key.py.
            rows.append([InlineKeyboardButton(
                text="🎁 Получить пробный ключ",
                callback_data=f"broadcast_trial_key:{broadcast_id}",
            )])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _with_segment_label(out: dict) -> dict:
    """Adds `segment_label` — the human name of a stored key («Платная истекла,
    не продлил за последние 6 месяцев»); unknown keys come back as they are."""
    if "segment" in out:
        out["segment_label"] = seg.segment_label(out.get("segment"))
    return out


def _serialize(row) -> dict:
    if not isinstance(row, dict):
        return {}
    out: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            continue
        else:
            out[k] = v
    return out


# ═════════════════════════════════════════════════════════════════════
# SCHEDULED BROADCASTS — отложенные и повторяющиеся
# ═════════════════════════════════════════════════════════════════════
#
# Тайм-зона планировщика — Europe/Moscow (UTC+3). Админ вводит время
# в MSK через UI, бэкенд конвертирует в UTC для хранения и сравнения
# с NOW() в scheduler-worker'е.

_MSK_TZ = timezone(timedelta(hours=3))
_MAX_SCHEDULE_WEEKS_AHEAD = 4  # запрет планировать больше чем на 4 недели вперёд


class ScheduleBroadcastRequest(BaseModel):
    """Запланировать существующую рассылку.

    Клонирует title/message/photo/buttons/discount из source_broadcast_id
    (снапшот) и создаёт задачу в scheduled_broadcasts.

    scheduled_at_msk: `YYYY-MM-DD HH:MM` в Europe/Moscow. Максимум +4 недели.
    recurrence: once | daily | weekdays | weekly
    recurrence_end_at_msk: опциональный «дедлайн» для recurring — тоже MSK.
    segment: опционально переопределить (по умолчанию — из source).
    """
    source_broadcast_id: int = Field(..., gt=0)
    scheduled_at_msk: str = Field(..., min_length=10, max_length=32)
    recurrence: str = Field("once")
    recurrence_end_at_msk: Optional[str] = Field(None, max_length=32)
    segment: Optional[str] = Field(None, min_length=1, max_length=60)

    @field_validator("recurrence")
    @classmethod
    def _valid_rec(cls, v: str) -> str:
        v = (v or "once").strip().lower()
        if v not in database.VALID_RECURRENCES:
            raise ValueError(
                f"recurrence must be one of {sorted(database.VALID_RECURRENCES)}"
            )
        return v


def _parse_msk(dt_str: str) -> datetime:
    """Парсит `YYYY-MM-DD HH:MM` (или ISO) как MSK, возвращает UTC-aware."""
    dt_str = dt_str.strip().replace("T", " ")
    # Пробуем два формата: с секундами и без
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(dt_str, fmt)
            break
        except ValueError:
            continue
    else:
        raise HTTPException(400, f"invalid datetime format: {dt_str!r}")
    msk = naive.replace(tzinfo=_MSK_TZ)
    return msk.astimezone(timezone.utc)


@router.post("/schedule")
async def broadcast_schedule_create(
    body: ScheduleBroadcastRequest,
    admin: dict = Depends(require_admin),
):
    """Создать отложенное/повторяющееся задание на основе существующей рассылки."""
    # 1. Валидация datetime (MSK → UTC)
    scheduled_utc = _parse_msk(body.scheduled_at_msk)
    now_utc = datetime.now(timezone.utc)
    if scheduled_utc < now_utc - timedelta(minutes=1):
        raise HTTPException(400, "scheduled_at is in the past")
    if scheduled_utc > now_utc + timedelta(weeks=_MAX_SCHEDULE_WEEKS_AHEAD):
        raise HTTPException(
            400,
            f"scheduled_at too far in the future (max {_MAX_SCHEDULE_WEEKS_AHEAD} weeks ahead)",
        )
    end_utc: Optional[datetime] = None
    if body.recurrence_end_at_msk:
        end_utc = _parse_msk(body.recurrence_end_at_msk)
        if end_utc <= scheduled_utc:
            raise HTTPException(400, "recurrence_end_at must be after scheduled_at")

    if body.segment:
        _require_valid_segment(body.segment)

    # 2. Достаём исходную рассылку — из неё делаем снапшот.
    try:
        source = await database.get_broadcast(body.source_broadcast_id)
    except Exception as e:
        raise server_error("source_lookup_failed") from e
    if not source:
        raise HTTPException(404, "source broadcast not found")

    # Discount fields — подтягиваем отдельно (лежат в broadcast_discounts)
    disc = None
    try:
        disc = await database.get_broadcast_discount(body.source_broadcast_id)
    except Exception as e:
        logger.warning("SCHED_DISC_LOOKUP_FAIL: %s", e)
    disc = disc or {}

    # 3. Создаём scheduled_broadcast
    try:
        sched_id = await database.create_scheduled_broadcast(
            source_broadcast_id=body.source_broadcast_id,
            title=str(source.get("title") or ""),
            message=str(source.get("message") or source.get("message_a") or ""),
            segment=body.segment or str(source.get("segment") or ""),
            scheduled_at=scheduled_utc,
            recurrence=body.recurrence,
            recurrence_end_at=end_utc,
            created_by=int(admin["sub"]),
            photo_file_id=(source.get("photo_file_id") or None),
            animation_file_id=(source.get("animation_file_id") or None),
            buttons=list(source.get("buttons") or []) or None,
            discount_percent=disc.get("discount_percent"),
            discount_hours=disc.get("discount_hours"),
            discount_label=disc.get("discount_label"),
            gift_reveal_percent=disc.get("gift_reveal_percent"),
        )
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise server_error("schedule_create_failed") from e

    bus.publish({
        "type": "broadcast:scheduled",
        "sched_id": sched_id,
        "source_broadcast_id": body.source_broadcast_id,
        "scheduled_at": scheduled_utc.isoformat(),
        "recurrence": body.recurrence,
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "sched_id": sched_id,
        "scheduled_at_utc": scheduled_utc.isoformat(),
        "scheduled_at_msk": scheduled_utc.astimezone(_MSK_TZ).isoformat(),
        "recurrence": body.recurrence,
    }
