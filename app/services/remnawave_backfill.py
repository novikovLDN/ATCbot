"""In-memory job для backfill remnawave_id + telegramId после Panel 3.x.

Singleton — только одна задача одновременно. Статус читаем через
get_status(). Прогресс живёт в памяти процесса — если бот
перезапустится посреди прогона, потерям видимость, но БД не
пострадает (каждая запись обрабатывается атомарно). Retry — просто
запустить снова, идемпотентно.

Concurrency: до 25 параллельных запросов к панели через
asyncio.Semaphore. Rate-limit убран по требованию — панель
переваривает нагрузку.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

CONCURRENCY = 25


@dataclass
class BackfillStatus:
    running: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    dry_run: bool = False
    total: int = 0
    processed: int = 0
    already_set: int = 0
    id_backfilled: int = 0
    tg_backfilled: int = 0
    missing: int = 0
    errors: int = 0
    last_error: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


_status = BackfillStatus()
_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()


def get_status() -> dict:
    d = _status.as_dict()
    if _status.started_at:
        d["elapsed_sec"] = round(
            (_status.finished_at or time.time()) - _status.started_at, 1,
        )
    else:
        d["elapsed_sec"] = 0
    return d


async def _iter_entities():
    """Пары (telegram_id, kind, uuid, cached_id) из активных subscriptions."""
    import database
    pool = await database.get_pool()
    if pool is None:
        return
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


async def _process_one(
    tg: int, kind: str, uuid: str, cached_id: Optional[int],
    *, dry_run: bool,
) -> None:
    """Обработать одну запись. Обновляет глобальный _status счётчики."""
    from app.services import remnawave_api
    import database

    try:
        # 1. Найти entity в панели ПО USERNAME (единственный
        # unambiguous путь для разделения bypass/premium):
        #   bypass  → username = str(tg)
        #   premium → username = tg_{tg}_premium
        # Раньше сначала пробовали find_user_by_telegram_id — этот вызов
        # через stream возвращает ПЕРВУЮ entity, часто premium даже когда
        # kind='bypass'. В итоге bypass.remnawave_id получал premium's
        # numeric id → бот читал premium вместо bypass → показывал
        # "безлимит" вместо реальных ГБ.
        uname = f"tg_{tg}_premium" if kind == "premium" else str(tg)
        entity = await remnawave_api.find_user_by_username(uname)
        if entity is None:
            _status.missing += 1
            return

        # 2. Cache numeric id
        num_id = entity.get("id")
        if num_id is not None:
            try:
                n = int(num_id)
                if cached_id != n:
                    if not dry_run:
                        if kind == "bypass":
                            await database.set_remnawave_id(tg, n)
                        else:
                            await database.set_remnawave_premium_id(tg, n)
                    _status.id_backfilled += 1
                else:
                    _status.already_set += 1
            except (TypeError, ValueError):
                pass

        # 3. Sync telegramId в панели (если пусто или отличается)
        panel_tg = entity.get("telegramId")
        same = False
        if panel_tg is not None:
            try:
                same = int(panel_tg) == int(tg)
            except (TypeError, ValueError):
                pass
        if not same and num_id is not None and not dry_run:
            result = await remnawave_api.update_user(int(num_id), telegramId=int(tg))
            if result is not None:
                _status.tg_backfilled += 1
    except Exception as e:
        _status.errors += 1
        _status.last_error = f"tg={tg} kind={kind}: {e}"[:300]
        logger.warning("backfill error tg=%s kind=%s: %s", tg, kind, e)
    finally:
        _status.processed += 1


async def _run(dry_run: bool) -> None:
    _status.running = True
    _status.started_at = time.time()
    _status.finished_at = None
    _status.dry_run = dry_run
    _status.total = 0
    _status.processed = 0
    _status.already_set = 0
    _status.id_backfilled = 0
    _status.tg_backfilled = 0
    _status.missing = 0
    _status.errors = 0
    _status.last_error = None

    try:
        # Сначала собираем полный список — чтобы знать total.
        items = []
        async for row in _iter_entities():
            items.append(row)
        _status.total = len(items)
        logger.info("backfill: %s entities to process (dry_run=%s)", len(items), dry_run)

        sem = asyncio.Semaphore(CONCURRENCY)

        async def _bounded(tg, kind, uuid, cached_id):
            async with sem:
                await _process_one(tg, kind, uuid, cached_id, dry_run=dry_run)

        await asyncio.gather(
            *[_bounded(*row) for row in items],
            return_exceptions=True,
        )
    except Exception as e:
        logger.exception("backfill outer failure: %s", e)
        _status.last_error = str(e)[:300]
    finally:
        _status.running = False
        _status.finished_at = time.time()
        logger.info(
            "backfill done: total=%s processed=%s id_bf=%s tg_bf=%s "
            "already=%s missing=%s errors=%s",
            _status.total, _status.processed, _status.id_backfilled,
            _status.tg_backfilled, _status.already_set, _status.missing,
            _status.errors,
        )


async def start(dry_run: bool = False) -> dict:
    """Запустить фон-задачу. Отвечает 409 если уже бежит."""
    global _task
    async with _lock:
        if _status.running:
            return {"ok": False, "error": "already_running", "status": get_status()}
        _task = asyncio.create_task(_run(dry_run=dry_run))
    return {"ok": True, "status": get_status()}
