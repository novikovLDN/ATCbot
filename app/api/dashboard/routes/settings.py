"""Admin settings — notification toggles and self-tests."""
import asyncio
import logging
import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import config
from app.api.dashboard.deps import require_admin
from app.services import admin_settings

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/notifications")
async def settings_get_notifications():
    return await admin_settings.get_notification_flags()


class NotificationFlagPatch(BaseModel):
    key: str = Field(..., min_length=1, max_length=40)
    enabled: bool


@router.post("/notifications")
async def settings_patch_notifications(body: NotificationFlagPatch):
    try:
        return await admin_settings.set_notification_flag(body.key, body.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _get_bot():
    from app.api import telegram_webhook
    bot = getattr(telegram_webhook, "_bot", None)
    if bot is None:
        raise HTTPException(503, "bot_not_ready")
    return bot


# Test phrase chosen at random from admin_notifier's set for
# realism — keeps the test output identical in shape to what a
# real milestone hit looks like.
_TEST_MILESTONE = 25_000
_TEST_PHRASES = [
    "Топ-форма, продолжай 🔥",
    "День явно твой 🚀",
    "Хорошо идёт — не сбавляй ⚡",
]


async def _send_test_sequence(bot):
    """Fire one of every admin-DM notification we have, 1 second
    apart, with a header line marking them as tests."""
    chat = config.ADMIN_TELEGRAM_ID
    intro = (
        "🧪 <b>Тестовая отправка</b>\n"
        "Сейчас придёт по одному примеру каждого типа уведомления "
        "с задержкой 1 сек. Это просто проверка — реальных событий "
        "в боте не происходило."
    )
    try:
        await bot.send_message(chat, intro, parse_mode="HTML")
    except Exception as e:
        logger.warning("test sequence intro send failed: %s", e)
        return

    messages = [
        (
            "⚠️ <b>Ошибка платежа</b>\n"
            "Стадия: <code>таймаут</code>\n"
            "Провайдер: <b>Platega</b>\n"
            "User: <code>tg:111111111</code>\n\n"
            "<i>тестовое сообщение</i>"
        ),
        (
            "📣 <b>Рассылка #1234 завершена</b>\n"
            "Доставлено: <b>9 832</b> / 10 000\n"
            "Не доставлено: <b>168</b>\n\n"
            "<i>тестовое сообщение</i>"
        ),
        (
            f"💸 <b>{_TEST_MILESTONE:,} ₽ за день</b>\n".replace(",", " ")
            + random.choice(_TEST_PHRASES)
            + "\n\n<i>тестовое сообщение</i>"
        ),
    ]
    for text in messages:
        await asyncio.sleep(1.0)
        try:
            await bot.send_message(
                chat, text, parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning("test notification send failed: %s", e)


@router.post("/notifications/test")
async def settings_test_notifications():
    """Send one sample of every admin notification we have, with
    a 1-second pause between them. Returns immediately; the actual
    send is fired as a background task."""
    bot = _get_bot()
    asyncio.create_task(_send_test_sequence(bot))
    return {"ok": True, "count": 3, "delay_seconds": 1.0}


# ── Web Push (browser notifications) ────────────────────────────────


@router.get("/push/vapid-key")
async def settings_push_vapid_key():
    """Public VAPID key for PushManager.subscribe(applicationServerKey)."""
    from app.services import push_notifications
    try:
        return {"publicKey": await push_notifications.get_public_key()}
    except Exception as e:
        raise HTTPException(500, f"vapid_key_failed: {e}")


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2000)
    p256dh: str = Field(..., min_length=10, max_length=1000)
    auth: str = Field(..., min_length=4, max_length=500)
    user_agent: str = Field("", max_length=300)
    label: str = Field("", max_length=60)


@router.post("/push/subscribe")
async def settings_push_subscribe(body: PushSubscribeRequest):
    from app.services import push_notifications
    ok = await push_notifications.upsert_subscription(
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_agent=body.user_agent or None,
        label=body.label or None,
    )
    if not ok:
        raise HTTPException(500, "subscribe_failed")
    return {"ok": True}


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2000)


@router.post("/push/unsubscribe")
async def settings_push_unsubscribe(body: PushUnsubscribeRequest):
    from app.services import push_notifications
    await push_notifications.remove_subscription(body.endpoint)
    return {"ok": True}


@router.get("/push/subscriptions")
async def settings_push_subscriptions():
    from app.services import push_notifications
    return await push_notifications.list_subscriptions()


@router.post("/push/test")
async def settings_push_test():
    """Send a single test push to every registered device."""
    from app.services import push_notifications
    try:
        result = await push_notifications.send_to_all(
            title="🧪 Тестовое уведомление",
            body="Atlas Admin — пуш-уведомления работают.",
            tag="atlas-test",
        )
        return result
    except Exception as e:
        raise HTTPException(500, f"push_test_failed: {e}")
