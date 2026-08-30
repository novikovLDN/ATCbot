"""Backfill numeric id и telegramId в панель Remnawave 3.x после апгрейда.

Панель уже на 3.x. Скрипт:
  1. По каждой активной строке `subscriptions` с непустым UUID:
      a. find_user_by_telegram_id(tg) → берём entity {id, telegramId, ...}
      b. UPDATE subscriptions SET remnawave_id / remnawave_premium_id.
         (Требуется миграция 078_remnawave_numeric_id.sql.)
  2. Для entity без telegramId в панели (или отличающимся) →
     PATCH /api/users body {id, telegramId=<наш>}.

Идемпотентно: повторные запуски пропускают уже забэкфильнутые.
Rate limit: 5 req/sec.

Запуск:
  python -m scripts.prep_remnawave_v3_migration [--dry-run] [--limit N]

Flags:
  --dry-run — только вывод, ничего не пишем.
  --limit N — обработать не более N записей (тест).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

import database
from app.services import remnawave_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("prep_remnawave_v3")


async def _iter_our_entities():
    """Пары (telegram_id, kind, uuid, cached_id) из subscriptions.

    kind ∈ {"bypass", "premium"}. Один tg может дать 2 entities.
    Отфильтровано на активные подписки.
    """
    pool = await database.get_pool()
    if pool is None:
        raise RuntimeError("database pool not ready")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id,
                      remnawave_uuid, remnawave_id,
                      remnawave_premium_uuid, remnawave_premium_id
                 FROM subscriptions
                WHERE status = 'active'
                  AND (remnawave_uuid IS NOT NULL
                       OR remnawave_premium_uuid IS NOT NULL)""",
        )
    for r in rows:
        tg = r["telegram_id"]
        if r["remnawave_uuid"]:
            yield (tg, "bypass", r["remnawave_uuid"], r["remnawave_id"])
        if r["remnawave_premium_uuid"]:
            yield (tg, "premium", r["remnawave_premium_uuid"], r["remnawave_premium_id"])


async def _has_migration_078() -> bool:
    pool = await database.get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT 1 FROM information_schema.columns
                WHERE table_name = 'subscriptions' AND column_name = 'remnawave_id'""",
        )
    return row is not None


async def _cache_id(telegram_id: int, kind: str, num_id: int) -> None:
    pool = await database.get_pool()
    if pool is None:
        return
    col = "remnawave_id" if kind == "bypass" else "remnawave_premium_id"
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE subscriptions SET {col} = $1 "
            "WHERE telegram_id = $2 AND status = 'active'",
            int(num_id), telegram_id,
        )


async def _process(
    telegram_id: int,
    kind: str,
    uuid: str,
    cached_id: Optional[int],
    *,
    dry_run: bool,
) -> tuple[str, str]:
    """Вернуть (outcome, note). outcome ∈
    {processed, missing, error}."""
    entity = await remnawave_api.find_user_by_telegram_id(telegram_id)
    if entity is None:
        # 3.x панель не знает нашего юзера по telegram_id. Возможно
        # entity действительно нет, ИЛИ панель не знает telegramId
        # (в старой 2.7.4 не проставлялся). Попробуем через username
        # (наш default = str(telegram_id)).
        entity = await remnawave_api.find_user_by_username(str(telegram_id))
        if entity is None:
            return ("missing", f"not found in panel by tg={telegram_id} or username")

    # (1) id caching
    num_id = entity.get("id")
    id_note = None
    if num_id is not None:
        try:
            n = int(num_id)
            if cached_id != n:
                if dry_run:
                    id_note = f"would cache id={n} (was {cached_id})"
                else:
                    await _cache_id(telegram_id, kind, n)
                    id_note = f"cached id={n}"
            else:
                id_note = "id already cached"
        except (TypeError, ValueError):
            id_note = f"bad id {num_id!r}"

    # (2) telegramId sync
    panel_tg = entity.get("telegramId")
    same = False
    if panel_tg is not None:
        try:
            same = int(panel_tg) == int(telegram_id)
        except (TypeError, ValueError):
            pass
    if same:
        tg_note = "tg already set"
    else:
        if dry_run:
            tg_note = f"would PATCH telegramId={telegram_id} (panel={panel_tg})"
        else:
            # 3.x PATCH /api/users с body {id, telegramId}
            result = await remnawave_api.update_user(
                int(num_id), telegramId=int(telegram_id),
            )
            tg_note = (
                f"PATCH telegramId={telegram_id} ok" if result is not None
                else f"PATCH telegramId={telegram_id} failed"
            )

    return ("processed", f"{id_note} | {tg_note}")


async def _main(dry_run: bool, limit: Optional[int]) -> int:
    logger.info("prep starting (dry_run=%s, limit=%s)", dry_run, limit)

    from database.core import initialize_database
    if not await initialize_database():
        logger.error("database initialize_database() → False")
        return 2

    if not await _has_migration_078():
        logger.error(
            "миграция 078_remnawave_numeric_id.sql не применена — "
            "перезапустите бот, чтобы автомиграция подхватила её, или "
            "примените вручную.",
        )
        return 3

    counts = {"processed": 0, "missing": 0, "error": 0}
    per_kind = {"bypass": 0, "premium": 0}
    processed_n = 0

    async for tg, kind, uuid, cached_id in _iter_our_entities():
        if limit is not None and processed_n >= limit:
            break
        processed_n += 1
        per_kind[kind] += 1
        try:
            outcome, note = await _process(tg, kind, uuid, cached_id, dry_run=dry_run)
        except Exception as e:
            outcome, note = ("error", str(e))
        counts[outcome] += 1
        if outcome != "processed" or processed_n % 100 == 0:
            logger.info(
                "tg=%s kind=%s uuid=%s → %s (%s)",
                tg, kind, uuid[:8], outcome, note,
            )
        await asyncio.sleep(0.2)  # ~5 req/sec

    logger.info(
        "done. processed=%s, per-kind=%s, outcomes=%s",
        processed_n, per_kind, counts,
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="без записи в панель / БД")
    p.add_argument("--limit", type=int, default=None, help="максимум N")
    args = p.parse_args()
    return asyncio.run(_main(dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    sys.exit(main())
