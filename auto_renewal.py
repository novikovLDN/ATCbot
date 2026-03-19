"""Модуль для автопродления подписок с баланса"""
import asyncio
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.utils.telegram_safe import safe_send_message
import asyncpg
import database
import config
from app import i18n
from app.services.notifications import service as notification_service
from app.services.language_service import resolve_user_language
from app.utils.logging_helpers import (
    log_worker_iteration_start,
    log_worker_iteration_end,
    classify_error,
)
from app.core.cooperative_yield import cooperative_yield
from app.core.pool_monitor import acquire_connection

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
# Minimum safe sleep on failure to prevent tight retry storms
MINIMUM_SAFE_SLEEP_ON_FAILURE = 300  # seconds (half of AUTO_RENEWAL_INTERVAL_SECONDS minimum)


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
    try:
        pool = await database.get_pool()
    except Exception as e:
        logger.warning(f"auto_renewal: Failed to acquire DB pool: {type(e).__name__}: {e}")
        return
    if not pool:
        logger.warning("auto_renewal: DB pool not available, skipping iteration")
        return
    now = datetime.now(timezone.utc)
    renewal_threshold = now + RENEWAL_WINDOW

    query_with_reachable = """
        SELECT s.*, u.language, u.balance
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
        SELECT s.*, u.language, u.balance
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
    while True:
        cm = acquire_connection(pool, "auto_renewal_main")
        try:
            conn = await asyncio.wait_for(cm.__aenter__(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("auto_renewal: pool.acquire() timed out after 10s")
            raise
        try:
            notifications_to_send = []
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

                iteration_start = time.monotonic()
                for i, sub_row in enumerate(subscriptions):
                    if i > 0 and i % 50 == 0:
                        await cooperative_yield()
                    if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
                        logger.warning("Auto-renewal iteration time limit reached, breaking early")
                        break
                    telegram_id = sub_row["telegram_id"]
                    subscription = sub_row
                    language = sub_row.get("language", "en")
                    try:
                        # Use savepoint so we can rollback per-subscription on failure
                        # without aborting the entire batch transaction.
                        sp = conn.transaction()
                        await sp.start()

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
                            await sp.rollback()
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
                            await sp.rollback()
                            continue
                        
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
                        
                        base_price = config.TARIFFS[tariff_type][period_days]["price"]
                        
                        is_vip = await database.is_vip_user(telegram_id, conn=conn)
                        if is_vip:
                            amount_rubles = round(base_price * 0.70, 2)  # 30% скидка
                        else:
                            personal_discount = await database.get_user_discount(telegram_id, conn=conn)
                            if personal_discount:
                                discount_percent = personal_discount["discount_percent"]
                                amount_rubles = round(base_price * (1 - discount_percent / 100), 2)
                            else:
                                amount_rubles = float(base_price)
                        
                        user_balance_kopecks = subscription.get("balance", 0) or 0
                        balance_rubles = user_balance_kopecks / 100.0
                        
                        if balance_rubles >= amount_rubles:
                            duration = timedelta(days=period_days)
                            months = period_days // 30
                            tariff_name = "Basic" if tariff_type == "basic" else "Plus"
                            success = await database.decrease_balance(
                                telegram_id=telegram_id,
                                amount=amount_rubles,
                                source="auto_renew",
                                description=f"Автопродление подписки {tariff_name} на {months} месяц(ев)",
                                conn=conn
                            )
                            
                            if not success:
                                logger.error(f"Failed to decrease balance for auto-renewal: user={telegram_id}")
                                await sp.rollback()
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
                                    from app.services.admin_alerts import alert_payment_failure
                                    await alert_payment_failure(
                                        bot, "auto_renewal", telegram_id,
                                        f"refund_uuid_regen_{telegram_id}",
                                        RuntimeError(f"Refund failed after UUID regeneration, amount={amount_rubles}"),
                                        is_transient=False,
                                        amount_rubles=amount_rubles,
                                        tariff=tariff_type,
                                        period_days=period_days,
                                    )
                                await sp.rollback()
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
                                    from app.services.admin_alerts import alert_payment_failure
                                    await alert_payment_failure(
                                        bot, "auto_renewal", telegram_id,
                                        f"refund_renewal_fail_{telegram_id}",
                                        RuntimeError(f"Refund failed after renewal failure, amount={amount_rubles}"),
                                        is_transient=False,
                                        amount_rubles=amount_rubles,
                                        tariff=tariff_type,
                                        period_days=period_days,
                                    )
                                await sp.rollback()
                                continue

                            tariff_str = f"{tariff_type}_{period_days}"
                            payment_id = await conn.fetchval(
                                "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved') RETURNING id",
                                telegram_id, tariff_str, round(amount_rubles * 100)
                            )
                            
                            if not payment_id:
                                logger.error(f"Failed to create payment record for auto-renewal: user={telegram_id}")
                                continue
                            
                            notification_already_sent = await notification_service.check_notification_idempotency(
                                payment_id, conn=conn
                            )
                            if notification_already_sent:
                                logger.info(
                                    f"NOTIFICATION_IDEMPOTENT_SKIP [type=auto_renewal, payment_id={payment_id}, user={telegram_id}]"
                                )
                                continue

                            expires_str = expires_at.strftime("%d.%m.%Y")
                            duration_days = duration.days
                            # Собираем payload для Phase B (после commit) — без Telegram и без вложенного acquire
                            xray_sync_info = result.get("renewal_xray_sync_after_commit")
                            notifications_to_send.append({
                                "telegram_id": telegram_id,
                                "payment_id": payment_id,
                                "language": language,
                                "expires_str": expires_str,
                                "duration_days": duration_days,
                                "amount_rubles": amount_rubles,
                                "tariff_type": tariff_type,
                                "period_days": period_days,
                                "xray_sync": xray_sync_info,
                            })
                            logger.info(f"Auto-renewal successful: user={telegram_id}, tariff={tariff_type}, period_days={period_days}, amount={amount_rubles} RUB, expires_at={expires_str}")
                            await sp.commit()

                        else:
                            logger.debug(f"Insufficient balance for auto-renewal: user={telegram_id}, balance={balance_rubles:.2f} RUB, required={amount_rubles:.2f} RUB")
                            await sp.rollback()

                    except Exception as e:
                        try:
                            await sp.rollback()
                        except Exception:
                            pass
                        logger.exception(f"Error processing auto-renewal for user {telegram_id}: {e}")
                        try:
                            from app.services.admin_alerts import send_alert
                            await send_alert(
                                bot, "payment",
                                f"Auto-renewal processing error\n"
                                f"User: {telegram_id}\n"
                                f"Error: {type(e).__name__}: {str(e)[:200]}"
                            )
                        except Exception:
                            pass

            # PHASE B: после commit — xray sync + отправка уведомлений (без финансовых мутаций)
            for item in notifications_to_send:
                # B0: Xray sync deferred from grant_access (must run post-commit)
                xray_sync = item.get("xray_sync")
                if xray_sync:
                    try:
                        import vpn_utils
                        await vpn_utils.ensure_user_in_xray(
                            telegram_id=xray_sync["telegram_id"],
                            uuid=xray_sync["uuid"],
                            subscription_end=xray_sync["subscription_end"],
                        )
                    except Exception as e:
                        logger.error(
                            f"AUTO_RENEWAL_XRAY_SYNC_FAILED user={item['telegram_id']} error={e}"
                        )
                try:
                    tariff_label = "Plus" if item.get("tariff_type") == "plus" else "Basic"
                    tariff_emoji = "⭐️" if item.get("tariff_type") == "plus" else "📦"
                    user_lang = await resolve_user_language(item["telegram_id"])
                    amount_val = item.get("amount_rubles", 0)
                    # amount may be in kopecks (>1000) or rubles
                    if amount_val > 1000:
                        amount_val = amount_val / 100
                    text = i18n.get_text(
                        user_lang, "purchase.auto_renewal_success",
                        tariff_name=f"{tariff_emoji} {tariff_label}",
                        days=item.get("period_days", 30),
                        expires_date=item["expires_str"],
                        amount=amount_val
                    )
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile")],
                    ])
                    sent = await safe_send_message(bot, item["telegram_id"], text, reply_markup=keyboard)
                    if sent is None:
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
                        except Exception:
                            pass
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
                    except Exception:
                        pass
        finally:
            # Release connection (equivalent to __aexit__)
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass  # Ignore errors during cleanup

        await asyncio.sleep(0)


async def process_card_auto_renewals(bot: Bot):
    """
    Автопродление подписок по сохранённой карте (YooKassa recurring).

    Обрабатывает подписки с auto_renew_card=TRUE и saved_payment_method_id IS NOT NULL.
    Для каждой — вызывает yookassa_service.create_autopayment(), при успехе продлевает через grant_access().

    Отличия от balance auto-renewal:
    - Не работает внутри единой DB-транзакции (внешний API вызов между проверкой и продлением)
    - Использует last_card_renewal_at для idempotency (аналог last_auto_renewal_at)
    - При ошибке оплаты — уведомляет пользователя, но не ретраит в этой итерации
    """
    try:
        import yookassa_service
        if not yookassa_service.is_enabled():
            return
    except ImportError:
        return

    pool = await database.get_pool()
    if not pool:
        return

    now = datetime.now(timezone.utc)
    renewal_threshold = now + RENEWAL_WINDOW

    # Fetch subscriptions eligible for card auto-renewal
    async with acquire_connection(pool, "card_auto_renewal_fetch") as conn:
        try:
            subscriptions = await conn.fetch(
                """SELECT s.telegram_id, s.expires_at, s.subscription_type,
                          s.saved_payment_method_id, s.saved_payment_method_title,
                          s.last_auto_renewal_at, u.language
                   FROM subscriptions s
                   JOIN users u ON s.telegram_id = u.telegram_id
                   WHERE s.status = 'active'
                     AND s.auto_renew_card = TRUE
                     AND s.saved_payment_method_id IS NOT NULL
                     AND s.expires_at <= $1
                     AND s.expires_at > $2
                     AND s.uuid IS NOT NULL
                     AND (s.last_auto_renewal_at IS NULL
                          OR s.last_auto_renewal_at < s.expires_at - INTERVAL '12 hours')
                   ORDER BY s.id ASC
                   LIMIT $3""",
                database._to_db_utc(renewal_threshold),
                database._to_db_utc(now),
                BATCH_SIZE,
            )
        except Exception as e:
            logger.error(f"card_auto_renewal: query failed: {e}")
            return

    if not subscriptions:
        return

    logger.info(f"Card auto-renewal: found {len(subscriptions)} subscriptions")

    iteration_start = time.monotonic()
    for sub_row in subscriptions:
        if time.monotonic() - iteration_start > MAX_ITERATION_SECONDS:
            logger.warning("Card auto-renewal iteration time limit reached, breaking early")
            break

        telegram_id = sub_row["telegram_id"]
        payment_method_id = sub_row["saved_payment_method_id"]
        language = sub_row.get("language", "ru")

        try:
            # Determine tariff and price (same logic as balance auto-renewal)
            last_payment = await database.get_last_approved_payment(telegram_id)
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
                        period_days = int(tariff_str) * 30
                    except ValueError:
                        period_days = 30

            if tariff_type not in config.TARIFFS or period_days not in config.TARIFFS[tariff_type]:
                tariff_type = "basic"
                period_days = 30

            base_price = config.TARIFFS[tariff_type][period_days]["price"]

            is_vip = await database.is_vip_user(telegram_id)
            if is_vip:
                amount_rubles = round(base_price * 0.70, 2)
            else:
                personal_discount = await database.get_user_discount(telegram_id)
                if personal_discount:
                    discount_percent = personal_discount["discount_percent"]
                    amount_rubles = round(base_price * (1 - discount_percent / 100), 2)
                else:
                    amount_rubles = float(base_price)

            tariff_label = "Plus" if tariff_type == "plus" else "Basic"
            months = period_days // 30

            # Mark renewal attempt (idempotency)
            async with acquire_connection(pool, "card_auto_renewal_mark") as conn:
                update_result = await conn.execute(
                    """UPDATE subscriptions
                       SET last_auto_renewal_at = $1
                       WHERE telegram_id = $2
                         AND status = 'active'
                         AND auto_renew_card = TRUE
                         AND saved_payment_method_id IS NOT NULL
                         AND (last_auto_renewal_at IS NULL
                              OR last_auto_renewal_at < expires_at - INTERVAL '12 hours')""",
                    database._to_db_utc(now), telegram_id,
                )
                if update_result == "UPDATE 0":
                    logger.debug(f"Card renewal: {telegram_id} already being processed, skipping")
                    continue

            # Call YooKassa autopayment API (external call, outside DB transaction)
            try:
                pay_result = await asyncio.wait_for(
                    yookassa_service.create_autopayment(
                        amount_rubles=amount_rubles,
                        payment_method_id=payment_method_id,
                        description=f"Atlas Secure VPN автопродление {tariff_label} на {months} мес.",
                        telegram_id=telegram_id,
                        metadata={"telegram_id": str(telegram_id), "source": "card_auto_renewal"},
                    ),
                    timeout=20.0,
                )
            except Exception as pay_err:
                logger.error(f"Card auto-renewal payment failed: user={telegram_id}, error={pay_err}")
                # Notify user about failed charge
                try:
                    user_lang = await resolve_user_language(telegram_id)
                    text = i18n.get_text(user_lang, "purchase.auto_renewal_card_failed")
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile")],
                    ])
                    await safe_send_message(bot, telegram_id, text, reply_markup=keyboard)
                except Exception:
                    pass
                continue

            if not pay_result.get("paid") and pay_result.get("status") != "succeeded":
                logger.warning(
                    f"Card auto-renewal: payment not confirmed immediately: user={telegram_id}, "
                    f"status={pay_result.get('status')}, paid={pay_result.get('paid')}"
                )
                # Payment may settle later via webhook — skip grant for now
                continue

            # Payment succeeded — grant access
            duration = timedelta(days=period_days)
            async with acquire_connection(pool, "card_auto_renewal_grant") as conn:
                async with conn.transaction():
                    result = await database.grant_access(
                        telegram_id=telegram_id,
                        duration=duration,
                        source="card_auto_renew",
                        admin_telegram_id=None,
                        admin_grant_days=None,
                        conn=conn,
                    )

                    expires_at = result.get("subscription_end")
                    action_type = result.get("action", "unknown")

                    if action_type != "renewal" or result.get("vless_url") is not None:
                        logger.error(
                            f"Card auto-renewal ERROR: UUID regenerated! user={telegram_id}, "
                            f"action={action_type}"
                        )
                        continue

                    tariff_str = f"{tariff_type}_{period_days}"
                    await conn.execute(
                        "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'approved')",
                        telegram_id, tariff_str, round(amount_rubles * 100),
                    )

            # Post-commit: xray sync
            xray_sync = result.get("renewal_xray_sync_after_commit")
            if xray_sync:
                try:
                    import vpn_utils
                    await vpn_utils.ensure_user_in_xray(
                        telegram_id=xray_sync["telegram_id"],
                        uuid=xray_sync["uuid"],
                        subscription_end=xray_sync["subscription_end"],
                    )
                except Exception as e:
                    logger.error(f"CARD_RENEWAL_XRAY_SYNC_FAILED user={telegram_id} error={e}")

            # Notify user
            try:
                user_lang = await resolve_user_language(telegram_id)
                tariff_emoji = "⭐️" if tariff_type == "plus" else "📦"
                expires_str = expires_at.strftime("%d.%m.%Y") if expires_at else "—"
                text = i18n.get_text(
                    user_lang, "purchase.auto_renewal_card_success",
                    tariff_name=f"{tariff_emoji} {tariff_label}",
                    days=period_days,
                    expires_date=expires_str,
                    amount=amount_rubles,
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu_profile")],
                ])
                await safe_send_message(bot, telegram_id, text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Card auto-renewal notification failed: user={telegram_id}, error={e}")

            logger.info(
                f"Card auto-renewal successful: user={telegram_id}, tariff={tariff_type}, "
                f"period_days={period_days}, amount={amount_rubles} RUB, "
                f"payment_id={pay_result.get('payment_id')}"
            )

        except Exception as e:
            logger.exception(f"Error in card auto-renewal for user {telegram_id}: {e}")
            try:
                from app.services.admin_alerts import send_alert
                await send_alert(
                    bot, "payment",
                    f"Card auto-renewal error\n"
                    f"User: {telegram_id}\n"
                    f"Error: {type(e).__name__}: {str(e)[:200]}"
                )
            except Exception:
                pass

        await cooperative_yield()


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
    
    # Первая проверка сразу при запуске
    try:
        async with _worker_lock:
            await process_auto_renewals(bot)
            await process_card_auto_renewals(bot)
    except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
        # RESILIENCE FIX: Temporary DB failures don't crash the task
        logger.warning(f"auto_renewal: Initial check failed (DB temporarily unavailable): {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"auto_renewal: Unexpected error in initial check: {type(e).__name__}: {str(e)[:100]}")
        logger.debug("auto_renewal: Full traceback for initial check", exc_info=True)

    # POOL STABILITY: One-time startup jitter to avoid 600s worker alignment burst.
    jitter_s = random.uniform(5, 60)
    await asyncio.sleep(jitter_s)
    logger.debug(f"auto_renewal: startup jitter done ({jitter_s:.1f}s)")
    
    iteration_number = 0
    
    while True:
        iteration_start_time = time.time()
        iteration_number += 1
        iteration_outcome = "success"
        iteration_error_type = None
        should_exit_loop = False

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

            async def _run_card_iteration_body():
                async with _worker_lock:
                    await process_card_auto_renewals(bot)

            try:
                await asyncio.wait_for(_run_iteration_body(), timeout=ITERATION_HARD_TIMEOUT_SECONDS)
                await asyncio.wait_for(_run_card_iteration_body(), timeout=ITERATION_HARD_TIMEOUT_SECONDS)
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
            logger.info("Auto-renewal task cancelled")
            iteration_outcome = "cancelled"
            should_exit_loop = True
        except (asyncpg.PostgresError, asyncio.TimeoutError) as e:
            # RESILIENCE FIX: Temporary DB failures don't crash the task loop
            logger.warning(f"auto_renewal: DB temporarily unavailable: {type(e).__name__}: {str(e)[:100]}")
            iteration_outcome = "degraded"
            iteration_error_type = "infra_error"
        except Exception as e:
            logger.error(f"auto_renewal: Unexpected error in task loop: {type(e).__name__}: {str(e)[:100]}")
            logger.debug("auto_renewal: Full traceback for task loop", exc_info=True)
            iteration_outcome = "failed"
            iteration_error_type = classify_error(e)
            try:
                from app.services.admin_alerts import alert_worker_failure
                await alert_worker_failure(bot, "auto_renewal", e, iteration=iteration_number)
            except Exception:
                pass
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
            if iteration_outcome not in ("success", "cancelled", "skipped"):
                await asyncio.sleep(MINIMUM_SAFE_SLEEP_ON_FAILURE)

        if should_exit_loop:
            break
        
        # Sleep after iteration completes (outside try/finally)
        # Ждем до следующей проверки (5-15 минут, по умолчанию 10 минут)
        await asyncio.sleep(AUTO_RENEWAL_INTERVAL_SECONDS)

