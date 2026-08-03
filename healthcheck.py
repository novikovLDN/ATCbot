"""Периодический health-check процесса.

Проверяем не только базу: раз в 10 минут смотрим БД, Redis, живость фоновых
воркеров, зарегистрированный у Telegram вебхук и удержание single-instance
лока. Раньше проверялись только БД и Redis, и мониторинг оставался зелёным
(HEALTH_CHECK db=ok) при мёртвых воркерах или сбитом вебхуке — то есть при
неработающем боте.

Ничего не чиним и никогда не падаем: задача обязана пережить любую ошибку,
иначе процесс потеряет и эти проверки тоже.
"""
import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, Mapping, Optional

from aiogram import Bot
import database
import config

logger = logging.getLogger(__name__)

# Кулдаун алертов — ПО КАТЕГОРИЯМ.
#
# Раньше была одна глобальная отметка времени на все алерты сразу. Стоило
# уйти сообщению про деградацию БД — и на час замолкали Redis, вебхук и
# упавшие воркеры. Разнотипные проблемы глушили друг друга ровно в тот час,
# когда сигнал нужен больше всего: сначала отвалилась база, следом Redis, а
# админ узнал только про первое.
_ALERT_COOLDOWNS: dict[str, float] = {
    "db": 3600.0,
    "redis": 3600.0,
    "workers": 1800.0,
    "webhook": 900.0,
    "instance_lock": 900.0,
}
_DEFAULT_COOLDOWN = 3600.0
_last_alert_at: dict[str, float] = {}

# Наблюдаемые фоновые задачи. Храним именно словари name→task ПО ССЫЛКЕ:
# main.py дозаполняет свой словарь при восстановлении БД, и воркеры,
# поднятые позже, попадают под наблюдение сами собой.
_watched_task_groups: list[Mapping[str, object]] = []

# Проверка удержания advisory-лока. Живёт в main.py (там соединение и
# логика перезахвата), сюда передаётся колбэком, чтобы не тащить импорт
# main в модуль, который main же и импортирует.
_instance_lock_check: Optional[Callable[[], Awaitable[bool]]] = None


def watch_tasks(tasks: Mapping[str, object]) -> None:
    """Взять словарь name→asyncio.Task под наблюдение (по ссылке)."""
    _watched_task_groups.append(tasks)


def register_instance_lock_check(check: Callable[[], Awaitable[bool]]) -> None:
    """Зарегистрировать проверку single-instance лока."""
    global _instance_lock_check
    _instance_lock_check = check


def reset_state() -> None:
    """Сбросить регистрации и кулдауны. Нужно тестам, в проде не вызывается."""
    global _instance_lock_check
    _watched_task_groups.clear()
    _last_alert_at.clear()
    _instance_lock_check = None


async def _send_admin_alert(bot: Bot, category: str, message: str) -> bool:
    """Отправить алерт админу с кулдауном по категории. Никогда не бросает."""
    now = time.monotonic()
    cooldown = _ALERT_COOLDOWNS.get(category, _DEFAULT_COOLDOWN)
    if now - _last_alert_at.get(category, 0.0) < cooldown:
        return False
    try:
        await asyncio.wait_for(
            bot.send_message(config.ADMIN_TELEGRAM_ID, message),
            timeout=10.0
        )
        _last_alert_at[category] = now
        return True
    except Exception as e:
        logger.warning("health_alert_failed category=%s error=%s", category, e)
        return False


async def health_check_task(bot: Bot) -> None:
    """Периодический health-check. Раз в 10 минут, никогда не висит."""
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug("health_check_task: startup jitter done (%.1fs)", jitter_s)

    while True:
        try:
            await asyncio.wait_for(_run_health_check(bot), timeout=45.0)
        except asyncio.TimeoutError:
            logger.error("HEALTH_CHECK_TIMEOUT exceeded 45s")
        except asyncio.CancelledError:
            logger.info("health_check_task cancelled")
            break
        except Exception as e:
            logger.exception("HEALTH_CHECK_ERROR error=%s", e)

        await asyncio.sleep(10 * 60)  # 10 minutes


async def _run_health_check(bot: Bot) -> None:
    """Прогнать все проверки.

    Каждая обёрнута отдельно: сбой одной не должен отменять остальные —
    иначе упавшая проверка БД снова скрыла бы мёртвые воркеры и вебхук.
    """
    for name, check in (
        ("db", _check_database),
        ("redis", _check_redis),
        ("workers", _check_workers),
        ("webhook", _check_webhook),
        ("instance_lock", _check_instance_lock),
    ):
        try:
            await check(bot)
        except Exception as e:
            logger.warning("HEALTH_CHECK_SUBCHECK_FAILED check=%s error=%s", name, type(e).__name__)


async def _check_database(bot: Bot) -> None:
    """БД: готовность флага, наличие пула и живой SELECT 1."""
    if not database.DB_READY:
        logger.warning("HEALTH_CHECK db_ready=False")
        await _send_admin_alert(bot, "db", "⚠️ Bot running in degraded mode (DB unavailable)")
        return

    pool = await database.get_pool()
    if not pool:
        logger.error("HEALTH_CHECK pool=None")
        await _send_admin_alert(bot, "db", "🚨 DB pool is None")
        return

    try:
        async with pool.acquire() as conn:
            result = await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=10.0)
            if result == 1:
                logger.info("HEALTH_CHECK db=ok")
            else:
                logger.error("HEALTH_CHECK unexpected_result=%s", result)
    except Exception as e:
        # SECURITY: Only log exception type, never connection strings or credentials
        logger.error("HEALTH_CHECK db_error=%s", type(e).__name__)
        await _send_admin_alert(bot, "db", f"DB health check failed: {type(e).__name__}")


async def _check_redis(bot: Bot) -> None:
    """Redis: без него теряются FSM-состояния, но бот жив — это отдельная категория."""
    from app.utils.redis_client import ping as redis_ping, is_configured as redis_configured
    if not redis_configured():
        return
    try:
        redis_ok = await redis_ping()
    except Exception as e:
        logger.warning("HEALTH_CHECK redis_check_error=%s", type(e).__name__)
        return
    if redis_ok:
        logger.info("HEALTH_CHECK redis=ok")
    else:
        logger.warning("HEALTH_CHECK redis=unavailable")
        await _send_admin_alert(bot, "redis", "⚠️ Redis health check failed — FSM states may be lost")


async def _check_workers(bot: Bot) -> None:
    """Живость фоновых задач.

    Умерший воркер не создаёт ошибок — он просто перестаёт что-либо делать.
    Ни автопродления, ни активаций, ни напоминаний, и ни строчки в логах.
    Единственный способ это заметить — периодически смотреть на сами задачи.
    """
    dead: list[str] = []
    for group in _watched_task_groups:
        for name, task in list(group.items()):
            if task is None or not hasattr(task, "done"):
                continue
            if not task.done():
                continue
            if getattr(task, "cancelled", lambda: False)():
                dead.append(f"{name} (cancelled)")
                continue
            try:
                exc = task.exception()
            except Exception:
                exc = None
            if exc is not None:
                dead.append(f"{name} ({type(exc).__name__}: {str(exc)[:100]})")
            else:
                dead.append(f"{name} (завершился сам)")

    if not dead:
        logger.info("HEALTH_CHECK workers=ok")
        return

    logger.error("HEALTH_CHECK workers_dead=%s", ", ".join(dead))
    await _send_admin_alert(
        bot, "workers",
        "🚨 Фоновые задачи не работают:\n" + "\n".join(f"• {item}" for item in dead),
    )


async def _check_webhook(bot: Bot) -> None:
    """Вебхук: сверяем зарегистрированный у Telegram URL с ожидаемым.

    URL проверялся ровно один раз — при старте. Если после этого его сбили
    (второй инстанс с тем же токеном, ручной вызов setWebhook), апдейты
    уходят мимо нас, а health-check продолжал рапортовать db=ok.
    """
    expected = getattr(config, "WEBHOOK_URL", None)
    if not expected:
        return
    try:
        info = await asyncio.wait_for(bot.get_webhook_info(), timeout=10.0)
    except Exception as e:
        logger.warning("HEALTH_CHECK webhook_check_error=%s", type(e).__name__)
        return

    actual = getattr(info, "url", None) or ""
    if actual != expected:
        logger.critical("HEALTH_CHECK webhook_mismatch expected=%s got=%s", expected, actual)
        await _send_admin_alert(
            bot, "webhook",
            f"🚨 Вебхук сбит — апдейты идут мимо бота\nОжидался: {expected}\nСейчас: {actual or '(пусто)'}",
        )
        return

    logger.info(
        "HEALTH_CHECK webhook=ok pending=%s",
        getattr(info, "pending_update_count", None),
    )


async def _check_instance_lock(bot: Bot) -> None:
    """Single-instance лок: держим ли мы его всё ещё.

    Session-level advisory lock снимается автоматически при обрыве
    соединения. Процесс при этом продолжает работать и считает себя
    единственной репликой — а вторая реплика спокойно берёт лок и начинает
    параллельно продлевать подписки и рассылать уведомления.
    """
    if _instance_lock_check is None:
        return
    held = await _instance_lock_check()
    if held:
        logger.info("HEALTH_CHECK instance_lock=ok")
        return
    logger.critical("HEALTH_CHECK instance_lock=lost")
    await _send_admin_alert(
        bot, "instance_lock",
        "🚨 Потерян single-instance лок — возможна вторая работающая реплика",
    )
