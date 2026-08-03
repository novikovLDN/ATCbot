"""Начисление реферального кешбэка — единственное место, где рефералка
трогает деньги.

ЧТО ЗДЕСЬ
    process_referral_reward: по факту оплаты покупателя начисляет процент
    пригласившему, пишет транзакцию баланса и историю в referral_rewards.

ПОЧЕМУ ОТДЕЛЬНО
    Это финансовая операция, и у неё другой контракт на ошибки, чем у
    остальной рефералки. Бизнес-проверки (нет реферера, самореферал, дубль)
    возвращают структуру с success=False. Сбои БД, наоборот, пробрасываются
    наружу, чтобы транзакция вызывающего откатилась целиком. Смешивать эти
    два вида ошибок нельзя: проглоченный сбой оставит зачисленный баланс
    без записи в истории.

ЧТО ЛЕГКО СЛОМАТЬ
    Функция работает в ЧУЖОЙ транзакции — conn приходит снаружи, своей
    транзакции она не открывает. Обернуть её в собственную транзакцию значит
    разорвать атомарность с активацией подписки.

    Защита от двойного начисления двухслойная: ранняя проверка по
    (buyer_id, purchase_id) и ON CONFLICT ... DO NOTHING на вставке. Второй
    слой ловит гонку, и при INSERT 0 обязателен raise — иначе баланс уже
    пополнен, а истории начисления нет.

    pg_advisory_xact_lock по referrer_id плюс SELECT ... FOR UPDATE держат
    порядок при одновременных покупках разных рефералов одного партнёра.
"""
import logging
from typing import Any, Dict

import asyncpg

logger = logging.getLogger(__name__)


async def process_referral_reward(
    buyer_id: int,
    purchase_id: str,
    amount_rubles: float,
    conn: asyncpg.Connection
) -> Dict[str, Any]:
    """
    Начислить реферальный кешбэк рефереру при успешной активации подписки покупателя.
    
    КРИТИЧЕСКИ ВАЖНО:
    - Начисление происходит ТОЛЬКО при успешной активации подписки (source='payment')
    - НЕ начисляется при admin-grant, test-access, free-access
    - Защита от повторного начисления за один purchase_id
    - Защита от самореферала
    
    Args:
        buyer_id: Telegram ID покупателя, который оплатил подписку
        purchase_id: ID покупки (для защиты от повторного начисления). Если None - начисление происходит без защиты
        amount_rubles: Сумма оплаты в рублях
    
    Returns:
        Словарь с результатом:
        {
            "success": bool,
            "referrer_id": Optional[int],
            "percent": Optional[int],
            "reward_amount": Optional[float],
            "message": str
        }
    """
    # BUSINESS LOGIC CHECKS (return structured results, do not raise):
    try:
        # 1. Получаем реферера покупателя
        user = await conn.fetchrow(
            "SELECT referrer_id, referred_by FROM users WHERE telegram_id = $1",
            buyer_id
        )
        
        if not user:
            logger.debug(f"process_referral_reward: User {buyer_id} not found")
            return {
                "success": False,
                "referrer_id": None,
                "percent": None,
                "reward_amount": None,
                "message": "User not found",
                "reason": "user_not_found"
            }
        
        # Use referrer_id, fallback to referred_by for backward compatibility
        referrer_id = user.get("referrer_id") or user.get("referred_by")
        
        if not referrer_id:
            # Пользователь не был приглашён через реферальную программу
            logger.debug(f"process_referral_reward: User {buyer_id} has no referrer")
            return {
                "success": False,
                "referrer_id": None,
                "percent": None,
                "reward_amount": None,
                "message": "No referrer",
                "reason": "no_referrer"
            }
        
        # Log referrer resolution
        logger.info(
            f"REFERRAL_RESOLVED [buyer={buyer_id}, referrer={referrer_id}, "
            f"purchase_id={purchase_id}]"
        )
        
        # 2. ЗАЩИТА ОТ САМОРЕФЕРАЛА
        if referrer_id == buyer_id:
            logger.warning(f"process_referral_reward: Self-referral detected: user {buyer_id}")
            return {
                "success": False,
                "referrer_id": referrer_id,
                "percent": None,
                "reward_amount": None,
                "message": "Self-referral detected",
                "reason": "self_referral"
            }
        
        # 3. ЗАЩИТА ОТ ПОВТОРНОГО НАЧИСЛЕНИЯ (idempotency check)
        # purchase_id теперь обязателен, проверка всегда выполняется
        existing_reward = await conn.fetchrow(
            "SELECT id FROM referral_rewards WHERE buyer_id = $1 AND purchase_id = $2",
            buyer_id, purchase_id
        )
        
        if existing_reward:
            logger.warning(
                f"process_referral_reward: Duplicate reward attempt detected: "
                f"buyer_id={buyer_id}, purchase_id={purchase_id}"
            )
            return {
                "success": False,
                "referrer_id": referrer_id,
                "percent": None,
                "reward_amount": None,
                "message": "Reward already processed for this purchase",
                "reason": "duplicate_reward"
            }
        
        # 4. Обновляем first_paid_at в referrals, если это первый платеж реферала
        referral_row = await conn.fetchrow(
            "SELECT first_paid_at FROM referrals WHERE referrer_user_id = $1 AND referred_user_id = $2",
            referrer_id, buyer_id
        )
        
        if not referral_row:
            # Создаем запись в referrals, если её нет
            await conn.execute(
                """INSERT INTO referrals (referrer_user_id, referred_user_id, first_paid_at)
                   VALUES ($1, $2, NOW())
                   ON CONFLICT (referred_user_id) DO UPDATE
                   SET first_paid_at = COALESCE(referrals.first_paid_at, NOW())""",
                referrer_id, buyer_id
            )
        elif not referral_row.get("first_paid_at"):
            # Обновляем first_paid_at, если он еще не установлен
            await conn.execute(
                "UPDATE referrals SET first_paid_at = NOW() WHERE referrer_user_id = $1 AND referred_user_id = $2 AND first_paid_at IS NULL",
                referrer_id, buyer_id
            )
        
        # 5. Определяем процент кешбэка на основе количества оплативших рефералов
        # Считаем количество рефералов, которые ХОТЯ БЫ ОДИН РАЗ оплатили подписку
        # Используем referrals.first_paid_at как источник истины
        paid_referrals_count = await conn.fetchval(
            """SELECT COUNT(DISTINCT referred_user_id)
               FROM referrals
               WHERE referrer_user_id = $1 AND first_paid_at IS NOT NULL""",
            referrer_id
        ) or 0
        
        # Определяем процент по прогрессивной шкале «Круга Амбассадоров»
        if paid_referrals_count >= 100:
            percent = 45
        elif paid_referrals_count >= 75:
            percent = 40
        elif paid_referrals_count >= 50:
            percent = 30
        elif paid_referrals_count >= 25:
            percent = 20
        else:
            percent = 10

        # 5a. Grandfather / admin-grant floor.
        # Пользователи, попавшие под старую шкалу (Platinum=45% при 50+) при
        # миграции 059, имеют cashback_floor_percent=45 — мы не снижаем им
        # процент, даже если по новой шкале они меньше.
        floor = await conn.fetchval(
            "SELECT cashback_floor_percent FROM users WHERE telegram_id = $1",
            referrer_id,
        )
        if floor is not None and floor > percent:
            percent = floor

        # 5a-fix. ADMIN OVERRIDE: cashback_fixed_percent жёстко замещает
        # результат тира + floor. Не суммируется. Работает и в меньшую
        # сторону (напр. штраф 5%) и в большую (напр. VIP 40%).
        fixed = await conn.fetchval(
            "SELECT cashback_fixed_percent FROM users WHERE telegram_id = $1",
            referrer_id,
        )
        if fixed is not None:
            percent = int(fixed)
            logger.info(
                f"REFERRAL_CASHBACK_FIXED_OVERRIDE referrer={referrer_id} "
                f"tier_percent_would_be_after_floor=(overridden) fixed={percent}"
            )

        # Вычисляем сколько осталось до следующего уровня
        if paid_referrals_count < 25:
            next_level_threshold = 25
            referrals_needed = 25 - paid_referrals_count
        elif paid_referrals_count < 50:
            next_level_threshold = 50
            referrals_needed = 50 - paid_referrals_count
        elif paid_referrals_count < 75:
            next_level_threshold = 75
            referrals_needed = 75 - paid_referrals_count
        elif paid_referrals_count < 100:
            next_level_threshold = 100
            referrals_needed = 100 - paid_referrals_count
        else:
            next_level_threshold = None
            referrals_needed = 0
        
        # 5b. Проверяем активный множитель кешбэка (x2 промо-акция)
        # Проверяем сначала персональный множитель, затем глобальную акцию —
        # акция распространяется на ВСЕХ пользователей, не только на тех,
        # кто был подписан на момент запуска.
        cashback_multiplier = 1
        try:
            multiplier_row = await conn.fetchrow(
                """SELECT multiplier FROM user_cashback_multipliers
                   WHERE telegram_id = $1
                   AND starts_at <= NOW() AND ends_at > NOW()
                   ORDER BY multiplier DESC LIMIT 1""",
                referrer_id
            )
            if multiplier_row:
                cashback_multiplier = multiplier_row["multiplier"]
            else:
                # Fallback: проверяем глобальную акцию в cashback_promotions
                global_promo = await conn.fetchrow(
                    """SELECT multiplier FROM cashback_promotions
                       WHERE is_active = TRUE
                       AND starts_at <= NOW() AND ends_at > NOW()
                       ORDER BY multiplier DESC LIMIT 1"""
                )
                if global_promo:
                    cashback_multiplier = global_promo["multiplier"]
            if cashback_multiplier > 1:
                logger.info(
                    f"CASHBACK_MULTIPLIER_ACTIVE [referrer={referrer_id}, "
                    f"multiplier=x{cashback_multiplier}, base_percent={percent}%]"
                )
        except Exception as e:
            logger.warning(f"Failed to check cashback multiplier for {referrer_id}: {e}")

        # Применяем множитель к проценту
        effective_percent = percent * cashback_multiplier

        # 6. Рассчитываем сумму кешбэка (в копейках)
        purchase_amount_kopecks = round(amount_rubles * 100)
        reward_amount_kopecks = int(purchase_amount_kopecks * effective_percent / 100)
        reward_amount_rubles = reward_amount_kopecks / 100.0
        
        if reward_amount_kopecks <= 0:
            logger.warning(
                f"process_referral_reward: Invalid reward amount: "
                f"{reward_amount_kopecks} kopecks for payment {amount_rubles} RUB, percent={percent}%"
            )
            return {
                "success": False,
                "referrer_id": referrer_id,
                "percent": percent,
                "reward_amount": None,
                "message": "Invalid reward amount",
                "reason": "invalid_amount"
            }
        
        # FINANCIAL OPERATIONS (raise exceptions on failure, do not catch):
        # 7. Начисляем кешбэк на баланс реферера
        # CRITICAL: advisory lock per referrer для защиты от race conditions
        # Consistent with increase_balance() locking pattern — prevents concurrent
        # balance modifications from different purchases for the same referrer
        await conn.execute(
            "SELECT pg_advisory_xact_lock($1)",
            referrer_id
        )
        
        # CRITICAL: SELECT FOR UPDATE для блокировки строки до конца транзакции
        balance_row = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1 FOR UPDATE",
            referrer_id
        )
        
        if not balance_row:
            raise ValueError(f"Referrer {referrer_id} not found for reward")
        
        # Обновляем баланс (строка уже заблокирована FOR UPDATE)
        await conn.execute(
            "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
            reward_amount_kopecks, referrer_id
        )
        
        # 8. Записываем транзакцию баланса
        # Если это упадет - исключение пробросится вверх, транзакция откатится
        multiplier_note = f" (x{cashback_multiplier})" if cashback_multiplier > 1 else ""
        await conn.execute(
            """INSERT INTO balance_transactions (user_id, amount, type, source, description, related_user_id)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            referrer_id, reward_amount_kopecks, "cashback", "referral",
            f"Реферальный кешбэк {effective_percent}%{multiplier_note} за оплату пользователя {buyer_id}",
            buyer_id
        )
        
        # 9. Создаём запись в referral_rewards (история начислений)
        # SECURITY: ON CONFLICT предотвращает повторное начисление при race condition
        insert_result = await conn.execute(
            """INSERT INTO referral_rewards
               (referrer_id, buyer_id, purchase_id, purchase_amount, percent, reward_amount)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (buyer_id, purchase_id) WHERE purchase_id IS NOT NULL DO NOTHING""",
            referrer_id, buyer_id, purchase_id, purchase_amount_kopecks, effective_percent, reward_amount_kopecks
        )
        if insert_result == "INSERT 0":
            # Race condition: another concurrent transaction already inserted this reward
            logger.warning(
                f"REFERRAL_REWARD_DUPLICATE_PREVENTED: buyer_id={buyer_id}, purchase_id={purchase_id} "
                f"(concurrent insert detected, rolling back balance credit)"
            )
            raise ValueError(
                f"Duplicate referral reward prevented for buyer_id={buyer_id}, purchase_id={purchase_id}"
            )
        
        # 10. Логируем событие
        details = (
            f"Referral reward awarded: referrer={referrer_id} ({effective_percent}%"
            f"{multiplier_note}), "
            f"buyer={buyer_id}, purchase_id={purchase_id}, "
            f"purchase={amount_rubles:.2f} RUB, reward={reward_amount_rubles:.2f} RUB "
            f"({reward_amount_kopecks} kopecks), paid_referrals_count={paid_referrals_count}"
        )
        from database.subscriptions import _log_audit_event_atomic
        await _log_audit_event_atomic(
            conn,
            "referral_reward",
            referrer_id,
            buyer_id,
            details
        )
        
        logger.info(
            f"REFERRAL_REWARD_APPLIED [referrer={referrer_id}, buyer={buyer_id}, "
            f"purchase_id={purchase_id}, percent={effective_percent}%{multiplier_note}, "
            f"amount={reward_amount_rubles:.2f} RUB, paid_referrals_count={paid_referrals_count}]"
        )

        return {
            "success": True,
            "referrer_id": referrer_id,
            "percent": effective_percent,
            "reward_amount": reward_amount_rubles,
            "paid_referrals_count": paid_referrals_count,
            "next_level_threshold": next_level_threshold,
            "referrals_needed": referrals_needed,
            "message": "Reward awarded successfully"
        }
                
    except (asyncpg.UniqueViolationError, asyncpg.ForeignKeyViolationError, 
            asyncpg.NotNullViolationError, asyncpg.CheckViolationError,
            asyncpg.PostgresConnectionError, asyncpg.InterfaceError, asyncpg.TimeoutError) as e:
        # FINANCIAL ERRORS: Database constraint violations, connection issues
        # These MUST propagate to cause transaction rollback
        logger.error(
            f"process_referral_reward: Financial error (will rollback transaction): "
            f"buyer_id={buyer_id}, purchase_id={purchase_id}, error={e}"
        )
        raise  # Re-raise to cause transaction rollback
    
    except asyncpg.PostgresError as e:
        # Other database errors - also financial, must rollback
        logger.error(
            f"process_referral_reward: Database error (will rollback transaction): "
            f"buyer_id={buyer_id}, purchase_id={purchase_id}, error={e}"
        )
        raise  # Re-raise to cause transaction rollback
