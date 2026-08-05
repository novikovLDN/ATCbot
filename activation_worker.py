"""Модуль для активации отложенных VPN подписок"""
import asyncio
import html
import logging
import os
import re
import time
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
import admin_notifications
from app.services.activation import service as activation_service
from app.services.activation.exceptions import (
    ActivationServiceError,
    ActivationNotAllowedError,
    ActivationMaxAttemptsReachedError,
    ActivationFailedError,
    VPNActivationError,
)
from app.services.language_service import resolve_user_language
from app import i18n
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.structured_logger import log_event
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection
from app.core.worker_startup import startup_jitter

logger = logging.getLogger(__name__)

# Потолок времени на одну итерацию — защита event loop от долгой блокировки.
#
# Было 15 с. Вместе с безусловным sleep(0.5) на каждый элемент это давало
# около 30 активаций за итерацию и не больше ~360 в час: очередь из 500
# оплаченных подписок разгребалась полтора часа. Sleep убран (см. ниже),
# бюджет поднят до 60 с — он по-прежнему втрое меньше внешнего
# asyncio.wait_for(..., timeout=120) вокруг итерации, так что раньше сработает
# именно этот мягкий выход с логом, а не жёсткая отмена задачи.
MAX_ITERATION_SECONDS = int(os.getenv("ACTIVATION_WORKER_MAX_ITERATION_SECONDS", "60"))

# Пауза после отправки сообщения пользователю. Нужна только из-за лимита
# Telegram (20 сообщений в секунду) и потому платится ТОЛЬКО когда сообщение
# действительно ушло — а не на каждой подписке очереди.
NOTIFICATION_PAUSE_SECONDS = 0.05

# Сколько подписок берём за виток. Было жёстко 50 — при интервале в 5 минут
# это потолок 600 активаций в час независимо от того, как быстро работает
# панель. Столько же строк, сколько раньше, при затыке просто не успеет
# обработаться, и остаток уйдёт в следующий виток (лог ACTIVATION_QUEUE_BACKLOG).
ACTIVATION_BATCH_LIMIT = int(os.getenv("ACTIVATION_BATCH_LIMIT", "200"))

_worker_lock = asyncio.Lock()

# Разметка Markdown в ключах admin.activation_error_*: **жирный** и `код`.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_CODE_RE = re.compile(r"`([^`]+)`")


def _admin_line(lang: str, key: str, **values) -> str:
    """Собрать строку админского алерта об ошибке активации в HTML.

    Ключи admin.activation_error_* писались под Markdown. Разметку переводим
    в HTML на СЫРОМ шаблоне — до подстановки значений, а сами значения
    экранируем через html.escape.

    Порядок здесь и есть суть фикса. Текст исключения сплошь и рядом содержит
    обратную кавычку, звёздочку или подчёркивание ('connect_timeout',
    'field *uuid* invalid'). Раньше всё это уезжало в Telegram с
    parse_mode="Markdown" как есть: разметка ломалась, Telegram отвечал
    BadRequest, safe_send_message молча возвращал None — и админ не узнавал,
    что оплаченная подписка окончательно помечена failed. Деньги получены,
    доступ не выдан, никакого сигнала.
    """
    template = i18n.get_text(lang, key)
    template = _MD_BOLD_RE.sub(r"<b>\1</b>", template)
    template = _MD_CODE_RE.sub(r"<code>\1</code>", template)
    if not values:
        return template
    safe_values = {name: html.escape(str(value)) for name, value in values.items()}
    try:
        return template.format(**safe_values)
    except (KeyError, IndexError, ValueError):
        # Опечатка в плейсхолдере перевода не должна съедать весь алерт:
        # лучше строка с фигурными скобками, чем молчание.
        logger.warning("ADMIN_ALERT_TEMPLATE_BROKEN key=%s lang=%s", key, lang)
        return template

# Конфигурация интервала проверки активации (по умолчанию 5 минут)
ACTIVATION_INTERVAL_SECONDS = int(os.getenv("ACTIVATION_INTERVAL_SECONDS", "300"))  # 5 минут
if ACTIVATION_INTERVAL_SECONDS < 60:  # Минимум 1 минута
    ACTIVATION_INTERVAL_SECONDS = 60
if ACTIVATION_INTERVAL_SECONDS > 1800:  # Максимум 30 минут
    ACTIVATION_INTERVAL_SECONDS = 1800

# Максимальное количество попыток активации (используется для логирования)
MAX_ACTIVATION_ATTEMPTS = activation_service.get_max_activation_attempts()

async def process_pending_activations(bot: Bot) -> tuple[int, str, str | None]:
    """
    Обработать подписки с отложенной активацией (activation_status='pending')

    ИНВАРИАНТЫ:
    - НЕ трогаем payments
    - НЕ трогаем expires_at
    - НЕ создаём новые подписки
    - НЕ дублируем UUID
    - Только перевод состояния: pending -> active или failed
    
    STEP 1.2 - BACKGROUND WORKERS CONTRACT:
    - Each iteration is stateless → no in-memory state across iterations
    - Each iteration may be safely skipped → no side effects if skipped
    - No unbounded retries → max_attempts enforced by activation_service
    - Errors do NOT kill the loop → exceptions caught at task level
    - All external calls guarded by retry_async → transient errors retried
    
    STEP 1.3 - EXTERNAL DEPENDENCIES POLICY:
    - DB unavailable → function returns early (no error raised)
    - VPN API unavailable → activation skipped, subscription remains 'pending'
    - VPN API disabled → activation skipped, subscription remains 'pending' (NOT error)
    - Domain exceptions (ActivationServiceError) → NOT retried, logged and handled
    
    Args:
        bot: Экземпляр Telegram бота для отправки уведомлений
    
    Returns:
        Кортеж (items_processed, outcome, error_type).

        outcome — "success" | "degraded" | "failed" | "skipped".
        error_type — таксономия сбоя из classify_error (infra_error /
        dependency_error / domain_error / unexpected_error) или None, если
        итерация прошла без ошибки.

        Третий элемент появился не для красоты: раньше тип ошибки считался
        здесь же, в except, и тут же выбрасывался — наружу уезжал только
        outcome="failed". В метриках все сбои выглядели одинаково, и отличить
        «база отвалилась» от «баг в коде» по ITERATION_END было нельзя.
    """
    if not database.DB_READY:
        logger.debug("Skipping activation worker: DB not ready")
        return (0, "skipped", None)

    if not config.VPN_ENABLED:
        logger.debug("Skipping activation worker: VPN API not enabled")
        return (0, "skipped", None)

    # RESILIENCE FIX: Handle temporary DB unavailability gracefully
    try:
        pool = await database.get_pool()
        if pool is None:
            logger.warning("Activation worker: Cannot get DB pool")
            return (0, "skipped", None)
    except (asyncpg.PostgresError, asyncio.TimeoutError, RuntimeError) as e:
        logger.warning(f"activation_worker: Database temporarily unavailable (pool acquisition failed): {type(e).__name__}: {str(e)[:100]}")
        return (0, "skipped", None)
    except Exception as e:
        logger.error(f"activation_worker: Unexpected error getting DB pool: {type(e).__name__}: {str(e)[:100]}")
        return (0, "failed", classify_error(e))
    
    items_processed = 0
    outcome = "success"
    
    try:
        # Fetch pending list with one short-lived connection (no sleep while holding conn)
        async with acquire_connection(pool, "activation_fetch_pending") as conn:
            pending_subscriptions = await activation_service.get_pending_subscriptions(
                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                limit=ACTIVATION_BATCH_LIMIT,
                conn=conn
            )
            pending_for_notification = await activation_service.get_pending_for_notification(
                threshold_minutes=activation_service.get_notification_threshold_minutes(),
                conn=conn
            )
            if pending_for_notification:
                total_pending_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM subscriptions WHERE activation_status = 'pending'"
                ) or 0
                if total_pending_count > 0:
                    await admin_notifications.notify_admin_pending_activations(
                        bot,
                        total_pending_count,
                        pending_for_notification
                    )
        # conn released here

        if not pending_subscriptions:
            logger.debug("No pending activations found")
            return (0, "success", None)

        logger.info(f"Found {len(pending_subscriptions)} pending activations to process")
        iteration_start = time.monotonic()

        for pending_sub in pending_subscriptions:
            # Раньше здесь был отдельный cooperative_yield каждые 50 записей.
            # Теперь управление отдаётся в конце каждого витка, так что
            # промежуточная страховка не нужна.
            if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
                logger.warning("Activation worker iteration time limit reached, breaking early")
                break
            items_processed += 1
            # Ушло ли в этой записи сообщение в Telegram. От этого зависит,
            # платим ли мы паузу в конце витка (см. NOTIFICATION_PAUSE_SECONDS).
            telegram_message_sent = False
            telegram_id = pending_sub.telegram_id
            subscription_id = pending_sub.subscription_id
            current_attempts = pending_sub.activation_attempts
            expires_at = pending_sub.expires_at

            # POOL STABILITY: attempt_activation uses pool and does not hold conn during HTTP.
            if activation_service.is_subscription_expired(expires_at):
                logger.warning(
                    f"ACTIVATION_SKIP_EXPIRED [subscription_id={subscription_id}, "
                    f"user={telegram_id}, expires_at={expires_at.isoformat() if expires_at else 'N/A'}]"
                )
                try:
                    async with acquire_connection(pool, "activation_mark_expired") as conn:
                        await activation_service.mark_expired_subscription_failed(
                            subscription_id,
                            conn=conn
                        )
                except Exception as e:
                    logger.critical(f"ACTIVATION_DB_FAILURE: Failed to mark expired subscription as failed: sub={subscription_id}, error={e}")
                    from app.services.admin_alerts import alert_subscription_failure
                    await alert_subscription_failure(
                        bot, telegram_id, f"mark_expired_failed (sub={subscription_id})", e,
                        amount_rubles=pending_sub.amount_rubles,
                        tariff=pending_sub.subscription_type,
                        period_days=pending_sub.period_days,
                        subscription_id=subscription_id,
                    )
            else:
                logger.info(
                    f"ACTIVATION_RETRY_ATTEMPT [subscription_id={subscription_id}, "
                    f"user={telegram_id}, attempt={current_attempts + 1}/{MAX_ACTIVATION_ATTEMPTS}]"
                )
                try:
                    activation_start_time = time.time()
                    result = await activation_service.attempt_activation(
                        subscription_id=subscription_id,
                        telegram_id=telegram_id,
                        current_attempts=current_attempts,
                        pool=pool
                    )
                    activation_duration_ms = (time.time() - activation_start_time) * 1000
                    uuid_preview = f"{result.uuid[:8]}..." if result.uuid and len(result.uuid) > 8 else (result.uuid or "N/A")
                    logger.info(
                        f"ACTIVATION_SUCCESS [subscription_id={subscription_id}, "
                        f"user={telegram_id}, uuid={uuid_preview}, attempt={result.attempts}, "
                        f"latency_ms={activation_duration_ms:.2f}]"
                    )

                    # Комбо: подписка выдана с опозданием — гигабайты обхода
                    # тоже. Пути оплаты при отложенной активации их НЕ
                    # начисляют (панель, из-за которой активация и отложена,
                    # начисление всё равно бы не приняла), поэтому здесь
                    # единственное место, где комбо-покупатель их получает.
                    #
                    # Признак и срок берутся из оплаченной покупки в базе, а
                    # не из FSM: между оплатой и активацией проходят минуты,
                    # и никакого состояния в памяти уже нет. Объём считает
                    # app/services/combo_traffic — единственный источник.
                    if pending_sub.is_combo:
                        try:
                            from app.services.combo_traffic import grant_combo_traffic
                            await grant_combo_traffic(
                                telegram_id,
                                pending_sub.subscription_type,
                                pending_sub.period_days,
                                is_combo=True,
                                purchase_id=f"subscription_{subscription_id}",
                                subscription_end=expires_at,
                                source="activation_worker",
                            )
                        except Exception as combo_err:
                            logger.error(
                                "COMBO_TRAFFIC_UNEXPECTED [subscription_id=%s, user=%s]: %s "
                                "— подписка активирована, гигабайты не начислены",
                                subscription_id, telegram_id, combo_err,
                            )

                    try:
                        async with acquire_connection(pool, "activation_notification_check") as conn:
                            subscription_check = await conn.fetchrow(
                                "SELECT activation_status, uuid, subscription_type FROM subscriptions WHERE id = $1",
                                subscription_id
                            )
                        if not subscription_check or subscription_check["activation_status"] != "active":
                            logger.warning(
                                f"ACTIVATION_NOTIFICATION_SKIP [subscription_id={subscription_id}, "
                                f"user={telegram_id}, reason=subscription_not_active]"
                            )
                        elif subscription_check.get("uuid") != result.uuid:
                            logger.info(
                                f"ACTIVATION_NOTIFICATION_SKIP_IDEMPOTENT [subscription_id={subscription_id}, "
                                f"user={telegram_id}, reason=already_notified]"
                            )
                        else:
                            from app.handlers.common.keyboards import get_connect_keyboard
                            language = await resolve_user_language(telegram_id)
                            expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "N/A"
                            sub_type = (subscription_check.get("subscription_type") or "basic").strip().lower()
                            if sub_type not in config.VALID_SUBSCRIPTION_TYPES:
                                sub_type = "basic"
                            if config.is_biz_tariff(sub_type):
                                tariff_label = "Business"
                            elif sub_type == "plus":
                                tariff_label = "Plus"
                            else:
                                tariff_label = "Basic"
                            tariff_emoji = "🏢" if config.is_biz_tariff(sub_type) else ("⭐️" if sub_type == "plus" else "📦")
                            text = (
                                "🎉 <b>Подписка активирована!</b>\n\n"
                                f"⚡ {tariff_label} · до {expires_str}\n\n"
                                "Нажмите кнопку ниже чтобы подключить устройство."
                            )
                            keyboard = get_connect_keyboard()
                            sent = await safe_send_message(
                                bot, telegram_id, text,
                                reply_markup=keyboard, parse_mode="HTML"
                            )
                            # Пауза Telegram платится только за доставленное
                            # сообщение: у заблокировавшего бота пользователя
                            # ждать нечего.
                            telegram_message_sent = sent is not None
                            # Исход берётся из ответа safe_send_message, а не из
                            # того, что отправку запустили. Она возвращает None,
                            # когда человек заблокировал бота или чат недоступен,
                            # — и раньше в этом случае в логе всё равно стояло
                            # ACTIVATION_NOTIFICATION_SENT. Подписка активирована,
                            # ключ выдан, человек об этом не знает, а по логам
                            # выходило, что знает: обращение «оплатил, ничего не
                            # пришло» упиралось в запись, говорящую обратное.
                            if telegram_message_sent:
                                logger.info(
                                    f"ACTIVATION_NOTIFICATION_SENT [subscription_id={subscription_id}, user={telegram_id}]"
                                )
                            else:
                                logger.warning(
                                    "ACTIVATION_NOTIFICATION_UNDELIVERED "
                                    "[subscription_id=%s, user=%s] — подписка активна, "
                                    "человек уведомления не получил",
                                    subscription_id, telegram_id,
                                )
                    except Exception as e:
                        logger.error(
                            f"Failed to send activation notification to user {telegram_id}: {e}"
                            )
                except VPNActivationError as e:
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    # Раньше здесь было три ветки логирования и try/except
                    # вокруг двух присваиваний, которые не могут упасть.
                    # Средняя ветка (ACTIVATION_SKIP_VPN_UNAVAILABLE) была
                    # недостижима: флаг «панель временно недоступна» жёстко
                    # выставлялся в False после снятия SystemState, а except
                    # никогда не срабатывал. Осталось то, что действительно
                    # различимо: выдача выключена конфигом или сбой панели.
                    if not config.VPN_ENABLED:
                        logger.warning(
                            f"ACTIVATION_FAILED_VPN_DISABLED [subscription_id={subscription_id}, "
                            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                            f"error={error_msg}]"
                        )
                    else:
                        logger.warning(
                            f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                            f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                            f"error={error_msg}]"
                        )
                    try:
                        # Исчерпание попыток — достаточное основание пометить
                        # активацию проваленной, какой бы ни была причина.
                        # Раньше требовалось ещё и vpn_api_permanently_disabled,
                        # то есть при обычной ошибке VPN API (сам VPN включён)
                        # подписка навсегда оставалась в статусе pending: деньги
                        # получены, доступ не выдан, финального алерта нет и
                        # никто об этом не узнаёт.
                        should_mark_failed = new_attempts >= MAX_ACTIVATION_ATTEMPTS
                        async with acquire_connection(pool, "activation_mark_failed") as conn:
                            await activation_service.mark_activation_failed(
                                subscription_id=subscription_id,
                                new_attempts=new_attempts,
                                error_msg=error_msg,
                                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                                conn=conn,
                                mark_as_failed=should_mark_failed
                            )
                        if should_mark_failed:
                            logger.error(
                                f"ACTIVATION_FAILED_FINAL [subscription_id={subscription_id}, "
                                f"user={telegram_id}, attempts={new_attempts}, error={error_msg}]"
                            )
                            try:
                                admin_lang = "ru"
                                from datetime import datetime, timezone as _tz
                                _now_str = datetime.now(_tz.utc).strftime("%d.%m.%Y %H:%M UTC")
                                # Текст ошибки режем: в него попадают ответы
                                # HTTP-клиентов целиком, а у Telegram лимит 4096.
                                _error_text = error_msg[:500]
                                _tariff = html.escape(str(pending_sub.subscription_type or "N/A"))
                                _tariff_line = f"\nТариф: {_tariff}"
                                _amount_line = f"\nСумма: {pending_sub.amount_rubles} RUB" if pending_sub.amount_rubles else ""
                                _period_line = f"\nСрок: {pending_sub.period_days} дн." if pending_sub.period_days else ""
                                admin_message = (
                                    f"{_admin_line(admin_lang, 'admin.activation_error_title')}\n\n"
                                    f"Дата: {_now_str}\n"
                                    f"{_admin_line(admin_lang, 'admin.activation_error_subscription_id', subscription_id=subscription_id)}\n"
                                    f"{_admin_line(admin_lang, 'admin.activation_error_user', telegram_id=telegram_id)}"
                                    f"{_tariff_line}{_amount_line}{_period_line}\n"
                                    f"{_admin_line(admin_lang, 'admin.activation_error_attempts', attempts=new_attempts, max_attempts=MAX_ACTIVATION_ATTEMPTS)}\n"
                                    f"{_admin_line(admin_lang, 'admin.activation_error_error', error_msg=_error_text)}\n\n"
                                    f"{_admin_line(admin_lang, 'admin.activation_error_status')}\n"
                                    f"{_admin_line(admin_lang, 'admin.activation_error_action')}"
                                )
                                if await safe_send_message(
                                    bot, config.ADMIN_TELEGRAM_ID,
                                    admin_message, parse_mode="HTML"
                                ):
                                    telegram_message_sent = True
                                    logger.info(
                                        f"Admin notification sent: Activation failed for subscription {subscription_id}"
                                    )
                            except Exception as admin_error:
                                logger.error(
                                    f"Failed to send admin notification: {admin_error}"
                                )
                    except Exception as db_error:
                        logger.critical(
                            f"ACTIVATION_DB_FAILURE: Failed to update activation attempts: "
                            f"subscription_id={subscription_id}, user={telegram_id}, error={db_error}"
                        )
                        from app.services.admin_alerts import alert_subscription_failure
                        await alert_subscription_failure(
                            bot, telegram_id, f"activation_db_update (sub={subscription_id})", db_error,
                            amount_rubles=pending_sub.amount_rubles,
                            tariff=pending_sub.subscription_type,
                            period_days=pending_sub.period_days,
                            subscription_id=subscription_id,
                        )
                except ActivationFailedError as e:
                    error_msg = str(e)
                    new_attempts = current_attempts + 1
                    logger.warning(
                        f"ACTIVATION_FAILED [subscription_id={subscription_id}, "
                        f"user={telegram_id}, attempt={new_attempts}/{MAX_ACTIVATION_ATTEMPTS}, "
                        f"error={error_msg}]"
                    )
                    try:
                        async with acquire_connection(pool, "activation_mark_failed_2") as conn:
                            await activation_service.mark_activation_failed(
                                subscription_id=subscription_id,
                                new_attempts=new_attempts,
                                error_msg=error_msg,
                                max_attempts=MAX_ACTIVATION_ATTEMPTS,
                                conn=conn
                            )
                    except Exception as db_error:
                        logger.critical(
                            f"ACTIVATION_DB_FAILURE: Failed to update activation attempts: "
                            f"subscription_id={subscription_id}, user={telegram_id}, error={db_error}"
                        )
                        from app.services.admin_alerts import alert_subscription_failure
                        await alert_subscription_failure(
                            bot, telegram_id, f"activation_db_update_2 (sub={subscription_id})", db_error,
                            amount_rubles=pending_sub.amount_rubles,
                            tariff=pending_sub.subscription_type,
                            period_days=pending_sub.period_days,
                            subscription_id=subscription_id,
                        )

                except ActivationNotAllowedError as e:
                    # Активация запрещена для этой конкретной подписки: истекла,
                    # уже активна, отозвана. Это не сбой воркера, а состояние
                    # одной записи.
                    #
                    # Раньше исключение не перехватывалось и улетало во внешний
                    # except всей функции: обработка обрывалась, и все подписки
                    # из очереди после неё оставались неактивированными до
                    # следующего тика. Одна испорченная запись блокировала выдачу
                    # всем остальным.
                    logger.warning(
                        "ACTIVATION_NOT_ALLOWED [subscription_id=%s, user=%s, reason=%s] "
                        "— пропускаем эту подписку, очередь продолжается",
                        subscription_id, telegram_id, e,
                    )

                except ActivationServiceError as e:
                    # Любая другая доменная ошибка сервиса активации: логируем
                    # и идём дальше по очереди по той же причине.
                    logger.error(
                        "ACTIVATION_SERVICE_ERROR [subscription_id=%s, user=%s, error=%s] "
                        "— пропускаем эту подписку, очередь продолжается",
                        subscription_id, telegram_id, e,
                    )

            # Раньше здесь стоял безусловный await asyncio.sleep(0.5) на каждую
            # подписку. Он ни от чего не защищал: обращение к панели и так
            # последовательное, а сообщение уходит далеко не каждой записи.
            # Зато вместе с бюджетом итерации он и был тем самым потолком
            # ~30 активаций за виток.
            #
            # Теперь платим только за реально отправленное сообщение (лимит
            # Telegram), а в остальных случаях просто отдаём управление циклу
            # событий, чтобы длинная очередь не держала loop.
            if telegram_message_sent:
                await asyncio.sleep(NOTIFICATION_PAUSE_SECONDS)
            else:
                await cooperative_yield()

        if items_processed < len(pending_subscriptions):
            # Очередь не разобрана до конца — это и есть сигнал «не успеваем».
            # Без него всплеск покупок виден только по жалобам пользователей.
            logger.warning(
                "ACTIVATION_QUEUE_BACKLOG processed=%s of=%s — очередь не разобрана за итерацию",
                items_processed, len(pending_subscriptions),
            )
        log_event(
            logger,
            component="worker",
            operation="activation_queue_depth",
            outcome="success",
            reason=f"fetched={len(pending_subscriptions)} processed={items_processed}",
        )

        return (items_processed, outcome, None)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures are logged as WARNING, not ERROR
        logger.warning(f"activation_worker: Database temporarily unavailable in process_pending_activations: {type(e).__name__}: {str(e)[:100]}")
        return (items_processed, "degraded", classify_error(e))
    except Exception as e:
        logger.error(f"activation_worker: Unexpected error in process_pending_activations: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("activation_worker: Full traceback in process_pending_activations", exc_info=True)
        # Тип ошибки уезжает наверх третьим элементом кортежа. Раньше он
        # считался этой же строкой и терялся тут же: в ITERATION_END уходило
        # голое failed без таксономии.
        return (items_processed, "failed", classify_error(e))


async def activation_worker_task(bot: Bot):
    """
    Фоновая задача для периодической обработки отложенных активаций
    
    Args:
        bot: Экземпляр Telegram бота
    """
    logger.info(f"Activation worker task started (interval={ACTIVATION_INTERVAL_SECONDS}s, max_attempts={MAX_ACTIVATION_ATTEMPTS})")
    
    # Разброс старта — общее правило для всех воркеров, см. app/core/worker_startup.
    await startup_jitter("activation_worker")


    iteration_number = 0
    
    # STEP 3 — PART B: WORKER LOOP SAFETY
    # Minimum safe sleep on failure to prevent tight retry storms
    MINIMUM_SAFE_SLEEP_ON_FAILURE = 10  # seconds
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        
        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="activation_worker",
            iteration_number=iteration_number
        )
        
        items_processed = 0
        outcome = "success"
        iteration_error_type = None

        try:
            # Feature flag check
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Background workers disabled, skipping iteration in activation_worker "
                    f"(iteration={iteration_number})"
                )
                outcome = "skipped"
                reason = "background_workers_enabled=false"
                log_worker_iteration_end(
                    worker_name="activation_worker",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # Simple DB readiness check
            if not database.DB_READY:
                logger.warning("activation_worker: skipping — DB not ready")
                outcome = "skipped"
                reason = "DB not ready"
                log_worker_iteration_end(
                    worker_name="activation_worker",
                    outcome=outcome,
                    items_processed=0,
                    duration_ms=(time.time() - iteration_start_time) * 1000,
                    reason=reason,
                )
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue
            
            # H1 fix: Wrap iteration body with timeout
            async def _run_iteration():
                # Process pending activations (lock prevents overlapping iterations)
                async with _worker_lock:
                    return await process_pending_activations(bot)
            
            try:
                items_processed, outcome, iteration_error_type = await asyncio.wait_for(
                    _run_iteration(), timeout=120.0
                )
            except asyncio.TimeoutError:
                logger.error(
                    "WORKER_TIMEOUT worker=activation_worker exceeded 120s — iteration cancelled"
                )
                items_processed = 0
                outcome = "timeout"
                iteration_error_type = "timeout"
            except Exception as e:
                logger.exception(f"activation_worker: Unexpected error in iteration: {type(e).__name__}: {str(e)[:100]}")
                items_processed = 0
                outcome = "failed"
                iteration_error_type = classify_error(e)
            
        except asyncio.CancelledError:
            # Отмена остаётся отменой: логируем, закрываем итерацию в метриках
            # (это делает finally ниже) и перебрасываем.
            #
            # Раньше здесь стоял break без raise. Задача завершалась «штатно»,
            # и снаружи, в main.py, остановка по shutdown была неотличима от
            # «воркер сам решил выйти навсегда» — по логам причину не найти.
            # Плюс asyncio считает проглоченную отмену багом: задача, которую
            # попросили остановиться, обязана отмениться.
            logger.info("Activation worker task cancelled")
            outcome = "cancelled"
            raise
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"activation_worker: Database temporarily unavailable in task loop: {type(e).__name__}: {str(e)[:100]}")
            outcome = "degraded"
            iteration_error_type = "infra_error"
        except Exception as e:
            logger.error(f"activation_worker: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("activation_worker: Full traceback for task loop", exc_info=True)
            outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "activation_worker", e, iteration=iteration_number)
            except Exception:
                pass
        finally:
            # H2 fix: ITERATION_END always fires in finally block.
            #
            # Проверок вида `if 'iteration_error_type' in locals()` здесь
            # больше нет: обе переменные безусловно инициализируются в начале
            # витка, так что ветка «переменной нет» была недостижима и только
            # маскировала бы настоящую опечатку в имени.
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="activation_worker",
                outcome=outcome,
                items_processed=items_processed,
                error_type=iteration_error_type,
                duration_ms=duration_ms
            )
            # На пути отмены (outcome="cancelled") здесь не должно быть ни
            # одного await: пока finally спит, задача не завершается, и
            # shutdown ждёт её впустую до своего таймаута.
            if outcome not in ("success", "cancelled", "skipped"):
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)

        # Sleep after iteration completes (outside try/finally)
        await asyncio.sleep(ACTIVATION_INTERVAL_SECONDS)
