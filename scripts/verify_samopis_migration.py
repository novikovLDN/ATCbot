#!/usr/bin/env python3
"""
Verify the samopis → Remnawave premium migration is complete.

READ-ONLY.  Never writes to DB or panel.  Safe to run on prod.

Checks:
  1. DB counters: migrated, pending, cache coverage (sub_url, short_uuid),
     orphan rows where samopis_migrated_at was stamped but uuid is NULL.
  2. Sample rows from each bucket (--sample N, default 10).
  3. Optional --check-panel: for N sampled migrated rows, GET
     /api/users/{uuid} and verify the entity exists, has status=ACTIVE,
     description contains "samopis", telegramId matches, and the
     MainServer squad is assigned.

Usage:
    python -m scripts.verify_samopis_migration                       # DB summary + samples
    python -m scripts.verify_samopis_migration --check-panel         # also probe the panel
    python -m scripts.verify_samopis_migration --sample 50           # bigger samples
    python -m scripts.verify_samopis_migration --check-panel --panel-sample 100

Exit codes:
    0  all clean
    1  config / DB problem
    2  inconsistencies found — read the report
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import database  # noqa: E402
from app.services import remnawave_api  # noqa: E402

logging.basicConfig(
    level=logging.WARNING,                # keep DB / migrations log noise out
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("verify_migration")


# ── DB queries ─────────────────────────────────────────────────────────

async def _db_summary() -> dict:
    """Single round-trip to gather the bucket counters."""
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        migrated = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE remnawave_premium_uuid IS NOT NULL "
            "  AND remnawave_premium_uuid != '' "
            "  AND samopis_migrated_at IS NOT NULL"
        )
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE status = 'active' "
            "  AND uuid IS NOT NULL AND uuid != '' "
            "  AND expires_at > NOW() "
            "  AND subscription_type IS DISTINCT FROM 'trial' "
            "  AND (remnawave_premium_uuid IS NULL OR remnawave_premium_uuid = '')"
        )
        with_sub_url = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE remnawave_premium_uuid IS NOT NULL "
            "  AND remnawave_premium_sub_url IS NOT NULL "
            "  AND remnawave_premium_sub_url != ''"
        )
        with_short = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE remnawave_premium_uuid IS NOT NULL "
            "  AND remnawave_premium_short_uuid IS NOT NULL "
            "  AND remnawave_premium_short_uuid != ''"
        )
        orphans = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE samopis_migrated_at IS NOT NULL "
            "  AND (remnawave_premium_uuid IS NULL OR remnawave_premium_uuid = '')"
        )
    return {
        "migrated": int(migrated or 0),
        "pending": int(pending or 0),
        "with_sub_url": int(with_sub_url or 0),
        "with_short_uuid": int(with_short or 0),
        "orphans": int(orphans or 0),
    }


async def _population_breakdown() -> dict:
    """Show why "total candidates" is smaller than the full subscriptions
    table: trial users / expired / non-active rows are excluded by design.

    Returns a flat dict of named bucket counts so the operator can see
    where every row in `subscriptions` lives.
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        total_rows = await conn.fetchval("SELECT COUNT(*) FROM subscriptions")
        with_legacy = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE uuid IS NOT NULL AND uuid != ''"
        )
        active_paid_unexpired = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE uuid IS NOT NULL AND uuid != '' "
            "  AND status = 'active' "
            "  AND subscription_type IS DISTINCT FROM 'trial' "
            "  AND expires_at > NOW()"
        )
        active_paid_expired = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE uuid IS NOT NULL AND uuid != '' "
            "  AND status = 'active' "
            "  AND subscription_type IS DISTINCT FROM 'trial' "
            "  AND expires_at <= NOW()"
        )
        active_trial = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE uuid IS NOT NULL AND uuid != '' "
            "  AND status = 'active' "
            "  AND subscription_type = 'trial'"
        )
        inactive = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE uuid IS NOT NULL AND uuid != '' "
            "  AND status IS DISTINCT FROM 'active'"
        )
        no_legacy_uuid = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions "
            "WHERE uuid IS NULL OR uuid = ''"
        )
    return {
        "total_rows": int(total_rows or 0),
        "with_legacy_uuid": int(with_legacy or 0),
        "active_paid_unexpired": int(active_paid_unexpired or 0),
        "active_paid_expired": int(active_paid_expired or 0),
        "active_trial": int(active_trial or 0),
        "inactive": int(inactive or 0),
        "no_legacy_uuid": int(no_legacy_uuid or 0),
    }


async def _sample_migrated(limit: int) -> list[dict]:
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, uuid, remnawave_premium_uuid, "
            "       remnawave_premium_sub_url, remnawave_premium_short_uuid, "
            "       samopis_migrated_at "
            "FROM subscriptions "
            "WHERE remnawave_premium_uuid IS NOT NULL "
            "  AND samopis_migrated_at IS NOT NULL "
            "ORDER BY samopis_migrated_at DESC "
            "LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]


async def _sample_pending(limit: int) -> list[dict]:
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, uuid, expires_at, subscription_type "
            "FROM subscriptions "
            "WHERE status = 'active' "
            "  AND uuid IS NOT NULL AND uuid != '' "
            "  AND expires_at > NOW() "
            "  AND subscription_type IS DISTINCT FROM 'trial' "
            "  AND (remnawave_premium_uuid IS NULL OR remnawave_premium_uuid = '') "
            "ORDER BY telegram_id "
            "LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]


# ── Panel verification ─────────────────────────────────────────────────

def _extract_squad_uuids(entity: dict) -> list[str]:
    """Panel responses use either ['uuid', ...] or [{'uuid': '…'}, …]."""
    out = []
    for s in entity.get("activeInternalSquads") or []:
        if isinstance(s, str):
            out.append(s)
        elif isinstance(s, dict) and s.get("uuid"):
            out.append(s["uuid"])
    return out


async def _check_panel_sample(rows: list[dict]) -> list[dict]:
    """For each sampled migrated row, fetch the panel entity and validate."""
    main_squad = (getattr(config, "REMNAWAVE_MAIN_SQUAD_UUID", "") or "").strip()
    results = []
    for row in rows:
        tg = row["telegram_id"]
        uuid = row["remnawave_premium_uuid"]
        try:
            entity = await remnawave_api.get_user(uuid)
        except Exception as e:
            results.append({
                "telegram_id": tg, "uuid": uuid,
                "status": "error", "reason": f"{type(e).__name__}: {e}",
            })
            continue
        if not entity:
            results.append({
                "telegram_id": tg, "uuid": uuid,
                "status": "panel_404", "reason": "entity not found in panel",
            })
            continue

        problems: list[str] = []
        if entity.get("status") != "ACTIVE":
            problems.append(f"status={entity.get('status')}")
        desc = (entity.get("description") or "").lower()
        if "samopis" not in desc:
            problems.append("description missing samopis marker")
        tg_field = entity.get("telegramId") if entity.get("telegramId") is not None else entity.get("telegram_id")
        if tg_field is not None:
            try:
                if int(tg_field) != int(tg):
                    problems.append(f"telegramId mismatch (panel={tg_field})")
            except (TypeError, ValueError):
                problems.append(f"telegramId not int ({tg_field!r})")
        squad_uuids = _extract_squad_uuids(entity)
        if main_squad and main_squad not in squad_uuids:
            problems.append("not in MainServer squad")
        results.append({
            "telegram_id": tg,
            "uuid": uuid,
            "status": "ok" if not problems else "issue",
            "reason": "; ".join(problems),
            "panel_username": entity.get("username"),
        })
    return results


# ── Main flow ──────────────────────────────────────────────────────────

async def _run(args) -> int:
    await database.init_db()
    if not getattr(database, "DB_READY", False):
        print("ERROR: DB initialisation failed", file=sys.stderr)
        return 1

    summary = await _db_summary()
    population = await _population_breakdown()
    total = summary["migrated"] + summary["pending"]
    pct = (summary["migrated"] / total * 100.0) if total else 0.0

    bar = "═" * 64
    print()
    print(bar)
    print("samopis → Remnawave premium migration — verification")
    print(bar)
    print(f"  Migrated:                 {summary['migrated']:>6} / {total} ({pct:.1f}%)")
    print(f"  Pending:                  {summary['pending']:>6}")
    print(f"  With sub_url cached:      {summary['with_sub_url']:>6}  "
          f"({summary['with_sub_url']}/{summary['migrated'] or 1} of migrated)")
    print(f"  With short_uuid cached:   {summary['with_short_uuid']:>6}")
    print(f"  Orphan rows:              {summary['orphans']:>6}  "
          "(migrated_at NOT NULL but uuid empty — should be 0)")
    print()
    # Why is "total candidates" smaller than the full subscriptions table?
    # Show the breakdown so the operator sees where every row lives.
    print("── Subscription population breakdown ──")
    print(f"  All rows in subscriptions table:       {population['total_rows']:>6}")
    print(f"  ├─ Without legacy samopis uuid:        {population['no_legacy_uuid']:>6}  (no migration needed)")
    print(f"  └─ With legacy samopis uuid:           {population['with_legacy_uuid']:>6}")
    print(f"     ├─ Active paid + unexpired:         {population['active_paid_unexpired']:>6}  ← migration target")
    print(f"     ├─ Active paid + already expired:   {population['active_paid_expired']:>6}  (excluded by design)")
    print(f"     ├─ Trial (any state):               {population['active_trial']:>6}  (excluded by design)")
    print(f"     └─ Inactive (blocked/cancelled/…):  {population['inactive']:>6}  (excluded by design)")
    print()

    exit_code = 0

    if summary["pending"] > 0:
        print(f"  ⚠️ {summary['pending']} pending rows — migration NOT complete.")
        exit_code = 2
    if summary["orphans"] > 0:
        print(f"  ⚠️ {summary['orphans']} orphan rows — partial write detected.")
        exit_code = 2

    cache_missing = summary["migrated"] - summary["with_sub_url"]
    if cache_missing > 0:
        # Informational — the subscription_proxy back-fills these lazily,
        # but a non-zero count means some users haven't hit the proxy yet.
        print(f"  ℹ️ {cache_missing} migrated rows missing sub_url cache "
              "(subscription_proxy back-fills on first /sub/{uuid} hit).")

    if args.sample > 0 and summary["migrated"] > 0:
        print()
        print(f"── Last {args.sample} migrated rows (newest first) ──")
        for r in await _sample_migrated(args.sample):
            samopis = (r.get("uuid") or "")[:8]
            panel = (r.get("remnawave_premium_uuid") or "")[:8]
            sub = "✓" if r.get("remnawave_premium_sub_url") else "✗"
            short = "✓" if r.get("remnawave_premium_short_uuid") else "✗"
            print(f"  tg={r['telegram_id']:>11}  legacy={samopis}…  "
                  f"panel={panel}…  sub_url={sub}  short_uuid={short}  "
                  f"at={r['samopis_migrated_at']}")

    if args.sample > 0 and summary["pending"] > 0:
        print()
        print(f"── First {args.sample} pending rows ──")
        for r in await _sample_pending(args.sample):
            print(f"  tg={r['telegram_id']:>11}  legacy={(r['uuid'] or '')[:8]}…  "
                  f"type={r['subscription_type']}  expires_at={r['expires_at']}")

    if args.check_panel:
        if not config.REMNAWAVE_ENABLED:
            print()
            print("  ⚠️ REMNAWAVE_API_URL/TOKEN not set — skipping panel check")
        elif summary["migrated"] == 0:
            print()
            print("  ⚠️ Nothing migrated yet — skipping panel check")
        else:
            print()
            print(f"── Panel verification (sampling {args.panel_sample} migrated entities) ──")
            rows = await _sample_migrated(args.panel_sample)
            results = await _check_panel_sample(rows)
            ok = sum(1 for r in results if r["status"] == "ok")
            issues = [r for r in results if r["status"] != "ok"]
            for r in results:
                marker = "✓" if r["status"] == "ok" else "✗"
                line = f"  {marker} tg={r['telegram_id']:>11}  uuid={r['uuid'][:8]}…  {r['status']}"
                if r.get("reason"):
                    line += f" — {r['reason']}"
                print(line)
            print()
            print(f"  Panel sample: {ok}/{len(results)} ok, {len(issues)} with issues")
            if issues:
                exit_code = 2

    print()
    print(bar)
    if exit_code == 0:
        print("✅ All clean.")
    elif exit_code == 2:
        print("⚠️ Inconsistencies found — see warnings above.")
    print(bar)
    print()
    return exit_code


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only verification of the samopis → Remnawave premium "
            "migration.  Counts DB buckets, lists sample rows, and "
            "optionally probes the Remnawave panel for sampled entities."
        )
    )
    parser.add_argument(
        "--sample", type=int, default=10,
        help="Number of sample rows from each bucket (default 10, 0 to disable)",
    )
    parser.add_argument(
        "--check-panel", action="store_true",
        help="Also GET /api/users/{uuid} on sampled migrated entities",
    )
    parser.add_argument(
        "--panel-sample", type=int, default=20,
        help="How many migrated entities to verify against the panel (default 20)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse_args())))
