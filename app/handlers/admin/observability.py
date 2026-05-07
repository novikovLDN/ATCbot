"""
Admin Observability dashboard — extended runtime metrics inside Telegram.

Two screens, available from `admin:main`:

  ▸ admin:observability               — overview (workers, error rate,
                                         payment funnel, rate-limit, DB pool)
  ▸ admin:observability:metrics       — drill-down list of all metric
                                         counters / gauges / histograms

Read-only. No state mutation. Refresh = click "Обновить" again.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
import database
from app.core import metrics as _metrics
from app.core import worker_registry
from app.handlers.common.utils import safe_edit_text
from app.i18n import get_text as i18n_get_text
from app.services.language_service import resolve_user_language

admin_observability_router = Router()
logger = logging.getLogger(__name__)


# ─────────────────────────── helpers ───────────────────────────


_HEALTH_ICON = {
    "healthy":   "🟢",
    "starting":  "⏳",
    "stale":     "🟡",
    "failing":   "🟠",
    "never_ran": "🔴",
}


def _fmt_age(seconds):
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}с"
    if seconds < 3600:
        return f"{seconds / 60:.1f}м"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}ч"
    return f"{seconds / 86400:.1f}д"


def _fmt_ms(value):
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value / 1000:.2f}с"
    return f"{value:.0f}мс"


def _format_workers_section() -> str:
    workers = worker_registry.snapshot()
    if not workers:
        return "🛠 Воркеры: нет зарегистрированных\n"
    lines = ["🛠 <b>Воркеры</b>"]
    stale_count = 0
    for w in workers:
        icon = _HEALTH_ICON.get(w["health"], "❓")
        if w["health"] in ("stale", "never_ran", "failing"):
            stale_count += 1
        lines.append(
            f"{icon} <code>{w['name']}</code> "
            f"· last: {_fmt_age(w['last_end_age_s'])} назад "
            f"· итер: {w['iteration_count']} "
            f"· сбоев: {w['failure_count']} "
            f"({w['consecutive_failures']} подряд)"
        )
        if w.get("last_error"):
            lines.append(f"   ↳ ошибка: <code>{w['last_error']}</code>")
    summary = f"\n<i>проблемных: {stale_count}/{len(workers)}</i>"
    return "\n".join(lines) + summary + "\n"


def _format_payment_funnel(snapshot: dict) -> str:
    counters = snapshot.get("counters", {})
    intent = counters.get(_metrics.M.PAYMENT_INTENT_TOTAL, {})
    funnel = {"created": 0, "paid": 0, "failed": 0, "expired": 0}
    for entry in intent.values():
        outcome = entry["labels"].get("outcome", "unknown")
        funnel[outcome] = funnel.get(outcome, 0) + entry["total"]
    lines = ["💳 <b>Платёжная воронка</b> (всего)"]
    lines.append(
        f"   создано: {funnel.get('created', 0)} · "
        f"оплачено: {funnel.get('paid', 0)} · "
        f"провалено: {funnel.get('failed', 0)} · "
        f"истекло: {funnel.get('expired', 0)}"
    )
    if funnel.get("created", 0) > 0:
        conv = (funnel.get("paid", 0) / funnel.get("created", 1)) * 100
        lines.append(f"   конверсия created→paid: <b>{conv:.1f}%</b>")
    # Provider latency
    hist = snapshot.get("histograms", {}).get(_metrics.M.PAYMENT_PROVIDER_LATENCY_MS, {})
    if hist:
        lines.append("   латентность провайдеров (p95):")
        for entry in hist.values():
            provider = entry["labels"].get("provider", "?")
            p95 = entry.get("p95_ms")
            lines.append(f"     · {provider}: {_fmt_ms(p95)} (n={entry['count']})")
    return "\n".join(lines) + "\n"


def _format_handlers_section(snapshot: dict) -> str:
    counters = snapshot.get("counters", {})
    handler_total = counters.get(_metrics.M.HANDLER_TOTAL, {})
    if not handler_total:
        return ""
    success = degraded = failed = 0
    for entry in handler_total.values():
        outcome = entry["labels"].get("outcome", "unknown")
        if outcome == "success":
            success += entry["total"]
        elif outcome == "degraded":
            degraded += entry["total"]
        elif outcome == "failed":
            failed += entry["total"]
    total = success + degraded + failed
    lines = ["⚙️ <b>Хендлеры</b>"]
    lines.append(
        f"   успех: {success} · degraded: {degraded} · ошибок: {failed} (всего {total})"
    )
    if total > 0:
        err_rate = (failed / total) * 100
        lines.append(f"   error rate: <b>{err_rate:.2f}%</b>")
    # p95 latency
    hist = snapshot.get("histograms", {}).get(_metrics.M.HANDLER_LATENCY_MS, {})
    if hist:
        # Pick the 5 slowest handlers by p95.
        with_p95 = []
        for entry in hist.values():
            handler = entry["labels"].get("handler", "?")
            p95 = entry.get("p95_ms")
            if p95 is not None:
                with_p95.append((handler, p95, entry["count"]))
        with_p95.sort(key=lambda x: -x[1])
        if with_p95:
            lines.append("   топ-5 по p95:")
            for handler, p95, count in with_p95[:5]:
                lines.append(f"     · {handler}: {_fmt_ms(p95)} (n={count})")
    return "\n".join(lines) + "\n"


def _format_rate_limit_section(snapshot: dict) -> str:
    counters = snapshot.get("counters", {})
    hits = counters.get(_metrics.M.RATE_LIMIT_HIT_TOTAL, {})
    if not hits:
        return ""
    lines = ["🛑 <b>Rate-limit</b>"]
    for entry in hits.values():
        action = entry["labels"].get("action", "?")
        lines.append(
            f"   · {action}: всего {entry['total']} "
            f"(60с: {entry['rate_60s']:.1f}/с, 5м: {entry['rate_5m']:.1f}/с)"
        )
    return "\n".join(lines) + "\n"


def _format_referral_section(snapshot: dict) -> str:
    counters = snapshot.get("counters", {})
    reg = counters.get(_metrics.M.REFERRAL_REGISTERED_TOTAL, {})
    act = counters.get(_metrics.M.REFERRAL_ACTIVATED_TOTAL, {})
    trial = counters.get(_metrics.M.TRIAL_ACTIVATED_TOTAL, {})
    if not (reg or act or trial):
        return ""
    reg_total = sum(e["total"] for e in reg.values())
    act_total = sum(e["total"] for e in act.values())
    trial_total = sum(e["total"] for e in trial.values())
    return (
        "📈 <b>Воронка trial / referral</b>\n"
        f"   trial активировано: {trial_total}\n"
        f"   referral регистраций: {reg_total} · активировано: {act_total}\n"
    )


def _format_db_section(snapshot: dict) -> str:
    gauges = snapshot.get("gauges", {})
    pool_size = gauges.get(_metrics.M.DB_POOL_SIZE, {}).get("_")
    pool_free = gauges.get(_metrics.M.DB_POOL_FREE, {}).get("_")
    hist = snapshot.get("histograms", {}).get(_metrics.M.DB_POOL_ACQUIRE_WAIT_MS, {})
    if pool_size is None and pool_free is None and not hist:
        return ""
    lines = ["🗄 <b>База данных</b>"]
    if pool_size is not None or pool_free is not None:
        lines.append(
            f"   пул: free {int(pool_free or 0)} / size {int(pool_size or 0)}"
        )
    for entry in hist.values():
        p95 = entry.get("p95_ms")
        lines.append(
            f"   acquire wait p95: {_fmt_ms(p95)} (n={entry['count']})"
        )
    return "\n".join(lines) + "\n"


def _build_overview_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:observability")],
            [InlineKeyboardButton(text="📜 Все метрики", callback_data="admin:observability:metrics")],
            [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
        ]
    )


# ─────────────────────────── handlers ───────────────────────────


@admin_observability_router.callback_query(F.data == "admin:observability")
async def callback_admin_observability(callback: CallbackQuery):
    """Live observability overview."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)
    snap = _metrics.snapshot()

    parts = ["📡 <b>Наблюдаемость</b>",
             f"<i>обновлено: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>",
             ""]
    parts.append(_format_workers_section())
    pf = _format_payment_funnel(snap)
    if pf:
        parts.append(pf)
    hs = _format_handlers_section(snap)
    if hs:
        parts.append(hs)
    rs = _format_rate_limit_section(snap)
    if rs:
        parts.append(rs)
    ref = _format_referral_section(snap)
    if ref:
        parts.append(ref)
    dbs = _format_db_section(snap)
    if dbs:
        parts.append(dbs)

    text = "\n".join(p for p in parts if p)
    # Telegram message limit 4096 — trim defensively.
    if len(text) > 3800:
        text = text[:3800] + "\n…(обрезано)"

    await safe_edit_text(callback.message, text, reply_markup=_build_overview_keyboard(language))
    await callback.answer()

    try:
        await database._log_audit_event_atomic_standalone(
            "admin_view_observability",
            callback.from_user.id,
            None,
            "Admin viewed observability dashboard",
        )
    except Exception:
        pass


@admin_observability_router.callback_query(F.data == "admin:observability:metrics")
async def callback_admin_observability_metrics(callback: CallbackQuery):
    """Drill-down: list all counters / gauges / histograms."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        language = await resolve_user_language(callback.from_user.id)
        await callback.answer(i18n_get_text(language, "admin.access_denied"), show_alert=True)
        return

    language = await resolve_user_language(callback.from_user.id)
    snap = _metrics.snapshot()

    lines = ["📜 <b>Все метрики</b>", ""]

    counters = snap.get("counters", {})
    if counters:
        lines.append("🔢 <b>Counters</b>")
        for name, entries in sorted(counters.items()):
            lines.append(f"  <code>{name}</code>")
            for label_str, entry in entries.items():
                lines.append(
                    f"     {label_str}: {entry['total']} "
                    f"(60с: {entry['rate_60s']:.2f}/с)"
                )
        lines.append("")

    gauges = snap.get("gauges", {})
    if gauges:
        lines.append("🎚 <b>Gauges</b>")
        for name, entries in sorted(gauges.items()):
            for label_str, value in entries.items():
                lines.append(f"  <code>{name}</code> {label_str}: {value}")
        lines.append("")

    hist = snap.get("histograms", {})
    if hist:
        lines.append("📊 <b>Histograms (p50 / p95 / p99)</b>")
        for name, entries in sorted(hist.items()):
            lines.append(f"  <code>{name}</code>")
            for label_str, entry in entries.items():
                p50 = _fmt_ms(entry.get("p50_ms"))
                p95 = _fmt_ms(entry.get("p95_ms"))
                p99 = _fmt_ms(entry.get("p99_ms"))
                lines.append(f"     {label_str}: {p50} / {p95} / {p99} (n={entry['count']})")
        lines.append("")

    if not (counters or gauges or hist):
        lines.append("<i>метрик пока нет</i>")

    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n…(обрезано)"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:observability:metrics")],
        [InlineKeyboardButton(text="◀️ Обзор", callback_data="admin:observability")],
        [InlineKeyboardButton(text=i18n_get_text(language, "admin.back"), callback_data="admin:main")],
    ])
    await safe_edit_text(callback.message, text, reply_markup=keyboard)
    await callback.answer()
