"""
Site sync worker — periodic balance & referral sync with Atlas Secure website.

Runs every 5 minutes. For each user with active subscription:
1. POST /api/bot/sync-balance → apply pending cashback from site
2. POST /api/bot/sync-referrals → merge referral data

Only syncs users who have active subscriptions (not all users).
Skips if site sync is not configured.
"""
import asyncio
import logging
import os
import time

import database
from app.services.site_sync import sync_balance, sync_referrals, is_enabled
from app.core.feature_flags import background_workers_paused

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 5 * 60  # 5 minutes
SYNC_USER_DELAY = 0.5  # delay between users to avoid rate limiting

# Потолок на один проход, меньше интервала: проходы не должны наезжать
# друг на друга.
#
# Арифметика: 500 человек × (два запроса к сайту + пауза 0.5 с) — это уже
# больше четырёх минут при мгновенных ответах. Стоит сайту начать отвечать
# медленнее, и проход перестаёт укладываться в интервал.
#
# SYNC_CONCURRENCY здесь раньше был объявлен, но нигде не использовался:
# цикл строго последовательный. Убран, чтобы не обещать параллельность,
# которой нет.
MAX_ITERATION_SECONDS = int(os.getenv("SITE_SYNC_MAX_ITERATION_SECONDS", "240"))


async def site_sync_worker_task(bot=None):
    """Background worker: periodic site sync every 5 minutes."""
    logger.info("site_sync_worker started (interval=%ds)", SYNC_INTERVAL)

    while True:
        # Аварийный рубильник фоновых воркеров. Проверяем внутри цикла:
        # флаг читается из окружения и может смениться без рестарта.
        if background_workers_paused("site_sync"):
            await asyncio.sleep(300)
            continue
        try:
            await asyncio.sleep(SYNC_INTERVAL)

            if not is_enabled():
                continue

            if not database.DB_READY:
                continue

            start_time = time.monotonic()
            logger.info("SITE_SYNC_ITERATION_START")

            # Get only site-linked users with active subscriptions
            pool = await database.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT DISTINCT s.telegram_id FROM subscriptions s
                       JOIN users u ON u.telegram_id = s.telegram_id
                       WHERE s.expires_at > NOW() AND s.telegram_id IS NOT NULL
                       AND u.site_linked = TRUE
                       LIMIT 500"""
                )

            synced = 0
            errors = 0
            skipped = 0
            for row in rows:
                if time.monotonic() - start_time > MAX_ITERATION_SECONDS:
                    # Не успели — остальные попадут в следующий проход.
                    # Синхронизация идемпотентна, терять нечего.
                    skipped = len(rows) - synced - errors
                    logger.warning(
                        "SITE_SYNC_ITERATION_CAPPED: успели %s из %s за %s с, "
                        "остальные в следующий проход",
                        synced + errors, len(rows), MAX_ITERATION_SECONDS,
                    )
                    break

                telegram_id = row["telegram_id"]
                try:
                    # Обе функции НЕ бросают при отказе сайта: _post отдаёт
                    # None и на не-200, и на success=false, и на таймаут.
                    # Раньше synced увеличивался просто по факту возврата из
                    # вызова, поэтому при полностью лежащем сайте итог гласил
                    # «synced=500 errors=0» — а не синхронизировалось ничего.
                    # errors ловил только исключения Python, то есть ровно те
                    # случаи, которые к сайту отношения не имеют.
                    balance_ok = await sync_balance(telegram_id) is not None
                    referrals_ok = await sync_referrals(telegram_id) is not None
                    if balance_ok and referrals_ok:
                        synced += 1
                    else:
                        errors += 1
                        logger.warning(
                            "SITE_SYNC_USER_FAILED: user=%s balance_ok=%s referrals_ok=%s "
                            "— сайт не принял данные",
                            telegram_id, balance_ok, referrals_ok,
                        )
                except Exception as e:
                    errors += 1
                    # Причина отказа раньше уходила в debug, а root-логгер
                    # настроен на INFO: в проде оставалось «errors=12» без
                    # единого слова о том, на ком и почему.
                    logger.warning(
                        "SITE_SYNC_USER_ERROR: user=%s error=%s: %s",
                        telegram_id, type(e).__name__, e,
                    )

                await asyncio.sleep(SYNC_USER_DELAY)

            duration_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "SITE_SYNC_ITERATION_END: synced=%d errors=%d skipped=%d duration=%.0fms",
                synced, errors, skipped, duration_ms,
            )

        except asyncio.CancelledError:
            logger.info("site_sync_worker cancelled (shutdown)")
            break
        except Exception as e:
            logger.exception("site_sync_worker unexpected error: %s", e)
            await asyncio.sleep(60)  # wait before retry on unexpected error
