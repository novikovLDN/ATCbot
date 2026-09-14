"""Dashboard metrics — thin routes over database/revenue.py,
database/metrics.py and app/services/system_health.py. Every number has
one definition, written down in docs/dashboard/metrics.md.

Reads only. The dashboard shares the process and the connection pool
with the bot, so:
  * every aggregate is cached server-side (app/services/dashboard_cache:
    one load per key per TTL, concurrent callers share the in-flight one);
  * every SQL read runs read-only with a statement_timeout
    (database/readonly.py);
  * HTTP checks (panel, Telegram, Redis) never run while a DB connection
    is held.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import config
import database
from app.api.dashboard.deps import require_admin
from app.services import panel_stats, system_health
from app.services.dashboard_cache import cached
from database import metrics as mx
from database import revenue as rev

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])

REPORT_TTL = 60.0
COHORT_TTL = 300.0
HEALTH_TTL = 30.0
ENGAGEMENT_TTL = 300.0
PANEL_WAIT_SECONDS = 4.0


async def _run(name: str, coro):
    try:
        return await coro
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("DASHBOARD_METRICS_FAIL %s", name)
        raise HTTPException(500, f"{name}_failed: {type(e).__name__}")


@router.get("/money")
async def metrics_money(days: int = Query(30, ge=1, le=365)):
    return await _run("money", cached(f"m:money:{days}", REPORT_TTL, lambda: rev.money_report(days)))


@router.get("/subscribers")
async def metrics_subscribers(
    days: int = Query(30, ge=1, le=365),
    grace_days: int = Query(mx.DEFAULT_RENEWAL_GRACE_DAYS, ge=0, le=30),
):
    return await _run(
        "subscribers",
        cached(f"m:subs:{days}:{grace_days}", REPORT_TTL,
               lambda: mx.subscribers_report(days, grace_days)),
    )


@router.get("/cohorts")
async def metrics_cohorts(months: int = Query(12, ge=2, le=24)):
    return await _run("cohorts", cached(f"m:cohorts:{months}", COHORT_TTL, lambda: rev.cohort_ltv(months)))


@router.get("/series")
async def metrics_series(
    unit: str = Query("day", pattern="^(day|week|month)$"),
    periods: int = Query(30, ge=2, le=366),
):
    return await _run("series", cached(f"m:series:{unit}:{periods}", REPORT_TTL,
                                       lambda: rev.series(unit, periods)))


async def _operations(hours: int) -> dict[str, Any]:
    summary, recent, daily, queue, snap = await asyncio.gather(
        database.get_payment_errors_summary(hours),
        database.get_recent_payment_errors(limit=30, hours=hours),
        mx.payment_errors_daily(14),
        mx.provisioning_queue(),
        mx.operations_snapshot(),
    )
    return {
        "hours": hours,
        "errors": summary,
        "errors_recent": [_iso(r) for r in recent],
        "errors_daily": daily,
        "provisioning": queue,
        "queues": snap,
    }


@router.get("/operations")
async def metrics_operations(hours: int = Query(24, ge=1, le=720)):
    return await _run("operations", cached(f"m:ops:{hours}", 30.0, lambda: _operations(hours)))


def _iso(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in row.items() if k != "raw_payload"}


# ── Health: system, payments, delivery ────────────────────────────────


async def _system_health() -> dict[str, Any]:
    return await cached("m:health", HEALTH_TTL, system_health.collect)


@router.get("/health")
async def metrics_health():
    return await _run("health", _system_health())


def provider_enabled() -> dict[str, Optional[bool]]:
    """Which providers can take money right now (config, not the DB).
    None = could not tell."""
    out: dict[str, Optional[bool]] = {}
    for name, module in (("platega", "platega_service"), ("wata", "wata_service"),
                         ("cryptobot", "cryptobot_service")):
        try:
            out[name] = bool(importlib.import_module(module).is_enabled())
        except Exception:
            out[name] = None
    out["telegram_payment"] = bool(getattr(config, "TG_PROVIDER_TOKEN", None))
    out["telegram_stars"] = True  # Stars need no provider token
    return out


async def _payments_health() -> dict[str, Any]:
    raw = await mx.payments_health()
    now = datetime.fromisoformat(raw["now"])
    enabled = provider_enabled()
    names = sorted((set(enabled) | set(raw["last_paid"]) | set(raw["paid_7d"])) - {"unknown", "balance"})
    providers = []
    for p in names:
        last = raw["last_paid"].get(p)
        silence = mx.provider_silence(datetime.fromisoformat(last) if last else None,
                                      int(raw["paid_7d"].get(p, 0)), now, enabled.get(p, True))
        providers.append({"provider": p, "enabled": enabled.get(p), "last_paid_at": last,
                          "paid_7d": int(raw["paid_7d"].get(p, 0)), **silence})
    raw["providers"] = providers
    raw["verdict"] = mx.payments_verdict(raw)
    return raw


@router.get("/payments-health")
async def metrics_payments_health():
    return await _run("payments_health", cached("m:payhealth", REPORT_TTL, _payments_health))


async def _delivery() -> dict[str, Any]:
    d = await mx.delivery_health()
    d["verdict"] = mx.delivery_verdict(d)
    return d


@router.get("/delivery")
async def metrics_delivery():
    return await _run("delivery", cached("m:delivery", 30.0, _delivery))


@router.get("/engagement")
async def metrics_engagement(days: int = Query(7, ge=1, le=90)):
    async def load():
        w = rev.window(days)
        return {"window_days": days, **(await mx.engagement(w["since"], w["until"]))}
    return await _run("engagement", cached(f"m:engagement:{days}", ENGAGEMENT_TTL, load))


@router.get("/pipeline")
async def metrics_pipeline():
    return await _run("pipeline", cached("m:pipeline", REPORT_TTL, mx.renewal_pipeline))


# ── Overview ──────────────────────────────────────────────────────────


def build_alerts(*, money: dict, active: dict, errors: dict, queues: dict,
                 provisioning: dict, panel: Optional[dict], nodes: Optional[dict],
                 health: Optional[dict] = None, payments: Optional[dict] = None) -> list[dict]:
    """What needs the owner's attention, worst first. Pure: tested directly."""
    alerts: list[dict] = []

    def add(level: str, key: str, title: str, detail: str, link: str) -> None:
        alerts.append({"level": level, "key": key, "title": title, "detail": detail, "link": link})

    for r in (health or {}).get("reasons") or []:
        if r["level"] == "down":
            add("critical", f"health_{r['key']}", "Система: сбой", r["text"], "/health")
        elif r["level"] == "degraded" and not r["key"].startswith("remnawave"):
            add("warning", f"health_{r['key']}", "Система: деградация", r["text"], "/health")

    if panel is not None and not panel.get("available", False):
        add("critical", "panel_down", "Панель Remnawave не отвечает",
            "Статистика панели недоступна. Проверьте панель и токен API.", "/panel")
    if nodes and nodes.get("available") and nodes.get("offline"):
        # Disabled nodes are excluded upstream (normalize_nodes). One node
        # down out of many is a warning — clients fail over; half or more
        # of the enabled fleet down is critical.
        offline = int(nodes["offline"])
        enabled = int(nodes.get("enabled") or nodes.get("total") or offline)
        major = offline * 2 >= enabled
        add("critical" if major else "warning", "nodes_offline",
            f"Ноды не в сети: {offline} из {enabled}",
            "Большая часть нод недоступна: у пользователей нет VPN." if major
            else "Клиенты переключатся на другие ноды. Проверьте ноду в панели.", "/panel")
    if provisioning.get("available") and provisioning.get("dead"):
        add("critical", "provisioning_dead", f"Выдача не удалась: {provisioning['dead']}",
            "Оплачено, но доступ не выдан. Разберите вручную.", "/health")

    total_err = int(errors.get("total") or 0)
    if total_err:
        add("critical" if total_err >= 10 else "warning", "payment_errors",
            f"Ошибки платежей за 24 ч: {total_err}",
            ", ".join(f"{s['stage']} ×{s['count']}" for s in (errors.get("by_stage") or [])[:3]),
            "/health")

    for r in (payments or {}).get("reasons") or []:
        if r["key"].startswith("silent_"):
            add("warning", r["key"], "Провайдер молчит", r["text"], "/health")

    for p in money.get("providers") or []:
        settled = int(p.get("paid") or 0) + int(p.get("expired") or 0)
        sr = p.get("success_rate")
        if sr is not None and settled >= 10 and sr < 50:
            add("warning", f"provider_{p['provider']}", f"{p['provider']}: доходит {sr:.0f}%",
                "Оплачено ÷ (оплачено + истекло) за период.", "/money")

    if int(queues.get("stuck_activations") or 0):
        add("warning", "stuck_activations", f"Активации в ожидании: {queues['stuck_activations']}",
            "Оплатили, ключ ещё не выдан.", "/health")

    d = (money.get("delta_pct") or {}).get("net")
    if d is not None and d <= -30:
        add("warning", "revenue_drop", f"Выручка упала на {abs(d):.0f}%",
            "К предыдущему периоду той же длины.", "/money")

    exp = (active.get("expiring_7d") or {})
    if exp.get("total"):
        manual = int(exp["total"]) - int(exp.get("auto_renew_on") or 0)
        if manual:
            add("info", "expiring", f"Истекает за 7 дней: {exp['total']}",
                f"Без автопродления: {manual}.", "/subscribers")

    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order[a["level"]])
    return alerts


async def _panel_quick() -> tuple[Optional[dict], Optional[dict]]:
    """Panel blocks for the overview, bounded: a slow panel must not hold
    the home screen hostage. None = not checked in time."""
    try:
        overview, nodes = await asyncio.wait_for(
            asyncio.gather(panel_stats.system_overview(), panel_stats.nodes_overview()),
            timeout=PANEL_WAIT_SECONDS,
        )
        return overview["system"], nodes
    except Exception as e:
        logger.warning("overview: panel skipped: %s", e)
        return None, None


def kpi(key: str, label: str, cur: Any, prev: Any, unit: str = "count") -> dict[str, Any]:
    return {"key": key, "label": label, "unit": unit, "value": cur, "prev": prev,
            "delta_pct": rev.delta_pct(cur, prev)}


async def _safe(name: str, coro, default):
    """One failing section must not blank the home screen."""
    try:
        return await coro
    except Exception:
        logger.exception("overview: %s failed", name)
        return default


async def _overview(days: int) -> dict[str, Any]:
    w = rev.window(days)
    cur = await rev.totals(w["since"], w["until"])
    prev = await rev.totals(w["prev_since"], w["prev_until"])
    pay = await rev.payers(w["since"], w["until"])
    prev_pay = await rev.payers(w["prev_since"], w["prev_until"])
    nu = await mx.new_users(w["since"], w["until"])
    prev_nu = await mx.new_users(w["prev_since"], w["prev_until"])
    series = await rev.series("day", max(days, 14))
    active = await mx.active_subscriptions()
    renewals = await mx.renewals(w["since"], w["until"])
    pipeline = await _safe("pipeline", cached("m:pipeline", REPORT_TTL, mx.renewal_pipeline), None)
    pay_h = await _safe("payments_health", cached("m:payhealth", REPORT_TTL, _payments_health), None)
    deliv = await _safe("delivery", cached("m:delivery", 30.0, _delivery), None)
    health = await _safe("health", _system_health(), None)
    panel, nodes = await _panel_quick()

    arppu, prev_arppu = rev.per_user(cur["net_kopecks"], pay["payers"]), rev.per_user(prev["net_kopecks"], prev_pay["payers"])
    kpis = [
        kpi("revenue", "Выручка VPN", cur["net_kopecks"], prev["net_kopecks"], "kopecks"),
        kpi("payments", "Оплат", cur["net_count"], prev["net_count"]),
        kpi("payers", "Платящие", pay["payers"], prev_pay["payers"]),
        kpi("new_payers", "Новые платящие", pay["new"], prev_pay["new"]),
        kpi("new_users", "Новые пользователи", nu, prev_nu),
        kpi("arppu", "ARPPU", arppu, prev_arppu, "kopecks"),
    ]
    lines = {k: {"value": cur[f"{k}_kopecks"], "prev": prev[f"{k}_kopecks"],
                 "delta_pct": rev.delta_pct(cur[f"{k}_kopecks"], prev[f"{k}_kopecks"])}
             for k in ("shop", "proxy", "game", "gross")}

    pv = (pay_h or {}).get("verdict") or {"status": "unknown", "reasons": []}
    dv = (deliv or {}).get("verdict") or {"status": "unknown", "reasons": []}
    hv = (health or {}).get("overall") or {"status": "unknown", "reasons": []}
    q = (deliv or {}).get("queue") or {"available": False}
    act = (deliv or {}).get("activations") or {}
    providers_24h = (pay_h or {}).get("providers_24h") or []
    created = sum(p["created"] for p in providers_24h)
    paid = sum(p["paid"] for p in providers_24h)

    return {
        "window_days": days,
        "since": w["since"].isoformat(),
        "prev_since": w["prev_since"].isoformat(),
        "prev_until": w["prev_until"].isoformat(),
        "kpis": kpis,
        "lines": lines,
        "series": series,
        "subscribers": {
            "with_access": active["with_access"],
            "paid": active["paid"],
            "by_kind": active["by_kind"],
            "granted_sources": active.get("granted_sources", {}),
            "expiring_7d": active["expiring_7d"],
            "auto_renew_share": active["auto_renew_share"],
            "renewal_rate": renewals["renewal_rate"],
            "churn_rate": renewals["churn_rate"],
            "pipeline": pipeline,
        },
        "health": {"status": hv["status"], "reasons": hv["reasons"][:6],
                   "checked_at": (health or {}).get("checked_at")},
        "payments": {
            "status": pv["status"], "reasons": pv["reasons"][:6],
            "errors_24h": ((pay_h or {}).get("errors_24h") or {}).get("total", 0),
            "invoices_24h": created, "paid_24h": paid,
            "conversion_24h": round(paid / created * 100, 1) if created else None,
            "silent": [p["provider"] for p in (pay_h or {}).get("providers", []) if p.get("state") == "silent"],
        },
        "delivery": {
            "status": dv["status"], "reasons": dv["reasons"][:6],
            "queue_open": (q.get("pending_new", 0) + q.get("retrying", 0) + q.get("running", 0))
            if q.get("available") else None,
            "dead": q.get("dead") if q.get("available") else None,
            "activations_pending": act.get("pending", 0),
        },
        "panel": {
            "checked": panel is not None,
            "available": bool(panel and panel.get("available")),
            "online_now": (panel or {}).get("online_now"),
            "nodes_online": (nodes or {}).get("online"),
            "nodes_total": (nodes or {}).get("total"),
            # v5: disabled nodes are not problems; offline = offline + connecting.
            "nodes_enabled": (nodes or {}).get("enabled"),
            "nodes_offline": (nodes or {}).get("offline"),
            "nodes_disabled": (nodes or {}).get("disabled"),
        },
        "alerts": build_alerts(
            money={"providers": (pay_h or {}).get("providers_7d") or [],
                   "delta_pct": {"net": kpis[0]["delta_pct"]}},
            active=active,
            errors=(pay_h or {}).get("errors_24h") or {},
            queues={"stuck_activations": act.get("pending", 0)},
            provisioning={"available": q.get("available", False), "dead": q.get("dead", 0)},
            panel=panel, nodes=nodes, health=hv, payments=pv,
        ),
    }


@router.get("/overview")
async def metrics_overview(days: int = Query(7, ge=1, le=90)):
    return await _run("overview", cached(f"m:overview:{days}", REPORT_TTL, lambda: _overview(days)))
