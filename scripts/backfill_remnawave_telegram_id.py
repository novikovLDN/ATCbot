"""Backfill telegramId в Remnawave-панели для всех наших entities.

Зачем: после миграции Remnawave 2.7.4 → 3.x поле telegramId — единственный
надёжный маркер «наш ли это юзер» в _is_our_entity (см. remnawave_bypass.py:65
и remnawave_premium.py:99). Все entities, которые ещё не имеют telegramId в
панели, будут обработаны через username fallback → это ОК, но медленнее.
Скрипт заранее засеивает telegramId, чтобы recovery был чистым.

Что делает:
  1. Читает нашу subscriptions-таблицу — все юзеры с remnawave_uuid IS NOT NULL
     ИЛИ remnawave_premium_uuid IS NOT NULL.
  2. Для каждого entity:
     a. GET /api/users/{uuid} — получить текущее состояние (telegramId + username)
     b. Если панель уже знает telegramId и он совпадает — SKIP.
     c. Если telegramId отсутствует ИЛИ отличается — PATCH /api/users/update
        body {uuid, telegramId=<из нашей БД>}.
  3. Также заполняет remnawave_id в нашей БД если панель отдаёт числовой id
     (миграция 078 добавляет колонку — работает только после её применения).
  4. Rate limit: 5 req/sec, чтобы не задосить панель.

Идемпотентно: можно запускать многократно, уже сматченные пропускаются.

Запуск:
  python -m scripts.backfill_remnawave_telegram_id [--dry-run] [--limit N]

Флаги:
  --dry-run  — только вывод, ничего не пишем в панель / БД.
  --limit N  — обработать не более N юзеров (для тестового прогона).
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
logger = logging.getLogger("backfill_telegram_id")


async def _iter_our_entities():
    """Вытащить все remnawave_uuid + remnawave_premium_uuid из subscriptions.

    Возвращает пары (telegram_id, entity_kind, uuid) для каждой не-NULL
    записи. Один telegram_id может дать 2 entities (bypass + premium).
    """
    pool = await database.get_pool()
    if pool is None:
        raise RuntimeError("database pool not ready")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_uuid, remnawave_premium_uuid
                 FROM subscriptions
                WHERE remnawave_uuid IS NOT NULL
                   OR remnawave_premium_uuid IS NOT NULL""",
        )
    for r in rows:
        tg = r["telegram_id"]
        if r["remnawave_uuid"]:
            yield (tg, "bypass", r["remnawave_uuid"])
        if r["remnawave_premium_uuid"]:
            yield (tg, "premium", r["remnawave_premium_uuid"])


async def _has_remnawave_id_column() -> bool:
    """Проверить, применена ли миграция 078 (добавила remnawave_id + remnawave_premium_id)."""
    pool = await database.get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT 1 FROM information_schema.columns
                WHERE table_name = 'subscriptions' AND column_name = 'remnawave_id'""",
        )
    return row is not None


async def _cache_remnawave_id(telegram_id: int, kind: str, num_id: int) -> None:
    """Закешировать числовой id панели в нашей БД. No-op если миграция 078
    ещё не применена (колонки нет)."""
    if not await _has_remnawave_id_column():
        return
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


async def _process_one(
    telegram_id: int,
    kind: str,
    uuid: str,
    *,
    dry_run: bool,
) -> tuple[str, Optional[str]]:
    """Обработать один entity. Возвращает (outcome, note).

    outcome ∈ {"skipped", "patched", "already-set", "missing-in-panel", "error"}.
    """
    entity = await remnawave_api.get_user(uuid)
    if entity is None:
        return ("missing-in-panel", "entity not found by uuid")

    # Кешируем id — работает независимо от dry-run (только SELECT + опциональный UPDATE).
    num_id = entity.get("id")
    if num_id is not None and not dry_run:
        try:
            await _cache_remnawave_id(telegram_id, kind, int(num_id))
        except Exception as e:
            logger.warning("cache id failed tg=%s kind=%s: %s", telegram_id, kind, e)

    panel_tg = entity.get("telegramId")
    if panel_tg is not None:
        try:
            if int(panel_tg) == int(telegram_id):
                return ("already-set", f"panel_tg={panel_tg}")
        except (TypeError, ValueError):
            pass

    if dry_run:
        return ("skipped", f"would PATCH telegramId={telegram_id} (was {panel_tg})")

    result = await remnawave_api.update_user(uuid, telegramId=int(telegram_id))
    if result is None:
        return ("error", "PATCH /users/update returned None")
    return ("patched", f"telegramId={telegram_id} set (was {panel_tg})")


async def _main(dry_run: bool, limit: Optional[int]) -> int:
    logger.info("backfill starting (dry_run=%s, limit=%s)", dry_run, limit)

    # Инициализация пула БД (если запускается вне бота).
    from database.core import initialize_database
    initialized = await initialize_database()
    if not initialized:
        logger.error("database initialize_database() returned False; aborting")
        return 2

    counts = {
        "already-set": 0,
        "patched": 0,
        "missing-in-panel": 0,
        "skipped": 0,
        "error": 0,
    }
    processed = 0

    async for tg, kind, uuid in _iter_our_entities():
        if limit is not None and processed >= limit:
            break
        processed += 1
        try:
            outcome, note = await _process_one(tg, kind, uuid, dry_run=dry_run)
        except Exception as e:
            outcome, note = ("error", str(e))
        counts[outcome] += 1
        if outcome in ("patched", "missing-in-panel", "error"):
            logger.info("tg=%s kind=%s uuid=%s → %s (%s)", tg, kind, uuid[:8], outcome, note)
        # Rate limit ~5 req/sec.
        await asyncio.sleep(0.2)

    logger.info("done. processed=%s, outcomes=%s", processed, counts)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="без записи в панель / БД")
    p.add_argument("--limit", type=int, default=None, help="максимум обработать N")
    args = p.parse_args()
    return asyncio.run(_main(dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    sys.exit(main())
