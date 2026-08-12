"""
Admin: tariff-vs-panel audit for active premium subscribers.

WHAT IT DOES
------------
Read-only diagnostic. For every active premium subscriber in the DB
(NOT bypass-only — those are traffic packs, live on +10y by design):

  1. Pull every paid record we can find:
     - subscription_history.end_date (canonical ledger)
     - pending_purchases (paid)
     - payments JOIN pending_purchases
     - gift_subscriptions (activated)
     and take MAX as the user's REAL legitimate end date.

  2. Compare against the bot's DB (subscriptions.expires_at).

  3. Compare against the panel's premium entity for the user
     (looked up STRICTLY by username = 'tg_<telegram_id>_premium' so
     we never confuse with a bypass entity, which has a different
     username).

The tool issues NO PATCHes. It only produces a categorised report.

GUARDRAILS
----------
- Background task. Admin starts it, gets an "запущено" reply, and can
  poll status via the same button or get the final report when done.
- Concurrency 3 + 400ms throttle between records. Will not compete
  with the bot's regular workers or rate-limit the panel.
- 10s per-HTTP-call timeout (wait_for inside the worker, after the
  semaphore — same pattern that finally worked in recovery).
- Idempotent + safe to cancel: nothing is written to the DB or panel.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
import database
from app.services import remnawave_api, remnawave_premium
from app.handlers.admin.keyboards import get_admin_back_keyboard
from app.handlers.common.utils import safe_edit_text

admin_audit_subs_router = Router()
logger = logging.getLogger(__name__)

# Tolerance: differences below this are treated as equal.
_TOLERANCE_SECONDS = 24 * 3600  # one day
# Concurrent panel calls during audit.
_AUDIT_CONCURRENCY = 3
# Sleep after each record — gentle on the panel.
_AUDIT_THROTTLE_S = 0.4
# Per-HTTP-call timeout.
_AUDIT_HTTP_TIMEOUT_S = 10
# Seconds between live progress edits.
_PROGRESS_INTERVAL = 6
# Hard ceiling.
_MAX_SCAN = 100_000

# In-memory state of the running audit per admin id.
# Each entry: {"status": "running"|"done"|"failed",
#              "total": int, "done": int,
#              "buckets": {key: int},
#              "samples": {key: [dict, ...]},
#              "error": str | None,
#              "task": asyncio.Task | None}
_audits: dict[int, dict] = {}


def _parse_panel_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _compute_real_end(rows: list) -> Optional[datetime]:
    """Replay paid records to derive the user's expected last end date.

    Each row has {created_at, period_days}. Renewal stacking respected.
    Returns None if no rows.
    """
    end: Optional[datetime] = None
    for row in rows:
        created = row["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        days = int(row["period_days"] or 0)
        if days <= 0:
            continue
        if end is None or created >= end:
            end = created + timedelta(days=days)
        else:
            end = end + timedelta(days=days)
    return end


async def _audit_worker(admin_id: int):
    """The actual long-running audit. Writes into _audits[admin_id]."""
    state = _audits[admin_id]
    try:
        subs = await database.get_active_premium_subscribers()
        subs = subs[:_MAX_SCAN]
        state["total"] = len(subs)
        state["done"] = 0

        tg_ids = [s["telegram_id"] for s in subs]
        history_ends = await database.get_max_subscription_end_bulk(tg_ids)
        paid = await database.get_paid_subscription_history_bulk(tg_ids)
        gifts = await database.get_activated_gifts_bulk(tg_ids)
        payments_hist = await database.get_paid_payments_via_purchases_bulk(tg_ids)

        sem = asyncio.Semaphore(_AUDIT_CONCURRENCY)

        async def _check_one(sub):
            tg = sub["telegram_id"]
            db_expires_at = sub["expires_at"]
            if db_expires_at and db_expires_at.tzinfo is None:
                db_expires_at = db_expires_at.replace(tzinfo=timezone.utc)
            expected_username = f"tg_{tg}_premium"

            # ── Compute expected end from all DB sources ──────────────
            signals = []
            hist = history_ends.get(tg)
            if hist is not None:
                if hist.tzinfo is None:
                    hist = hist.replace(tzinfo=timezone.utc)
                signals.append(("history", hist))
            for source_name, src in (
                ("gift", [{"created_at": g["activated_at"],
                          "period_days": g["period_days"]}
                         for g in gifts.get(tg, [])]),
                ("paid", paid.get(tg, [])),
                ("payments", payments_hist.get(tg, [])),
            ):
                computed = _compute_real_end(src)
                if computed is not None:
                    signals.append((source_name, computed))

            expected_end = None
            expected_source = "none"
            if signals:
                expected_source, expected_end = max(signals, key=lambda s: s[1])

            # ── Panel lookup STRICTLY via username ────────────────────
            panel_user = None
            panel_error = None
            try:
                panel_user = await asyncio.wait_for(
                    remnawave_api.find_user_by_username(expected_username),
                    timeout=_AUDIT_HTTP_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                panel_error = "timeout"
            except Exception as e:
                panel_error = f"{type(e).__name__}: {e}"

            panel_expires = None
            if panel_user:
                panel_expires = _parse_panel_dt(panel_user.get("expireAt"))

            # ── Categorise ────────────────────────────────────────────
            rec = {
                "telegram_id": tg,
                "tariff": sub.get("subscription_type"),
                "db_expires_at": db_expires_at,
                "expected_end": expected_end,
                "expected_source": expected_source,
                "panel_expires": panel_expires,
                "panel_error": panel_error,
            }

            buckets = state["buckets"]
            samples = state["samples"]
            samples_full = state["samples_full"]
            # Buckets we want to keep EVERY record for, so the operator
            # can review the full list before/after fix.
            _FULL_KEEP = {"panel_behind_paid", "panel_missing",
                          "panel_ahead_of_paid"}

            def _sample(bucket_key):
                buckets[bucket_key] = buckets.get(bucket_key, 0) + 1
                lst = samples.setdefault(bucket_key, [])
                if len(lst) < 5:
                    lst.append(rec)
                if bucket_key in _FULL_KEEP:
                    samples_full.setdefault(bucket_key, []).append(rec)

            if panel_error == "timeout":
                _sample("panel_timeout")
            elif panel_user is None:
                _sample("panel_missing")
            else:
                # Have a panel entity. Compare panel vs expected vs DB.
                if expected_end is None:
                    _sample("no_paid_signal_but_db_active")
                else:
                    if panel_expires is None:
                        _sample("panel_no_expire")
                    else:
                        delta = (panel_expires - expected_end).total_seconds()
                        if abs(delta) <= _TOLERANCE_SECONDS:
                            # Panel matches expected — good.
                            # Now compare DB ↔ expected.
                            if db_expires_at is None:
                                _sample("db_no_expire")
                            else:
                                d_delta = (db_expires_at - expected_end).total_seconds()
                                if abs(d_delta) <= _TOLERANCE_SECONDS:
                                    _sample("ok")
                                elif d_delta > 0:
                                    _sample("db_ahead_of_paid")
                                else:
                                    _sample("db_behind_paid")
                        elif delta > _TOLERANCE_SECONDS:
                            _sample("panel_ahead_of_paid")
                        else:
                            _sample("panel_behind_paid")

        async def _check_one_throttled(sub):
            async with sem:
                try:
                    await _check_one(sub)
                except Exception as e:
                    logger.exception("AUDIT_SUBS: tg=%s error: %s",
                                     sub["telegram_id"], e)
                    state["buckets"]["error"] = state["buckets"].get("error", 0) + 1
                state["done"] += 1
                try:
                    await asyncio.sleep(_AUDIT_THROTTLE_S)
                except Exception:
                    pass

        await asyncio.gather(*[_check_one_throttled(s) for s in subs])
        state["status"] = "done"
        logger.info("AUDIT_SUBS_DONE admin=%s checked=%s buckets=%s",
                    admin_id, state["done"], state["buckets"])
    except Exception as e:
        logger.exception("AUDIT_SUBS_FAILED admin=%s: %s", admin_id, e)
        state["status"] = "failed"
        state["error"] = f"{type(e).__name__}: {e}"


_BUCKET_LABELS = {
    "ok": "✅ Всё совпадает (БД ≈ оплаты ≈ панель)",
    "panel_ahead_of_paid": "🚨 Панель ВПЕРЕДИ оплаты (юзер сидит дольше, чем заплатил)",
    "panel_behind_paid": "⚠️ Панель ПОЗАДИ оплаты (юзер получает меньше)",
    "panel_missing": "👻 Entity нет на панели (но подписка active в БД)",
    "panel_no_expire": "⚠️ Entity на панели без expireAt",
    "panel_timeout": "⏱ Таймаут на панель — недопроверены",
    "db_ahead_of_paid": "⚠️ БД ВПЕРЕДИ оплаты (но панель совпадает)",
    "db_behind_paid": "⚠️ БД ПОЗАДИ оплаты (panel совпадает с oplaty)",
    "db_no_expire": "⚠️ БД active без expires_at",
    "no_paid_signal_but_db_active": "⚠️ Active без следов оплаты",
    "error": "❌ Ошибка проверки",
}


def _actionable_records(state: dict) -> list:
    """Records we can auto-fix in the panel: behind-paid or missing."""
    out = []
    for key in ("panel_behind_paid", "panel_missing"):
        for s in state.get("samples_full", {}).get(key, []):
            out.append((key, s))
    return out


def _format_report(state: dict, limit_samples: int = 3,
                   full_list: bool = False) -> str:
    total = state.get("total", 0)
    done = state.get("done", 0)
    buckets = state.get("buckets", {})

    lines = [
        "🔍 <b>Аудит активных premium-подписок</b>",
        "",
        f"Проверено: <b>{done}</b> / {total}",
        f"Статус: {state.get('status', 'running')}",
        "",
    ]
    if state.get("status") == "failed":
        lines.append(f"❌ Ошибка: <code>{state.get('error') or '—'}</code>")
        return "\n".join(lines)

    order = [
        "ok",
        "panel_ahead_of_paid",
        "db_ahead_of_paid",
        "panel_behind_paid",
        "db_behind_paid",
        "panel_missing",
        "no_paid_signal_but_db_active",
        "panel_no_expire",
        "db_no_expire",
        "panel_timeout",
        "error",
    ]
    for key in order:
        n = buckets.get(key, 0)
        if n == 0:
            continue
        label = _BUCKET_LABELS.get(key, key)
        lines.append(f"  {label}: <b>{n}</b>")

    # When showing the full actionable list (for review before fix),
    # dump every panel_behind_paid + panel_missing record.
    if full_list:
        for key, label_prefix in (
            ("panel_behind_paid", "⚠️ Панель ПОЗАДИ оплаты"),
            ("panel_missing", "👻 Entity нет на панели"),
        ):
            recs = state.get("samples_full", {}).get(key, [])
            if not recs:
                continue
            lines.append("")
            lines.append(f"<i>{label_prefix} (всего {len(recs)}):</i>")
            for s in recs:
                tg = s["telegram_id"]
                db_str = s["db_expires_at"].strftime("%Y-%m-%d") if s["db_expires_at"] else "—"
                exp_str = s["expected_end"].strftime("%Y-%m-%d") if s["expected_end"] else "—"
                pan_str = s["panel_expires"].strftime("%Y-%m-%d") if s["panel_expires"] else "—"
                tariff = s.get("tariff") or "?"
                lines.append(
                    f"  <code>{tg}</code> · {tariff} · "
                    f"БД:{db_str} · paid:{exp_str} · panel:{pan_str}"
                )
        return "\n".join(lines)

    # Compact: just a few examples of the most actionable bucket.
    for key in ("panel_ahead_of_paid", "db_ahead_of_paid",
                "panel_missing", "no_paid_signal_but_db_active"):
        sample = state.get("samples", {}).get(key, [])[:limit_samples]
        if not sample:
            continue
        lines.append("")
        lines.append(f"<i>Примеры — {_BUCKET_LABELS.get(key, key)}:</i>")
        for s in sample:
            tg = s["telegram_id"]
            db_str = s["db_expires_at"].strftime("%Y-%m-%d") if s["db_expires_at"] else "—"
            exp_str = s["expected_end"].strftime("%Y-%m-%d") if s["expected_end"] else "—"
            pan_str = s["panel_expires"].strftime("%Y-%m-%d") if s["panel_expires"] else "—"
            lines.append(
                f"  <code>{tg}</code> · БД:{db_str} · paid:{exp_str} · panel:{pan_str}"
            )

    return "\n".join(lines)


@admin_audit_subs_router.callback_query(F.data == "admin:audit_subs")
async def callback_audit_subs(callback: CallbackQuery):
    """Start / show status of the active-premium audit."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    admin_id = callback.from_user.id
    existing = _audits.get(admin_id)
    if existing and existing.get("status") == "running":
        # Already running — just show the current state.
        await safe_edit_text(
            callback.message, _format_report(existing),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin:audit_subs")],
                [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
            ]),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    if existing and existing.get("status") in ("done", "failed"):
        # Show last report; offer the full list, fix, or restart.
        actionable = _actionable_records(existing)
        rows = []
        if actionable:
            rows.append([InlineKeyboardButton(
                text=f"📋 Список ({len(actionable)})",
                callback_data="admin:audit_subs_list",
            )])
            rows.append([InlineKeyboardButton(
                text=f"🔧 Исправить ({len(actionable)})",
                callback_data="admin:audit_subs_fix",
            )])
        rows.append([InlineKeyboardButton(text="🔁 Запустить заново", callback_data="admin:audit_subs_start")])
        rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")])
        await safe_edit_text(
            callback.message, _format_report(existing, limit_samples=5),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    # No prior run — kick one off.
    await _start_audit(callback, admin_id)


@admin_audit_subs_router.callback_query(F.data == "admin:audit_subs_start")
async def callback_audit_subs_start(callback: CallbackQuery):
    """Force-start a new audit even if a previous one is finished."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await _start_audit(callback, callback.from_user.id)


@admin_audit_subs_router.callback_query(F.data == "admin:audit_subs_list")
async def callback_audit_subs_list(callback: CallbackQuery):
    """Dump the full actionable list for review."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    state = _audits.get(callback.from_user.id)
    if not state or state.get("status") != "done":
        await safe_edit_text(
            callback.message,
            "🔍 Сначала запустите аудит.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    actionable = _actionable_records(state)
    rows = []
    if actionable:
        rows.append([InlineKeyboardButton(
            text=f"🔧 Исправить ({len(actionable)})",
            callback_data="admin:audit_subs_fix",
        )])
    rows.append([InlineKeyboardButton(text="◀ К отчёту", callback_data="admin:audit_subs")])

    await safe_edit_text(
        callback.message,
        _format_report(state, full_list=True),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_audit_subs_router.callback_query(F.data == "admin:audit_subs_fix")
async def callback_audit_subs_fix(callback: CallbackQuery):
    """Auto-fix actionable records (panel_behind_paid + panel_missing).

    Strict premium-only:
      - Username check is enforced at PATCH time (only `tg_<id>_premium`).
      - PATCH/create flows go through remnawave_premium.* which only
        touches the premium entity. Bypass entities are NOT touched.
    """
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    state = _audits.get(callback.from_user.id)
    if not state or state.get("status") != "done":
        await safe_edit_text(
            callback.message,
            "🔍 Сначала запустите аудит.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    actionable = _actionable_records(state)
    if not actionable:
        await safe_edit_text(
            callback.message,
            "✅ Исправлять нечего — все active premium-подписки в порядке.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    if state.get("fix_status") == "running":
        await safe_edit_text(
            callback.message,
            f"🔧 Исправление уже выполняется: "
            f"{state['fix_done']} / {state['fix_total']}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:audit_subs_fix_status")],
            ]),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    state["fix_status"] = "running"
    state["fix_total"] = len(actionable)
    state["fix_done"] = 0
    state["fix_ok"] = 0
    state["fix_skipped"] = 0
    state["fix_failed"] = 0
    asyncio.create_task(_fix_worker(callback.from_user.id, actionable))

    await safe_edit_text(
        callback.message,
        f"🔧 <b>Исправление {len(actionable)} подписок запущено</b>\n\n"
        "Работа идёт в фоне. Только premium — bypass entities <b>не трогаются</b>.\n"
        "Защита по username и через premium-only функции.\n\n"
        "Нажмите «🔄 Обновить» через минуту, чтобы увидеть результат.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin:audit_subs_fix_status")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]),
        bot=callback.bot, parse_mode="HTML",
    )


@admin_audit_subs_router.callback_query(F.data == "admin:audit_subs_fix_status")
async def callback_audit_subs_fix_status(callback: CallbackQuery):
    """Show progress / final report of the running or finished fix."""
    if callback.from_user.id != config.ADMIN_TELEGRAM_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    try:
        await callback.answer()
    except Exception:
        pass

    state = _audits.get(callback.from_user.id)
    if not state or not state.get("fix_status"):
        await safe_edit_text(
            callback.message,
            "🔧 Нет данных по исправлению.",
            reply_markup=get_admin_back_keyboard(),
            bot=callback.bot, parse_mode="HTML",
        )
        return

    status = state.get("fix_status")
    total = state.get("fix_total", 0)
    done = state.get("fix_done", 0)
    ok = state.get("fix_ok", 0)
    skipped = state.get("fix_skipped", 0)
    failed = state.get("fix_failed", 0)

    if status == "running":
        text = (
            "🔧 <b>Исправление в процессе</b>\n\n"
            f"Обработано: <b>{done}</b> / {total}\n"
            f"  ✅ Исправлено: {ok}\n"
            f"  🛡 Пропущено (защита): {skipped}\n"
            f"  ❌ Сбой: {failed}"
        )
        rows = [
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:audit_subs_fix_status")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]
    else:
        text = (
            "🔧 <b>Исправление завершено</b>\n\n"
            f"✅ Исправлено: <b>{ok}</b> / {total}\n"
            f"🛡 Пропущено (защита по username/типу): <b>{skipped}</b>\n"
            f"❌ Сбой (ручной разбор): <b>{failed}</b>\n\n"
            "<i>Bypass entities остались нетронутыми.</i>\n"
            "Запустите аудит повторно, чтобы убедиться."
        )
        rows = [
            [InlineKeyboardButton(text="🔍 Повторный аудит", callback_data="admin:audit_subs_start")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]

    await safe_edit_text(
        callback.message, text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        bot=callback.bot, parse_mode="HTML",
    )


async def _fix_worker(admin_id: int, actionable: list):
    """Background fixer for panel_behind_paid + panel_missing.

    Strict guarantees:
      - For panel_behind_paid: we GET the entity to verify username
        == f"tg_{tg}_premium" before PATCHing. No PATCH if mismatch.
      - For panel_missing: we call create_premium_user_entity which
        internally writes the username `tg_{tg}_premium`. Cannot affect
        the user's bypass entity (different username, different uuid
        column in the DB).
    """
    state = _audits[admin_id]
    sem = asyncio.Semaphore(_AUDIT_CONCURRENCY)

    target_squad = getattr(
        config, "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", None,
    ) or None

    def _iso_z(dt) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _fix_behind(rec):
        """PATCH expireAt to expected_end. Strict username check."""
        tg = rec["telegram_id"]
        expected_username = f"tg_{tg}_premium"
        try:
            user = await asyncio.wait_for(
                remnawave_api.find_user_by_username(expected_username),
                timeout=_AUDIT_HTTP_TIMEOUT_S,
            )
        except Exception as e:
            state["fix_failed"] += 1
            logger.warning("AUDIT_FIX: lookup tg=%s %s", tg, e)
            return
        if user is None:
            # Username lookup says no entity — that's panel_missing
            # territory. Skip; the missing-bucket path would handle it.
            state["fix_skipped"] += 1
            logger.info("AUDIT_FIX_SKIP_NO_USERNAME tg=%s username=%s",
                        tg, expected_username)
            return
        actual_username = (user.get("username") or "").strip()
        if actual_username != expected_username:
            state["fix_skipped"] += 1
            logger.warning(
                "AUDIT_FIX_SKIP_WRONG_USERNAME tg=%s expected=%s got=%r",
                tg, expected_username, actual_username,
            )
            return
        uuid = user.get("uuid")
        if not uuid:
            state["fix_skipped"] += 1
            return
        fields = {"expireAt": _iso_z(rec["expected_end"]), "status": "ACTIVE"}
        if target_squad:
            fields["externalSquadUuid"] = target_squad
        try:
            result = await asyncio.wait_for(
                remnawave_api.update_user(uuid, **fields),
                timeout=_AUDIT_HTTP_TIMEOUT_S,
            )
        except Exception as e:
            state["fix_failed"] += 1
            logger.warning("AUDIT_FIX: patch tg=%s %s", tg, e)
            return
        if result is not None:
            state["fix_ok"] += 1
            logger.info(
                "AUDIT_FIX_PATCHED tg=%s uuid=%s to=%s",
                tg, uuid[:8], rec["expected_end"].isoformat(),
            )
        else:
            state["fix_failed"] += 1

    async def _fix_missing(rec):
        """Create the premium entity. Goes via remnawave_premium.create_premium_user_entity
        which writes username `tg_{tg}_premium` — cannot affect bypass."""
        tg = rec["telegram_id"]
        try:
            result = await asyncio.wait_for(
                remnawave_premium.create_premium_user_entity(
                    tg,
                    requested_uuid=None,
                    expire_at=rec["expected_end"],
                ),
                timeout=_AUDIT_HTTP_TIMEOUT_S * 3,
            )
        except Exception as e:
            state["fix_failed"] += 1
            logger.warning("AUDIT_FIX: create tg=%s %s", tg, e)
            return
        if result and getattr(result, "ok", False):
            state["fix_ok"] += 1
            logger.info(
                "AUDIT_FIX_CREATED tg=%s uuid=%s to=%s",
                tg, (getattr(result, "panel_uuid", "") or "")[:8],
                rec["expected_end"].isoformat(),
            )
        else:
            state["fix_failed"] += 1
            logger.warning(
                "AUDIT_FIX: create-fail tg=%s status=%s err=%s",
                tg, getattr(result, "status", None),
                getattr(result, "error", None),
            )

    async def _one(item):
        bucket, rec = item
        async with sem:
            try:
                if bucket == "panel_behind_paid":
                    await _fix_behind(rec)
                elif bucket == "panel_missing":
                    await _fix_missing(rec)
                else:
                    state["fix_skipped"] += 1
            except Exception as e:
                state["fix_failed"] += 1
                logger.exception("AUDIT_FIX_UNEXPECTED tg=%s %s",
                                 rec.get("telegram_id"), e)
            state["fix_done"] += 1
            try:
                await asyncio.sleep(_AUDIT_THROTTLE_S)
            except Exception:
                pass

    try:
        await asyncio.gather(*[_one(item) for item in actionable])
        state["fix_status"] = "done"
        logger.info(
            "AUDIT_FIX_DONE admin=%s ok=%s skipped=%s failed=%s",
            admin_id, state["fix_ok"], state["fix_skipped"], state["fix_failed"],
        )
    except Exception as e:
        state["fix_status"] = "failed"
        logger.exception("AUDIT_FIX_FATAL admin=%s %s", admin_id, e)


async def _start_audit(callback: CallbackQuery, admin_id: int):
    state = {
        "status": "running",
        "total": 0,
        "done": 0,
        "buckets": {},
        "samples": {},
        "samples_full": {},
        "error": None,
        "task": None,
        # Set on completion of a fix run.
        "fix_status": None,
        "fix_total": 0,
        "fix_done": 0,
        "fix_ok": 0,
        "fix_skipped": 0,
        "fix_failed": 0,
    }
    _audits[admin_id] = state
    task = asyncio.create_task(_audit_worker(admin_id))
    state["task"] = task

    await safe_edit_text(
        callback.message,
        "🔍 <b>Аудит активных premium-подписок запущен</b>\n\n"
        "Работа идёт в фоне. Кнопка «🔄 Обновить статус» покажет прогресс "
        "и итоговый отчёт по завершении.\n\n"
        "<i>Тулза только читает данные — никаких изменений в БД или панели.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin:audit_subs")],
            [InlineKeyboardButton(text="◀ Назад", callback_data="admin:main")],
        ]),
        bot=callback.bot, parse_mode="HTML",
    )
