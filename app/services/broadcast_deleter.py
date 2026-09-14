"""Delete a broadcast's messages from every user's chat.

Telegram lets a bot delete its own messages in a private chat within
48 hours of sending (and removed certain old-message restrictions in
late 2024). Older messages will fail with TelegramBadRequest, which we
count as `failed` and move on — the row stays in broadcast_log so a
later run could try again, but we don't auto-retry.

Publishes bus events:
  broadcast:delete_progress {broadcast_id, processed, total, deleted, failed}
  broadcast:delete_done     {broadcast_id, deleted, failed, total}
  broadcast:delete_failed   {broadcast_id, error}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot

# Running task registry — broadcast_id → asyncio.Task. Allows the
# /broadcasts/{id}/delete-from-users/cancel endpoint to call .cancel()
# on the worker; finished tasks evict themselves.
_RUNNING: dict[int, asyncio.Task] = {}


def get_running_task(broadcast_id: int) -> Optional[asyncio.Task]:
    t = _RUNNING.get(broadcast_id)
    if t and t.done():
        _RUNNING.pop(broadcast_id, None)
        return None
    return t


def is_running(broadcast_id: int) -> bool:
    return get_running_task(broadcast_id) is not None


def register_task(broadcast_id: int, task: asyncio.Task) -> None:
    _RUNNING[broadcast_id] = task

    def _cleanup(_t):
        # Drop ourselves once we're done, no matter why (cancelled,
        # exception, success).
        if _RUNNING.get(broadcast_id) is task:
            _RUNNING.pop(broadcast_id, None)

    task.add_done_callback(_cleanup)


def cancel_running(broadcast_id: int) -> bool:
    """Stop the in-progress deleter for this broadcast. Returns True if
    a cancellation was actually sent."""
    t = get_running_task(broadcast_id)
    if t is None:
        return False
    t.cancel()
    return True

import database
from app.events import bus

logger = logging.getLogger(__name__)

# Telegram bot API guidance: bulk-deletes are subject to the general
# 30-msg/sec global limit. 30 per second with a 1-second pause every 30
# deletes mirrors the existing in-bot implementation in
# broadcast.py:1500 — proven safe in production.
_DELETE_BATCH = 30
_DELETE_PAUSE = 1.0


async def delete_broadcast_from_users(
    *,
    bot: Bot,
    broadcast_id: int,
    admin_telegram_id: int | None = None,
) -> dict:
    """Iterate every (telegram_id, message_id) pair recorded for the
    broadcast and call bot.delete_message. Returns final counters."""
    try:
        pairs = await database.get_broadcast_message_ids(broadcast_id)
    except Exception as e:
        logger.exception("BROADCAST_DELETE_FETCH_FAIL bid=%s: %s", broadcast_id, e)
        bus.publish({
            "type": "broadcast:delete_failed",
            "broadcast_id": broadcast_id,
            "error": f"{type(e).__name__}: {e}",
        })
        return {"ok": False, "error": str(e), "deleted": 0, "failed": 0, "total": 0}

    total = len(pairs)
    if total == 0:
        bus.publish({
            "type": "broadcast:delete_done",
            "broadcast_id": broadcast_id,
            "deleted": 0, "failed": 0, "total": 0,
        })
        return {"ok": True, "deleted": 0, "failed": 0, "total": 0}

    deleted = 0
    failed = 0
    processed = 0

    try:
        for telegram_id, message_id in pairs:
            try:
                await bot.delete_message(chat_id=int(telegram_id), message_id=int(message_id))
                deleted += 1
            except Exception as e:
                failed += 1
                # Log a sample of errors to spot systemic issues (e.g.
                # all the messages were too old).
                if failed <= 3 or failed % 200 == 0:
                    logger.info(
                        "BROADCAST_DELETE_SKIP bid=%s tg=%s mid=%s err=%s",
                        broadcast_id, telegram_id, message_id, e,
                    )

            processed += 1
            if processed % _DELETE_BATCH == 0:
                bus.publish({
                    "type": "broadcast:delete_progress",
                    "broadcast_id": broadcast_id,
                    "processed": processed,
                    "total": total,
                    "deleted": deleted,
                    "failed": failed,
                })
                await asyncio.sleep(_DELETE_PAUSE)

        # Final flush
        bus.publish({
            "type": "broadcast:delete_progress",
            "broadcast_id": broadcast_id,
            "processed": processed,
            "total": total,
            "deleted": deleted,
            "failed": failed,
        })

        # Mark rows as 'deleted' in broadcast_log so we don't re-attempt
        # automatically and we have an audit trail.
        try:
            await database.mark_broadcast_messages_deleted(broadcast_id)
        except Exception as e:
            logger.warning(
                "BROADCAST_DELETE_MARK_FAIL bid=%s: %s", broadcast_id, e,
            )

        try:
            await database._log_audit_event_atomic_standalone(
                "broadcast_deleted",
                admin_telegram_id,
                None,
                f"Broadcast {broadcast_id}: deleted={deleted}, failed={failed}, total={total}",
            )
        except Exception:
            pass

        bus.publish({
            "type": "broadcast:delete_done",
            "broadcast_id": broadcast_id,
            "deleted": deleted,
            "failed": failed,
            "total": total,
        })
        logger.info(
            "BROADCAST_DELETE_DONE bid=%s deleted=%s failed=%s total=%s",
            broadcast_id, deleted, failed, total,
        )
        return {"ok": True, "deleted": deleted, "failed": failed, "total": total}

    except asyncio.CancelledError:
        # Admin pressed Стоп. Persist the partial progress so the row
        # status is honest, publish the final delta and re-raise so
        # the task is marked cancelled in the registry.
        logger.info(
            "BROADCAST_DELETE_CANCELLED bid=%s processed=%s/%s deleted=%s failed=%s",
            broadcast_id, processed, total, deleted, failed,
        )
        try:
            await database._log_audit_event_atomic_standalone(
                "broadcast_delete_cancelled",
                admin_telegram_id,
                None,
                f"Broadcast {broadcast_id}: processed={processed}/{total}, "
                f"deleted={deleted}, failed={failed}",
            )
        except Exception:
            pass
        bus.publish({
            "type": "broadcast:delete_cancelled",
            "broadcast_id": broadcast_id,
            "processed": processed,
            "total": total,
            "deleted": deleted,
            "failed": failed,
        })
        raise
    except Exception as e:
        logger.exception("BROADCAST_DELETE_FATAL bid=%s: %s", broadcast_id, e)
        bus.publish({
            "type": "broadcast:delete_failed",
            "broadcast_id": broadcast_id,
            "error": f"{type(e).__name__}: {e}",
        })
        return {"ok": False, "error": str(e), "deleted": deleted, "failed": failed, "total": total}
