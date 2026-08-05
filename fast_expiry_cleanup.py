"""
Fast Expiry Cleanup - автоматическое отключение истёкших VPN подписок

Фоновая задача для немедленного отзыва VPN-доступа после истечения подписки.
Работает асинхронно, не блокирует основной event loop бота.

Требования:
- Запускается каждые 1-5 минут (настраивается через переменную окружения)
- Использует UTC время для сравнения дат
- Идемпотентна (безопасно запускать несколько раз)
- Устойчива к сетевым ошибкам (повтор в следующем цикле)
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
import asyncpg
import database
import config
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection
from app.core.worker_startup import startup_jitter
from app.utils.telegram_safe import safe_send_message
from app.services.language_service import resolve_user_language
from app import i18n

logger = logging.getLogger(__name__)

# Event loop protection: max iteration time (prevents 300s blocking)
MAX_ITERATION_SECONDS = int(os.getenv("FAST_EXPIRY_MAX_ITERATION_SECONDS", "15"))
BATCH_SIZE = 100
_worker_lock = asyncio.Lock()

# Интервал проверки: 1-5 минут (настраивается через переменную окружения)
# По умолчанию: 60 секунд (1 минута)
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))
# Ограничиваем интервал от 60 секунд (1 минута) до 300 секунд (5 минут)
CLEANUP_INTERVAL_SECONDS = max(60, min(300, CLEANUP_INTERVAL_SECONDS))

# STEP 3 — PART B: WORKER LOOP SAFETY
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 10  # seconds


async def fast_expiry_cleanup_task(bot=None):
    """
    Fast Expiry Cleanup Task
    
    Автоматическая фоновая задача для отключения истёкших VPN подписок.
    Работает асинхронно, не блокирует основной event loop бота.
    
    Логика:
    1. Находит все подписки где:
       - status = 'active'
       - expires_at (subscription_end) < текущее UTC время
       - uuid IS NOT NULL
    2. Для каждой подписки:
       - Вызывает POST {XRAY_API_URL}/remove-user/{uuid} с заголовком X-API-Key
       - Если API вызов успешен - обновляет статус на 'expired' и очищает uuid/vpn_key
    3. Защита от повторного удаления: проверка что UUID всё ещё существует перед обновлением БД
    4. При ошибке сети - НЕ очищает БД, повторит в следующем цикле
    
    Идемпотентность:
    - remove-user идемпотентен (отсутствие UUID на сервере не считается ошибкой)
    - Повторное удаление одного UUID безопасно
    - Защиты от гонки между репликами здесь НЕТ. Раньше в этом списке
      значилось «защита через processing_uuids множество» — множество жило в
      памяти процесса, uuid добавлялся и удалялся в пределах одного витка
      цикла, так что проверка не могла сработать ни разу, а между репликами
      она не защищала в принципе. Строчка в докстринге была опаснее самого
      кода: на несуществующую защиту можно было опереться при доработке.
      Настоящая защита — advisory-лок или SELECT ... FOR UPDATE SKIP LOCKED,
      как в auto_renewal; здесь пока обходимся тем, что операция идемпотентна
      и UPDATE идёт с проверкой uuid (сравните cleanup: UPDATE_FAILED).

    Не блокирует event loop:
    - Использует async/await для всех операций
    - Сетевые запросы выполняются асинхронно
    - База данных операции выполняются через asyncpg
    """
    logger.info(
        f"Fast expiry cleanup task started (interval: {CLEANUP_INTERVAL_SECONDS} seconds, "
        f"range: 60-300 seconds, using UTC time)"
    )
    
    # Разброс старта — общее правило для всех воркеров,
    # см. app/core/worker_startup.
    await startup_jitter("fast_expiry_cleanup")
    
    iteration_number = 0
    
    while True:
        # Initialize variables at top of loop to ensure they're always defined
        items_processed = 0
        # Сколько строк реально закрыто, а не просто просмотрено.
        # items_processed увеличивается ПЕРВЫМ действием в цикле — до
        # проверки истечения, до guard'а платной подписки, до UPDATE и до
        # обращения к панели. То есть это размер выборки: итерация, где все
        # 100 строк пропущены или упали, рапортовала «обработано 100».
        items_revoked = 0
        outcome = "success"
        iteration_error_type = None
        # Причина пропуска итерации — уходит в метрики вместе с исходом.
        iteration_reason = None
        
        # Здесь заводилось множество processing_uuids «для защиты от race
        # condition». Удалено: uuid добавлялся в него и тут же удалялся в
        # finally того же прохода цикла, обработка последовательная, так что
        # проверка `if uuid in processing_uuids` не могла сработать ни разу.
        # Между репликами оно тоже не защищало — живёт в памяти процесса.
        # См. докстринг выше про то, чем защищаться на самом деле.
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="fast_expiry_cleanup",
            iteration_number=iteration_number
        )
        
        try:
            # Feature flag check
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            # Конец итерации логирует finally ниже — он срабатывает и на
            # continue. Раньше эта ветка логировала его ещё и сама, поэтому
            # каждая пропущенная итерация попадала в метрики дважды.
            if not feature_flags.background_workers_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Background workers disabled, skipping iteration in fast_expiry_cleanup "
                    f"(iteration={iteration_number})"
                )
                outcome = "skipped"
                iteration_reason = "background_workers_enabled=false"
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue

            # Simple DB readiness check
            if not database.DB_READY:
                # Исход обязателен: раньше переменная оставалась "success",
                # и в метриках пропущенная итерация выглядела успешной —
                # то есть недоступность базы была не видна вовсе.
                logger.warning("fast_expiry_cleanup: skipping — DB not ready")
                outcome = "skipped"
                iteration_reason = "db_not_ready"
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # Получаем текущее UTC время для сравнения
            # PostgreSQL TIMESTAMP хранит без timezone, поэтому используем naive datetime
            now_utc = datetime.now(timezone.utc)
            
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration_body():
                nonlocal items_processed, items_revoked, outcome  # Allow modification of outer scope variables
                # Event loop protection: prevent overlapping iterations
                async with _worker_lock:
                    # Получаем истёкшие подписки с активными UUID
                    # Используем expires_at (в БД) - это и есть subscription_end
                    # STEP 1.2 - BACKGROUND WORKERS CONTRACT: Each iteration is stateless, may be safely skipped
                    # STEP 1.3 - EXTERNAL DEPENDENCIES POLICY: DB unavailable → iteration skipped, no error raised
                    try:
                        pool = await database.get_pool()
                    except (asyncpg.PostgresError, asyncio.TimeoutError, RuntimeError) as e:
                        logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable (pool acquisition failed): {type(e).__name__}: {str(e)[:100]}")
                        return
                    except Exception as e:
                        logger.error(f"fast_expiry_cleanup: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
                        return

                    try:
                        last_seen_id = 0
                        # Бюджет времени на всю итерацию, а не на один батч.
                        # Раньше отсчёт стоял внутри цикла и обнулялся на
                        # каждой сотне строк: заявленные 15 секунд на итерацию
                        # превращались в 15 секунд НА БАТЧ, и воркер мог
                        # держать event loop минутами — ровно то, от чего
                        # лимит и должен защищать.
                        loop_start = time.monotonic()
                        while True:
                            # POOL_STABILITY: Fetch batch with short-lived conn; release immediately (no HTTP inside).
                            async with acquire_connection(pool, "fast_expiry_fetch") as conn:
                                rows = await conn.fetch(
                                    """SELECT id, telegram_id, uuid, vpn_key, expires_at, status, source 
                                       FROM subscriptions 
                                       WHERE status = 'active'
                                       AND expires_at < $1
                                       AND uuid IS NOT NULL
                                       AND id > $2
                                       ORDER BY id ASC
                                       LIMIT $3""",
                                    database._to_db_utc(now_utc), last_seen_id, BATCH_SIZE
                                )
                            if not rows:
                                break

                            logger.info(f"cleanup: FOUND_EXPIRED [count={len(rows)}]")
                            if time.monotonic() - loop_start > MAX_ITERATION_SECONDS:
                                logger.warning(
                                    "fast_expiry_cleanup: бюджет итерации исчерпан, "
                                    "остальные батчи — в следующем проходе"
                                )
                                break
                            for i, row in enumerate(rows):
                                if i > 0 and i % 20 == 0:
                                    await cooperative_yield()
                                if time.monotonic() - loop_start > MAX_ITERATION_SECONDS:
                                    logger.warning("Fast expiry cleanup iteration time limit reached, breaking early")
                                    break
                                items_processed += 1
                                telegram_id = row["telegram_id"]
                                uuid = row["uuid"]
                                expires_at = row["expires_at"]
                                source = row.get("source", "unknown")

                                # ЗАЩИТА: Проверяем что подписка действительно истекла (используем UTC)
                                expires_at_aware = database._from_db_utc(expires_at) if expires_at else None
                                if expires_at_aware is not None and expires_at_aware >= now_utc:
                                    logger.warning(
                                        f"cleanup: SKIP_NOT_EXPIRED [user={telegram_id}, expires_at={expires_at.isoformat()}, "
                                        f"now={now_utc.isoformat()}]"
                                    )
                                    continue

                                # Canonical guard: paid subscription ALWAYS overrides trial (short-lived conn only).
                                async with acquire_connection(pool, "fast_expiry_paid_check") as conn_check:
                                    active_paid = await database.get_active_paid_subscription(conn_check, telegram_id, now_utc)
                                if active_paid:
                                    paid_expires_at = active_paid["expires_at"]
                                    logger.info(
                                        f"SKIP_TRIAL_EXPIRY_PAID_USER: user_id={telegram_id}, "
                                        f"trial_expires_at={expires_at.isoformat() if expires_at else None}, "
                                        f"paid_expires_at={paid_expires_at.isoformat() if paid_expires_at else None}, "
                                        f"expired_subscription_source={source} - "
                                        "User has active paid subscription, skipping expired subscription cleanup"
                                    )
                                    continue

                                # Здесь была проверка `if uuid in processing_uuids`
                                # с веткой SKIP_ALREADY_PROCESSING — недостижимая
                                # (см. комментарий в начале витка цикла).
                                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")

                                try:
                                    logger.info(
                                        f"cleanup: EXPIRING [user={telegram_id}, uuid={uuid_preview}, "
                                        f"expires_at={expires_at.isoformat()}]"
                                    )
                                    # ПОРЯДОК ДЕЙСТВИЙ: сначала база, потом панель.
                                    #
                                    # Здесь стоял вызов vpn_service.remove_uuid_if_needed —
                                    # он вёл в no-op заглушку и возвращал True, ничего не
                                    # отключив, а следом писался аудит vpn_expire с
                                    # result=success. То есть запись «доступ закрыт»
                                    # появлялась до всякой проверки, что подписку и правда
                                    # закрывают: строкой ниже мог сработать SKIP_RENEWED —
                                    # человек продлился, аудит уже соврал.
                                    #
                                    # Теперь premium-сущность гасится ПОСЛЕ успешного
                                    # UPDATE, и только там пишется аудит. Bypass-сущность
                                    # это не трогает: она отдельная и продолжает работать,
                                    # если у человека остались гигабайты обхода.

                                    # POOL_STABILITY: DB update with dedicated short-lived conn (no conn held during HTTP).
                                    # Панель гасим не здесь, а после выхода из соединения:
                                    # HTTP внутри открытой транзакции держал бы соединение
                                    # пула и блокировки строк всё время запроса.
                                    disable_premium_after_commit = False
                                    try:
                                        async with acquire_connection(pool, "fast_expiry_update") as conn:
                                            async with conn.transaction():
                                                check_row = await conn.fetchrow(
                                                    """SELECT uuid, expires_at, status 
                                                       FROM subscriptions 
                                                       WHERE telegram_id = $1 
                                                       AND uuid = $2 
                                                       AND status = 'active'""",
                                                    telegram_id, uuid
                                                )
                                                if check_row:
                                                    check_expires_at = database._from_db_utc(check_row["expires_at"]) if check_row["expires_at"] else None
                                                    if check_expires_at is not None and check_expires_at >= now_utc:
                                                        logger.warning(
                                                            f"cleanup: SKIP_RENEWED [user={telegram_id}, uuid={uuid_preview}, "
                                                            f"expires_at={check_expires_at.isoformat()}] - subscription was renewed"
                                                        )
                                                    else:
                                                        # Check if user has Remnawave bypass traffic
                                                        # If yes: transition to bypass-only (keep Remnawave active)
                                                        has_remnawave = await conn.fetchval(
                                                            "SELECT remnawave_uuid FROM subscriptions WHERE telegram_id = $1 AND remnawave_uuid IS NOT NULL",
                                                            telegram_id,
                                                        )

                                                        if has_remnawave:
                                                            # Bypass GB must keep working — transition to bypass-only
                                                            from datetime import timedelta
                                                            far_future = database._to_db_utc(now_utc + timedelta(days=3650))
                                                            update_result = await conn.execute(
                                                                """UPDATE subscriptions
                                                                   SET uuid = NULL, vpn_key = NULL, vpn_key_plus = NULL,
                                                                       is_bypass_only = TRUE,
                                                                       expires_at = $3,
                                                                       source = 'bypass_only'
                                                                   WHERE telegram_id = $1
                                                                   AND uuid = $2
                                                                   AND status = 'active'""",
                                                                telegram_id, uuid, far_future,
                                                            )
                                                            if update_result == "UPDATE 1":
                                                                logger.info(
                                                                    f"cleanup: TRANSITION_TO_BYPASS_ONLY [user={telegram_id}, uuid={uuid_preview}] "
                                                                    f"— Remnawave stays active, Xray removed"
                                                                )
                                                                # Extend Remnawave expiry so bypass keeps working
                                                                try:
                                                                    from app.services.remnawave_service import extend_remnawave_for_bypass_bg
                                                                    extend_remnawave_for_bypass_bg(telegram_id)
                                                                except Exception as rmn_err:
                                                                    logger.warning(f"REMNAWAVE_BYPASS_EXTEND_FAIL: tg={telegram_id} {rmn_err}")
                                                                # Notify user
                                                                if bot:
                                                                    try:
                                                                        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                                                                        lang = await resolve_user_language(telegram_id)
                                                                        bypass_text = i18n.get_text(lang, "traffic.subscription_expired_bypass_active")
                                                                        bypass_kb = InlineKeyboardMarkup(inline_keyboard=[
                                                                            [InlineKeyboardButton(text=i18n.get_text(lang, "traffic.buy_traffic_btn"), callback_data="buy_traffic")],
                                                                            [InlineKeyboardButton(text=i18n.get_text(lang, "traffic.buy_subscription"), callback_data="menu_buy_vpn")],
                                                                        ])
                                                                        await safe_send_message(bot, telegram_id, bypass_text, parse_mode="HTML", reply_markup=bypass_kb)
                                                                    except Exception as notif_err:
                                                                        logger.warning(f"cleanup: failed to send bypass-only notification to {telegram_id}: {notif_err}")
                                                        else:
                                                            update_result = await conn.execute(
                                                                """UPDATE subscriptions
                                                                   SET status = 'expired', uuid = NULL, vpn_key = NULL
                                                                   WHERE telegram_id = $1
                                                                   AND uuid = $2
                                                                   AND status = 'active'""",
                                                                telegram_id, uuid
                                                            )
                                                        if update_result == "UPDATE 1":
                                                            # Платный доступ снят в обоих случаях, значит
                                                            # premium в панели надо погасить. До этой строки
                                                            # нельзя дойти, если человек успел продлиться
                                                            # (SKIP_RENEWED выше).
                                                            disable_premium_after_commit = True

                                                            # А вот ЧТО ИМЕННО произошло — разное, и в аудите
                                                            # это должно читаться по-разному.
                                                            #
                                                            # Раньше здесь для обеих веток писалось «подписка
                                                            # истекла и UUID удалён». Для перехода в
                                                            # bypass-only это неправда: строка остаётся
                                                            # активной, expires_at уезжает на десять лет
                                                            # вперёд, человек продолжает пользоваться обходом.
                                                            # Разбор инцидента по такому журналу вёл не туда:
                                                            # искали истёкшую подписку, а она активна.
                                                            if has_remnawave:
                                                                audit_action = "vpn_bypass_transition"
                                                                audit_details = (
                                                                    f"Платная подписка закончилась, остался обход. "
                                                                    f"expires_at={expires_at.isoformat()}"
                                                                )
                                                                audit_event = "uuid_bypass_transition"
                                                                audit_event_details = (
                                                                    f"Переход в bypass-only, снят UUID {uuid_preview}, "
                                                                    f"expired_at={expires_at.isoformat()}"
                                                                )
                                                            else:
                                                                logger.info(
                                                                    f"cleanup: SUBSCRIPTION_EXPIRED [user={telegram_id}, uuid={uuid_preview}, "
                                                                    f"expires_at={expires_at.isoformat()}]"
                                                                )
                                                                audit_action = "vpn_expire"
                                                                audit_details = (
                                                                    f"Subscription expired and UUID removed, "
                                                                    f"expires_at={expires_at.isoformat()}"
                                                                )
                                                                audit_event = "uuid_fast_deleted"
                                                                audit_event_details = (
                                                                    f"Fast-deleted expired UUID {uuid_preview}, "
                                                                    f"expired_at={expires_at.isoformat()}"
                                                                )

                                                            import config
                                                            await database._log_audit_event_atomic(
                                                                conn,
                                                                audit_event,
                                                                config.ADMIN_TELEGRAM_ID,
                                                                telegram_id,
                                                                audit_event_details,
                                                            )
                                                            try:
                                                                await database._log_vpn_lifecycle_audit_async(
                                                                    action=audit_action,
                                                                    telegram_id=telegram_id,
                                                                    uuid=uuid,
                                                                    source="auto-expiry",
                                                                    result="success",
                                                                    details=audit_details,
                                                                )
                                                            except Exception as e:
                                                                logger.warning(f"Failed to log VPN expire audit (non-blocking): {e}")
                                                            # «SUCCESS» здесь — про базу, а не про доступ.
                                                            #
                                                            # Отзыв в панели идёт ниже, уже после закрытия
                                                            # транзакции, и может не состояться. Прежний текст
                                                            # не оговаривал стадию, поэтому запись читалась как
                                                            # «доступ отозван» — при том что на этот момент в
                                                            # панели не сделано ещё ничего.
                                                            items_revoked += 1
                                                            logger.info(
                                                                f"cleanup: DB_CLEARED [user={telegram_id}, uuid={uuid_preview}, "
                                                                f"mode={'bypass_only' if has_remnawave else 'expired'}, "
                                                                f"expires_at={expires_at.isoformat()}] — "
                                                                f"база обновлена, отзыв в панели ниже"
                                                            )
                                                        else:
                                                            logger.warning(
                                                                f"cleanup: UPDATE_FAILED [user={telegram_id}, uuid={uuid_preview}, "
                                                                f"update_result={update_result}] - UUID may have been updated by another process"
                                                            )
                                                else:
                                                    logger.debug(
                                                        f"cleanup: UUID_ALREADY_CLEANED [user={telegram_id}, uuid={uuid_preview}] - "
                                                        "UUID was already removed or subscription is no longer active"
                                                    )

                                        # Соединение отдано в пул, транзакция закрыта —
                                        # теперь можно идти по сети.
                                        if disable_premium_after_commit:
                                            try:
                                                from app.services.remnawave_premium import disable_premium_user
                                                if await disable_premium_user(telegram_id):
                                                    logger.info(
                                                        "cleanup: PREMIUM_DISABLED [user=%s]", telegram_id,
                                                    )
                                                else:
                                                    # Сущности нет, панель выключена или отказала —
                                                    # три разных причины под одним исходом, различить
                                                    # их по этой записи нельзя.
                                                    #
                                                    # Уровень поднят до error, потому что ретрая не
                                                    # будет ПО ПОСТРОЕНИЮ: uuid в базе уже занулён, и
                                                    # в следующий проход строка не попадёт в выборку
                                                    # (условие uuid IS NOT NULL). Доступ в панели
                                                    # остаётся активным, а эта строка — единственное
                                                    # свидетельство, по которому его потом искать.
                                                    logger.error(
                                                        "cleanup: PREMIUM_DISABLE_SKIPPED [user=%s] — "
                                                        "сущность в панели могла остаться активной; "
                                                        "повтора не будет, uuid в базе уже очищен",
                                                        telegram_id,
                                                    )
                                            except Exception as rmn_err:
                                                # То же последствие, что и выше: доступ не отозван и
                                                # автоматически отозван не будет. Плюс traceback —
                                                # без него причина сетевого отказа не видна.
                                                logger.error(
                                                    "cleanup: PREMIUM_DISABLE_FAIL [user=%s] %s — "
                                                    "доступ в панели НЕ отозван, повтора не будет",
                                                    telegram_id, rmn_err, exc_info=True,
                                                )
                                    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
                                        logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable during DB update: {type(e).__name__}: {str(e)[:100]}")
                                    except Exception as e:
                                        logger.error(f"fast_expiry_cleanup: Unexpected error during DB update: {type(e).__name__}: {str(e)[:100]}")
                                        logger.debug(f"fast_expiry_cleanup: Full traceback for DB update", exc_info=True)

                                except ValueError as e:
                                    logger.error(
                                        f"cleanup: VALUE_ERROR [user={telegram_id}, uuid={uuid_preview}, error={str(e)}]"
                                    )

                                except Exception as e:
                                    logger.error(
                                        f"cleanup: UNEXPECTED_ERROR [user={telegram_id}, uuid={uuid_preview}, "
                                        f"error={str(e)}, error_type={type(e).__name__}] - will retry in next cycle"
                                    )
                                    logger.exception(f"cleanup: EXCEPTION_TRACEBACK [user={telegram_id}, uuid={uuid_preview}]")

                                # Здесь был finally с processing_uuids.discard(uuid) —
                                # вторая половина мнимой защиты от гонки, из-за
                                # которой она и не работала: uuid снимался в том же
                                # витке, в котором ставился.

                            if rows:
                                last_seen_id = rows[-1]["id"]
                            await asyncio.sleep(0)

                    # STEP 2.3 — OBSERVABILITY: Log once per worker cycle (after all batches)
                    # Note: outcome and items_processed set inside _run_iteration_body
                    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
                        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
                        logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable in main loop: {type(e).__name__}: {str(e)[:100]}")
                        outcome = "degraded"
                    except Exception as e:
                        logger.error(f"fast_expiry_cleanup: Unexpected error in main loop: {type(e).__name__}: {str(e)[:100]}")
                        logger.debug("fast_expiry_cleanup: Full traceback in main loop", exc_info=True)
                        outcome = "failed"
            
            # H1 fix: Execute iteration body with timeout wrapper
            try:
                await asyncio.wait_for(_run_iteration_body(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=fast_expiry_cleanup exceeded 120s — iteration cancelled"
                )
                outcome = "timeout"
                iteration_error_type = "timeout"
            except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
                # RESILIENCE FIX: Temporary DB failures don't crash the task
                logger.warning(f"fast_expiry_cleanup: Database temporarily unavailable in task loop: {type(e).__name__}: {str(e)[:100]}")
                outcome = "degraded"
                iteration_error_type = "infra_error"
            except Exception as e:
                logger.error(f"fast_expiry_cleanup: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
                logger.debug("fast_expiry_cleanup: Full traceback for task loop", exc_info=True)
                outcome = "failed"
                iteration_error_type = classify_error(e)
                try:
                    from app.services.admin_alerts import alert_worker_failure
                    await alert_worker_failure(bot, "fast_expiry_cleanup", e, iteration=iteration_number)
                except Exception:
                    pass
            finally:
                # H2 fix: ITERATION_END always fires in finally block
                duration_ms = (time.time() - iteration_start_time) * 1000
                log_worker_iteration_end(
                    worker_name="fast_expiry_cleanup",
                    outcome=outcome,
                    items_processed=items_processed,
                    error_type=iteration_error_type,
                    duration_ms=duration_ms,
                    reason=iteration_reason,
                    items_examined=items_processed,
                    items_revoked=items_revoked,
                )
                if outcome not in ("success", "cancelled", "skipped"):
                    await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
            
        except asyncio.CancelledError:
            logger.info("Fast expiry cleanup task cancelled")
            raise
        
        # Sleep after iteration completes (outside try/finally)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


