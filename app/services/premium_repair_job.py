"""Premium expireAt > 5 years — the dashboard job around app/services/premium_repair.

Дашборд → «Ещё» → «Настройки» → «Премиум больше 5 лет». Same rule and code as
the CLI (scripts/fix_premium_over_issuance.py): build_plan / apply_plan /
summarize / send_summary_alert; nothing is duplicated here.

Two kinds of background job, one at a time in this process:
  * check — premium_repair.build_plan() + summarize(): the panel stream and one
    short DB read. Writes nothing but the report below.
  * apply — a fresh build_plan(), then apply_plan() (≤ RATE_PER_SEC PATCH/s,
    each user re-read from the DB right before its PATCH, a failed user is
    counted and never aborts the run). Pause / stop take effect before the
    next PATCH. `limit` = a trial run of N PATCHes, then "paused".

State lives in app_settings (STATE_KEY: {"check": …, "apply": …, "report": meta}),
the dashboard polls it. After a restart a stored "running" with no live task
is reported as "interrupted"; «Продолжить» (resume) re-plans from a fresh scan —
users already fixed are no longer > 5 years and drop out by themselves. The
rows of the last check / apply (table + CSV) live in app_settings REPORT_KEY.
A finished apply (done / stopped / failed) sends one admin alert with the summary.
No DB connection is held during a panel request (premium_repair guarantees it).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.structured_logger import log_event
from app.services import premium_repair

logger = logging.getLogger(__name__)

STATE_KEY = "premium_repair_job"
REPORT_KEY = "premium_repair_report"
PERSIST_EVERY = 10          # progress write to app_settings every N users
CONTROL_WAIT_S = 15.0       # pause / stop wait this long for the loop to notice
TERMINAL_STATES = ("done", "stopped", "failed")
RESUMABLE = ("paused", "running")   # stored states («running» without a task = interrupted)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── storage (app_settings) ────────────────────────────────────────────

class _AppSettingsKV:
    async def load(self, key: str) -> Optional[Dict[str, Any]]:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
        return json.loads(raw) if raw else None

    async def save(self, key: str, value: Dict[str, Any]) -> None:
        import database
        pool = await database.get_pool()
        if pool is None:
            raise RuntimeError("db_unavailable")
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO app_settings (key, value, updated_at) VALUES ($1, $2, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                key, json.dumps(value),
            )


_store: Any = _AppSettingsKV()     # tests swap in an in-memory store

_CHECK_DEFAULT: Dict[str, Any] = {
    "state": "idle", "started_at": None, "finished_at": None, "started_by": None,
    "last_error": None, "summary": None, "would_fix": 0, "eta_seconds": 0,
    "stale": False,             # an apply ran after this check: its numbers no longer hold
}
_APPLY_DEFAULT: Dict[str, Any] = {
    "state": "idle", "total": 0, "done": 0, "fixed": 0, "errors": 0, "skipped": 0,
    "plus_one_day": 0, "db_shortened": 0, "candidates": 0, "remaining": 0, "limit": None,
    "last_error": None, "summary": None, "started_at": None, "updated_at": None,
    "finished_at": None, "started_by": None,
}


async def _load_doc() -> Dict[str, Any]:
    try:
        raw = await _store.load(STATE_KEY)
    except Exception as e:  # noqa: BLE001 — shown as "idle"; the job itself goes on
        logger.warning("PREMIUM_REPAIR_JOB_STATE_LOAD_FAILED: %s", type(e).__name__)
        raw = None
    doc = raw if isinstance(raw, dict) else {}
    return {
        "check": {**_CHECK_DEFAULT, **(doc.get("check") or {})},
        "apply": {**_APPLY_DEFAULT, **(doc.get("apply") or {})},
        "report": doc.get("report"),
    }


async def _save(doc: Dict[str, Any]) -> None:
    doc["apply"]["updated_at"] = _now_iso() if doc["apply"]["state"] != "idle" else None
    try:
        await _store.save(STATE_KEY, doc)
    except Exception as e:  # noqa: BLE001 — progress display only; the job goes on
        logger.warning("PREMIUM_REPAIR_JOB_STATE_SAVE_FAILED: %s", type(e).__name__)


async def _save_report(doc: Dict[str, Any], kind: str, rows: List[Dict[str, Any]]) -> None:
    now = _now_iso()
    report = {"kind": kind, "generated_at": now, "rows": [premium_repair.json_row(r) for r in rows]}
    try:
        await _store.save(REPORT_KEY, report)
    except Exception as e:  # noqa: BLE001
        logger.warning("PREMIUM_REPAIR_JOB_REPORT_SAVE_FAILED: %s", type(e).__name__)
        return
    doc["report"] = {"kind": kind, "generated_at": now, "rows": len(report["rows"])}


async def _load_report() -> Optional[Dict[str, Any]]:
    try:
        rep = await _store.load(REPORT_KEY)
    except Exception as e:  # noqa: BLE001
        logger.warning("PREMIUM_REPAIR_JOB_REPORT_LOAD_FAILED: %s", type(e).__name__)
        return None
    return rep if isinstance(rep, dict) else None


async def get_report(offset: int = 0, limit: int = 50) -> Dict[str, Any]:
    """Rows of the last check / apply (a page of them)."""
    rep = await _load_report() or {}
    rows = rep.get("rows") or []
    offset = max(0, int(offset))
    return {
        "kind": rep.get("kind"), "generated_at": rep.get("generated_at"),
        "total": len(rows), "offset": offset, "rows": rows[offset:offset + max(0, int(limit))],
    }


async def report_csv() -> Optional[Dict[str, Any]]:
    """{kind, generated_at, rows, text} of the last report (CLI CSV columns), None if none."""
    rep = await _load_report()
    if not rep or rep.get("rows") is None:
        return None
    rows = rep["rows"]
    return {"kind": rep.get("kind"), "generated_at": rep.get("generated_at"),
            "rows": len(rows), "text": premium_repair.csv_text(rows)}


# ── job control ───────────────────────────────────────────────────────

_task: Optional[asyncio.Task] = None
_kind: Optional[str] = None          # "check" | "apply" — of the live task
_control: Optional[str] = None       # "pause" | "stop" — read before every PATCH
_lock = asyncio.Lock()


def is_running() -> bool:
    return _task is not None and not _task.done()


def running_kind() -> Optional[str]:
    return _kind if is_running() else None


async def get_status() -> Dict[str, Any]:
    doc = await _load_doc()
    kind = running_kind()
    for k in ("check", "apply"):
        if doc[k]["state"] == "running" and kind != k:
            doc[k]["state"] = "interrupted"       # the process restarted mid-run
    return {
        "running": kind is not None,
        "running_kind": kind,
        "rate_per_sec": premium_repair.RATE_PER_SEC,
        "check": doc["check"],
        "apply": doc["apply"],
        "report": doc["report"],
    }


async def _refused(error: str) -> Dict[str, Any]:
    return {"ok": False, "error": error, "status": await get_status()}


def _spawn(kind: str, coro) -> asyncio.Task:
    global _task, _kind, _control
    _control = None
    _kind = kind
    _task = asyncio.create_task(coro)
    return _task


async def start_check(admin_id: Optional[int]) -> Dict[str, Any]:
    """Dry run in the background: scan + rule, nothing written but the report."""
    async with _lock:
        if is_running():
            return await _refused("already_running")
        doc = await _load_doc()
        doc["check"] = {**_CHECK_DEFAULT, "state": "running", "started_at": _now_iso(), "started_by": admin_id}
        await _save(doc)
        _spawn("check", _run_check(doc))
        logger.warning("PREMIUM_REPAIR_CHECK_STARTED: admin=%s", admin_id)
    return {"ok": True, "status": await get_status()}


async def start_apply(admin_id: Optional[int], *, limit: Optional[int] = None, bot=None) -> Dict[str, Any]:
    """A new repair run (counters reset). `limit` PATCHes → "paused"."""
    async with _lock:
        if is_running():
            return await _refused("already_running")
        doc = await _load_doc()
        now = _now_iso()
        doc["apply"] = {**_APPLY_DEFAULT, "state": "running", "started_at": now,
                        "started_by": admin_id, "limit": limit}
        doc["check"]["stale"] = True
        await _save(doc)
        _spawn("apply", _run_apply(doc, resume=False, limit=limit, bot=bot))
        logger.warning("PREMIUM_REPAIR_APPLY_STARTED: admin=%s limit=%s", admin_id, limit)
    return {"ok": True, "status": await get_status()}


async def resume(admin_id: Optional[int], *, bot=None) -> Dict[str, Any]:
    """Continue a paused or interrupted run from a fresh scan (fixed count kept)."""
    async with _lock:
        if is_running():
            return await _refused("already_running")
        doc = await _load_doc()
        if doc["apply"]["state"] not in RESUMABLE:
            return await _refused("not_resumable")
        doc["apply"].update(state="running", finished_at=None)
        await _save(doc)
        _spawn("apply", _run_apply(doc, resume=True, limit=None, bot=bot))
        logger.warning("PREMIUM_REPAIR_APPLY_RESUMED: admin=%s fixed=%s", admin_id, doc["apply"]["fixed"])
    return {"ok": True, "status": await get_status()}


async def _signal(kind: str) -> None:
    global _control
    _control = kind
    task = _task
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), CONTROL_WAIT_S)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — the status says where it is
            pass


async def pause() -> Dict[str, Any]:
    if running_kind() != "apply":
        return await _refused("not_running")
    await _signal("pause")
    return {"ok": True, "status": await get_status()}


async def stop(admin_id: Optional[int] = None, *, bot=None) -> Dict[str, Any]:
    """Stop a running repair, or close a paused / interrupted one (with the alert)."""
    if running_kind() == "apply":
        await _signal("stop")
        return {"ok": True, "status": await get_status()}
    async with _lock:
        if is_running():                       # a check is running
            return await _refused("already_running")
        doc = await _load_doc()
        a = doc["apply"]
        if a["state"] not in RESUMABLE:
            return await _refused("not_running")
        a.update(state="stopped", finished_at=_now_iso())
        await _save(doc)
    logger.warning("PREMIUM_REPAIR_APPLY_STOPPED: admin=%s (not running)", admin_id)
    await _alert(a, bot)
    return {"ok": True, "status": await get_status()}


def _error_text(e: BaseException) -> str:
    if isinstance(e, premium_repair.PanelUnavailable):
        return "panel_unavailable"
    if isinstance(e, RuntimeError) and str(e) == "db_unavailable":
        return "db_unavailable"
    return type(e).__name__


# ── the jobs ──────────────────────────────────────────────────────────

async def _run_check(doc: Dict[str, Any]) -> None:
    c = doc["check"]
    started = time.monotonic()
    try:
        plan = await premium_repair.build_plan()
        s = premium_repair.summarize(plan)
        would_fix = int(s["actions"].get("would_fix", 0))
        c.update(state="done", summary=s, would_fix=would_fix, last_error=None, stale=False,
                 eta_seconds=math.ceil(would_fix / premium_repair.RATE_PER_SEC))
        await _save_report(doc, "check", plan["rows"])
    except asyncio.CancelledError:
        await _save(doc)                        # stays "running" → "interrupted"
        raise
    except Exception as e:  # noqa: BLE001 — the admin sees it on the card
        c.update(state="failed", last_error=_error_text(e))
    c["finished_at"] = _now_iso()
    await _save(doc)
    log_event(
        logger, component="premium_repair", operation="dashboard_check",
        outcome="success" if c["state"] == "done" else "failed",
        duration_ms=int((time.monotonic() - started) * 1000),
        reason=f"candidates={(c['summary'] or {}).get('candidates')} would_fix={c['would_fix']}"
        if c["state"] == "done" else c["last_error"],
    )


def _summary(a: Dict[str, Any], plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The alert / card summary: the last scan's breakdown + the run's cumulative counters."""
    base = premium_repair.summarize(plan) if plan is not None else {
        "panel_entities": 0, "premium_entities": 0, "candidates": 0, "actions": {},
        "skip_reasons": {}, "error_reasons": {}, "fallback": {}, "target_source": {}, "db_leaked": 0,
    }
    return {
        **base,
        "candidates": a.get("candidates") or base.get("candidates", 0),
        "actions": {**base["actions"], "fixed": a["fixed"], "error": a["errors"],
                    "skip": base["actions"].get("skip", 0)},
        "plus_one_day": a["plus_one_day"],
        "db_shortened": a["db_shortened"],
        "remaining": a.get("remaining", 0),
    }


async def _run_apply(doc: Dict[str, Any], *, resume: bool, limit: Optional[int], bot) -> None:
    a = doc["apply"]
    final = "done"
    plan: Optional[Dict[str, Any]] = None
    started = time.monotonic()
    try:
        plan = await premium_repair.build_plan()
        todo = sum(1 for r in plan["rows"] if r["action"] == "would_fix")
        if limit is not None:
            todo = min(todo, limit)
        if not resume:
            a["candidates"] = plan["stats"]["candidates"]
        base = int(a.get("fixed") or 0)          # a resumed run keeps what it already fixed
        a.update(total=base + todo, done=base, errors=0, skipped=0, last_error=None, remaining=0)
        await _save(doc)
        unsaved = 0

        async def on_row(row: Dict[str, Any]) -> None:
            nonlocal unsaved
            a["done"] += 1
            if row["action"] == "fixed":
                a["fixed"] += 1
                a["plus_one_day"] += 1 if row.get("fallback") else 0
                a["db_shortened"] += 1 if row.get("db_shortened") else 0
            elif row["action"] == "error":
                a["errors"] += 1
                a["last_error"] = f"tg:{row['telegram_id']}: {row.get('reason')}"[:300]
            else:
                a["skipped"] += 1                # e.g. would_extend after the fresh DB read
            unsaved += 1
            if unsaved >= PERSIST_EVERY:
                await _save(doc)
                unsaved = 0

        await premium_repair.apply_plan(
            plan, limit=limit, should_stop=lambda: _control is not None, on_row=on_row,
        )
        a["remaining"] = sum(
            1 for r in plan["rows"]
            if r["action"] == "would_fix" or (r["action"] == "skip" and r["reason"] == "limit")
        )
        if _control == "stop":
            final = "stopped"
        elif _control == "pause" or a["remaining"]:
            final = "paused"                     # paused, or the trial `limit` was reached
    except asyncio.CancelledError:
        await _save(doc)                        # stays "running" → "interrupted"
        raise
    except Exception as e:  # noqa: BLE001 — the run ends, the admin is told
        final = "failed"
        a["last_error"] = _error_text(e)
        logger.warning("PREMIUM_REPAIR_APPLY_FAILED: %s", type(e).__name__)
    a["state"] = final
    if final in TERMINAL_STATES:
        a["finished_at"] = _now_iso()
    a["summary"] = _summary(a, plan)
    if plan is not None:
        await _save_report(doc, "apply", plan["rows"])
    await _save(doc)
    log_event(
        logger, component="premium_repair", operation="dashboard_apply",
        outcome="success" if final == "done" and not a["errors"] else final,
        duration_ms=int((time.monotonic() - started) * 1000),
        reason=f"state={final} fixed={a['fixed']} errors={a['errors']} skipped={a['skipped']} "
               f"remaining={a['remaining']}",
    )
    if final in TERMINAL_STATES:
        await _alert(a, bot)


async def _alert(a: Dict[str, Any], bot) -> None:
    """One admin alert per finished run (premium_repair.send_summary_alert never raises)."""
    summary = dict(a.get("summary") or _summary(a, None))
    summary.update(outcome=a["state"], last_error=a.get("last_error"))
    await premium_repair.send_summary_alert(summary, bot=bot)


def reset_for_tests() -> None:
    """Forget the in-process job (simulates a restart)."""
    global _task, _kind, _control
    _task = None
    _kind = None
    _control = None
