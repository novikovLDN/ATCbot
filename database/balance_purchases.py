"""Покупка и пополнение с внутреннего баланса — денежное ядро.

ЧТО ЗДЕСЬ
    finalize_balance_purchase — оплата подписки деньгами с баланса.
    finalize_balance_topup    — зачисление на баланс после внешней оплаты.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ
    Это две самые длинные функции админского слоя (279 и 217 строк) и
    единственные там, где деньги списываются и зачисляются. Их правят по
    другим причинам, чем экспорт статистики или рассылки, рядом с которыми
    они лежали, — и ошибка здесь стоит несопоставимо дороже.

ЧТО ЛЕГКО СЛОМАТЬ
    Обе функции работают под advisory-локом по telegram_id и внутри одной
    транзакции: проверка баланса и списание обязаны быть неразделимы, иначе
    два параллельных нажатия спишут дважды. Любой внешний вызов (панель,
    Telegram) выносится за пределы транзакции — держать её открытой на время
    сетевого запроса значит исчерпать пул соединений.

    Деньги в базе в копейках, аргументы приходят в рублях: преобразование
    делается на границе, и переносить его внутрь нельзя.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import config
import vpn_utils
import database.core as _core
from database.core import get_pool, _to_db_utc, _from_db_utc, safe_int
from database.subscriptions import (
    grant_access,
    _log_audit_event_atomic,
    _log_subscription_history_atomic,
)
from database.promo import _consume_promo_in_transaction

logger = logging.getLogger(__name__)


async def finalize_balance_purchase(
    telegram_id: int,
    tariff_type: str,
    period_days: int,
    amount_rubles: float,
    description: Optional[str] = None,
    promo_code: Optional[str] = None,
    country: Optional[str] = None
) -> Dict[str, Any]:
    """
    Атомарно обработать покупку подписки с баланса.
    
    Выполняет в одной транзакции:
    - Списывает баланс
    - Активирует подписку
    - Создает запись о платеже
    - Обрабатывает реферальный кешбэк
    
    Args:
        telegram_id: Telegram ID пользователя
        tariff_type: Тип тарифа ('basic' или 'plus')
        period_days: Количество дней подписки
        amount_rubles: Сумма платежа в рублях
        description: Описание платежа (опционально)
        promo_code: Промокод (опционально, потребляется внутри транзакции)
    
    Returns:
        {
            "success": bool,
            "payment_id": Optional[int],
            "expires_at": Optional[datetime],
            "vpn_key": Optional[str],
            "is_renewal": bool,
            "new_balance": float,
            "referral_reward": Optional[Dict[str, Any]]
        }
    
    Raises:
        ValueError: При недостатке баланса или других бизнес-ошибках
        asyncpg exceptions: При финансовых ошибках (откат транзакции)
    """
    from database.subscriptions import grant_access
    from database.users import process_referral_reward

    if amount_rubles <= 0:
        raise ValueError(f"Invalid amount for balance purchase: {amount_rubles}")
    
    amount_kopecks = round(amount_rubles * 100)
    pool = await get_pool()
    
    if pool is None:
        raise RuntimeError("Database pool is not available")

    duration = timedelta(days=period_days)
    now_pre = datetime.now(timezone.utc)
    subscription_end_pre = now_pre + duration

    # PHASE 1 (outside DB transaction): Provision UUID via VPN API if new issuance needed
    pre_provisioned_uuid = None
    uuid_to_cleanup_on_failure = None
    async with pool.acquire() as conn_pre:
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
                # Task 2 cut-over: provision premium + bypass entities in
                # Remnawave; the legacy samopis xray master is no longer
                # called from the balance-purchase path.  Return shape
                # matches add_vless_user so Phase 2 (grant_access) is unchanged.
                from app.services import purchase_flow
                tariff_norm = (tariff_type or "basic").strip().lower()
                vless_result = await purchase_flow.provision_subscription(
                    telegram_id,
                    tariff=tariff_norm,
                    subscription_end=subscription_end_pre,
                    period_days=period_days,
                    is_trial=False,
                )
                pre_provisioned_uuid = {
                    "uuid": vless_result["uuid"].strip(),
                    "vless_url": vless_result["vless_url"],
                    "vless_url_plus": vless_result.get("vless_url_plus"),
                    "subscription_type": vless_result.get("subscription_type") or tariff_norm,
                }
                uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                logger.info(
                    f"finalize_balance_purchase: TWO_PHASE_PHASE1_DONE [user={telegram_id}, "
                    f"uuid={uuid_to_cleanup_on_failure[:8]}..., tariff={tariff_norm}]"
                )
            except Exception as phase1_err:
                logger.warning(
                    f"finalize_balance_purchase: Phase 1 provisioning failed: user={telegram_id}, error={phase1_err}"
                )
                pre_provisioned_uuid = None
                uuid_to_cleanup_on_failure = None
    
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                # CRITICAL: advisory lock per user для защиты от race conditions
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    telegram_id
                )
                
                # STEP 1: Проверяем и списываем баланс (SELECT FOR UPDATE для блокировки строки)
                row = await conn.fetchrow(
                    "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
                    telegram_id
                )
                
                if not row:
                    raise ValueError(f"User {telegram_id} not found")
                
                current_balance = row["balance"]
                
                if current_balance < amount_kopecks:
                    raise ValueError(
                        f"Insufficient balance: {current_balance} < {amount_kopecks} "
                        f"(user={telegram_id}, required={amount_rubles:.2f} RUB)"
                    )
                
                # Списываем баланс
                new_balance = current_balance - amount_kopecks
                await conn.execute(
                    "UPDATE users SET balance = $1 WHERE telegram_id = $2",
                    new_balance, telegram_id
                )
                
                # Записываем транзакцию баланса
                transaction_description = description or f"Оплата подписки {tariff_type} на {period_days} дней"
                await conn.execute(
                    """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                       VALUES ($1, $2, $3, $4, $5)""",
                    telegram_id, -amount_kopecks, "subscription_payment", "subscription_payment", transaction_description
                )
                
                # STEP 1.5: Потребляем промокод (если был использован) - atomic UPDATE ... RETURNING
                if promo_code:
                    from database.subscriptions import _consume_promo_in_transaction
                    await _consume_promo_in_transaction(conn, promo_code, telegram_id, None)
                
                # STEP 2: Активируем подписку
                grant_result_for_removal = grant_result = await grant_access(
                    telegram_id=telegram_id,
                    duration=duration,
                    source="payment",
                    admin_telegram_id=None,
                    admin_grant_days=None,
                    conn=conn,
                    pre_provisioned_uuid=pre_provisioned_uuid,
                    _caller_holds_transaction=True,
                    tariff=tariff_type or "basic",
                    country=country,
                )

                expires_at = grant_result["subscription_end"]
                vpn_key = grant_result.get("vless_url") or grant_result.get("vpn_key") or ""
                action = grant_result.get("action")
                is_renewal = action == "renewal"
                
                # expires_at is ALWAYS required (for both new and renewal)
                if not expires_at:
                    raise ValueError(
                        f"grant_access returned invalid result: expires_at={expires_at}"
                    )
                
                # vpn_key is required ONLY for new subscriptions (not for renewals)
                if action != "renewal" and not vpn_key:
                    raise ValueError(
                        "grant_access returned invalid result for NEW subscription: vpn_key is missing"
                    )
                
                # STEP 3: Создаем запись о платеже.
                # payment_provider='balance' — это внутреннее движение уже
                # учтённых денег, а не новая выручка: рубли попали в отчёты
                # ещё при пополнении баланса. Без пометки строка неотличима
                # от прямой оплаты картой (tariff тот же), и одни и те же
                # деньги считались дважды. См. миграцию 072.
                payment_id = await conn.fetchval(
                    """INSERT INTO payments (telegram_id, tariff, amount, status, payment_provider)
                       VALUES ($1, $2, $3, 'approved', 'balance') RETURNING id""",
                    telegram_id, f"{tariff_type}_{period_days}", amount_kopecks
                )
                
                if not payment_id:
                    raise ValueError(f"Failed to create payment record for user {telegram_id}")
                
                # STEP 4: Обрабатываем реферальный кешбэк
                purchase_id = f"balance_purchase_{payment_id}"
                referral_reward_result = None
                
                try:
                    referral_reward_result = await process_referral_reward(
                        buyer_id=telegram_id,
                        purchase_id=purchase_id,
                        amount_rubles=amount_rubles,
                        conn=conn
                    )
                except Exception as e:
                    # FINANCIAL errors propagate and rollback transaction
                    logger.error(
                        f"finalize_balance_purchase: Referral reward financial error "
                        f"(transaction will rollback): user={telegram_id}, purchase_id={purchase_id}, error={e}"
                    )
                    raise
                
                # STEP 5: Получаем новый баланс
                new_balance_kopecks = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
                )
                new_balance = (new_balance_kopecks or 0) / 100.0
                
                logger.info(
                    f"finalize_balance_purchase: SUCCESS [user={telegram_id}, payment_id={payment_id}, "
                    f"tariff={tariff_type}, period={period_days}, amount={amount_rubles:.2f} RUB, "
                    f"expires_at={expires_at.isoformat()}, is_renewal={is_renewal}, "
                    f"new_balance={new_balance:.2f} RUB, referral_reward_success={referral_reward_result.get('success') if referral_reward_result else False}]"
                )
                
                subscription_type_ret = (grant_result.get("subscription_type") or "basic").strip().lower()
                if subscription_type_ret not in config.VALID_SUBSCRIPTION_TYPES:
                    subscription_type_ret = "basic"
                vpn_key_plus_ret = grant_result.get("vpn_key_plus") or grant_result.get("vless_url_plus")
                ret_val = {
                    "success": True,
                    "payment_id": payment_id,
                    "expires_at": expires_at,
                    "vpn_key": vpn_key,
                    "vpn_key_plus": vpn_key_plus_ret,
                    "is_renewal": is_renewal,
                    "subscription_type": subscription_type_ret,
                    "new_balance": new_balance,
                    "referral_reward": referral_reward_result,
                    "is_basic_to_plus_upgrade": grant_result.get("is_basic_to_plus_upgrade", False),
                }
        except Exception as e:
            if uuid_to_cleanup_on_failure:
                try:
                    await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                    uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                    logger.critical(
                        f"ORPHAN_PREVENTED uuid={uuid_preview} reason=finalize_balance_purchase_tx_failed "
                        f"user={telegram_id} error={e}"
                    )
                except Exception as remove_err:
                    uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                    logger.critical(
                        f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} user={telegram_id}"
                    )
            raise
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("old_uuid_to_remove_after_commit"):
            old_uuid = grant_result_for_removal["old_uuid_to_remove_after_commit"]
            try:
                await vpn_utils.safe_remove_vless_user_with_retry(old_uuid)
                logger.info("OLD_UUID_REMOVED_AFTER_COMMIT", extra={"uuid": old_uuid[:8] + "..."})
            except Exception as rem_err:
                logger.critical(
                    "OLD_UUID_REMOVAL_FAILED_POST_COMMIT",
                    extra={"uuid": old_uuid[:8] + "...", "error": str(rem_err)[:200]}
                )
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("renewal_panel_sync_after_commit"):
            sync_info = grant_result_for_removal["renewal_panel_sync_after_commit"]
            try:
                from app.services import purchase_flow
                await purchase_flow.sync_renewal_to_remnawave(sync_info)
            except Exception as e:
                logger.critical(
                    "RENEWAL_REMNAWAVE_SYNC_FAILED",
                    extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
                )
        return ret_val

async def finalize_balance_topup(
    telegram_id: int,
    amount_rubles: float,
    provider: str,
    provider_charge_id: str,
    description: Optional[str] = None,
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Атомарно обработать пополнение баланса с идемпотентностью.
    
    КРИТИЧЕСКИ ВАЖНО: Эта функция идемпотентна по provider_charge_id.
    Повторный вызов с тем же provider_charge_id НЕ увеличит баланс.
    
    Выполняет в одной транзакции:
    - Проверяет идемпотентность (по provider_charge_id)
    - Пополняет баланс (если не дубликат)
    - Создает запись о платеже
    - Обрабатывает реферальный кешбэк
    
    Args:
        telegram_id: Telegram ID пользователя
        amount_rubles: Сумма пополнения в рублях
        provider: Провайдер платежа ('telegram', 'platega', 'telegram_stars')
        provider_charge_id: Уникальный ID платежа от провайдера (для идемпотентности)
        description: Описание платежа (опционально)
        correlation_id: ID для корреляции логов (опционально)
    
    Returns:
        {
            "success": bool,
            "payment_id": Optional[int],
            "new_balance": float,
            "referral_reward": Optional[Dict[str, Any]],
            "reason": Optional[str]  # "already_processed" if duplicate
        }
    
    Raises:
        ValueError: При некорректной сумме или отсутствии provider_charge_id
        asyncpg exceptions: При финансовых ошибках (откат транзакции)
    """
    from database.users import process_referral_reward

    if amount_rubles <= 0:
        raise ValueError(f"Invalid amount for balance topup: {amount_rubles}")

    if not provider_charge_id:
        raise ValueError("provider_charge_id is required for idempotency")
    
    if provider not in ("telegram", "cryptobot", "platega", "crypto2328", "telegram_stars"):
        raise ValueError(f"Invalid provider: {provider}. Must be 'telegram', 'platega', or 'telegram_stars'")
    
    amount_kopecks = round(amount_rubles * 100)
    pool = await get_pool()
    
    if pool is None:
        raise RuntimeError("Database pool is not available")
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # STEP 1: SCHEMA SAFETY CHECK (P0 HOTFIX - prevent silent failures)
            # Defensive check: ensure idempotency columns exist before querying
            provider_column_map = {
                'telegram': 'telegram_payment_charge_id',
                'cryptobot': 'cryptobot_payment_id',
                'platega': 'platega_payment_id',
                'crypto2328': 'crypto2328_payment_id',
            }
            idempotency_column = provider_column_map[provider]
            column_exists = await conn.fetchval(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'payments'
                  AND column_name = $1
                """,
                idempotency_column
            )

            if not column_exists:
                error_msg = (
                    f"CRITICAL_SCHEMA_MISMATCH: payments.{idempotency_column} "
                    f"column missing. Migration may not have been applied correctly. "
                    f"Provider: {provider}, provider_charge_id: {provider_charge_id}"
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            # STEP 2: IDEMPOTENCY CHECK (CRITICAL - at the very start)
            existing_payment = await conn.fetchrow(
                """
                SELECT id, telegram_id, amount, status
                FROM payments
                WHERE telegram_payment_charge_id = $1
                   OR cryptobot_payment_id = $1
                   OR platega_payment_id = $1
                   OR crypto2328_payment_id = $1
                """,
                provider_charge_id
            )
            
            if existing_payment:
                logger.warning(
                    f"BALANCE_TOPUP_DUPLICATE_SKIPPED [provider={provider}, "
                    f"provider_charge_id={provider_charge_id}, telegram_id={telegram_id}, "
                    f"correlation_id={correlation_id}, existing_payment_id={existing_payment['id']}]"
                )
                # Return existing payment info without modifying balance
                existing_balance_kopecks = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
                )
                existing_balance = (existing_balance_kopecks or 0) / 100.0
                
                return {
                    "success": False,
                    "payment_id": existing_payment["id"],
                    "new_balance": existing_balance,
                    "referral_reward": None,
                    "reason": "already_processed"
                }
            
            # STEP 3: Проверяем существование пользователя
            user_exists = await conn.fetchval(
                "SELECT telegram_id FROM users WHERE telegram_id = $1", telegram_id
            )
            
            if user_exists is None:
                raise ValueError(f"User {telegram_id} not found")
            
            # STEP 4: ATOMIC INSERT + CREDIT (payment record FIRST, then balance)
            # Insert payment record with idempotency key
            payment_id = await conn.fetchval(
                """
                INSERT INTO payments (
                    telegram_id,
                    tariff,
                    amount,
                    status,
                    telegram_payment_charge_id,
                    cryptobot_payment_id,
                    platega_payment_id,
                    crypto2328_payment_id
                )
                VALUES (
                    $1, $2, $3, 'approved',
                    CASE WHEN $4 = 'telegram' THEN $5 ELSE NULL END,
                    CASE WHEN $4 = 'cryptobot' THEN $5 ELSE NULL END,
                    CASE WHEN $4 = 'platega' THEN $5 ELSE NULL END,
                    CASE WHEN $4 = 'crypto2328' THEN $5 ELSE NULL END
                )
                RETURNING id
                """,
                telegram_id,
                "balance_topup",
                amount_kopecks,
                provider,
                provider_charge_id
            )
            
            if not payment_id:
                raise ValueError(f"Failed to create payment record for user {telegram_id}")
            
            # STEP 5: Пополняем баланс (AFTER payment record created)
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                amount_kopecks, telegram_id
            )
            
            # STEP 6: Записываем транзакцию баланса
            transaction_description = description or f"Пополнение баланса через {provider}"
            transaction_type = "topup"
            await conn.execute(
                """INSERT INTO balance_transactions (user_id, amount, type, source, description)
                   VALUES ($1, $2, $3, $4, $5)""",
                telegram_id, amount_kopecks, transaction_type, provider, transaction_description
            )
            
            # STEP 7: Обрабатываем реферальный кешбэк
            purchase_id = f"balance_topup_{payment_id}"
            referral_reward_result = None
            
            try:
                referral_reward_result = await process_referral_reward(
                    buyer_id=telegram_id,
                    purchase_id=purchase_id,
                    amount_rubles=amount_rubles,
                    conn=conn
                )
            except Exception as e:
                # FINANCIAL errors propagate and rollback transaction
                logger.error(
                    f"finalize_balance_topup: Referral reward financial error "
                    f"(transaction will rollback): user={telegram_id}, purchase_id={purchase_id}, error={e}"
                )
                raise
            
            # STEP 8: Получаем новый баланс
            new_balance_kopecks = await conn.fetchval(
                "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
            )
            new_balance = (new_balance_kopecks or 0) / 100.0
            
            logger.info(
                f"BALANCE_TOPUP_SUCCESS [user={telegram_id}, payment_id={payment_id}, "
                f"provider={provider}, provider_charge_id={provider_charge_id}, "
                f"amount={amount_rubles:.2f} RUB, new_balance={new_balance:.2f} RUB, "
                f"referral_reward_success={referral_reward_result.get('success') if referral_reward_result else False}, "
                f"correlation_id={correlation_id}]"
            )
            
            return {
                "success": True,
                "payment_id": payment_id,
                "new_balance": new_balance,
                "referral_reward": referral_reward_result
            }
