"""
Reusable broadcast sender.

Extracted as a standalone async function so both the in-bot admin
wizard and the web dashboard can dispatch a broadcast without
duplicating the batched / semaphored / retried delivery code.

The bot wizard in app/handlers/admin/broadcast.py still has its own
inline closure (untouched) — we leave it alone to avoid risk;
the dashboard path uses this function exclusively. Long-term they
should converge.

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
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

import database
from app.events import bus
from app.handlers.admin.broadcast import (
    BROADCAST_CONCURRENCY,
    BROADCAST_BATCH_SIZE,
    BROADCAST_BATCH_PAUSE,
    _safe_send_with_buttons,
)

logger = logging.getLogger(__name__)


async def send_broadcast(
    *,
    bot: Bot,
    broadcast_id: int,
    user_ids: list[int],
    message: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo_file_id: Optional[str] = None,
    is_ab_test: bool = False,
    message_a: Optional[str] = None,
    message_b: Optional[str] = None,
    admin_telegram_id: Optional[int] = None,
) -> dict:
    """Send to every uid in user_ids. Returns final stats dict.

    Supports the same {bypass_key} substitution as the bot wizard,
    and the same A/B variant split. Photo + caption path is used when
    photo_file_id is set (caption = message).
    """
    from app.services.user_subscription_links import get_user_bypass_url

    total = len(user_ids)
    semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
    sent_count = 0
    failed_count = 0
    processed = 0
    has_photo = photo_file_id is not None

    async def _send_one(
        uid: int,
        msg: str,
        variant: Optional[str],
        p_fid: Optional[str],
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
            photo_file_id=p_fid, caption=cap,
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
                    items.append((uid, msg_for_user, variant, None, None))
                else:
                    if has_photo:
                        items.append((uid, message, None, photo_file_id, message))
                    else:
                        items.append((uid, message, None, None, None))

            tasks = [_send_one(uid, m, v, p, c) for uid, m, v, p, c in items]
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
