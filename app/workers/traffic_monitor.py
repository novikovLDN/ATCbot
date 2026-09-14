"""
Background worker: check traffic usage and send threshold notifications.

Runs every 5 minutes. Gated by REMNAWAVE_ENABLED and DB_READY.

One pass = the DB rows to check + ONE paged read of the panel
(`GET /api/users/stream`, pages of PAGE_SIZE) — never a GET per user. Only the
used/limit bytes of the rows we check are kept; pages are dropped as they are
read. A failed stream skips the pass (no partial data); passes never overlap.
"""
import asyncio
import logging
import time
from typing import Dict, Optional, Tuple

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
import database
from app.services import remnawave_api
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language
from app.utils.telegram_safe import safe_send_message

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # 5 minutes
PAGE_SIZE = 500         # users per stream page (panel max 1000)
PAGE_DELAY_S = 0.5      # pause between pages: no burst into the panel rate limit
STREAM_MAX_RETRIES = 3  # attempts per page (the only retry layer: _request has none)

_pass_lock = asyncio.Lock()


def _format_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} ГБ"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} МБ"
    return f"{b / 1024:.0f} КБ"


def _pick_threshold(remaining: int, limit: int, flags: Dict[str, bool]) -> Optional[str]:
    """The flag of the notice to send now, or None. One notice per pass."""
    for threshold_bytes, flag_key in config.TRAFFIC_NOTIFY_THRESHOLDS:
        # Порог должен быть строго меньше лимита юзера — иначе он
        # триггерится СРАЗУ после активации: у trial'а лимит 500 МБ,
        # но пороги 8/5/3/1 ГБ все ≥ 500 МБ, поэтому за первые
        # 30 минут летели 6 уведомлений подряд с текстом «купите
        # дополнительный трафик». Строгое неравенство также
        # гарантирует что порог 500 МБ не сработает для юзера
        # с лимитом ровно 500 МБ на моменте активации.
        # Особый случай: порог 0 ГБ (закончился трафик) — всегда
        # актуален, любой юзер должен узнать что доступ отключился.
        if threshold_bytes > 0 and threshold_bytes >= limit:
            continue
        if remaining <= threshold_bytes and not flags.get(flag_key, False):
            return flag_key
    return None


async def _check_user_traffic(bot: Bot, telegram_id: int, used: int, limit: int) -> bool:
    """Check traffic thresholds and send one-shot notifications. True if one was sent."""
    try:
        if limit <= 0:
            return False
        remaining = max(0, limit - used)
        # No threshold can fire even with every flag unset → skip the DB read.
        if _pick_threshold(remaining, limit, {}) is None:
            return False

        flags = await database.get_traffic_notification_flags(telegram_id)
        if not flags:
            return False

        flag_key = _pick_threshold(remaining, limit, flags)
        if flag_key is None:
            return False
        await _send_traffic_notification(bot, telegram_id, remaining, flag_key)
        await database.set_traffic_notification_flag(telegram_id, flag_key)
        return True
    except Exception as e:
        logger.warning("TRAFFIC_CHECK_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


async def _send_traffic_notification(
    bot: Bot,
    telegram_id: int,
    remaining_bytes: int,
    flag_key: str,
) -> None:
    """Send traffic warning notification to user."""
    try:
        language = await resolve_user_language(telegram_id)

        if flag_key == "traffic_notified_0":
            text = i18n_get_text(language, "traffic.notify_zero")
        elif flag_key == "traffic_notified_500mb":
            text = i18n_get_text(language, "traffic.notify_500mb", remaining=_format_bytes(remaining_bytes))
        elif flag_key == "traffic_notified_1gb":
            text = i18n_get_text(language, "traffic.notify_1gb")
        elif flag_key == "traffic_notified_3gb":
            text = i18n_get_text(language, "traffic.notify_3gb", remaining=_format_bytes(remaining_bytes))
        elif flag_key == "traffic_notified_5gb":
            text = i18n_get_text(language, "traffic.notify_5gb", remaining=_format_bytes(remaining_bytes))
        else:
            text = i18n_get_text(language, "traffic.notify_8gb", remaining=_format_bytes(remaining_bytes))

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_traffic_btn"),
                callback_data="buy_traffic",
            )],
        ])
        # safe_send_message: 403 marks the user unreachable, a short 429 is
        # retried once. The caller still sets the flag after the attempt (one
        # try per threshold — no per-5-min retries to a user who blocked the bot).
        sent = await safe_send_message(bot, telegram_id, text, reply_markup=kb, parse_mode="HTML")
        if sent:
            logger.info("TRAFFIC_NOTIFICATION_SENT: tg=%s flag=%s remaining=%d", telegram_id, flag_key, remaining_bytes)
        else:
            logger.warning("TRAFFIC_NOTIFICATION_NOT_DELIVERED: tg=%s flag=%s", telegram_id, flag_key)
    except Exception as e:
        logger.warning("TRAFFIC_NOTIFICATION_FAIL: tg=%s %s: %s", telegram_id, type(e).__name__, e)


def _used_limit(entity: dict) -> Tuple[int, int]:
    """Same fields get_user_traffic reads (3.4.3: usage only in userTraffic)."""
    user_traffic = entity.get("userTraffic") or {}
    used = user_traffic.get("usedTrafficBytes", entity.get("usedTrafficBytes", 0))
    return int(used or 0), int(entity.get("trafficLimitBytes", 0) or 0)


async def _read_panel_traffic(
    want_ids: set, want_uuids: set,
) -> Optional[Tuple[Dict[int, Tuple[int, int]], Dict[str, Tuple[int, int]]]]:
    """One stream pass → (by numeric id, by vlessUuid) for the wanted entities
    only. None if the stream failed (the caller skips the pass)."""
    by_id: Dict[int, Tuple[int, int]] = {}
    by_uuid: Dict[str, Tuple[int, int]] = {}

    def on_page(batch) -> None:
        for ent in batch:
            if not isinstance(ent, dict):
                continue
            try:
                pid = int(ent.get("id"))
            except (TypeError, ValueError):
                pid = None
            if pid is not None and pid in want_ids:
                by_id[pid] = _used_limit(ent)
            for key in ("vlessUuid", "uuid"):
                value = ent.get(key)
                if value and str(value) in want_uuids:
                    by_uuid[str(value)] = _used_limit(ent)

    result = await remnawave_api.get_all_users(
        page_size=PAGE_SIZE, page_delay=PAGE_DELAY_S,
        max_retries=STREAM_MAX_RETRIES, on_page=on_page,
    )
    if result is None:
        return None
    return by_id, by_uuid


async def traffic_monitor_iteration(bot: Bot) -> bool:
    """Single pass over all active Remnawave users. False = skipped (a pass is
    already running, or the panel stream failed)."""
    if _pass_lock.locked():
        logger.warning("TRAFFIC_MONITOR_SKIPPED: previous pass still running")
        return False
    async with _pass_lock:
        return await _run_pass(bot)


async def _run_pass(bot: Bot) -> bool:
    started = time.monotonic()
    users = await database.get_active_remnawave_users()
    if not users:
        return True

    # Prefer numeric id (3.x); legacy rows without a backfilled id → vlessUuid.
    want_ids = {int(u["remnawave_id"]) for u in users if u.get("remnawave_id")}
    want_uuids = {str(u["remnawave_uuid"]) for u in users if not u.get("remnawave_id") and u.get("remnawave_uuid")}
    panel = await _read_panel_traffic(want_ids, want_uuids)
    if panel is None:
        logger.warning("TRAFFIC_MONITOR_STREAM_FAILED: panel stream unavailable, pass skipped (rows=%d)", len(users))
        return False
    by_id, by_uuid = panel

    missing = notified = 0
    for user in users:
        rid = user.get("remnawave_id")
        traffic = by_id.get(int(rid)) if rid else by_uuid.get(str(user.get("remnawave_uuid")))
        if traffic is None:
            missing += 1
            continue
        if await _check_user_traffic(bot, user["telegram_id"], *traffic):
            notified += 1

    logger.info(
        "TRAFFIC_MONITOR_PASS: rows=%d matched=%d missing=%d notified=%d duration_ms=%d",
        len(users), len(users) - missing, missing, notified, (time.monotonic() - started) * 1000,
    )
    return True


async def traffic_monitor_task(bot: Bot) -> None:
    """Main loop — one pass, then INTERVAL_SECONDS of sleep (never overlapping)."""
    logger.info("TRAFFIC_MONITOR: starting (interval=%ds)", INTERVAL_SECONDS)
    from app.core import runtime_health  # dashboard liveness (in-memory)
    # One pass streams the whole panel in pages of PAGE_SIZE with a pause
    # between pages, so it can run for minutes: a generous interval keeps
    # "stale" meaningful.
    runtime_health.register("traffic_monitor", interval_s=INTERVAL_SECONDS + 1800, initial_delay_s=30)
    await asyncio.sleep(30)  # Initial delay

    while True:
        try:
            if not database.DB_READY or not config.REMNAWAVE_ENABLED:
                runtime_health.record("traffic_monitor", "skipped")
                await asyncio.sleep(INTERVAL_SECONDS)
                continue

            if await traffic_monitor_iteration(bot):
                runtime_health.beat("traffic_monitor")
            else:
                runtime_health.record("traffic_monitor", "skipped")
        except asyncio.CancelledError:
            logger.info("TRAFFIC_MONITOR: cancelled")
            break
        except Exception as e:
            logger.error("TRAFFIC_MONITOR_ERROR: %s: %s", type(e).__name__, e)
            runtime_health.fail("traffic_monitor", e)

        await asyncio.sleep(INTERVAL_SECONDS)
