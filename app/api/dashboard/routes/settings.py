"""Admin settings — notification toggles and self-tests.

СЕКРЕТЫ
    Текст исключения наружу — только через scrub_secrets. Здесь это не
    формальность: push-стек ходит на сторонние сервисы (FCM, APNs, Mozilla) и
    в тексте ошибки приезжает endpoint подписки — по сути идентификатор
    устройства администратора, — а у VAPID-ветки в ошибке бывает и сам ключ.

ЛОГИ
    Каждый обработчик пишет и удачу, и отказ. Экран «Настройки» показывает
    состояние тумблеров, и «тумблер выключен» надо уметь отличить от «запрос
    не дошёл» по логу.
"""
import asyncio
import logging
import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import config
from app.api.dashboard.deps import require_admin
from app.services import admin_settings
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/notifications")
async def settings_get_notifications():
    """Текущее состояние тумблеров уведомлений админу."""
    try:
        flags = await admin_settings.get_notification_flags()
    except Exception as e:
        logger.error("settings.notifications_get failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"notifications_get_failed: {scrub_secrets(e)}")
    logger.info("settings.notifications_get ok")
    return flags


class NotificationFlagPatch(BaseModel):
    key: str = Field(..., min_length=1, max_length=40)
    enabled: bool


@router.post("/notifications")
async def settings_patch_notifications(body: NotificationFlagPatch):
    """Включить или выключить один тип уведомлений."""
    try:
        flags = await admin_settings.set_notification_flag(body.key, body.enabled)
    except ValueError as e:
        # Неизвестный ключ — ошибка вызывающего, а не сбой. 400, не 500.
        logger.warning(
            "settings.notifications_patch rejected: key=%s %s",
            body.key,
            scrub_secrets(e),
        )
        raise HTTPException(400, scrub_secrets(e))
    except Exception as e:
        logger.error("settings.notifications_patch failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"notifications_patch_failed: {scrub_secrets(e)}")
    logger.info(
        "settings.notifications_patch ok: key=%s enabled=%s", body.key, body.enabled
    )
    return flags


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
            logger.warning(
                "settings.notifications_test send failed: %s", scrub_secrets(e)
            )


@router.post("/notifications/test")
async def settings_test_notifications():
    """Send one sample of every admin notification we have, with
    a 1-second pause between them. Returns immediately; the actual
    send is fired as a background task."""
    bot = _get_bot()
    asyncio.create_task(_send_test_sequence(bot))
    logger.info("settings.notifications_test scheduled: count=3")
    return {"ok": True, "count": 3, "delay_seconds": 1.0}


# ── Web Push (browser notifications) ────────────────────────────────


@router.get("/push/vapid-key")
async def settings_push_vapid_key():
    """Public VAPID key for PushManager.subscribe(applicationServerKey)."""
    from app.services import push_notifications
    try:
        key = await push_notifications.get_public_key()
    except Exception as e:
        logger.error("settings.push_vapid_key failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"vapid_key_failed: {scrub_secrets(e)}")
    logger.info("settings.push_vapid_key ok")
    return {"publicKey": key}


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2000)
    p256dh: str = Field(..., min_length=10, max_length=1000)
    auth: str = Field(..., min_length=4, max_length=500)
    user_agent: str = Field("", max_length=300)
    label: str = Field("", max_length=60)


@router.post("/push/subscribe")
async def settings_push_subscribe(body: PushSubscribeRequest):
    """Зарегистрировать браузерную подписку на push."""
    from app.services import push_notifications
    try:
        ok = await push_notifications.upsert_subscription(
            endpoint=body.endpoint,
            p256dh=body.p256dh,
            auth=body.auth,
            user_agent=body.user_agent or None,
            label=body.label or None,
        )
    except Exception as e:
        logger.error("settings.push_subscribe failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"subscribe_failed: {scrub_secrets(e)}")
    if not ok:
        logger.error("settings.push_subscribe rejected by service")
        raise HTTPException(500, "subscribe_failed")
    # endpoint в лог не пишем целиком: это идентификатор устройства.
    logger.info("settings.push_subscribe ok: label=%s", body.label or "-")
    return {"ok": True}


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2000)


@router.post("/push/unsubscribe")
async def settings_push_unsubscribe(body: PushUnsubscribeRequest):
    """Отключить push на одном устройстве."""
    from app.services import push_notifications
    try:
        await push_notifications.remove_subscription(body.endpoint)
    except Exception as e:
        logger.error("settings.push_unsubscribe failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"unsubscribe_failed: {scrub_secrets(e)}")
    logger.info("settings.push_unsubscribe ok")
    return {"ok": True}


@router.get("/push/subscriptions")
async def settings_push_subscriptions():
    """Список устройств, на которых подключён push."""
    from app.services import push_notifications
    try:
        rows = await push_notifications.list_subscriptions()
    except Exception as e:
        logger.error("settings.push_subscriptions failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"subscriptions_failed: {scrub_secrets(e)}")
    logger.info("settings.push_subscriptions ok: rows=%s", len(rows or []))
    return rows


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
    except Exception as e:
        logger.error("settings.push_test failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"push_test_failed: {scrub_secrets(e)}")
    logger.info(
        "settings.push_test ok: sent=%s total=%s",
        (result or {}).get("sent"),
        (result or {}).get("total"),
    )
    return result
