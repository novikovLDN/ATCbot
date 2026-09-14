"""CLI-обёртка вокруг app.services.panel_traffic_audit.

Скрипт для локального прогона аудита (без dashboard):
  python -m scripts.audit_bypass_traffic_mismatch                    # dry-run
  python -m scripts.audit_bypass_traffic_mismatch --user 8343902286  # один
  python -m scripts.audit_bypass_traffic_mismatch --fix              # PATCH mismatches
  python -m scripts.audit_bypass_traffic_mismatch --limit 200 --fix  # первые 200

Идемпотентен: повторный запуск не изменяет уже починенные.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import config
import database
from app.services.panel_traffic_audit import (
    AuditResult,
    apply_fix,
    run_audit,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("audit_bypass_traffic")


def _fmt_gb(b: int) -> str:
    return f"{b / (1024 ** 3):.2f} GB"


def _print_report(results: list[AuditResult]) -> None:
    matches = [r for r in results if r.kind == "match"]
    mismatches = [r for r in results if r.kind == "mismatch"]
    no_entity = [r for r in results if r.kind == "no_entity"]
    errors = [r for r in results if r.kind == "panel_error"]
    total_short = sum(r.shortfall_bytes for r in mismatches)

    print("\n" + "=" * 76)
    print(f"AUDIT REPORT — {len(results)} users audited")
    print("=" * 76)
    print(f"  match:       {len(matches):>6}  ({len(matches)/max(1,len(results))*100:.1f}%)")
    print(f"  MISMATCH:    {len(mismatches):>6}  shortfall total: {_fmt_gb(total_short)}")
    print(f"  no_entity:   {len(no_entity):>6}")
    print(f"  panel_error: {len(errors):>6}")
    print()
    if mismatches:
        print("MISMATCH (shortfall descending):")
        for r in sorted(mismatches, key=lambda x: -x.shortfall_bytes)[:50]:
            print(
                f"  tg={r.tg:<12}  status={r.panel_status:<10}  "
                f"expected={_fmt_gb(r.expected_bytes):>10}  "
                f"panel={_fmt_gb(r.actual_bytes):>10}  "
                f"used={_fmt_gb(r.used_bytes):>10}  "
                f"shortfall={_fmt_gb(r.shortfall_bytes):>10}"
            )
        if len(mismatches) > 50:
            print(f"  ... +{len(mismatches)-50} more")
    if no_entity:
        print("\nNO_ENTITY (первые 20):")
        for r in no_entity[:20]:
            print(f"  tg={r.tg:<12}  expected={_fmt_gb(r.expected_bytes)}  note={r.note}")
    if errors:
        print("\nPANEL_ERROR (первые 20):")
        for r in errors[:20]:
            print(f"  tg={r.tg:<12}  note={r.note}")
    print()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--fix", action="store_true",
                    help="PATCH bypass entity's trafficLimitBytes до expected+used")
    ap.add_argument("--limit", type=int, default=None,
                    help="Обработать первые N юзеров (тест)")
    ap.add_argument("--user", type=int, default=None,
                    help="Один telegram_id")
    ap.add_argument("--concurrent", type=int, default=5,
                    help="Max concurrent GET/PATCH запросов к панели (default 5)")
    ap.add_argument("--batch-sleep", type=float, default=0.2,
                    help="Sleep между батчами (sec, default 0.2)")
    args = ap.parse_args()

    if not config.REMNAWAVE_ENABLED:
        print("ERROR: REMNAWAVE_ENABLED=false — нечего аудировать", file=sys.stderr)
        return 2

    try:
        await database.init_db()
    except Exception as e:
        print(f"ERROR: init_db failed: {e}", file=sys.stderr)
        return 2

    def _progress(done: int, total: int) -> None:
        logger.info("Progress: %d/%d audited", done, total)

    results = await run_audit(
        limit=args.limit,
        only_tg=args.user,
        concurrent=args.concurrent,
        batch_sleep=args.batch_sleep,
        progress_cb=_progress,
    )
    if not results:
        print("No users to audit.")
        return 0

    _print_report(results)

    mismatches = [r for r in results if r.kind == "mismatch"]
    if not mismatches:
        logger.info("No mismatches. Bye.")
        return 0

    if not args.fix:
        print(f"Run with --fix to PATCH the {len(mismatches)} mismatched users.")
        return 0

    logger.info("Applying fixes...")
    fixed = 0
    for r in mismatches:
        out = await apply_fix(r)
        if out.get("ok"):
            fixed += 1
        else:
            logger.warning("apply_fix tg=%s failed: %s", r.tg, out.get("reason"))
        await asyncio.sleep(max(0.0, args.batch_sleep) / max(1, args.concurrent))
    logger.info("Fix done. Applied to %d/%d users.", fixed, len(mismatches))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
