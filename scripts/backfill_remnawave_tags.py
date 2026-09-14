"""Remnawave tags by tariff — backfill of existing panel users (CLI).

Same service as the dashboard section (Дашборд → «Ещё» → «Настройки» → «Теги в
панели Remnawave»): app/services/remnawave_tags. Scope: users WITH AN ACTIVE
SUBSCRIPTION; premium entity → tariff tag (TRIAL / BASIC / PLUS / COMBO_BASIC /
COMBO_PLUS), bypass entity → BYPASS. Tag-only PATCH, at most 2 PATCH/s, only the
entities whose tag differs. The bot never starts it on its own.

  python -m scripts.backfill_remnawave_tags                      # dry run (default)
  python -m scripts.backfill_remnawave_tags --apply              # paced run
  python -m scripts.backfill_remnawave_tags --apply --limit 50   # 50 PATCHes, then "paused"

Resumable: every run re-plans from the panel and skips entities already tagged.
A run that is going on (or was interrupted) in the bot is refused without --force.
Exit code: 0 ok, 1 finished with entity errors, 2 DB / panel unavailable, 3 refused.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

from app.services import remnawave_tags

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("backfill_remnawave_tags")


def _print_preview(summary: dict) -> None:
    print(f"users with an active subscription: {summary['users']}")
    print(f"their entities in the panel: {summary['entities']} "
          f"(already right {summary['already']}, not in the panel {summary['missing']})")
    for row in summary["tags"]:
        print(f"  {row['tag']:<12} to change {row['differ']:>6} of {row['total']}")
    print(f"to change: {summary['differ']} (≈ {summary['eta_seconds']} s at "
          f"{remnawave_tags.RATE_PER_SEC:g} PATCH/s)")


async def _main(*, apply: bool, limit: Optional[int], force: bool) -> int:
    from database.core import init_db
    if not await init_db():
        print("database init failed")
        return 2
    try:
        summary = await remnawave_tags.preview()
    except remnawave_tags.TagBackfillUnavailable as e:
        print(f"unavailable: {e}")
        return 2
    _print_preview(summary)
    if not apply:
        print("dry run: nothing changed (--apply sets the tags)")
        return 0
    status = await remnawave_tags.get_status()
    if status["state"] in ("running", "interrupted", "paused") and not force:
        print(f"a run is {status['state']} in the bot (done {status['done']}/{status['total']}): "
              "continue it from the dashboard, or pass --force")
        return 3
    result = await remnawave_tags.run_foreground(None, limit=limit)
    print(f"{result['state']}: changed {result['patched']} of {result['total']}, errors {result['errors']}")
    if result.get("last_error"):
        print(f"last error: {result['last_error']}")
    return 1 if result["errors"] else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tag Remnawave entities by tariff (users with an active subscription).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="only count (default)")
    mode.add_argument("--apply", action="store_true", help="send the tag PATCHes (≤ 2/s)")
    p.add_argument("--limit", type=int, default=None, help="at most N PATCHes this run")
    p.add_argument("--force", action="store_true", help="run even if the bot shows a run in progress")
    args = p.parse_args(argv)
    return asyncio.run(_main(apply=args.apply, limit=args.limit, force=args.force))


if __name__ == "__main__":
    sys.exit(main())
