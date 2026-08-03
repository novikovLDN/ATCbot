"""Сводка get_extended_bot_stats обязана называть вещи своими именами.

Дефекты. Дашборд читает отсюда только total_users и
active_subscriptions; всё остальное рисуют два экрана бота
(/admin → Статистика и /admin → Расширенная статистика). Поэтому
«удалить неиспользуемое» не вариант — экраны обращаются к ключам по
индексу и упали бы целиком. Чинили семантику:

1. new_today считался от полуночи UTC, хотя весь остальной дашборд режет
   сутки по Europe/Moscow. Три часа регистраций — с 00:00 до 03:00 МСК —
   у этой цифры уезжали во вчера.
2. mrr никогда не был MRR: это сумма оплат за 30 дней вместе с разовыми
   покупками мини-магазина.
3. total_revenue и mrr отдавались в копейках, а бот печатал их с «₽» —
   на экране висела цифра в сто раз больше настоящей.
4. churn_rate — не отток, а доля юзеров с просроченной подпиской за всё
   время (замечание аудита «считается по строкам, а не по юзерам» не
   подтвердилось: subscriptions.telegram_id UNIQUE, строка = юзер).
5. avg_subs_per_user из-за того же UNIQUE был тождественно равен 1.0.
"""
import inspect
import re

import pytest

import database.analytics as analytics_mod


class _FakeConn:
    """Отдаёт заранее подготовленные ответы, подбирая их по фрагменту SQL."""

    def __init__(self, answers):
        self.sql: list[str] = []
        self._answers = answers

    async def fetchval(self, query, *args):
        self.sql.append(query)
        for needle, value in self._answers.items():
            if needle in " ".join(query.split()):
                return value
        return 0

    async def fetch(self, query, *args):
        self.sql.append(query)
        return []

    async def fetchrow(self, query, *args):
        self.sql.append(query)
        return None


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


# Порядок значим: совпадение ищется по первому подходящему фрагменту,
# поэтому более узкие условия стоят выше более общих.
ANSWERS = {
    "trial_used_at IS NOT NULL": 40,
    "COUNT(*) FROM users WHERE created_at": 7,           # new_today
    "COUNT(*) FROM users": 100,                          # total_users
    "expires_at > $1": 30,
    "expires_at <= $1": 10,
    "COUNT(DISTINCT telegram_id) FROM subscriptions": 25,
    "AND created_at >= $1": 1_234_500,                   # выручка за 30 дней
    "FROM pending_purchases WHERE status = 'paid'": 9_876_500,
    "FROM broadcasts": 5,
    "FROM subscription_history": 2.4,
}


@pytest.fixture()
def conn(monkeypatch):
    c = _FakeConn(ANSWERS)
    pool = _FakePool(c)

    async def _get_pool():
        return pool

    monkeypatch.setattr(analytics_mod, "get_pool", _get_pool)
    return c


@pytest.mark.asyncio
async def test_new_today_cuts_the_day_in_moscow(conn):
    """Сутки — как у get_daily_timeseries и тайла «Сегодня»."""
    await analytics_mod.get_extended_bot_stats()
    q = next(s for s in conn.sql if "FROM users WHERE created_at" in " ".join(s.split()))
    assert "Europe/Moscow" in q
    assert "DATE_TRUNC('day'" in q


def test_new_today_no_longer_uses_python_midnight():
    """Полночь по UTC собиралась в Python'е через now.replace(hour=0…) —
    именно она и сдвигала сутки на три часа."""
    src = inspect.getsource(analytics_mod.get_extended_bot_stats)
    assert "replace(hour=0" not in src


@pytest.mark.asyncio
async def test_money_is_reported_in_rubles(conn):
    """В базе копейки, а потребитель дописывает «₽»: отдавать надо рубли."""
    out = await analytics_mod.get_extended_bot_stats()
    assert out["total_revenue_rubles"] == 98_765.0
    assert out["revenue_last_30d_rubles"] == 12_345.0
    # Старые имена — те же значения, тоже в рублях.
    assert out["total_revenue"] == out["total_revenue_rubles"]
    assert out["mrr"] == out["revenue_last_30d_rubles"]


@pytest.mark.asyncio
async def test_thirty_day_revenue_is_not_called_mrr_anymore(conn):
    """Имя обязано говорить, что это выручка за окно, а не MRR."""
    out = await analytics_mod.get_extended_bot_stats()
    assert "revenue_last_30d_rubles" in out


@pytest.mark.asyncio
async def test_expired_share_has_an_honest_name(conn):
    """Это не отток: доля за всё время, без привязки к периоду."""
    out = await analytics_mod.get_extended_bot_stats()
    assert out["expired_subscription_share_percent"] == 25.0
    assert out["churn_rate"] == out["expired_subscription_share_percent"]


@pytest.mark.asyncio
async def test_periods_per_user_is_not_a_constant(conn):
    """AVG по subscriptions был тождественно 1.0 (telegram_id UNIQUE).
    Считаем по subscription_history, где на юзера правда бывает больше
    одного оплаченного периода."""
    out = await analytics_mod.get_extended_bot_stats()
    assert out["avg_subscription_periods_per_user"] == 2.4
    assert out["avg_subs_per_user"] == 2.4

    q = next(s for s in conn.sql if "subscription_history" in s)
    assert "action_type IN ('purchase', 'renewal')" in q, (
        "перевыпуск ключа — не новая подписка"
    )


@pytest.mark.asyncio
async def test_keys_the_bot_screens_index_are_all_present(conn):
    """Экраны бота читают эти ключи по индексу: пропажа любого = KeyError
    и «Ошибка при получении расширенной статистики» вместо экрана."""
    out = await analytics_mod.get_extended_bot_stats()
    for key in (
        "total_users", "new_today", "total_trial", "trial_rate",
        "active_subs", "expired_subs", "conversion_rate",
        "total_broadcasts", "churn_rate", "avg_subs_per_user",
        "total_revenue", "mrr",
    ):
        assert key in out, key


def test_bot_screens_use_the_honest_names():
    """Подписи «Отток» и «MRR» на экранах бота были неверны — их убрали
    вместе с переходом на честные ключи."""
    from pathlib import Path

    src = Path("app/handlers/admin/stats.py").read_text(encoding="utf-8")
    body = re.sub(r"#.*", "", src)  # комментарии объясняют дефект, их не смотрим
    assert "Отток" not in body
    assert "MRR" not in body
    assert "expired_subscription_share_percent" in body
    assert "revenue_last_30d_rubles" in body
