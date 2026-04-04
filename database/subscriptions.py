"""
Database operations: Subscriptions, Payments, Trials, Access, Purchase flow, Promo codes, Reminders.

All shared state (get_pool, helpers) imported from database.core.
DB_READY accessed via _core.DB_READY to get live value (not stale import-time copy).
Cross-module calls (increase_balance, process_referral_reward) use lazy imports.
"""
import asyncpg
import asyncio
import json
import logging
import random
import string
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING, List
import config
import vpn_utils
import database.core as _core
from database.core import (
    get_pool,
    _to_db_utc, _from_db_utc, _ensure_utc,
    _normalize_subscription_row, _generate_subscription_uuid,
    safe_int, mark_payment_notification_sent,
    retry_async,
)

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

async def get_pending_payment_by_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить pending платеж пользователя"""
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_pending_payment_by_user skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_pending_payment_by_user skipped")
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        return dict(row) if row else None


async def create_payment(telegram_id: int, tariff: str) -> Optional[int]:
    """Создать платеж и вернуть его ID. Возвращает None, если уже есть pending платеж
    
    Автоматически применяет скидки в следующем порядке приоритета:
    1. VIP-статус (30%) - ВЫСШИЙ ПРИОРИТЕТ
    2. Персональная скидка (admin)
    """
    # Проверяем наличие pending платежа
    existing_payment = await get_pending_payment_by_user(telegram_id)
    if existing_payment:
        return None  # У пользователя уже есть pending платеж
    
    # Рассчитываем цену с учетом скидки
    # TARIFFS is nested: tariff -> period_days -> {price}. Default to 30-day period.
    tariff_periods = config.TARIFFS.get(tariff, config.TARIFFS.get("basic", {}))
    tariff_data = tariff_periods.get(30, {})
    base_price = tariff_data.get("price", 149)
    
    # ПРИОРИТЕТ 1: Проверяем VIP-статус (высший приоритет)
    from database.admin import is_vip_user, get_user_discount
    is_vip = await is_vip_user(telegram_id)
    discount_applied = None
    discount_type = None
    
    if is_vip:
        # Применяем VIP-скидку 30% ко всем тарифам
        discounted_price = int(base_price * 0.70)  # 30% скидка
        amount = discounted_price
        discount_applied = 30
        discount_type = "vip"
    else:
        # ПРИОРИТЕТ 2: Проверяем персональную скидку
        personal_discount = await get_user_discount(telegram_id)
        
        if personal_discount:
            # Применяем персональную скидку
            discount_percent = personal_discount["discount_percent"]
            discounted_price = int(base_price * (1 - discount_percent / 100))
            amount = discounted_price
            discount_applied = discount_percent
            discount_type = "personal"
        else:
            # Без скидки - используем базовую цену
            amount = base_price
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        payment_id = await conn.fetchval(
            "INSERT INTO payments (telegram_id, tariff, amount, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
            telegram_id, tariff, amount
        )
        
        # Логируем применение скидки
        if discount_applied:
            details = f"{discount_type} discount applied: tariff={tariff}, base_price={base_price}, discount={discount_applied}%, final_price={amount}"
            await _log_audit_event_atomic(conn, f"{discount_type}_discount_applied", telegram_id, telegram_id, details)
        
        return payment_id


async def get_payment(payment_id: int) -> Optional[Dict[str, Any]]:
    """Получить платеж по ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE id = $1", payment_id
        )
        return dict(row) if row else None


async def get_last_approved_payment(telegram_id: int, conn: Optional[asyncpg.Connection] = None) -> Optional[Dict[str, Any]]:
    """Получить последний утверждённый платёж пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        conn: Опциональное соединение (если передано — используется оно, без pool.acquire)
    
    Returns:
        Словарь с данными платежа или None, если платёж не найден
    """
    if conn is not None:
        row = await conn.fetchrow(
            """SELECT * FROM payments 
               WHERE telegram_id = $1 AND status = 'approved'
               ORDER BY created_at DESC
               LIMIT 1""",
            telegram_id
        )
        return dict(row) if row else None
    pool = await get_pool()
    async with pool.acquire() as acquired:
        row = await acquired.fetchrow(
            """SELECT * FROM payments 
               WHERE telegram_id = $1 AND status = 'approved'
               ORDER BY created_at DESC
               LIMIT 1""",
            telegram_id
        )
        return dict(row) if row else None


async def update_payment_status(payment_id: int, status: str, admin_telegram_id: Optional[int] = None):
    """Обновить статус платежа
    
    Args:
        payment_id: ID платежа
        status: Новый статус ('approved', 'rejected', и т.д.)
        admin_telegram_id: Telegram ID администратора (опционально, для аудита)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Получаем информацию о платеже для аудита
            payment_row = await conn.fetchrow(
                "SELECT telegram_id FROM payments WHERE id = $1",
                payment_id
            )
            target_user = payment_row["telegram_id"] if payment_row else None
            
            # Обновляем статус
            await conn.execute(
                "UPDATE payments SET status = $1 WHERE id = $2",
                status, payment_id
            )
            
            # Записываем в audit_log, если указан admin_telegram_id
            if admin_telegram_id is not None:
                action_type = "payment_rejected" if status == "rejected" else f"payment_status_changed_{status}"
                details = f"Payment ID: {payment_id}, Status: {status}"
                await _log_audit_event_atomic(conn, action_type, admin_telegram_id, target_user, details)


async def check_and_disable_expired_subscription(telegram_id: int) -> bool:
    """
    Проверить и немедленно отключить истёкшую подписку.
    
    Two-phase pattern: Phase 1 DB read, Phase 2 remove from Xray (outside tx), Phase 3 DB update.
    External API call NEVER inside DB transaction.
    
    Returns:
        True если подписка была отключена, False если подписка активна или отсутствует
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, check_and_disable_expired_subscription skipped")
        return False
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, check_and_disable_expired_subscription skipped")
        return False
    now = datetime.now(timezone.utc)
    now_db = _to_db_utc(now)
    uuid_to_remove = None
    subscription = None
    subscription_id = None
    # PHASE 1 — DB read (inside tx)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT * FROM subscriptions
                   WHERE telegram_id = $1
                     AND expires_at <= $2
                     AND status = 'active'
                     AND uuid IS NOT NULL""",
                telegram_id, now_db
            )
            if not row:
                return False
            subscription = dict(row)
            subscription_id = subscription.get("id")
            uuid_to_remove = subscription.get("uuid")
            logger.info(
                "EXPIRY_PHASE1",
                extra={"telegram_id": telegram_id, "uuid": (uuid_to_remove[:8] + "...") if uuid_to_remove and len(uuid_to_remove) > 8 else "N/A"}
            )
    # E1: Re-verify row still expired before Phase 2 (avoids removing UUID if renewal won race)
    if uuid_to_remove and subscription_id:
        async with pool.acquire() as conn:
            recheck = await conn.fetchrow(
                """SELECT 1 FROM subscriptions
                   WHERE id = $1 AND telegram_id = $2 AND uuid = $3 AND status = 'active' AND expires_at <= $4""",
                subscription_id, telegram_id, uuid_to_remove, now_db
            )
            if not recheck:
                logger.debug(
                    "EXPIRY_SKIPPED_RENEWED",
                    extra={"telegram_id": telegram_id, "uuid": uuid_to_remove[:8] + "..."}
                )
                return False
    # PHASE 2 — External call (outside tx)
    removal_success = True
    if uuid_to_remove:
        try:
            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)
            logger.info(
                "EXPIRY_REMOVE_SUCCESS",
                extra={"telegram_id": telegram_id, "uuid": uuid_to_remove[:8] + "..."}
            )
            try:
                expires_at_str = (subscription.get("expires_at") or "").isoformat() if subscription else "N/A"
                await _log_vpn_lifecycle_audit_async(
                    action="vpn_expire",
                    telegram_id=telegram_id,
                    uuid=uuid_to_remove,
                    source="auto-expiry",
                    result="success",
                    details=f"Real-time expiration check, expires_at={expires_at_str}"
                )
            except Exception as e:
                logger.warning(f"Failed to log VPN expire audit (non-blocking): {e}")
        except Exception as e:
            removal_success = False
            logger.critical(
                "EXPIRY_REMOVE_FAILED",
                extra={"telegram_id": telegram_id, "uuid": uuid_to_remove[:8] + "...", "error": str(e)[:200]}
            )
            return False
    if not removal_success:
        return False
    # PHASE 3 — DB update (new transaction)
    # E1 FIX: Re-check expires_at to avoid race with renewal. If renewal extended expires_at
    # between Phase 1 and Phase 3, this UPDATE must match 0 rows — subscription stays active.
    if not uuid_to_remove:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """UPDATE subscriptions
                   SET status = 'expired', uuid = NULL, vpn_key = NULL
                   WHERE id = $1 AND telegram_id = $2 AND uuid = $3 AND status = 'active'
                     AND expires_at <= $4""",
                subscription_id, telegram_id, uuid_to_remove, now_db
            )
            rows = int(result.split()[-1]) if result else 0
            if rows > 0:
                logger.info(
                    "EXPIRY_DB_UPDATE_SUCCESS",
                    extra={"telegram_id": telegram_id, "uuid": (uuid_to_remove[:8] + "...") if uuid_to_remove else "N/A"}
                )
                # Disable Remnawave bypass (fire-and-forget)
                try:
                    from app.services.remnawave_service import disable_remnawave_user_bg
                    disable_remnawave_user_bg(telegram_id)
                except Exception as rmn_err:
                    logger.warning("REMNAWAVE_EXPIRY_HOOK_FAIL: tg=%s %s", telegram_id, rmn_err)

                # Создаем спецпредложение -15% на 3 дня для пользователей с оплаченной подпиской
                sub_source = subscription.get("source", "")
                if sub_source == "payment":
                    try:
                        await set_special_offer(telegram_id)
                        logger.info(f"SPECIAL_OFFER_CREATED for user {telegram_id} after paid subscription expired")
                    except Exception as e:
                        logger.warning(f"Failed to create special offer for {telegram_id}: {e}")
            elif rows == 0 and subscription_id and uuid_to_remove:
                logger.debug(
                    "EXPIRY_SKIPPED_RENEWED",
                    extra={"telegram_id": telegram_id, "uuid": uuid_to_remove[:8] + "..."}
                )
            return rows > 0


async def get_subscription(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную подписку пользователя
    
    Активной считается подписка, у которой:
    - status = 'active'
    - expires_at > текущего времени
    
    НЕ фильтрует по source (payment/admin/test) - все подписки равны.
    
    Перед возвратом проверяет и отключает истёкшие подписки.
    """
    # Сначала проверяем и отключаем истёкшие подписки
    await check_and_disable_expired_subscription(telegram_id)
    
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscription skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription skipped")
        return None
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1 AND status = 'active' AND expires_at > $2",
            telegram_id, _to_db_utc(now)
        )
        return _normalize_subscription_row(row) if row else None


async def get_subscription_any(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить подписку пользователя независимо от статуса (активная или истекшая)
    
    Возвращает подписку, если она существует, даже если expires_at <= now.
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscription_any skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription_any skipped")
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1",
            telegram_id
        )
        return _normalize_subscription_row(row) if row else None


async def admin_switch_tariff(telegram_id: int, new_tariff: str, vpn_key_plus: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Заменить тариф подписки (Basic↔Plus) без изменения срока. Только для активной подписки.

    Args:
        telegram_id: ID пользователя
        new_tariff: 'basic' или 'plus'
        vpn_key_plus: для plus — ссылка White List; для basic — None (очищается)

    Returns:
        Обновлённая строка подписки или None, если активной подписки нет.
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, admin_switch_tariff skipped")
        return None
    pool = await get_pool()
    if pool is None:
        return None
    tariff = (new_tariff or "basic").strip().lower()
    if tariff not in config.VALID_SUBSCRIPTION_TYPES:
        tariff = "basic"
    async with pool.acquire() as conn:
        if vpn_key_plus is not None:
            await conn.execute(
                """UPDATE subscriptions SET subscription_type = $1, vpn_key_plus = $2
                   WHERE telegram_id = $3 AND status = 'active'""",
                tariff, vpn_key_plus, telegram_id
            )
        else:
            await conn.execute(
                """UPDATE subscriptions SET subscription_type = $1, vpn_key_plus = NULL
                   WHERE telegram_id = $2 AND status = 'active'""",
                tariff, telegram_id
            )
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
            telegram_id
        )
        return _normalize_subscription_row(row) if row else None


async def has_any_subscription(telegram_id: int) -> bool:
    """Проверить, есть ли у пользователя хотя бы одна подписка (любого статуса)
    
    Returns:
        True если есть хотя бы одна запись в subscriptions, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM subscriptions WHERE telegram_id = $1 LIMIT 1",
            telegram_id
        )
        return row is not None


async def has_any_payment(telegram_id: int) -> bool:
    """Проверить, есть ли у пользователя хотя бы один платёж (любого статуса)
    
    Returns:
        True если есть хотя бы одна запись в payments, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM payments WHERE telegram_id = $1 LIMIT 1",
            telegram_id
        )
        return row is not None


async def has_trial_used(telegram_id: int) -> bool:
    """Проверить, использовал ли пользователь trial-период
    
    Trial считается использованным, если trial_used_at IS NOT NULL
    
    Returns:
        True если trial уже использован, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT trial_used_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
        if not row:
            return False
        return row["trial_used_at"] is not None


async def get_trial_info(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить информацию о trial для пользователя
    
    Returns:
        Dict с trial_used_at и trial_expires_at или None если пользователь не найден
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT trial_used_at, trial_expires_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
        if not row:
            return None
        return {
            "trial_used_at": _from_db_utc(row["trial_used_at"]) if row["trial_used_at"] else None,
            "trial_expires_at": _from_db_utc(row["trial_expires_at"]) if row["trial_expires_at"] else None
        }


async def get_active_paid_subscription(conn, telegram_id: int, now: datetime):
    """Single source of truth: does user have an active paid (non-trial) subscription?
    Paid subscription ALWAYS overrides trial logic. Used by trial_notifications and
    fast_expiry_cleanup to skip trial notifications and trial cleanup when paid exists.
    Returns: row with expires_at or None. Caller must pass existing conn (same transaction)."""
    return await conn.fetchrow("""
        SELECT expires_at FROM subscriptions
        WHERE telegram_id = $1 AND source != 'trial' AND status = 'active' AND expires_at > $2
        LIMIT 1
    """, telegram_id, _to_db_utc(now))


async def mark_trial_used(telegram_id: int, trial_expires_at: datetime) -> bool:
    """Пометить trial как использованный
    
    Args:
        telegram_id: Telegram ID пользователя
        trial_expires_at: Время окончания trial (now + 72 hours)
    
    Returns:
        True если успешно, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                UPDATE users 
                SET trial_used_at = CURRENT_TIMESTAMP,
                    trial_expires_at = $1
                WHERE telegram_id = $2
            """, _to_db_utc(trial_expires_at), telegram_id)
            logger.info(f"Trial marked as used: user={telegram_id}, expires_at={trial_expires_at.isoformat()}")
            return True
        except Exception as e:
            logger.error(f"Error marking trial as used for user {telegram_id}: {e}")
            return False


async def is_eligible_for_trial(telegram_id: int) -> bool:
    """Проверить, может ли пользователь активировать trial-период
    
    Пользователь может активировать trial ТОЛЬКО если:
    - trial_used_at IS NULL (trial ещё не использован)
    
    ВАЖНО: Наличие подписок или платежей НЕ влияет на eligibility.
    Trial может быть активирован даже если есть активная подписка.
    
    Returns:
        True если пользователь может активировать trial, False иначе
    """
    # КРИТИЧНО: Проверяем ТОЛЬКО trial_used_at
    # Наличие подписок или платежей НЕ блокирует trial
    trial_used = await has_trial_used(telegram_id)
    return not trial_used


async def is_trial_available(telegram_id: int) -> bool:
    """Проверить, доступна ли кнопка "Пробный период 3 дня" в главном меню
    
    Кнопка показывается ТОЛЬКО если ВСЕ условия выполнены:
    1. trial_used_at IS NULL (trial ещё не использован)
    2. Нет активной подписки (status='active' AND expires_at > now)
    3. Нет платных подписок в истории (source='payment')
    
    Returns:
        True если кнопка должна быть показана, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        
        # Проверка 1: trial_used_at IS NULL
        user_row = await conn.fetchrow(
            "SELECT trial_used_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
        if not user_row:
            return False
        
        if user_row["trial_used_at"] is not None:
            return False
        
        # Проверка 2: Нет активной подписки
        active_subscription = await conn.fetchrow(
            """SELECT 1 FROM subscriptions 
               WHERE telegram_id = $1 
               AND status = 'active' 
               AND expires_at > $2
               LIMIT 1""",
            telegram_id, _to_db_utc(now)
        )
        if active_subscription:
            return False
        
        # Проверка 3: Нет платных подписок в истории (source='payment')
        paid_subscription = await conn.fetchrow(
            """SELECT 1 FROM subscriptions 
               WHERE telegram_id = $1 
               AND source = 'payment'
               LIMIT 1""",
            telegram_id
        )
        if paid_subscription:
            return False
        
        return True


async def set_special_offer(telegram_id: int) -> bool:
    """Установить спецпредложение для пользователя (3 дня, -15%).

    Вызывается когда подписка истекает (source='payment').
    """
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            now_db = _to_db_utc(datetime.now(timezone.utc))
            await conn.execute(
                "UPDATE users SET special_offer_created_at = $1 WHERE telegram_id = $2",
                now_db, telegram_id
            )
        return True
    except Exception as e:
        logger.warning(f"Failed to set special offer for {telegram_id}: {e}")
        return False


async def get_special_offer_info(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить информацию о спецпредложении пользователя.

    Returns:
        Dict с ключами: created_at, expires_at, remaining_seconds, remaining_text
        или None если спецпредложение не активно или истекло.
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT special_offer_created_at FROM users WHERE telegram_id = $1",
                telegram_id
            )
            if not row or not row["special_offer_created_at"]:
                return None

            created_at = _from_db_utc(row["special_offer_created_at"])
            now = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(days=3)
            remaining = expires_at - now

            if remaining.total_seconds() <= 0:
                return None

            total_seconds = int(remaining.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600

            if days > 0:
                remaining_text = f"{days}д {hours}ч"
            else:
                remaining_text = f"{hours}ч"

            return {
                "created_at": created_at,
                "expires_at": expires_at,
                "remaining_seconds": total_seconds,
                "remaining_text": remaining_text,
                "discount_percent": 15,
            }
    except Exception as e:
        logger.warning(f"Failed to get special offer for {telegram_id}: {e}")
        return None


async def has_active_special_offer(telegram_id: int) -> bool:
    """Проверить, есть ли активное спецпредложение."""
    info = await get_special_offer_info(telegram_id)
    return info is not None


async def get_active_subscription(subscription_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную подписку по ID
    
    Args:
        subscription_id: ID подписки
    
    Returns:
        Словарь с данными подписки или None, если:
        - подписка не найдена
        - статус != "active"
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        row = await conn.fetchrow(
            """SELECT * FROM subscriptions 
               WHERE id = $1 
               AND status = 'active' 
               AND expires_at > $2""",
            subscription_id, _to_db_utc(now)
        )
        return _normalize_subscription_row(row) if row else None


async def update_subscription_uuid(subscription_id: int, new_uuid: str, vpn_key: Optional[str] = None) -> None:
    """Обновить UUID подписки (и vpn_key при перевыпуске)
    
    Args:
        subscription_id: ID подписки
        new_uuid: Новый UUID
        vpn_key: VLESS URL (опционально, при перевыпуске)
    
    Note:
        НЕ меняет статус
        НЕ трогает даты
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if vpn_key is not None:
            await conn.execute(
                "UPDATE subscriptions SET uuid = $1, vpn_key = $2 WHERE id = $3",
                new_uuid, vpn_key, subscription_id
            )
        else:
            await conn.execute(
                "UPDATE subscriptions SET uuid = $1 WHERE id = $2",
                new_uuid, subscription_id
            )
        logger.info(f"Subscription UUID updated: subscription_id={subscription_id}, new_uuid={new_uuid[:8]}...")


async def get_all_active_subscriptions() -> List[Dict[str, Any]]:
    """Получить все активные подписки
    
    Returns:
        Список подписок со статусом 'active' и expires_at > now
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE status = 'active' 
               AND expires_at > $1
               ORDER BY id ASC""",
            _to_db_utc(now)
        )
        return [_normalize_subscription_row(row) for row in rows]


async def reissue_subscription_key(subscription_id: int) -> "Tuple[str, str]":
    """Перевыпустить VPN ключ для подписки (сервисная функция)
    
    Алгоритм:
    1) Получить подписку через get_active_subscription
    2) Если None → выбросить бизнес-ошибку
    3) Сохранить old_uuid
    4) Вызвать reissue_vpn_access(old_uuid) — API returns vless_link (single source of truth)
    5) Получить new_uuid, vless_url
    6) Обновить uuid, vpn_key в БД через update_subscription_uuid
    7) Вернуть (new_uuid, vless_url)
    
    Args:
        subscription_id: ID подписки
    
    Returns:
        (new_uuid, vless_url) — оба из API
    
    Raises:
        ValueError: Если подписка не найдена или не активна
        VPNAPIError: При ошибках VPN API
    """
    # 1. Получаем активную подписку
    subscription = await get_active_subscription(subscription_id)
    if not subscription:
        error_msg = f"Subscription {subscription_id} not found or not active"
        logger.error(f"reissue_subscription_key: {error_msg}")
        raise ValueError(error_msg)
    
    old_uuid = subscription.get("uuid")
    if not old_uuid:
        error_msg = f"Subscription {subscription_id} has no UUID"
        logger.error(f"reissue_subscription_key: {error_msg}")
        raise ValueError(error_msg)
    
    telegram_id = subscription.get("telegram_id")
    uuid_preview = f"{old_uuid[:8]}..." if old_uuid and len(old_uuid) > 8 else (old_uuid or "N/A")
    logger.info(
        f"reissue_subscription_key: START [subscription_id={subscription_id}, "
        f"telegram_id={telegram_id}, old_uuid={uuid_preview}]"
    )
    
    # 2. Перевыпускаем VPN доступ
    expires_at_raw = subscription.get("expires_at")
    expires_at = _ensure_utc(expires_at_raw) if expires_at_raw else None
    if not expires_at:
        error_msg = f"Subscription {subscription_id} has no expires_at"
        logger.error(f"reissue_subscription_key: {error_msg}")
        raise ValueError(error_msg)
    
    try:
        new_uuid, vless_url = await vpn_utils.reissue_vpn_access(
            old_uuid=old_uuid,
            telegram_id=telegram_id,
            subscription_end=expires_at
        )
    except Exception as e:
        logger.error(
            f"reissue_subscription_key: VPN_API_FAILED [subscription_id={subscription_id}, "
            f"telegram_id={telegram_id}, error={str(e)}]"
        )
        raise

    # 3. Обновляем UUID и vpn_key в БД (vless_url from API — single source of truth)
    try:
        await update_subscription_uuid(subscription_id, new_uuid, vpn_key=vless_url)
    except Exception as e:
        logger.error(
            f"reissue_subscription_key: DB_UPDATE_FAILED [subscription_id={subscription_id}, "
            f"telegram_id={telegram_id}, new_uuid={new_uuid[:8]}..., error={str(e)}]"
        )
        # КРИТИЧНО: UUID в VPN API уже обновлён, но БД не обновлена
        # Это несоответствие, но мы не можем откатить VPN API
        raise
    
    new_uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
    logger.info(
        f"reissue_subscription_key: SUCCESS [subscription_id={subscription_id}, "
        f"telegram_id={telegram_id}, old_uuid={uuid_preview}, new_uuid={new_uuid_preview}]"
    )

    return new_uuid, vless_url


async def _log_audit_event_atomic(
    conn,
    action: str,
    telegram_id: int,
    target_user: Optional[int] = None,
    details: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Записать событие аудита в таблицу audit_log
    
    STEP 5 — COMPLIANCE & AUDITABILITY:
    Must be called ONLY within an active transaction.
    
    PART F — FAILURE SAFETY:
    Non-blocking, best-effort. Never throws exceptions.
    
    Args:
        conn: Database connection (within transaction)
        action: Action type (e.g., 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID of the actor
        target_user: Telegram ID of the target user (optional)
        details: Additional details (optional, JSON string)
        correlation_id: Correlation ID for tracing (optional)
    """
    try:
        # STEP 5 — PART F: FAILURE SAFETY
        # Try to insert with correlation_id if column exists, fallback to without it
        try:
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, details, correlation_id)
                   VALUES ($1, $2, $3, $4, $5)""",
                action, telegram_id, target_user, details, correlation_id
            )
        except (asyncpg.UndefinedColumnError, asyncpg.PostgresError):
            # Fallback if correlation_id column doesn't exist yet
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, details)
                   VALUES ($1, $2, $3, $4)""",
                action, telegram_id, target_user, details
            )
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"audit_log table missing or inaccessible — skipping audit log: action={action}, telegram_id={telegram_id}")
    except Exception as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"Error logging audit event: {e}")


async def _log_vpn_lifecycle_audit_async(
    action: str,
    telegram_id: int,
    uuid: Optional[str] = None,
    source: Optional[str] = None,
    result: str = "success",
    details: Optional[str] = None
):
    """
    Записать событие VPN lifecycle в audit_log (async, non-blocking).
    
    Используется для логирования:
    - add_user: создание UUID через VPN API
    - remove_user: удаление UUID через VPN API
    - renew: продление подписки (без создания UUID)
    - expire: автоматическое истечение подписки
    
    Не блокирует основной flow - ошибки логируются, но не пробрасываются.
    
    Args:
        action: Тип действия ('vpn_add_user', 'vpn_remove_user', 'vpn_renew', 'vpn_expire')
        telegram_id: Telegram ID пользователя
        uuid: UUID пользователя (опционально, частично логируется для безопасности)
        source: Источник ('payment', 'admin', 'auto-expiry', 'test')
        result: Результат операции ('success' или 'error')
        details: Дополнительные детали (опционально)
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Безопасное логирование UUID (только первые 8 символов в БД)
            uuid_safe = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or None)
            
            await conn.execute(
                """INSERT INTO audit_log (action, telegram_id, target_user, uuid, source, result, details)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                action, telegram_id, telegram_id, uuid_safe, source, result, details
            )
            logger.debug(
                f"VPN audit logged: action={action}, user={telegram_id}, uuid={uuid_safe}, "
                f"source={source}, result={result}"
            )
    except Exception as e:
        # Не блокируем основной flow при ошибках логирования
        logger.warning(f"Failed to log VPN audit event: action={action}, user={telegram_id}, error={e}")


def _log_vpn_lifecycle_audit_fire_and_forget(
    action: str,
    telegram_id: int,
    uuid: Optional[str] = None,
    source: Optional[str] = None,
    result: str = "success",
    details: Optional[str] = None
):
    """
    Записать событие VPN lifecycle в audit_log (fire-and-forget, не блокирует).
    
    Создаёт async task для логирования, не ожидает завершения.
    Используется когда нужно залогировать событие вне async контекста.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Если event loop уже запущен, создаём task
            asyncio.create_task(
                _log_vpn_lifecycle_audit_async(action, telegram_id, uuid, source, result, details)
            )
        else:
            # Если event loop не запущен, запускаем корутину
            asyncio.run(_log_vpn_lifecycle_audit_async(action, telegram_id, uuid, source, result, details))
    except Exception as e:
        # Не блокируем основной flow
        logger.warning(f"Failed to schedule VPN audit log: action={action}, user={telegram_id}, error={e}")


async def _log_subscription_history_atomic(conn, telegram_id: int, vpn_key: str, start_date: datetime, end_date: datetime, action_type: str):
    """Записать запись в историю подписок
    
    Должна вызываться ТОЛЬКО внутри активной транзакции.
    
    Args:
        conn: Соединение с БД (внутри транзакции)
        telegram_id: Telegram ID пользователя
        vpn_key: VPN-ключ (может быть None для pending activations)
        start_date: Дата начала периода
        end_date: Дата окончания периода
        action_type: Тип действия ('purchase', 'renewal', 'reissue', 'manual_reissue')
    """
    # Пропускаем запись истории для pending activations (vpn_key == None)
    # История будет записана позже, когда activation_worker активирует подписку
    if vpn_key is None:
        logger.info(
            f"SUBSCRIPTION_HISTORY_SKIPPED [reason=pending_activation, user={telegram_id}, "
            f"action={action_type}, subscription_end={end_date.isoformat()}]"
        )
        return
    
    await conn.execute(
        """INSERT INTO subscription_history (telegram_id, vpn_key, start_date, end_date, action_type)
           VALUES ($1, $2, $3, $4, $5)""",
        telegram_id, vpn_key, _to_db_utc(start_date), _to_db_utc(end_date), action_type
    )


async def _log_audit_event_atomic_standalone(
    action: str,
    telegram_id: int,
    target_user: Optional[int] = None,
    details: Optional[str] = None,
    correlation_id: Optional[str] = None
):
    """
    Записать событие аудита в таблицу audit_log (standalone версия)
    
    STEP 5 — COMPLIANCE & AUDITABILITY:
    Creates its own transaction. Used when audit event needs to be logged outside existing transaction.
    
    PART F — FAILURE SAFETY:
    Non-blocking, best-effort. Never throws exceptions.
    
    Args:
        action: Тип действия (например, 'payment_approved', 'payment_rejected', 'vpn_key_issued', 'subscription_renewed')
        telegram_id: Telegram ID администратора, который выполнил действие
        target_user: Telegram ID пользователя, над которым выполнено действие (опционально)
        details: Дополнительные детали действия (опционально, JSON string)
        correlation_id: Correlation ID for tracing (optional)
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), audit log skipped")
        return
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, audit log skipped")
        return
    
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _log_audit_event_atomic(conn, action, telegram_id, target_user, details, correlation_id)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"audit_log table missing or inaccessible — skipping audit log: action={action}, telegram_id={telegram_id}")
    except Exception as e:
        # STEP 5 — PART F: FAILURE SAFETY
        # Log warning but never throw
        logger.warning(f"Error logging audit event (standalone): {e}")


async def reissue_vpn_key_atomic(
    telegram_id: int,
    admin_telegram_id: int,
    correlation_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Атомарно перевыпустить VPN-ключ для пользователя.

    Strict two-phase activation: add_vless_user OUTSIDE DB transaction.
    Phase 1: Session lock, fetch sub, add_vless_user (new UUID).
    Phase 2: DB transaction (UPDATE). On failure: safe_remove_vless_user_with_retry.
    After commit: remove old UUID from Xray.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Session-level lock (prevents concurrent reissue for same user)
        await conn.execute("SELECT pg_advisory_lock($1)", telegram_id)
        try:
            now = datetime.now(timezone.utc)
            subscription_row = await conn.fetchrow(
                """SELECT * FROM subscriptions
                   WHERE telegram_id = $1 AND status = 'active' AND expires_at > $2""",
                telegram_id, _to_db_utc(now)
            )
            if not subscription_row:
                logger.error(f"Cannot reissue VPN key for user {telegram_id}: no active subscription")
                return None, None

            subscription = dict(subscription_row)
            old_uuid = subscription.get("uuid")
            old_vpn_key = subscription.get("vpn_key", "")
            expires_at = _ensure_utc(subscription["expires_at"])
            reissue_tariff = (subscription.get("subscription_type") or "basic").strip().lower()
            if reissue_tariff not in config.VALID_SUBSCRIPTION_TYPES:
                reissue_tariff = "basic"

            # PHASE 1 (outside DB transaction): add_vless_user
            new_uuid = _generate_subscription_uuid()
            vless_result = await vpn_utils.add_vless_user(
                telegram_id=telegram_id,
                subscription_end=expires_at,
                uuid=new_uuid,
                tariff=reissue_tariff,
            )
            new_vpn_key = vless_result.get("vless_url")
            uuid_from_api = vless_result.get("uuid")
            if not uuid_from_api:
                raise RuntimeError("Xray API returned empty UUID")
            new_uuid = uuid_from_api
            if not new_vpn_key:
                raise RuntimeError("Xray API returned empty vless_url")

            logger.info(
                "REISSUE_TWO_PHASE_ACTIVATION",
                extra={"user": telegram_id, "new_uuid": new_uuid[:8] + "...", "phase": "phase1_complete"}
            )

            uuid_to_cleanup_on_failure = new_uuid

            try:
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
                    sub_check = await conn.fetchrow(
                        """SELECT telegram_id FROM subscriptions
                           WHERE telegram_id = $1 AND status = 'active' AND expires_at > $2""",
                        telegram_id, _to_db_utc(now)
                    )
                    if not sub_check:
                        raise Exception("Subscription no longer active")
                    new_subscription_type = (vless_result.get("subscription_type") or reissue_tariff).strip().lower()
                    if new_subscription_type not in config.VALID_SUBSCRIPTION_TYPES:
                        new_subscription_type = "basic"
                    await conn.execute(
                        "UPDATE subscriptions SET uuid = $1, vpn_key = $2, subscription_type = $4 WHERE telegram_id = $3",
                        new_uuid, new_vpn_key, telegram_id, new_subscription_type
                    )
                    await _log_subscription_history_atomic(conn, telegram_id, new_vpn_key, now, expires_at, "manual_reissue")
                    old_key_preview = f"{old_vpn_key[:20]}..." if old_vpn_key and len(old_vpn_key) > 20 else (old_vpn_key or "N/A")
                    new_key_preview = f"{new_vpn_key[:20]}..." if new_vpn_key and len(new_vpn_key) > 20 else (new_vpn_key or "N/A")
                    details = f"User {telegram_id}, Old key: {old_key_preview}, New key: {new_key_preview}, Expires: {expires_at.isoformat()}"
                    await _log_audit_event_atomic(conn, "admin_reissue", admin_telegram_id, telegram_id, details)
            except Exception as tx_err:
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    logger.critical(
                        f"ORPHAN_PREVENTED uuid={uuid_to_cleanup_on_failure[:8]}... reason=reissue_phase2_failed "
                        f"user={telegram_id} error={tx_err}"
                    )
                except Exception as remove_err:
                    logger.critical(
                        f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_to_cleanup_on_failure[:8]}... "
                        f"reason={remove_err} user={telegram_id}"
                    )
                logger.exception(f"Error in reissue_vpn_key_atomic for user {telegram_id}, transaction rolled back")
                raise

            if old_uuid:
                try:
                    await vpn_utils.remove_vless_user(old_uuid)
                    old_uuid_preview = f"{old_uuid[:8]}..." if len(old_uuid) > 8 else "***"
                    logger.info(f"VPN key reissue [action=remove_old, user={telegram_id}, old_uuid={old_uuid_preview}]")
                    try:
                        await _log_vpn_lifecycle_audit_async(
                            action="vpn_remove_user",
                            telegram_id=telegram_id,
                            uuid=old_uuid,
                            source="admin_reissue",
                            result="success",
                            details=f"Old UUID removed after reissue, expires_at={expires_at.isoformat()}"
                        )
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Failed to remove old UUID for user {telegram_id}: {e}")

            new_uuid_preview = f"{new_uuid[:8]}..." if len(new_uuid) > 8 else "***"
            logger.info(
                f"VPN key reissued [action=admin_reissue, user={telegram_id}, admin={admin_telegram_id}, "
                f"new_uuid={new_uuid_preview}]",
                extra={"correlation_id": correlation_id} if correlation_id else {}
            )
            return new_vpn_key, old_vpn_key
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", telegram_id)


"""
SINGLE SOURCE OF TRUTH: grant_access

ЕДИНАЯ ФУНКЦИЯ ВЫДАЧИ ДОСТУПА
Это единственное место, где:
- UUID создаются
- subscription_end изменяется
- VPN API вызывается

КРИТИЧЕСКИЕ ПРАВИЛА:
1. UUID НЕ МЕНЯЕТСЯ пока подписка активна
2. UUID УДАЛЯЕТСЯ немедленно при истечении
3. Admin-подписки ведут себя ИДЕНТИЧНО платным
4. Продление расширяет subscription_end, никогда не заменяет UUID
5. Истекшая подписка → новая покупка → новый UUID
"""


async def grant_access(
    telegram_id: int,
    duration: timedelta,
    source: str,
    admin_telegram_id: Optional[int] = None,
    admin_grant_days: Optional[int] = None,
    conn=None,
    pre_provisioned_uuid: Optional[Dict[str, str]] = None,
    _caller_holds_transaction: bool = False,
    tariff: str = "basic",
    country: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ЕДИНАЯ ФУНКЦИЯ ВЫДАЧИ ДОСТУПА (SINGLE SOURCE OF TRUTH)
    
    Это ЕДИНСТВЕННОЕ место, где:
    - UUID создаются (через vpn_utils.add_vless_user)
    - subscription_end изменяется
    - VPN API вызывается для создания нового UUID
    
    КРИТИЧЕСКИ ВАЖНО: UUID остаётся стабильным при продлении подписки.
    VPN API /add-user вызывается ТОЛЬКО если нет активного UUID.
    
    ЛОГИКА (СТРОГАЯ):
    Step 1: Получить текущую подписку для telegram_id
    
    Step 2: RENEWAL (продление)
    IF subscription exists AND status == "active" AND expires_at > now() AND uuid IS NOT NULL:
        - НЕ вызывать VPN API /add-user
        - НЕ менять UUID (UUID остаётся стабильным)
        - Только: subscription_end = expires_at + duration
        - Обновить БД
        - Вернуть: {uuid: existing, vless_url: None, subscription_end: new_date, action: "renewal"}
        - Результат: VPN соединение НЕ прерывается
    
    Step 3: NEW ISSUANCE (новая выдача)
    IF no subscription OR status == "expired" OR uuid IS NULL:
        - Вызвать VPN API POST /add-user
        - Получить {uuid, vless_url}
        - Создать/обновить подписку:
            - subscription_start = now (activated_at)
            - subscription_end = now + duration
            - status = "active"
            - source = source
            - uuid = new_uuid
            - vpn_key = vless_url
        - Вернуть: {uuid: new, vless_url: new_link, subscription_end: new_date, action: "new_issuance"}
        - Результат: Пользователь получает новый VLESS ключ
    
    ЗАЩИТА ОТ ДВОЙНОГО СОЗДАНИЯ UUID:
    - UUID создаётся ТОЛЬКО в этой функции
    - Проверка активности подписки перед созданием UUID
    - Атомарные транзакции БД
    
    Args:
        telegram_id: Telegram ID пользователя
        duration: Продолжительность доступа (timedelta)
        source: Источник выдачи ('payment', 'admin', 'test')
        admin_telegram_id: Telegram ID администратора (опционально, для admin-источников)
        admin_grant_days: Количество дней для админ-доступа (опционально)
        conn: Соединение с БД (если None, создаётся новое)
        pre_provisioned_uuid: Опционально. При двухфазной активации: {"uuid": str, "vless_url": str, "subscription_type": str}.
            Если задан — add_vless_user НЕ вызывается (UUID уже создан вне транзакции).
        tariff: "basic" или "plus" — тип тарифа для VPN API (и для subscription_type в БД при new issuance).
    
    Returns:
        Dict[str, Any] with keys:
            - "uuid": Optional[str] - UUID (None for pending activation)
            - "vless_url": Optional[str] - VLESS URL (None for renewal, present for new issuance)
            - "subscription_end": datetime - Subscription expiration date
            - "action": str - "renewal", "new_issuance", or "pending_activation"
        
        Guaranteed to return a dict. Never returns None.
    
    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
    """
    _acquired_pool = None
    if conn is None:
        _acquired_pool = await get_pool()
        conn = await _acquired_pool.acquire()
        should_release_conn = True
    else:
        should_release_conn = False
    
    try:
        now = datetime.now(timezone.utc)
        
        # Логируем начало операции с полными данными
        duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
        logger.info(f"grant_access: START [telegram_id={telegram_id}, source={source}, duration={duration_str}]")
        
        # =====================================================================
        # STEP 1: Получить текущую подписку
        # =====================================================================
        subscription_row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1",
            telegram_id
        )
        subscription = dict(subscription_row) if subscription_row else None
        logger.debug(f"grant_access: GET_SUBSCRIPTION [user={telegram_id}, exists={subscription is not None}]")
        
        # Определяем статус подписки
        if subscription:
            expires_at_raw = subscription.get("expires_at")
            expires_at = _ensure_utc(expires_at_raw) if expires_at_raw else None
            db_status = subscription.get("status")
            uuid = subscription.get("uuid")
            
            # КРИТИЧЕСКАЯ ПРОВЕРКА: Подписка активна ТОЛЬКО если:
            # 1. status = 'active' И
            # 2. expires_at > now() И
            # 3. uuid IS NOT NULL
            is_active = (
                db_status == "active" and
                expires_at and
                expires_at > now and
                uuid is not None
            )
            
            if not is_active:
                # Подписка неактивна (истекла или нет UUID)
                status = "expired"
            else:
                status = "active"
        else:
            status = None
            expires_at = None
            uuid = None
        
        # =====================================================================
        # STEP 2: Активная подписка - ПРОДЛЕНИЕ (без создания нового UUID)
        # =====================================================================
        # КРИТИЧЕСКОЕ ПРОВЕРКА: Подписка активна если:
        # 1. subscription существует
        # 2. status == 'active'
        # 3. expires_at > now() (не истекла)
        # 4. uuid IS NOT NULL (UUID существует)
        if subscription and status == "active" and uuid and expires_at and expires_at > now:
            current_sub_type = (subscription.get("subscription_type") or "basic").strip().lower()
            incoming_tariff = (tariff or "basic").strip().lower()

            # Basic→Plus upgrade: same UUID, call upgrade_vless_user, update vpn_key, vpn_key_plus, subscription_type, extend dates
            if source == "payment" and incoming_tariff == "plus" and current_sub_type == "basic":
                logger.info(
                    f"grant_access: BASIC_TO_PLUS_UPGRADE [user={telegram_id}, uuid={uuid[:8]}..., source={source}]"
                )
                try:
                    upgrade_result = await vpn_utils.upgrade_vless_user(uuid)
                    new_vpn_key = upgrade_result.get("vless_url")
                    new_vpn_key_plus = upgrade_result.get("vless_url_plus")
                    if not new_vpn_key:
                        raise Exception("upgrade_vless_user did not return vless_url (basic_link)")
                    old_expires_at = expires_at
                    subscription_end = max(expires_at, now) + duration
                    _start_raw = subscription.get("activated_at") or subscription.get("expires_at") or now
                    subscription_start = _ensure_utc(_start_raw) if _start_raw else now
                    if subscription_end <= old_expires_at:
                        raise Exception(f"Invalid upgrade: new_end={subscription_end} <= old_end={old_expires_at}")
                    await conn.execute(
                        """UPDATE subscriptions
                           SET expires_at = $1, vpn_key = $2, vpn_key_plus = $3, subscription_type = 'plus',
                               status = 'active', source = $4,
                               reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE,
                               reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, activation_status = 'active'
                           WHERE telegram_id = $5""",
                        _to_db_utc(subscription_end), new_vpn_key, new_vpn_key_plus, source, telegram_id
                    )
                    await _log_subscription_history_atomic(conn, telegram_id, new_vpn_key, subscription_start, subscription_end, "renewal")
                    logger.info(
                        f"grant_access: BASIC_TO_PLUS_UPGRADE_SUCCESS [user={telegram_id}, uuid={uuid[:8]}..., "
                        f"new_expires={subscription_end.isoformat()}]"
                    )
                    return {
                        "uuid": uuid,
                        "vless_url": new_vpn_key,
                        "vpn_key": new_vpn_key,
                        "vpn_key_plus": new_vpn_key_plus,
                        "subscription_end": subscription_end,
                        "action": "renewal",
                        "subscription_type": "plus",
                        "is_basic_to_plus_upgrade": True,
                    }
                except Exception as e:
                    logger.error(f"grant_access: BASIC_TO_PLUS_UPGRADE_FAILED [user={telegram_id}, error={e}]")
                    raise Exception(f"Basic→Plus upgrade failed: {e}") from e

            # Plus→Basic downgrade: remove from plus inbound, set vpn_key_plus=NULL, subscription_type=basic, extend dates
            if source == "payment" and incoming_tariff == "basic" and current_sub_type == "plus":
                logger.info(
                    f"grant_access: PLUS_TO_BASIC_DOWNGRADE [user={telegram_id}, uuid={uuid[:8]}..., source={source}]"
                )
                try:
                    await vpn_utils.remove_plus_inbound(uuid)
                except Exception as e:
                    logger.error(f"grant_access: PLUS_TO_BASIC_DOWNGRADE remove_plus_inbound failed [user={telegram_id}, error={e}]")
                    raise Exception(f"Plus→Basic downgrade (remove plus) failed: {e}") from e
                old_expires_at = expires_at
                subscription_end = max(expires_at, now) + duration
                _start_raw = subscription.get("activated_at") or subscription.get("expires_at") or now
                subscription_start = _ensure_utc(_start_raw) if _start_raw else now
                if subscription_end <= old_expires_at:
                    raise Exception(f"Invalid downgrade: new_end={subscription_end} <= old_end={old_expires_at}")
                vpn_key_basic = subscription.get("vpn_key")
                await conn.execute(
                    """UPDATE subscriptions
                       SET expires_at = $1, vpn_key_plus = NULL, subscription_type = 'basic',
                           status = 'active', source = $2,
                           reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE,
                           reminder_3h_sent = FALSE, reminder_6h_sent = FALSE, activation_status = 'active'
                       WHERE telegram_id = $3""",
                    _to_db_utc(subscription_end), source, telegram_id
                )
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key_basic or uuid, subscription_start, subscription_end, "renewal")
                logger.info(
                    f"grant_access: PLUS_TO_BASIC_DOWNGRADE_SUCCESS [user={telegram_id}, uuid={uuid[:8]}..., "
                    f"new_expires={subscription_end.isoformat()}]"
                )
                return {
                    "uuid": uuid,
                    "vless_url": vpn_key_basic,
                    "vpn_key": vpn_key_basic,
                    "vpn_key_plus": None,
                    "subscription_end": subscription_end,
                    "action": "renewal",
                    "subscription_type": "basic",
                }

            # UUID СТАБИЛЕН - продлеваем подписку БЕЗ вызова VPN API (renewal same tariff)
            logger.info(
                f"grant_access: RENEWAL_DETECTED [user={telegram_id}, current_expires={expires_at.isoformat()}, "
                f"uuid={uuid[:8] if uuid else 'N/A'}..., source={source}] - "
                "Active subscription found, will EXTEND without UUID regeneration"
            )
            # ЗАЩИТА: Не продлеваем если UUID отсутствует (не должно быть, но на всякий случай)
            if not uuid:
                logger.warning(
                    f"grant_access: WARNING_ACTIVE_WITHOUT_UUID [user={telegram_id}, "
                    f"will create new UUID instead of renewal]"
                )
                # Переходим к созданию нового UUID (Step 3)
            else:
                # UUID НЕ МЕНЯЕТСЯ - только продлеваем subscription_end
                old_expires_at = expires_at
                subscription_end = max(expires_at, now) + duration
                # subscription_start сохраняется (activated_at не меняется при продлении)
                _start_raw = subscription.get("activated_at") or subscription.get("expires_at") or now
                subscription_start = _ensure_utc(_start_raw) if _start_raw else now
                
                # ВАЛИДАЦИЯ: Проверяем что subscription_end увеличен
                if subscription_end <= old_expires_at:
                    error_msg = f"Invalid renewal: new_end={subscription_end} <= old_end={old_expires_at} for user {telegram_id}"
                    logger.error(f"grant_access: ERROR_INVALID_RENEWAL [user={telegram_id}, error={error_msg}]")
                    raise Exception(error_msg)
                
                logger.info(
                    f"grant_access: RENEWING_SUBSCRIPTION [user={telegram_id}, old_expires={old_expires_at.isoformat()}, "
                    f"new_expires={subscription_end.isoformat()}, extension_days={duration.days}, uuid={uuid[:8]}...] - "
                    "Extending subscription WITHOUT calling VPN API /add-user"
                )
                
                # B1 FIX: TWO-PHASE RENEWAL — DB first (source of truth), Xray sync OUTSIDE transaction.
                # ensure_user_in_xray must NEVER run inside active DB transaction (pool exhaustion).
                # When caller holds tx: return renewal_xray_sync_after_commit; callers run sync post-commit.
                # When standalone: run ensure_user_in_xray after DB update (no tx held).
                assert subscription_end.tzinfo is not None, "subscription_end must be timezone-aware"
                assert subscription_end.tzinfo == timezone.utc, "subscription_end must be UTC"
                expiry_ms = int(subscription_end.timestamp() * 1000)
                logger.info(f"XRAY_UUID_FLOW [user={telegram_id}, uuid={uuid[:8]}..., operation=renewal_db_first]")

                # PHASE 1: DB update (inside caller's tx if any)
                # UUID НЕ МЕНЯЕТСЯ - VPN соединение продолжает работать без перерыва
                try:
                    await conn.execute(
                        """UPDATE subscriptions
                           SET expires_at = $1,
                               uuid = $4,
                               status = 'active',
                               source = $2,
                               subscription_type = COALESCE($5, subscription_type),
                               reminder_sent = FALSE,
                               reminder_3d_sent = FALSE,
                               reminder_24h_sent = FALSE,
                               reminder_3h_sent = FALSE,
                               reminder_6h_sent = FALSE,
                               activation_status = 'active'
                           WHERE telegram_id = $3""",
                        _to_db_utc(subscription_end), source, telegram_id, uuid, incoming_tariff
                    )
                    
                    # ВАЛИДАЦИЯ: Проверяем что запись обновлена
                    updated_subscription = await conn.fetchrow(
                        "SELECT expires_at, status, uuid FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    if not updated_subscription or _from_db_utc(updated_subscription["expires_at"]) != subscription_end:
                        error_msg = f"Failed to verify subscription renewal for user {telegram_id}"
                        logger.error(f"grant_access: ERROR_RENEWAL_VERIFICATION [user={telegram_id}, error={error_msg}]")
                        raise Exception(error_msg)
                    
                    logger.info(
                        f"grant_access: RENEWAL_SYNC_SUCCESS [telegram_id={telegram_id}, uuid={uuid[:8]}..., "
                        f"old_expiry={old_expires_at.isoformat()}, new_expiry={subscription_end.isoformat()}, "
                        f"expiry_timestamp_ms={expiry_ms}]"
                    )
                    logger.info(
                        f"grant_access: RENEWAL_SAVED_SUCCESS [user={telegram_id}, "
                        f"subscription_end={updated_subscription['expires_at'].isoformat()}, "
                        f"status={updated_subscription['status']}, uuid={uuid[:8]}...]"
                    )
                except Exception as e:
                    logger.error(f"grant_access: RENEWAL_SAVE_FAILED [user={telegram_id}, error={str(e)}]")
                    raise Exception(f"Failed to renew subscription in database: {e}") from e
                
                # WHY: При оплате во время trial явно завершаем trial и логируем — trial_notifications/cleanup не должны трогать paid
                if source == "payment":
                    user_row = await conn.fetchrow("SELECT trial_expires_at FROM users WHERE telegram_id = $1", telegram_id)
                    old_trial_expires_at = user_row["trial_expires_at"] if user_row else None
                    if old_trial_expires_at and _from_db_utc(old_trial_expires_at) > now:
                        await conn.execute(
                            "UPDATE users SET trial_expires_at = $1 WHERE telegram_id = $2 AND trial_expires_at > $1",
                            _to_db_utc(now), telegram_id
                        )
                        logger.info(
                            f"TRIAL_OVERRIDDEN_BY_PAID_SUBSCRIPTION: user_id={telegram_id}, "
                            f"old_trial_expires_at={old_trial_expires_at.isoformat()}, "
                            f"paid_subscription_expires_at={subscription_end.isoformat()}"
                        )
                
                # Определяем action_type для истории
                if source == "payment":
                    history_action_type = "renewal"
                elif source == "admin":
                    history_action_type = "admin_grant"
                else:
                    history_action_type = source
                
                # Записываем в историю подписок
                vpn_key = subscription.get("vpn_key") or subscription.get("uuid", "")
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key, subscription_start, subscription_end, history_action_type)
                
                # Audit log
                if admin_telegram_id:
                    duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
                    uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                    details = f"Renewed access: {duration_str} via {source}, Expires: {subscription_end.isoformat()}, UUID: {uuid_preview}"
                    await _log_audit_event_atomic(conn, "subscription_renewed", admin_telegram_id, telegram_id, details)
                
                # Безопасное логирование UUID (только первые 8 символов)
                uuid_preview = f"{uuid[:8]}..." if uuid and len(uuid) > 8 else (uuid or "N/A")
                duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
                extension_days = (subscription_end - old_expires_at).days if old_expires_at else duration.days
                logger.info(
                    f"grant_access: RENEWAL_SUCCESS [action=renewal, telegram_id={telegram_id}, uuid={uuid_preview}, "
                    f"subscription_start={subscription_start.isoformat()}, old_expires={old_expires_at.isoformat()}, "
                    f"new_expires={subscription_end.isoformat()}, extension={extension_days} days, "
                    f"source={source}, duration={duration_str}]"
                )
                logger.info(
                    f"grant_access: UUID_STABLE [action=renewal, telegram_id={telegram_id}, uuid={uuid_preview}] - "
                    "UUID preserved, VPN connection will NOT be interrupted"
                )
                
                # VPN AUDIT LOG: Логируем продление подписки (без создания UUID)
                try:
                    await _log_vpn_lifecycle_audit_async(
                        action="vpn_renew",
                        telegram_id=telegram_id,
                        uuid=uuid,
                        source=source,
                        result="success",
                        details=f"Subscription renewed, old_expires={old_expires_at.isoformat()}, new_expires={subscription_end.isoformat()}, extension={extension_days} days"
                    )
                except Exception as e:
                    logger.warning(f"Failed to log VPN renew audit (non-blocking): {e}")
                
                result_dict = {
                    "uuid": uuid,
                    "vless_url": None,  # Не новый UUID
                    "vpn_key": subscription.get("vpn_key"),  # Используем существующий из БД (от API при issuance)
                    "subscription_end": subscription_end,
                    "action": "renewal",  # Явно указываем тип операции
                    "subscription_type": incoming_tariff,
                }
                if _caller_holds_transaction:
                    # B1: Caller holds tx — ensure_user_in_xray MUST run post-commit. Defer to caller.
                    result_dict["renewal_xray_sync_after_commit"] = {
                        "telegram_id": telegram_id,
                        "uuid": uuid,
                        "subscription_end": subscription_end
                    }
                    return result_dict
                # Standalone: no transaction held — safe to call ensure_user_in_xray here.
                try:
                    await vpn_utils.ensure_user_in_xray(
                        telegram_id=telegram_id,
                        uuid=uuid,
                        subscription_end=subscription_end
                    )
                except Exception as e:
                    logger.critical(
                        "RENEWAL_XRAY_SYNC_FAILED",
                        extra={"telegram_id": telegram_id, "uuid": uuid[:8] + "...", "error": str(e)[:200]}
                    )
                return result_dict
        
        # =====================================================================
        # STEP 3: Новая выдача доступа - создаём новый UUID
        # =====================================================================
        # Сюда попадаем если:
        # - подписки нет
        # - подписка истекла (expires_at <= now)
        # - статус не 'active'
        # - UUID отсутствует
        
        logger.info(
            f"grant_access: NEW_ISSUANCE_REQUIRED [user={telegram_id}, source={source}, "
            f"reason=no_active_subscription_or_expired] - "
            "Will create NEW UUID via VPN API /add-user"
        )
        
        # ЗАЩИТА: Проверяем доступность VPN API перед созданием UUID
        import config
        if not config.VPN_ENABLED:
            # PREMIUM FLOW: Delayed activation - create subscription with pending status
            # Payment succeeds, subscription is created, but VPN key will be generated later
            logger.info(
                f"grant_access: ACTIVATION_PENDING [user={telegram_id}, source={source}, "
                f"reason=VPN_API_not_available] - "
                "Creating subscription with pending activation status"
            )
            
            # Вычисляем даты
            subscription_start = now
            subscription_end = now + duration
            
            # ВАЛИДАЦИЯ: Проверяем что subscription_end вычислен корректно
            if not subscription_end or subscription_end <= subscription_start:
                error_msg = f"Invalid subscription_end for user {telegram_id}: start={subscription_start}, end={subscription_end}"
                logger.error(f"grant_access: ERROR_INVALID_DATES [user={telegram_id}, error={error_msg}]")
                raise Exception(error_msg)
            
            logger.info(
                f"grant_access: CALCULATED_DATES [user={telegram_id}, subscription_start={subscription_start.isoformat()}, "
                f"subscription_end={subscription_end.isoformat()}, duration_days={duration.days}]"
            )
            
            # Определяем action_type для истории
            if source == "payment":
                history_action_type = "purchase"
            elif source == "admin":
                history_action_type = "admin_grant"
            else:
                history_action_type = source
            
            # Сохраняем подписку с pending activation status
            try:
                pending_sub_type = (tariff or "basic").strip().lower()
                await conn.execute(
                    """INSERT INTO subscriptions (
                           telegram_id, uuid, vpn_key, expires_at, status, source,
                           reminder_sent, reminder_3d_sent, reminder_24h_sent,
                           reminder_3h_sent, reminder_6h_sent, admin_grant_days,
                           activated_at, last_bytes,
                           trial_notif_6h_sent, trial_notif_18h_sent, trial_notif_30h_sent,
                           trial_notif_42h_sent, trial_notif_54h_sent, trial_notif_60h_sent,
                           trial_notif_71h_sent,
                           activation_status, activation_attempts, last_activation_error,
                           country, subscription_type
                       )
                       VALUES ($1, NULL, NULL, $2, 'active', $3, FALSE, FALSE, FALSE, FALSE, FALSE, $4, $5, 0,
                               FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
                               'pending', 0, NULL, $6, $7)
                       ON CONFLICT (telegram_id)
                       DO UPDATE SET
                           expires_at = $2,
                           status = 'active',
                           source = $3,
                           reminder_sent = FALSE,
                           reminder_3d_sent = FALSE,
                           reminder_24h_sent = FALSE,
                           reminder_3h_sent = FALSE,
                           reminder_6h_sent = FALSE,
                           admin_grant_days = $4,
                           activated_at = $5,
                           last_bytes = 0,
                           trial_notif_6h_sent = FALSE,
                           trial_notif_18h_sent = FALSE,
                           trial_notif_30h_sent = FALSE,
                           trial_notif_42h_sent = FALSE,
                           trial_notif_54h_sent = FALSE,
                           trial_notif_60h_sent = FALSE,
                           trial_notif_71h_sent = FALSE,
                           activation_status = 'pending',
                           activation_attempts = 0,
                           last_activation_error = NULL,
                           uuid = NULL,
                           vpn_key = NULL,
                           country = COALESCE($6, subscriptions.country),
                           subscription_type = COALESCE($7, subscriptions.subscription_type)""",
                    telegram_id, _to_db_utc(subscription_end), source, admin_grant_days, _to_db_utc(subscription_start), country, pending_sub_type
                )
                
                # ВАЛИДАЦИЯ: Проверяем что запись действительно сохранена
                saved_subscription = await conn.fetchrow(
                    "SELECT expires_at, status, activation_status FROM subscriptions WHERE telegram_id = $1",
                    telegram_id
                )
                if not saved_subscription or _from_db_utc(saved_subscription["expires_at"]) != subscription_end:
                    error_msg = f"Failed to verify subscription save for user {telegram_id}"
                    logger.error(f"grant_access: ERROR_DB_VERIFICATION [user={telegram_id}, error={error_msg}]")
                    raise Exception(error_msg)
                
                subscription_id = await conn.fetchval(
                    "SELECT id FROM subscriptions WHERE telegram_id = $1",
                    telegram_id
                )
                
                logger.info(
                    f"grant_access: ACTIVATION_PENDING [user={telegram_id}, subscription_id={subscription_id}, "
                    f"subscription_end={saved_subscription['expires_at'].isoformat()}, "
                    f"status={saved_subscription['status']}, activation_status={saved_subscription.get('activation_status', 'pending')}]"
                )
            except Exception as e:
                logger.error(
                    f"grant_access: DB_SAVE_FAILED [user={telegram_id}, error={str(e)}]"
                )
                raise Exception(f"Failed to save subscription to database: {e}") from e
            
            # Записываем в историю подписок (без VPN ключа)
            await _log_subscription_history_atomic(conn, telegram_id, None, subscription_start, subscription_end, history_action_type)
            
            # Audit log
            if admin_telegram_id:
                duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
                details = f"Granted {duration_str} access via {source}, Expires: {subscription_end.isoformat()}, Activation: pending (VPN API unavailable)"
                await _log_audit_event_atomic(conn, "subscription_created", admin_telegram_id, telegram_id, details)
            
            duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
            logger.info(
                f"grant_access: PENDING_ACTIVATION_SUCCESS [action=pending_activation, telegram_id={telegram_id}, "
                f"subscription_end={subscription_end.isoformat()}, duration={duration_str}, source={source}]"
            )
            
            return {
                "uuid": None,
                "vless_url": None,
                "subscription_end": subscription_end,
                "action": "pending_activation"
            }
        
        # Capture old UUID for removal AFTER transaction commits (no external call inside tx).
        old_uuid_to_remove_after_commit = uuid if uuid else None
        
        # Вычисляем subscription_end ДО вызова VPN API (передаётся в Xray как expiryTime)
        subscription_start = now
        subscription_end = now + duration
        assert subscription_end.tzinfo is not None, "subscription_end must be timezone-aware"
        assert subscription_end.tzinfo == timezone.utc, "subscription_end must be UTC"
        duration_days = duration.days
        expiry_ms = int(subscription_end.timestamp() * 1000)
        logger.info(
            f"grant_access: CALCULATED_DATES [user={telegram_id}, subscription_end={subscription_end.isoformat()}, "
            f"duration_days={duration_days}, expiry_timestamp_ms={expiry_ms}]"
        )
        
        # INVARIANT: add_vless_user must NEVER run inside DB transaction (orphan UUID risk).
        if _caller_holds_transaction and (not pre_provisioned_uuid or not pre_provisioned_uuid.get("uuid")):
            raise RuntimeError(
                "INVARIANT_VIOLATION: add_vless_user must never run inside DB transaction. "
                "Caller holds transaction but did not provide pre_provisioned_uuid. "
                "Use two-phase activation: Phase 1 add_vless_user outside tx, Phase 2 grant_access with pre_provisioned_uuid."
            )
        vless_result = None  # set by add_vless_user path; None when using pre_provisioned_uuid
        # TWO-PHASE: If caller provided pre_provisioned_uuid, use it — NEVER call add_vless_user inside transaction.
        vless_url_plus = None
        if pre_provisioned_uuid and pre_provisioned_uuid.get("uuid") and pre_provisioned_uuid.get("vless_url"):
            new_uuid = pre_provisioned_uuid["uuid"].strip()
            vless_url = pre_provisioned_uuid["vless_url"]
            vless_url_plus = pre_provisioned_uuid.get("vless_url_plus")
            uuid_from_api = new_uuid
            pending_activation = False
            uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
            logger.info(
                f"grant_access: TWO_PHASE_PRE_PROVISIONED [user={telegram_id}, uuid={uuid_preview}, "
                f"source={source}] — using externally provisioned UUID, skipping add_vless_user"
            )
        else:
            # Generate UUID for API request; Xray response overrides (Xray is source of truth).
            vless_url_plus = None
            new_uuid = _generate_subscription_uuid()
            assert new_uuid is not None, "UUID generation failed"
            logger.info(f"XRAY_UUID_FLOW [user={telegram_id}, uuid={new_uuid[:8]}..., operation=add]")
            logger.info(f"grant_access: CALLING_VPN_API [action=add_user, user={telegram_id}, uuid={new_uuid[:8]}..., subscription_end={subscription_end.isoformat()}, source={source}]")

            import asyncio
            MAX_VPN_RETRIES = 2
            RETRY_DELAY_SECONDS = 1.0

            last_exception = None
            vless_result = None
            vless_url = None
            uuid_from_api = None  # Xray API is canonical; override any pre-generated UUID

            for attempt in range(MAX_VPN_RETRIES + 1):
                if attempt > 0:
                    delay = RETRY_DELAY_SECONDS * attempt
                    logger.info(
                        f"grant_access: VPN_API_RETRY [user={telegram_id}, attempt={attempt + 1}/{MAX_VPN_RETRIES + 1}, "
                        f"delay={delay}s, previous_error={str(last_exception)}]"
                    )
                    await asyncio.sleep(delay)

                try:
                    # VPN API call - config.VPN_ENABLED already checked above
                    vless_result = await vpn_utils.add_vless_user(
                        telegram_id=telegram_id,
                        subscription_end=subscription_end,
                        uuid=new_uuid,
                        tariff=tariff,
                    )
                    vless_url = vless_result.get("vless_url")
                    vless_url_plus = vless_result.get("vless_url_plus")
                    uuid_from_api = vless_result.get("uuid")
                    if not uuid_from_api:
                        raise RuntimeError("Xray API returned empty UUID")
                    new_uuid = uuid_from_api  # HARD OVERRIDE

                    # ВАЛИДАЦИЯ: Проверяем что UUID и VLESS URL получены (new_uuid now from API)
                    if not new_uuid:
                        error_msg = f"VPN API returned empty UUID for user {telegram_id}"
                        logger.error(f"grant_access: ERROR_VPN_API_RESPONSE [user={telegram_id}, attempt={attempt + 1}, error={error_msg}]")
                        last_exception = Exception(error_msg)
                        if attempt < MAX_VPN_RETRIES:
                            continue
                        raise last_exception

                    if not vless_url:
                        error_msg = f"VPN API returned empty vless_url for user {telegram_id}"
                        logger.error(f"grant_access: ERROR_VPN_API_RESPONSE [user={telegram_id}, attempt={attempt + 1}, error={error_msg}]")
                        last_exception = Exception(error_msg)
                        if attempt < MAX_VPN_RETRIES:
                            continue
                        raise last_exception

                    uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
                    logger.info(
                        f"grant_access: ACTIVATION_IMMEDIATE_SUCCESS [action=add_user, user={telegram_id}, uuid={uuid_preview}, "
                        f"source={source}, attempt={attempt + 1}, vless_url_length={len(vless_url) if vless_url else 0}]"
                    )
                    break  # Успех - выходим из цикла retry

                except Exception as e:
                    last_exception = e
                    logger.error(
                        f"grant_access: VPN_API_FAILED [action=add_user_failed, user={telegram_id}, "
                        f"source={source}, attempt={attempt + 1}/{MAX_VPN_RETRIES + 1}, error={str(e)}]"
                    )
                    if attempt < MAX_VPN_RETRIES:
                        continue
                    error_msg = f"Failed to create VPN access after {MAX_VPN_RETRIES + 1} attempts: {e}"
                    logger.error(
                        f"grant_access: VPN_API_ALL_RETRIES_FAILED [user={telegram_id}, source={source}, "
                        f"attempts={MAX_VPN_RETRIES + 1}, final_error={str(e)}]"
                    )
                    try:
                        await _log_vpn_lifecycle_audit_async(
                            action="vpn_add_user",
                            telegram_id=telegram_id,
                            uuid=None,
                            source=source,
                            result="error",
                            details=f"VPN API call failed after {MAX_VPN_RETRIES + 1} attempts: {str(e)}"
                        )
                    except Exception:
                        pass
                    raise Exception(error_msg) from e

        # subscription_type for DB: from vless_result, pre_provisioned_uuid, or tariff
        subscription_type_value = "basic"
        if vless_result:
            subscription_type_value = (vless_result.get("subscription_type") or tariff or "basic").strip().lower()
        elif pre_provisioned_uuid:
            subscription_type_value = (pre_provisioned_uuid.get("subscription_type") or tariff or "basic").strip().lower()
        else:
            subscription_type_value = (tariff or "basic").strip().lower()
        if subscription_type_value not in config.VALID_SUBSCRIPTION_TYPES:
            subscription_type_value = "basic"

        # Defensive: UUID must be resolved after successful provisioning
        if not new_uuid:
            raise RuntimeError("UUID resolution failed after VPN provisioning")

        # PART D.7: Handle case where VPN API is disabled (no vless_url)
        # If VPN API is disabled, set activation_status to 'pending' instead of raising error
        if not new_uuid or not vless_url:
            # VPN API call failed - mark as pending
            logger.warning(
                f"grant_access: VPN_API_CALL_FAILED [user={telegram_id}] - "
                f"setting activation_status='pending'"
            )
            pending_activation = True
        else:
            pending_activation = False
        
        # subscription_start, subscription_end уже вычислены выше (перед VPN API вызовом)
        # ВАЛИДАЦИЯ: Проверяем что subscription_end вычислен корректно
        if not subscription_end or subscription_end <= subscription_start:
            error_msg = f"Invalid subscription_end for user {telegram_id}: start={subscription_start}, end={subscription_end}"
            logger.error(f"grant_access: ERROR_INVALID_DATES [user={telegram_id}, error={error_msg}]")
            raise Exception(error_msg)
        
        logger.info(
            f"grant_access: CALCULATED_DATES [user={telegram_id}, subscription_start={subscription_start.isoformat()}, "
            f"subscription_end={subscription_end.isoformat()}, duration_days={duration.days}]"
        )
        
        # Определяем action_type для истории
        if source == "payment":
            history_action_type = "purchase"
        elif source == "admin":
            history_action_type = "admin_grant"
        else:
            history_action_type = source
        
        # ВАЛИДАЦИЯ: Запрещено выдавать ключ без записи в БД
        # Defensive: ensure UUID override was applied (Xray is canonical)
        if not pending_activation and uuid_from_api is not None:
            if new_uuid != uuid_from_api:
                raise RuntimeError("UUID override failed – inconsistent state")
        uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
        logger.info(
            f"grant_access: SAVING_TO_DB [user={telegram_id}, uuid={uuid_preview}, "
            f"subscription_start={subscription_start.isoformat()}, subscription_end={subscription_end.isoformat()}, "
            f"status=active, source={source}]"
        )
        
        # Сохраняем/обновляем подписку
        try:
            # vless_url_plus already set in pre_provisioned path; in add_user path set from vless_result
            if vless_result is not None:
                vless_url_plus = vless_result.get("vless_url_plus")
            activation_status_value = 'pending' if pending_activation else 'active'
            args = (telegram_id, new_uuid, vless_url, vless_url_plus, _to_db_utc(subscription_end), source, admin_grant_days, _to_db_utc(subscription_start), activation_status_value, subscription_type_value, country)
            logger.debug(
                f"grant_access: SQL_ARGS_COUNT [user={telegram_id}, "
                f"placeholders=11, args_count={len(args)}, "
                f"activation_status={activation_status_value}, subscription_type={subscription_type_value}, country={country}]"
            )

            await conn.execute(
                """INSERT INTO subscriptions (
                       telegram_id, uuid, vpn_key, vpn_key_plus, expires_at, status, source,
                       reminder_sent, reminder_3d_sent, reminder_24h_sent,
                       reminder_3h_sent, reminder_6h_sent, admin_grant_days,
                       activated_at, last_bytes,
                       trial_notif_6h_sent, trial_notif_18h_sent, trial_notif_30h_sent,
                       trial_notif_42h_sent, trial_notif_54h_sent, trial_notif_60h_sent,
                       trial_notif_71h_sent,
                       activation_status, activation_attempts, last_activation_error,
                       subscription_type, country
                   )
                   VALUES ($1, $2, $3, $4, $5, 'active', $6, FALSE, FALSE, FALSE, FALSE, FALSE, $7, $8, 0,
                           FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
                           $9, 0, NULL, $10, $11)
                   ON CONFLICT (telegram_id)
                   DO UPDATE SET
                       uuid = COALESCE($2, subscriptions.uuid),
                       vpn_key = COALESCE($3, subscriptions.vpn_key),
                       vpn_key_plus = COALESCE($4, subscriptions.vpn_key_plus),
                       expires_at = $5,
                       status = 'active',
                       source = $6,
                       reminder_sent = FALSE,
                       reminder_3d_sent = FALSE,
                       reminder_24h_sent = FALSE,
                       reminder_3h_sent = FALSE,
                       reminder_6h_sent = FALSE,
                       admin_grant_days = $7,
                       activated_at = COALESCE($8, subscriptions.activated_at),
                       last_bytes = 0,
                       trial_notif_6h_sent = FALSE,
                       trial_notif_18h_sent = FALSE,
                       trial_notif_30h_sent = FALSE,
                       trial_notif_42h_sent = FALSE,
                       trial_notif_54h_sent = FALSE,
                       trial_notif_60h_sent = FALSE,
                       trial_notif_71h_sent = FALSE,
                       activation_status = $9,
                       activation_attempts = 0,
                       last_activation_error = NULL,
                       subscription_type = COALESCE($10, subscriptions.subscription_type),
                       country = COALESCE($11, subscriptions.country)""",
                *args
            )
            
            # ВАЛИДАЦИЯ: Проверяем что запись действительно сохранена
            saved_subscription = await conn.fetchrow(
                "SELECT uuid, expires_at, status FROM subscriptions WHERE telegram_id = $1",
                telegram_id
            )
            if not saved_subscription or saved_subscription["uuid"] != new_uuid:
                error_msg = f"Failed to verify subscription save for user {telegram_id}"
                logger.error(f"grant_access: ERROR_DB_VERIFICATION [user={telegram_id}, error={error_msg}]")
                raise Exception(error_msg)
            
            logger.info(
                f"grant_access: DB_SAVED_SUCCESS [user={telegram_id}, uuid={uuid_preview}, "
                f"subscription_end={saved_subscription['expires_at'].isoformat()}, status={saved_subscription['status']}]"
            )
        except Exception as e:
            logger.error(
                f"grant_access: DB_SAVE_FAILED [user={telegram_id}, uuid={uuid_preview}, error={str(e)}]"
            )
            raise Exception(f"Failed to save subscription to database: {e}") from e
        
        # WHY: При оплате во время trial явно завершаем trial и логируем — trial_notifications/cleanup не должны трогать paid
        if source == "payment":
            user_row = await conn.fetchrow("SELECT trial_expires_at FROM users WHERE telegram_id = $1", telegram_id)
            old_trial_expires_at = user_row["trial_expires_at"] if user_row else None
            if old_trial_expires_at and _from_db_utc(old_trial_expires_at) > now:
                await conn.execute(
                    "UPDATE users SET trial_expires_at = $1 WHERE telegram_id = $2 AND trial_expires_at > $1",
                    _to_db_utc(now), telegram_id
                )
                logger.info(
                    f"TRIAL_OVERRIDDEN_BY_PAID_SUBSCRIPTION: user_id={telegram_id}, "
                    f"old_trial_expires_at={old_trial_expires_at.isoformat()}, "
                    f"paid_subscription_expires_at={subscription_end.isoformat()}"
                )
        
        # Записываем в историю подписок
        await _log_subscription_history_atomic(conn, telegram_id, vless_url, subscription_start, subscription_end, history_action_type)
        
        # Audit log
        if admin_telegram_id:
            duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
            uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
            details = f"Granted {duration_str} access via {source}, Expires: {subscription_end.isoformat()}, UUID: {uuid_preview}"
            await _log_audit_event_atomic(conn, "subscription_created", admin_telegram_id, telegram_id, details)
        
        # Безопасное логирование UUID
        uuid_preview = f"{new_uuid[:8]}..." if new_uuid and len(new_uuid) > 8 else (new_uuid or "N/A")
        duration_str = f"{duration.days} days" if duration.days > 0 else f"{int(duration.total_seconds() / 60)} minutes"
        logger.info(
            f"grant_access: NEW_ISSUANCE_SUCCESS [action=new_issuance, telegram_id={telegram_id}, uuid={uuid_preview}, "
            f"subscription_end={subscription_end.isoformat()}, expiry_timestamp_ms={expiry_ms}, duration_days={duration_days}, "
            f"source={source}, duration={duration_str}, vless_url_length={len(vless_url) if vless_url else 0}]"
        )
        logger.info(
            f"grant_access: UUID_CREATED [action=new_issuance, telegram_id={telegram_id}, uuid={uuid_preview}] - "
            "New UUID created via VPN API, user must connect with new VLESS link"
        )
        
        # ВАЛИДАЦИЯ: Возвращаем только если все данные сохранены в БД
        return {
            "uuid": new_uuid,
            "vless_url": vless_url,
            "vpn_key_plus": vless_url_plus,
            "subscription_end": subscription_end,
            "action": "new_issuance",
            "subscription_type": subscription_type_value,
            "old_uuid_to_remove_after_commit": old_uuid_to_remove_after_commit
        }
        
    except Exception as e:
        logger.error(
            f"grant_access: ERROR [telegram_id={telegram_id}, source={source}, error={str(e)}, "
            f"error_type={type(e).__name__}]"
        )
        logger.exception(f"grant_access: EXCEPTION_TRACEBACK [user={telegram_id}]")
        raise  # Пробрасываем исключение, не возвращаем None
    finally:
        if should_release_conn and _acquired_pool is not None:
            try:
                await _acquired_pool.release(conn)
            except Exception as release_err:
                logger.error(f"grant_access: failed to release connection: {release_err}")


def _calculate_subscription_days(months: int) -> int:
    """
    Рассчитать количество дней для подписки на основе количества месяцев
    
    Args:
        months: Количество месяцев (1, 3, 6, 12)
    
    Returns:
        Количество дней (30, 90, 180, 365)
    """
    days_map = {
        1: 30,
        3: 90,
        6: 180,
        12: 365
    }
    return days_map.get(months, months * 30)


async def approve_payment_atomic(payment_id: int, months: int, admin_telegram_id: int, bot: Optional["Bot"] = None) -> Tuple[Optional[datetime], bool, Optional[str]]:
    """Атомарно подтвердить платеж в одной транзакции
    
    Two-phase activation: Phase 1 add_vless_user outside tx, Phase 2 grant_access inside tx.
    Eliminates orphan UUID risk (no external call inside DB transaction).
    
    В одной транзакции:
    - обновляет payment → approved
    - создает/продлевает subscription с VPN-ключом
    - записывает событие в audit_log
    
    Args:
        payment_id: ID платежа
        months: Количество месяцев подписки
        admin_telegram_id: Telegram ID администратора, который выполняет approve
    
    Returns:
        (expires_at, is_renewal, vpn_key) или (None, False, None) при ошибке или отсутствии ключей
    
    При любой ошибке транзакция откатывается.
    """
    pool = await get_pool()
    
    # Pre-fetch payment and subscription (read-only) for Phase 1
    pre_provisioned_uuid = None
    uuid_to_cleanup_on_failure = None
    async with pool.acquire() as conn_pre:
        payment_row = await conn_pre.fetchrow(
            "SELECT * FROM payments WHERE id = $1 AND status = 'pending'",
            payment_id
        )
        if not payment_row:
            logger.error(f"Payment {payment_id} not found or not pending for atomic approve")
            return None, False, None
        
        payment = dict(payment_row)
        telegram_id = payment["telegram_id"]
        now_pre = datetime.now(timezone.utc)
        days = _calculate_subscription_days(months)
        tariff_duration = timedelta(days=days)
        subscription_end_pre = now_pre + tariff_duration
        
        sub_row = await conn_pre.fetchrow("SELECT * FROM subscriptions WHERE telegram_id = $1", telegram_id)
        is_new_issuance = True
        if sub_row:
            sub = dict(sub_row)
            exp_raw = sub.get("expires_at")
            exp = _from_db_utc(exp_raw) if exp_raw else None
            is_new_issuance = (
                sub.get("status") != "active" or not exp or exp <= now_pre or not sub.get("uuid")
            )
        if is_new_issuance and config.VPN_ENABLED:
            try:
                new_uuid_pre = _generate_subscription_uuid()
                vless_result = await vpn_utils.add_vless_user(
                    telegram_id=telegram_id,
                    subscription_end=subscription_end_pre,
                    uuid=new_uuid_pre
                )
                pre_provisioned_uuid = {
                    "uuid": vless_result["uuid"].strip(),
                    "vless_url": vless_result["vless_url"],
                    "subscription_type": vless_result.get("subscription_type") or "basic",
                }
                uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                logger.info(
                    f"approve_payment_atomic: TWO_PHASE_PHASE1_DONE [payment_id={payment_id}, user={telegram_id}, "
                    f"uuid={uuid_to_cleanup_on_failure[:8]}...]"
                )
            except Exception as phase1_err:
                logger.warning(
                    f"approve_payment_atomic: Phase 1 add_vless_user failed: payment_id={payment_id}, error={phase1_err}"
                )
                pre_provisioned_uuid = None
                uuid_to_cleanup_on_failure = None
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # 1. Re-verify payment (may have been approved by another process)
                payment_row = await conn.fetchrow(
                    "SELECT * FROM payments WHERE id = $1 AND status = 'pending'",
                    payment_id
                )
                if not payment_row:
                    logger.error(f"Payment {payment_id} not found or not pending for atomic approve (race)")
                    return None, False, None
                
                payment = dict(payment_row)
                telegram_id = payment["telegram_id"]
                
                # 2. Обновляем статус платежа на approved
                await conn.execute(
                    "UPDATE payments SET status = 'approved' WHERE id = $1",
                    payment_id
                )
                
                now = datetime.now(timezone.utc)
                tariff_duration = timedelta(days=days)
                
                # 4. Используем grant_access с pre_provisioned_uuid
                grant_result_for_removal = result = await grant_access(
                    telegram_id=telegram_id,
                    duration=tariff_duration,
                    source="payment",
                    admin_telegram_id=None,
                    admin_grant_days=None,
                    conn=conn,
                    pre_provisioned_uuid=pre_provisioned_uuid,
                    _caller_holds_transaction=True
                )
                expires_at = result["subscription_end"]
                # Если vless_url есть - это новый UUID, используем его
                # Если vless_url нет - это продление, получаем vpn_key из подписки
                if result.get("vless_url"):
                    final_vpn_key = result["vless_url"]
                    is_renewal = False
                else:
                    # Продление - получаем vpn_key из существующей подписки
                    subscription_after = await conn.fetchrow(
                        "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                        telegram_id
                    )
                    if subscription_after and subscription_after.get("vpn_key"):
                        final_vpn_key = subscription_after["vpn_key"]
                    else:
                        # Fallback: используем UUID (не должно быть, но на всякий случай)
                        final_vpn_key = result.get("uuid", "")
                    is_renewal = True
                
                # 7. Записываем событие в audit_log
                audit_action_type = "subscription_renewed" if is_renewal else "payment_approved"
                vpn_key_display = final_vpn_key[:50] if len(final_vpn_key) > 50 else final_vpn_key
                details = f"Payment ID: {payment_id}, Tariff: {months} months, Expires: {expires_at.isoformat()}, UUID: {result['uuid']}, VPN: {vpn_key_display}..."
                await _log_audit_event_atomic(conn, audit_action_type, admin_telegram_id, telegram_id, details)
                
                # 8. Обрабатываем реферальный кешбэк (только при первой оплате, не при продлении)
                # E) PURCHASE FLOW: Use unified process_referral_reward function
                if not is_renewal:
                    try:
                        # Получаем сумму платежа в рублях
                        payment_amount_rubles = payment.get("amount", 0) / 100.0  # Конвертируем из копеек
                        
                        if payment_amount_rubles > 0:
                            # Используем единую функцию process_referral_reward
                            # purchase_id = f"admin_approve_{payment_id}" для уникальности
                            purchase_id = f"admin_approve_{payment_id}"
                            from database.users import process_referral_reward
                            referral_reward_result = await process_referral_reward(
                                buyer_id=telegram_id,
                                purchase_id=purchase_id,
                                amount_rubles=payment_amount_rubles,
                                conn=conn
                            )

                            if referral_reward_result.get("success"):
                                logger.info(
                                    f"REFERRAL_CASHBACK_GRANTED [payment_id={payment_id}, "
                                    f"referrer={referral_reward_result.get('referrer_id')}, "
                                    f"amount={referral_reward_result.get('reward_amount')} RUB, "
                                    f"percent={referral_reward_result.get('percent')}%]"
                                )
                                
                                # Отправляем уведомление рефереру (вне транзакции)
                                # NOTE: Notification is sent from handler, not from database layer
                                # This keeps database layer clean (no bot dependencies)
                                logger.info(
                                    f"REFERRAL_CASHBACK_GRANTED [payment_id={payment_id}, "
                                    f"referrer={referral_reward_result.get('referrer_id')}, "
                                    f"referred={telegram_id}, amount={referral_reward_result.get('reward_amount')} RUB]"
                                )
                            else:
                                # BUSINESS LOGIC ERROR: Reward skipped but payment continues
                                reason = referral_reward_result.get("reason", "unknown")
                                logger.debug(
                                    f"Referral reward skipped for payment {payment_id}: "
                                    f"user={telegram_id}, reason={reason}"
                                )
                    except Exception as e:
                        # Не блокируем транзакцию при ошибке обработки реферального кешбэка
                        logger.exception(f"Error processing referral reward for payment {payment_id}: {e}")
                
                logger.info(f"Payment {payment_id} approved atomically for user {telegram_id}, is_renewal={is_renewal}")
                ret_val = (expires_at, is_renewal, final_vpn_key)
            except Exception as e:
                if uuid_to_cleanup_on_failure:
                    try:
                        await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                        uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                        logger.critical(
                            f"ORPHAN_PREVENTED uuid={uuid_preview} reason=approve_payment_atomic_tx_failed "
                            f"payment_id={payment_id} user={telegram_id} error={e}"
                        )
                    except Exception as remove_err:
                        uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                        logger.critical(
                            f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} "
                            f"payment_id={payment_id} user={telegram_id}"
                        )
                logger.exception(f"Error in atomic approve for payment {payment_id}, transaction rolled back")
                raise
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("old_uuid_to_remove_after_commit"):
            old_uuid = grant_result_for_removal["old_uuid_to_remove_after_commit"]
            try:
                await vpn_utils.safe_remove_vless_user_with_retry(old_uuid)
                logger.info("OLD_UUID_REMOVED_AFTER_COMMIT", extra={"uuid": old_uuid[:8] + "..."})
            except Exception as e:
                logger.critical(
                    "OLD_UUID_REMOVAL_FAILED_POST_COMMIT",
                    extra={"uuid": old_uuid[:8] + "...", "error": str(e)[:200]}
                )
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("renewal_xray_sync_after_commit"):
            sync_info = grant_result_for_removal["renewal_xray_sync_after_commit"]
            try:
                await vpn_utils.ensure_user_in_xray(
                    telegram_id=sync_info["telegram_id"],
                    uuid=sync_info["uuid"],
                    subscription_end=sync_info["subscription_end"]
                )
            except Exception as e:
                logger.critical(
                    "RENEWAL_XRAY_SYNC_FAILED",
                    extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
                )
        return ret_val


async def get_pending_payments() -> list:
    """Получить все pending платежи (для админа)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC"
        )
        return [dict(row) for row in rows]


async def get_subscriptions_needing_reminder() -> list:
    """Получить подписки, которым нужно отправить напоминание
    
    Возвращает список подписок, где:
    - expires_at > now (активная)
    - reminder_sent = FALSE
    - expires_at <= now + 3 days
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscriptions_needing_reminder skipped")
        return []
    now = datetime.now(timezone.utc)
    reminder_date = now + timedelta(days=3)
    
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_needing_reminder skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscriptions 
               WHERE expires_at > $1 
               AND expires_at <= $2
               AND reminder_sent = FALSE
               ORDER BY expires_at ASC""",
            _to_db_utc(now), _to_db_utc(reminder_date)
        )
        return [_normalize_subscription_row(row) for row in rows]


async def mark_reminder_sent(telegram_id: int):
    """Отметить, что напоминание отправлено пользователю (старая функция, для совместимости)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET reminder_sent = TRUE WHERE telegram_id = $1",
            telegram_id
        )


# SECURITY: Pre-built SQL queries for each reminder flag.
# Eliminates f-string SQL interpolation — only static SQL strings are used.
_REMINDER_FLAG_UPDATE_QUERIES = {
    "reminder_7d_sent": (
        "UPDATE subscriptions SET reminder_7d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_3d_sent": (
        "UPDATE subscriptions SET reminder_3d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_1d_sent": (
        "UPDATE subscriptions SET reminder_1d_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_24h_sent": (
        "UPDATE subscriptions SET reminder_24h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_3h_sent": (
        "UPDATE subscriptions SET reminder_3h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "reminder_6h_sent": (
        "UPDATE subscriptions SET reminder_6h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "trial_notif_24h_sent": (
        "UPDATE subscriptions SET trial_notif_24h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
    "trial_notif_3h_sent": (
        "UPDATE subscriptions SET trial_notif_3h_sent = TRUE, "
        "last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE telegram_id = $1"
    ),
}

# Expose frozenset for external validation (used by app/services/notifications/service.py)
_ALLOWED_REMINDER_FLAGS = frozenset(_REMINDER_FLAG_UPDATE_QUERIES.keys())


async def mark_reminder_flag_sent(telegram_id: int, flag_name: str):
    """Отметить, что конкретное напоминание отправлено пользователю

    Args:
        telegram_id: Telegram ID пользователя
        flag_name: Имя флага ('reminder_3d_sent', 'reminder_24h_sent', 'reminder_3h_sent', 'reminder_6h_sent')

    Raises:
        ValueError: если flag_name не в whitelist
    """
    query = _REMINDER_FLAG_UPDATE_QUERIES.get(flag_name)
    if query is None:
        raise ValueError(
            f"Invalid flag_name '{flag_name}'. "
            f"Allowed: {sorted(_ALLOWED_REMINDER_FLAGS)}"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(query, telegram_id)


async def mark_user_unreachable(telegram_id: int) -> None:
    """Mark user as unreachable (chat not found, blocked). Background workers filter by is_reachable."""
    if not _core.DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_reachable = FALSE WHERE telegram_id = $1",
                telegram_id
            )
    except asyncpg.UndefinedColumnError:
        logger.debug("mark_user_unreachable skipped: is_reachable column not present")
    except Exception as e:
        logger.warning(f"mark_user_unreachable failed for user={telegram_id}: {e}")


async def update_last_reminder_at(subscription_id: int) -> None:
    """Update last_reminder_at for idempotency guard (container restart protection)."""
    if not _core.DB_READY:
        return
    try:
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET last_reminder_at = (NOW() AT TIME ZONE 'UTC') WHERE id = $1",
                subscription_id
            )
    except Exception as e:
        logger.warning(f"update_last_reminder_at failed for subscription_id={subscription_id}: {e}")


# Active promo definition: is_active=true AND deleted_at IS NULL AND expires_at > now() AND used_count < max_uses
_ACTIVE_PROMO_WHERE = (
    "is_active = true AND deleted_at IS NULL "
    "AND (expires_at IS NULL OR expires_at > NOW()) "
    "AND (max_uses IS NULL OR used_count < max_uses)"
)


async def get_promo_code(code: str) -> Optional[Dict[str, Any]]:
    """Получить любой промокод по коду (может быть неактивным). Для валидации используйте get_active_promo_by_code."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM promo_codes WHERE UPPER(code) = UPPER($1) ORDER BY created_at DESC LIMIT 1",
            code
        )
        return dict(row) if row else None


async def get_active_promo_by_code(conn, code: str) -> Optional[Dict[str, Any]]:
    """Получить активный промокод по коду (is_active, !deleted, !expired, !exhausted). Требует conn."""
    has_deleted_at = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
    )
    if has_deleted_at:
        row = await conn.fetchrow(
            f"""
            SELECT * FROM promo_codes
            WHERE UPPER(code) = UPPER($1) AND {_ACTIVE_PROMO_WHERE}
            ORDER BY id DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            code
        )
    else:
        row = await conn.fetchrow(
            """
            SELECT * FROM promo_codes
            WHERE UPPER(code) = UPPER($1)
              AND is_active = true
              AND (expires_at IS NULL OR expires_at > NOW())
              AND (max_uses IS NULL OR used_count < max_uses)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            code
        )
    return dict(row) if row else None


async def has_active_promo(code: str) -> bool:
    """Проверить, есть ли активный промокод с таким кодом"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        promo = await get_active_promo_by_code(conn, code)
        return promo is not None


async def check_promo_code_valid(code: str) -> Optional[Dict[str, Any]]:
    """Проверить, валиден ли промокод и вернуть его данные (только активный)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await get_active_promo_by_code(conn, code)


async def log_promo_code_usage(
    promo_code: str,
    telegram_id: int,
    tariff: str,
    discount_percent: int,
    price_before: int,
    price_after: int
):
    """Записать использование промокода в лог"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO promo_usage_logs 
            (promo_code, telegram_id, tariff, discount_percent, price_before, price_after)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, promo_code.upper(), telegram_id, tariff, discount_percent, price_before, price_after)


async def get_promo_stats() -> list:
    """
    Получить статистику по промокодам через SQL-агрегацию.
    Без кеширования. Активный промокод: is_active, !deleted, !expired, !exhausted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        has_id = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
        )
        if not has_id:
            rows = await conn.fetch("""
                SELECT code, discount_percent, max_uses, used_count, is_active, expires_at, created_at, created_by
                FROM promo_codes ORDER BY code
            """)
            return [dict(row) for row in rows]
        rows = await conn.fetch("""
            SELECT id, code, discount_percent, max_uses, used_count, is_active, deleted_at,
                   expires_at, created_at, created_by,
                   (is_active = true AND deleted_at IS NULL
                    AND (expires_at IS NULL OR expires_at > NOW())
                    AND (max_uses IS NULL OR used_count < max_uses)) AS is_effective_active
            FROM promo_codes
            ORDER BY code, created_at DESC
        """)
        return [dict(row) for row in rows]


def generate_promo_code(length: int = 6) -> str:
    """Генерировать случайный промокод из заглавных букв A-Z"""
    return ''.join(random.choices(string.ascii_uppercase, k=length))


async def create_promocode_atomic(
    code: str,
    discount_percent: int,
    duration_seconds: int,
    max_uses: int,
    created_by: int
) -> Optional[int]:
    """
    Создать промокод атомарно. Разрешает пересоздание, если предыдущий удалён/истёк/исчерпан.
    Блокирует создание, если активный промокод с таким кодом уже существует.
    
    Returns:
        ID созданного промокода или None при конфликте/ошибке
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, create_promocode_atomic skipped")
        return None
    pool = await get_pool()
    if pool is None:
        return None

    code_normalized = code.upper().strip()
    if len(code_normalized) < 3 or len(code_normalized) > 32:
        logger.error(f"Invalid promocode length: {len(code_normalized)}")
        return None
    if not all(c.isalnum() for c in code_normalized):
        logger.error(f"Invalid promocode characters: {code_normalized}")
        return None
    if discount_percent < 0 or discount_percent > 100:
        logger.error(f"Invalid discount_percent: {discount_percent}")
        return None
    if max_uses <= 0:
        logger.error(f"Invalid max_uses: {max_uses}")
        return None
    if duration_seconds <= 0:
        logger.error(f"Invalid duration_seconds: {duration_seconds}")
        return None

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                has_id = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
                )
                if has_id:
                    conflict = await conn.fetchrow(
                        """
                        SELECT id FROM promo_codes
                        WHERE UPPER(code) = UPPER($1)
                          AND is_active = true AND deleted_at IS NULL
                          AND (expires_at IS NULL OR expires_at > NOW())
                          AND (max_uses IS NULL OR used_count < max_uses)
                        LIMIT 1
                        """,
                        code_normalized
                    )
                    if conflict:
                        logger.warning(f"PROMO_CONFLICT code={code_normalized} active promo exists id={conflict['id']}")
                        return None

                if has_id:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO promo_codes
                        (code, discount_percent, duration_seconds, max_uses, expires_at, created_by, is_active, used_count)
                        VALUES ($1, $2, $3, $4, $5, $6, TRUE, 0)
                        RETURNING id
                        """,
                        code_normalized, discount_percent, duration_seconds, max_uses, _to_db_utc(expires_at), created_by
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO promo_codes
                        (code, discount_percent, duration_seconds, max_uses, expires_at, created_by, is_active)
                        VALUES ($1, $2, $3, $4, $5, $6, TRUE)
                        RETURNING code
                        """,
                        code_normalized, discount_percent, duration_seconds, max_uses, _to_db_utc(expires_at), created_by
                    )
                if not row:
                    return None

                promo_id = row.get("id") or row.get("code")
                prev_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM promo_codes WHERE UPPER(code) = UPPER($1)",
                    code_normalized
                ) or 0
                is_recreate = has_id and int(prev_count) > 1
                if is_recreate:
                    logger.info(
                        f"PROMO_RECREATED code={code_normalized} id={promo_id} discount={discount_percent}% "
                        f"max_uses={max_uses} created_by={created_by}"
                    )
                else:
                    logger.info(
                        f"PROMO_CREATED code={code_normalized} id={promo_id} discount={discount_percent}% "
                        f"max_uses={max_uses} expires_at={expires_at} created_by={created_by}"
                    )
                return int(promo_id) if promo_id else None
            except asyncpg.UniqueViolationError:
                logger.warning(f"Promocode unique violation (active conflict): {code_normalized}")
                return None
            except Exception as e:
                logger.exception(f"Error creating promocode {code_normalized}: {e}")
                return None


async def deactivate_promocode(promo_id: Optional[int] = None, code: Optional[str] = None) -> bool:
    """
    Деактивировать промокод: UPDATE is_active=false, deleted_at=now().
    Передайте promo_id (предпочтительно) или code. Логирует PROMO_DEACTIVATED.
    """
    if not _core.DB_READY:
        return False
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        has_deleted_at = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
        )
        if promo_id is not None:
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false, deleted_at = NOW() WHERE id = $1 RETURNING code",
                    promo_id
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false WHERE id = $1 RETURNING code",
                    promo_id
                )
        elif code:
            code_n = code.upper().strip()
            if has_deleted_at:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false, deleted_at = NOW() WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n
                )
            else:
                row = await conn.fetchrow(
                    "UPDATE promo_codes SET is_active = false WHERE UPPER(code) = UPPER($1) RETURNING code",
                    code_n
                )
        else:
            return False
        if row:
            logger.info(f"PROMO_DEACTIVATED code={row['code']} id={promo_id or 'N/A'}")
            return True
        return False


async def _consume_promo_in_transaction(
    conn, code: str, telegram_id: int, purchase_id: Optional[str] = None
) -> None:
    """
    Потребление промокода внутри транзакции: UPDATE ... WHERE id = ? AND used_count < max_uses RETURNING *
    Если строк не возвращено — промокод исчерпан. Логирует PROMO_USAGE_INCREMENTED.
    Raises ValueError при ошибке.
    """
    code_normalized = code.upper().strip()
    promo = await get_active_promo_by_code(conn, code_normalized)
    if not promo:
        ctx = f" purchase_id={purchase_id}" if purchase_id else ""
        raise ValueError(f"PROMO_INVALID_OR_EXPIRED: code={code_normalized}{ctx}")

    promo_id = promo.get("id")
    has_id = promo_id is not None
    if has_id:
        updated = await conn.fetchrow(
            """
            UPDATE promo_codes
            SET used_count = used_count + 1
            WHERE id = $1 AND (max_uses IS NULL OR used_count < max_uses)
            RETURNING *
            """,
            promo_id
        )
    else:
        updated = await conn.fetchrow(
            """
            UPDATE promo_codes
            SET used_count = used_count + 1
            WHERE UPPER(code) = UPPER($1)
              AND is_active = true
              AND (expires_at IS NULL OR expires_at > NOW())
              AND (max_uses IS NULL OR used_count < max_uses)
            RETURNING *
            """,
            code_normalized
        )
    if not updated:
        ctx = f" purchase_id={purchase_id}" if purchase_id else ""
        logger.warning(f"PROMO_EXHAUSTED code={code_normalized} user={telegram_id}{ctx}")
        raise ValueError("PROMO_EXHAUSTED")

    used = updated["used_count"]
    max_uses_val = updated["max_uses"]
    logger.info(
        f"PROMO_USAGE_INCREMENTED code={code_normalized} id={promo_id or 'N/A'} user={telegram_id} "
        f"used_count={used}/{max_uses_val if max_uses_val else 'unlimited'}"
    )


async def validate_promocode_atomic(code: str) -> Dict[str, Any]:
    """
    Валидация промокода без инкремента счетчика.
    Использует определение активного промо: is_active, !deleted, !expired, !exhausted.
    
    Returns:
        {"success": bool, "promo_data": Optional[Dict], "error": Optional[str]}
    """
    if not _core.DB_READY:
        return {"success": False, "promo_data": None, "error": "invalid"}
    pool = await get_pool()
    if pool is None:
        return {"success": False, "promo_data": None, "error": "invalid"}
    code_normalized = code.upper().strip()
    async with pool.acquire() as conn:
        try:
            promo = await get_active_promo_by_code(conn, code_normalized)
            if not promo:
                return {"success": False, "promo_data": None, "error": "invalid"}
            logger.info(
                f"PROMOCODE_VALIDATED code={code_normalized} "
                f"used_count={promo.get('used_count', 0)}/{promo.get('max_uses') or 'unlimited'}"
            )
            return {"success": True, "promo_data": dict(promo), "error": None}
        except Exception as e:
            logger.exception(f"Error validating promocode {code_normalized}: {e}")
            return {"success": False, "promo_data": None, "error": "invalid"}


async def consume_promocode_atomic(code: str, telegram_id: int) -> None:
    """
    Потребление промокода — инкремент счетчика использований.
    Вызывается ТОЛЬКО при успешной оплате.
    
    CRITICAL: Эта функция должна вызываться только после успешной оплаты.
    
    Raises:
        ValueError: Если промокод не найден, уже исчерпан или невалиден
    """
    if not _core.DB_READY:
        raise ValueError("PROMO_DB_NOT_READY")
    
    pool = await get_pool()
    if pool is None:
        raise ValueError("PROMO_DB_NOT_READY")
    
    code_normalized = code.upper().strip()
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                # CRITICAL: Advisory lock на код для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    code_normalized
                )
                
                # CRITICAL: SELECT FOR UPDATE для блокировки строки
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM promo_codes
                    WHERE code = $1
                    FOR UPDATE
                    """,
                    code_normalized
                )
                
                if not row:
                    raise ValueError("PROMO_NOT_FOUND")
                
                # Проверяем активность
                if not row.get("is_active", False):
                    raise ValueError("PROMO_INACTIVE")
                
                # Проверяем срок действия
                expires_at = row.get("expires_at")
                if expires_at:
                    expired_check = await conn.fetchval(
                        "SELECT expires_at < NOW() FROM promo_codes WHERE code = $1",
                        row["code"]
                    )
                    if expired_check:
                        await conn.execute(
                            "UPDATE promo_codes SET is_active = FALSE WHERE code = $1",
                            row["code"]
                        )
                        raise ValueError("PROMO_EXPIRED")
                
                # Проверяем лимит использований
                used_count = row.get("used_count", 0)
                max_uses = row.get("max_uses")
                if max_uses is not None and used_count >= max_uses:
                    raise ValueError("PROMO_ALREADY_CONSUMED")
                
                # SUCCESS — увеличиваем счетчик использований атомарно
                await conn.execute(
                    """
                    UPDATE promo_codes
                    SET used_count = used_count + 1
                    WHERE code = $1
                    """,
                    row["code"]
                )
                
                # Получаем обновленное значение used_count
                updated_row = await conn.fetchrow(
                    "SELECT used_count, max_uses FROM promo_codes WHERE code = $1",
                    row["code"]
                )
                new_count = updated_row["used_count"]
                
                # Автоматическая деактивация при достижении лимита
                if max_uses is not None and new_count >= max_uses:
                    await conn.execute(
                        """
                        UPDATE promo_codes
                        SET is_active = FALSE
                        WHERE code = $1
                        AND used_count >= max_uses
                        """,
                        row["code"]
                    )
                
                logger.info(
                    f"PROMOCODE_CONSUMED code={code_normalized} user={telegram_id} "
                    f"used_count={new_count}/{max_uses if max_uses else 'unlimited'}"
                )
                
            except ValueError:
                # Пробрасываем ValueError как есть
                raise
            except Exception as e:
                logger.exception(f"Error consuming promocode {code_normalized}: {e}")
                raise ValueError("PROMO_CONSUME_ERROR")


async def is_user_first_purchase(telegram_id: int) -> bool:
    """Проверить, является ли это первой покупкой пользователя
    
    Пользователь считается новым, если:
    - у него НИКОГДА не было подтверждённой оплаты (status = 'approved')
    - у него НИКОГДА не было активной или истёкшей подписки
    
    Returns:
        True если это первая покупка, False иначе
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем наличие подтверждённых платежей
        approved_payment = await conn.fetchrow(
            "SELECT id FROM payments WHERE telegram_id = $1 AND status = 'approved' LIMIT 1",
            telegram_id
        )
        
        if approved_payment:
            return False
        
        # Проверяем наличие подписок в истории (любых, включая истёкшие)
        subscription_history = await conn.fetchrow(
            """SELECT id FROM subscription_history 
               WHERE telegram_id = $1 
               AND action_type IN ('purchase', 'renewal', 'reissue')
               LIMIT 1""",
            telegram_id
        )
        
        if subscription_history:
            return False
        
        return True


async def get_subscriptions_for_reminders() -> list:
    """Получить все активные подписки, которым нужно отправить напоминания

    Filters out users with is_reachable = FALSE (blocked/chat not found).
    Falls back to legacy query if is_reachable column not yet present (migration 014).
    Returns список подписок с информацией о типе (админ-доступ или оплаченный тариф)
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscriptions_for_reminders skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscriptions_for_reminders skipped")
        return []
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        query_with_reachable = """
            SELECT s.*,
                   (SELECT action_type FROM subscription_history
                    WHERE telegram_id = s.telegram_id
                    ORDER BY created_at DESC LIMIT 1) as last_action_type
            FROM subscriptions s
            JOIN users u ON s.telegram_id = u.telegram_id
            WHERE s.expires_at > $1
            AND COALESCE(u.is_reachable, TRUE) = TRUE
            ORDER BY s.expires_at ASC"""
        fallback_query = """
            SELECT s.*,
                   (SELECT action_type FROM subscription_history
                    WHERE telegram_id = s.telegram_id
                    ORDER BY created_at DESC LIMIT 1) as last_action_type
            FROM subscriptions s
            WHERE s.expires_at > $1
            ORDER BY s.expires_at ASC"""
        try:
            rows = await conn.fetch(query_with_reachable, _to_db_utc(now))
        except asyncpg.UndefinedColumnError:
            logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, fallback to legacy query")
            rows = await conn.fetch(fallback_query, _to_db_utc(now))
        return [_normalize_subscription_row(row) for row in rows]


async def get_admin_stats() -> Dict[str, int]:
    """Получить статистику для админ-дашборда

    Returns:
        Словарь с ключами:
        - total_users: всего пользователей
        - active_subscriptions: активных подписок
        - expired_subscriptions: истёкших подписок
        - total_payments: всего платежей
        - approved_payments: подтверждённых платежей
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, get_admin_stats skipped")
        return {"total_users": 0, "active_subscriptions": 0, "expired_subscriptions": 0, "total_payments": 0, "approved_payments": 0}
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_admin_stats skipped")
        return {"total_users": 0, "active_subscriptions": 0, "expired_subscriptions": 0, "total_payments": 0, "approved_payments": 0}
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        
        # Всего пользователей
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        
        # Активных подписок (expires_at > now)
        active_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at > $1",
            _to_db_utc(now)
        )
        
        # Истёкших подписок (expires_at <= now)
        expired_subscriptions = await conn.fetchval(
            "SELECT COUNT(*) FROM subscriptions WHERE expires_at <= $1",
            _to_db_utc(now)
        )
        
        # Всего платежей
        total_payments = await conn.fetchval("SELECT COUNT(*) FROM payments")
        
        # Подтверждённых платежей
        approved_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status = 'approved'"
        )
        
        return {
            "total_users": total_users or 0,
            "active_subscriptions": active_subscriptions or 0,
            "expired_subscriptions": expired_subscriptions or 0,
            "total_payments": total_payments or 0,
            "approved_payments": approved_payments or 0,
        }


async def get_admin_referral_stats(
    search_query: Optional[str] = None,
    sort_by: str = "total_revenue",  # "total_revenue", "invited_count", "cashback_paid"
    sort_order: str = "DESC",  # "ASC", "DESC"
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Получить агрегированную статистику по всем рефералам для админ-дашборда
    
    Args:
        search_query: Поисковый запрос (telegram_id или username)
        sort_by: Поле для сортировки ("total_revenue", "invited_count", "cashback_paid")
        sort_order: Порядок сортировки ("ASC", "DESC")
        limit: Максимальное количество записей
        offset: Смещение для пагинации
    
    Returns:
        Список словарей с агрегированной статистикой по каждому рефереру:
        - referrer_id: Telegram ID реферера
        - username: Username реферера
        - invited_count: Всего приглашённых
        - paid_count: Сколько оплатили
        - conversion_percent: Процент конверсии
        - total_invited_revenue: Общий доход от приглашённых (рубли)
        - total_cashback_paid: Общий выплаченный кешбэк (рубли)
        - current_cashback_percent: Текущий процент кешбэка
        - first_referral_date: Дата первого приглашения
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_admin_referral_stats skipped")
        return []
    
    pool = await get_pool()
    if pool is None:
        return []
    
    try:
        # FIX: Все операции с conn должны происходить строго внутри async with
        async with pool.acquire() as conn:
            # Базовый запрос для агрегированной статистики
            # Используем подзапросы для корректной агрегации
            base_query = """
            SELECT 
                u.telegram_id AS referrer_id,
                u.username,
                COALESCE(ref_stats.invited_count, 0) AS invited_count,
                COALESCE(paid_stats.paid_count, 0) AS paid_count,
                COALESCE(MIN(r.created_at), NULL) AS first_referral_date,
                COALESCE(revenue_stats.total_revenue_kopecks, 0) AS total_invited_revenue_kopecks,
                COALESCE(cashback_stats.total_cashback_kopecks, 0) AS total_cashback_paid_kopecks
            FROM users u
            LEFT JOIN referrals r ON u.telegram_id = r.referrer_user_id
            LEFT JOIN (
                SELECT referrer_user_id, COUNT(DISTINCT referred_user_id) AS invited_count
                FROM referrals
                GROUP BY referrer_user_id
            ) ref_stats ON u.telegram_id = ref_stats.referrer_user_id
            LEFT JOIN (
                SELECT r.referrer_user_id, COUNT(DISTINCT r.referred_user_id) AS paid_count
                FROM referrals r
                INNER JOIN payments p ON r.referred_user_id = p.telegram_id AND p.status = 'approved'
                GROUP BY r.referrer_user_id
            ) paid_stats ON u.telegram_id = paid_stats.referrer_user_id
            LEFT JOIN (
                SELECT r.referrer_user_id, SUM(p.amount) AS total_revenue_kopecks
                FROM referrals r
                INNER JOIN payments p ON r.referred_user_id = p.telegram_id AND p.status = 'approved'
                GROUP BY r.referrer_user_id
            ) revenue_stats ON u.telegram_id = revenue_stats.referrer_user_id
            LEFT JOIN (
                SELECT bt.user_id AS referrer_user_id, SUM(bt.amount) AS total_cashback_kopecks
                FROM balance_transactions bt
                WHERE bt.type = 'cashback' AND bt.source = 'referral'
                GROUP BY bt.user_id
            ) cashback_stats ON u.telegram_id = cashback_stats.referrer_user_id
            """
            
            where_clauses = []
            params = []
            param_index = 1
            
            # Фильтр по поисковому запросу
            if search_query:
                try:
                    # Пробуем найти по telegram_id
                    telegram_id = int(search_query)
                    where_clauses.append(f"u.telegram_id = ${param_index}")
                    params.append(telegram_id)
                    param_index += 1
                except ValueError:
                    # Иначе ищем по username
                    where_clauses.append(f"LOWER(u.username) LIKE LOWER(${param_index})")
                    params.append(f"%{search_query}%")
                    param_index += 1
            
            # Фильтр: показываем только рефереров (тех, кто пригласил хотя бы одного)
            where_clauses.append(f"ref_stats.invited_count > 0 OR EXISTS (SELECT 1 FROM referrals r2 WHERE r2.referrer_user_id = u.telegram_id)")
            
            # Группировка по рефереру
            group_by = "GROUP BY u.telegram_id, u.username, ref_stats.invited_count, paid_stats.paid_count, revenue_stats.total_revenue_kopecks, cashback_stats.total_cashback_kopecks"
            
            # Сортировка
            sort_column_map = {
                "total_revenue": "total_invited_revenue_kopecks",
                "invited_count": "invited_count",
                "cashback_paid": "total_cashback_paid_kopecks"
            }
            sort_column = sort_column_map.get(sort_by, "total_invited_revenue_kopecks")
            # Validate sort_order to prevent SQL injection
            if sort_order.upper() not in ("ASC", "DESC"):
                sort_order = "DESC"
            order_by = f"ORDER BY {sort_column} {sort_order.upper()}, u.telegram_id ASC"
            
            # Пагинация
            limit_clause = f"LIMIT ${param_index} OFFSET ${param_index + 1}"
            params.extend([limit, offset])
            
            # Собираем полный запрос
            where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            full_query = f"{base_query} {where_clause} {group_by} {order_by} {limit_clause}"
            
            # FIX: Все операции с conn.fetch() происходят строго внутри блока async with
            rows = await conn.fetch(full_query, *params)
            
            # FIX: Извлекаем все данные из rows внутри блока async with
            # Преобразуем rows в список словарей, чтобы не зависеть от connection после выхода из блока
            rows_data = []
            for row in rows:
                rows_data.append(dict(row))
        
        # FIX: Обработка результатов происходит ПОСЛЕ выхода из блока async with
        # Это гарантирует, что conn не используется после release
        result = []
        for row_data in rows_data:
            try:
                referrer_id = row_data.get("referrer_id")
                if referrer_id is None:
                    continue  # Пропускаем строки без referrer_id
                
                # Безопасное извлечение значений с обработкой NULL
                invited_count = safe_int(row_data.get("invited_count"))
                paid_count = safe_int(row_data.get("paid_count"))
                
                # Вычисляем процент конверсии (защита от деления на 0)
                conversion_percent = (paid_count / invited_count * 100) if invited_count > 0 else 0.0
                
                # Конвертируем из копеек в рубли с безопасной обработкой NULL
                total_invited_revenue_kopecks = safe_int(row_data.get("total_invited_revenue_kopecks"))
                total_cashback_paid_kopecks = safe_int(row_data.get("total_cashback_paid_kopecks"))
                total_invited_revenue = total_invited_revenue_kopecks / 100.0
                total_cashback_paid = total_cashback_paid_kopecks / 100.0
                
                # Определяем текущий процент кешбэка (безопасно)
                # FIX: Вызываем после выхода из блока conn, чтобы избежать проблем с connection lifecycle
                try:
                    from database.users import get_referral_cashback_percent
                    current_cashback_percent = await get_referral_cashback_percent(referrer_id)
                except Exception as e:
                    logger.warning(f"Error getting cashback percent for referrer_id={referrer_id}: {e}")
                    current_cashback_percent = 10  # Значение по умолчанию
                
                result.append({
                    "referrer_id": referrer_id,
                    "username": row_data.get("username") or f"ID{referrer_id}",
                    "invited_count": invited_count,
                    "paid_count": paid_count,
                    "conversion_percent": round(conversion_percent, 2),
                    "total_invited_revenue": round(total_invited_revenue, 2),
                    "total_cashback_paid": round(total_cashback_paid, 2),
                    "current_cashback_percent": current_cashback_percent,
                    "first_referral_date": row_data.get("first_referral_date")
                })
            except Exception as e:
                logger.exception(f"Error processing row in get_admin_referral_stats: {e}, row={row_data}")
                continue  # Пропускаем проблемные строки, но продолжаем обработку
        
        return result
    except asyncpg.PostgresError as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping admin referral stats: {e}")
        return []
    except Exception as e:
        logger.warning(f"Error getting admin referral stats: {e}")
        return []


async def get_admin_referral_detail(referrer_id: int) -> Optional[Dict[str, Any]]:
    """
    Получить детальную информацию по конкретному рефереру
    
    Args:
        referrer_id: Telegram ID реферера
    
    Returns:
        Словарь с детальной информацией:
        - referrer_id: Telegram ID реферера
        - username: Username реферера
        - invited_list: Список приглашённых с деталями:
          - invited_user_id: Telegram ID приглашённого
          - username: Username приглашённого
          - registered_at: Дата регистрации
          - first_payment_date: Дата первой оплаты
          - purchase_amount: Сумма покупки (рубли)
          - cashback_amount: Сумма кешбэка (рубли)
          - purchase_id: ID платежа
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_admin_referral_detail skipped")
        return None
    
    pool = await get_pool()
    if pool is None:
        return None
    
    try:
        async with pool.acquire() as conn:
            # Получаем информацию о реферере
            referrer = await conn.fetchrow(
                "SELECT telegram_id, username FROM users WHERE telegram_id = $1",
                referrer_id
            )
            
            if not referrer:
                return None
            
            # Получаем список всех приглашённых с детальной информацией
            invited_list_query = """
            SELECT 
                r.referred_user_id AS invited_user_id,
                u.username,
                r.created_at AS registered_at,
                MIN(p.created_at) AS first_payment_date,
                MIN(p.id) AS purchase_id,
                MIN(p.amount) AS purchase_amount_kopecks,
                COALESCE(SUM(CASE 
                    WHEN bt.type = 'cashback' AND bt.source = 'referral' 
                    AND bt.related_user_id = r.referred_user_id THEN bt.amount 
                    ELSE 0 
                END), 0) AS cashback_amount_kopecks
            FROM referrals r
            LEFT JOIN users u ON r.referred_user_id = u.telegram_id
            LEFT JOIN payments p ON r.referred_user_id = p.telegram_id 
                AND p.status = 'approved'
            LEFT JOIN balance_transactions bt ON bt.user_id = $1 
                AND bt.type = 'cashback' 
                AND bt.source = 'referral'
                AND bt.related_user_id = r.referred_user_id
            WHERE r.referrer_user_id = $1
            GROUP BY r.referred_user_id, u.username, r.created_at
            ORDER BY r.created_at DESC
            """
            
            invited_rows = await conn.fetch(invited_list_query, referrer_id)
            
            invited_list = []
            for row in invited_rows:
                invited_list.append({
                    "invited_user_id": row["invited_user_id"],
                    "username": row["username"] or f"ID{row['invited_user_id']}",
                    "registered_at": row["registered_at"],
                    "first_payment_date": row["first_payment_date"],
                    "purchase_amount": (row["purchase_amount_kopecks"] or 0) / 100.0,
                    "cashback_amount": (row["cashback_amount_kopecks"] or 0) / 100.0,
                    "purchase_id": row["purchase_id"]
                })
            
            return {
                "referrer_id": referrer_id,
                "username": referrer["username"] or f"ID{referrer_id}",
                "invited_list": invited_list
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping admin referral detail: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error getting admin referral detail: {e}")
        return None


async def get_referral_overall_stats(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Получить общую статистику по реферальной системе
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
    
    Returns:
        Словарь с общей статистикой:
        - total_referrers: Всего рефереров
        - total_referrals: Всего приглашённых пользователей
        - total_paid_referrals: Всего оплативших рефералов
        - total_revenue: Общий доход от рефералов (рубли)
        - total_cashback_paid: Общий выплаченный кешбэк (рубли)
        - avg_cashback_per_referrer: Средний кешбэк на реферера (рубли)
    """
    if not _core.DB_READY:
        logger.warning("DB not ready (degraded mode), get_referral_overall_stats skipped")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    
    pool = await get_pool()
    if pool is None:
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    
    try:
        async with pool.acquire() as conn:
            # Базовые условия для фильтрации по дате
            date_filter = ""
            params = []
            if date_from or date_to:
                conditions = []
                if date_from:
                    conditions.append("rr.created_at >= $1")
                    params.append(date_from)
                if date_to:
                    param_idx = len(params) + 1
                    conditions.append(f"rr.created_at <= ${param_idx}")
                    params.append(date_to)
                date_filter = "WHERE " + " AND ".join(conditions)
            
            # Всего рефереров (уникальных)
            # Безопасная обработка NULL через COALESCE
            total_referrers_query = f"""
                SELECT COALESCE(COUNT(DISTINCT rr.referrer_id), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_referrers_val = await conn.fetchval(total_referrers_query, *params)
            total_referrers = safe_int(total_referrers_val)
            
            # Всего приглашённых (из таблицы referrals)
            total_referrals_query = "SELECT COALESCE(COUNT(DISTINCT referred_user_id), 0) FROM referrals"
            if date_from or date_to:
                # Если есть фильтр по дате, применяем его к referrals
                if date_from:
                    total_referrals_query += " WHERE created_at >= $1"
                if date_to:
                    param_idx = len([date_from]) + 1
                    total_referrals_query += f" {'AND' if date_from else 'WHERE'} created_at <= ${param_idx}"
            total_referrals_val = await conn.fetchval(total_referrals_query, *params)
            total_referrals = safe_int(total_referrals_val)
            
            # Всего оплативших рефералов (уникальных buyer_id из referral_rewards)
            total_paid_referrals_query = f"""
                SELECT COALESCE(COUNT(DISTINCT rr.buyer_id), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_paid_referrals_val = await conn.fetchval(total_paid_referrals_query, *params)
            total_paid_referrals = safe_int(total_paid_referrals_val)
            
            # Общий доход от рефералов (сумма purchase_amount из referral_rewards)
            total_revenue_query = f"""
                SELECT COALESCE(SUM(rr.purchase_amount), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_revenue_kopecks_val = await conn.fetchval(total_revenue_query, *params)
            total_revenue_kopecks = safe_int(total_revenue_kopecks_val)
            total_revenue = total_revenue_kopecks / 100.0
            
            # Общий выплаченный кешбэк (сумма reward_amount из referral_rewards)
            total_cashback_query = f"""
                SELECT COALESCE(SUM(rr.reward_amount), 0)
                FROM referral_rewards rr
                {date_filter}
            """
            total_cashback_kopecks_val = await conn.fetchval(total_cashback_query, *params)
            total_cashback_kopecks = safe_int(total_cashback_kopecks_val)
            total_cashback_paid = total_cashback_kopecks / 100.0
            
            # Средний кешбэк на реферера (защита от деления на 0)
            avg_cashback_per_referrer = total_cashback_paid / total_referrers if total_referrers > 0 else 0.0
            
            return {
                "total_referrers": total_referrers,
                "total_referrals": total_referrals,
                "total_paid_referrals": total_paid_referrals,
                "total_revenue": round(total_revenue, 2),
                "total_cashback_paid": round(total_cashback_paid, 2),
                "avg_cashback_per_referrer": round(avg_cashback_per_referrer, 2)
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or referral_rewards tables missing or inaccessible — skipping referral overall stats: {e}")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }
    except Exception as e:
        logger.warning(f"Error getting referral overall stats: {e}")
        return {
            "total_referrers": 0,
            "total_referrals": 0,
            "total_paid_referrals": 0,
            "total_revenue": 0.0,
            "total_cashback_paid": 0.0,
            "avg_cashback_per_referrer": 0.0
        }


async def get_referral_rewards_history(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Получить историю начислений реферального кешбэка
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
        limit: Максимальное количество записей
        offset: Смещение для пагинации
    
    Returns:
        Список словарей с историей начислений:
        - id: ID записи
        - referrer_id: Telegram ID реферера
        - referrer_username: Username реферера
        - buyer_id: Telegram ID покупателя
        - buyer_username: Username покупателя
        - purchase_amount: Сумма покупки (рубли)
        - percent: Процент кешбэка
        - reward_amount: Сумма кешбэка (рубли)
        - created_at: Дата начисления
        - purchase_id: ID покупки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Базовый запрос
        base_query = """
            SELECT 
                rr.id,
                rr.referrer_id,
                referrer_user.username AS referrer_username,
                rr.buyer_id,
                buyer_user.username AS buyer_username,
                rr.purchase_amount,
                rr.percent,
                rr.reward_amount,
                rr.created_at,
                rr.purchase_id
            FROM referral_rewards rr
            LEFT JOIN users referrer_user ON rr.referrer_id = referrer_user.telegram_id
            LEFT JOIN users buyer_user ON rr.buyer_id = buyer_user.telegram_id
        """
        
        where_clauses = []
        params = []
        param_index = 1
        
        # Фильтрация по дате
        if date_from:
            where_clauses.append(f"rr.created_at >= ${param_index}")
            params.append(date_from)
            param_index += 1
        
        if date_to:
            where_clauses.append(f"rr.created_at <= ${param_index}")
            params.append(date_to)
            param_index += 1
        
        # Собираем запрос
        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        order_by = "ORDER BY rr.created_at DESC"
        limit_clause = f"LIMIT ${param_index} OFFSET ${param_index + 1}"
        params.extend([limit, offset])
        
        full_query = f"{base_query} {where_clause} {order_by} {limit_clause}"
        
        rows = await conn.fetch(full_query, *params)
        
        # Обрабатываем результаты
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "referrer_id": row["referrer_id"],
                "referrer_username": row["referrer_username"] or f"ID{row['referrer_id']}",
                "buyer_id": row["buyer_id"],
                "buyer_username": row["buyer_username"] or f"ID{row['buyer_id']}",
                "purchase_amount": (row["purchase_amount"] or 0) / 100.0,
                "percent": row["percent"] or 0,
                "reward_amount": (row["reward_amount"] or 0) / 100.0,
                "created_at": row["created_at"],
                "purchase_id": row["purchase_id"]
            })
        
        return result


async def get_referral_rewards_history_count(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> int:
    """
    Получить общее количество записей в истории начислений (для пагинации)
    
    Args:
        date_from: Начальная дата для фильтрации (опционально)
        date_to: Конечная дата для фильтрации (опционально)
    
    Returns:
        Общее количество записей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        base_query = "SELECT COUNT(*) FROM referral_rewards rr"
        
        where_clauses = []
        params = []
        param_index = 1
        
        if date_from:
            where_clauses.append(f"rr.created_at >= ${param_index}")
            params.append(date_from)
            param_index += 1
        
        if date_to:
            where_clauses.append(f"rr.created_at <= ${param_index}")
            params.append(date_to)
            param_index += 1
        
        where_clause = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        full_query = f"{base_query} {where_clause}"
        
        count = await conn.fetchval(full_query, *params) or 0
        return count


async def calculate_final_price(
    telegram_id: int,
    tariff: str,
    period_days: int,
    promo_code: Optional[str] = None,
    country: Optional[str] = None
) -> Dict[str, Any]:
    """
    ЕДИНАЯ ФУНКЦИЯ РАСЧЕТА ФИНАЛЬНОЙ ЦЕНЫ (SINGLE SOURCE OF TRUTH)
    
    Рассчитывает финальную цену тарифа с учетом всех скидок:
    - Базовая цена из config.TARIFFS
    - Промокод (высший приоритет)
    - VIP-скидка 30% (если нет промокода)
    - Спецпредложение -15% (если нет промокода и VIP, подписка истекла)
    - Персональная скидка (если нет промокода, VIP и спецпредложения)
    
    Args:
        telegram_id: Telegram ID пользователя
        tariff: Тип тарифа ("basic" или "plus")
        period_days: Период в днях (30, 90, 180, 365)
        promo_code: Промокод (опционально)
    
    Returns:
        {
            "base_price_kopecks": int,      # Базовая цена в копейках
            "discount_amount_kopecks": int, # Размер скидки в копейках
            "final_price_kopecks": int,     # Финальная цена в копейках
            "discount_percent": int,        # Процент скидки (0-100)
            "discount_type": str,           # "promo", "vip", "personal", None
            "promo_code": Optional[str],    # Промокод (если применен)
            "is_valid": bool                # True если цена >= 64 RUB
        }
    
    Raises:
        ValueError: Если тариф или период не найдены в конфиге
    """
    import config
    
    # Проверяем валидность тарифа и периода
    if tariff not in config.TARIFFS:
        raise ValueError(f"Invalid tariff: {tariff}")
    
    if period_days not in config.TARIFFS[tariff]:
        raise ValueError(f"Invalid period_days: {period_days} for tariff {tariff}")
    
    # Получаем базовую цену в рублях из конфига
    base_price_rubles = config.TARIFFS[tariff][period_days]["price"]
    # Для бизнес-тарифов применяем множитель страны
    if country and config.is_biz_tariff(tariff):
        multiplier = config.BIZ_COUNTRIES.get(country, {}).get("multiplier", 1.0)
        base_price_rubles = int(round(base_price_rubles * multiplier / 100) * 100)
    base_price_kopecks = round(base_price_rubles * 100)
    
    # ПРИОРИТЕТ 0: Промокод (высший приоритет, перекрывает все остальные скидки)
    promo_data = None
    if promo_code:
        promo_data = await check_promo_code_valid(promo_code.upper())
    
    has_promo = promo_data is not None
    
    # ПРИОРИТЕТ 1: VIP-статус (только если нет промокода)
    from database.admin import is_vip_user as _is_vip, get_user_discount as _get_discount
    is_vip = await _is_vip(telegram_id) if not has_promo else False

    # ПРИОРИТЕТ 2: Спецпредложение -15% (только если нет промокода и VIP)
    special_offer = None
    if not has_promo and not is_vip:
        special_offer = await get_special_offer_info(telegram_id)

    # ПРИОРИТЕТ 3: Персональная скидка (только если нет промокода, VIP и спецпредложения)
    personal_discount = None
    if not has_promo and not is_vip and not special_offer:
        personal_discount = await _get_discount(telegram_id)

    # Применяем скидку в порядке приоритета
    discount_amount_kopecks = 0
    discount_percent = 0
    discount_type = None
    final_price_kopecks = base_price_kopecks

    if has_promo:
        discount_percent = promo_data["discount_percent"]
        # КРИТИЧНО: Защита от скидки > 100% - ограничиваем до 100%
        discount_percent = min(discount_percent, 100)
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        # КРИТИЧНО: Гарантируем, что финальная цена >= 0
        final_price_kopecks = max(final_price_kopecks, 0)
        discount_type = "promo"
        applied_promo_code = promo_code.upper()
    elif is_vip:
        discount_percent = 30
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "vip"
        applied_promo_code = None
    elif special_offer:
        discount_percent = special_offer["discount_percent"]
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "special_offer"
        applied_promo_code = None
    elif personal_discount:
        discount_percent = personal_discount["discount_percent"]
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "personal"
        applied_promo_code = None
    else:
        applied_promo_code = None
    
    # Округляем до целых копеек
    final_price_kopecks = int(final_price_kopecks)
    
    # Проверяем минимальную цену (64 RUB = 6400 kopecks)
    MIN_PRICE_KOPECKS = 6400
    is_valid = final_price_kopecks >= MIN_PRICE_KOPECKS
    
    return {
        "base_price_kopecks": base_price_kopecks,
        "discount_amount_kopecks": discount_amount_kopecks,
        "final_price_kopecks": final_price_kopecks,
        "discount_percent": discount_percent,
        "discount_type": discount_type,
        "promo_code": applied_promo_code,
        "is_valid": is_valid
    }


async def create_pending_balance_topup_purchase(
    telegram_id: int,
    amount_kopecks: int,
) -> str:
    """
    Create pending purchase for balance top-up only.
    No tariff, no period_days. Separate from subscription logic.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        purchase_id = f"purchase_{uuid_lib.uuid4().hex[:16]}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        await conn.execute(
            """INSERT INTO pending_purchases (purchase_id, telegram_id, purchase_type, price_kopecks, status, expires_at)
               VALUES ($1, $2, 'balance_topup', $3, 'pending', $4)""",
            purchase_id, telegram_id, amount_kopecks, _to_db_utc(expires_at)
        )
        logger.info(
            f"BALANCE_TOPUP_PURCHASE_CREATED purchase_id={purchase_id} telegram_id={telegram_id} "
            f"amount={amount_kopecks} kopecks"
        )
        return purchase_id


async def create_pending_purchase(
    telegram_id: int,
    tariff: str,  # "basic", "plus", or "biz_*"
    period_days: int,
    price_kopecks: int,
    promo_code: Optional[str] = None,
    country: Optional[str] = None,
    purchase_type: str = "subscription",
) -> str:
    """
    Создать pending покупку с уникальным purchase_id

    Args:
        telegram_id: Telegram ID пользователя
        tariff: Тип тарифа ("basic" или "plus")
        period_days: Период в днях (30, 90, 180, 365)
        price_kopecks: Цена в копейках
        promo_code: Промокод (опционально)
        purchase_type: Тип покупки ("subscription", "gift", "balance_topup")

    Returns:
        purchase_id: Уникальный ID покупки
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Отменяем все предыдущие pending покупки этого пользователя
        await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )

        # Генерируем уникальный purchase_id
        purchase_id = f"purchase_{uuid_lib.uuid4().hex[:16]}"

        # Срок действия контекста покупки (30 минут)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

        # Создаем запись о покупке
        await conn.execute(
            """INSERT INTO pending_purchases (purchase_id, telegram_id, purchase_type, tariff, period_days, price_kopecks, promo_code, status, expires_at, country)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            purchase_id, telegram_id, purchase_type, tariff, period_days, price_kopecks, promo_code, "pending", _to_db_utc(expires_at), country
        )

        logger.info(f"Pending purchase created: purchase_id={purchase_id}, telegram_id={telegram_id}, tariff={tariff}, period_days={period_days}, price={price_kopecks} kopecks, country={country}")
        
        return purchase_id


async def get_pending_purchase(purchase_id: str, telegram_id: int, check_expiry: bool = True) -> Optional[Dict[str, Any]]:
    """
    Получить pending покупку по purchase_id с валидацией
    
    Args:
        purchase_id: ID покупки
        telegram_id: Telegram ID пользователя
        check_expiry: Проверять ли срок действия (по умолчанию True, False для оплаты)
    
    Returns:
        Словарь с данными покупки, если валидна, иначе None
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_pending_purchase skipped")
        return None
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_pending_purchase skipped")
        return None
    async with pool.acquire() as conn:
        if check_expiry:
            # При обычной проверке (создание покупки) проверяем срок действия
            purchase = await conn.fetchrow(
                """SELECT * FROM pending_purchases 
                   WHERE purchase_id = $1 AND telegram_id = $2 AND status = 'pending' AND expires_at > NOW()""",
                purchase_id, telegram_id
            )
        else:
            # При оплате (webhook) не проверяем срок - покупка может быть оплачена после expires_at
            purchase = await conn.fetchrow(
                """SELECT * FROM pending_purchases 
                   WHERE purchase_id = $1 AND telegram_id = $2 AND status = 'pending'""",
                purchase_id, telegram_id
            )
        
        if purchase:
            return dict(purchase)
        else:
            logger.warning(f"Invalid pending purchase: purchase_id={purchase_id}, telegram_id={telegram_id}, check_expiry={check_expiry}")
            return None


async def get_pending_purchase_by_id(purchase_id: str, check_expiry: bool = False) -> Optional[Dict[str, Any]]:
    """
    Get pending purchase by purchase_id only (for webhook when payload is "purchase:{id}").
    
    Args:
        purchase_id: ID покупки
        check_expiry: Проверять ли срок действия (по умолчанию False для webhook)
    
    Returns:
        Словарь с данными покупки или None
    """
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        if check_expiry:
            row = await conn.fetchrow(
                """SELECT * FROM pending_purchases
                   WHERE purchase_id = $1 AND status = 'pending' AND expires_at > NOW()""",
                purchase_id
            )
        else:
            # For webhooks: accept both 'pending' and 'expired' — payment may arrive
            # after user created a new purchase (which expired the old one)
            row = await conn.fetchrow(
                """SELECT * FROM pending_purchases
                   WHERE purchase_id = $1 AND status IN ('pending', 'expired')""",
                purchase_id
            )
        return dict(row) if row else None


async def cancel_pending_purchases(telegram_id: int, reason: str = "user_action") -> None:
    """
    Отменить все pending покупки пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        reason: Причина отмены
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        
        if result != "UPDATE 0":
            logger.info(f"Pending purchases cancelled: telegram_id={telegram_id}, reason={reason}")


async def update_pending_purchase_invoice_id(purchase_id: str, invoice_id: str) -> bool:
    """
    Обновить provider_invoice_id для pending покупки
    
    Args:
        purchase_id: ID покупки
        invoice_id: Invoice ID от платежного провайдера
    
    Returns:
        True если успешно, False если покупка не найдена
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Для crypto purchases устанавливаем TTL = 30 минут с момента создания invoice
        now_utc = datetime.now(timezone.utc)
        expires_at_utc = now_utc + timedelta(minutes=30)
        
        result = await conn.execute(
            "UPDATE pending_purchases SET provider_invoice_id = $1, expires_at = $3 WHERE purchase_id = $2 AND status = 'pending'",
            invoice_id, purchase_id, _to_db_utc(expires_at_utc)
        )
        
        if result == "UPDATE 1":
            logger.info(f"Pending purchase invoice_id updated: purchase_id={purchase_id}, invoice_id={invoice_id}")
            return True
        else:
            logger.warning(f"Failed to update pending purchase invoice_id: purchase_id={purchase_id}, result={result}")
            return False


async def mark_pending_purchase_paid(purchase_id: str) -> bool:
    """
    Пометить pending покупку как оплаченную
    
    Args:
        purchase_id: ID покупки
    
    Returns:
        True если успешно, False если покупка не найдена или уже оплачена
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'paid' WHERE purchase_id = $1 AND status IN ('pending', 'expired')",
            purchase_id
        )

        if result == "UPDATE 1":
            logger.info(f"Pending purchase marked as paid: purchase_id={purchase_id}")
            return True
        else:
            logger.warning(f"Failed to mark pending purchase as paid: purchase_id={purchase_id}, result={result}")
            return False


async def finalize_purchase(
    purchase_id: str,
    payment_provider: str,
    amount_rubles: float,
    invoice_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    ЕДИНАЯ ФУНКЦИЯ ФИНАЛИЗАЦИИ ПОКУПКИ (SINGLE SOURCE OF TRUTH)
    
    Эта функция вызывается после успешной оплаты (карта или крипта)
    и выполняет ВСЮ бизнес-логику в ОДНОЙ транзакции:
    
    1. Проверяет pending_purchase (должен быть status='pending')
    2. Обновляет pending_purchase → status='paid'
    3. Создает payment record
    4. Активирует подписку через grant_access
    5. Обновляет payment → status='approved'
    6. Обрабатывает реферальный кешбэк
    
    КРИТИЧНО: Все операции в одной транзакции БД.
    Если любой шаг падает → rollback, логирование, исключение.
    
    Args:
        purchase_id: ID покупки из pending_purchases
        payment_provider: 'telegram_payment', 'platega', 'telegram_stars', etc.
        amount_rubles: Сумма оплаты в рублях
        invoice_id: ID инвойса (опционально)
    
    Returns:
        {
            "success": bool,
            "payment_id": int,
            "expires_at": datetime,
            "vpn_key": str,
            "is_renewal": bool
        }
    
    Raises:
        ValueError: Если pending_purchase не найден или уже обработан
        Exception: При любых ошибках активации подписки
    """
    from datetime import timedelta

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Pre-fetch with FOR UPDATE SKIP LOCKED to prevent concurrent finalization
        # SECURITY: Row-level lock ensures only one webhook can process a purchase at a time
        pending_row = await conn.fetchrow(
            "SELECT * FROM pending_purchases WHERE purchase_id = $1 FOR UPDATE SKIP LOCKED",
            purchase_id
        )
        if not pending_row:
            # Could be not found OR locked by another concurrent finalization
            error_msg = f"Pending purchase not found or locked: purchase_id={purchase_id}"
            logger.error(f"finalize_purchase: payment_rejected: reason=purchase_not_found_or_locked, {error_msg}")
            raise ValueError(error_msg)
        pending_purchase = dict(pending_row)
        telegram_id = pending_purchase["telegram_id"]
        status = pending_purchase.get("status")
        promo_code = pending_purchase.get("promo_code")
        if status == "paid":
            error_msg = f"Pending purchase already processed: purchase_id={purchase_id}, status={status}"
            logger.warning(f"finalize_purchase: payment_rejected: reason=already_processed, {error_msg}")
            raise ValueError(error_msg)
        if status not in ("pending", "expired"):
            error_msg = f"Pending purchase invalid status: purchase_id={purchase_id}, status={status}"
            logger.warning(f"finalize_purchase: payment_rejected: reason=invalid_status, {error_msg}")
            raise ValueError(error_msg)
        if status == "expired":
            logger.info(f"finalize_purchase: recovering expired purchase: purchase_id={purchase_id}, user={telegram_id}")
        tariff_type = pending_purchase.get("tariff")
        period_days = pending_purchase.get("period_days")
        purchase_type = pending_purchase.get("purchase_type", "subscription")
        price_kopecks = pending_purchase["price_kopecks"]
        purchase_country = pending_purchase.get("country")
        expected_amount_rubles = price_kopecks / 100.0
        is_balance_topup = (purchase_type == "balance_topup") or (period_days == 0 and purchase_type not in ("traffic_pack", "gift"))
        is_gift_purchase = (purchase_type == "gift")
        is_traffic_pack = (purchase_type == "traffic_pack")
        amount_diff = abs(amount_rubles - expected_amount_rubles)
        # SECURITY: Percentage-based tolerance (0.5%) instead of fixed ±1₽
        # For 149₽ → max diff 0.75₽, for 1199₽ → max diff 6₽, minimum floor 0.50₽
        max_tolerance = max(0.50, expected_amount_rubles * 0.005)
        if amount_diff > max_tolerance:
            error_msg = (
                f"Payment amount mismatch: purchase_id={purchase_id}, user={telegram_id}, "
                f"expected={expected_amount_rubles:.2f} RUB, actual={amount_rubles:.2f} RUB, "
                f"diff={amount_diff:.2f} RUB (tolerance={max_tolerance:.2f} RUB)"
            )
            logger.error(f"finalize_purchase: PAYMENT_AMOUNT_MISMATCH: {error_msg}")
            raise ValueError(error_msg)

        # TWO-PHASE: Phase 1 — add_vless_user OUTSIDE transaction (orphan prevention)
        pre_provisioned_uuid = None
        uuid_to_cleanup_on_failure = None
        if not is_balance_topup and not is_gift_purchase and tariff_type and period_days and period_days > 0:
            sub_row = await conn.fetchrow("SELECT * FROM subscriptions WHERE telegram_id = $1", telegram_id)
            now_pre = datetime.now(timezone.utc)
            is_new_issuance = True
            if sub_row:
                sub = dict(sub_row)
                exp_raw = sub.get("expires_at")
                exp = _from_db_utc(exp_raw) if exp_raw else None
                is_new_issuance = (
                    sub.get("status") != "active"
                    or not exp
                    or exp <= now_pre
                    or not sub.get("uuid")
                )
            if is_new_issuance and config.VPN_ENABLED:
                try:
                    duration_pre = timedelta(days=period_days)
                    subscription_end_pre = now_pre + duration_pre
                    new_uuid_pre = _generate_subscription_uuid()
                    vless_result = await vpn_utils.add_vless_user(
                        telegram_id=telegram_id,
                        subscription_end=subscription_end_pre,
                        uuid=new_uuid_pre,
                        tariff=tariff_type or "basic",
                    )
                    pre_provisioned_uuid = {
                        "uuid": vless_result["uuid"].strip(),
                        "vless_url": vless_result["vless_url"],
                        "vless_url_plus": vless_result.get("vless_url_plus"),
                        "subscription_type": vless_result.get("subscription_type") or tariff_type or "basic",
                    }
                    uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                    logger.info(
                        f"finalize_purchase: TWO_PHASE_PHASE1_DONE [purchase_id={purchase_id}, "
                        f"user={telegram_id}, uuid={uuid_to_cleanup_on_failure[:8]}...]"
                    )
                except Exception as phase1_err:
                    logger.warning(
                        f"finalize_purchase: Phase 1 add_vless_user failed (grant_access may use pending_activation): "
                        f"purchase_id={purchase_id}, user={telegram_id}, error={phase1_err}"
                    )
                    pre_provisioned_uuid = None
                    uuid_to_cleanup_on_failure = None

        try:
            async with conn.transaction():
                assert conn is not None, "finalize_purchase requires an active DB connection"
                logger.info(
                    f"finalize_purchase: START [purchase_id={purchase_id}, user={telegram_id}, "
                    f"provider={payment_provider}, amount={amount_rubles:.2f} RUB (expected={expected_amount_rubles:.2f} RUB), "
                    f"purchase_type={purchase_type}, tariff={tariff_type}, period_days={period_days}]"
                )
                logger.info(
                    f"payment_event_received: purchase_id={purchase_id}, user={telegram_id}, "
                    f"provider={payment_provider}, amount={amount_rubles:.2f} RUB, invoice_id={invoice_id or 'N/A'}"
                )
                logger.info(
                    f"payment_verified: purchase_id={purchase_id}, user={telegram_id}, "
                    f"provider={payment_provider}, amount={amount_rubles:.2f} RUB, amount_match=True, purchase_status={status}"
                )

                # STEP 3: Обновляем pending_purchase → paid
                result = await conn.execute(
                    "UPDATE pending_purchases SET status = 'paid' WHERE purchase_id = $1 AND status IN ('pending', 'expired')",
                    purchase_id
                )
            
                if result != "UPDATE 1":
                    error_msg = f"Failed to mark pending purchase as paid: purchase_id={purchase_id}"
                    logger.error(f"finalize_purchase: payment_rejected: reason=db_update_failed, {error_msg}")
                    raise Exception(error_msg)

                if is_balance_topup:
                    # CRITICAL: Balance top-up MUST run inside the same transaction as finalize_purchase.
                    # increase_balance is called with conn=conn to ensure atomicity.
                    # Do NOT remove conn parameter — this prevents free balance on partial rollback.
                    # ОБРАБОТКА ПОПОЛНЕНИЯ БАЛАНСА
                    logger.info(
                        f"finalize_purchase: BALANCE_TOPUP [purchase_id={purchase_id}, user={telegram_id}, "
                        f"amount={amount_rubles:.2f} RUB]"
                    )
                    from database.users import increase_balance, process_referral_reward as _process_referral_reward
                    balance_increased = await increase_balance(
                        telegram_id=telegram_id,
                        amount=amount_rubles,
                        source=payment_provider or "telegram_payment",
                        description=f"Balance top-up via {payment_provider}",
                        conn=conn
                    )
                    if not balance_increased:
                        error_msg = f"Failed to increase balance: purchase_id={purchase_id}, user={telegram_id}"
                        logger.error(f"finalize_purchase: {error_msg}")
                        raise Exception(error_msg)
                    now_utc = datetime.now(timezone.utc)
                    payment_id = await conn.fetchval(
                        """INSERT INTO payments (telegram_id, tariff, amount, status, paid_at)
                           VALUES ($1, $2, $3, 'approved', $4) RETURNING id""",
                        telegram_id,
                        "balance_topup",
                        round(amount_rubles * 100),
                        _to_db_utc(now_utc)
                    )
                    if not payment_id:
                        error_msg = f"Failed to create payment record: purchase_id={purchase_id}, user={telegram_id}"
                        logger.error(f"finalize_purchase: {error_msg}")
                        raise Exception(error_msg)
                    if promo_code:
                        await _consume_promo_in_transaction(conn, promo_code, telegram_id, purchase_id)
                    referral_reward_result = await _process_referral_reward(
                        buyer_id=telegram_id,
                        purchase_id=purchase_id,
                        amount_rubles=amount_rubles,
                        conn=conn
                    )
                    if referral_reward_result.get("success"):
                        logger.info(
                            f"finalize_purchase: REFERRAL_CASHBACK_GRANTED [BALANCE_TOPUP] "
                            f"purchase_id={purchase_id}, user={telegram_id}, "
                            f"referrer={referral_reward_result.get('referrer_id')}, "
                            f"amount={referral_reward_result.get('reward_amount')} RUB"
                        )
                    else:
                        reason = referral_reward_result.get("reason", "unknown")
                        logger.debug(
                            f"finalize_purchase: Referral reward skipped for balance topup: "
                            f"purchase_id={purchase_id}, user={telegram_id}, reason={reason}"
                        )
                    logger.info(
                        f"balance_topup_completed: purchase_id={purchase_id}, user={telegram_id}, "
                        f"provider={payment_provider}, payment_id={payment_id}, amount={amount_rubles:.2f} RUB"
                    )
                    logger.info(
                        f"finalize_purchase: SUCCESS [BALANCE_TOPUP] [purchase_id={purchase_id}, user={telegram_id}, "
                        f"provider={payment_provider}, payment_id={payment_id}, amount={amount_rubles:.2f} RUB]"
                    )
                    return {
                        "success": True,
                        "payment_id": payment_id,
                        "expires_at": None,
                        "vpn_key": None,
                        "is_renewal": False,
                        "is_balance_topup": True,
                        "amount": amount_rubles,
                        "referral_reward": referral_reward_result
                    }

                # STEP 4.5: ОБРАБОТКА ПОДАРОЧНОЙ ПОДПИСКИ (gift)
                if is_gift_purchase:
                    logger.info(
                        f"finalize_purchase: GIFT_PURCHASE [purchase_id={purchase_id}, user={telegram_id}, "
                        f"tariff={tariff_type}, period_days={period_days}, amount={amount_rubles:.2f} RUB]"
                    )
                    now_utc = datetime.now(timezone.utc)
                    payment_id = await conn.fetchval(
                        """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id, paid_at)
                           VALUES ($1, $2, $3, 'approved', $4, $5) RETURNING id""",
                        telegram_id,
                        f"gift_{tariff_type}_{period_days}",
                        round(amount_rubles * 100),
                        purchase_id,
                        _to_db_utc(now_utc),
                    )
                    if not payment_id:
                        raise Exception(f"Failed to create payment record for gift: purchase_id={purchase_id}")

                    # Создаём подарочную подписку
                    from database.admin import generate_gift_code
                    gift_code = generate_gift_code()
                    gift_expires = now_utc + timedelta(days=90)
                    await conn.execute(
                        """INSERT INTO gift_subscriptions
                           (gift_code, buyer_telegram_id, tariff, period_days, price_kopecks,
                            purchase_id, status, created_at, expires_at)
                           VALUES ($1, $2, $3, $4, $5, $6, 'paid', $7, $8)""",
                        gift_code, telegram_id, tariff_type, period_days, price_kopecks,
                        purchase_id, _to_db_utc(now_utc), _to_db_utc(gift_expires),
                    )

                    logger.info(
                        f"finalize_purchase: GIFT_CREATED [purchase_id={purchase_id}, user={telegram_id}, "
                        f"gift_code={gift_code}, tariff={tariff_type}, period={period_days}d]"
                    )
                    return {
                        "success": True,
                        "payment_id": payment_id,
                        "expires_at": None,
                        "vpn_key": None,
                        "is_renewal": False,
                        "is_gift": True,
                        "gift_code": gift_code,
                        "gift_tariff": tariff_type,
                        "gift_period_days": period_days,
                    }

                # STEP 4.6: ОБРАБОТКА ПОКУПКИ ТРАФИКА (traffic_pack)
                if is_traffic_pack:
                    logger.info(
                        f"finalize_purchase: TRAFFIC_PACK [purchase_id={purchase_id}, user={telegram_id}, "
                        f"tariff={tariff_type}, amount={amount_rubles:.2f} RUB]"
                    )
                    now_utc = datetime.now(timezone.utc)
                    payment_id = await conn.fetchval(
                        """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id, paid_at)
                           VALUES ($1, $2, $3, 'approved', $4, $5) RETURNING id""",
                        telegram_id,
                        tariff_type or "traffic_pack",
                        round(amount_rubles * 100),
                        purchase_id,
                        _to_db_utc(now_utc),
                    )
                    if not payment_id:
                        raise Exception(f"Failed to create payment record for traffic pack: purchase_id={purchase_id}")

                    # Extract GB amount from tariff field (e.g., "traffic_5gb" → 5)
                    _gb = 0
                    if tariff_type and tariff_type.startswith("traffic_") and tariff_type.endswith("gb"):
                        try:
                            _gb = int(tariff_type[len("traffic_"):-len("gb")])
                        except ValueError:
                            pass

                    # Record in traffic_purchases
                    _payment_method = payment_provider or "card"
                    await conn.execute(
                        """INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub, payment_method, created_at)
                           VALUES ($1, $2, $3, $4, $5)""",
                        telegram_id,
                        _gb,
                        round(amount_rubles),
                        _payment_method,
                        _to_db_utc(now_utc),
                    )

                    logger.info(
                        f"finalize_purchase: TRAFFIC_PACK_DONE [purchase_id={purchase_id}, user={telegram_id}, "
                        f"payment_id={payment_id}, gb={_gb}, method={_payment_method}]"
                    )
                    return {
                        "success": True,
                        "payment_id": payment_id,
                        "expires_at": None,
                        "vpn_key": None,
                        "is_renewal": False,
                        "is_traffic_pack": True,
                        "traffic_gb": _gb,
                    }

                # STEP 5: ОБРАБОТКА ПОДПИСКИ (subscription only)
                if tariff_type is None or period_days is None or period_days <= 0:
                    error_msg = f"Invalid subscription purchase: tariff={tariff_type}, period_days={period_days}"
                    logger.error(f"finalize_purchase: {error_msg}")
                    raise ValueError(error_msg)
                now_utc = datetime.now(timezone.utc)
                payment_id = await conn.fetchval(
                    """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id, cryptobot_payment_id, paid_at)
                       VALUES ($1, $2, $3, 'pending', $4, $5, $6) RETURNING id""",
                    telegram_id,
                    f"{tariff_type}_{period_days}",
                    round(amount_rubles * 100),
                    purchase_id,
                    str(invoice_id) if invoice_id else None,
                    _to_db_utc(now_utc)
                )
                if not payment_id:
                    error_msg = f"Failed to create payment record: purchase_id={purchase_id}, user={telegram_id}"
                    logger.error(f"finalize_purchase: {error_msg}")
                    raise Exception(error_msg)
                duration = timedelta(days=period_days)
                # When Phase 1 succeeded we have pre_provisioned_uuid → use caller's conn and two-phase.
                # When Phase 1 failed (pre_provisioned_uuid is None) → grant_access must run add_vless_user
                # outside any transaction: pass conn=None and _caller_holds_transaction=False to avoid
                # INVARIANT_VIOLATION; grant_access will acquire its own conn and call add_vless_user outside tx.
                grant_result_for_removal = grant_result = await grant_access(
                    telegram_id=telegram_id,
                    duration=duration,
                    source="payment",
                    admin_telegram_id=None,
                    admin_grant_days=None,
                    conn=conn if pre_provisioned_uuid else None,
                    pre_provisioned_uuid=pre_provisioned_uuid,
                    _caller_holds_transaction=bool(pre_provisioned_uuid),
                    tariff=tariff_type or "basic",
                    country=purchase_country,
                )
                if not grant_result:
                    error_msg = f"grant_access returned None: purchase_id={purchase_id}, user={telegram_id}"
                    logger.error(f"finalize_purchase: {error_msg}")
                    raise Exception(error_msg)
                expires_at = grant_result.get("subscription_end")
                if not expires_at:
                    error_msg = f"grant_access returned None expires_at: purchase_id={purchase_id}, user={telegram_id}"
                    logger.error(f"finalize_purchase: {error_msg}")
                    raise Exception(error_msg)
            
                # Проверяем action для обработки pending activation
                action = grant_result.get("action")
                is_renewal = action == "renewal"
            
                # PENDING ACTIVATION: Если action == 'pending_activation', это ожидаемое поведение
                # VPN ключ будет создан позже activation_worker'ом
                if action == "pending_activation":
                    logger.info(
                        f"finalize_purchase: PENDING_ACTIVATION_ACCEPTED [purchase_id={purchase_id}, user={telegram_id}]"
                    )
                
                    # Обновляем payment → approved
                    await conn.execute(
                        "UPDATE payments SET status = 'approved' WHERE id = $1",
                        payment_id
                    )
                
                    ret_val = {
                        "success": True,
                        "payment_id": payment_id,
                        "expires_at": expires_at,
                        "vpn_key": None,
                        "activation_status": "pending",
                        "is_renewal": False
                    }
                else:
                    # Получаем VPN ключ для нормальной активации
                    vpn_key = grant_result.get("vless_url")
                
                    if not vpn_key:
                        # Renewal: get vpn_key from subscription (API is source of truth, no local generation)
                        if is_renewal:
                            vpn_key = grant_result.get("vpn_key")
                            if not vpn_key:
                                subscription_row = await conn.fetchrow(
                                    "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                                    telegram_id
                                )
                                vpn_key = subscription_row["vpn_key"] if subscription_row and subscription_row.get("vpn_key") else ""
                            if not vpn_key:
                                error_msg = (
                                    f"Renewal: no vpn_key in subscription or grant_result. "
                                    f"Bot MUST use vless_link from API only. purchase_id={purchase_id}, user={telegram_id}"
                                )
                                logger.error(f"finalize_purchase: {error_msg}")
                                raise Exception(error_msg)
                        else:
                            # New issuance: vless_url must come from grant_access (API response)
                            error_msg = (
                                f"No VPN key from API: purchase_id={purchase_id}, user={telegram_id}. "
                                "API must return vless_link. Bot MUST NOT generate links."
                            )
                            logger.error(f"finalize_purchase: {error_msg}")
                            raise Exception(error_msg)
                
                    if not vpn_key:
                        error_msg = f"VPN key is empty: purchase_id={purchase_id}, user={telegram_id}"
                        logger.error(f"finalize_purchase: {error_msg}")
                        raise Exception(error_msg)
                
                    # API is source of truth — vpn_key from API, no local validation
                    # STEP 6: Потребляем промокод ПЕРЕД approve (если consumption упадёт — payment не будет approved)
                    if promo_code:
                        await _consume_promo_in_transaction(conn, promo_code, telegram_id, purchase_id)

                    # STEP 7: Обновляем payment → approved (ПОСЛЕ promo consumption для атомарности)
                    await conn.execute(
                        "UPDATE payments SET status = 'approved' WHERE id = $1",
                        payment_id
                    )
                
                    # STEP 8: Обрабатываем реферальный кешбэк
                    # Обработка реферального кешбэка внутри той же транзакции
                    # FINANCIAL errors будут проброшены и откатят всю транзакцию
                    # BUSINESS errors вернут success=False и покупка продолжится без награды
                    from database.users import process_referral_reward as _prr
                    referral_reward_result = await _prr(
                        buyer_id=telegram_id,
                        purchase_id=purchase_id,
                        amount_rubles=amount_rubles,
                        conn=conn
                    )
                
                    if referral_reward_result.get("success"):
                        logger.info(
                            f"finalize_purchase: referral_reward_processed: purchase_id={purchase_id}, "
                            f"user={telegram_id}, referrer={referral_reward_result.get('referrer_id')}, "
                            f"amount={referral_reward_result.get('reward_amount')} RUB"
                        )
                    else:
                        # BUSINESS LOGIC ERROR: Reward skipped but purchase continues
                        reason = referral_reward_result.get("reason", "unknown")
                        logger.info(
                            f"finalize_purchase: Purchase finalized without referral reward: "
                            f"purchase_id={purchase_id}, user={telegram_id}, reason={reason}"
                        )
                
                    # КРИТИЧНО: Логируем активацию подписки и выдачу ключа для аудита
                    logger.info(
                        f"subscription_activated: purchase_id={purchase_id}, user={telegram_id}, "
                        f"provider={payment_provider}, payment_id={payment_id}, "
                        f"expires_at={expires_at.isoformat()}, is_renewal={is_renewal}"
                    )
                
                    logger.info(
                        f"vpn_key_issued: purchase_id={purchase_id}, user={telegram_id}, "
                        f"provider={payment_provider}, payment_id={payment_id}, "
                        f"vpn_key_length={len(vpn_key)}, is_renewal={is_renewal}"
                    )
                
                    logger.info(
                        f"finalize_purchase: SUCCESS [purchase_id={purchase_id}, user={telegram_id}, provider={payment_provider}, "
                        f"payment_id={payment_id}, expires_at={expires_at.isoformat()}, "
                        f"is_renewal={is_renewal}, vpn_key_length={len(vpn_key)}, subscription_activated=True, vpn_key_issued=True]"
                    )

                    raw_subscription_type = grant_result.get("subscription_type")
                    subscription_type_ret = (raw_subscription_type or "basic").strip().lower()
                    if subscription_type_ret not in config.VALID_SUBSCRIPTION_TYPES:
                        logger.warning(
                            f"TARIFF_TYPE_COERCED: purchase_id={purchase_id}, user={telegram_id}, "
                            f"raw_value='{raw_subscription_type}', coerced_to='basic'"
                        )
                        subscription_type_ret = "basic"
                    if is_renewal:
                        sub_row = await conn.fetchrow(
                            "SELECT subscription_type FROM subscriptions WHERE telegram_id = $1",
                            telegram_id
                        )
                        if sub_row and sub_row.get("subscription_type"):
                            subscription_type_ret = (sub_row["subscription_type"] or "basic").strip().lower()

                    vpn_key_plus_ret = grant_result.get("vpn_key_plus") or grant_result.get("vless_url_plus")
                    ret_val = {
                        "success": True,
                        "payment_id": payment_id,
                        "expires_at": expires_at,
                        "vpn_key": vpn_key,
                        "vpn_key_plus": vpn_key_plus_ret,
                        "is_renewal": is_renewal,
                        "subscription_type": subscription_type_ret,
                        "referral_reward": referral_reward_result,
                        "is_basic_to_plus_upgrade": grant_result.get("is_basic_to_plus_upgrade", False),
                    }
        except Exception as tx_err:
            # TWO-PHASE: Phase 2 failed — remove orphan UUID from Xray
            if uuid_to_cleanup_on_failure:
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                    logger.critical(
                        f"ORPHAN_PREVENTED uuid={uuid_preview} reason=phase2_failed "
                        f"purchase_id={purchase_id} user={telegram_id} error={tx_err}"
                    )
                except Exception as remove_err:
                    uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                    logger.critical(
                        f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} "
                        f"purchase_id={purchase_id} user={telegram_id}"
                    )
            raise
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("old_uuid_to_remove_after_commit"):
            old_uuid = grant_result_for_removal["old_uuid_to_remove_after_commit"]
            try:
                await vpn_utils.safe_remove_vless_user_with_retry(old_uuid)
                logger.info("OLD_UUID_REMOVED_AFTER_COMMIT", extra={"uuid": old_uuid[:8] + "..."})
            except Exception as e:
                logger.critical(
                    "OLD_UUID_REMOVAL_FAILED_POST_COMMIT",
                    extra={"uuid": old_uuid[:8] + "...", "error": str(e)[:200]}
                )
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("renewal_xray_sync_after_commit"):
            sync_info = grant_result_for_removal["renewal_xray_sync_after_commit"]
            try:
                await vpn_utils.ensure_user_in_xray(
                    telegram_id=sync_info["telegram_id"],
                    uuid=sync_info["uuid"],
                    subscription_end=sync_info["subscription_end"]
                )
            except Exception as e:
                logger.critical(
                    "RENEWAL_XRAY_SYNC_FAILED",
                    extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
                )
        if ret_val is not None:
            return ret_val


