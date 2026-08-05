"""Сверка: единственное место, где «Исправить» что-то меняет.

ЧТО ЗДЕСЬ
    `apply_reconciliation_fix` — пересчитывает срок по платежам,
    подрезает expireAt в панели Remnawave и пишет строку в журнал сверки.

ПОЧЕМУ ВЫДЕЛЕНО
    Всё остальное в сверке только читает. Эта функция — записывающая, и
    цена ошибки здесь другая: она отнимает у людей оплаченный доступ.

ЧТО ЛЕГКО СЛОМАТЬ
    Порядок действий. Журнал пишется ТОЛЬКО после удавшегося патча
    панели: запись «до» уже приводила к тому, что при недоступной панели
    в журнале лежало исправление, которого не произошло.

    bot-DB `subscriptions.expires_at` здесь не трогается намеренно — это
    забота grant_access / auto_renewal и часть bypass-only-дизайна.

    Сброс кэша кандидатов после успешного патча: без него админ жмёт
    «Исправить», обновляет экран и до 10 минут видит того же человека в
    списке — выглядит как «кнопка не сработала».
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database.core import get_pool, _to_db_utc, _from_db_utc
from database.reconciliation_expiry import (
    _extract_period_days_from_tariff,
    _simulate_expiry_from_payments,
    clamp_recomputed_expiry,
)
from database.reconciliation_panel import invalidate_panel_scan_cache

logger = logging.getLogger(__name__)


async def apply_reconciliation_fix(
    telegram_id: int,
    admin_telegram_id: int,
    *,
    reason: str = "manual reconciliation via dashboard",
) -> Dict[str, Any]:
    """Recompute expires_at from approved payments + admin_grant_days, apply
    the correction in a single transaction, and log the before/after.

    Returns a dict describing the outcome — see below.
    """
    pool = await get_pool()
    if pool is None:
        return {"success": False, "error": "db_unavailable"}

    now = datetime.now(timezone.utc)

    # Правим ТОЛЬКО Remnawave premium entity (`tg_{telegram_id}_premium`).
    # Bot-DB `subscriptions.expires_at` НЕ трогаем: это лежит на совести
    # штатного grant_access / auto_renewal и может быть частью
    # bypass-only-дизайна. Наша задача — прикрыть реальный VPN-доступ,
    # который в 100% случаев управляется expireAt в Remnawave.
    panel_updated = False
    is_bypass_only = False

    async with pool.acquire() as conn:
        async with conn.transaction():
            sub_row = await conn.fetchrow(
                """SELECT expires_at, activated_at, admin_grant_days,
                          COALESCE(is_bypass_only, FALSE) AS is_bypass_only
                   FROM subscriptions
                   WHERE telegram_id = $1
                   FOR UPDATE""",
                telegram_id,
            )
            if not sub_row:
                # Orphan panel entity (no bot-DB row) — we still want to
                # neutralise the premium entity in Remnawave. Fabricate a
                # minimal "empty" state so the calculation clamps to NOW+1d.
                is_bypass_only = False
                old_expires_at = None
                activated_at = None
                admin_grant_days = 0
                payment_rows = []
            else:
                old_expires_at = _from_db_utc(sub_row["expires_at"])
                activated_at = (
                    _from_db_utc(sub_row["activated_at"])
                    if sub_row["activated_at"] else None
                )
                admin_grant_days = sub_row["admin_grant_days"] or 0
                is_bypass_only = bool(sub_row["is_bypass_only"])

            if sub_row:
                payment_rows = await conn.fetch(
                    """SELECT id, tariff, COALESCE(paid_at, created_at) AS effective_at
                       FROM payments
                       WHERE telegram_id = $1
                         AND status = 'approved'
                       ORDER BY COALESCE(paid_at, created_at) ASC""",
                    telegram_id,
                )

            proof_ids: List[int] = []
            counted_for_sim: List[Dict[str, Any]] = []
            total_paid_days = 0
            for p in payment_rows:
                period_days = _extract_period_days_from_tariff((p["tariff"] or "").strip())
                if not period_days:
                    continue
                proof_ids.append(p["id"])
                total_paid_days += period_days
                eff = _from_db_utc(p["effective_at"]) if p["effective_at"] else None
                if eff:
                    counted_for_sim.append({
                        "effective_at": eff,
                        "period_days": period_days,
                    })
            counted_for_sim.sort(key=lambda x: x["effective_at"])

            # Симулируем стандартный renewal (см. _simulate_expiry_from_payments).
            # Ровно то, что делает бот в grant_access: платёж без дырки
            # продлевает окно, с дыркой — стартует новое от paid_at.
            # admin_grant_days ложится поверх итога.
            new_expires_at = _simulate_expiry_from_payments(
                counted_for_sim, int(admin_grant_days or 0),
            )

            # Приводим пересчёт к безопасному значению — правила и причины
            # см. в докстринге clamp_recomputed_expiry.
            new_expires_at, fallback_applied = clamp_recomputed_expiry(
                new_expires_at, old_expires_at, now,
            )

            days_removed = (
                (old_expires_at - new_expires_at).days
                if old_expires_at else 0
            )

            # bot-DB expires_at здесь НЕ обновляем — это забота штатного
            # grant_access / auto_renewal. Наш «Исправить» подрезает
            # только реальный VPN-доступ через panel (см. блок ниже,
            # после DB-транзакции).

            log_reason = reason
            if fallback_applied == "past_date":
                log_reason += (
                    " [fallback: past-date computed, clamped to NOW+1d]"
                )
            elif fallback_applied == "kept_current_would_extend":
                log_reason += (
                    " [fallback: пересчёт длиннее текущего — оставлен текущий срок, "
                    "продление реконсиляцией не выполняется]"
                )
            elif fallback_applied == "no_payments":
                log_reason += (
                    " [fallback: no counted payments and no admin_grant, "
                    "clamped to NOW+1d]"
                )
            log_reason += " [panel-only fix — bot-DB untouched by design]"

    # ── Remnawave panel: PATCH expireAt on the premium entity ────────
    #
    # ЗДЕСЬ ЕДИНСТВЕННОЕ РЕАЛЬНОЕ ДЕЙСТВИЕ ЭТОЙ ФУНКЦИИ.
    #
    # Bot-DB expires_at мы намеренно не трогаем: это забота штатного
    # grant_access / auto_renewal и часть bypass-only-дизайна. «Исправить»
    # подрезает ровно одно — expireAt в панели, которым и управляется
    # настоящий доступ.
    #
    # Ретрай встроен в remnawave_premium.renew_premium_user (3 попытки).
    panel_error: Optional[str] = None
    try:
        from app.services import remnawave_premium
        panel_updated = await remnawave_premium.renew_premium_user(
            telegram_id, new_expires_at,
        )
        if not panel_updated:
            panel_error = "renew_premium_user returned False"
            # Тихий отказ панели: исключения нет, поэтому ветка ниже с
            # logger.exception не срабатывает, а причина уезжала только в
            # JSON-ответ. В логах единственным следом оставалась строка
            # RECONCILIATION_FIX_APPLIED — то есть отказ выглядел успехом.
            logger.error(
                "RECONCILIATION_PANEL_UPDATE_REJECTED user=%s new=%s — панель "
                "вернула False, срок доступа НЕ подрезан",
                telegram_id, new_expires_at.isoformat(),
            )
        else:
            # Панель изменилась — кэшированный список кандидатов протух.
            # Иначе после «Исправить» человек ещё до 10 минут висит в списке.
            invalidate_panel_scan_cache()
    except Exception as e:
        panel_error = f"{type(e).__name__}: {e}"
        logger.exception(
            "RECONCILIATION_PANEL_UPDATE_FAIL user=%s: %s", telegram_id, e,
        )

    # ── Запись в журнал сверки — только после удавшегося патча ────────
    #
    # Раньше INSERT шёл внутри DB-транзакции ВЫШЕ, то есть коммитился до
    # обращения к панели. При недоступной панели админ видел ошибку в
    # интерфейсе, а в журнале уже лежала строка old→new с доказательными
    # payment_ids. Повторные попытки плодили дубли, а последующий аудит по
    # журналу считал этих людей исправленными — хотя доступ у них так и
    # остался с прежним сроком.
    #
    # Комментарий на этом месте утверждал, что «bot-DB хотя бы подрезан»,
    # но bot-DB здесь не трогается вовсе, и записывать было нечего.
    log_id = None
    if panel_updated:
        try:
            async with pool.acquire() as log_conn:
                log_id = await log_conn.fetchval(
                    """INSERT INTO subscription_reconciliation_log (
                           telegram_id, old_expires_at, new_expires_at,
                           old_days_from_now, new_days_from_now, days_removed,
                           reason, proof_payment_ids, total_paid_days,
                           admin_grant_days_kept, admin_telegram_id
                       )
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                       RETURNING id""",
                    telegram_id,
                    _to_db_utc(old_expires_at) if old_expires_at else _to_db_utc(now),
                    _to_db_utc(new_expires_at),
                    (old_expires_at - now).days if old_expires_at else 0,
                    (new_expires_at - now).days,
                    days_removed,
                    log_reason,
                    proof_ids,
                    total_paid_days,
                    int(admin_grant_days or 0),
                    admin_telegram_id,
                )
        except Exception as e:
            # Панель уже исправлена — сообщать об ошибке всей операции
            # нельзя, иначе админ повторит фикс. Но и молчать нельзя:
            # исправление осталось без следа в журнале.
            logger.error(
                "RECONCILIATION_LOG_WRITE_FAIL user=%s new=%s: %s — "
                "панель исправлена, записи в журнале нет",
                telegram_id, new_expires_at.isoformat(), e,
            )

    # Тег и уровень записи следуют из того, изменилась ли панель.
    #
    # Раньше строка была одна и писалась безусловно: при упавшей панели в лог
    # уходило «RECONCILIATION_FIX_APPLIED ... removed_days=3200
    # panel_updated=False» — то есть слово «исправлено» с полным набором
    # доказательств для человека, у которого не изменилось ничего. Подсчёт
    # «сколько исправили» по тегу давал завышенное число, а уровень info не
    # выделял провал. days_removed при этом — тоже намерение: он посчитан
    # внутри транзакции, ДО единственного реального действия (патча панели).
    #
    # admin_telegram_id добавлен сюда потому, что DB-строка журнала пишется
    # только при panel_updated: в сценарии отказа лог остаётся единственным
    # источником, и без него не видно, кто именно инициировал подрезку.
    _fix_log = logger.info if panel_updated else logger.error
    _fix_tag = "RECONCILIATION_FIX_APPLIED" if panel_updated else "RECONCILIATION_FIX_FAILED"
    _fix_log(
        "%s user=%s admin=%s old=%s new=%s removed_days=%s "
        "total_paid_days=%s admin_grant_days=%s proof_ids=%s log_id=%s "
        "panel_updated=%s fallback=%s panel_error=%s",
        _fix_tag,
        telegram_id,
        admin_telegram_id,
        old_expires_at.isoformat() if old_expires_at else None,
        new_expires_at.isoformat(),
        days_removed,
        total_paid_days,
        admin_grant_days,
        proof_ids,
        log_id,
        panel_updated,
        fallback_applied,
        panel_error,
    )

    # Успех фикса = панель обновилась. Если панель упала — success=False:
    # реальный VPN-доступ у юзера остаётся с 10-летним expireAt, ничего
    # мы не «поправили». UI покажет ошибку и админ повторит.
    return {
        "success": panel_updated,
        "log_id": log_id,
        "old_expires_at": old_expires_at.isoformat() if old_expires_at else None,
        "new_expires_at": new_expires_at.isoformat(),
        "days_removed": days_removed,
        "total_paid_days": total_paid_days,
        "admin_grant_days_kept": int(admin_grant_days or 0),
        "proof_payment_ids": proof_ids,
        "fallback_applied": fallback_applied,
        "panel_updated": panel_updated,
        "panel_error": panel_error,
        "is_bypass_only": is_bypass_only,
    }

