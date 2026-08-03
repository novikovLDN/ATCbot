"""«Время апрува» убрано целиком, а не оставлено вечным прочерком.

Дефект. avg_payment_approval_time_seconds считалось так: из audit_log
брались строки с action 'payment_approved'/'subscription_renewed', из
свободного текста details регуляркой выдирался «Payment ID: N»,
кастовался в INTEGER и джойнился с payments.

Почему это фикция. Строк такого вида не пишет никто: единственным
писателем 'payment_approved' была ручная модерация платежей
(approve_payment_atomic), её удалили — платежи подтверждает вебхук
провайдера. Метрика всегда возвращала NULL, а шесть плиток дашборда
рисовали прочерк. Заменить её нечем: payments.paid_at и
payments.created_at проставляются одним и тем же INSERT'ом, промежутка
«оплатили → подтвердили» в системе не существует.

Почему важно. Прочерк на экране админ читает как «данных пока нет» и
ждёт, что они появятся. Хуже того, CAST текста в INTEGER — это 500 на
всём /stats/business, если формат details когда-нибудь поменяется.
"""
import inspect

import pytest

import database.analytics as analytics_mod
from app.api.dashboard.routes import stats as stats_route

DEAD_KEY = "avg_payment_approval_time_seconds"


def test_metric_is_not_computed_anymore():
    """Ни расчёта, ни парсинга audit_log в get_business_metrics."""
    src = inspect.getsource(analytics_mod.get_business_metrics)
    body = src.split('"""', 2)[-1]  # докстринг объясняет дефект, его не смотрим
    assert DEAD_KEY not in body
    assert "Payment ID" not in body
    assert "payment_approved" not in body
    assert "SUBSTRING" not in body


def test_surviving_metrics_are_still_there():
    """Живые метрики обязаны остаться.

    approval_rate_percent из этого списка уехал — его убрали отдельно,
    см. tests/services/test_approval_rate_removed.py.
    """
    src = inspect.getsource(analytics_mod.get_business_metrics)
    for key in (
        "avg_subscription_lifetime_days",
        "avg_renewals_per_user",
    ):
        assert key in src


@pytest.mark.asyncio
async def test_api_no_longer_advertises_the_metric():
    """Ни один эндпоинт дашборда не обещает «время апрува» в докстринге."""
    for fn in (stats_route.stats_overview, stats_route.stats_business):
        assert DEAD_KEY not in (fn.__doc__ or "")


def test_dashboard_stopped_rendering_the_tile():
    """Фронт не читает удалённый ключ — иначе на экране снова прочерк."""
    from pathlib import Path

    src = Path("dashboard/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
    assert f"business_metrics?.{DEAD_KEY}" not in src
    assert "Время апрува" not in src.replace(
        "Плитки «Время апрува» здесь больше нет", "",
    )


def test_bot_metrics_screen_stopped_rendering_the_tile():
    """Раздел /admin → Метрики печатал «нет данных» вечно — тоже убрано."""
    from pathlib import Path

    src = Path("app/handlers/admin/stats.py").read_text(encoding="utf-8")
    assert DEAD_KEY not in src
