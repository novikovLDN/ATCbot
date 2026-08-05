"""Модуль для автопродления подписок с баланса"""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
from app import i18n
from app.constants import tariffs
from app.services.notifications import service as notification_service
from app.services.language_service import resolve_user_language
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection
from app.core.worker_startup import startup_jitter

logger = logging.getLogger(__name__)

# Event loop protection: max iteration time (prevents 300s blocking)
MAX_ITERATION_SECONDS = int(os.getenv("AUTO_RENEWAL_MAX_ITERATION_SECONDS", "15"))
# Hard timeout for entire iteration (prevents hung worker holding DB, avoids liveness watchdog)
ITERATION_HARD_TIMEOUT_SECONDS = 120.0
BATCH_SIZE = 100
_worker_lock = asyncio.Lock()

# Конфигурация интервала проверки автопродления (5-15 минут, по умолчанию 10 минут)
AUTO_RENEWAL_INTERVAL_SECONDS = int(os.getenv("AUTO_RENEWAL_INTERVAL_SECONDS", "600"))  # 10 минут
if AUTO_RENEWAL_INTERVAL_SECONDS < 300:  # Минимум 5 минут
    AUTO_RENEWAL_INTERVAL_SECONDS = 300
if AUTO_RENEWAL_INTERVAL_SECONDS > 900:  # Максимум 15 минут
    AUTO_RENEWAL_INTERVAL_SECONDS = 900

# Окно для автопродления: проверяем подписки, истекающие в течение этого времени (по умолчанию 6 часов)
RENEWAL_WINDOW_HOURS = int(os.getenv("RENEWAL_WINDOW_HOURS", "6"))
if RENEWAL_WINDOW_HOURS < 1:
    RENEWAL_WINDOW_HOURS = 1
RENEWAL_WINDOW = timedelta(hours=RENEWAL_WINDOW_HOURS)

# STEP 3 — PART B: WORKER LOOP SAFETY
#
# НИЖНЯЯ ГРАНИЦА паузы после сбойной итерации — защита от шторма повторов,
# когда база или панель лежат и каждая попытка падает мгновенно.
#
# Важно: это пол, а не добавка. Комментарий «half of AUTO_RENEWAL_INTERVAL_SECONDS
# minimum» вводил в заблуждение — 300 это и есть сам минимум интервала, а
# сложение с ним давало 600 с реальной паузы вместо заявленных 300.
# Применяется через max(), см. конец auto_renewal_task.
MINIMUM_SAFE_SLEEP_ON_FAILURE = 300  # seconds


class _RollbackUser(Exception):
    """Отменить обработку одного пользователя, откатив его savepoint.

    Нужна там, где мы уже что-то записали (last_auto_renewal_at), но решили
    не продлевать: обычный continue закоммитил бы запись и выключил
    пользователя из автопродления до следующего окна.
    """


async def process_auto_renewals(bot: Bot):
    """
    Обработать автопродление подписок, которые истекают в течение RENEWAL_WINDOW
    
    ТРЕБОВАНИЯ:
    - Подписки со status='active' и auto_renew=TRUE
    - subscription_end <= now + RENEWAL_WINDOW (по умолчанию 6 часов)
    - Проверяем баланс >= цена подписки
    - Если баланса хватает: продлеваем через grant_access() (без создания нового UUID)
    - Если баланса не хватает: ничего не делаем (auto-expiry обработает)
    
    Защита от race conditions:
    - SELECT ... FOR UPDATE SKIP LOCKED: только один воркер может обработать подписку
    - last_auto_renewal_at устанавливается в НАЧАЛЕ транзакции (до обработки)
    - При ошибке транзакция откатывается, last_auto_renewal_at возвращается к предыдущему значению
    - Идемпотентность: при рестарте не будет двойного списания
    - Атомарные транзакции для баланса и подписки
    """
    pool = await database.get_pool()
    now = datetime.now(timezone.utc)
    renewal_threshold = now + RENEWAL_WINDOW

    query_with_reachable = """
        SELECT s.*, u.balance
        FROM subscriptions s
        JOIN users u ON s.telegram_id = u.telegram_id
        WHERE s.status = 'active'
        AND s.auto_renew = TRUE
        AND s.expires_at <= $1
        AND s.expires_at > $2
        AND s.uuid IS NOT NULL
        AND COALESCE(u.is_reachable, TRUE) = TRUE
        AND (s.last_auto_renewal_at IS NULL OR s.last_auto_renewal_at < s.expires_at - INTERVAL '12 hours')
        ORDER BY s.id ASC
        LIMIT $3
        FOR UPDATE SKIP LOCKED"""
    fallback_query = """
        SELECT s.*, u.balance
        FROM subscriptions s
        JOIN users u ON s.telegram_id = u.telegram_id
        WHERE s.status = 'active'
        AND s.auto_renew = TRUE
        AND s.expires_at <= $1
        AND s.expires_at > $2
        AND s.uuid IS NOT NULL
        AND (s.last_auto_renewal_at IS NULL OR s.last_auto_renewal_at < s.expires_at - INTERVAL '12 hours')
        ORDER BY s.id ASC
        LIMIT $3
        FOR UPDATE SKIP LOCKED"""

    # Pool is created with acquire timeout in database._get_pool_config() (DB_POOL_ACQUIRE_TIMEOUT, default 10s).
    # This worker does not call VPN API (no httpx); only DB and Telegram.
    # Pool timeout is already configured (10s); acquire_connection uses pool.acquire() which respects that timeout.
    # For extra safety, we wrap acquire in wait_for to ensure cancellation if pool hangs.
    # Бюджет времени на весь проход, а не на один батч. Раньше отсчёт стоял
    # внутри цикла по батчам и обнулялся на каждой сотне подписок: заявленные
    # 15 секунд превращались в 15 секунд НА БАТЧ, и воркер мог держать
    # event loop минутами — ровно то, от чего лимит и должен защищать.
    iteration_start = time.monotonic()
    while True:
        if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
            logger.warning(
                "auto_renewal: бюджет прохода исчерпан, остальные подписки — "
                "в следующем цикле (они не потеряются: выборка идёт по окну "
                "истечения, а не по курсору)"
            )
            break
        cm = acquire_connection(pool, "auto_renewal_main")
        try:
            conn = await asyncio.wait_for(cm.__aenter__(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("auto_renewal: pool.acquire() timed out after 10s")
            raise
        try:
            notifications_to_send = []
            # Алерты админу копим и шлём в фазе B: сеть внутри транзакции
            # держит строки, взятые SELECT ... FOR UPDATE, заблокированными
            # на всё время обращения к Telegram.
            alerts_to_send = []
            async with conn.transaction():
                try:
                    subscriptions = await conn.fetch(
                        query_with_reachable,
                        database._to_db_utc(renewal_threshold),
                        database._to_db_utc(now),
                        BATCH_SIZE
                    )
                except asyncpg.UndefinedColumnError:
                    logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, auto_renewal fallback to legacy query")
                    subscriptions = await conn.fetch(
                        fallback_query,
                        database._to_db_utc(renewal_threshold),
                        database._to_db_utc(now),
                        BATCH_SIZE
                    )

                if not subscriptions:
                    break

                if not isinstance(subscriptions, list):
                    logger.error("auto_renewal unexpected fetch result (not list)")
                    break

                logger.info(
                    f"Auto-renewal check: Found {len(subscriptions)} subscriptions expiring within {RENEWAL_WINDOW_HOURS} hours"
                )

                for i, sub_row in enumerate(subscriptions):
                    if i > 0 and i % 50 == 0:
                        await cooperative_yield()
                    if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
                        logger.warning("Auto-renewal iteration time limit reached, breaking early")
                        break
                    telegram_id = sub_row["telegram_id"]
                    subscription = sub_row
                    # Здесь читался language из users и клался в payload фазы B.
                    # Он никогда не использовался: текст уведомления берёт язык
                    # через resolve_user_language (там ещё и фолбэки по
                    # language_code Telegram). Убрано, чтобы не выглядело
                    # источником языка — правка этой строки ни на что не влияла.
                    try:
                      # Вложенная транзакция = SAVEPOINT. Без неё ошибка Postgres
                      # на одном пользователе переводила соединение в failed-
                      # состояние: все следующие запросы падали, COMMIT падал, и
                      # откатывался ВЕСЬ батч до ста подписок — включая уже
                      # успешно продлённые. Теперь откатывается только виновник.
                      async with conn.transaction():
                        # КРИТИЧНО: Обновляем last_auto_renewal_at в НАЧАЛЕ транзакции
                        # Это предотвращает обработку одной подписки несколькими воркерами
                        # даже при рестарте или параллельных вызовах
                        update_result = await conn.execute(
                        """UPDATE subscriptions 
                           SET last_auto_renewal_at = $1 
                           WHERE telegram_id = $2 
                           AND status = 'active'
                           AND auto_renew = TRUE
                           AND (last_auto_renewal_at IS NULL OR last_auto_renewal_at < expires_at - INTERVAL '12 hours')""",
                        database._to_db_utc(now), telegram_id
                        )
                        
                        # Если UPDATE не затронул ни одной строки - подписка уже обрабатывается или не подходит
                        if update_result == "UPDATE 0":
                            logger.debug(f"Subscription {telegram_id} already being processed or conditions changed, skipping")
                            continue
                        
                        # Дополнительная проверка: убеждаемся, что подписка еще не была обработана
                        # (дополнительная защита от race condition)
                        current_sub = await conn.fetchrow(
                            """SELECT auto_renew, expires_at, last_auto_renewal_at 
                               FROM subscriptions 
                               WHERE telegram_id = $1""",
                            telegram_id
                        )
                        
                        if not current_sub or not current_sub["auto_renew"]:
                            logger.debug(f"Subscription {telegram_id} no longer has auto_renew enabled, skipping")
                            # Здесь нужен именно откат: last_auto_renewal_at мы уже
                            # проставили выше, а продления не будет. Обычный continue
                            # закоммитил бы savepoint и заблокировал пользователя до
                            # следующего окна. Комментарий обещал откат и раньше, но
                            # без вложенной транзакции откатывать было нечего.
                            raise _RollbackUser()
                        
                        # PHASE A: Только DB по conn — без вложенного pool.acquire и без сетевых вызовов
                        last_payment = await database.get_last_approved_payment(telegram_id, conn=conn)
                        
                        # Парсим тариф из последнего платежа
                        # Формат может быть: "basic_30", "plus_90" или legacy "1", "3", "6", "12"
                        if not last_payment:
                            tariff_type = "basic"
                            period_days = 30
                        else:
                            tariff_str = last_payment.get("tariff", "basic_30")
                            if "_" in tariff_str:
                                parts = tariff_str.split("_")
                                tariff_type = parts[0] if len(parts) > 0 else "basic"
                                try:
                                    period_days = int(parts[1]) if len(parts) > 1 else 30
                                except (ValueError, IndexError):
                                    period_days = 30
                            else:
                                tariff_type = "basic"
                                try:
                                    months = int(tariff_str)
                                    period_days = months * 30
                                except ValueError:
                                    period_days = 30
                        
                        if tariff_type not in config.TARIFFS or period_days not in config.TARIFFS[tariff_type]:
                            tariff_type = "basic"
                            period_days = 30

                        # Комбо-тариф: подписка вместе с пакетом ГБ обхода.
                        #
                        # Признак хранится отдельной колонкой subscriptions.is_combo,
                        # а subscription_type содержит базовый тариф ('plus'), потому
                        # что уровень доступа у комбо тот же. Автопродление про это не
                        # знало: списывало цену обычного тарифа (Комбо Plus на месяц
                        # стоит 499 ₽ против 449 ₽ у Plus) и не пополняло гигабайты
                        # обхода — человек платил за комбо, а получал голую подписку.
                        is_combo_sub = bool(subscription.get("is_combo"))
                        combo_key = f"combo_{tariff_type}" if is_combo_sub else None
                        combo_price = tariffs.combo_price_rubles(combo_key, period_days) if combo_key else None
                        if is_combo_sub and combo_price is None:
                            # Периода нет в таблице комбо (например, тариф купили
                            # ещё до его появления). Продлеваем как обычную
                            # подписку — это честнее, чем брать цену наугад.
                            logger.warning(
                                "AUTO_RENEWAL_COMBO_UNKNOWN_PERIOD user=%s combo=%s period_days=%s "
                                "— продлеваем как обычную подписку",
                                telegram_id, combo_key, period_days,
                            )
                            is_combo_sub = False

                        # Цену считает общий калькулятор, а не своя формула.
                        #
                        # Раньше здесь была отдельная лестница скидок: базовая
                        # цена из config.TARIFFS, вручную -30% для VIP, вручную
                        # персональная скидка. Она не знала про админские price
                        # override и глобальную скидку (миграция 069), поэтому
                        # автопродление списывало не ту сумму, которую человек
                        # видел на витрине: админ снижал цену — автопродление
                        # продолжало брать старую.
                        #
                        # Промокоды и спецпредложение сознательно не применяются:
                        # это разовые акции на конкретную покупку, а не свойство
                        # подписки. Автопродление берёт постоянную цену.
                        from app.services.subscriptions import service as _sub_service
                        _price = await _sub_service.calculate_price(
                            telegram_id=telegram_id,
                            tariff=tariff_type,
                            period_days=period_days,
                            # У комбо своя цена из config.COMBO_TARIFFS; скидки
                            # калькулятор применит поверх неё, как и при покупке.
                            base_price_override_rubles=combo_price,
                        )
                        amount_rubles = round(_price["final_price_kopecks"] / 100.0, 2)


                        user_balance_kopecks = subscription.get("balance", 0) or 0
                        balance_rubles = user_balance_kopecks / 100.0
                        
                        if balance_rubles >= amount_rubles:
                            duration = timedelta(days=period_days)
                            months = period_days // 30
                            # Название для строки в истории баланса. У комбо
                            # оно должно отличаться: человек ищет в выписке
                            # именно то, за что заплатил.
                            tariff_name = tariffs.display_name(
                                tariff_type, is_combo=is_combo_sub,
                            )
                            success = await database.decrease_balance(
                                telegram_id=telegram_id,
                                amount=amount_rubles,
                                source="auto_renew",
                                description=f"Автопродление подписки {tariff_name} на {months} месяц(ев)",
                                conn=conn
                            )
                            
                            if not success:
                                # В записи не было ни суммы, ни баланса — то есть
                                # ПОЧЕМУ отказано, восстановить было нельзя.
                                logger.error(
                                    "AUTO_RENEWAL_DEBIT_FAILED user=%s tariff=%s amount=%s ₽ "
                                    "balance=%s ₽ — списание не прошло, подписка не продлена",
                                    telegram_id, tariff_type, amount_rubles, balance_rubles,
                                )
                                continue
                            
                            result = await database.grant_access(
                                telegram_id=telegram_id,
                                duration=duration,
                                source="auto_renew",
                                admin_telegram_id=None,
                                admin_grant_days=None,
                                conn=conn
                            )
                            
                            expires_at = result["subscription_end"]
                            action_type = result.get("action", "unknown")
                            
                            if action_type != "renewal" or result.get("vless_url") is not None:
                                logger.error(
                                    f"Auto-renewal ERROR: UUID was regenerated instead of renewal! "
                                    f"user={telegram_id}, action={action_type}, has_vless_url={result.get('vless_url') is not None}"
                                )
                                refund_ok = await database.increase_balance(
                                    telegram_id=telegram_id,
                                    amount=amount_rubles,
                                    source="refund",
                                    description=f"Возврат средств: ошибка автопродления (UUID пересоздан)",
                                    conn=conn
                                )
                                if not refund_ok:
                                    logger.critical(
                                        f"REFUND_FAILED: user={telegram_id}, amount={amount_rubles} RUB, "
                                        f"reason=UUID_regenerated, refund_returned=False"
                                    )
                                    alerts_to_send.append({
                                        "kind": "payment_failure",
                                        "telegram_id": telegram_id,
                                        "purchase_id": f"refund_uuid_regen_{telegram_id}",
                                        "error": RuntimeError(
                                            f"Refund failed after UUID regeneration, amount={amount_rubles}"
                                        ),
                                        "amount_rubles": amount_rubles,
                                        "tariff": tariff_type,
                                        "period_days": period_days,
                                    })
                                continue
                            
                            subscription_row = await conn.fetchrow(
                                "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                                telegram_id
                            )
                            vpn_key = None
                            if subscription_row and subscription_row.get("vpn_key"):
                                vpn_key = subscription_row["vpn_key"]
                            else:
                                vpn_key = result.get("uuid", "")
                            
                            if expires_at is None:
                                logger.error(f"Failed to renew subscription for auto-renewal: user={telegram_id}, expires_at=None")
                                refund_ok = await database.increase_balance(
                                    telegram_id=telegram_id,
                                    amount=amount_rubles,
                                    source="refund",
                                    description=f"Возврат средств за неудачное автопродление",
                                    conn=conn
                                )
                                if not refund_ok:
                                    logger.critical(
                                        f"REFUND_FAILED: user={telegram_id}, amount={amount_rubles} RUB, "
                                        f"reason=expires_at_None, refund_returned=False"
                                    )
                                    alerts_to_send.append({
                                        "kind": "payment_failure",
                                        "telegram_id": telegram_id,
                                        "purchase_id": f"refund_renewal_fail_{telegram_id}",
                                        "error": RuntimeError(
                                            f"Refund failed after renewal failure, amount={amount_rubles}"
                                        ),
                                        "amount_rubles": amount_rubles,
                                        "tariff": tariff_type,
                                        "period_days": period_days,
                                    })
                                continue
                            
                            tariff_str = f"{tariff_type}_{period_days}"
                            # payment_provider='balance': автопродление всегда
                            # списывает с баланса — это внутреннее движение уже
                            # учтённых денег, а не новая выручка (см. миграцию 072).
                            payment_id = await conn.fetchval(
                                """INSERT INTO payments (telegram_id, tariff, amount, status, payment_provider)
                                   VALUES ($1, $2, $3, 'approved', 'balance') RETURNING id""",
                                telegram_id, tariff_str, round(amount_rubles * 100)
                            )
                            
                            if not payment_id:
                                # Худшая точка разрыва: деньги УЖЕ списаны и
                                # grant_access уже выполнен, а continue коммитит
                                # savepoint. Человек не попадает в
                                # notifications_to_send, значит фаза B его не
                                # тронет: в панели не продлят и не уведомят.
                                # Прежний текст («Failed to create payment
                                # record») читался как безобидный сбой вставки.
                                logger.critical(
                                    "AUTO_RENEWAL_PAYMENT_RECORD_LOST user=%s tariff=%s "
                                    "amount=%s ₽ — деньги списаны, доступ в базе продлён, "
                                    "записи о платеже нет; продление в панели и уведомление "
                                    "НЕ состоятся, возврата не было",
                                    telegram_id, tariff_type, amount_rubles,
                                )
                                continue
                            
                            notification_already_sent = await notification_service.check_notification_idempotency(
                                payment_id, conn=conn
                            )
                            if notification_already_sent:
                                # continue пропускает не только уведомление:
                                # человек не попадает в notifications_to_send, а
                                # значит фаза B не продлит его и в панели. Текст
                                # говорил только про уведомления, а уровень info
                                # не отражал того, что деньги списаны, а панель
                                # не тронута.
                                logger.warning(
                                    "AUTO_RENEWAL_SKIPPED_ALREADY_NOTIFIED payment_id=%s user=%s — "
                                    "деньги списаны, база продлена, но фаза B пропущена: "
                                    "продления в панели и уведомления не будет",
                                    payment_id, telegram_id,
                                )
                                continue

                            expires_str = expires_at.strftime("%d.%m.%Y")
                            duration_days = duration.days
                            # Собираем payload для Phase B (после commit) — без Telegram и без вложенного acquire
                            notifications_to_send.append({
                                "telegram_id": telegram_id,
                                "payment_id": payment_id,
                                "expires_str": expires_str,
                                "expires_at": expires_at,
                                "duration_days": duration_days,
                                "amount_rubles": amount_rubles,
                                # Без этого ключа ветки «Комбо Plus»/«Комбо Basic»
                                # в тексте уведомления были недостижимы, и
                                # комбо-подписчик читал про обычный тариф.
                                "is_combo": is_combo_sub,
                                "tariff_type": tariff_type,
                                "period_days": period_days,
                            })
                            # «successful» относилось ко всему продлению, хотя на
                            # этот момент сделана только фаза A: списание и
                            # запись в базу. Панель трогает фаза B, уже после
                            # коммита, и она может не состояться целиком — а в
                            # логе всё равно оставалось «Auto-renewal
                            # successful». payment_id добавлен, чтобы эта строка
                            # сшивалась с NOTIFICATION_SENT из фазы B: раньше их
                            # можно было связать только по времени.
                            logger.info(
                                "AUTO_RENEWAL_CHARGED user=%s payment_id=%s tariff=%s "
                                "period_days=%s amount=%s RUB expires_at=%s — "
                                "деньги списаны и база продлена, панель обновляется в фазе B",
                                telegram_id, payment_id, tariff_type, period_days,
                                amount_rubles, expires_str,
                            )

                        else:
                            # Главная штатная причина отказа стояла на debug, а
                            # root-логгер настроен на INFO — то есть в проде её
                            # не было видно. При этом last_auto_renewal_at уже
                            # проставлен, человек выключен из автопродления на
                            # 12 часов и подписка истечёт: на вопрос «почему не
                            # продлили» ответа в логах не оставалось.
                            logger.info(
                                "AUTO_RENEWAL_INSUFFICIENT_BALANCE user=%s balance=%.2f RUB "
                                "required=%.2f RUB — подписка не продлена",
                                telegram_id, balance_rubles, amount_rubles,
                            )
                    
                    except _RollbackUser:
                        # Штатный отказ от продления: savepoint откачен, идём дальше.
                        continue
                    except Exception as e:
                        logger.exception(f"Error processing auto-renewal for user {telegram_id}: {e}")
                        # Алерт откладываем в фазу B: сеть внутри транзакции держит
                        # заблокированными строки всего батча.
                        alerts_to_send.append({
                            "kind": "error",
                            "telegram_id": telegram_id,
                            "text": (
                                f"Auto-renewal processing error\n"
                                f"User: {telegram_id}\n"
                                f"Error: {type(e).__name__}: {str(e)[:200]}"
                            ),
                        })

            # PHASE B: после commit — алерты админу, xray sync и уведомления.
            # Здесь транзакция уже закрыта, строки разблокированы, поэтому
            # обращения к Telegram никого не задерживают.
            for alert in alerts_to_send:
                try:
                    if alert["kind"] == "payment_failure":
                        from app.services.admin_alerts import alert_payment_failure
                        await alert_payment_failure(
                            bot, "auto_renewal", alert["telegram_id"],
                            alert["purchase_id"], alert["error"],
                            is_transient=False,
                            amount_rubles=alert["amount_rubles"],
                            tariff=alert["tariff"],
                            period_days=alert["period_days"],
                        )
                    else:
                        from app.services.admin_alerts import send_alert
                        await send_alert(bot, "payment", alert["text"])
                except Exception as alert_err:
                    logger.warning("auto_renewal: не удалось отправить алерт: %s", alert_err)

            # PHASE B: после commit — продление в панели и уведомления
            # (никаких финансовых мутаций: деньги уже списаны в фазе A).
            for item in notifications_to_send:
                # Здесь был блок синхронизации с xray. Он вызывал
                # vpn_utils.ensure_user_in_xray — заглушку, которая после
                # снятия samopis только пишет строчку в лог. Настоящее
                # продление идёт ниже, через Remnawave.
                #
                # Продление в панели. У комбо это не просто продление срока:
                # вместе с подпиской человек оплатил пакет ГБ обхода, и его
                # нужно начислить на новый период — ровно как при обычной
                # покупке комбо (см. payments_messages.py). Раньше этого не
                # происходило вовсе: комбо-подписчик платил за комбо и получал
                # голую подписку без гигабайтов.
                _ar_tariff = item.get("tariff_type", "basic")
                _ar_expires = item.get("expires_at")
                _ar_combo = bool(item.get("is_combo"))
                try:
                    if _ar_combo and _ar_expires:
                        # Объём пакета считает app/services/combo_traffic —
                        # то же место, что и при обычной покупке комбо. Своей
                        # копии расчёта здесь быть не должно: расходились они
                        # молча, и заметить это можно было только по жалобе.
                        from app.services.combo_traffic import grant_combo_traffic
                        await grant_combo_traffic(
                            item["telegram_id"],
                            _ar_tariff,
                            item.get("period_days", 30),
                            is_combo=True,
                            purchase_id=f"auto_renewal_{item['payment_id']}",
                            subscription_end=_ar_expires,
                            source="auto_renewal",
                        )
                    elif _ar_tariff in ("basic", "plus") and _ar_expires:
                        from app.services.remnawave_service import renew_remnawave_user_bg
                        renew_remnawave_user_bg(
                            item["telegram_id"], _ar_tariff, _ar_expires,
                            period_days=item.get("period_days", 30),
                        )
                except Exception as rmn_err:
                    logger.warning("REMNAWAVE_AUTORENEW_FAIL: tg=%s %s", item["telegram_id"], rmn_err)

                try:
                    _ar_is_combo = item.get("is_combo", False)
                    _ar_type = item.get("tariff_type", "basic")
                    if _ar_is_combo and _ar_type == "plus":
                        tariff_label, tariff_emoji = "Комбо Plus", "🚀"
                    elif _ar_is_combo:
                        tariff_label, tariff_emoji = "Комбо Basic", "🚀"
                    elif _ar_type == "plus":
                        tariff_label, tariff_emoji = "Plus", "⭐️"
                    else:
                        tariff_label, tariff_emoji = "Basic", "📦"
                    user_lang = await resolve_user_language(item["telegram_id"])
                    # В payload сумма лежит под ключом amount_rubles и уже в
                    # рублях. Раньше читался несуществующий ключ "amount", поэтому
                    # в тексте всегда стоял ноль: «списано 0 ₽». Эвристику
                    # «больше 1000 — значит копейки» убрали: годовая подписка
                    # дороже 1000 ₽, и она честную сумму делила на сто.
                    text = i18n.get_text(
                        user_lang, "purchase.auto_renewal_success",
                        tariff_name=f"{tariff_emoji} {tariff_label}",
                        days=item.get("period_days", 30),
                        expires_date=item["expires_str"],
                        amount=item.get("amount_rubles", 0),
                    )
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile")],
                    ])
                    sent = await safe_send_message(bot, item["telegram_id"], text, reply_markup=keyboard)
                    if sent is None:
                        # Полностью немой путь: результат проверялся и молча
                        # выбрасывался. mark_notification_sent при этом не
                        # вызывается, поэтому ни NOTIFICATION_SENT, ни
                        # NOTIFICATION_FLAG_ALREADY_SET не появятся — по логам
                        # человек просто исчезал после списания денег.
                        logger.warning(
                            "AUTO_RENEWAL_NOTIFICATION_UNDELIVERED payment_id=%s user=%s — "
                            "подписка продлена и оплачена, человек уведомления не получил",
                            item.get("payment_id"), item.get("telegram_id"),
                        )
                        continue
                    await asyncio.sleep(0.05)  # Telegram rate limit: max 20 msgs/sec
                    # Explicit timeout for notification connection acquire (pool timeout is 10s)
                    notify_cm = acquire_connection(pool, "auto_renewal_notify")
                    try:
                        notify_conn = await asyncio.wait_for(notify_cm.__aenter__(), timeout=10.0)
                    except asyncio.TimeoutError:
                        logger.error("auto_renewal: pool.acquire() timed out for notify_conn after 10s")
                        continue
                    try:
                        marked = await notification_service.mark_notification_sent(item["payment_id"], conn=notify_conn)
                        if marked:
                            logger.info(
                                f"NOTIFICATION_SENT [type=auto_renewal, payment_id={item['payment_id']}, user={item['telegram_id']}]"
                            )
                        else:
                            logger.warning(
                                f"NOTIFICATION_FLAG_ALREADY_SET [type=auto_renewal, payment_id={item['payment_id']}, user={item['telegram_id']}]"
                            )
                    finally:
                        # Release notification connection
                        try:
                            await notify_cm.__aexit__(None, None, None)
                        except Exception as rel_err:
                            # Утечка соединения пула — прямая причина
                            # последующих таймаутов pool.acquire() в этом же
                            # воркере. Молчание здесь делало её
                            # недиагностируемой: таймауты появлялись позже и
                            # выглядели как проблема базы.
                            logger.warning(
                                "auto_renewal: notify_conn release failed: %s: %s",
                                type(rel_err).__name__, rel_err,
                            )
                except Exception as e:
                    logger.error(
                        f"CRITICAL: Failed to send/mark auto-renewal notification: payment_id={item.get('payment_id')}, user={item.get('telegram_id')}, error={e}"
                    )
                    try:
                        from app.services.admin_alerts import send_alert
                        await send_alert(
                            bot, "payment",
                            f"Auto-renewal notification failed\n"
                            f"User: {item.get('telegram_id')}\n"
                            f"Payment: {item.get('payment_id')}\n"
                            f"Error: {type(e).__name__}: {str(e)[:200]}"
                        )
                    except Exception as alert_err:
                        # Двойной провал: уведомление не ушло И админа не
                        # позвали. Раньше не оставлял следа вовсе — а это
                        # ровно тот случай, когда человек заплатил и об этом
                        # никто не узнал.
                        logger.critical(
                            "AUTO_RENEWAL_NOTIFY_AND_ALERT_FAILED payment_id=%s user=%s: %s — "
                            "не удалось ни уведомить, ни поднять алерт",
                            item.get("payment_id"), item.get("telegram_id"), alert_err,
                        )
        finally:
            # Release connection (equivalent to __aexit__)
            try:
                await cm.__aexit__(None, None, None)
            except Exception as rel_err:
                # См. выше: утечка соединения проявляется позже и в другом
                # месте, поэтому её надо записывать там, где она произошла.
                logger.warning(
                    "auto_renewal: connection release failed: %s: %s",
                    type(rel_err).__name__, rel_err,
                )

        await asyncio.sleep(0)


async def auto_renewal_task(bot: Bot):
    """
    Фоновая задача для автопродления подписок
    
    Запускается каждые AUTO_RENEWAL_INTERVAL_SECONDS (по умолчанию 10 минут, минимум 5, максимум 15)
    для проверки подписок, истекающих в течение RENEWAL_WINDOW (по умолчанию 6 часов).
    
    Это обеспечивает:
    - Своевременное продление (частые проверки, не пропустим подписки)
    - Безопасность при рестартах (не будет двойного списания благодаря last_auto_renewal_at)
    - Идемпотентность (повторные вызовы безопасны)
    - Атомарность (баланс и подписка обновляются в одной транзакции)
    - UUID стабильность (продление без пересоздания UUID через grant_access)
    """
    logger.info(
        f"Auto-renewal task started: interval={AUTO_RENEWAL_INTERVAL_SECONDS}s, "
        f"renewal_window={RENEWAL_WINDOW_HOURS}h"
    )
    
    # Стартового прогона здесь больше нет.
    #
    # Раньше перед циклом безусловно вызывался process_auto_renewals: без
    # проверки feature-флагов, без database.DB_READY и без wait_for. То есть
    # реальное списание денег шло в обход всех гейтов и не попадало в
    # ITERATION_START/END — для метрик этой работы просто не существовало.
    # Два конкретных последствия: при выключенном auto_renewal_enabled деньги
    # всё равно списывались (флаг читался только на первом витке цикла), а
    # зависший прогон навсегда удерживал _worker_lock — каждая следующая
    # итерация честно рапортовала timeout, но причина в логах не видна.
    #
    # Теперь первая же работа идёт через обычный виток цикла: со всеми
    # гейтами, таймаутом и логированием. Задержка — только jitter ниже (до
    # минуты), что несущественно при окне продления в 6 часов.
    #
    # Разброс старта — общее правило для всех воркеров,
    # см. app/core/worker_startup.
    await startup_jitter("auto_renewal")
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        iteration_outcome = "success"
        iteration_error_type = None

        # STEP 2.3 — OBSERVABILITY: Structured logging for worker iteration start
        correlation_id = log_worker_iteration_start(
            worker_name="auto_renewal",
            iteration_number=iteration_number
        )

        try:
            # Feature flag check
            from app.core.feature_flags import get_feature_flags
            feature_flags = get_feature_flags()
            if not feature_flags.background_workers_enabled or not feature_flags.auto_renewal_enabled:
                logger.warning(
                    f"[FEATURE_FLAG] Auto-renewal disabled, skipping iteration in auto_renewal "
                    f"(iteration={iteration_number}, workers_enabled={feature_flags.background_workers_enabled}, "
                    f"auto_renewal_enabled={feature_flags.auto_renewal_enabled})"
                )
                iteration_outcome = "skipped"
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue

            # Simple DB readiness check
            if not database.DB_READY:
                logger.warning("auto_renewal: skipping — DB not ready")
                iteration_outcome = "skipped"
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)
                continue

            # Wrap entire iteration body so a hung run is cancelled after 2 minutes (avoids holding DB forever, liveness watchdog)
            async def _run_iteration_body():
                async with _worker_lock:
                    await process_auto_renewals(bot)

            try:
                await asyncio.wait_for(_run_iteration_body(), timeout=ITERATION_HARD_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                logger.error(
                    "auto_renewal: iteration timed out after %.0fs (worker=auto_renewal correlation_id=%s)",
                    ITERATION_HARD_TIMEOUT_SECONDS,
                    correlation_id,
                    extra={"worker": "auto_renewal", "correlation_id": correlation_id},
                )
                iteration_outcome = "timeout"
                iteration_error_type = "timeout"
                # Do NOT re-raise; continue to next iteration after finally

        except asyncio.CancelledError:
            # Отмена перебрасывается, а не гасится (единый контракт всех
            # воркеров). Раньше здесь был break: задача завершалась «штатно»,
            # и по логам нельзя было отличить остановку процесса от
            # самопроизвольного выхода воркера — а для автопродления это
            # разница между «мы выключились» и «деньги перестали списываться».
            logger.info("Auto-renewal task cancelled")
            iteration_outcome = "cancelled"
            raise
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"auto_renewal: DB temporarily unavailable: {type(e).__name__}: {str(e)[:100]}")
            iteration_outcome = "degraded"
            iteration_error_type = "infra_error"
        except Exception as e:
            # Трейсбэк стоял на debug, а root-логгер настроен на INFO: в
            # проде оставался только класс исключения и 100 символов текста,
            # без стека и без указания, на ком упало.
            logger.error(
                "auto_renewal: Unexpected error in task loop: %s: %s",
                type(e).__name__, e, exc_info=True,
            )
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "auto_renewal", e, iteration=iteration_number)
            except Exception as alert_err:
                # «Воркер автопродления упал и админа не позвали» — событие,
                # которое обязано быть видно: без него тишина в логах
                # неотличима от штатной работы.
                logger.error(
                    "auto_renewal: alert_worker_failure failed on iteration %s: %s",
                    iteration_number, alert_err,
                )
        finally:
            # Always log ITERATION_END so production logs confirm the iteration completed (no indefinite hang)
            duration_ms = (time.time() - iteration_start_time) * 1000
            log_worker_iteration_end(
                worker_name="auto_renewal",
                outcome=iteration_outcome,
                items_processed=0,
                error_type=iteration_error_type,
                duration_ms=duration_ms,
            )
            # Здесь был `await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)`.
            # Он ПРИБАВЛЯЛСЯ к обычной паузе цикла ниже: после сбойной итерации
            # воркер спал 300 с, а потом ещё AUTO_RENEWAL_INTERVAL_SECONDS.
            # При минимальном интервале в 300 с фактическая пауза выходила
            # вдвое больше заявленной, и окно продления в 6 часов обслуживалось
            # реже, чем обещает конфиг. Нижняя граница паузы теперь считается
            # как максимум, а не как слагаемое (см. ниже).
            #
            # Заодно на пути отмены в finally больше нет ни одного await:
            # спящий finally задерживает завершение задачи при shutdown.

        # Пауза до следующей итерации.
        #
        # MINIMUM_SAFE_SLEEP_ON_FAILURE — это ПОЛ паузы после сбоя (защита от
        # шторма повторов), а не добавка к интервалу. Поэтому max, а не сумма.
        # Сейчас интервал сам по себе не меньше 300 с, так что max почти всегда
        # вернёт интервал — правило записано явно, чтобы понижение нижней
        # границы интервала в будущем не сняло защиту молча.
        sleep_seconds = AUTO_RENEWAL_INTERVAL_SECONDS
        if iteration_outcome not in ("success", "skipped"):
            sleep_seconds = max(AUTO_RENEWAL_INTERVAL_SECONDS, MINIMUM_SAFE_SLEEP_ON_FAILURE)
        await asyncio.sleep(sleep_seconds)

