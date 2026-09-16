"""
Admin Telegram notifications driven by app.events.bus.

Sends DMs to ADMIN_TELEGRAM_ID on:
  - payment:error           — throttled (≤ 1 msg/min per stage+provider)
  - broadcast:done          — every completion
  - revenue milestone hits  — when today's gross crosses 5/10/15/20/
                              25/30/35/40k RUB. Каждый milestone
                              стреляет один раз за MSK-сутки, с
                              рандомной короткой поздравительной фразой.

Runs as a long-lived asyncio task started from main.py. Subscribes
to the bus and forwards events to the bot. Survives bus stalls and
Telegram failures (logged, continues).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot

import config
from app.events import bus

logger = logging.getLogger(__name__)


# ── Revenue milestones — ascending order, in rubles ────────────────
MILESTONES = [5_000, 10_000, 15_000, 20_000, 25_000, 30_000, 35_000, 40_000]

# Short congrat phrases per milestone. Multiple variants per level so
# the day feels alive when the same threshold gets crossed across
# different days; one is picked at random.
PHRASES: dict[int, list[str]] = {
    5_000: [
        "Ты молодец! 🦾",
        "Утренний стартап получился 🌱",
        "Первая планка — твоя 🎯",
    ],
    10_000: [
        "Стабильная база — го дальше 🌱",
        "Десятка взята 💪",
        "Двузначно. Дальше — больше 📈",
    ],
    15_000: [
        "Полтора червонца, отличный темп ⚡",
        "Темп держится 🔝",
        "Уверенно идём 🏃‍♂️",
    ],
    20_000: [
        "Двадцатка взята, день удался 🎯",
        "Это сильно. Молодец 💪",
        "Двадцатник — респект 🤝",
    ],
    25_000: [
        "Топ-форма, продолжай 🔥",
        "День явно твой 🚀",
        "Хорошо идёт — не сбавляй ⚡",
    ],
    30_000: [
        "Это сильно. Серьёзно 💪",
        "Тридцатник — мощно 🦁",
        "Поздравляю с тридцаткой 🏆",
    ],
    35_000: [
        "Сегодня офигеть как зашло 🚀",
        "35k+ — это пушка 🔥",
        "Чувак, ты на огне 🔥🔥🔥",
    ],
    40_000: [
        "Сорокет — это космос 🚀🌌",
        "40k за день — рекорд-темп 🏆",
        "Ты сегодня в форме бога 🔥🦾",
    ],
}


# MSK — часовой пояс, в котором админ живёт и в котором «сегодня»
# должен считаться. Milestone-накопитель раньше жил в UTC — в 03:00 МСК
# счётчик сбрасывался, что путало.
MSK_TZ = timezone(timedelta(hours=3))


def _today_key() -> str:
    """MSK date key for milestone bookkeeping (admin lives in MSK).

    Раньше был UTC, из-за чего в 03:00 МСК счётчик сбрасывался и админ
    видел «40k за день» на самом деле только за неполные 3 часа.
    """
    return datetime.now(MSK_TZ).strftime("%Y-%m-%d")


def msk_midnight_utc() -> datetime:
    """Start of the current MSK day as an aware UTC moment."""
    now_msk = datetime.now(MSK_TZ)
    start = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


async def revenue_since_msk_midnight() -> float:
    """Gross revenue of the running MSK day, in rubles.

    Exact midnight bound. The previous call passed «hours since midnight»
    rounded to whole hours into a trailing-window query, so at 10:20 MSK
    it measured from 00:20 and quietly dropped the first minutes of the
    day (and at 10:40 it counted 20 minutes of yesterday).
    """
    import database
    data = await database.get_revenue_for_period(24, since=msk_midnight_utc())
    return float(data.get("revenue_rubles") or 0)


def _fmt_provider(p: str | None) -> str:
    if not p:
        return "—"
    return {
        "platega": "Platega",
        "cryptobot": "CryptoBot",
        "telegram_stars": "Telegram Stars",
        "telegram_payment": "Telegram",
        "lava": "Lava",
        "balance": "балансом",
    }.get(p, p)


def _fmt_stage(s: str) -> str:
    return {
        "webhook_invalid_json": "невалидный JSON",
        "setup_missing": "бот не готов",
        "service_missing": "сервис не подключён",
        "transient": "временная ошибка",
        "timeout": "таймаут",
        "unhandled_exception": "исключение",
        "amount_mismatch": "сумма не совпадает",
        "provider_callback_invalid": "невалидный callback",
        "provision_failed": "provisioning",
        "idempotency_rejected": "идемпотентность",
    }.get(s, s)


class _State:
    def __init__(self) -> None:
        # Per-(stage,provider) timestamp of last-sent error notification
        self.error_last_sent: dict[tuple[str, str], float] = {}
        # Day → set of milestones already announced today
        self.fired_milestones: dict[str, set[int]] = {}

    def can_send_error(self, key: tuple[str, str], cooldown: float = 60.0) -> bool:
        loop = asyncio.get_event_loop()
        now = loop.time()
        last = self.error_last_sent.get(key, 0.0)
        if now - last < cooldown:
            return False
        self.error_last_sent[key] = now
        return True


_state = _State()


# ── «Какие планки уже объявлены сегодня» ─────────────────────────────
# Раньше жило только в памяти процесса: любой деплой среди дня забывал
# отправленное, и вечерний платёж заново присылал 5k/10k/15k… Redis
# переживает рестарт; без Redis остаётся прежнее поведение (память).
_MILESTONES_KEY = "dashboard:revenue_milestones"
_MILESTONES_TTL_S = 48 * 3600


async def _redis():
    try:
        from app.utils.redis_client import get_redis, is_configured
        if not is_configured():
            return None
        return await get_redis()
    except Exception:
        return None


def _remember(day: str, fired: set[int]) -> None:
    """In-process mirror of today's state; also the whole store when
    Redis is not configured. Older days are dropped."""
    _state.fired_milestones = {day: set(fired)}


async def fired_milestones(day: str) -> set[int]:
    """Milestones already announced on that MSK day."""
    r = await _redis()
    if r is not None:
        try:
            raw = await r.get(f"{_MILESTONES_KEY}:{day}")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return {int(m) for m in json.loads(raw)} if raw else set()
        except Exception as e:
            logger.warning("milestone state read failed: %s", e)
    return set(_state.fired_milestones.get(day, set()))


async def remember_milestones(day: str, fired: set[int]) -> None:
    _remember(day, fired)
    r = await _redis()
    if r is None:
        return
    try:
        await r.set(
            f"{_MILESTONES_KEY}:{day}",
            json.dumps(sorted(fired)),
            ex=_MILESTONES_TTL_S,
        )
    except Exception as e:
        logger.warning("milestone state write failed: %s", e)


async def _dashboard_url(path: str) -> str:
    base = getattr(config, "DASHBOARD_BASE_URL", "") or ""
    return base.rstrip("/") + path


async def _send(bot: Bot, *, title: str, body: str, tag: str, url: str) -> None:
    """Deliver to both channels the admin really has.

    Browser web-push reaches a registered device; the Telegram DM is what
    the admin actually watches — and what the dashboard's «тестовая
    отправка» button uses. Until now only the test wrote to Telegram, so
    a real event was silent whenever no browser was subscribed (a revoked
    or never-created push subscription returns "sent 0" without an error).
    Each channel fails on its own; neither can break the other.
    """
    from app.services import push_notifications
    try:
        await push_notifications.send_to_all(
            title=title, body=body, tag=tag, url=url,
        )
        try:
            from app.core import runtime_health
            runtime_health.record_alert(f"push:{(tag or 'other').split(':')[0]}")
        except Exception:
            pass
    except Exception as e:
        logger.warning("admin_notifier push send failed: %s", e)

    admin_id = getattr(config, "ADMIN_TELEGRAM_ID", None)
    if not admin_id:
        logger.warning("ADMIN_NOTIFIER: ADMIN_TELEGRAM_ID not set — Telegram DM skipped")
        return
    try:
        from app.utils.telegram_safe import safe_send_message
        await safe_send_message(
            bot,
            int(admin_id),
            f"<b>{title}</b>\n{body}",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("admin_notifier telegram send failed: %s", e)


async def _on_payment_error(bot: Bot, e: dict[str, Any]) -> None:
    from app.services import admin_settings
    if not await admin_settings.is_enabled("payment_error"):
        return
    stage = str(e.get("stage") or "")
    provider = str(e.get("provider") or "")
    key = (stage, provider)
    if not _state.can_send_error(key):
        return
    body_parts = [
        f"{_fmt_provider(provider)} · {_fmt_stage(stage)}",
    ]
    tg = e.get("telegram_id")
    if isinstance(tg, int):
        body_parts.append(f"tg:{tg}")
    body = " · ".join(body_parts)
    await _send(
        bot,
        title="⚠️ Ошибка платежа",
        body=body,
        tag=f"payment_error:{stage}:{provider}",
        # /dashboard/payments only redirects since v4; link the screen
        # that actually shows payment health.
        url=await _dashboard_url("/dashboard/health"),
    )


async def _on_broadcast_done(bot: Bot, e: dict[str, Any]) -> None:
    from app.services import admin_settings
    if not await admin_settings.is_enabled("broadcast_done"):
        return
    bid = e.get("broadcast_id")
    sent = int(e.get("sent") or 0)
    failed = int(e.get("failed") or 0)
    total = int(e.get("total") or 0)
    body = f"Доставлено {sent}/{total}" + (f" · ошибок {failed}" if failed else "")
    await _send(
        bot,
        title=f"📣 Рассылка #{bid} завершена",
        body=body,
        tag=f"broadcast_done:{bid}",
        url=await _dashboard_url("/dashboard/broadcasts"),
    )


async def _on_payment_approved(bot: Bot, _e: dict[str, Any]) -> None:
    """Recompute today's revenue and announce any newly-crossed
    milestones. We pull fresh numbers from DB rather than aggregating
    here so the totals match the dashboard exactly."""
    from app.services import admin_settings
    if not await admin_settings.is_enabled("revenue_milestone"):
        logger.info("REVENUE_MILESTONE skipped: toggle revenue_milestone is off")
        return
    try:
        revenue_today = await revenue_since_msk_midnight()
    except Exception as e:
        logger.warning("milestone revenue lookup failed: %s", e)
        return

    day = _today_key()
    fired = await fired_milestones(day)
    crossed = [m for m in MILESTONES if revenue_today >= m and m not in fired]
    if not crossed:
        return
    await remember_milestones(day, fired | set(crossed))
    target = crossed[-1]
    phrase = random.choice(PHRASES.get(target, ["Ты молодец! 🦾"]))
    title = f"💸 {target:,} ₽ за день".replace(",", " ")
    logger.info(
        "REVENUE_MILESTONE fired=%s revenue=%.2f day=%s", target, revenue_today, day
    )
    await _send(
        bot,
        title=title,
        body=phrase,
        tag=f"revenue_milestone:{target}",
        url=await _dashboard_url("/dashboard/money"),
    )


async def run_admin_notifier(bot: Bot) -> None:
    """Long-lived task: subscribe to bus, fan-out to handlers."""
    q = bus.subscribe()
    logger.info("ADMIN_NOTIFIER started")
    try:
        while True:
            try:
                event = await q.get()
            except asyncio.CancelledError:
                break
            etype = event.get("type", "")
            try:
                if etype == "payment:error":
                    await _on_payment_error(bot, event)
                elif etype == "broadcast:done":
                    await _on_broadcast_done(bot, event)
                elif etype == "payment:approved":
                    await _on_payment_approved(bot, event)
            except Exception as e:
                logger.exception("ADMIN_NOTIFIER handler error: %s", e)
    finally:
        bus.unsubscribe(q)
        logger.info("ADMIN_NOTIFIER stopped")
