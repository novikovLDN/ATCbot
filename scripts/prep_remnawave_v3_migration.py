"""PRE-миграция под Remnawave Panel 2.7.4 → 3.x.

⚠️ ЗАПУСКАТЬ ПОКА ПАНЕЛЬ ЕЩЁ 2.7.4 ⚠️
После апгрейда до 3.x endpoint'ы `/api/users/{uuid}` перестают работать
(поле uuid удалено), поэтому кешировать numeric id из UUID можно только
сейчас.

Что делает:
  1. По каждой активной строке `subscriptions` с `remnawave_uuid` /
     `remnawave_premium_uuid`:
        a. GET /api/users/{uuid} на 2.7.4 → берём числовой поле `id`.
        b. UPDATE subscriptions SET remnawave_id / remnawave_premium_id.
     (Миграция 078 должна быть применена — иначе колонок нет.)
  2. Для тех же entities проверяет `telegramId` в панели:
        - если пусто ИЛИ отличается от нашего telegram_id →
          PATCH /api/users body {uuid, telegramId=<наш>}.
     Это нужно для чистого `_is_our_entity` recovery после апгрейда:
     в 3.x единственный способ найти нашего юзера по TG —
     `GET /api/users/stream?telegramId=X`.

Идемпотентно: повторные запуски пропускают уже забэкфильнутые записи.

Rate limit: 5 req/sec (панель не любит спам).

Использует ПРЯМЫЕ httpx-запросы к 2.7.4 endpoints — НЕ через
app.services.remnawave_api (тот уже переведён на 3.x).

Запуск:
  python -m scripts.prep_remnawave_v3_migration [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

import httpx

import config
import database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("prep_remnawave_v3")

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_HEADERS = {
    "Authorization": f"Bearer {config.REMNAWAVE_API_TOKEN}",
    "Content-Type": "application/json",
}


async def _get_user_v27(client: httpx.AsyncClient, uuid: str) -> Optional[dict]:
    """GET /api/users/{uuid} на панели 2.7.4."""
    url = f"{config.REMNAWAVE_API_URL}/api/users/{uuid}"
    try:
        resp = await client.get(url, headers=_HEADERS)
    except Exception as e:
        logger.warning("GET %s failed: %s", uuid[:8], e)
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        logger.warning("GET %s status=%s body=%s", uuid[:8], resp.status_code, resp.text[:200])
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data if isinstance(data, dict) else None


async def _patch_telegram_id_v27(
    client: httpx.AsyncClient, uuid: str, telegram_id: int,
) -> bool:
    """PATCH через 2.7.4 auto-discover для установки telegramId.

    2.7.4 не имел стабильного пути — пробуем /api/users body-based
    (тот же паттерн что и в старом app/services/remnawave_api.py:_update_method).
    """
    body = {"uuid": uuid, "telegramId": int(telegram_id)}
    variants = [
        ("PATCH", "/api/users"),
        ("POST", "/api/users/update"),
        ("PUT", "/api/users"),
    ]
    for method, path in variants:
        url = f"{config.REMNAWAVE_API_URL}{path}"
        try:
            resp = await client.request(method, url, headers=_HEADERS, json=body)
        except Exception as e:
            logger.debug("%s %s failed: %s", method, path, e)
            continue
        if 200 <= resp.status_code < 300:
            return True
    return False


async def _iter_our_entities():
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
    """Проверить что миграция 078 применена (колонка remnawave_id есть)."""
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
    client: httpx.AsyncClient,
    telegram_id: int,
    kind: str,
    uuid: str,
    cached_id: Optional[int],
    *,
    dry_run: bool,
) -> tuple[str, str]:
    """Вернуть (outcome, note). outcome ∈
    {id-cached, id-already-cached, tg-patched, tg-already-set, missing, error}"""
    entity = await _get_user_v27(client, uuid)
    if entity is None:
        return ("missing", "not found in panel")

    # (1) id caching
    num_id = entity.get("id")
    id_outcome = None
    if num_id is not None:
        try:
            n = int(num_id)
            if cached_id != n:
                if dry_run:
                    id_outcome = f"would cache id={n} (was {cached_id})"
                else:
                    await _cache_id(telegram_id, kind, n)
                    id_outcome = f"cached id={n}"
            else:
                id_outcome = "id already cached"
        except (TypeError, ValueError):
            id_outcome = f"bad id {num_id!r}"

    # (2) telegramId sync
    panel_tg = entity.get("telegramId")
    same = False
    if panel_tg is not None:
        try:
            same = int(panel_tg) == int(telegram_id)
        except (TypeError, ValueError):
            pass
    tg_outcome = None
    if same:
        tg_outcome = "tg already set"
    else:
        if dry_run:
            tg_outcome = f"would PATCH telegramId={telegram_id} (panel={panel_tg})"
        else:
            ok = await _patch_telegram_id_v27(client, uuid, telegram_id)
            tg_outcome = (
                f"PATCH telegramId={telegram_id} ok" if ok
                else f"PATCH telegramId={telegram_id} failed"
            )

    outcome = "processed"
    return (outcome, f"{id_outcome} | {tg_outcome}")


async def _main(dry_run: bool, limit: Optional[int]) -> int:
    logger.info("prep starting (dry_run=%s, limit=%s)", dry_run, limit)

    from database.core import initialize_database
    if not await initialize_database():
        logger.error("database initialize_database() → False")
        return 2

    if not await _has_migration_078():
        logger.error(
            "миграция 078 не применена — apply migrations/078_remnawave_numeric_id.sql сначала",
        )
        return 3

    counts = {"processed": 0, "missing": 0, "error": 0}
    per_kind = {"bypass": 0, "premium": 0}
    processed_n = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async for tg, kind, uuid, cached_id in _iter_our_entities():
            if limit is not None and processed_n >= limit:
                break
            processed_n += 1
            per_kind[kind] += 1
            try:
                outcome, note = await _process(
                    client, tg, kind, uuid, cached_id, dry_run=dry_run,
                )
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
