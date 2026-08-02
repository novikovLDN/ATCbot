"""Логика пробного периода: истечение, тайминги и уведомления.

ЗАЧЕМ ЭТОТ ФАЙЛ ПЕРЕПИСАН
    Прежняя версия вызывала функции по сигнатурам, которых давно нет:
    аргументы шли в другом порядке, часть функций стала асинхронной, а
    расписание уведомлений сознательно опустело. Двенадцать тестов падали
    и не проверяли ничего — они лишь фиксировали API пятилетней давности.
    Здесь зафиксировано фактическое поведение, чтобы тесты снова были
    документацией, а не шумом.

ЧТО ВАЖНО ЗНАТЬ ПРО ТРИАЛ
    Триал длится 72 часа. «Часы с момента активации» вычисляются как
    72 минус часы до истечения — отдельной отметки о старте нет.
    Все проверки идут по UTC.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.trials.service import (
    calculate_trial_timing,
    get_final_reminder_config,
    get_notification_schedule,
    prepare_notification_payload,
    should_send_final_reminder,
)


NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _conn_without_paid_subscription():
    """Соединение, где у пользователя нет активной платной подписки."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _conn_with_paid_subscription():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"?column?": 1})
    return conn


class TestCalculateTrialTiming:
    """Тайминги: сколько осталось и сколько прошло с активации."""

    def test_full_trial_ahead(self):
        timing = calculate_trial_timing(NOW + timedelta(hours=72), NOW)
        assert timing["hours_until_expiry"] == pytest.approx(72)
        assert timing["hours_since_activation"] == pytest.approx(0)

    def test_midway(self):
        timing = calculate_trial_timing(NOW + timedelta(hours=36), NOW)
        assert timing["hours_until_expiry"] == pytest.approx(36)
        assert timing["hours_since_activation"] == pytest.approx(36)

    def test_expired_clamps_to_zero(self):
        """Отрицательное время до истечения не должно утекать наружу.

        При этом «часов с активации» продолжает расти: триал закончился
        5 часов назад, значит с активации прошло 72 + 5 = 77 часов.
        """
        timing = calculate_trial_timing(NOW - timedelta(hours=5), NOW)
        assert timing["hours_until_expiry"] == 0.0
        assert timing["hours_since_activation"] == pytest.approx(77)

    def test_missing_expiry_is_safe(self):
        timing = calculate_trial_timing(None, NOW)
        assert timing == {"hours_until_expiry": 0.0, "hours_since_activation": 0.0}


class TestFinalReminderConfig:
    """«Последний час» — единственное оставшееся уведомление триала."""

    def test_timing_matches_its_text(self):
        """Раньше напоминание слали за 6 часов, а текст обещал «последний
        час» — пользователей это путало. Значения обязаны совпадать."""
        cfg = get_final_reminder_config()
        assert cfg["hours_before_expiry"] == 1
        assert cfg["notification_key"] == "trial.notification_71h"

    def test_has_button_and_flag(self):
        cfg = get_final_reminder_config()
        assert cfg["has_button"] is True
        assert cfg["db_flag"]

    def test_schedule_is_intentionally_empty(self):
        """Промежуточные уведомления убраны намеренно: они дублировали
        друг друга и приходили дважды за пять минут."""
        assert get_notification_schedule() == []


class TestShouldSendFinalReminder:
    """Окно отправки: (0.5, 1] час до истечения."""

    @pytest.mark.asyncio
    async def test_sends_inside_window(self):
        ok, reason = await should_send_final_reminder(
            1, NOW + timedelta(minutes=45), NOW + timedelta(days=30),
            False, NOW, _conn_without_paid_subscription(),
        )
        assert ok is True and reason is None

    @pytest.mark.asyncio
    async def test_too_early(self):
        ok, reason = await should_send_final_reminder(
            1, NOW + timedelta(hours=5), NOW + timedelta(days=30),
            False, NOW, _conn_without_paid_subscription(),
        )
        assert ok is False and reason == "too_early"

    @pytest.mark.asyncio
    async def test_too_late(self):
        """Нижняя граница защищает от гонки с истечением триала:
        воркер не должен слать уведомление о том, что уже закончилось."""
        ok, reason = await should_send_final_reminder(
            1, NOW + timedelta(minutes=20), NOW + timedelta(days=30),
            False, NOW, _conn_without_paid_subscription(),
        )
        assert ok is False and reason == "too_late"

    @pytest.mark.asyncio
    async def test_not_sent_twice(self):
        ok, reason = await should_send_final_reminder(
            1, NOW + timedelta(minutes=45), NOW + timedelta(days=30),
            True, NOW, _conn_without_paid_subscription(),
        )
        assert ok is False and reason == "already_sent"

    @pytest.mark.asyncio
    async def test_skipped_when_subscription_expired(self):
        ok, reason = await should_send_final_reminder(
            1, NOW + timedelta(minutes=45), NOW - timedelta(hours=1),
            False, NOW, _conn_without_paid_subscription(),
        )
        assert ok is False and reason == "subscription_expired"

    @pytest.mark.asyncio
    async def test_skipped_when_user_already_paid(self):
        """Купившему подписку не пишут, что триал заканчивается."""
        ok, reason = await should_send_final_reminder(
            1, NOW + timedelta(minutes=45), NOW + timedelta(days=30),
            False, NOW, _conn_with_paid_subscription(),
        )
        assert ok is False and reason == "has_active_paid_subscription"


class TestPrepareNotificationPayload:
    def test_payload_carries_key_and_button(self):
        payload = prepare_notification_payload("trial.notification_71h", has_button=True)
        assert payload["notification_key"] == "trial.notification_71h"
        assert payload["has_button"] is True

    def test_button_defaults_to_false(self):
        assert prepare_notification_payload("trial.some_key")["has_button"] is False
