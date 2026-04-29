#!/usr/bin/env python3
"""
Восстановление пользователей, у которых флаг триала установлен,
но VPN-подписка не была создана (handler упал по таймауту на VPN API
до коммита 94d71aa, менявшего порядок операций в callback_activate_trial).

Критерий «застрявшего» пользователя:
- users.trial_used_at IS NOT NULL (флаг установлен)
- Нет записи в subscription_history с action_type='trial' для этого юзера
  (grant_access не дошёл до INSERT → history тоже пустая)

Действие: очистить trial_used_at и trial_expires_at — пользователь сможет
повторно активировать триал через кнопку. После коммита 94d71aa новые
«застрявшие» пользователи не появляются.

Использование:
    # Dry-run: только показать список, без изменений (по умолчанию)
    python -m scripts.recover_stuck_trials

    # Применить изменения
    python -m scripts.recover_stuck_trials --apply

    # Ограничить одним пользователем (для ручной проверки)
    python -m scripts.recover_stuck_trials --apply --telegram-id 210948123
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("recover_stuck_trials")


DETECT_QUERY = """
    SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at
    FROM users u
    WHERE u.trial_used_at IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM subscription_history sh
          WHERE sh.telegram_id = u.telegram_id
            AND sh.action_type = 'trial'
      )
    ORDER BY u.trial_used_at DESC
"""

DETECT_QUERY_SINGLE = """
    SELECT u.telegram_id, u.trial_used_at, u.trial_expires_at
    FROM users u
    WHERE u.telegram_id = $1
      AND u.trial_used_at IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM subscription_history sh
          WHERE sh.telegram_id = u.telegram_id
            AND sh.action_type = 'trial'
      )
"""

RECOVER_QUERY = """
    UPDATE users
    SET trial_used_at = NULL, trial_expires_at = NULL
    WHERE telegram_id = $1
      AND trial_used_at IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM subscription_history sh
          WHERE sh.telegram_id = users.telegram_id
            AND sh.action_type = 'trial'
      )
"""


async def find_stuck_users(telegram_id: int | None = None) -> list[dict]:
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        if telegram_id is not None:
            rows = await conn.fetch(DETECT_QUERY_SINGLE, telegram_id)
        else:
            rows = await conn.fetch(DETECT_QUERY)
        return [dict(r) for r in rows]


async def recover_one(telegram_id: int) -> bool:
    """Очистить флаг триала для одного юзера. Возвращает True если UPDATE затронул строку."""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(RECOVER_QUERY, telegram_id)
        # result строка формата "UPDATE 1" или "UPDATE 0"
        updated = result.endswith(" 1")

    if updated:
        try:
            await database._log_audit_event_atomic_standalone(
                action="trial_recovery_unblock",
                telegram_id=telegram_id,
                target_user=telegram_id,
                details=(
                    "Очистка trial_used_at для застрявшего пользователя "
                    "(VPN API hang before commit 94d71aa)"
                ),
            )
        except Exception as e:
            logger.warning(
                "audit_log_failed: telegram_id=%s error=%s", telegram_id, e
            )
    return updated


async def main():
    parser = argparse.ArgumentParser(
        description="Recover stuck trial users (trial_used_at set but no trial subscription)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the recovery (default: dry-run only)",
    )
    parser.add_argument(
        "--telegram-id",
        type=int,
        default=None,
        help="Recover only this user (for manual verification)",
    )
    args = parser.parse_args()

    await database.init_db()
    if not database.DB_READY:
        logger.error("DB not ready, aborting")
        return 1

    stuck = await find_stuck_users(telegram_id=args.telegram_id)

    if not stuck:
        logger.info("No stuck trial users found.")
        return 0

    logger.info("Found %d stuck trial user(s):", len(stuck))
    for row in stuck:
        logger.info(
            "  telegram_id=%s trial_used_at=%s trial_expires_at=%s",
            row["telegram_id"],
            row["trial_used_at"].isoformat() if row["trial_used_at"] else None,
            row["trial_expires_at"].isoformat() if row["trial_expires_at"] else None,
        )

    if not args.apply:
        logger.info("")
        logger.info("DRY-RUN mode. Run with --apply to clear flags.")
        return 0

    logger.info("")
    logger.info("Applying recovery for %d user(s)...", len(stuck))
    recovered = 0
    for row in stuck:
        tg = row["telegram_id"]
        try:
            ok = await recover_one(tg)
            if ok:
                recovered += 1
                logger.info("  recovered: telegram_id=%s", tg)
            else:
                logger.warning(
                    "  skipped (no longer matches criteria): telegram_id=%s", tg
                )
        except Exception as e:
            logger.error("  failed: telegram_id=%s error=%s", tg, e)

    logger.info("")
    logger.info("Recovery complete: %d/%d user(s) unblocked.", recovered, len(stuck))
    logger.info("Users can now re-activate the trial via the bot button.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
