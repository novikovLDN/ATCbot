"""Health-check не должен быть зелёным при неработающем боте.

Два дефекта.

1. Общий кулдаун алертов. Одна глобальная отметка времени на все типы сразу:
   ушёл алерт про БД — на час замолкали Redis, вебхук и упавшие воркеры.
   Деградация базы глушила сигнал о потере FSM-состояний.

2. Проверялись только БД и Redis. Мёртвый фоновый воркер, сбитый вебхук и
   потерянный single-instance лок не проверялись вообще — мониторинг
   продолжал писать HEALTH_CHECK db=ok, пока бот не работал.
"""
import asyncio

import pytest

import config
import database
import healthcheck


class _FakeBot:
    """Запоминает отправленное админу; больше от бота ничего не нужно."""

    def __init__(self, webhook_url="https://test.example/telegram/webhook"):
        self.messages = []
        self._webhook_url = webhook_url

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)
        return True

    async def get_webhook_info(self):
        class _Info:
            url = self._webhook_url
            pending_update_count = 0

        return _Info()


@pytest.fixture(autouse=True)
def clean_healthcheck():
    healthcheck.reset_state()
    yield
    healthcheck.reset_state()


async def test_alerts_of_different_kinds_do_not_mute_each_other():
    """Алерт про БД не должен съедать алерт про Redis.

    Именно это и происходило: сначала деградировала база, через десять минут
    отваливался Redis — и про Redis админ не узнавал вообще.
    """
    bot = _FakeBot()
    assert await healthcheck._send_admin_alert(bot, "db", "БД лежит")
    assert await healthcheck._send_admin_alert(bot, "redis", "Redis лежит")
    assert len(bot.messages) == 2


async def test_same_kind_is_still_rate_limited():
    """Повтор той же проблемы не должен превращаться в спам."""
    bot = _FakeBot()
    assert await healthcheck._send_admin_alert(bot, "db", "БД лежит")
    assert not await healthcheck._send_admin_alert(bot, "db", "БД всё ещё лежит")
    assert len(bot.messages) == 1


async def test_dead_worker_is_noticed():
    """Умерший воркер молчит — заметить его можно только проверкой задач."""
    async def _boom():
        raise RuntimeError("воркер упал")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    healthcheck.watch_tasks({"auto_renewal": task})
    bot = _FakeBot()
    await healthcheck._check_workers(bot)

    assert bot.messages, "смерть воркера прошла незамеченной"
    assert "auto_renewal" in bot.messages[0]


async def test_live_worker_does_not_alert():
    """Живой воркер не должен поднимать ложную тревогу."""
    task = asyncio.create_task(asyncio.sleep(5))
    healthcheck.watch_tasks({"reminders": task})
    bot = _FakeBot()
    try:
        await healthcheck._check_workers(bot)
    finally:
        task.cancel()
    assert bot.messages == []


async def test_watched_group_is_read_by_reference():
    """Воркеры, поднятые после восстановления БД, тоже под наблюдением.

    main.py передаёт свой словарь по ссылке и дозаполняет его позже — иначе
    восстановленные воркеры остались бы без присмотра до перезапуска.
    """
    started = {}
    healthcheck.watch_tasks(started)

    async def _boom():
        raise RuntimeError("упал после восстановления")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    started["site_sync"] = task

    bot = _FakeBot()
    await healthcheck._check_workers(bot)
    assert bot.messages and "site_sync" in bot.messages[0]


async def test_webhook_mismatch_is_reported(monkeypatch):
    """Сбитый вебхук = апдейты идут мимо бота, а db=ok при этом зелёный."""
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://ожидаемый/hook")
    bot = _FakeBot(webhook_url="https://чужой/hook")
    await healthcheck._check_webhook(bot)
    assert bot.messages, "подмена вебхука не замечена"
    assert "чужой" in bot.messages[0]


async def test_matching_webhook_is_silent(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://ожидаемый/hook")
    bot = _FakeBot(webhook_url="https://ожидаемый/hook")
    await healthcheck._check_webhook(bot)
    assert bot.messages == []


async def test_lost_instance_lock_is_reported():
    """Потеря advisory-лока означает возможную вторую реплику."""
    async def _lost():
        return False

    healthcheck.register_instance_lock_check(_lost)
    bot = _FakeBot()
    await healthcheck._check_instance_lock(bot)
    assert bot.messages, "потеря single-instance лока не замечена"


async def test_held_instance_lock_is_silent():
    async def _held():
        return True

    healthcheck.register_instance_lock_check(_held)
    bot = _FakeBot()
    await healthcheck._check_instance_lock(bot)
    assert bot.messages == []


async def test_one_broken_check_does_not_cancel_the_rest(monkeypatch):
    """Сбой проверки БД не должен скрывать мёртвые воркеры.

    Раньше _run_health_check был линейным и выходил по return при неготовой
    БД — всё остальное просто не проверялось.
    """
    async def _explode(bot):
        raise RuntimeError("проверка БД сама сломалась")

    monkeypatch.setattr(healthcheck, "_check_database", _explode)
    monkeypatch.setattr(database, "DB_READY", False)

    async def _boom():
        raise RuntimeError("воркер упал")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    healthcheck.watch_tasks({"activation_worker": task})

    bot = _FakeBot(webhook_url=config.WEBHOOK_URL)
    await healthcheck._run_health_check(bot)

    assert any("activation_worker" in m for m in bot.messages), (
        "упавшая проверка БД снова скрыла мёртвый воркер"
    )
