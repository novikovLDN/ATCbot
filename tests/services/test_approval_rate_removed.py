"""«Процент подтверждённых платежей» убран, а не оставлен вечной сотней.

Дефект. approval_rate_percent считался как COUNT(status='approved') /
COUNT(*) по payments и всегда давал 100%. Не потому что у нас идеальные
платежи, а потому что строка в payments по построению не может остаться
неподтверждённой: пять из шести мест INSERT'а сразу пишут
status='approved', а шестое (ветка подписки в finalize_purchase)
вставляет 'pending' и переводит в 'approved' в ТОЙ ЖЕ транзакции — при
неудаче транзакция откатывается и строки не остаётся вовсе. Числитель и
знаменатель — одно множество.

Почему не переопределили как «долю успешных попыток оплаты». Нужен
знаменатель — попытки, и обоих кандидатов пришлось забраковать:

  • payment_errors — это лог НАШИХ сбоев на вебхуке (setup_missing,
    webhook_invalid_json, transient, timeout), а не отказов плательщику.
    'transient' означает «повторим», и после успешного повтора в базе
    будут и строка ошибки, и одобренный платёж.

  • pending_purchases — 'expired' там не равно «не заплатил»: создание
    нового счёта принудительно гасит все прежние pending этого юзера,
    так что перебор тарифов кнопками сам плодит «неудачи».

Отказ на стороне провайдера (карта не прошла) до нас просто не доходит:
вебхук приходит только по успеху. Мерить нечего.

Почему плитку нельзя было просто оставить: константа 100% на экране
читается как «всё в порядке», то есть как утверждение о здоровье
платежей, которого мы не проверяли.
"""
import inspect
import re
from pathlib import Path

import pytest

import database.analytics as analytics_mod
import database.analytics_stats as analytics_stats_mod
from app.api.dashboard.routes import stats as stats_route

DEAD_KEY = "approval_rate_percent"


def _body(fn) -> str:
    """Исходник без докстринга: докстринг объясняет дефект и обязан
    упоминать удалённое имя."""
    return inspect.getsource(fn).split('"""', 2)[-1]


def test_metric_is_not_computed_anymore():
    body = _body(analytics_mod.get_business_metrics)
    assert DEAD_KEY not in body
    assert "FROM payments" not in body
    assert "approval_rate" not in body


def test_surviving_metrics_are_untouched():
    """Убрана ровно одна метрика."""
    body = _body(analytics_mod.get_business_metrics)
    assert "avg_subscription_lifetime_days" in body
    assert "avg_renewals_per_user" in body


@pytest.mark.asyncio
async def test_payload_no_longer_carries_the_key(monkeypatch):
    """Ключа нет и в ответе — иначе фронт «на всякий случай» его вернёт."""
    class _Conn:
        async def fetchval(self, query, *args):
            return 1

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(self):
                    return _Conn()

                async def __aexit__(self, *exc):
                    return False

            return _Ctx()

    async def _get_pool():
        return _Pool()

    # Подменять get_pool надо в модуле, где функция ОБЪЯВЛЕНА:
    # database/analytics.py теперь фасад, и его get_pool запросы не видят.
    monkeypatch.setattr(analytics_stats_mod, "get_pool", _get_pool)
    out = await analytics_mod.get_business_metrics()

    assert DEAD_KEY not in out
    assert set(out) == {"avg_subscription_lifetime_days", "avg_renewals_per_user"}


def test_api_no_longer_advertises_it():
    """Докстринг эндпоинта — тоже контракт: по нему пишут фронт."""
    for fn in (stats_route.stats_overview, stats_route.stats_business):
        doc = fn.__doc__ or ""
        assert DEAD_KEY not in doc


def test_dashboard_stopped_rendering_the_tile():
    src = Path("dashboard/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
    # Комментарии объясняют, почему плитки нет, и обязаны называть её по
    # имени — ищем в коде, а не в объяснении.
    code = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    assert f"business_metrics?.{DEAD_KEY}" not in code
    assert "Approval rate" not in code


def test_bot_metrics_screen_stopped_rendering_the_tile():
    """/admin → Метрики печатал ту же вечную сотню."""
    src = Path("app/handlers/admin/stats.py").read_text(encoding="utf-8")
    assert DEAD_KEY not in src


def test_payments_rows_are_still_born_approved():
    """Гард под весь разбор выше: если платежи когда-нибудь начнут жить в
    статусе pending дольше транзакции, метрику станет осмысленно вернуть —
    и этот тест должен об этом сообщить."""
    src = Path("database/subscriptions.py").read_text(encoding="utf-8")
    pending_inserts = src.count("VALUES ($1, $2, $3, 'pending'")
    assert pending_inserts <= 1, (
        "появилась новая ветка, вставляющая платёж в pending — "
        "проверь, доживает ли он до коммита в этом статусе"
    )
