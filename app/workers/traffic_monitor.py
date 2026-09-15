"""
Background worker: bypass traffic notices.

Runs every 5 minutes. Gated by REMNAWAVE_ENABLED and DB_READY.

Owner rules (2026-09-14):
  * thresholds of the REMAINING bypass GB: 50, 30, 15, 10, 5, 3, 1 — only
    those strictly below the amount left at the last GB grant / top-up apply
    (a user with 20 GB left never gets «50» / «30»);
  * at most ONE message per check — for the lowest threshold crossed; the
    higher ones are marked with it (no cascade);
  * at least MIN_GAP (3 h) between two traffic messages to the same user;
  * more GB (a grant, a top-up) re-arms the thresholds below the new amount;
  * 0 GB: premium active → «обход исчерпан, основные серверы работают»; no
    premium → «доступ отключён, купите ГБ или подписку».

Panel polling is exactly as on production (owner: «keep it as it was»): the
same rows (database.get_active_remnawave_users), one get_user_traffic per row,
the same 0.2 s pacing. Only the notice rules changed.

State lives in users (migration 084): traffic_notice_floor_bytes (NULL = not
observed since the last grant; else thresholds >= floor are done) and
traffic_notice_last_at. Every change is a compare-and-set, claimed before the
send: two passes never tell the same threshold twice.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

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
REQUEST_PACING_S = 0.2  # between two per-user panel GETs (unchanged)

GB = 1024 ** 3
THRESHOLDS = tuple(t * GB for t in (50, 30, 15, 10, 5, 3, 1))
MIN_GAP = timedelta(hours=3)


@dataclass(frozen=True)
class Decision:
    new_floor: Optional[int]
    notice: Optional[int] = None     # threshold bytes to tell (0 = exhausted); None = store only


def decide(remaining: int, limit: int, floor: Optional[int], last_at: Optional[datetime],
           now: datetime, *, legacy_zero_told: bool = False) -> Optional[Decision]:
    """Pure: what to store / tell for one check (None = nothing at all)."""
    if limit <= 0:
        return None                                   # unlimited bypass: nothing to count down
    remaining = max(0, int(remaining))
    gap_ok = last_at is None or now - last_at >= MIN_GAP
    if floor is None or remaining > floor:
        # First check since a GB grant / top-up: the amount left is the baseline.
        if remaining > 0:
            return Decision(new_floor=remaining)
        if legacy_zero_told:                          # told «0» by the old worker already
            return Decision(new_floor=0)
        return Decision(new_floor=0, notice=0) if gap_ok else None
    crossed = [t for t in THRESHOLDS + (0,) if t < floor and remaining <= t]
    if not crossed or not gap_ok:
        return None
    target = min(crossed)
    return Decision(new_floor=target, notice=target)


def premium_active(row: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    exp = row.get("expires_at")
    return (not row.get("is_bypass_only") and (row.get("source") or "") != "bypass_only"
            and exp is not None and exp > now)


async def apply_check(bot: Bot, telegram_id: int, *, used: int, limit: int, premium: bool,
                      state: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Decide, claim (compare-and-set) and — only when claimed — send. True when
    a message was sent. Never raises."""
    now = now or datetime.now(timezone.utc)
    try:
        floor, last_at = state.get("floor"), state.get("last_at")
        d = decide(max(0, limit - used), limit, floor, last_at, now,
                   legacy_zero_told=bool(state.get("legacy_zero_told")))
        if d is None:
            return False
        new_last = now if d.notice is not None else last_at
        if not await database.claim_traffic_notice_state(telegram_id, floor, last_at, d.new_floor, new_last):
            return False
        if d.notice is None:
            return False
        await _send_traffic_notification(bot, telegram_id, max(0, limit - used), d.notice, premium=premium)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("TRAFFIC_CHECK_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


async def _check_user_traffic(bot: Bot, row: Dict[str, Any], now: datetime) -> str:
    """One check: one panel GET (as on production), the user's notice state
    (one short DB read — production read the flags here), then apply_check.
    Returns the outcome for the pass summary: sent / checked / skipped / no_data / error."""
    telegram_id = row["telegram_id"]
    # Prefer numeric id (3.x fast-path без UUID→id auto-resolve).
    # Fallback на uuid для legacy юзеров без забэкфильнутого id.
    panel_ref = row.get("remnawave_id") or row["remnawave_uuid"]
    try:
        traffic = await remnawave_api.get_user_traffic(panel_ref)
        if not traffic:
            logger.warning("TRAFFIC_CHECK_NO_DATA: tg=%s", telegram_id)
            return "no_data"
        limit = int(traffic.get("trafficLimitBytes") or 0)
        if limit <= 0:
            return "skipped"                               # unlimited: nothing to count down
        state = await database.get_traffic_notice_state(telegram_id)
        if state is None:
            return "skipped"
    except Exception as e:  # noqa: BLE001
        logger.warning("TRAFFIC_CHECK_ERROR: tg=%s %s: %s", telegram_id, type(e).__name__, e)
        return "error"
    sent = await apply_check(
        bot, telegram_id, used=int(traffic.get("usedTrafficBytes") or 0), limit=limit,
        premium=premium_active(row, now), state=state, now=now,
    )
    return "sent" if sent else "checked"


def _text_key(threshold: int, premium: bool) -> str:
    if threshold == 0:
        return "traffic.zero_premium" if premium else "traffic.zero_no_premium"
    if threshold >= 10 * GB:
        return "traffic.left_info"
    if threshold >= 3 * GB:
        return "traffic.left_warn"
    return "traffic.left_last"


async def _send_traffic_notification(bot: Bot, telegram_id: int, remaining_bytes: int, threshold: int,
                                     *, premium: bool) -> None:
    """Send one traffic notice (already claimed). Never raises."""
    try:
        from app.services.subscriptions.live_state import format_bytes
        language = await resolve_user_language(telegram_id)
        key = _text_key(threshold, premium)
        text = i18n_get_text(language, key, remaining=format_bytes(language, remaining_bytes))
        rows = [[InlineKeyboardButton(
            text=i18n_get_text(language, "traffic.buy_traffic_btn"),
            callback_data="buy_traffic",
        )]]
        if key == "traffic.zero_no_premium":
            rows.append([InlineKeyboardButton(
                text=i18n_get_text(language, "traffic.buy_subscription"),
                callback_data="menu_buy_vpn",
            )])
        # safe_send_message: 403 marks the user unreachable, a short 429 is
        # retried once. One try per threshold — no retries to a user who blocked the bot.
        sent = await safe_send_message(bot, telegram_id, text,
                                       reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        if sent:
            logger.info("TRAFFIC_NOTIFICATION_SENT: tg=%s threshold=%d remaining=%d", telegram_id, threshold, remaining_bytes)
        else:
            logger.warning("TRAFFIC_NOTIFICATION_NOT_DELIVERED: tg=%s threshold=%d", telegram_id, threshold)
    except Exception as e:  # noqa: BLE001
        logger.warning("TRAFFIC_NOTIFICATION_FAIL: tg=%s %s: %s", telegram_id, type(e).__name__, e)


async def traffic_monitor_iteration(bot: Bot) -> None:
    """Single iteration: check all active Remnawave users (as on production)."""
    users = await database.get_active_remnawave_users()
    if not users:
        return
    now = datetime.now(timezone.utc)
    started = time.monotonic()
    counts: Dict[str, int] = {}
    notified: list = []
    for row in users:
        outcome = await _check_user_traffic(bot, row, now)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "sent":
            notified.append(row["telegram_id"])
        await asyncio.sleep(REQUEST_PACING_S)  # Rate limit API calls
    # One line per pass (the per-user panel GETs are filtered out of the log).
    logger.info(
        "TRAFFIC_MONITOR_PASS: users=%d sent=%d checked=%d skipped=%d no_data=%d errors=%d "
        "duration_s=%d notified_tg=%s",
        len(users), counts.get("sent", 0), counts.get("checked", 0), counts.get("skipped", 0),
        counts.get("no_data", 0), counts.get("error", 0), int(time.monotonic() - started),
        ",".join(str(t) for t in notified[:50]) + (f",…+{len(notified) - 50}" if len(notified) > 50 else ""),
    )


async def traffic_monitor_task(bot: Bot) -> None:
    """Main loop — runs every INTERVAL_SECONDS."""
    logger.info("TRAFFIC_MONITOR: starting (interval=%ds)", INTERVAL_SECONDS)
    from app.core import runtime_health  # dashboard liveness (in-memory)
    # One iteration walks every user with a panel UUID at 0.2 s per call,
    # so it can run long: a generous interval keeps "stale" meaningful.
    runtime_health.register("traffic_monitor", interval_s=INTERVAL_SECONDS + 1800, initial_delay_s=30)
    await asyncio.sleep(30)  # Initial delay

    while True:
        try:
            if not database.DB_READY or not config.REMNAWAVE_ENABLED:
                runtime_health.record("traffic_monitor", "skipped")
                await asyncio.sleep(INTERVAL_SECONDS)
                continue

            await traffic_monitor_iteration(bot)
            runtime_health.beat("traffic_monitor")
        except asyncio.CancelledError:
            logger.info("TRAFFIC_MONITOR: cancelled")
            break
        except Exception as e:
            logger.error("TRAFFIC_MONITOR_ERROR: %s: %s", type(e).__name__, e)
            runtime_health.fail("traffic_monitor", e)

        await asyncio.sleep(INTERVAL_SECONDS)
