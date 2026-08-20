"""Audit + repair Remnawave bypass trafficLimitBytes mismatches.

Проблема: у юзера в БД зафиксировано N GB (subscription base +
traffic_purchases), но в панели Remnawave `trafficLimitBytes` меньше
(или 0) — юзер недополучил трафик.

Скрипт:
  1. Собирает все активные bypass entities (subscriptions с
     remnawave_uuid либо remnawave_id NOT NULL).
  2. По каждому:
     - expected_bytes = subscription_base_bytes + Σ traffic_purchases.gb_amount * 1024**3
       subscription_base_bytes:
         combo_basic/combo_plus → COMBO_TARIFFS[tariff][period]["gb"] * 1024**3
         basic/plus            → TRAFFIC_LIMITS[tariff][period]  (в bytes)
         trial                 → TRIAL_BYPASS_MB * 1024**2
     - actual_bytes = panel.trafficLimitBytes
     - used_bytes   = panel.usedTrafficBytes
  3. Определяет mismatch:
     - shortfall = expected_bytes − actual_bytes
     - report если shortfall > 100 MB (округления).
  4. С --fix: PATCH trafficLimitBytes = expected_bytes + used_bytes
     (юзер получает ровно expected remaining, used история сохраняется).

Rate-limit: max_concurrent панель-запросов, пауза между батчами.

Запуск:
  python -m scripts.audit_bypass_traffic_mismatch                     # dry-run report
  python -m scripts.audit_bypass_traffic_mismatch --fix                # применить PATCH
  python -m scripts.audit_bypass_traffic_mismatch --limit 100          # первые 100 юзеров
  python -m scripts.audit_bypass_traffic_mismatch --user 8343902286    # один юзер
  python -m scripts.audit_bypass_traffic_mismatch --concurrent 3       # снизить нагрузку

Идемпотентен: повторный запуск на уже починенных не изменит state
(shortfall уже 0).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Optional

import config
import database
from app.services import remnawave_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("audit_bypass_traffic")

# Порог shortfall для отчёта — округления при GiB↔bytes конверсии могут
# дать несколько MB, игнорируем.
SHORTFALL_TOLERANCE_BYTES = 100 * 1024 * 1024   # 100 MB


@dataclass
class UserRow:
    telegram_id: int
    subscription_type: str
    period_days: Optional[int]
    is_bypass_only: bool
    remnawave_uuid: Optional[str]
    remnawave_id: Optional[int]
    traffic_purchases_gb: int   # Σ traffic_purchases.gb_amount


@dataclass
class AuditResult:
    tg: int
    expected_bytes: int
    actual_bytes: int
    used_bytes: int
    shortfall_bytes: int
    panel_status: str
    kind: str                   # "match" | "mismatch" | "no_entity" | "panel_error"
    note: str = ""


def _fmt_gb(b: int) -> str:
    return f"{b / (1024 ** 3):.2f} GB"


def _subscription_base_bytes(sub_type: str, period_days: Optional[int]) -> int:
    """GB-лимит от тарифа подписки (не считая купленные traffic packs).

    Правила:
      trial               → TRIAL_BYPASS_MB MB
      combo_basic/plus    → COMBO_TARIFFS[t][p]["gb"] GB
      basic / plus        → TRAFFIC_LIMITS[t][p] (уже bytes)
      всё остальное       → 0 (bypass-only, apple_id, spotify, gift, farm...)
    """
    sub_type = (sub_type or "").strip().lower()
    p = int(period_days or 30)
    if sub_type == "trial":
        return int(getattr(config, "TRIAL_BYPASS_MB", 500)) * (1024 ** 2)
    combo = getattr(config, "COMBO_TARIFFS", {}) or {}
    if sub_type in combo:
        per_period = combo[sub_type].get(p) or {}
        gb = int(per_period.get("gb") or 0)
        return gb * (1024 ** 3)
    limits = getattr(config, "TRAFFIC_LIMITS", {}) or {}
    if sub_type in limits:
        table = limits[sub_type]
        if isinstance(table, dict):
            if p in table:
                return int(table[p])
            available = sorted(table.keys())
            for cand in available:
                if cand >= p:
                    return int(table[cand])
            if available:
                return int(table[available[-1]])
        if isinstance(table, int):
            return int(table)
    return 0


async def _fetch_candidates(limit: Optional[int], only_tg: Optional[int]) -> list[UserRow]:
    """Собрать список юзеров для аудита из subscriptions + traffic_purchases."""
    pool = await database.get_pool()
    if pool is None:
        raise RuntimeError("database pool not ready")

    sql = """
        SELECT s.telegram_id,
               COALESCE(s.subscription_type, 'basic') AS subscription_type,
               s.period_days,
               COALESCE(s.is_bypass_only, FALSE)     AS is_bypass_only,
               s.remnawave_uuid,
               s.remnawave_id,
               COALESCE((SELECT SUM(gb_amount) FROM traffic_purchases WHERE telegram_id = s.telegram_id), 0) AS gp
        FROM subscriptions s
        WHERE (s.remnawave_uuid IS NOT NULL AND s.remnawave_uuid <> '')
           OR s.remnawave_id IS NOT NULL
    """
    params: list = []
    if only_tg is not None:
        sql += " AND s.telegram_id = $1"
        params.append(int(only_tg))
    sql += " ORDER BY s.telegram_id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    out: list[UserRow] = []
    for r in rows:
        out.append(UserRow(
            telegram_id=int(r["telegram_id"]),
            subscription_type=str(r["subscription_type"] or "basic"),
            period_days=int(r["period_days"]) if r["period_days"] is not None else None,
            is_bypass_only=bool(r["is_bypass_only"]),
            remnawave_uuid=(r["remnawave_uuid"] or None),
            remnawave_id=int(r["remnawave_id"]) if r["remnawave_id"] is not None else None,
            traffic_purchases_gb=int(r["gp"] or 0),
        ))
    return out


async def _audit_one(row: UserRow) -> AuditResult:
    """Один юзер — сравнить expected vs panel."""
    # bypass-only юзеры не получают subscription base traffic (у них
    # нет активной подписки в UI-смысле, только пакеты GB).
    if row.is_bypass_only:
        expected = 0
    else:
        expected = _subscription_base_bytes(row.subscription_type, row.period_days)
    expected += int(row.traffic_purchases_gb) * (1024 ** 3)

    probe = row.remnawave_id if row.remnawave_id is not None else row.remnawave_uuid
    if probe is None:
        return AuditResult(
            tg=row.telegram_id, expected_bytes=expected, actual_bytes=0,
            used_bytes=0, shortfall_bytes=expected, panel_status="—",
            kind="no_entity", note="no remnawave_uuid AND no remnawave_id",
        )

    try:
        entity = await remnawave_api.get_user(probe)
    except Exception as e:
        return AuditResult(
            tg=row.telegram_id, expected_bytes=expected, actual_bytes=0,
            used_bytes=0, shortfall_bytes=expected, panel_status="—",
            kind="panel_error", note=f"{type(e).__name__}: {str(e)[:120]}",
        )
    if not entity:
        return AuditResult(
            tg=row.telegram_id, expected_bytes=expected, actual_bytes=0,
            used_bytes=0, shortfall_bytes=expected, panel_status="—",
            kind="no_entity", note="get_user returned None",
        )
    actual = int(entity.get("trafficLimitBytes") or 0)
    used = int(entity.get("usedTrafficBytes") or 0)
    status = str(entity.get("status") or "?")
    shortfall = max(0, expected - actual)
    is_mismatch = shortfall > SHORTFALL_TOLERANCE_BYTES and expected > 0
    return AuditResult(
        tg=row.telegram_id, expected_bytes=expected, actual_bytes=actual,
        used_bytes=used, shortfall_bytes=shortfall, panel_status=status,
        kind="mismatch" if is_mismatch else "match",
    )


async def _apply_fix(result: AuditResult, dry: bool) -> bool:
    """Поднять trafficLimitBytes до expected+used. Возвращает True если PATCH ушёл."""
    if result.kind != "mismatch" or result.shortfall_bytes <= SHORTFALL_TOLERANCE_BYTES:
        return False
    new_limit = result.expected_bytes + result.used_bytes
    if dry:
        logger.info(
            "DRY-RUN would PATCH tg=%s: limit %s → %s (used=%s, expected=%s)",
            result.tg, _fmt_gb(result.actual_bytes), _fmt_gb(new_limit),
            _fmt_gb(result.used_bytes), _fmt_gb(result.expected_bytes),
        )
        return False
    try:
        row = await _lookup_probe(result.tg)
        if row is None:
            logger.warning("apply_fix tg=%s: user vanished from DB", result.tg)
            return False
        probe = row.remnawave_id if row.remnawave_id is not None else row.remnawave_uuid
        resp = await remnawave_api.update_user(
            probe, trafficLimitBytes=new_limit, status="ACTIVE",
        )
        if resp is None:
            logger.error("apply_fix tg=%s: PATCH returned None", result.tg)
            return False
        logger.info(
            "FIXED tg=%s: limit %s → %s (+%s, expected=%s)",
            result.tg, _fmt_gb(result.actual_bytes), _fmt_gb(new_limit),
            _fmt_gb(result.shortfall_bytes), _fmt_gb(result.expected_bytes),
        )
        return True
    except Exception as e:
        logger.error("apply_fix tg=%s: %s", result.tg, e)
        return False


async def _lookup_probe(tg: int) -> Optional[UserRow]:
    """Один row для PATCH-фазы (svежий snapshot)."""
    rows = await _fetch_candidates(limit=1, only_tg=tg)
    return rows[0] if rows else None


async def _bounded_gather(coros, concurrency: int):
    """Run coros with semaphore-bounded concurrency, preserve order."""
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(c):
        async with sem:
            return await c

    return await asyncio.gather(*(_guarded(c) for c in coros))


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
                    help="Один telegram_id (для точечной диагностики)")
    ap.add_argument("--concurrent", type=int, default=5,
                    help="Max concurrent GET/PATCH запросов к панели (default 5)")
    ap.add_argument("--batch-sleep", type=float, default=0.2,
                    help="Sleep между батчами concurrent запросов (sec, default 0.2)")
    args = ap.parse_args()

    if args.concurrent < 1 or args.concurrent > 20:
        print("ERROR: --concurrent должен быть 1..20", file=sys.stderr)
        return 2

    if not config.REMNAWAVE_ENABLED:
        print("ERROR: REMNAWAVE_ENABLED=false — нечего аудировать", file=sys.stderr)
        return 2

    try:
        await database.init_db()
    except Exception as e:
        print(f"ERROR: init_db failed: {e}", file=sys.stderr)
        return 2

    logger.info("Fetching candidates from DB...")
    candidates = await _fetch_candidates(limit=args.limit, only_tg=args.user)
    logger.info("Loaded %d candidates for audit", len(candidates))
    if not candidates:
        print("No users to audit.")
        return 0

    logger.info(
        "Auditing with concurrent=%d, batch_sleep=%.2fs",
        args.concurrent, args.batch_sleep,
    )
    # Батчами по concurrent для явной паузы между волнами.
    results: list[AuditResult] = []
    batch_size = args.concurrent * 4
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        batch_results = await _bounded_gather(
            [_audit_one(row) for row in batch],
            concurrency=args.concurrent,
        )
        results.extend(batch_results)
        if i + batch_size < len(candidates) and args.batch_sleep > 0:
            await asyncio.sleep(args.batch_sleep)
        if (i // batch_size) % 5 == 0:
            logger.info("Progress: %d/%d audited", len(results), len(candidates))

    _print_report(results)

    mismatches = [r for r in results if r.kind == "mismatch"]
    if not mismatches:
        logger.info("No mismatches. Bye.")
        return 0

    if not args.fix:
        print(f"Run with --fix to PATCH the {len(mismatches)} mismatched users.")
        return 0

    logger.info("Applying fixes (concurrent=%d)...", args.concurrent)
    fixed_count = 0
    for i in range(0, len(mismatches), batch_size):
        batch = mismatches[i:i + batch_size]
        outcomes = await _bounded_gather(
            [_apply_fix(r, dry=False) for r in batch],
            concurrency=args.concurrent,
        )
        fixed_count += sum(1 for ok in outcomes if ok)
        if i + batch_size < len(mismatches) and args.batch_sleep > 0:
            await asyncio.sleep(args.batch_sleep)

    logger.info("Fix done. Applied to %d/%d users.", fixed_count, len(mismatches))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
