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
from typing import Optional

from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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
from app.events import bus

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


# ── READ ──────────────────────────────────────────────────────────────


@router.get("/recent")
async def broadcasts_recent(limit: int = Query(20, gt=0, le=200)):
    try:
        rows = await database.get_recent_broadcasts(limit)
    except Exception as e:
        raise HTTPException(500, f"broadcasts_failed: {e}")
    return [_serialize(r) for r in rows]


@router.get("/segments")
async def segments_list():
    """Available segments with current member counts + tooltip descriptions.

    Counts are computed eagerly so the wizard can show an audience size
    before the admin commits. `group` группирует сегменты в UI, чтобы
    админу было проще ориентироваться среди 25+ ключей.
    """
    # (key, label, description, group) — description показывается в
    # tooltip рядом с каждым сегментом в дашборде.
    segments = [
        # ── Базовые ──────────────────────────────────────────────────
        ("all_users", "Все юзеры",
         "Все записи в таблице users — включая тех, кто нажал /start и ушёл.",
         "Базовые"),
        ("active_subscriptions", "Активные подписки",
         "У пользователя есть подписка с expires_at > NOW (любого типа: триал, платная, gift, admin_grant).",
         "Базовые"),
        ("no_subscription", "Без активной подписки",
         "Нет строки в subscriptions с expires_at > NOW. Включает и тех, кто никогда не подписывался, и тех, у кого истекла.",
         "Базовые"),
        ("no_remnawave", "Без Remnawave",
         "Никогда не было entity в панели Remnawave — ни premium, ни bypass. То есть не завёл ни одного ключа.",
         "Базовые"),

        # ── Cold-start (новые молчуны) ───────────────────────────────
        ("started_1d_cold", "Cold — старт за 24ч, ничего",
         "Нажал /start за последние 24 часа И до сих пор не активировал триал, не купил, не завёл ключ. Свежий молчун, самое время догреть.",
         "Cold-start"),
        ("started_3d_cold", "Cold — старт за 3 дня, ничего",
         "Нажал /start за последние 3 дня И до сих пор ничего. Ещё помнит про бот.",
         "Cold-start"),
        ("started_7d_cold", "Cold — старт за 7 дней, ничего",
         "Нажал /start за последние 7 дней И до сих пор ничего.",
         "Cold-start"),
        ("started_14d_cold", "Cold — старт за 14 дней, ничего",
         "Нажал /start за последние 14 дней И до сих пор ничего. Уже подзабыл, нужен сильный оффер.",
         "Cold-start"),
        ("started_30d_cold", "Cold — старт за 30 дней, ничего",
         "Нажал /start за последние 30 дней И до сих пор ничего. Крайний край — «уходящий».",
         "Cold-start"),

        # ── Триальная воронка (кто активировал триал) ────────────────
        ("trial_ends_in_1d", "Триал — заканчивается через 24ч",
         "Триал ещё идёт, но истечёт в ближайшие 24 часа. Ключевой момент конверсии — «оформи, чтобы не потерять».",
         "Триал"),
        ("trial_expired_6h", "Триал — истёк 6ч назад",
         "Триал закончился ~6 часов назад, платной подписки не оформлено. Свежий «упавший» триал.",
         "Триал"),
        ("trial_expired_1d", "Триал — истёк 1 день назад",
         "Триал закончился ~1 день назад, платной нет. Первое напоминание после разрыва.",
         "Триал"),
        ("trial_expired_2d", "Триал — истёк 2 дня назад",
         "Триал закончился ~2 дня назад, платной нет.",
         "Триал"),
        ("trial_expired_3d", "Триал — истёк 3 дня назад",
         "Триал закончился ~3 дня назад, платной нет.",
         "Триал"),
        ("trial_expired_7d", "Триал — истёк 7 дней назад (не купил)",
         "Триал закончился ~7 дней назад И НИКОГДА не покупал подписку. Холодная реактивация недельной давности.",
         "Триал"),
        ("trial_expired_14d", "Триал — истёк 14 дней назад (не купил)",
         "Триал закончился ~14 дней назад И никогда не покупал. Двухнедельная реактивация.",
         "Триал"),
        ("trial_expired_30d", "Триал — истёк 30 дней назад (не купил)",
         "Триал закончился ~30 дней назад И никогда не покупал. Месячная реактивация.",
         "Триал"),
        ("trial_expired_90d", "Триал — истёк 3 мес назад (не купил)",
         "Триал закончился ~90 дней назад И никогда не покупал. «Последний шанс» — сильный оффер обязателен.",
         "Триал"),

        # ── Платные churn / реактивация ──────────────────────────────
        ("expires_in_3d", "Платная — заканчивается за 3 дня",
         "Платная подписка ещё активна, истечёт в ближайшие 72ч. Точка «продли/переоформи» пока пользователь внутри.",
         "Платная"),
        ("paid_expired_1d", "Платная — истекла 1 день назад",
         "Платная истекла ~1 день назад, сейчас платной нет. Свежий churn — первое напоминание.",
         "Платная"),
        ("paid_expired_7d", "Платная — истекла 7 дней назад",
         "Платная истекла ~7 дней назад, сейчас платной нет. Недельная реактивация.",
         "Платная"),
        ("paid_expired_14d", "Платная — истекла 14 дней назад",
         "Платная истекла ~14 дней назад, сейчас нет.",
         "Платная"),
        ("paid_expired_30d", "Платная — истекла за последние 30 дней",
         "По истории подписок последняя платная закончилась в окне 1–30 дней назад и сейчас неактивна.",
         "Платная"),
        ("paid_expired_60d", "Платная — истекла 60 дней назад",
         "Платная истекла ~60 дней назад. Двухмесячный churn.",
         "Платная"),
        ("paid_expired_90d", "Платная — истекла 90 дней назад",
         "Платная истекла ~90 дней назад. Крайний край реактивации.",
         "Платная"),
        ("paid_lapsed_any", "Платная — когда-либо платил, сейчас не активен",
         "Когда-либо оплачивал (purchase / renewal / auto_renew) и сейчас без активной подписки. Максимальная реактивационная аудитория — всех «ушедших».",
         "Платная"),

        # ── Любая (комбинированные) ──────────────────────────────────
        ("expired_1d", "Истекла (любая) 1 день назад",
         "Любая подписка (триал ∪ платная) истекла ~1 день назад.",
         "Истёкшие (любые)"),
        ("expired_2d", "Истекла (любая) 2 дня назад",
         "Любая подписка истекла ~2 дня назад.",
         "Истёкшие (любые)"),
        ("expired_3d", "Истекла (любая) 3 дня назад",
         "Любая подписка истекла ~3 дня назад.",
         "Истёкшие (любые)"),

        # ── Апселл / VIP / балансовый ────────────────────────────────
        ("vip_active", "VIP-пользователи",
         "users.is_vip = TRUE. Для эксклюзивных приглашений, ранних доступов, фидбека.",
         "Апселл / особые"),
        ("basic_active", "Активные Basic",
         "Сейчас активна подписка Basic. Целевая для upsell на Plus / Combo.",
         "Апселл / особые"),
        ("plus_active", "Активные Plus",
         "Сейчас активна подписка Plus. Целевая для upsell на Combo или продление на 1 год.",
         "Апселл / особые"),
        ("combo_active", "Активные Combo",
         "Сейчас активная подписка типа Combo (Basic/Plus). Целевая для апселла на большие GB-паки обхода / доп. устройств.",
         "Апселл / особые"),
        ("discount_active", "Активная персональная скидка",
         "У пользователя действует скидка в user_discounts (не broadcast). Напомнить: «у тебя действует скидка N% — воспользуйся».",
         "Апселл / особые"),
        ("has_balance_50plus", "Баланс ≥ 50₽",
         "На балансе не меньше 50₽. Напоминание использовать балансовый чекаут.",
         "Апселл / особые"),
    ]
    out = []
    for key, label, description, group in segments:
        try:
            ids = await database.get_users_by_segment(key)
            count = len(ids)
        except Exception as e:
            logger.warning("SEGMENT_COUNT_FAIL key=%s err=%s", key, e)
            count = -1
        out.append({
            "key": key,
            "label": label,
            "description": description,
            "group": group,
            "count": count,
        })
    return out


@router.get("/{broadcast_id}")
async def broadcast_detail(broadcast_id: int = Path(..., gt=0)):
    """Full broadcast row + discount/gift_reveal — используется UI-ом
    «Отправить снова», чтоб предзаполнить визард всеми полями."""
    try:
        row = await database.get_broadcast(broadcast_id)
    except Exception as e:
        raise HTTPException(500, f"broadcast_detail_failed: {e}")
    if not row:
        raise HTTPException(404, "Broadcast not found")
    out = _serialize(row)
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
        raise HTTPException(500, f"broadcast_stats_failed: {e}")
    return _serialize(stats or {})


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
        raise HTTPException(500, f"upload_to_telegram_failed: {e}")

    if not msg.photo:
        raise HTTPException(500, "telegram_returned_no_photo")
    return {"file_id": msg.photo[-1].file_id}


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
}


_GIFT_REVEAL_PERCENT_CHOICES = (20, 25, 30, 35, 40)


class BroadcastCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    segment: str = Field(..., min_length=1, max_length=60)
    photo_file_id: Optional[str] = Field(None, max_length=300)
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


@router.post("")
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
                text="💬 Поддержка", url="https://t.me/atlas_suppbot",
            )])
        elif btn == "channel":
            rows.append([InlineKeyboardButton(
                text="📢 Наш канал", url="https://t.me/ATC_VPN",
            )])
        elif btn == "referral":
            rows.append([InlineKeyboardButton(
                text="👥 Пригласить друга", callback_data="menu_referral",
            )])
        elif btn == "bypass":
            rows.append([InlineKeyboardButton(
                text="🌐 Включить обход", callback_data="traffic_info",
            )])
        elif btn == "happ_ios":
            rows.append([InlineKeyboardButton(
                text="📲 Скачать Happ для iOS ⚡️",
                url="https://apps.apple.com/ru/app/happ-proxy-utility/id6783623643?l=en-GB",
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
        elif btn == "gift_1y_40":
            # «🎁 1 год со скидкой 40%». Открывает 2-шаговый flow: тариф →
            # период. Скидка применяется ТОЛЬКО к 365-дневному плану,
            # остальные периоды по обычной цене. Реализация в
            # app/handlers/admin/broadcast.py:callback_broadcast_gift_1y_40.
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
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


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
