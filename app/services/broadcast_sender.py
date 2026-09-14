"""
Reusable broadcast sender.

Extracted as a standalone async function so both the in-bot admin
wizard and the web dashboard can dispatch a broadcast without
duplicating the batched / semaphored / retried delivery code.

The in-bot admin broadcast wizard was removed (2026-09-14); the web
dashboard (and scheduled broadcasts) are the only callers.

Publishes bus events so dashboard subscribers see live progress:
  - broadcast:progress {broadcast_id, processed, total, sent, failed}
  - broadcast:done     {broadcast_id, sent, failed, total}
  - broadcast:failed   {broadcast_id, error}
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import random
import time
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

import database
from app.events import bus
from app.utils.telegram_safe import convert_tg_emoji, safe_send_message

logger = logging.getLogger(__name__)

# Production broadcast: controlled concurrency, rate limiting, event-loop safe
BROADCAST_CONCURRENCY = 15          # Safe under Telegram 30 msg/sec
BROADCAST_BATCH_SIZE = 200          # Soft batch limit
BROADCAST_BATCH_PAUSE = 2           # Seconds between batches
BROADCAST_RETRY_LIMIT = 3           # Retry per user

# TG-RT-10 (docs/audit/11_telegram_runtime.md): Telegram allows ~30 messages
# per second across all chats. Concurrency alone does not bound the RATE (a
# fast Bot API lets every slot send again at once). Every send, retries
# included, takes the next free slot of 1 / BROADCAST_MAX_PER_SEC seconds.
BROADCAST_MAX_PER_SEC = 25
_clock = time.monotonic             # tests patch it


class _Pacer:
    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = _clock()
            if self._next_at > now:
                await asyncio.sleep(self._next_at - now)
                now = self._next_at
            self._next_at = now + self._interval


async def _safe_send_with_buttons(
    bot: Bot,
    user_id: int,
    text: str,
    semaphore: asyncio.Semaphore,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo_file_id: str | None = None,
    animation_file_id: str | None = None,
    caption: str | None = None,
    pacer: "_Pacer | None" = None,
) -> int | None:
    """Send message with optional inline buttons.

    Приоритет media:
      1) animation_file_id (GIF/MP4) → send_animation
      2) photo_file_id → send_photo
      3) plain text → send_message

    Returns message_id on success, None on failure.

    HOW_IT_WORKS P2: text goes through safe_send_message (a blocked / deleted
    chat is marked users.is_reachable = FALSE, no retry); media sends apply the
    same rule. Flood waits are still slept out and retried.
    """
    async with semaphore:
        for attempt in range(BROADCAST_RETRY_LIMIT):
            if pacer is not None:
                await pacer.wait()
            try:
                if animation_file_id:
                    result = await bot.send_animation(
                        user_id,
                        animation=animation_file_id,
                        caption=convert_tg_emoji(caption or text),
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                elif photo_file_id:
                    result = await bot.send_photo(
                        user_id,
                        photo=photo_file_id,
                        caption=convert_tg_emoji(caption or text),
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    msg = await safe_send_message(
                        bot, user_id, text, reply_markup=reply_markup, parse_mode="HTML",
                        raise_retry_after=True,
                    )
                    return msg.message_id if msg else None
                return result.message_id
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                await _mark_unreachable(user_id)
                return None
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower():
                    await _mark_unreachable(user_id)
                    return None
                await asyncio.sleep(1)
            except Exception:
                await asyncio.sleep(1)
        return None


async def _mark_unreachable(user_id: int) -> None:
    try:
        await database.mark_user_unreachable(user_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("BROADCAST_MARK_UNREACHABLE_FAILED user=%s: %s", user_id, e)


async def send_broadcast(
    *,
    bot: Bot,
    broadcast_id: int,
    user_ids: list[int],
    message: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo_file_id: Optional[str] = None,
    animation_file_id: Optional[str] = None,
    is_ab_test: bool = False,
    message_a: Optional[str] = None,
    message_b: Optional[str] = None,
    admin_telegram_id: Optional[int] = None,
) -> dict:
    """Send to every uid in user_ids. Returns final stats dict.

    Supports the same {bypass_key} substitution as the bot wizard,
    and the same A/B variant split. Media priority:
      animation_file_id (GIF/MP4) > photo_file_id > plain text.
    caption = message (для photo/animation).
    """
    from app.services.user_subscription_links import get_user_bypass_url

    total = len(user_ids)
    semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
    pacer = _Pacer(BROADCAST_MAX_PER_SEC)
    sent_count = 0
    failed_count = 0
    processed = 0
    has_animation = animation_file_id is not None
    has_photo = photo_file_id is not None and not has_animation

    async def _send_one(
        uid: int,
        msg: str,
        variant: Optional[str],
        p_fid: Optional[str],
        a_fid: Optional[str],
        cap: Optional[str],
    ):
        needs_key = "{bypass_key}" in (msg or "") or "{bypass_key}" in (cap or "")
        if needs_key:
            try:
                bypass_url = await get_user_bypass_url(uid)
            except Exception:
                bypass_url = None
            if not bypass_url:
                return (uid, variant, None)
            safe_url = _html.escape(bypass_url, quote=False)
            if msg:
                msg = msg.replace("{bypass_key}", safe_url)
            if cap:
                cap = cap.replace("{bypass_key}", safe_url)
        msg_id = await _safe_send_with_buttons(
            bot, uid, msg, semaphore,
            reply_markup=reply_markup,
            photo_file_id=p_fid,
            animation_file_id=a_fid,
            caption=cap,
            pacer=pacer,
        )
        return (uid, variant, msg_id)

    try:
        for i in range(0, total, BROADCAST_BATCH_SIZE):
            batch = user_ids[i:i + BROADCAST_BATCH_SIZE]
            items = []
            for uid in batch:
                if is_ab_test and message_a and message_b:
                    variant = "A" if random.random() < 0.5 else "B"
                    msg_for_user = message_a if variant == "A" else message_b
                    items.append((uid, msg_for_user, variant, None, None, None))
                else:
                    if has_animation:
                        items.append((uid, message, None, None, animation_file_id, message))
                    elif has_photo:
                        items.append((uid, message, None, photo_file_id, None, message))
                    else:
                        items.append((uid, message, None, None, None, None))

            tasks = [_send_one(uid, m, v, p, a, c) for uid, m, v, p, a, c in items]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    failed_count += 1
                    logger.warning(
                        "BROADCAST_TASK_ERROR broadcast_id=%s err=%s",
                        broadcast_id, r,
                    )
                    continue
                uid, v, msg_id = r
                if msg_id:
                    sent_count += 1
                    try:
                        await database.log_broadcast_send(
                            broadcast_id, uid, "sent", v, message_id=msg_id,
                        )
                    except Exception:
                        pass
                else:
                    failed_count += 1
                    try:
                        await database.log_broadcast_send(
                            broadcast_id, uid, "failed", v,
                        )
                    except Exception:
                        pass

            processed += len(batch)
            bus.publish({
                "type": "broadcast:progress",
                "broadcast_id": broadcast_id,
                "processed": processed,
                "total": total,
                "sent": sent_count,
                "failed": failed_count,
            })
            logger.info(
                "BROADCAST_PROGRESS broadcast_id=%s processed=%s/%s sent=%s failed=%s",
                broadcast_id, processed, total, sent_count, failed_count,
            )
            if i + BROADCAST_BATCH_SIZE < total:
                await asyncio.sleep(BROADCAST_BATCH_PAUSE)

        bus.publish({
            "type": "broadcast:done",
            "broadcast_id": broadcast_id,
            "sent": sent_count,
            "failed": failed_count,
            "total": total,
        })
        try:
            await database._log_audit_event_atomic_standalone(
                "broadcast_sent",
                admin_telegram_id,
                None,
                f"Broadcast ID: {broadcast_id}, "
                f"Sent: {sent_count}, Failed: {failed_count}",
            )
        except Exception:
            pass
        return {"sent": sent_count, "failed": failed_count, "total": total}

    except Exception as e:
        logger.exception(
            "BROADCAST_SEND_FATAL broadcast_id=%s err=%s", broadcast_id, e,
        )
        bus.publish({
            "type": "broadcast:failed",
            "broadcast_id": broadcast_id,
            "error": f"{type(e).__name__}: {e}",
        })
        raise
