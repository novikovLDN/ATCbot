"""Remnawave tags by tariff — the one-off backfill of existing panel entities.

Owner decisions 2026-09-14:
  * the premium entity's tag is the user's current tariff (TRIAL / BASIC / PLUS
    / COMBO_BASIC / COMBO_PLUS, legacy biz → PLUS), the bypass entity's tag is
    BYPASS. New and changed tariffs are tagged by provisioning itself
    (app/services/provisioning.py, purchase_flow.py);
  * existing panel users are tagged ONLY by this backfill, started by the admin
    (dashboard → «Ещё» → «Настройки» → «Теги в панели Remnawave», or
    scripts/backfill_remnawave_tags.py). Explicitly approved for users WITH AN
    ACTIVE SUBSCRIPTION. Tags only: never expireAt, limits or status.

Scope: subscriptions rows with status='active', expires_at > now, not
bypass-only. Per user: the premium entity (username tg_{id}_premium) gets
tariffs.premium_panel_tag_for_subscription(row), the bypass entity (username
{id}) gets BYPASS. Only entities whose panel tag differs are PATCHed.

The job: one at a time in this process; a tag-only PATCH {id, tag}; at most
RATE_PER_SEC PATCH/s; a failed entity is counted and logged and never aborts
the run. Progress lives in app_settings (key STATE_KEY): the dashboard polls it.
After a restart the state still says "running" with no live task — reported as
"interrupted"; «Продолжить» (resume) builds a new plan, which skips the entities
already tagged. Pause / stop take effect before the next PATCH. The target tag
is re-read from the DB in batches of BATCH right before the PATCHes, so a
purchase made during a long run is never overwritten with the old tariff.
Finish (done / stopped / failed): one admin Telegram alert with the summary.
No DB connection is held during a panel request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import config
from app.services import remnawave_api, remnawave_bypass, remnawave_premium, tariffs

logger = logging.getLogger(__name__)

STATE_KEY = "remnawave_tag_backfill"
RATE_PER_SEC = 2.0
BATCH = 20                  # DB re-read of the target tags per this many entities
PERSIST_EVERY = 10          # progress write to app_settings every N entities
CONTROL_WAIT_S = 15.0       # pause / stop wait this long for the loop to notice
PANEL_PAGE_SIZE = 1000
PANEL_PAGE_DELAY_S = 0.2
TERMINAL_STATES = ("done", "stopped", "failed")

_clock = time.monotonic     # tests patch both (fake clock)
_sleep = asyncio.sleep


class TagBackfillUnavailable(RuntimeError):
    """The DB or the panel cannot be read (preview / plan)."""


# ── plan (pure) ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Change:
    telegram_id: int
    kind: str                # "premium" | "bypass"
    panel_id: int
    tag: str
    current: Optional[str]


@dataclass
class Plan:
    users: int = 0
    changes: List[Change] = field(default_factory=list)
    already: int = 0
    missing: int = 0
    totals: Dict[str, int] = field(default_factory=lambda: {t: 0 for t in tariffs.PANEL_TAGS})
    differ: Dict[str, int] = field(default_factory=lambda: {t: 0 for t in tariffs.PANEL_TAGS})


def targets(rows: Iterable[Dict[str, Any]]) -> List[Tuple[int, str, str, str]]:
    """(telegram_id, kind, panel username, tag) for every entity in scope."""
    out: List[Tuple[int, str, str, str]] = []
    for row in rows:
        tg = int(row["telegram_id"])
        premium_tag = tariffs.premium_panel_tag_for_subscription(row)
        if premium_tag:
            out.append((tg, "premium", remnawave_premium.build_premium_username(tg), premium_tag))
        out.append((tg, "bypass", remnawave_bypass.build_bypass_username(tg), tariffs.PANEL_TAG_BYPASS))
    return out


def build_plan(rows: List[Dict[str, Any]], panel_users: Iterable[Dict[str, Any]]) -> Plan:
    """Match the entities in scope to the panel by username (unique in the panel,
    and the only unambiguous split between a user's premium and bypass entity)."""
    by_name = {str(u.get("username")): u for u in panel_users if isinstance(u, dict)}
    plan = Plan(users=len(rows))
    for tg, kind, username, tag in targets(rows):
        ent = by_name.get(username)
        try:
            panel_id = int(ent["id"]) if ent and ent.get("id") is not None else None
        except (TypeError, ValueError):
            panel_id = None
        if panel_id is None:
            plan.missing += 1
            continue
        plan.totals[tag] += 1
        if ent.get("tag") == tag:
            plan.already += 1
            continue
        plan.differ[tag] += 1
        plan.changes.append(Change(tg, kind, panel_id, tag, ent.get("tag")))
    return plan


def summarize(plan: Plan) -> Dict[str, Any]:
    return {
        "generated_at": _now_iso(),
        "users": plan.users,
        "entities": sum(plan.totals.values()),
        "differ": len(plan.changes),
        "already": plan.already,
        "missing": plan.missing,
        "tags": [{"tag": t, "total": plan.totals[t], "differ": plan.differ[t]} for t in tariffs.PANEL_TAGS],
        "eta_seconds": math.ceil(len(plan.changes) / RATE_PER_SEC),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── reads ─────────────────────────────────────────────────────────────

# to_jsonb: the row as it is — subscriptions.is_combo / is_bypass_only may be
# missing on an old schema (is_combo is added at runtime by set_combo_flag).
_ACTIVE_SQL = """
    SELECT to_jsonb(s) AS row
      FROM subscriptions s
     WHERE s.status = 'active' AND s.expires_at > $1
"""


async def load_active_rows(telegram_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Active, not bypass-only subscriptions rows (optionally only these users)."""
    import database
    from database.core import _to_db_utc
    try:
        pool = await database.get_pool()
    except Exception as e:
        raise TagBackfillUnavailable(f"db: {type(e).__name__}") from e
    if pool is None:
        raise TagBackfillUnavailable("db: no pool")
    now = _to_db_utc(datetime.now(timezone.utc))
    async with pool.acquire() as conn:
        if telegram_ids is None:
            recs = await conn.fetch(_ACTIVE_SQL, now)
        else:
            recs = await conn.fetch(_ACTIVE_SQL + " AND s.telegram_id = ANY($2::bigint[])",
                                    now, [int(t) for t in telegram_ids])
    rows: List[Dict[str, Any]] = []
    for rec in recs:
        row = rec["row"]
        if isinstance(row, str):
            row = json.loads(row)
        if isinstance(row, dict) and not row.get("is_bypass_only"):
            rows.append(row)
    return rows


async def load_panel_users() -> List[Dict[str, Any]]:
    if not config.REMNAWAVE_ENABLED:
        raise TagBackfillUnavailable("remnawave_disabled")
    users = await remnawave_api.get_all_users(page_size=PANEL_PAGE_SIZE, page_delay=PANEL_PAGE_DELAY_S)
    if users is None:
        raise TagBackfillUnavailable("panel_unavailable")
    return users


async def make_plan() -> Plan:
    rows = await load_active_rows()          # DB first, released before the panel stream
    return build_plan(rows, await load_panel_users())


async def preview() -> Dict[str, Any]:
    """Dry run: what the job would change. Writes nothing."""
    return summarize(await make_plan())


# ── state (app_settings) ──────────────────────────────────────────────

class _AppSettingsStore:
    async def load(self) -> Optional[Dict[str, Any]]:
        import database
        pool = await database.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", STATE_KEY)
        return json.loads(raw) if raw else None

    async def save(self, state: Dict[str, Any]) -> None:
        import database
        pool = await database.get_pool()
        if pool is None:
            raise TagBackfillUnavailable("db: no pool")
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO app_settings (key, value, updated_at) VALUES ($1, $2, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                STATE_KEY, json.dumps(state),
            )


_store: Any = _AppSettingsStore()     # tests swap in an in-memory store

_DEFAULT_STATUS: Dict[str, Any] = {
    "state": "idle", "total": 0, "done": 0, "patched": 0, "errors": 0,
    "per_tag": {}, "last_error": None, "started_at": None, "updated_at": None,
    "finished_at": None, "started_by": None,
}


def _fresh_state(admin_id: Optional[int]) -> Dict[str, Any]:
    now = _now_iso()
    return {
        **_DEFAULT_STATUS, "state": "running", "per_tag": {t: 0 for t in tariffs.PANEL_TAGS},
        "started_at": now, "updated_at": now, "started_by": admin_id,
    }


async def _save(state: Dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    try:
        await _store.save(dict(state))
    except Exception as e:  # noqa: BLE001 — progress display only; the job goes on
        logger.warning("REMNAWAVE_TAG_BACKFILL_STATE_SAVE_FAILED: %s: %s", type(e).__name__, e)


# ── job ───────────────────────────────────────────────────────────────

_task: Optional[asyncio.Task] = None
_control: Optional[str] = None       # "pause" | "stop" — read before every PATCH
_lock = asyncio.Lock()


def is_running() -> bool:
    return _task is not None and not _task.done()


async def get_status() -> Dict[str, Any]:
    try:
        stored = await _store.load()
    except Exception as e:  # noqa: BLE001
        logger.warning("REMNAWAVE_TAG_BACKFILL_STATE_LOAD_FAILED: %s: %s", type(e).__name__, e)
        stored = None
    out = {**_DEFAULT_STATUS, **(stored or {})}
    running = is_running()
    if out["state"] == "running" and not running:
        out["state"] = "interrupted"          # the process restarted mid-run
    out["running"] = running
    out["rate_per_sec"] = RATE_PER_SEC
    return out


def _spawn(state: Dict[str, Any], *, bot=None, limit: Optional[int] = None) -> asyncio.Task:
    global _task, _control
    _control = None
    _task = asyncio.create_task(_run(state, bot=bot, limit=limit))
    return _task


async def start(admin_id: Optional[int], *, bot=None) -> Dict[str, Any]:
    """A new run (counters reset; the plan skips entities already tagged)."""
    async with _lock:
        if is_running():
            return {"ok": False, "error": "already_running", "status": await get_status()}
        state = _fresh_state(admin_id)
        await _save(state)
        _spawn(state, bot=bot)
        logger.warning("REMNAWAVE_TAG_BACKFILL_STARTED: admin=%s", admin_id)
    return {"ok": True, "status": await get_status()}


async def resume(admin_id: Optional[int], *, bot=None) -> Dict[str, Any]:
    """Continue a paused or interrupted run (counters kept)."""
    async with _lock:
        if is_running():
            return {"ok": False, "error": "already_running", "status": await get_status()}
        state = await _store.load()
        if not state or state.get("state") not in ("paused", "running"):
            return {"ok": False, "error": "not_resumable", "status": await get_status()}
        state["state"] = "running"
        await _save(state)
        _spawn(state, bot=bot)
        logger.warning("REMNAWAVE_TAG_BACKFILL_RESUMED: admin=%s done=%s", admin_id, state.get("done"))
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
    if not is_running():
        return {"ok": False, "error": "not_running", "status": await get_status()}
    await _signal("pause")
    return {"ok": True, "status": await get_status()}


async def stop(admin_id: Optional[int] = None, *, bot=None) -> Dict[str, Any]:
    """Stop a running job, or close a paused / interrupted one (with the summary alert)."""
    if is_running():
        await _signal("stop")
        return {"ok": True, "status": await get_status()}
    async with _lock:
        state = await _store.load()
        if not state or state.get("state") not in ("paused", "running"):
            return {"ok": False, "error": "not_running", "status": await get_status()}
        state["state"] = "stopped"
        state["finished_at"] = _now_iso()
        await _save(state)
    logger.warning("REMNAWAVE_TAG_BACKFILL_STOPPED: admin=%s (not running)", admin_id)
    await _alert(state, bot)
    return {"ok": True, "status": await get_status()}


async def run_foreground(admin_id: Optional[int] = None, *, limit: Optional[int] = None) -> Dict[str, Any]:
    """The CLI (--apply): the same loop, in the calling task. `limit` PATCHes → paused."""
    state = _fresh_state(admin_id)
    await _save(state)
    await _run(state, bot=None, limit=limit)
    return await get_status()


async def _target_tags(chunk: List[Change]) -> Dict[Tuple[int, str], Optional[str]]:
    """The tag each entity of `chunk` must have NOW (None = no longer in scope)."""
    rows = {int(r["telegram_id"]): r for r in await load_active_rows([c.telegram_id for c in chunk])}
    out: Dict[Tuple[int, str], Optional[str]] = {}
    for c in chunk:
        row = rows.get(c.telegram_id)
        if row is None:
            out[(c.telegram_id, c.kind)] = None
        elif c.kind == "premium":
            out[(c.telegram_id, c.kind)] = tariffs.premium_panel_tag_for_subscription(row)
        else:
            out[(c.telegram_id, c.kind)] = tariffs.PANEL_TAG_BYPASS
    return out


async def _patch_one(change: Change, tag: str) -> Optional[str]:
    """Tag-only PATCH. None on success, else a short error."""
    try:
        env = await remnawave_api.set_user_tag(change.panel_id, tag)
    except Exception as e:  # noqa: BLE001 — one entity never aborts the run
        return type(e).__name__
    if env.get("ok"):
        return None
    return f"HTTP {env.get('status')}" + (f" {env.get('error')}" if env.get("error") else "")


async def _run(state: Dict[str, Any], *, bot, limit: Optional[int]) -> None:
    final = "done"
    try:
        plan = await make_plan()
        base = int(state.get("patched") or 0)       # a resumed run keeps what it already did
        state.update(total=base + len(plan.changes), done=base, errors=0, last_error=None)
        state.setdefault("per_tag", {})
        await _save(state)
        logger.warning(
            "REMNAWAVE_TAG_BACKFILL_PLAN: users=%s to_change=%s already=%s missing=%s",
            plan.users, len(plan.changes), plan.already, plan.missing,
        )
        interval = 1.0 / RATE_PER_SEC
        next_at: Optional[float] = None
        attempts = 0
        unsaved = 0
        changes = plan.changes
        for i in range(0, len(changes), BATCH):
            if _control or (limit is not None and attempts >= limit):
                break
            chunk = changes[i:i + BATCH]
            wanted = await _target_tags(chunk)
            for change in chunk:
                if _control or (limit is not None and attempts >= limit):
                    break
                tag = wanted.get((change.telegram_id, change.kind))
                if tag is None or tag == change.current:
                    state["total"] -= 1          # left the scope / already right since the plan
                    continue
                if next_at is not None:
                    wait = next_at - _clock()
                    if wait > 0:
                        await _sleep(wait)
                        if _control:             # pause / stop pressed while waiting
                            break
                next_at = _clock() + interval
                attempts += 1
                err = await _patch_one(change, tag)
                state["done"] += 1
                if err is None:
                    state["patched"] += 1
                    state["per_tag"][tag] = int(state["per_tag"].get(tag) or 0) + 1
                else:
                    state["errors"] += 1
                    state["last_error"] = f"{change.kind} tg:{change.telegram_id} → {tag}: {err}"[:300]
                    logger.warning(
                        "REMNAWAVE_TAG_BACKFILL_ENTITY_FAILED: tg=%s kind=%s id=%s tag=%s %s",
                        change.telegram_id, change.kind, change.panel_id, tag, err,
                    )
                unsaved += 1
                if unsaved >= PERSIST_EVERY:
                    await _save(state)
                    unsaved = 0
        if _control == "stop":
            final = "stopped"
        elif _control == "pause" or (limit is not None and attempts >= limit and state["done"] < state["total"]):
            final = "paused"
    except asyncio.CancelledError:
        await _save(state)                      # stays "running" → "interrupted"
        raise
    except Exception as e:  # noqa: BLE001 — the run ends, the admin is told
        final = "failed"
        state["last_error"] = f"{type(e).__name__}: {e}"[:300]
        logger.exception("REMNAWAVE_TAG_BACKFILL_FAILED: %s", e)
    state["state"] = final
    if final in TERMINAL_STATES:
        state["finished_at"] = _now_iso()
    await _save(state)
    logger.warning(
        "REMNAWAVE_TAG_BACKFILL_%s: done=%s/%s patched=%s errors=%s",
        final.upper(), state.get("done"), state.get("total"), state.get("patched"), state.get("errors"),
    )
    if final in TERMINAL_STATES:
        await _alert(state, bot)


def _alert_text(state: Dict[str, Any]) -> str:
    head = {
        "done": "Теги Remnawave проставлены",
        "stopped": "Проставление тегов Remnawave остановлено",
        "failed": "Проставление тегов Remnawave прервано ошибкой",
    }.get(state.get("state"), "Проставление тегов Remnawave")
    per_tag = ", ".join(f"{t} {n}" for t, n in (state.get("per_tag") or {}).items() if n) or "—"
    lines = [
        head,
        f"изменено: {state.get('patched', 0)} из {state.get('total', 0)}",
        f"ошибок: {state.get('errors', 0)}",
        f"по тегам: {per_tag}",
    ]
    if state.get("last_error"):
        lines.append(f"последняя ошибка: {state['last_error']}")
    lines.append("Дашборд → «Ещё» → «Настройки» → «Теги в панели Remnawave».")
    return "\n".join(lines)


async def _alert(state: Dict[str, Any], bot) -> None:
    """One admin alert per finished run. Never raises."""
    try:
        if bot is None:
            from app.services import purchase_flow
            bot = purchase_flow._alert_bot()
        if bot is None:
            logger.warning("REMNAWAVE_TAG_BACKFILL_ALERT_NO_BOT: %s", state.get("state"))
            return
        from app.services import admin_alerts
        await admin_alerts.send_alert(bot, "worker", _alert_text(state), force=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("REMNAWAVE_TAG_BACKFILL_ALERT_FAILED: %s: %s", type(e).__name__, e)


def reset_for_tests() -> None:
    """Forget the in-process job (simulates a restart)."""
    global _task, _control
    _task = None
    _control = None
