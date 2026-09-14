"""Premium entities with expireAt > 5 years ahead → date by real purchases (CLI).

Service: app/services/premium_repair (same rule as the dashboard «Сверка» fix,
database/reconciliation.compute_repair_target):

  target = max(by_purchases, by_db); none or in the past → now + 1 day;
  never later than the panel's current expireAt.

Touches ONLY premium entities `tg_<id>_premium` (PATCH {id, expireAt}) and a
leaked (> 5 years, not bypass-only) subscriptions.expires_at. Bypass entities
and the bypass-only +10y placeholder are never touched.

  python -m scripts.fix_premium_over_issuance                       # dry run → CSV
  python -m scripts.fix_premium_over_issuance --apply               # asks to confirm
  python -m scripts.fix_premium_over_issuance --apply --yes --limit 20

Exit code: 0 ok, 1 finished with user errors, 2 DB / panel unavailable, 3 not confirmed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

from app.services import premium_repair

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("fix_premium_over_issuance")


def _default_out() -> str:
    return f"premium_over_issuance_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"


def _confirmed(yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print("not confirmed: pass --yes (no interactive terminal)")
        return False
    return input("Type 'yes' to PATCH these premium entities: ").strip().lower() == "yes"


def _make_bot():
    """A Bot for the one admin alert (the CLI has no webhook bot). None if no token."""
    import config
    if not getattr(config, "BOT_TOKEN", None):
        return None
    from aiogram import Bot
    return Bot(token=config.BOT_TOKEN)


async def _main(*, apply: bool, yes: bool, limit: Optional[int], out: Optional[str]) -> int:
    # Only a pool — never init_db(): run from outside it would re-run the
    # migrations and the inline ALTER TABLE ... IF NOT EXISTS (ACCESS EXCLUSIVE
    # locks) on the live production DB.
    from database.core import get_pool
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:  # noqa: BLE001
        print(f"database unavailable: {type(e).__name__}")
        return 2
    try:
        plan = await premium_repair.build_plan()
    except premium_repair.PanelUnavailable as e:
        print(f"panel unavailable: {e}")
        return 2
    except RuntimeError as e:
        print(f"unavailable: {e}")
        return 2
    out = out or _default_out()
    premium_repair.write_csv(plan["rows"], out)
    print(premium_repair.format_summary(premium_repair.summarize(plan)))
    print(f"report: {out}")
    if not apply:
        print("dry run: nothing changed (--apply sets the dates)")
        return 0
    todo = sum(1 for r in plan["rows"] if r["action"] == "would_fix")
    if limit is not None:
        todo = min(todo, limit)
    print(f"about to PATCH {todo} premium entities (≤ {premium_repair.RATE_PER_SEC:g}/s)")
    if not _confirmed(yes):
        return 3
    await premium_repair.apply_plan(plan, limit=limit)
    premium_repair.write_csv(plan["rows"], out)
    summary = premium_repair.summarize(plan)
    print(premium_repair.format_summary(summary))
    print(f"report: {out}")
    bot = None
    try:
        bot = _make_bot()
        await premium_repair.send_summary_alert(summary, bot=bot)
    except Exception as e:  # noqa: BLE001 — the alert never fails the run
        logger.warning("alert failed: %s", type(e).__name__)
    finally:
        session = getattr(bot, "session", None)
        if session is not None:
            await session.close()
    return 1 if summary["actions"].get("error") else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Set premium expireAt (> 5 years ahead) by the user's real purchases.")
    p.add_argument("--apply", action="store_true", help="send the PATCHes (default: dry run)")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation with --apply")
    p.add_argument("--limit", type=int, default=None, help="at most N PATCHes this run")
    p.add_argument("--out", default=None, help="CSV report path (default: ./premium_over_issuance_<UTC>.csv)")
    args = p.parse_args(argv)
    return asyncio.run(_main(apply=args.apply, yes=args.yes, limit=args.limit, out=args.out))


if __name__ == "__main__":
    sys.exit(main())
