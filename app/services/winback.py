"""
Winback "2-day gift + 20% discount" campaign.

Admin-triggered (not scheduled). Targets users whose paid VPN
subscription expired in the last 3 days AND whose remaining bypass
traffic is ≤1 GB (queried per-user via the Remnawave API; on API
failure the user is included rather than silently dropped, so a down
panel doesn't shrink the cohort invisibly).

For each surviving candidate, in order:

  1. ``grant_access(source='gift', tariff='basic', duration=2d)``
     — extends/creates the subscription with a fresh 2-day window.
  2. ``create_user_discount(20%, expires_at=now+7d)``
     — overwrites any existing personal discount.
  3. ``send_message`` — emotional notification with a 🎁 button.
  4. ``mark_winback_2d_sent`` — set the dedup flag so re-runs skip them.

Steps 1-4 are per-user atomic: a transient failure on one user (VPN API
down, telegram blocked, panel timeout) does NOT block the rest, and the
dedup flag is only set when the notification was successfully delivered
— so retries pick the user back up later.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import (
    TelegramRetryAfter,
    TelegramBadRequest,
    TelegramForbiddenError,
)

import config
import database
from database.subscriptions import grant_access
from app.services import remnawave_api
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

logger = logging.getLogger(__name__)

# Bypass-balance threshold: target users with ≤1 GB remaining.
BYPASS_REMAIN_LIMIT_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB

GIFT_DAYS = 2
GIFT_TARIFF = "basic"
DISCOUNT_PERCENT = 20
DISCOUNT_VALID_DAYS = 7

# How aggressively to parallelise the Remnawave bypass-balance fetches.
# Each candidate = one HTTP call to the panel; keep this conservative so
# we don't blow the panel's per-IP rate limits during a large run.
BYPASS_CHECK_CONCURRENCY = 8

# How aggressively to parallelise the per-user gift+discount+send work.
# Telegram global is ~30 msg/sec; the 0.07s sleep below keeps us under
# that even at concurrency 7.
SEND_CONCURRENCY = 7
PER_MESSAGE_SLEEP = 0.07


# Sentinel: "effectively unlimited" remaining bytes.  Used when the panel
# reports trafficLimitBytes=0 (no limit configured) — these users have
# plenty of bypass and should be EXCLUDED from the winback cohort.
_BYPASS_EFFECTIVELY_UNLIMITED = 1 << 62


async def _remaining_bypass_bytes(rmn_uuid: Optional[str]) -> Optional[int]:
    """Read the user's remaining bypass-traffic balance from Remnawave.

    Returns:
        - 0 if the user has no Remnawave UUID (never had bypass — counts
          as "low balance" so they're in the cohort)
        - ``_BYPASS_EFFECTIVELY_UNLIMITED`` if Remnawave reports the user
          has no traffic limit at all (i.e. unlimited bypass — exclude)
        - remaining bytes (limit − used) if the panel returned a real record
        - ``None`` if the panel returned nothing or the call failed —
          caller treats this as "include the user" (fail-open), because
          we'd rather send a winback to someone with a full tank than
          silently drop them when the panel is having a bad day.
    """
    if not rmn_uuid:
        return 0
    if not config.REMNAWAVE_ENABLED:
        return 0
    try:
        user_data = await remnawave_api.get_user(rmn_uuid)
    except Exception as e:
        logger.warning("WINBACK_BYPASS_FETCH_ERROR uuid=%s err=%s", rmn_uuid[:8], e)
        return None
    if not user_data:
        return 0
    limit = user_data.get("trafficLimitBytes") or 0
    used = user_data.get("usedTrafficBytes") or 0
    if limit <= 0:
        return _BYPASS_EFFECTIVELY_UNLIMITED
    remaining = limit - used
    return max(0, remaining)


async def filter_by_bypass(
    candidates: List[Dict[str, Any]],
    *,
    max_remaining_bytes: int = BYPASS_REMAIN_LIMIT_BYTES,
    concurrency: int = BYPASS_CHECK_CONCURRENCY,
) -> List[Dict[str, Any]]:
    """Keep only candidates whose remaining bypass traffic is ≤ ``max_remaining_bytes``.

    Runs the Remnawave fetches concurrently with a semaphore.  Candidates
    where the bypass-balance lookup returned a value ABOVE the limit are
    dropped; candidates where it returned None (lookup failed OR panel
    reports unlimited) are kept — we'd rather over-include than under.
    """
    if not candidates:
        return []
    semaphore = asyncio.Semaphore(concurrency)
    kept: List[Dict[str, Any]] = []
    kept_lock = asyncio.Lock()

    async def check_one(c: Dict[str, Any]) -> None:
        async with semaphore:
            remaining = await _remaining_bypass_bytes(c.get("remnawave_uuid"))
        # remaining == None  → include (fail-open)
        # remaining is int   → include iff <= limit
        include = remaining is None or remaining <= max_remaining_bytes
        if include:
            async with kept_lock:
                kept.append({**c, "_bypass_remaining_bytes": remaining})

    await asyncio.gather(*(check_one(c) for c in candidates))
    return kept


async def preview_winback_audience() -> Dict[str, Any]:
    """Dry-run: report how many users the campaign would touch right now,
    broken down by the filter stages.  Does NOT send anything, does NOT
    grant anything, does NOT mark anyone."""
    raw = await database.get_winback_2d_candidates(lookback_days=3)
    filtered = await filter_by_bypass(raw)
    return {
        "raw_count": len(raw),
        "filtered_count": len(filtered),
        "dropped_by_bypass": len(raw) - len(filtered),
    }


async def _grant_winback_bonus(telegram_id: int, admin_telegram_id: int) -> Dict[str, Any]:
    """Grant the 2-day basic subscription + 20% discount valid 7 days.

    Raises on failure; caller catches per-user.
    """
    grant_result = await grant_access(
        telegram_id=telegram_id,
        duration=timedelta(days=GIFT_DAYS),
        source="gift",
        tariff=GIFT_TARIFF,
    )
    discount_expires_at = datetime.now(timezone.utc) + timedelta(days=DISCOUNT_VALID_DAYS)
    await database.create_user_discount(
        telegram_id=telegram_id,
        discount_percent=DISCOUNT_PERCENT,
        expires_at=discount_expires_at,
        created_by=admin_telegram_id,
    )
    return grant_result


async def _send_winback_notification(bot: Bot, telegram_id: int, language: str) -> bool:
    """Send the winback notification.  Returns True on delivery, False otherwise.

    On ``TelegramRetryAfter`` we honour the panel's backoff and retry
    once.  On "chat not found" / "blocked" we mark the user unreachable
    so future workers skip them.  On any other Telegram error we give up
    quietly — the dedup flag stays unset, so the next campaign run
    will retry."""
    text = i18n_get_text(language, "winback.notif_text")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=i18n_get_text(language, "winback.claim_button"),
            callback_data="menu_buy_vpn",
        )],
    ])

    async def _send_once() -> bool:
        try:
            await bot.send_message(telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
            return True
        except TelegramForbiddenError:
            logger.warning("WINBACK_SEND_FORBIDDEN user=%s", telegram_id)
            try:
                await database.mark_user_unreachable(telegram_id)
            except Exception:
                pass
            return False
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "chat not found" in err:
                logger.warning("WINBACK_SEND_CHAT_NOT_FOUND user=%s", telegram_id)
                try:
                    await database.mark_user_unreachable(telegram_id)
                except Exception:
                    pass
            else:
                logger.warning("WINBACK_SEND_BAD_REQUEST user=%s %s", telegram_id, e)
            return False

    try:
        return await _send_once()
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            return await _send_once()
        except Exception as e2:
            logger.warning("WINBACK_SEND_RETRY_FAILED user=%s err=%s", telegram_id, e2)
            return False
    except Exception as e:
        logger.warning("WINBACK_SEND_FAILED user=%s %s: %s", telegram_id, type(e).__name__, e)
        return False


async def run_winback_2d_campaign(
    bot: Bot,
    admin_telegram_id: int,
) -> Dict[str, Any]:
    """Execute the campaign end-to-end.

    Pipeline:
      1. Pull raw candidates (status='expired' in last 3 days, no winback flag)
      2. Filter by Remnawave bypass-balance ≤ 1 GB
      3. For each survivor (concurrently, rate-limited):
            grant gift → grant discount → send message → set dedup flag

    Never raises.  Returns a stats dict suitable for admin reporting.
    """
    started_at = time.time()
    stats = {
        "raw_candidates": 0,
        "after_bypass_filter": 0,
        "gift_failed": 0,
        "send_failed": 0,
        "delivered": 0,
        "duration_seconds": 0.0,
    }

    if not database.DB_READY:
        logger.warning("WINBACK_SKIP db_not_ready")
        stats["duration_seconds"] = time.time() - started_at
        return stats

    try:
        raw = await database.get_winback_2d_candidates(lookback_days=3)
    except Exception as e:
        logger.exception("WINBACK_CANDIDATES_FETCH_ERROR %s", e)
        stats["duration_seconds"] = time.time() - started_at
        return stats
    stats["raw_candidates"] = len(raw)

    if not raw:
        stats["duration_seconds"] = time.time() - started_at
        logger.info("WINBACK_NO_CANDIDATES")
        return stats

    survivors = await filter_by_bypass(raw)
    stats["after_bypass_filter"] = len(survivors)

    if not survivors:
        stats["duration_seconds"] = time.time() - started_at
        logger.info("WINBACK_NO_SURVIVORS_AFTER_BYPASS_FILTER raw=%d", len(raw))
        return stats

    semaphore = asyncio.Semaphore(SEND_CONCURRENCY)
    counters_lock = asyncio.Lock()

    async def process_one(user: Dict[str, Any]) -> None:
        telegram_id = user["telegram_id"]
        async with semaphore:
            # Step 1+2: gift + discount (must succeed before we promise
            # them anything in the notification).  On failure: skip the
            # user entirely — no dedup flag, no message — so the next
            # run picks them up again.
            try:
                await _grant_winback_bonus(telegram_id, admin_telegram_id)
            except Exception as e:
                logger.exception("WINBACK_GRANT_FAILED user=%s %s", telegram_id, e)
                async with counters_lock:
                    stats["gift_failed"] += 1
                return

            # CRITICAL: set the dedup flag *before* the send attempt.
            # The gift+discount are already in the user's account; even
            # if the message never lands (blocked, deleted chat), we
            # MUST NOT grant them another 2 days on the next campaign
            # run.  The next run would just extend an already-active sub
            # and overwrite their discount silently.
            try:
                await database.mark_winback_2d_sent(telegram_id)
            except Exception as e:
                logger.warning("WINBACK_MARK_SENT_FAILED user=%s %s", telegram_id, e)

            # Step 3: notification.
            try:
                language = await resolve_user_language(telegram_id)
            except Exception:
                language = "ru"
            sent = await _send_winback_notification(bot, telegram_id, language)
            await asyncio.sleep(PER_MESSAGE_SLEEP)
            async with counters_lock:
                if sent:
                    stats["delivered"] += 1
                else:
                    stats["send_failed"] += 1

    try:
        await asyncio.gather(*(process_one(u) for u in survivors))
    except asyncio.CancelledError:
        logger.info("WINBACK_CANCELLED stats_partial=%s", stats)
        raise

    stats["duration_seconds"] = time.time() - started_at
    logger.info(
        "WINBACK_CAMPAIGN_DONE raw=%d after_filter=%d delivered=%d gift_failed=%d send_failed=%d duration=%.1fs",
        stats["raw_candidates"], stats["after_bypass_filter"],
        stats["delivered"], stats["gift_failed"], stats["send_failed"],
        stats["duration_seconds"],
    )
    return stats
