"""
Production-ready broadcast to users WITHOUT active paid subscription or trial.
Runs as background task, batched, with race-condition re-check and defensive error handling.
"""
import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot
from app.utils.telegram_safe import safe_send_message
from app.utils.logging_helpers import generate_correlation_id, set_correlation_id

import database

logger = logging.getLogger(__name__)

BATCH_SIZE = 25
SLEEP_BETWEEN_MESSAGES = 0.05
ADMIN_BROADCAST_TYPE = "no_subscription"


def _format_completion_message(result: dict) -> str:
    return (
        f"✅ Broadcast completed.\n\n"
        f"Recipients: {result.get('total', 0)}\n"
        f"Delivered: {result.get('success', 0)}\n"
        f"Failed: {result.get('failed', 0)}\n"
        f"Skipped: {result.get('skipped', 0)}\n"
        f"Duration: {result.get('duration_seconds', 0):.1f} sec."
    )


async def run_no_subscription_broadcast(
    bot: Bot,
    text: str,
    admin_telegram_id: int,
    notify_admin_on_complete: bool = True,
) -> dict:
    """
    Send broadcast to users without active paid subscription or trial.
    Uses safe_send_message (marks unreachable on chat_not_found/blocked).
    Re-checks eligibility before each send (race-condition protection).
    Never crashes; returns stats dict.
    """
    correlation_id = generate_correlation_id()
    set_correlation_id(correlation_id)

    start_time = time.time()
    success_count = 0
    failed_count = 0
    skipped_count = 0

    if not database.DB_READY:
        logger.warning(
            f"ADMIN_BROADCAST_SKIP [correlation_id={correlation_id}, reason=DB_not_ready]"
        )
        return {"success": success_count, "failed": failed_count, "skipped": skipped_count}

    try:
        users = await database.get_eligible_no_subscription_broadcast_users()
        total = len(users)
    except Exception as e:
        logger.exception(f"ADMIN_BROADCAST_FETCH_ERROR [correlation_id={correlation_id}]")
        return {"success": 0, "failed": 0, "skipped": 0}

    audit_id = await database.insert_admin_broadcast_record(
        ADMIN_BROADCAST_TYPE, total, 0, 0
    )

    logger.info(
        f"ADMIN_BROADCAST_STARTED [correlation_id={correlation_id}, total_recipients={total}]"
    )

    pool = None
    try:
        pool = await database.get_pool()
    except Exception:
        pass

    if pool is None:
        logger.warning("ADMIN_BROADCAST_SKIP [reason=pool_unavailable]")
        return {"success": 0, "failed": 0, "skipped": total}

    for i, user_row in enumerate(users):
        telegram_id = user_row["telegram_id"]
        try:
            async with pool.acquire() as conn:
                now = datetime.utcnow()
                if not await database.check_user_still_eligible_for_no_sub_broadcast(
                    conn, telegram_id, now
                ):
                    skipped_count += 1
                    continue

            sent = await safe_send_message(bot, telegram_id, text)
            if sent is not None:
                success_count += 1
            else:
                failed_count += 1

            if (i + 1) % BATCH_SIZE == 0:
                logger.info(
                    f"ADMIN_BROADCAST_PROGRESS [correlation_id={correlation_id}, "
                    f"processed={i + 1}, success={success_count}, failed={failed_count}, skipped={skipped_count}]"
                )

            await asyncio.sleep(SLEEP_BETWEEN_MESSAGES)

        except asyncio.CancelledError:
            logger.info(f"ADMIN_BROADCAST_CANCELLED [correlation_id={correlation_id}]")
            raise
        except Exception as e:
            failed_count += 1
            logger.exception(
                f"ADMIN_BROADCAST_SEND_ERROR [correlation_id={correlation_id}, user={telegram_id}]"
            )

    duration_ms = (time.time() - start_time) * 1000

    if audit_id:
        await database.update_admin_broadcast_record(audit_id, success_count, failed_count)

    logger.info(
        f"ADMIN_BROADCAST_COMPLETED [correlation_id={correlation_id}, "
        f"total_recipients={total}, success_count={success_count}, failed_count={failed_count}, "
        f"skipped_count={skipped_count}, duration_ms={duration_ms:.0f}]"
    )

    result = {
        "success": success_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "total": total,
        "duration_seconds": duration_ms / 1000,
    }

    if notify_admin_on_complete:
        try:
            await bot.send_message(
                admin_telegram_id,
                _format_completion_message(result),
            )
        except Exception as e:
            logger.warning(f"Failed to notify admin of broadcast completion: {e}")

    return result
