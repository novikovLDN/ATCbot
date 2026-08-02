"""Тесты клампа пересчитанной даты в реконсиляции.

Что проверяем: `database.reconciliation.clamp_recomputed_expiry` — чистая
функция, через которую проходит КАЖДОЕ значение expires_at перед записью в
Remnawave из админского «Исправить».

Почему это важно отдельным тестом: раньше ветка «пересчёт длиннее текущей
даты» ставила NOW+1 день, и оплаченный год у пользователя превращался в
сутки просто потому, что симуляция по платежам разошлась с subscriptions.
expires_at в большую сторону. Реконсиляция обязана уметь только отнимать
лишнее — она не выдаёт доступ и не отнимает оплаченный.
"""
from datetime import datetime, timedelta, timezone

from database.reconciliation import clamp_recomputed_expiry

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def test_no_payments_clamps_to_now_plus_one_day():
    """Ни платежей, ни admin_grant — сутки от сейчас, чтобы expiry-cleanup
    штатно погасил юзера, а панель не получила отрицательный expireAt."""
    result, fallback = clamp_recomputed_expiry(None, NOW + timedelta(days=30), NOW)
    assert result == NOW + timedelta(days=1)
    assert fallback == "no_payments"


def test_past_date_clamps_to_now_plus_one_day():
    """Пересчёт дал прошлое — тот же безопасный NOW+1д."""
    result, fallback = clamp_recomputed_expiry(
        NOW - timedelta(days=5), NOW + timedelta(days=30), NOW,
    )
    assert result == NOW + timedelta(days=1)
    assert fallback == "past_date"


def test_would_extend_keeps_current_date_and_never_cuts_to_one_day():
    """Главный регресс-тест: пересчёт ДЛИННЕЕ текущего срока не должен ни
    продлевать доступ, ни резать его до суток — дата остаётся как есть."""
    old = NOW + timedelta(days=10)
    result, fallback = clamp_recomputed_expiry(NOW + timedelta(days=360), old, NOW)
    assert result == old
    assert fallback == "kept_current_would_extend"
    assert result != NOW + timedelta(days=1), "оплаченный доступ срезан до суток"


def test_shorter_recomputed_date_is_applied_as_is():
    """Пересчёт короче текущего — это и есть нормальная работа сверки:
    лишние дни снимаются, fallback не нужен."""
    recomputed = NOW + timedelta(days=30)
    result, fallback = clamp_recomputed_expiry(
        recomputed, NOW + timedelta(days=3650), NOW,
    )
    assert result == recomputed
    assert fallback is None


def test_no_old_date_means_recomputed_wins():
    """Строки в subscriptions нет (orphan-сущность в панели) — сравнивать
    не с чем, берём пересчёт."""
    recomputed = NOW + timedelta(days=30)
    result, fallback = clamp_recomputed_expiry(recomputed, None, NOW)
    assert result == recomputed
    assert fallback is None
