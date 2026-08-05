"""Сверка: арифметика сроков. Ни базы, ни панели — только вычисления.

ЧТО ЗДЕСЬ
    • `_extract_period_days_from_tariff` — сколько дней даёт платёж;
    • `_simulate_expiry_from_payments` — каким срок ДОЛЖЕН быть по истории
      платежей (повторяет штатную логику grant_access);
    • `clamp_recomputed_expiry` — приведение пересчёта к безопасному
      значению перед записью в панель;
    • `_from_db_utc_str` — разбор ISO-строки обратно в дату.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственная часть сверки, которую можно проверить тестом без
    моков базы и панели, — и именно она решает, сколько дней у человека
    отнимут. Ошибка здесь не видна ни в логах, ни в интерфейсе: цифра
    просто получится другой.

ЧТО ЛЕГКО СЛОМАТЬ
    `_simulate_expiry_from_payments` и `clamp_recomputed_expiry` зовут ДВА
    места — детальный экран и «Исправить». Экран показывает админу одно
    число, кнопка применяет другое; если поправить расчёт только для
    одного вызывающего, разойдутся показанное и сделанное.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _extract_period_days_from_tariff(tariff: str) -> Optional[int]:
    """Parse `basic_30`, `plus_365`, `combo_basic_180` etc. into period days.

    Returns None for anything that isn't a subscription-time payment (traffic
    packs, gifts, topups, bypass GB packs).
    """
    if not tariff or tariff == "balance_topup":
        return None
    if tariff.startswith(("gift_", "traffic_", "bypass_", "farm_", "apple_", "steam_")):
        return None
    parts = tariff.split("_")
    if not parts:
        return None
    # combo_basic_180 → last part; basic_30 → last part; plus_365 → last part.
    try:
        days = int(parts[-1])
    except ValueError:
        return None
    # Sanity: subscription periods are 30/90/180/365 in prod. Anything above
    # 730 days from a single payment is almost certainly a parse artefact.
    if 1 <= days <= 730:
        return days
    return None


def _from_db_utc_str(iso: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string (as saved by proof_payments) back into a
    timezone-aware datetime. Small helper used only by get_reconciliation_detail
    for computing base_start from the earliest counted payment."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _simulate_expiry_from_payments(
    counted_payments: List[Dict[str, Any]],
    admin_grant_days: int,
) -> Optional[datetime]:
    """Compute the "correct" expires_at by simulating the standard bot renewal
    logic over the user's payment history.

    Each counted payment either:
      • starts a fresh subscription window (if there is no prior window OR
        the previous window has already ended by the time this payment was
        made — gap in subscription);
      • extends the current window (if paid while still-active — like the
        standard "renewal" branch in grant_access).

    Admin grant days (subscriptions.admin_grant_days) are added on TOP of
    the resulting end. Mirrors how admins actually use the grant flow:
    they hand out extra days after the standard payment history.

    Example (matches product spec):
      payments=[(01.07.2026, 30)], admin=0 → 31.07.2026
      payments=[(01.06, 30), (25.06, 30)], admin=0 → 31.07 (extend)
      payments=[(01.01.2020, 30), (01.07.2026, 30)], admin=0 → 31.07.2026
        — 6-year gap → last payment starts fresh.

    Args:
        counted_payments: sorted ascending by effective_at, each with keys
            `effective_at: datetime` and `period_days: int`.
        admin_grant_days: total admin_grant_days from subscriptions row.

    Returns None if there are neither payments nor admin grants — caller
    should treat as "no legit subscription time exists → clamp to NOW+1d".
    """
    current_end: Optional[datetime] = None
    for p in counted_payments:
        paid_at = p["effective_at"]
        period = int(p["period_days"] or 0)
        if paid_at is None or period <= 0:
            continue
        if current_end is None or paid_at > current_end:
            # Gap or first payment: start fresh from this payment.
            current_end = paid_at + timedelta(days=period)
        else:
            # Renewal — extend current window.
            current_end += timedelta(days=period)

    if admin_grant_days and admin_grant_days > 0:
        base = current_end or datetime.now(timezone.utc)
        current_end = base + timedelta(days=admin_grant_days)

    return current_end


# ──────────────────────────────────────────────────────────────────────
#  3. Fix — apply reconciliation
# ──────────────────────────────────────────────────────────────────────

def clamp_recomputed_expiry(
    recomputed: Optional[datetime],
    old_expires_at: Optional[datetime],
    now: datetime,
) -> tuple[datetime, Optional[str]]:
    """Привести пересчитанную дату окончания к безопасному значению.

    Зачем: пересчёт по платежам (`_simulate_expiry_from_payments`) может дать
    результат, который нельзя писать в панель как есть. Здесь три ситуации,
    и во всех реконсиляция обязана только УРЕЗАТЬ лишнее, но никогда не
    выдавать доступ, которого пользователь не оплачивал, и никогда не резать
    оплаченный доступ «за компанию».

    Правила:
      • пересчёт пустой (ни платежей, ни admin_grant) → NOW + 1 день
        (`no_payments`). Сутки, а не «прямо сейчас», чтобы Remnawave не
        споткнулся об отрицательный expireAt, а штатный expiry-cleanup
        через ~24 часа сам перевёл юзера в expired;
      • пересчёт дал прошлое → NOW + 1 день (`past_date`), причина та же;
      • пересчёт ДЛИННЕЕ текущей даты → оставляем текущую дату
        (`kept_current_would_extend`). Раньше здесь тоже ставилось NOW+1д,
        и оплаченный год превращался в сутки просто потому, что расчёт
        разошёлся с базой в большую сторону. Продлевать реконсиляция права
        не имеет, но и отнимать оплаченное — тем более.

    Возвращает `(итоговая_дата, метка_fallback | None)`. Метка уходит в
    `subscription_reconciliation_log.reason` и в ответ дашборда.
    """
    min_new = now + timedelta(days=1)
    if recomputed is None:
        return min_new, "no_payments"
    if recomputed < now:
        return min_new, "past_date"
    if old_expires_at and recomputed > old_expires_at:
        return old_expires_at, "kept_current_would_extend"
    return recomputed, None

