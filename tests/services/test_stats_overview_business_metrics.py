"""/stats/overview обязан отдавать business_metrics.

Дефект: фронт читает overview.data.business_metrics в шести местах
(Dashboard.tsx), а get_extended_bot_stats такого ключа не возвращает вовсе —
шесть KPI на главной («процент подтверждения», «время жизни подписки»,
«продлений на пользователя», «среднее время подтверждения оплаты») всегда
показывали «—». Отдельный эндпоинт /stats/business существовал, но дашборд
его не вызывал.
"""
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_overview_includes_business_metrics(monkeypatch):
    from app.api.dashboard.routes import stats

    monkeypatch.setattr(
        stats.database, "get_extended_bot_stats",
        AsyncMock(return_value={"total_users": 10, "active_subscriptions": 3}),
    )
    monkeypatch.setattr(
        stats.database, "get_active_paid_subscriptions_count",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        stats.database, "get_business_metrics",
        AsyncMock(return_value={
            "avg_subscription_lifetime_days": 63.2,
            "avg_renewals_per_user": 1.4,
        }),
    )

    data = await stats.stats_overview()

    assert data["business_metrics"]["avg_subscription_lifetime_days"] == 63.2
    assert data["business_metrics"]["avg_renewals_per_user"] == 1.4
    assert data["active_paid_subscriptions"] == 2


@pytest.mark.asyncio
async def test_business_metrics_failure_does_not_break_the_page(monkeypatch):
    """Тяжёлый запрос по audit_log не должен ронять всю главную страницу."""
    from app.api.dashboard.routes import stats

    monkeypatch.setattr(
        stats.database, "get_extended_bot_stats",
        AsyncMock(return_value={"total_users": 10}),
    )
    monkeypatch.setattr(
        stats.database, "get_active_paid_subscriptions_count",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        stats.database, "get_business_metrics",
        AsyncMock(side_effect=RuntimeError("audit_log scan timed out")),
    )

    data = await stats.stats_overview()

    assert data["total_users"] == 10
    assert data["business_metrics"] == {}
