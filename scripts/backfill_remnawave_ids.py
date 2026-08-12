#!/usr/bin/env python3
"""
Backfill `subscriptions.remnawave_id` / `remnawave_premium_id` for every user
provisioned before migration 075 (Remnawave 3.x panel cutover).

Resolution order per user, per kind:
  1. Skip — id already cached.
  2. If short-uuid cached → GET /api/users/by-short-uuid/{short} → take `id`.
  3. Fallback: GET /api/users/stream?telegramId=X → pick the entity whose
     username matches the expected (bypass|premium) pattern → take `id`.
  4. Give up; log the miss.  A subsequent migration-notice or click by
     the user will trigger the resolver again.

Usage
-----
    # Real backfill against production database + panel.
    python scripts/backfill_remnawave_ids.py

    # Dry-run: scan and report but never UPDATE.
    python scripts/backfill_remnawave_ids.py --dry-run

    # Cap the run at N users per kind (useful for smoke testing).
    python scripts/backfill_remnawave_ids.py --limit 100

    # Kind selection: default runs both.
    python scripts/backfill_remnawave_ids.py --kind bypass
    python scripts/backfill_remnawave_ids.py --kind premium

Safety
------
- Rate limited to 10 requests/sec by default (--rps).
- Exponential backoff on 5xx (3 tries).
- Idempotent — running twice on the same corpus is a no-op after the first.
- Never deletes DB data.  Only UPDATE SET remnawave_id / remnawave_premium_id.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# Make imports work regardless of cwd
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _fetch_targets(pool, kind: str, limit: Optional[int]):
    """Return (telegram_id, short_uuid, legacy_uuid) for users needing backfill."""
    if kind == "premium":
        id_col = "remnawave_premium_id"
        short_col = "remnawave_premium_short_uuid"
        legacy_col = "remnawave_premium_uuid"
    else:
        id_col = "remnawave_id"
        short_col = "remnawave_bypass_short_uuid"
        legacy_col = "remnawave_uuid"

    query = (
        f"SELECT telegram_id, {short_col} AS short_uuid, {legacy_col} AS legacy_uuid "
        f"FROM subscriptions "
        f"WHERE {id_col} IS NULL "
        f"  AND ({short_col} IS NOT NULL AND {short_col} != '' "
        f"       OR {legacy_col} IS NOT NULL AND {legacy_col} != '') "
        f"ORDER BY telegram_id"
    )
    if limit and limit > 0:
        query += f" LIMIT {int(limit)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query)
    return [dict(r) for r in rows]


async def _resolve_one(
    telegram_id: int,
    short_uuid: Optional[str],
    kind: str,
) -> Optional[int]:
    """Try short-uuid then stream.  Returns the numeric id or None."""
    from app.services import remnawave_api

    async def _try(coro):
        for attempt in range(3):
            try:
                return await coro()
            except Exception as e:
                delay = 1.5 ** attempt
                logging.debug(
                    "backfill: attempt %d for tg=%s failed (%s), sleep %.1fs",
                    attempt + 1, telegram_id, e, delay,
                )
                await asyncio.sleep(delay)
        return None

    panel_id: Optional[int] = None

    if short_uuid:
        entity = await _try(lambda: remnawave_api.get_user_by_short_uuid(short_uuid))
        if isinstance(entity, dict) and entity.get("id") is not None:
            try:
                panel_id = int(entity["id"])
            except (TypeError, ValueError):
                panel_id = None

    if panel_id is None:
        # stream fallback — pick by username match
        users = await _try(
            lambda: remnawave_api.get_users_stream_by_telegram_id(telegram_id, size=10)
        )
        if users:
            if kind == "premium":
                expected = f"tg_{telegram_id}_premium"
            else:
                expected = str(telegram_id)
            for u in users:
                if not isinstance(u, dict):
                    continue
                if (u.get("username") or "").strip() == expected:
                    try:
                        panel_id = int(u["id"])
                    except (TypeError, ValueError):
                        continue
                    break

    return panel_id


async def _persist(pool, telegram_id: int, kind: str, panel_id: int) -> None:
    col = "remnawave_premium_id" if kind == "premium" else "remnawave_id"
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE subscriptions SET {col} = $1 WHERE telegram_id = $2",
            int(panel_id), telegram_id,
        )


async def _backfill_kind(
    pool, kind: str, *, limit: Optional[int], rps: float, dry_run: bool,
) -> dict:
    targets = await _fetch_targets(pool, kind, limit)
    n = len(targets)
    if n == 0:
        logging.info("backfill[%s]: nothing to do", kind)
        return {"kind": kind, "total": 0, "resolved": 0, "missed": 0}

    logging.info("backfill[%s]: %d rows to process (dry_run=%s)", kind, n, dry_run)

    resolved = 0
    missed = 0
    interval = 1.0 / max(rps, 0.1)
    last_progress = 0
    started = time.monotonic()
    last_ts = 0.0

    for i, row in enumerate(targets, 1):
        # Rate limit
        elapsed = time.monotonic() - last_ts
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        last_ts = time.monotonic()

        tg = int(row["telegram_id"])
        short = row.get("short_uuid") or None
        panel_id = await _resolve_one(tg, short, kind)
        if panel_id is None:
            missed += 1
            logging.info("backfill[%s]: MISS tg=%s legacy=%s",
                         kind, tg, (row.get("legacy_uuid") or "")[:8])
        else:
            resolved += 1
            if not dry_run:
                try:
                    await _persist(pool, tg, kind, panel_id)
                except Exception as e:
                    logging.warning("backfill[%s]: persist FAIL tg=%s %s", kind, tg, e)
                    missed += 1
                    resolved -= 1

        if i - last_progress >= 100 or i == n:
            rate = i / max(time.monotonic() - started, 0.001)
            logging.info(
                "backfill[%s]: progress %d/%d resolved=%d missed=%d (~%.1f req/s)",
                kind, i, n, resolved, missed, rate,
            )
            last_progress = i

    return {"kind": kind, "total": n, "resolved": resolved, "missed": missed}


async def _main_async(args) -> int:
    # Init DB pool (also triggers bootstrap SQL — includes migration 075
    # ALTER TABLE ... IF NOT EXISTS, so a fresh run is safe).
    import database
    await database.init_pool()
    pool = await database.get_pool()
    if pool is None:
        logging.error("DB pool unavailable")
        return 2

    kinds = ["bypass", "premium"] if args.kind == "both" else [args.kind]

    summary = []
    for kind in kinds:
        out = await _backfill_kind(
            pool, kind,
            limit=args.limit, rps=args.rps, dry_run=args.dry_run,
        )
        summary.append(out)

    logging.info("=" * 60)
    for out in summary:
        logging.info(
            "SUMMARY[%s]: total=%d resolved=%d missed=%d",
            out["kind"], out["total"], out["resolved"], out["missed"],
        )
    logging.info("=" * 60)

    # Non-zero exit on any misses so a CI/one-shot wrapper can alert.
    total_missed = sum(o["missed"] for o in summary)
    return 0 if total_missed == 0 else 1


def _parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--kind", choices=["bypass", "premium", "both"], default="both")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap the number of rows per kind (0 = no cap).")
    p.add_argument("--rps", type=float, default=10.0,
                   help="Panel request rate limit (per second).")
    p.add_argument("--dry-run", action="store_true",
                   help="Do the panel lookups but skip DB writes.")
    p.add_argument("--verbose", action="store_true",
                   help="Enable DEBUG logging.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        logging.warning("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
