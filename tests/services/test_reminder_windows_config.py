"""Окна напоминаний: тумблер в дашборде должен реально влиять на отправку.

Дефект 1 (app/services/notifications/service.py). Окна платных напоминаний
были вбиты в should_send_reminder: 7д ±3ч, 3д ±2.4ч, 24ч ±1ч, 3ч ±0.5ч. В
реестре автоуведомлений для тех же ключей записаны ДРУГИЕ default_trigger
(±12 / ±6 / ±2 / ±1 ч), а reminders.py читал из trigger_config только
segment_filter. Админ расширял допуск в дашборде, реальное окно не менялось —
и он считал настройку рабочей.

Дефект 2 (database/reminders_queries.py). Воркер тянул ВСЕ подписки с
expires_at > now, без лимита, вместе с bypass-only строками (дата «сейчас +
10 лет») и триальными, которых обслуживает отдельный воркер. Итерация в
reminders.py обёрнута в asyncio.wait_for(120s) — на десятках тысяч подписок
она в него не укладывалась, срабатывал WORKER_TIMEOUT, и хвост выборки (из-за
ORDER BY expires_at ASC — ровно кандидаты на напоминание за 7 дней) писем не
получал вовсе.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.services.notifications.service import (
    ReminderType,
    reminder_query_windows,
    resolve_reminder_window,
    should_send_reminder,
)

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _sub(hours_left: float, **extra):
    row = {
        "telegram_id": 42,
        "expires_at": NOW + timedelta(hours=hours_left),
        "subscription_type": "basic",
        "source": "payment",
    }
    row.update(extra)
    return row


# ── Дефект 1: окна из trigger_config ──────────────────────────────────

def test_default_window_matches_the_registry_not_the_old_hardcode():
    """Дефолт — тот же, что default_trigger в реестре (±6ч для 3 дней)."""
    target, tolerance = resolve_reminder_window(ReminderType.REMINDER_3D)
    assert target == timedelta(days=3)
    assert tolerance == timedelta(hours=6)


def test_widened_tolerance_from_dashboard_captures_more_users():
    """Админ расширил допуск до 12 часов — человек за 3д10ч должен попасть."""
    sub = _sub(3 * 24 + 10)
    assert should_send_reminder(sub, now=NOW).should_send is False

    cfg = {"subscription.reminder_3d": {"before_expiry_hours": 72, "tolerance_hours": 12}}
    decision = should_send_reminder(sub, now=NOW, trigger_configs=cfg)
    assert decision.should_send is True
    assert decision.reminder_type == ReminderType.REMINDER_3D


def test_narrowed_tolerance_from_dashboard_excludes_users():
    """Сужение окна тоже должно работать — иначе тумблер односторонний."""
    sub = _sub(3 * 24 + 5)
    assert should_send_reminder(sub, now=NOW).should_send is True

    cfg = {"subscription.reminder_3d": {"before_expiry_hours": 72, "tolerance_hours": 1}}
    assert should_send_reminder(sub, now=NOW, trigger_configs=cfg).should_send is False


def test_shifted_before_expiry_hours_moves_the_whole_window():
    """Меняем не только допуск, но и саму точку отправки."""
    cfg = {"subscription.reminder_1d": {"before_expiry_hours": 48, "tolerance_hours": 1}}
    decision = should_send_reminder(_sub(48), now=NOW, trigger_configs=cfg)
    assert decision.reminder_type == ReminderType.REMINDER_1D
    assert decision.should_send is True


@pytest.mark.parametrize("bad", ["", "abc", 0, -5, None, True])
def test_garbage_in_trigger_config_falls_back_to_default(bad):
    """trigger_config правит человек. Мусор не должен выключать напоминание."""
    cfg = {"subscription.reminder_3d": {"before_expiry_hours": bad, "tolerance_hours": bad}}
    assert resolve_reminder_window(ReminderType.REMINDER_3D, cfg) == (
        timedelta(days=3), timedelta(hours=6),
    )


def test_already_sent_flag_still_wins_over_a_matching_window():
    """Расширение окна не должно приводить к повторной отправке."""
    sub = _sub(3 * 24, reminder_3d_sent=True)
    decision = should_send_reminder(sub, now=NOW)
    assert decision.should_send is False
    assert decision.reminder_type == ReminderType.REMINDER_3D


def test_admin_grant_windows_are_unchanged():
    """Админские гранты из дашборда не настраиваются — поведение прежнее."""
    sub = _sub(6, admin_grant_days=1, source="admin")
    assert should_send_reminder(sub, now=NOW).reminder_type == ReminderType.ADMIN_1DAY_6H


# ── Дефект 2: отбор кандидатов в SQL ──────────────────────────────────

class _FakeConn:
    def __init__(self, batches, recorder):
        self._batches = batches
        self._recorder = recorder

    async def fetch(self, sql, *args):
        self._recorder["sql"] = sql
        self._recorder["calls"] += 1
        return self._batches.pop(0) if self._batches else []


class _Acquire:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        self._pool.recorder["acquires"] += 1
        return _FakeConn(self._pool.batches, self._pool.recorder)

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, batches, recorder):
        self.batches = batches
        self.recorder = recorder

    def acquire(self):
        return _Acquire(self)


def _install_pool(monkeypatch, batches):
    import database.reminders_queries as rq
    recorder = {"calls": 0, "acquires": 0, "sql": ""}
    monkeypatch.setattr(rq._core, "DB_READY", True, raising=False)
    monkeypatch.setattr(rq, "get_pool", AsyncMock(return_value=_FakePool(batches, recorder)))
    return rq, recorder


@pytest.mark.asyncio
async def test_selection_filters_are_pushed_into_sql(monkeypatch):
    """Окна, флаги, bypass-only и триалы отсекаются запросом, а не циклом."""
    rq, rec = _install_pool(monkeypatch, [[]])
    await rq.get_subscriptions_for_reminders()

    sql = rec["sql"]
    assert "is_bypass_only" in sql, "bypass-only строки живут на NOW+10 лет"
    assert "'trial'" in sql, "триалы обслуживает отдельный воркер"
    assert "BETWEEN" in sql, "окна должны считаться в SQL"
    assert "reminder_7d_sent" in sql and "reminder_3h_sent" in sql
    assert "LIMIT" in sql, "выборка обязана быть ограниченной"


@pytest.mark.asyncio
async def test_reads_in_batches_and_releases_the_connection_between_them(monkeypatch):
    """Пагинация по id: соединение берётся на батч, а не на весь проход."""
    rq, rec = _install_pool(monkeypatch, [
        [{"id": i, "telegram_id": i} for i in range(1, 501)],
        [{"id": 501, "telegram_id": 501}],
    ])
    rows = await rq.get_subscriptions_for_reminders(batch_size=500)

    assert len(rows) == 501
    assert rec["calls"] == 2
    assert rec["acquires"] == 2, "на каждый батч — своё короткое соединение"


@pytest.mark.asyncio
async def test_row_cap_stops_a_runaway_selection(monkeypatch):
    """Аномальный допуск не должен вытягивать полбазы в память воркера."""
    rq, rec = _install_pool(monkeypatch, [
        [{"id": i, "telegram_id": i} for i in range(1, 11)],
        [{"id": i, "telegram_id": i} for i in range(11, 21)],
        [{"id": i, "telegram_id": i} for i in range(21, 31)],
    ])
    rows = await rq.get_subscriptions_for_reminders(batch_size=10, max_rows=15)

    assert len(rows) == 20, "остановились сразу после превышения потолка"
    assert rec["calls"] == 2


@pytest.mark.asyncio
async def test_unknown_flag_column_is_refused(monkeypatch):
    """Имя флага уходит в текст запроса — только из whitelist."""
    rq, _ = _install_pool(monkeypatch, [[]])
    with pytest.raises(ValueError, match="Invalid reminder flag"):
        await rq.get_subscriptions_for_reminders(windows=[(24, 1, "expires_at; DROP TABLE")])


@pytest.mark.asyncio
async def test_worker_windows_reach_the_query(monkeypatch):
    """Выборка и решение должны считать по одним числам.

    reminder_query_windows отдаёт то же, чем потом пользуется
    should_send_reminder; если разъедутся — воркер либо вычитывает людей,
    которых всё равно пропустит, либо не вычитывает тех, кому пора писать.
    """
    cfg = {"subscription.reminder_7d": {"before_expiry_hours": 168, "tolerance_hours": 20}}
    windows = dict((flag, (before, tol)) for before, tol, flag in reminder_query_windows(cfg))
    assert windows["reminder_7d_sent"] == (168.0, 20.0)
    assert windows["reminder_6h_sent"] == (6.0, 0.5)

    target, tolerance = resolve_reminder_window(ReminderType.REMINDER_7D, cfg)
    assert (target.total_seconds() / 3600, tolerance.total_seconds() / 3600) == (168.0, 20.0)


@pytest.mark.asyncio
async def test_worker_reads_dashboard_config_once_and_hands_it_to_the_query(monkeypatch):
    """Проводка целиком: воркер читает trigger_config и отдаёт окна в выборку.

    Раньше конфиг вообще не доходил ни до выборки, ни до решения — тумблер в
    дашборде был декоративным. Конфигов четыре, подписок могут быть десятки
    тысяч, поэтому читать их надо один раз на проход, а не на пользователя.
    """
    import reminders
    from app.services import automated_notifications as an

    reads = []

    async def fake_get_trigger_config(key):
        reads.append(key)
        return {"before_expiry_hours": 168, "tolerance_hours": 20}

    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(an, "get_trigger_config", fake_get_trigger_config)
    monkeypatch.setattr(reminders, "database", type("DB", (), {
        "get_subscriptions_for_reminders": staticmethod(fetch),
    }))

    await reminders.send_smart_reminders(bot=None)

    assert sorted(reads) == [
        "subscription.reminder_1d", "subscription.reminder_3d",
        "subscription.reminder_3h", "subscription.reminder_7d",
    ]
    windows = dict(
        (flag, (before, tol))
        for before, tol, flag in fetch.await_args.kwargs["windows"]
    )
    assert windows["reminder_7d_sent"] == (168.0, 20.0)
