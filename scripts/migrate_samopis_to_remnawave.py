#!/usr/bin/env python3
"""
samopis (vpnapi master) → Remnawave PREMIUM migration script.

For every active paid subscription that still lives on the legacy samopis
(self-written vpnapi at 138.124.90.195) this script creates a SECOND user
entity in the Remnawave panel.  That entity:

  - belongs to the "MainServer" squad (config.REMNAWAVE_MAIN_SQUAD_UUID)
  - has trafficLimitBytes = 0 (unlimited)
  - has trafficLimitStrategy = NO_RESET
  - has expireAt copied from subscriptions.expires_at
  - has telegramId = the user's Telegram id
  - has description = "Imported from samopis vpnapi"
  - tries to use the legacy samopis uuid as its full uuid (controlled by
    config.REMNAWAVE_PREMIUM_FORCE_UUID).  On 400/409/422 it retries without
    the forced uuid and accepts whatever the panel assigns.

The resulting (telegram_id → panel uuid) mapping is persisted in:
  - subscriptions.remnawave_premium_uuid
  - subscriptions.samopis_migrated_at
  - migration_log.csv (next to the script's working dir by default)

Usage:
    # Dry-run: list candidates, no API or DB writes (default)
    python -m scripts.migrate_samopis_to_remnawave

    # Apply for real
    python -m scripts.migrate_samopis_to_remnawave --apply

    # Test on a single user
    python -m scripts.migrate_samopis_to_remnawave --apply --telegram-id 210948123

    # Resume after a partial run (the default — already-migrated rows are
    # excluded by the SQL query).  Pass --include-already-migrated to re-run.
    python -m scripts.migrate_samopis_to_remnawave --apply --resume

    # Throttle to N requests per second (default 5)
    python -m scripts.migrate_samopis_to_remnawave --apply --rate 3

    # Limit batch size
    python -m scripts.migrate_samopis_to_remnawave --apply --limit 100

    # Override log path
    python -m scripts.migrate_samopis_to_remnawave --apply --log-file ./out/migration.csv

Environment variables (required for --apply):
    STAGE_/PROD_DATABASE_URL, STAGE_/PROD_REMNAWAVE_API_URL,
    STAGE_/PROD_REMNAWAVE_API_TOKEN, STAGE_/PROD_REMNAWAVE_MAIN_SQUAD_UUID

Exit codes:
    0  success (all candidates processed or none found)
    1  config / DB problem (nothing was migrated)
    2  one or more rows failed; CSV log contains the errors
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import csv
import errno
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import database  # noqa: E402
from app.services import remnawave_premium  # noqa: E402


# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("samopis_migration")


def _jlog(level: int, event: str, **fields) -> None:
    """Emit a structured JSON log line."""
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    logger.log(level, json.dumps(payload, default=str, ensure_ascii=False))


# ── Rate limiter ───────────────────────────────────────────────────────

class _RateLimiter:
    """Simple async rate limiter — caps requests/sec.

    Token bucket would be overkill here; we just sleep until the next slot.
    """

    def __init__(self, rps: float):
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.min_interval = 1.0 / rps
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# ── CSV log writer ─────────────────────────────────────────────────────

CSV_FIELDS = [
    "timestamp",
    "telegram_id",
    "uuid_samopis",
    "uuid_remnawave_bypass",   # the existing remnawave_uuid (bypass tier, may be NULL)
    "uuid_remnawave_premium",  # the NEW UUID created or recovered by this script
    "forced_uuid_accepted",
    "recovered",               # True when the entity already existed in the panel
    "status",                  # ok | recovered | failed | dry-run
    "http_status",
    "subscription_url",
    "error",
]


@dataclass
class LogRow:
    timestamp: str
    telegram_id: int
    uuid_samopis: str
    uuid_remnawave_bypass: Optional[str]
    uuid_remnawave_premium: Optional[str]
    forced_uuid_accepted: bool
    recovered: bool
    status: str
    http_status: int
    subscription_url: Optional[str]
    error: Optional[str]


class _CsvLog:
    def __init__(self, path: Path, dry_run: bool):
        self.path = path
        self.dry_run = dry_run
        self._fh = None
        self._writer: Optional[csv.DictWriter] = None

    def __enter__(self):
        # Append so resumed runs accumulate in the same file
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS)
        if new_file:
            self._writer.writeheader()
        return self

    def __exit__(self, *exc):
        if self._fh:
            self._fh.flush()
            self._fh.close()

    def write(self, row: LogRow) -> None:
        if self._writer is None:
            return
        self._writer.writerow(asdict(row))
        self._fh.flush()


# ── Config validation ─────────────────────────────────────────────────

def _validate_apply_config() -> Optional[str]:
    """Return a human-readable reason string if --apply is not safe, else None."""
    if not config.REMNAWAVE_ENABLED:
        return "REMNAWAVE_API_URL / REMNAWAVE_API_TOKEN are not set"
    if not getattr(config, "REMNAWAVE_MAIN_SQUAD_UUID", ""):
        return "REMNAWAVE_MAIN_SQUAD_UUID is not set — refuse to create entities without a squad"
    return None


# ── PID lock (single-writer guarantee for --apply) ─────────────────────

class LockHeldError(RuntimeError):
    """Raised when another instance of the script is already running."""


def _pid_is_alive(pid: int) -> bool:
    """Return True if process `pid` exists on this host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The PID exists but is owned by another user — still alive.
        return True
    except OSError as e:
        # ESRCH is "no such process"; anything else we treat as alive to be safe.
        return e.errno != errno.ESRCH
    return True


def acquire_pid_lock(lock_path: Path) -> None:
    """Refuse to run if another --apply instance is in progress.

    Stale lock files (PID no longer alive) are removed automatically.  On
    success the current PID is written and a cleanup hook is registered so
    the file is removed on normal exit (including KeyboardInterrupt).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip() or "0")
        except (ValueError, OSError):
            old_pid = 0
        if old_pid and _pid_is_alive(old_pid):
            raise LockHeldError(
                f"another migration instance is running (PID {old_pid}); "
                f"remove {lock_path} manually only if you are sure that process is dead"
            )
        # Stale lock — drop it.
        logger.warning("Removing stale lock file %s (PID %s no longer alive)", lock_path, old_pid)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

    lock_path.write_text(str(os.getpid()))

    def _release() -> None:
        try:
            # Only remove the lock if it still belongs to us — otherwise
            # another concurrent instance might have stolen it after we
            # crashed and we don't want to delete its lock.
            current = int(lock_path.read_text().strip() or "0")
            if current == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (FileNotFoundError, ValueError, OSError):
            pass

    atexit.register(_release)


# ── Per-row processing ─────────────────────────────────────────────────

async def _process_one(
    row: dict,
    *,
    apply: bool,
    rate_limiter: _RateLimiter,
) -> LogRow:
    tg = int(row["telegram_id"])
    samopis_uuid = row["uuid"]
    bypass_uuid = row.get("remnawave_uuid")
    expires_at = row["expires_at"]

    base = LogRow(
        timestamp=datetime.now(timezone.utc).isoformat(),
        telegram_id=tg,
        uuid_samopis=samopis_uuid or "",
        uuid_remnawave_bypass=bypass_uuid or None,
        uuid_remnawave_premium=None,
        forced_uuid_accepted=False,
        recovered=False,
        status="dry-run",
        http_status=0,
        subscription_url=None,
        error=None,
    )

    if not apply:
        _jlog(logging.INFO, "dry_run.candidate",
              telegram_id=tg, samopis_uuid=samopis_uuid[:8] if samopis_uuid else None,
              expires_at=str(expires_at), subscription_type=row.get("subscription_type"))
        return base

    # ── apply path ─────────────────────────────────────────────────────
    await rate_limiter.acquire()
    try:
        result = await remnawave_premium.create_premium_user_entity(
            tg,
            requested_uuid=samopis_uuid,
            expire_at=expires_at,
            existing_username=None,
        )
    except Exception as e:
        _jlog(logging.ERROR, "create.exception",
              telegram_id=tg, error=str(e), exc_type=type(e).__name__)
        base.status = "failed"
        base.error = f"{type(e).__name__}: {e}"
        return base

    if not result.ok:
        _jlog(logging.ERROR, "create.failed",
              telegram_id=tg, http_status=result.status, error=result.error)
        base.status = "failed"
        base.http_status = result.status
        base.error = result.error
        base.uuid_remnawave_premium = result.panel_uuid  # may be set when error==conflict_unrelated_user
        return base

    # Persist (uuid, sub_url) in one atomic UPDATE so the fallback router
    # never needs to call the panel just to learn the subscription URL.
    try:
        await database.set_remnawave_premium_uuid_and_url(
            tg, result.panel_uuid or "", result.subscription_url
        )
    except Exception as e:
        _jlog(logging.ERROR, "persist.failed", telegram_id=tg,
              panel_uuid=(result.panel_uuid or "")[:8], error=str(e))
        base.status = "failed"
        base.uuid_remnawave_premium = result.panel_uuid
        base.subscription_url = result.subscription_url
        base.http_status = result.status
        base.forced_uuid_accepted = result.forced_uuid_accepted
        base.recovered = result.recovered
        base.error = f"db_persist_error: {type(e).__name__}: {e}"
        return base

    base.status = "recovered" if result.recovered else "ok"
    base.uuid_remnawave_premium = result.panel_uuid
    base.subscription_url = result.subscription_url
    base.http_status = result.status
    base.forced_uuid_accepted = result.forced_uuid_accepted
    base.recovered = result.recovered

    _jlog(logging.INFO,
          "migrated.recovered" if result.recovered else "migrated.created",
          telegram_id=tg,
          uuid_samopis=samopis_uuid[:8] if samopis_uuid else None,
          panel_uuid=(result.panel_uuid or "")[:8],
          forced_uuid_accepted=result.forced_uuid_accepted,
          recovered=result.recovered,
          subscription_url=result.subscription_url)
    return base


# ── Main flow ──────────────────────────────────────────────────────────

async def _run(args) -> int:
    # Init DB (also runs pending migrations, including 045/046 if not yet applied)
    await database.init_db()
    if not getattr(database, "DB_READY", False):
        logger.error("DB initialisation failed — aborting")
        return 1

    if args.apply:
        problem = _validate_apply_config()
        if problem:
            logger.error("Refusing --apply: %s", problem)
            return 1
        # Single-writer guarantee — only one --apply instance at a time.
        lock_path = Path(args.lock_file) if args.lock_file else Path(args.log_file + ".lock")
        try:
            acquire_pid_lock(lock_path)
            logger.info("Acquired PID lock %s (pid=%s)", lock_path, os.getpid())
        except LockHeldError as e:
            logger.error("%s", e)
            return 1

    candidates: List[dict] = await database.list_subscriptions_for_premium_migration(
        limit=args.limit,
        telegram_id=args.telegram_id,
        include_already_migrated=args.include_already_migrated,
    )

    if not candidates:
        logger.info("No migration candidates found. Nothing to do.")
        return 0

    logger.info(
        "Found %d candidate(s) (limit=%s, single=%s, include_migrated=%s, apply=%s, rate=%.1f rps)",
        len(candidates), args.limit, args.telegram_id,
        args.include_already_migrated, args.apply, args.rate,
    )

    rate_limiter = _RateLimiter(args.rate)
    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    ok = recovered = skipped = failed = 0
    with _CsvLog(log_path, dry_run=not args.apply) as csv_log:
        for idx, row in enumerate(candidates, start=1):
            try:
                out = await _process_one(row, apply=args.apply, rate_limiter=rate_limiter)
            except Exception as e:
                _jlog(logging.ERROR, "process_one.crash",
                      telegram_id=row.get("telegram_id"),
                      error=str(e), exc_type=type(e).__name__)
                out = LogRow(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    telegram_id=int(row.get("telegram_id") or 0),
                    uuid_samopis=row.get("uuid") or "",
                    uuid_remnawave_bypass=row.get("remnawave_uuid"),
                    uuid_remnawave_premium=None,
                    forced_uuid_accepted=False,
                    recovered=False,
                    status="failed",
                    http_status=0,
                    subscription_url=None,
                    error=f"{type(e).__name__}: {e}",
                )

            csv_log.write(out)
            if out.status == "ok":
                ok += 1
            elif out.status == "recovered":
                recovered += 1
            elif out.status == "dry-run":
                skipped += 1
            else:
                failed += 1

            if idx % 50 == 0:
                logger.info(
                    "Progress: %d/%d (ok=%d recovered=%d failed=%d)",
                    idx, len(candidates), ok, recovered, failed,
                )

    logger.info(
        "Done. ok=%d recovered=%d failed=%d dry-run=%d total=%d. Log: %s",
        ok, recovered, failed, skipped, len(candidates), log_path,
    )
    if failed > 0:
        return 2
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate active paid samopis subscriptions to a Remnawave premium "
            "(MainServer squad) user entity. Dry-run by default."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually call the Remnawave API and write to the DB (default: dry-run only)",
    )
    parser.add_argument(
        "--telegram-id",
        type=int,
        default=None,
        help="Restrict to a single Telegram user (manual verification)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Hard cap on the number of candidates processed in one run",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=5.0,
        help="Maximum Remnawave POST requests per second (default 5)",
    )
    parser.add_argument(
        "--log-file",
        default="migration_log.csv",
        help="Path to the per-row CSV log (default ./migration_log.csv)",
    )
    parser.add_argument(
        "--lock-file",
        default=None,
        help="Path to the PID lock file (default: <log-file>.lock). "
             "Ignored without --apply.",
    )
    parser.add_argument(
        "--include-already-migrated",
        action="store_true",
        help="Include rows that already have remnawave_premium_uuid set",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Alias for the default behaviour (already-migrated rows are skipped). "
             "Provided for clarity in runbooks.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse_args())))
