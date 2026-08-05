"""Проведение оплаченной покупки: деньги приняты — выдать товар.

ЧТО ЗДЕСЬ
    finalize_purchase          обёртка: берёт advisory-лок и снимает его
    _finalize_purchase_locked  тело под локом: вся выдача
    _publish_payment_approved  событие в шину после коммита
    PaymentAlreadyProcessed / PaymentAmountMismatch /
    PurchaseLocked / PurchaseInvalidStatus — доменные исключения, по ним
    сервисный слой отличает «повторный вебхук» от «денег не сходится»

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ
    Это единственная точка входа платёжных вебхуков. Сюда приходят карты,
    крипта, звёзды — и отсюда выдаётся всё: подписка, пополнение баланса,
    подарок, пакет трафика, Apple ID, плёнка на ферме. Правят его по
    поводам платёжных провайдеров, а не по поводам подписок, поэтому он
    больше не лежит вместе с grant_access.

ЛОК И ЕГО ГРАНИЦЫ
    Раньше защита от параллельной финализации была фиктивной: SELECT ...
    FOR UPDATE SKIP LOCKED выполнялся вне транзакции, соединение было в
    autocommit, и блокировка снималась сразу. Два одновременных вебхука
    проходили дальше оба.

    Обернуть всю функцию в транзакцию нельзя: ниже идёт создание сущности
    в панели по HTTP, и держать транзакцию на время сетевого запроса —
    прямой путь к исчерпанию пула. Поэтому берётся сессионный
    pg_try_advisory_lock по purchase_id: он живёт до явного освобождения и
    снимается в finally. Тело вынесено отдельной функцией именно ради
    этого finally — лок обязан сниматься при любом исходе, включая
    исключение. Снимать его надо на ТОМ ЖЕ соединении, на котором брали,
    поэтому conn передаётся внутрь параметром.

ДВЕ ФАЗЫ И СИРОТЫ
    Фаза 1 — провижининг в панели ДО транзакции. Фаза 2 — транзакция.
    Если фаза 2 падает, созданная сущность остаётся сиротой, и обработчик
    в except удаляет её через remnawave_api.delete_user. Заменишь удаление
    на лог — человек не заплатит, а доступ у него будет.

ЧТО ЛЕГКО СЛОМАТЬ
    Неизвестный purchase_type обязан падать громко. Раньше такой платёж
    молча доходил до конца, ничего не выдав, и выглядел успешным.

    Реферальный кешбэк считается в ТОЙ ЖЕ транзакции: финансовая ошибка
    откатывает всю покупку, бизнес-отказ (нет реферера) — нет. Вынести
    его наружу значит начислять кешбэк за откатившиеся платежи.

    Тесты (tests/integration/test_vpn_entitlement.py) подменяют get_pool и
    grant_access КАК АТРИБУТЫ ЭТОГО МОДУЛЯ. Патч по имени фасада
    database.subscriptions не подействует.
"""
import logging
# timedelta намеренно не тут: обе функции ниже делают `from datetime import
# timedelta` в своём теле, и локальное имя всё равно затенит модульное.
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import config
import vpn_utils
from database.core import (
    get_pool,
    _to_db_utc,
    _from_db_utc,
    _generate_subscription_uuid,
)
from database.promo import _consume_promo_in_transaction
from database.subscription_grant import grant_access
from database.subscription_state import set_combo_flag, set_bypass_only_flag

logger = logging.getLogger(__name__)


class PaymentAlreadyProcessed(ValueError):
    """Покупка уже финализирована — законный повтор вебхука от провайдера."""


class PaymentAmountMismatch(ValueError):
    """Оплаченная сумма не совпала с ценой покупки за пределами допуска."""


class PurchaseLocked(ValueError):
    """Покупка не найдена или заблокирована параллельной финализацией."""


class PurchaseInvalidStatus(ValueError):
    """Покупка в статусе, из которого её нельзя финализировать."""


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
        # Защита от параллельной финализации одной покупки.
        #
        # Раньше здесь стоял SELECT ... FOR UPDATE SKIP LOCKED, но выполнялся он
        # вне транзакции: соединение в режиме autocommit, поэтому блокировка
        # строки снималась сразу после запроса. Комментарий обещал «только один
        # вебхук обрабатывает покупку», а на деле два одновременных вебхука
        # проходили дальше оба.
        #
        # Обернуть всю функцию в транзакцию нельзя: ниже идёт создание сущности
        # в панели (внешний HTTP-вызов), и держать транзакцию открытой на время
        # сетевого запроса — прямой путь к исчерпанию пула соединений.
        #
        # Поэтому берём сессионный advisory-лок по идентификатору покупки. Он
        # живёт до явного освобождения, не зависит от транзакций и снимается
        # в finally ниже. Второй вебхук на ту же покупку не получит лок и
        # завершится с PurchaseLocked — ровно то поведение, которое ожидалось.
        got_lock = await conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1))", purchase_id
        )
        if not got_lock:
            error_msg = f"Pending purchase is being finalized concurrently: purchase_id={purchase_id}"
            logger.warning(f"finalize_purchase: payment_rejected: reason=concurrent_finalization, {error_msg}")
            raise PurchaseLocked(error_msg)

        try:
            result = await _finalize_purchase_locked(
                conn, purchase_id, payment_provider, amount_rubles, invoice_id
            )
            _publish_payment_approved(
                result, purchase_id=purchase_id, amount_rubles=amount_rubles,
                payment_provider=payment_provider,
            )
            return result
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", purchase_id)


def _publish_payment_approved(
    result: Dict[str, Any],
    *,
    purchase_id: str,
    amount_rubles: float,
    payment_provider: str,
) -> None:
    """Сообщить шине о состоявшейся оплате — событие payment:approved.

    Кто это слушает:
      • LivePaymentTicker в дашборде — живая лента оплат;
      • app/services/admin_notifier — считает дневную выручку и шлёт админу
        push при достижении milestone.

    Почему функция появилась. Событие публиковалось ровно в одном месте —
    внутри approve_payment_atomic, ветки ручной модерации платежей, у
    которой не было ни одного вызывающего (она удалена). Реальные оплаты
    идут через finalize_purchase и в шину не писали ничего: лента в
    дашборде была пустой всегда, milestone-push не приходил никогда.

    Публикуем в обёртке, а не в теле под локом, потому что здесь транзакция
    уже закоммичена: подписчик, который полезет в базу за подробностями,
    увидит записанные данные, а не их отсутствие.

    Падение шины не должно ронять оплату — деньги уже приняты и записаны.
    """
    if not result or not result.get("success"):
        return
    try:
        from app.events import bus
        expires_at = result.get("expires_at")
        bus.publish({
            "type": "payment:approved",
            "payment_id": result.get("payment_id"),
            "telegram_id": result.get("telegram_id"),
            "purchase_id": purchase_id,
            "amount_rubles": amount_rubles,
            "provider": payment_provider,
            "tariff": result.get("tariff_type"),
            "is_renewal": bool(result.get("is_renewal")),
            "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else None,
        })
    except Exception as e:
        logger.warning("PAYMENT_APPROVED_PUBLISH_FAILED purchase_id=%s: %s", purchase_id, e)


async def _finalize_purchase_locked(
    conn,
    purchase_id: str,
    payment_provider: str,
    amount_rubles: float,
    invoice_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Тело finalize_purchase, выполняемое под advisory-локом покупки.

    Вынесено отдельной функцией, чтобы лок гарантированно освобождался в
    finally вызывающей стороны при любом исходе, включая исключение.
    Соединение передаётся снаружи: лок сессионный и должен сниматься на том
    же соединении, на котором был взят.
    """
    from datetime import timedelta

    if True:  # сохраняем исходный уровень вложенности тела функции
        pending_row = await conn.fetchrow(
            "SELECT * FROM pending_purchases WHERE purchase_id = $1",
            purchase_id
        )
        if not pending_row:
            error_msg = f"Pending purchase not found: purchase_id={purchase_id}"
            logger.error(f"finalize_purchase: payment_rejected: reason=purchase_not_found, {error_msg}")
            raise PurchaseLocked(error_msg)
        pending_purchase = dict(pending_row)
        telegram_id = pending_purchase["telegram_id"]
        status = pending_purchase.get("status")
        promo_code = pending_purchase.get("promo_code")
        if status == "paid":
            error_msg = f"Pending purchase already processed: purchase_id={purchase_id}, status={status}"
            logger.warning(f"finalize_purchase: payment_rejected: reason=already_processed, {error_msg}")
            raise PaymentAlreadyProcessed(error_msg)
        if status not in ("pending", "expired"):
            error_msg = f"Pending purchase invalid status: purchase_id={purchase_id}, status={status}"
            logger.warning(f"finalize_purchase: payment_rejected: reason=invalid_status, {error_msg}")
            raise PurchaseInvalidStatus(error_msg)
        if status == "expired":
            logger.info(f"finalize_purchase: recovering expired purchase: purchase_id={purchase_id}, user={telegram_id}")
        tariff_type = pending_purchase.get("tariff")
        period_days = pending_purchase.get("period_days")
        purchase_type = pending_purchase.get("purchase_type", "subscription")
        price_kopecks = pending_purchase["price_kopecks"]
        purchase_country = pending_purchase.get("country")
        is_combo_purchase = pending_purchase.get("is_combo", False)
        expected_amount_rubles = price_kopecks / 100.0
        # Пополнение баланса — только по явному типу.
        #
        # Раньше здесь стояла ещё и эвристика «period_days == 0 и типа нет
        # в списке исключений». Список был неполон: steam и proxy в нём
        # отсутствовали, а создаются они ровно с period_days = 0. То есть
        # оплата Steam на пять тысяч могла быть зачислена человеку на
        # внутренний баланс, а Steam бы не пополнился — товар при этом
        # выглядел оплаченным. Не срабатывало это лишь потому, что оба
        # типа перехватывались раньше по дороге, в другом модуле.
        #
        # Полный список неподписочных типов — config.NON_SUBSCRIPTION_PURCHASE_TYPES.
        is_balance_topup = (purchase_type == "balance_topup")
        is_gift_purchase = (purchase_type == "gift")
        is_traffic_pack = (purchase_type == "traffic_pack")
        is_apple_id = (purchase_type == "apple_id")
        is_farm_effect = (purchase_type == "farm_effect")

        # Типы, которые сюда приходить не должны: Steam, Spotify, прокси,
        # Telegram Premium и звёзды выдаются вручную или через отдельный
        # путь, и своей ветки в этой функции у них нет. Если такой тип всё
        # же дошёл, продолжать нельзя: покупка молча дойдёт до конца, не
        # выдав ничего, и будет выглядеть успешной.
        #
        # Падаем громко — тип покупки известен, значит и путь для него надо
        # добавить осознанно, а не обнаружить потом по жалобе покупателя.
        _handled_here = ("balance_topup", "gift", "traffic_pack", "apple_id", "farm_effect")
        if purchase_type in config.NON_SUBSCRIPTION_PURCHASE_TYPES and purchase_type not in _handled_here:
            error_msg = (
                f"finalize_purchase не умеет выдавать purchase_type={purchase_type!r}: "
                f"purchase_id={purchase_id}, user={telegram_id}. Такие покупки "
                f"обрабатываются отдельно (app/services/payments/confirmation.py)."
            )
            logger.error(f"finalize_purchase: UNSUPPORTED_PURCHASE_TYPE: {error_msg}")
            raise ValueError(error_msg)
        amount_diff = abs(amount_rubles - expected_amount_rubles)
        # Допуск общий для всего проекта — config.payment_amount_tolerance.
        # Здесь стояла своя копия той же формулы, а в сервисном слое —
        # фиксированный ±1 ₽; из-за расхождения платёж мог пройти одну
        # проверку и упасть на второй.
        max_tolerance = config.payment_amount_tolerance(expected_amount_rubles)
        if amount_diff > max_tolerance:
            error_msg = (
                f"Payment amount mismatch: purchase_id={purchase_id}, user={telegram_id}, "
                f"expected={expected_amount_rubles:.2f} RUB, actual={amount_rubles:.2f} RUB, "
                f"diff={amount_diff:.2f} RUB (tolerance={max_tolerance:.2f} RUB)"
            )
            logger.error(f"finalize_purchase: PAYMENT_AMOUNT_MISMATCH: {error_msg}")
            raise PaymentAmountMismatch(error_msg)

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
            if is_new_issuance:
                try:
                    duration_pre = timedelta(days=period_days)
                    subscription_end_pre = now_pre + duration_pre
                    new_uuid_pre = _generate_subscription_uuid()
                    # Task 2 cut-over: always provision premium + bypass
                    # entities in Remnawave; samopis xray master is no
                    # longer called from the create path.  Return shape
                    # matches the historical add_vless_user contract so
                    # the rest of finalize_purchase (uuid_to_cleanup_on_failure,
                    # pre_provisioned_uuid dict) is unchanged.
                    from app.services import purchase_flow
                    vless_result = await purchase_flow.provision_subscription(
                        telegram_id,
                        tariff=tariff_type or "basic",
                        subscription_end=subscription_end_pre,
                        period_days=period_days,
                        is_trial=False,  # finalize_purchase is paid flow only
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

                # STEP 3: Обновляем pending_purchase → paid + payment_provider
                # payment_provider added for analytics (migration 054). The
                # column may be missing on freshly-deployed-but-not-migrated
                # boxes; we ignore the error in that case so the payment
                # still settles.
                try:
                    result = await conn.execute(
                        """UPDATE pending_purchases
                           SET status = 'paid', payment_provider = $2
                           WHERE purchase_id = $1
                             AND status IN ('pending', 'expired')""",
                        purchase_id, payment_provider,
                    )
                except Exception as e:
                    logger.warning(
                        "finalize_purchase: provider write skipped (%s) — "
                        "falling back to status-only update", e,
                    )
                    result = await conn.execute(
                        "UPDATE pending_purchases SET status = 'paid' WHERE purchase_id = $1 AND status IN ('pending', 'expired')",
                        purchase_id,
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
                        """INSERT INTO payments (telegram_id, tariff, amount, status, paid_at, payment_provider)
                           VALUES ($1, $2, $3, 'approved', $4, $5) RETURNING id""",
                        telegram_id,
                        "balance_topup",
                        round(amount_rubles * 100),
                        _to_db_utc(now_utc),
                        payment_provider
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
                        "telegram_id": telegram_id,
                        "expires_at": None,
                        "vpn_key": None,
                        "is_renewal": False,
                        "is_balance_topup": True,
                        "amount": amount_rubles,
                        "referral_reward": referral_reward_result
                    }

                # STEP 4.4b: ОБРАБОТКА ПОКУПКИ APPLE ID
                if is_apple_id:
                    logger.info(
                        f"finalize_purchase: APPLE_ID [purchase_id={purchase_id}, user={telegram_id}, "
                        f"tariff={tariff_type}, amount={amount_rubles:.2f} RUB]"
                    )
                    now_utc = datetime.now(timezone.utc)
                    payment_id = await conn.fetchval(
                        """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id, paid_at, payment_provider)
                           VALUES ($1, $2, $3, 'approved', $4, $5, $6) RETURNING id""",
                        telegram_id, tariff_type, round(amount_rubles * 100),
                        purchase_id, _to_db_utc(now_utc),
                        payment_provider,
                    )
                    return {
                        "success": True,
                        "payment_id": payment_id,
                        "telegram_id": telegram_id,
                        "expires_at": None,
                        "vpn_key": None,
                        "is_renewal": False,
                        "is_balance_topup": False,
                        "is_traffic_pack": False,
                        "tariff_type": tariff_type,
                        "price_kopecks": price_kopecks,
                        "amount": amount_rubles,
                    }

                # STEP 4.5: ОБРАБОТКА ПОДАРОЧНОЙ ПОДПИСКИ (gift)
                if is_gift_purchase:
                    logger.info(
                        f"finalize_purchase: GIFT_PURCHASE [purchase_id={purchase_id}, user={telegram_id}, "
                        f"tariff={tariff_type}, period_days={period_days}, amount={amount_rubles:.2f} RUB]"
                    )
                    now_utc = datetime.now(timezone.utc)
                    payment_id = await conn.fetchval(
                        """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id, paid_at, payment_provider)
                           VALUES ($1, $2, $3, 'approved', $4, $5, $6) RETURNING id""",
                        telegram_id,
                        f"gift_{tariff_type}_{period_days}",
                        round(amount_rubles * 100),
                        purchase_id,
                        _to_db_utc(now_utc),
                        payment_provider,
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

                    # Код подарка — предъявительский токен на оплаченную
                    # подписку: кто прочитал лог, тот её и активирует. Пишем
                    # маску, цепочка собирается по purchase_id.
                    from app.utils.security import mask_secret
                    logger.info(
                        f"finalize_purchase: GIFT_CREATED [purchase_id={purchase_id}, user={telegram_id}, "
                        f"gift_code={mask_secret(gift_code)}, tariff={tariff_type}, period={period_days}d]"
                    )
                    return {
                        "success": True,
                        "payment_id": payment_id,
                        "telegram_id": telegram_id,
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
                        """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id, paid_at, payment_provider)
                           VALUES ($1, $2, $3, 'approved', $4, $5, $6) RETURNING id""",
                        telegram_id,
                        tariff_type or "traffic_pack",
                        round(amount_rubles * 100),
                        purchase_id,
                        _to_db_utc(now_utc),
                        payment_provider,
                    )
                    if not payment_id:
                        raise Exception(f"Failed to create payment record for traffic pack: purchase_id={purchase_id}")

                    # Extract GB amount from tariff field (e.g., "traffic_5gb" → 5, "bypass_10gb" → 10)
                    _gb = 0
                    if tariff_type and tariff_type.endswith("gb") and ("traffic_" in tariff_type or "bypass_" in tariff_type):
                        _prefix = "bypass_" if tariff_type.startswith("bypass_") else "traffic_"
                        try:
                            _gb = int(tariff_type[len(_prefix):-len("gb")])
                        except ValueError:
                            logger.error(
                                "finalize_purchase: TRAFFIC_PACK_GB_PARSE_FAIL tariff=%s purchase_id=%s",
                                tariff_type, purchase_id,
                            )
                    if _gb <= 0:
                        logger.error(
                            "finalize_purchase: TRAFFIC_PACK_INVALID_GB gb=%s tariff=%s purchase_id=%s",
                            _gb, tariff_type, purchase_id,
                        )

                    # Record in traffic_purchases (payment_method column may not exist yet)
                    _payment_method = payment_provider or "card"
                    _has_pm_col = await conn.fetchval(
                        """SELECT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'traffic_purchases' AND column_name = 'payment_method'
                        )"""
                    )
                    if _has_pm_col:
                        await conn.execute(
                            """INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub, payment_method, created_at)
                               VALUES ($1, $2, $3, $4, $5)""",
                            telegram_id, _gb, round(amount_rubles), _payment_method, _to_db_utc(now_utc),
                        )
                    else:
                        await conn.execute(
                            """INSERT INTO traffic_purchases (telegram_id, gb_amount, price_rub, created_at)
                               VALUES ($1, $2, $3, $4)""",
                            telegram_id, _gb, round(amount_rubles), _to_db_utc(now_utc),
                        )

                    logger.info(
                        f"finalize_purchase: TRAFFIC_PACK_DONE [purchase_id={purchase_id}, user={telegram_id}, "
                        f"payment_id={payment_id}, gb={_gb}, method={_payment_method}]"
                    )
                    return {
                        "success": True,
                        "payment_id": payment_id,
                        "telegram_id": telegram_id,
                        "expires_at": None,
                        "vpn_key": None,
                        "is_renewal": False,
                        "is_traffic_pack": True,
                        "traffic_gb": _gb,
                        "tariff_type": tariff_type,
                    }

                # STEP 4.7: ОБРАБОТКА ПОКУПКИ ЭФФЕКТА ФЕРМЫ (farm_storm_shield)
                if is_farm_effect:
                    plot_id = pending_purchase.get("farm_plot_id")
                    logger.info(
                        f"finalize_purchase: FARM_EFFECT [purchase_id={purchase_id}, user={telegram_id}, "
                        f"tariff={tariff_type}, plot_id={plot_id}, amount={amount_rubles:.2f} RUB]"
                    )
                    if plot_id is None:
                        raise ValueError(
                            f"farm_effect purchase {purchase_id} has no farm_plot_id"
                        )
                    now_utc = datetime.now(timezone.utc)
                    payment_id = await conn.fetchval(
                        """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id, paid_at, payment_provider)
                           VALUES ($1, $2, $3, 'approved', $4, $5, $6) RETURNING id""",
                        telegram_id,
                        tariff_type or "farm_storm_shield",
                        round(amount_rubles * 100),
                        purchase_id,
                        _to_db_utc(now_utc),
                        payment_provider,
                    )
                    if not payment_id:
                        raise Exception(
                            f"Failed to create payment record for farm_effect: purchase_id={purchase_id}"
                        )

                    # Apply shield WITHOUT touching balance — already paid via PSP.
                    # Pass our connection so the row update happens in the same
                    # transaction (no nested advisory lock, no deadlock risk).
                    from database.farm import apply_storm_shield_atomic
                    shield_ok, shield_reason = await apply_storm_shield_atomic(
                        telegram_id, plot_id, cost_kopecks=0, deduct_balance=False,
                        conn=conn,
                    )
                    logger.info(
                        f"finalize_purchase: FARM_EFFECT_SHIELD [purchase_id={purchase_id}, "
                        f"plot_id={plot_id}, ok={shield_ok}, reason={shield_reason}]"
                    )
                    # Плёнка не применилась — грядку успели собрать, она уже
                    # накрыта, статус больше не growing или шторм уже прошёл.
                    #
                    # Раньше платёж просто оставался записанным, а «логика
                    # возврата» существовала только в комментарии: в коде её не
                    # было. Человек платил через СБП за 10 минут до удара, шторм
                    # успевал сработать, деньги списывались, плёнки не было, а в
                    # ответ приходил успех. Дальше — поддержка.
                    #
                    # Компенсируем автоматически: зачисляем уплаченное на баланс
                    # в этой же транзакции. Возврат через PSP невозможен изнутри
                    # webhook'а, а баланс тратится на что угодно внутри бота и
                    # выводится (деньги реальные, не игровые — см.
                    # database/users.py:GAME_EARNING_SOURCES).
                    refunded_kopecks = 0
                    if not shield_ok:
                        refunded_kopecks = round(amount_rubles * 100)
                        if refunded_kopecks > 0:
                            await conn.execute(
                                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                                refunded_kopecks, telegram_id,
                            )
                            await conn.execute(
                                """INSERT INTO balance_transactions
                                       (user_id, amount, type, source, description)
                                   VALUES ($1, $2, 'refund', 'farm_shield_refund', $3)""",
                                telegram_id, refunded_kopecks,
                                f"Плёнка не применилась ({shield_reason}) — возврат на баланс",
                            )
                        logger.warning(
                            "finalize_purchase: FARM_SHIELD_REFUNDED [purchase_id=%s, user=%s, "
                            "plot_id=%s, reason=%s, refunded_kopecks=%s]",
                            purchase_id, telegram_id, plot_id, shield_reason, refunded_kopecks,
                        )

                    return {
                        "success": True,
                        "payment_id": payment_id,
                        "telegram_id": telegram_id,
                        "expires_at": None,
                        "vpn_key": None,
                        "is_renewal": False,
                        "is_farm_effect": True,
                        "farm_plot_id": plot_id,
                        "farm_shield_applied": shield_ok,
                        "farm_shield_reason": shield_reason,
                        # Копейки, вернувшиеся на баланс. Вызывающий код обязан
                        # сказать об этом пользователю — иначе он увидит только
                        # списание и пойдёт в поддержку.
                        "farm_shield_refund_kopecks": refunded_kopecks,
                        "tariff_type": tariff_type,
                    }

                # STEP 5: ОБРАБОТКА ПОДПИСКИ (subscription only)
                if tariff_type is None or period_days is None or period_days <= 0:
                    error_msg = f"Invalid subscription purchase: tariff={tariff_type}, period_days={period_days}"
                    logger.error(f"finalize_purchase: {error_msg}")
                    raise ValueError(error_msg)
                now_utc = datetime.now(timezone.utc)
                payment_id = await conn.fetchval(
                    """INSERT INTO payments (telegram_id, tariff, amount, status, purchase_id,
                                             cryptobot_payment_id, paid_at, payment_provider)
                       VALUES ($1, $2, $3, 'pending', $4, $5, $6, $7) RETURNING id""",
                    telegram_id,
                    f"{tariff_type}_{period_days}",
                    round(amount_rubles * 100),
                    purchase_id,
                    str(invoice_id) if invoice_id else None,
                    _to_db_utc(now_utc),
                    payment_provider,
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
                        "telegram_id": telegram_id,
                        "expires_at": expires_at,
                        "vpn_key": None,
                        "activation_status": "pending",
                        "is_renewal": False,
                        "is_combo": is_combo_purchase,
                        "period_days": period_days,
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
                        "telegram_id": telegram_id,
                        "expires_at": expires_at,
                        "vpn_key": vpn_key,
                        "vpn_key_plus": vpn_key_plus_ret,
                        "is_renewal": is_renewal,
                        "subscription_type": subscription_type_ret,
                        "referral_reward": referral_reward_result,
                        "is_basic_to_plus_upgrade": grant_result.get("is_basic_to_plus_upgrade", False),
                        "is_combo": is_combo_purchase,
                        "period_days": period_days,
                    }
        except Exception as tx_err:
            # Фаза 2 упала — сущность из фазы 1 осталась сиротой в панели.
            #
            # Раньше здесь вызывался vpn_utils.safe_remove_vless_user_with_retry.
            # После снятия samopis xray это заглушка: компенсация ничего не
            # удаляла, лог рапортовал ORPHAN_PREVENTED, а сущность продолжала
            # жить в панели. Пользователь не платил, но доступ у него был.
            #
            # Удаляем через API панели — тот же способ, что в перевыпуске ключа.
            if uuid_to_cleanup_on_failure:
                uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                try:
                    from app.services import remnawave_api
                    await remnawave_api.delete_user(uuid_to_cleanup_on_failure)
                    logger.critical(
                        f"ORPHAN_PREVENTED uuid={uuid_preview} reason=phase2_failed "
                        f"purchase_id={purchase_id} user={telegram_id} error={tx_err}"
                    )
                except Exception as remove_err:
                    logger.critical(
                        f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} "
                        f"purchase_id={purchase_id} user={telegram_id} — удалите сущность "
                        f"в панели вручную"
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
        if ret_val is not None and grant_result_for_removal and grant_result_for_removal.get("renewal_panel_sync_after_commit"):
            sync_info = grant_result_for_removal["renewal_panel_sync_after_commit"]
            try:
                from app.services import purchase_flow
                await purchase_flow.sync_renewal_to_remnawave(sync_info)
            except Exception as e:
                logger.critical(
                    "RENEWAL_REMNAWAVE_SYNC_FAILED — webhook will return 5xx for retry",
                    extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
                )
                ret_val["remnawave_sync_failed"] = True
                ret_val["remnawave_sync_error"] = str(e)[:200]
        if ret_val is not None:
            # Set or clear combo flag reliably from pending_purchase data (not FSM)
            try:
                await set_combo_flag(telegram_id, is_combo_purchase)
                logger.info(f"finalize_purchase: COMBO_FLAG_SET user={telegram_id} is_combo={is_combo_purchase}")
            except Exception as cf_err:
                logger.warning(f"finalize_purchase: COMBO_FLAG_FAIL user={telegram_id}: {cf_err}")
            # Clear bypass-only flag — user now has a real subscription
            try:
                await set_bypass_only_flag(telegram_id, False)
            except Exception:
                pass
            return ret_val
