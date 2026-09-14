"""Bulk repair of PREMIUM panel entities whose expireAt sits years ahead.

Owner spec 2026-09-14: pull from the panel every premium entity whose
subscription runs more than five years ahead, match it to the user's real
purchases and set the date by them ("bought a year → a year"); a date in the
past becomes NOW + 1 day (the panel rejects a past expireAt).

- Scan: ONE paced panel stream (`get_all_users`), never per-user GETs.
  Candidates = username `^tg_(\\d+)_premium$` with expireAt > NOW + 5y. The
  telegram id comes from the username, not from a (possibly stale) DB uuid.
  Bypass entities (username = str(telegram_id), +10y by design) never match.
- Rule: `database.reconciliation.compute_repair_target` — the same rule as
  the dashboard «Сверка» fix.
- Apply: `database.reconciliation.repair_premium_entity` per user — fresh DB
  read, PATCH only `{id, expireAt}` by the entity's numeric id, then log +
  shorten a leaked (non bypass-only) DB date. ≤ RATE_PER_SEC PATCH/s; a
  failed user is counted and never aborts the run.

CLI: `python -m scripts.fix_premium_over_issuance` (dry run by default).
"""
from __future__ import annotations

import asyncio
import csv
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.structured_logger import log_event
from database import reconciliation as recon

logger = logging.getLogger(__name__)

RATE_PER_SEC = 2.0
CUTOFF = timedelta(days=365 * 5)
LOG_REASON = "bulk script fix_premium_over_issuance"
PREMIUM_USERNAME_RE = re.compile(r"^tg_(\d+)_premium$")

_clock = time.monotonic     # tests patch both (fake clock)
_sleep = asyncio.sleep

CSV_COLUMNS = [
    "telegram_id", "panel_id", "panel_username", "panel_expire_at",
    "db_expires_at", "db_status", "db_is_bypass_only", "db_source",
    "approved_payments", "counted_payments", "last_paid_at", "admin_grant_days",
    "by_purchases", "by_db", "target", "target_source", "fallback", "db_leaked",
    "action", "reason", "log_id", "db_shortened",
]


class PanelUnavailable(Exception):
    """The panel stream could not be read — nothing was planned."""


async def scan_panel(now: datetime) -> tuple:
    """One paced stream over the whole panel → (stats, candidates)."""
    from app.services import remnawave_api
    users = await remnawave_api.get_all_users(page_size=500, page_delay=0.7, max_retries=6)
    if users is None:
        raise PanelUnavailable("panel stream failed")
    cutoff = now + CUTOFF
    premium = 0
    candidates: List[Dict[str, Any]] = []
    for u in users:
        username = (u.get("username") or "").strip()
        m = PREMIUM_USERNAME_RE.match(username)
        if not m:
            continue
        premium += 1
        expire_at = recon._parse_remnawave_dt(u.get("expireAt"))
        if expire_at is None or expire_at <= cutoff:
            continue
        candidates.append({
            "telegram_id": int(m.group(1)),
            "panel_id": u.get("id"),
            "panel_username": username,
            "panel_expire_at": expire_at,
        })
    candidates.sort(key=lambda c: c["panel_expire_at"], reverse=True)
    stats = {"panel_entities": len(users), "premium_entities": premium, "candidates": len(candidates)}
    return stats, candidates


def _apply_decision(row: Dict[str, Any], inputs: Dict[str, Any], decision: Dict[str, Any]) -> None:
    row.update({
        "db_expires_at": inputs.get("db_expires_at"),
        "db_status": inputs.get("db_status"),
        "db_is_bypass_only": inputs.get("db_is_bypass_only"),
        "db_source": inputs.get("db_source"),
        "approved_payments": inputs.get("approved_payments", 0),
        "counted_payments": inputs.get("counted_payments", 0),
        "last_paid_at": inputs.get("last_paid_at"),
        "admin_grant_days": inputs.get("admin_grant_days", 0),
        "by_purchases": decision["by_purchases"],
        "by_db": decision["by_db"],
        "target": decision["target"],
        "target_source": decision["target_source"],
        "fallback": decision["fallback"],
        "db_leaked": decision["db_leaked"],
    })


async def build_plan() -> Dict[str, Any]:
    """Scan the panel, read the bot DB (one short connection), apply the rule.
    Nothing is written. Raises PanelUnavailable / RuntimeError('db_unavailable')."""
    now = datetime.now(timezone.utc)
    stats, candidates = await scan_panel(now)
    inputs = await recon.load_repair_inputs([c["telegram_id"] for c in candidates])
    rows: List[Dict[str, Any]] = []
    for c in candidates:
        row = dict(c, action="would_fix", reason=None, log_id=None, db_shortened=False)
        inp = inputs.get(c["telegram_id"]) or {}
        decision = recon.compute_repair_target(inp, now=now, panel_expires_at=c["panel_expire_at"])
        _apply_decision(row, inp, decision)
        if c["panel_id"] is None:
            row.update(action="skip", reason="no_panel_id")
        elif decision["would_extend"] or not decision["target"] < c["panel_expire_at"]:
            # Never extend (always true for a > 5y candidate unless the
            # purchases themselves reach past the panel date).
            row.update(action="skip", reason="would_extend")
        rows.append(row)
    return {"now": now, "stats": stats, "rows": rows}


async def apply_plan(plan: Dict[str, Any], *, limit: Optional[int] = None) -> Dict[str, Any]:
    """PATCH every `would_fix` row (paced, at most `limit`). Each user is
    re-planned from a fresh DB read right before its PATCH. Rows updated in place."""
    interval = 1.0 / RATE_PER_SEC
    next_at: Optional[float] = None
    attempted = 0
    started = _clock()
    for row in plan["rows"]:
        if row["action"] != "would_fix":
            continue
        if limit is not None and attempted >= limit:
            row.update(action="skip", reason="limit")
            continue
        if next_at is not None:
            wait = next_at - _clock()
            if wait > 0:
                await _sleep(wait)
        next_at = _clock() + interval
        attempted += 1
        try:
            res = await recon.repair_premium_entity(
                row["telegram_id"],
                panel_id=int(row["panel_id"]),
                panel_username=row["panel_username"],
                panel_expires_at=row["panel_expire_at"],
                reason=LOG_REASON,
            )
        except Exception as e:  # noqa: BLE001 — one user never aborts the run
            res = {"action": "error", "reason": f"unexpected: {type(e).__name__}"}
        if res.get("inputs") is not None and res.get("target") is not None:
            _apply_decision(row, res["inputs"], res)
        row.update(action=res["action"], reason=res.get("reason"),
                   log_id=res.get("log_id"), db_shortened=bool(res.get("db_shortened")))
        if res["action"] == "error":
            logger.warning("PREMIUM_REPAIR_USER_ERROR panel_id=%s reason=%s", row["panel_id"], res.get("reason"))
    s = summarize(plan)
    log_event(
        logger, component="premium_repair", operation="bulk_apply",
        outcome="success" if not s["actions"].get("error") else "partial",
        duration_ms=int((_clock() - started) * 1000),
        reason=f"fixed={s['actions'].get('fixed', 0)} errors={s['actions'].get('error', 0)} "
               f"skipped={s['actions'].get('skip', 0)}",
    )
    return plan


def summarize(plan: Dict[str, Any]) -> Dict[str, Any]:
    rows = plan["rows"]
    actionable = [r for r in rows if r["action"] in ("would_fix", "fixed")]
    return {
        **plan["stats"],
        "actions": dict(Counter(r["action"] for r in rows)),
        "skip_reasons": dict(Counter(r["reason"] for r in rows if r["action"] == "skip")),
        "error_reasons": dict(Counter(r["reason"] for r in rows if r["action"] == "error")),
        "fallback": dict(Counter(r["fallback"] or "none" for r in actionable)),
        "target_source": dict(Counter(r["target_source"] for r in actionable)),
        "plus_one_day": sum(1 for r in actionable if r["fallback"]),
        "db_leaked": sum(1 for r in actionable if r["db_leaked"]),
        "db_shortened": sum(1 for r in rows if r["db_shortened"]),
    }


def format_summary(s: Dict[str, Any]) -> str:
    lines = [
        f"panel entities streamed: {s['panel_entities']} (premium tg_*_premium: {s['premium_entities']})",
        f"premium with expireAt > now + 5 years: {s['candidates']}",
        f"actions: {s['actions']}",
    ]
    if s["skip_reasons"]:
        lines.append(f"skip reasons: {s['skip_reasons']}")
    if s["error_reasons"]:
        lines.append(f"error reasons: {s['error_reasons']}")
    lines += [
        f"target from: {s['target_source']}",
        f"fallback reasons: {s['fallback']}",
        f"go to now + 1 day: {s['plus_one_day']}",
        f"leaked DB dates (> 5 years, not bypass-only) to shorten: {s['db_leaked']}"
        f" (shortened: {s['db_shortened']})",
    ]
    return "\n".join(lines)


def _csv_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if v is None:
        return ""
    return v


def write_csv(rows: List[Dict[str, Any]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv_value(r.get(k)) for k in CSV_COLUMNS})


def _alert_text(s: Dict[str, Any]) -> str:
    a = s["actions"]
    return "\n".join([
        "Починка premium expireAt (скрипт fix_premium_over_issuance) завершена.",
        f"кандидатов (premium > 5 лет): {s['candidates']}",
        f"исправлено: {a.get('fixed', 0)}, ошибок: {a.get('error', 0)}, пропущено: {a.get('skip', 0)}",
        f"на +1 день: {s['plus_one_day']}; дата в БД подрезана: {s['db_shortened']}",
        "Подробности — CSV-отчёт прогона и журнал «Сверка».",
    ])


async def send_summary_alert(summary: Dict[str, Any], bot=None) -> bool:
    """One admin alert per --apply run. Never raises."""
    try:
        if bot is None:
            from app.services import purchase_flow
            bot = purchase_flow._alert_bot()
        if bot is None:
            logger.warning("PREMIUM_REPAIR_ALERT_NO_BOT")
            return False
        from app.services import admin_alerts
        return bool(await admin_alerts.send_alert(bot, "worker", _alert_text(summary), force=True))
    except Exception as e:  # noqa: BLE001
        logger.warning("PREMIUM_REPAIR_ALERT_FAILED: %s", type(e).__name__)
        return False
