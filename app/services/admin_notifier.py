"""
Admin Telegram notifications driven by app.events.bus.

Sends DMs to ADMIN_TELEGRAM_ID on:
  - payment:error           — throttled (≤ 1 msg/min per stage+provider)
  - broadcast:done          — every completion
  - revenue milestone hits  — when today's gross crosses 5/10/15/20/
                              25/30/35k RUB. Each milestone fires
                              once per UTC day, with a randomised
                              short congrat phrase.

Runs as a long-lived asyncio task started from main.py. Subscribes
to the bus and forwards events to the bot. Survives bus stalls and
Telegram failures (logged, continues).
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

import config
from app.events import bus

logger = logging.getLogger(__name__)


# ── Revenue milestones — ascending order, in rubles ────────────────
MILESTONES = [5_000, 10_000, 15_000, 20_000, 25_000, 30_000, 35_000]

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
}


def _today_key() -> str:
    """UTC date key for milestone bookkeeping."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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

    def milestones_to_fire(self, revenue_now: float) -> list[int]:
        """Return milestones the current daily revenue has just crossed
        for the first time today (in ascending order, possibly multi
        if a single payment leapfrogged several)."""
        day = _today_key()
        fired = self.fired_milestones.setdefault(day, set())
        # Forget older days to keep the dict small over weeks.
        for k in list(self.fired_milestones.keys()):
            if k != day:
                del self.fired_milestones[k]
        out = []
        for m in MILESTONES:
            if revenue_now >= m and m not in fired:
                fired.add(m)
                out.append(m)
        return out


_state = _State()


async def _dashboard_url(path: str) -> str:
    base = getattr(config, "DASHBOARD_BASE_URL", "") or ""
    return base.rstrip("/") + path


async def _send(bot: Bot, *, title: str, body: str, tag: str, url: str) -> None:
    """Primary delivery is browser web-push (system notifications).
    Telegram DM is intentionally NOT used here — admin opted into web
    push as the channel. The /settings → "тестовые в Telegram" button
    still uses the bot directly for the dry-run."""
    from app.services import push_notifications
    try:
        await push_notifications.send_to_all(
            title=title, body=body, tag=tag, url=url,
        )
    except Exception as e:
        logger.warning("admin_notifier push send failed: %s", e)


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
        url=await _dashboard_url("/dashboard/payments"),
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
        return
    try:
        import database
        # 24h trailing != today; we want today-since-midnight-UTC.
        # The /payments/revenue endpoint uses trailing-N-hours, so we
        # use the same get_revenue_for_period but with hours computed
        # from how far we are into the current UTC day.
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_into_day = max(1, (now - midnight).total_seconds())
        hours_since_midnight = max(1, int(round(seconds_into_day / 3600)))
        data = await database.get_revenue_for_period(hours_since_midnight)
        revenue_today = float(data.get("revenue_rubles") or 0)
    except Exception as e:
        logger.warning("milestone revenue lookup failed: %s", e)
        return

    crossed = _state.milestones_to_fire(revenue_today)
    if not crossed:
        return
    target = crossed[-1]
    phrase = random.choice(PHRASES.get(target, ["Ты молодец! 🦾"]))
    title = f"💸 {target:,} ₽ за день".replace(",", " ")
    await _send(
        bot,
        title=title,
        body=phrase,
        tag=f"revenue_milestone:{target}",
        url=await _dashboard_url("/dashboard/payments"),
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
