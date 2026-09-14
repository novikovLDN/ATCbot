"""System health for the dashboard: one snapshot, one overall status.

Checks, in this order:
  1. Database — DB_READY flag, pool size / in use, SELECT 1 latency. The
     connection is released before anything else runs.
  2. Remnawave API, Redis, Telegram getWebhookInfo — concurrently, each
     with its own timeout. No DB connection is held during them.
  3. In-process facts — worker liveness (app/core/runtime_health), admin
     alert volume, uptime, version.

overall_status() is pure and turns the snapshot into ok / degraded / down
with human-readable reasons (tested in tests/services/test_system_health.py).
Rules are documented in docs/dashboard/metrics.md («Здоровье системы»).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import config
import database
from app.core import runtime_health
from app.core.runtime_context import get_bot_start_time

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 6.0
DB_TIMEOUT_S = 3.0
POOL_BUSY_WARN = 0.8
WEBHOOK_PENDING_WARN = 50
WEBHOOK_PENDING_DOWN = 500
WEBHOOK_ERROR_RECENT_S = 15 * 60
REMNAWAVE_SLOW_MS = 3000

_DIST_INDEX = Path(__file__).resolve().parents[2] / "dashboard" / "dist" / "index.html"
_SHA_ENV = ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT_SHA", "GIT_SHA", "SOURCE_COMMIT")

# Worker names as registered in runtime_health → label on the dashboard.
WORKER_LABELS = {
    "reminders": "Напоминания о продлении",
    "trial_notifications": "Уведомления пробного периода",
    "fast_expiry_cleanup": "Отключение истёкших",
    "auto_renewal": "Автопродление",
    "activation_worker": "Отложенные активации",
    "provisioning_worker": "Очередь выдачи доступа",
    "healthcheck": "Самопроверка БД",
    "farm_notifications": "Игра: уведомления",
    "traffic_monitor": "Мониторинг трафика",
    "wata_reconciler": "Сверка WATA",
    "scheduled_broadcasts": "Запланированные рассылки",
}


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


async def check_db() -> dict[str, Any]:
    out: dict[str, Any] = {"ready": bool(getattr(database, "DB_READY", False)), "ok": False}
    try:
        pool = await database.get_pool()
    except Exception as e:
        out["error"] = type(e).__name__
        return out
    if pool is None:
        out["error"] = "no_pool"
        return out
    try:
        size, idle = pool.get_size(), pool.get_idle_size()
        out["pool"] = {"size": size, "idle": idle, "in_use": size - idle,
                       "min": pool.get_min_size(), "max": pool.get_max_size()}
    except Exception:
        pass
    t0 = time.perf_counter()
    try:
        conn = await pool.acquire(timeout=DB_TIMEOUT_S)
        try:
            await conn.fetchval("SELECT 1", timeout=DB_TIMEOUT_S)
        finally:
            await pool.release(conn)
        out.update(ok=True, latency_ms=_ms(t0))
    except Exception as e:
        out.update(error=type(e).__name__, latency_ms=_ms(t0))
    return out


async def check_remnawave() -> dict[str, Any]:
    if not getattr(config, "REMNAWAVE_ENABLED", False):
        return {"enabled": False}
    from app.services import remnawave_api

    t0 = time.perf_counter()
    try:
        # GET /api/system/stats — already used by the Panel screen; one
        # call per health refresh (the route caches the snapshot).
        res = await asyncio.wait_for(remnawave_api.get_system_stats(), HTTP_TIMEOUT_S + 2)
        return {"enabled": True, "ok": res is not None, "latency_ms": _ms(t0),
                **({} if res is not None else {"error": "no_response"})}
    except asyncio.TimeoutError:
        return {"enabled": True, "ok": False, "latency_ms": _ms(t0), "error": "timeout"}
    except Exception as e:
        return {"enabled": True, "ok": False, "latency_ms": _ms(t0), "error": type(e).__name__}


async def check_redis() -> dict[str, Any]:
    try:
        from app.utils.redis_client import is_configured, ping
    except Exception:
        return {"configured": False}
    if not is_configured():
        return {"configured": False}
    t0 = time.perf_counter()
    try:
        ok = bool(await asyncio.wait_for(ping(), DB_TIMEOUT_S))
        return {"configured": True, "ok": ok, "latency_ms": _ms(t0)}
    except Exception as e:
        return {"configured": True, "ok": False, "latency_ms": _ms(t0), "error": type(e).__name__}


def _to_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc)
    except Exception:
        return None


async def check_webhook() -> dict[str, Any]:
    try:
        from app.api import telegram_webhook

        bot = getattr(telegram_webhook, "_bot", None)
    except Exception:
        bot = None
    if bot is None:
        return {"ok": False, "error": "bot_not_ready"}
    t0 = time.perf_counter()
    try:
        info = await asyncio.wait_for(bot.get_webhook_info(), HTTP_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout", "latency_ms": _ms(t0)}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "latency_ms": _ms(t0)}
    now = datetime.now(timezone.utc)
    err_at = _to_dt(getattr(info, "last_error_date", None))
    url = getattr(info, "url", "") or ""
    return {
        "ok": True,
        "latency_ms": _ms(t0),
        "url_set": bool(url),
        # Host only: the path carries the webhook secret route.
        "url_host": urlparse(url).hostname if url else None,
        "pending_update_count": int(getattr(info, "pending_update_count", 0) or 0),
        "last_error_at": err_at.isoformat() if err_at else None,
        "last_error_age_s": round((now - err_at).total_seconds()) if err_at else None,
        "last_error_message": (getattr(info, "last_error_message", None) or "")[:200] or None,
        "max_connections": getattr(info, "max_connections", None),
    }


def version_info() -> dict[str, Any]:
    sha = next((os.getenv(k) for k in _SHA_ENV if os.getenv(k)), None)
    built = None
    try:
        if _DIST_INDEX.exists():
            built = datetime.fromtimestamp(_DIST_INDEX.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        built = None
    return {
        "git_sha": sha[:12] if sha else None,
        "git_branch": os.getenv("RAILWAY_GIT_BRANCH"),
        "environment": getattr(config, "APP_ENV", None),
        "dashboard_built_at": built,
    }


def uptime() -> dict[str, Any]:
    start = get_bot_start_time()
    if not start:
        return {"started_at": None, "uptime_s": None}
    return {"started_at": start.isoformat(),
            "uptime_s": round((datetime.now(timezone.utc) - start).total_seconds())}


def _flags() -> dict[str, Any]:
    try:
        from app.core.feature_flags import get_feature_flags

        f = get_feature_flags()
        return {"background_workers": bool(f.background_workers_enabled),
                "auto_renewal": bool(f.auto_renewal_enabled)}
    except Exception:
        return {}


def overall_status(s: dict[str, Any]) -> dict[str, Any]:
    """ok / degraded / down + reasons, worst first. Pure (tested)."""
    reasons: list[dict[str, str]] = []

    def add(level: str, key: str, text: str) -> None:
        reasons.append({"level": level, "key": key, "text": text})

    db = s.get("db") or {}
    if not db.get("ready"):
        add("down", "db_not_ready", "База данных не готова: бот работает в деградированном режиме.")
    elif not db.get("ok"):
        add("down", "db_ping", f"База не отвечает на SELECT 1 ({db.get('error') or 'ошибка'}).")
    pool = db.get("pool") or {}
    if pool.get("max") and pool.get("in_use", 0) / pool["max"] >= POOL_BUSY_WARN:
        add("degraded", "db_pool", f"Пул соединений занят на {pool['in_use']} из {pool['max']}.")

    wh = s.get("webhook") or {}
    if wh.get("ok"):
        if not wh.get("url_set"):
            add("down", "webhook_unset", "Вебхук Telegram не установлен: обновления не приходят.")
        pending = int(wh.get("pending_update_count") or 0)
        if pending >= WEBHOOK_PENDING_DOWN:
            add("down", "webhook_backlog", f"В очереди Telegram {pending} необработанных обновлений.")
        elif pending >= WEBHOOK_PENDING_WARN:
            add("degraded", "webhook_backlog", f"В очереди Telegram {pending} необработанных обновлений.")
        age = wh.get("last_error_age_s")
        if age is not None and age <= WEBHOOK_ERROR_RECENT_S:
            text = (f"Telegram не смог доставить обновление {age // 60} мин назад: "
                    f"{wh.get('last_error_message') or 'без текста'}.")
            # A delivery error is actionable only while updates pile up:
            # Telegram retries on its own, and a one-off timeout with an
            # empty queue has already healed. "Piling up" = the queue grew
            # since the previous check, or it is already a backlog.
            growing = bool(wh.get("pending_growing")) and pending > 0
            if growing or pending >= WEBHOOK_PENDING_WARN:
                add("degraded", "webhook_error", text)
            else:
                add("info", "webhook_error", f"{text} Очередь не растёт — Telegram дошлёт сам.")
    elif wh.get("error") != "bot_not_ready":
        add("degraded", "webhook_check", f"Не удалось спросить Telegram о вебхуке ({wh.get('error')}).")

    rw = s.get("remnawave") or {}
    if rw.get("enabled"):
        if not rw.get("ok"):
            add("degraded", "remnawave_down", f"Панель Remnawave не отвечает ({rw.get('error') or 'ошибка'}).")
        elif (rw.get("latency_ms") or 0) >= REMNAWAVE_SLOW_MS:
            add("degraded", "remnawave_slow", f"Панель Remnawave отвечает медленно: {rw['latency_ms']:.0f} мс.")

    rd = s.get("redis") or {}
    if rd.get("configured") and not rd.get("ok"):
        add("degraded", "redis_down", "Redis не отвечает: антифлуд работает в памяти процесса.")

    flags = s.get("flags") or {}
    for w in s.get("workers") or []:
        label = WORKER_LABELS.get(w["name"], w["name"])
        if w["state"] == "stale":
            add("degraded", f"worker_{w['name']}", f"«{label}» давно не завершал цикл.")
        elif w["state"] == "failing":
            add("degraded", f"worker_{w['name']}",
                f"«{label}» падает {w['consecutive_fails']} раз подряд ({w.get('last_error') or 'ошибка'}).")
        elif w["state"] == "paused":
            add("info", f"worker_{w['name']}", f"«{label}» пропускает циклы (флаг или БД).")
    if flags and not flags.get("background_workers", True):
        add("info", "flag_workers", "Фоновые воркеры выключены флагом.")
    elif flags and not flags.get("auto_renewal", True):
        add("info", "flag_auto_renewal", "Автопродление выключено флагом.")

    order = {"down": 0, "degraded": 1, "info": 2}
    reasons.sort(key=lambda r: order[r["level"]])
    status = "down" if any(r["level"] == "down" for r in reasons) else (
        "degraded" if any(r["level"] == "degraded" for r in reasons) else "ok")
    return {"status": status, "reasons": reasons}


# Previous Telegram queue size, in process memory (one bot process).
_last_webhook_pending: Optional[int] = None


def _track_webhook_queue(webhook: dict[str, Any]) -> None:
    """Annotate the webhook check with the previous queue size and whether
    it grew since then. The first check after start has no baseline."""
    global _last_webhook_pending
    if not webhook.get("ok"):
        return
    pending = int(webhook.get("pending_update_count") or 0)
    prev = _last_webhook_pending
    webhook["pending_prev"] = prev
    webhook["pending_growing"] = prev is not None and pending > prev
    _last_webhook_pending = pending


async def collect() -> dict[str, Any]:
    db = await check_db()  # connection released inside before any HTTP call
    remnawave, redis, webhook = await asyncio.gather(
        check_remnawave(), check_redis(), check_webhook(),
    )
    _track_webhook_queue(webhook)
    workers = runtime_health.snapshot()
    for w in workers:
        w["label"] = WORKER_LABELS.get(w["name"], w["name"])
    snap: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "db": db,
        "remnawave": remnawave,
        "redis": redis,
        "webhook": webhook,
        "workers": workers,
        "flags": _flags(),
        "alerts": runtime_health.alerts_summary(),
        "uptime": uptime(),
        "version": version_info(),
    }
    snap["overall"] = overall_status(snap)
    return snap
