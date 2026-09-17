"""
Platega recurring SBP subscriptions — DB helpers.

Таблицы:
  - platega_subscriptions          (migration 074)
  - platega_subscription_charges   (migration 074) — списания, PK = charge_id
                                   для идемпотентности webhook'а.

Чтение и update статуса подписки — fail-safe (warning log + None/[]):
UI и webhook не падают из-за DB flake.

Запись подписки и списаний (create_subscription, record_charge,
claim_charge, confirm_charge, release_charge) — НАОБОРОТ, кидают исключение
(нет БД → RuntimeError, ошибка запроса → исходное исключение). Ошибка БД здесь,
молча превращённая в «дубль», = потерянные деньги. Вызывающий
(platega_service) превращает её в TransientPaymentError → HTTP 500 →
Platega повторит callback.

Жизненный цикл оплаченного списания (CONFIRMED), колонка charges.status:
  claim_charge   → строка 'PROCESSING' (захват; счётчики подписки не трогаем)
  grant_access   → в platega_service, ВНЕ транзакции (там HTTP в Remnawave)
  confirm_charge → 'CONFIRMED' + счётчики подписки, одной транзакцией
  release_charge → DELETE строки 'PROCESSING', если выдача упала
                   (ретрай Platega пройдёт заново, а не как «дубль»)
Строка 'PROCESSING' старше stale_after_seconds (краш процесса посреди выдачи)
перезахватывается следующим ретраем. Новая миграция не нужна: status — VARCHAR(32),
старые строки 'CONFIRMED'/'CANCELED' трактуются как обработанные.

T11 (USE_NEW_PROVISIONING, entrypoint "platega_recurring"): confirm_charge(conn=…)
выполняется в транзакции вызывающего вместе с grant_access(defer_panel) и
provisioning.enqueue — окна «доступ выдан, списание не подтверждено» нет.

is_combo (migration 082) пишется только для combo-подписок и только если колонка
уже есть: код не полагается на то, что миграция применена (database/CLAUDE.md).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import database.core as _core
from database.core import get_pool

logger = logging.getLogger(__name__)

CHARGE_PROCESSING = "PROCESSING"
CHARGE_CONFIRMED = "CONFIRMED"


async def _require_pool(op: str):
    """Пул для пишущих операций: нет БД → RuntimeError, а не молчаливый skip."""
    if not _core.DB_READY:
        raise RuntimeError(f"platega_subscriptions.{op}: DB not ready")
    pool = await get_pool()
    if pool is None:
        raise RuntimeError(f"platega_subscriptions.{op}: pool is None")
    return pool


# Migration 082 adds platega_subscriptions.is_combo. Only a positive probe is
# cached: the column may appear while the process runs, and the probe runs
# only when a combo subscription is created (rare).
_IS_COMBO_COLUMN_EXISTS = False


async def _is_combo_column_exists(conn) -> bool:
    global _IS_COMBO_COLUMN_EXISTS
    if not _IS_COMBO_COLUMN_EXISTS:
        _IS_COMBO_COLUMN_EXISTS = bool(await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'platega_subscriptions'
                   AND column_name = 'is_combo'
            )
            """
        ))
    return _IS_COMBO_COLUMN_EXISTS


async def create_subscription(
    subscription_id: str,
    telegram_id: int,
    amount_kopecks: int,
    interval_days: int,
    tariff_type: str,
    description: Optional[str] = None,
    is_combo: bool = False,
) -> None:
    """Записать вновь созданную подписку (статус по умолчанию — PendingAgreement).

    Если запись с таким subscription_id уже существует — ничего не делаем
    (ON CONFLICT DO NOTHING). Callback'и SUBSCRIPTION_ACTIVATED / первое
    списание переведут её в статус Active через update_subscription_status.

    is_combo=True пишется в колонку is_combo (migration 082), если она есть;
    без колонки — прежний INSERT + error-лог (списания пойдут как не-combo).
    is_combo=False — прежний INSERT без изменений (DEFAULT FALSE).

    Raises:
        RuntimeError / ошибка asyncpg — запись не удалась. Ссылку на привязку
        юзеру отдавать нельзя: без строки в БД списания станут orphan (FK).
    """
    pool = await _require_pool("create_subscription")
    try:
        async with pool.acquire() as conn:
            with_combo = bool(is_combo) and await _is_combo_column_exists(conn)
            if is_combo and not with_combo:
                logger.error(
                    "PLATEGA_SUB_IS_COMBO_NOT_PERSISTED: id=%s tg=%s — column "
                    "platega_subscriptions.is_combo missing (migration 082 not applied); "
                    "charges of this subscription will be provisioned as non-combo",
                    subscription_id, telegram_id,
                )
            if with_combo:
                await conn.execute(
                    """
                    INSERT INTO platega_subscriptions
                        (subscription_id, telegram_id, amount_kopecks, interval_days,
                         tariff_type, description, status, created_at, updated_at, is_combo)
                    VALUES ($1, $2, $3, $4, $5, $6, 'PendingAgreement', NOW(), NOW(), TRUE)
                    ON CONFLICT (subscription_id) DO NOTHING
                    """,
                    subscription_id, int(telegram_id), int(amount_kopecks),
                    int(interval_days), tariff_type, (description or "")[:2000],
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO platega_subscriptions
                        (subscription_id, telegram_id, amount_kopecks, interval_days,
                         tariff_type, description, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, 'PendingAgreement', NOW(), NOW())
                    ON CONFLICT (subscription_id) DO NOTHING
                    """,
                    subscription_id, int(telegram_id), int(amount_kopecks),
                    int(interval_days), tariff_type, (description or "")[:2000],
                )
    except Exception as e:
        logger.error(
            "platega_subscriptions.create_subscription failed: id=%s tg=%s err=%s",
            subscription_id, telegram_id, e,
        )
        raise
    logger.info(
        "platega_sub_created: id=%s tg=%s amount_k=%s interval_d=%s tariff=%s combo=%s",
        subscription_id, telegram_id, amount_kopecks, interval_days, tariff_type, bool(is_combo),
    )


async def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
    """Вернуть подписку по её platega subscription_id или None."""
    if not _core.DB_READY:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM platega_subscriptions WHERE subscription_id = $1",
                subscription_id,
            )
            return dict(row) if row else None
    except Exception as e:
        logger.warning(
            "platega_subscriptions.get_subscription failed: id=%s err=%s",
            subscription_id, e,
        )
        return None


async def get_user_active_subscriptions(telegram_id: int) -> List[Dict[str, Any]]:
    """Список подписок юзера в состоянии Active (или PendingAgreement).

    PastDue не считаем активной для UI — юзер может создать новую.
    Возвращаем [] при ошибке.
    """
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM platega_subscriptions
                WHERE telegram_id = $1 AND status IN ('Active', 'PendingAgreement')
                ORDER BY created_at DESC
                """,
                int(telegram_id),
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(
            "platega_subscriptions.get_user_active_subscriptions failed: tg=%s err=%s",
            telegram_id, e,
        )
        return []


# Статусы, по которым Platega ещё может списывать или слать callback'и
# (доку §2.3). PastDue сюда входит: повторов провайдер не делает, но
# подписка жива, и остановить её можно только явной отменой.
LIVE_STATUSES = ("Active", "PendingAgreement", "PastDue")


async def get_user_live_subscriptions(telegram_id: int) -> List[Dict[str, Any]]:
    """Подписки юзера, по которым Platega ещё может списывать.

    Отличие от get_user_active_subscriptions: сюда попадает и PastDue.
    Списание по ней не прошло и провайдер попыток не повторяет (доку §2.3),
    но подписка жива — остановить её можно только явной отменой, и админу
    в карточке она нужна именно для этого.
    """
    if not _core.DB_READY:
        return []
    pool = await get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM platega_subscriptions
                WHERE telegram_id = $1 AND status = ANY($2::text[])
                ORDER BY created_at DESC
                """,
                int(telegram_id), list(LIVE_STATUSES),
            )
            return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "platega_subscriptions.get_user_live_subscriptions failed: tg=%s err=%s",
            telegram_id, e,
        )
        return []


async def update_subscription_status(
    subscription_id: str,
    status: str,
    next_charge_at: Optional[datetime] = None,
    customer_email: Optional[str] = None,
) -> None:
    """Обновить статус подписки + (опционально) next_charge_at / customer_email.

    COALESCE — если параметр None, оставляем существующее значение.
    """
    if not _core.DB_READY:
        logger.warning("platega_subscriptions.update_subscription_status: DB not ready")
        return
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE platega_subscriptions
                   SET status = $2,
                       next_charge_at = COALESCE($3, next_charge_at),
                       customer_email = COALESCE($4, customer_email),
                       updated_at = NOW()
                 WHERE subscription_id = $1
                """,
                subscription_id, status, next_charge_at, customer_email,
            )
    except Exception as e:
        logger.warning(
            "platega_subscriptions.update_subscription_status failed: id=%s status=%s err=%s",
            subscription_id, status, e,
        )


async def record_charge(
    charge_id: str,
    subscription_id: str,
    telegram_id: int,
    amount_kopecks: int,
    status: str,
) -> bool:
    """Идемпотентная запись НЕоплаченного списания (CANCELED).

    Оплаченные (CONFIRMED) списания идут через claim_charge → confirm_charge:
    их нельзя фиксировать до выдачи доступа.

    Возвращает:
      True  — списание записано впервые.
      False — такой charge_id уже был (дубль webhook'а).
    Raises:
      ошибку БД — НЕ маскируем под «дубль» (иначе 200 и потерянный callback).

    CANCELED также инкрементит charges_failed в той же транзакции.
    """
    pool = await _require_pool("record_charge")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO platega_subscription_charges
                        (charge_id, subscription_id, telegram_id, amount_kopecks, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (charge_id) DO NOTHING
                    RETURNING charge_id
                    """,
                    charge_id, subscription_id, int(telegram_id),
                    int(amount_kopecks), status,
                )
                if not inserted:
                    # Дубль — счётчики НЕ трогаем.
                    return False

                if (status or "").upper() == "CANCELED":
                    await conn.execute(
                        """
                        UPDATE platega_subscriptions
                           SET charges_failed = charges_failed + 1,
                               updated_at = NOW()
                         WHERE subscription_id = $1
                        """,
                        subscription_id,
                    )
        return True
    except Exception as e:
        logger.error(
            "platega_subscriptions.record_charge failed: charge=%s sub=%s err=%s",
            charge_id, subscription_id, e,
        )
        raise


async def claim_charge(
    charge_id: str,
    subscription_id: str,
    telegram_id: int,
    amount_kopecks: int,
    stale_after_seconds: float = 240.0,
) -> str:
    """Захватить оплаченное списание перед выдачей доступа.

    Возвращает:
      "claimed"     — строка 'PROCESSING' создана, можно выдавать доступ;
      "reclaimed"   — была брошенная 'PROCESSING' старше stale_after_seconds
                      (краш посреди выдачи) — перехвачена, можно выдавать;
      "in_progress" — другая попытка выдаёт прямо сейчас → вызывающий отдаёт 500;
      "done"        — списание уже обработано → настоящий дубль, 200.
    Raises:
      ошибку БД (вызывающий → TransientPaymentError).

    Гонки: INSERT ... ON CONFLICT и UPDATE ... WHERE created_at < ... атомарны
    по строке — перехватить брошенный claim может ровно одна попытка.
    """
    pool = await _require_pool("claim_charge")
    try:
        async with pool.acquire() as conn:
            inserted = await conn.fetchval(
                """
                INSERT INTO platega_subscription_charges
                    (charge_id, subscription_id, telegram_id, amount_kopecks, status, created_at)
                VALUES ($1, $2, $3, $4, 'PROCESSING', NOW())
                ON CONFLICT (charge_id) DO NOTHING
                RETURNING charge_id
                """,
                charge_id, subscription_id, int(telegram_id), int(amount_kopecks),
            )
            if inserted:
                return "claimed"

            reclaimed = await conn.fetchval(
                """
                UPDATE platega_subscription_charges
                   SET created_at = NOW()
                 WHERE charge_id = $1
                   AND status = 'PROCESSING'
                   AND created_at < NOW() - make_interval(secs => $2::double precision)
                RETURNING charge_id
                """,
                charge_id, float(stale_after_seconds),
            )
            if reclaimed:
                return "reclaimed"

            status = await conn.fetchval(
                "SELECT status FROM platega_subscription_charges WHERE charge_id = $1",
                charge_id,
            )
    except Exception as e:
        logger.error(
            "platega_subscriptions.claim_charge failed: charge=%s sub=%s err=%s",
            charge_id, subscription_id, e,
        )
        raise
    # None — строку только что удалил release_charge соседней неудачной
    # попытки: пусть Platega повторит (500), а не «дубль».
    if status is None or status == CHARGE_PROCESSING:
        return "in_progress"
    return "done"


async def _confirm_charge_on(conn, charge_id: str, subscription_id: str, amount_kopecks: int) -> bool:
    confirmed = await conn.fetchval(
        """
        UPDATE platega_subscription_charges
           SET status = 'CONFIRMED'
         WHERE charge_id = $1 AND status = 'PROCESSING'
        RETURNING charge_id
        """,
        charge_id,
    )
    if not confirmed:
        return False
    await conn.execute(
        """
        UPDATE platega_subscriptions
           SET charges_success = charges_success + 1,
               total_amount_kopecks = total_amount_kopecks + $2,
               last_charge_at = NOW(),
               updated_at = NOW()
         WHERE subscription_id = $1
        """,
        subscription_id, int(amount_kopecks),
    )
    return True


async def confirm_charge(
    charge_id: str,
    subscription_id: str,
    amount_kopecks: int,
    conn=None,
) -> bool:
    """Отметить захваченное списание обработанным (доступ уже выдан).

    'PROCESSING' → 'CONFIRMED' + charges_success / total_amount_kopecks /
    last_charge_at подписки — одной транзакцией, чтобы счётчики не разъехались.
    Возвращает False, если строки 'PROCESSING' нет (уже подтверждена).

    conn: соединение с ОТКРЫТОЙ транзакцией вызывающего (T11) — оба UPDATE идут
    в ней, commit/rollback делает вызывающий. UPDATE берёт блокировку строки
    списания: параллельная попытка с тем же charge_id ждёт commit и получает
    False (строка уже 'CONFIRMED'). None — своя транзакция (как раньше).
    Raises: ошибку БД.
    """
    try:
        if conn is not None:
            return await _confirm_charge_on(conn, charge_id, subscription_id, amount_kopecks)
        pool = await _require_pool("confirm_charge")
        async with pool.acquire() as own_conn:
            async with own_conn.transaction():
                return await _confirm_charge_on(own_conn, charge_id, subscription_id, amount_kopecks)
    except Exception as e:
        logger.error(
            "platega_subscriptions.confirm_charge failed: charge=%s sub=%s err=%s",
            charge_id, subscription_id, e,
        )
        raise


async def release_charge(charge_id: str) -> None:
    """Снять захват (выдача упала), чтобы ретрай Platega выдал доступ заново.

    Удаляет только 'PROCESSING' — обработанные строки не трогает.
    Raises: ошибку БД (тогда брошенный claim подберётся по stale-таймауту).
    """
    pool = await _require_pool("release_charge")
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM platega_subscription_charges
                 WHERE charge_id = $1 AND status = 'PROCESSING'
                """,
                charge_id,
            )
    except Exception as e:
        logger.error(
            "platega_subscriptions.release_charge failed: charge=%s err=%s",
            charge_id, e,
        )
        raise
