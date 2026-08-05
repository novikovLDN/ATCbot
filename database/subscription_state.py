"""Состояние строки подписки: истечение, флаги, тариф, UUID.

ЧТО ЗДЕСЬ
    Точечные операции над одной строкой subscriptions, каждая из которых
    меняет состояние, но не выдаёт доступ и не проводит деньги:
        check_and_disable_expired_subscription  гашение истёкшей подписки
        ensure_bypass_only_subscription         строка под покупку трафика
        set_combo_flag / set_bypass_only_flag   флаги после покупки
        admin_switch_tariff                     переключение Basic↔Plus
        update_subscription_uuid                подмена uuid/vpn_key

ПОЧЕМУ ОТДЕЛЬНО ОТ ВЫДАЧИ
    grant_access и finalize_purchase — про «выдать товар за деньги», а это
    модуль про «поправить одну строку». Их правят по разным поводам, и
    держать их рядом означало, что любая правка флага требовала читать
    тысячу строк логики оплаты.

ЧТО ЛЕГКО СЛОМАТЬ
    check_and_disable_expired_subscription разнесена на три фазы намеренно:
    читаем состояние → снимаем доступ в панели ВНЕ транзакции → пишем в
    базу новой транзакцией. Сетевой вызов внутри транзакции держал бы
    соединение пула и блокировки строк на всё время запроса. Между фазами
    состояние перепроверяется: пока мы ходили в панель, человек мог
    продлиться, и тогда гасить его нельзя.

    ensure_bypass_only_subscription НЕ трогает активную платную подписку.
    Раньше она безусловно ставила expires_at на десять лет вперёд и
    is_bypass_only=TRUE любой строке — отсюда и брались «premium на 10 лет»
    у тех, кто просто докупил пакет трафика.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import config
import vpn_utils
import database.core as _core
from database.core import (
    get_pool,
    _to_db_utc,
    _from_db_utc,
    _normalize_subscription_row,
)
from database.subscription_audit import _log_vpn_lifecycle_audit_async
# Спецпредложение «-15% на 3 дня» выдаётся ровно в одном месте — когда у
# человека истекла ОПЛАЧЕННАЯ подписка. Живёт в trials_queries вместе с
# остальными персональными офферами, поэтому импорт перекрёстный.
from database.trials_queries import set_special_offer

logger = logging.getLogger(__name__)


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
            # Под этим вызовом нет действия: vpn_utils.remove_vless_user —
            # заглушка, xray снят с эксплуатации. EXPIRY_REMOVE_SUCCESS и
            # аудит result="success" утверждали снятие доступа, которого
            # не происходило, — и не «иногда», а всегда.
            # Фактический отзыв делает disable_remnawave_user_bg ниже, уже
            # после того как строка в базе закрыта.
            logger.info(
                "EXPIRY_LEGACY_UUID_CLEARED telegram_id=%s uuid=%s — xray-заглушка, "
                "ничего не удалено; фактический отзыв идёт через Remnawave фазой 3",
                telegram_id, uuid_to_remove[:8],
            )
            try:
                expires_at_str = (subscription.get("expires_at") or "").isoformat() if subscription else "N/A"
                await _log_vpn_lifecycle_audit_async(
                    action="vpn_expire",
                    telegram_id=telegram_id,
                    uuid=uuid_to_remove,
                    source="auto-expiry",
                    # result ограничен CHECK-ом ('success'|'error',
                    # migrations/007_add_audit_log_fields.sql), третьего
                    # значения туда не положить — поэтому правда о том, что
                    # именно произошло, идёт в details. Раньше строка читалась
                    # как «доступ снят», хотя снимать было нечем.
                    result="success",
                    details=(
                        f"Real-time expiration check, expires_at={expires_at_str}; "
                        f"legacy xray uuid cleared (no-op stub, nothing removed); "
                        f"фактический отзыв в панели — disable_remnawave_user_bg"
                    ),
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
            # Check if user has Remnawave bypass traffic — if so, transition to bypass-only
            # instead of fully expiring (bypass GB work independently of main subscription)
            has_remnawave = await conn.fetchval(
                "SELECT remnawave_uuid FROM subscriptions WHERE id = $1 AND remnawave_uuid IS NOT NULL",
                subscription_id,
            )

            if has_remnawave:
                # Transition to bypass-only: remove Xray but keep Remnawave active
                far_future = now + timedelta(days=3650)
                result = await conn.execute(
                    """UPDATE subscriptions
                       SET uuid = NULL, vpn_key = NULL, vpn_key_plus = NULL,
                           is_bypass_only = TRUE,
                           expires_at = $5,
                           source = 'bypass_only'
                       WHERE id = $1 AND telegram_id = $2 AND uuid = $3 AND status = 'active'
                         AND expires_at <= $4""",
                    subscription_id, telegram_id, uuid_to_remove, now_db,
                    _to_db_utc(far_future),
                )
                rows = int(result.split()[-1]) if result else 0
                if rows > 0:
                    logger.info(
                        "EXPIRY_TRANSITION_TO_BYPASS_ONLY user=%s — Remnawave stays active",
                        telegram_id,
                    )
                    # Extend Remnawave expiry so bypass keeps working
                    try:
                        from app.services.remnawave_service import extend_remnawave_for_bypass_bg
                        extend_remnawave_for_bypass_bg(telegram_id)
                    except Exception as rmn_err:
                        logger.warning("REMNAWAVE_BYPASS_EXTEND_FAIL: tg=%s %s", telegram_id, rmn_err)
                return rows > 0

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
                # Disable Remnawave bypass (fire-and-forget) — no remnawave_uuid means safe to disable
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


async def set_combo_flag(telegram_id: int, is_combo: bool = True):
    """Set is_combo flag on subscription after combo purchase."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE"
            )
        except Exception:
            pass
        result = await conn.execute(
            "UPDATE subscriptions SET is_combo = $1 WHERE telegram_id = $2",
            is_combo, telegram_id,
        )
        logger.info(f"set_combo_flag: user={telegram_id} is_combo={is_combo} result={result}")


async def set_bypass_only_flag(telegram_id: int, is_bypass_only: bool = True):
    """Set is_bypass_only flag on subscription after bypass-only purchase."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_bypass_only BOOLEAN DEFAULT FALSE"
            )
        except Exception:
            pass
        result = await conn.execute(
            "UPDATE subscriptions SET is_bypass_only = $1 WHERE telegram_id = $2",
            is_bypass_only, telegram_id,
        )
        logger.info(f"set_bypass_only_flag: user={telegram_id} is_bypass_only={is_bypass_only} result={result}")


async def ensure_bypass_only_subscription(telegram_id: int) -> bool:
    """Создать bypass-only subscription row, если её нет.

    Поведение:
      • Если subscription отсутствует — INSERT bypass_only row на 10 лет.
      • Если существует и **истёкшая** ИЛИ уже bypass_only — UPDATE:
        status='active', is_bypass_only=TRUE, expires_at=far_future,
        source='bypass_only' (если истёкшая).
      • Если существует и это **активная платная** подписка
        (expires_at > NOW() AND NOT is_bypass_only) — **НЕ трогаем**.
        Покупка bypass-трафика премиум-юзером — это просто +ГБ в
        Remnawave; основная подписка остаётся как есть.

    Раньше код безусловно делал `expires_at = GREATEST(expires_at,
    +10y)` и `is_bypass_only=TRUE` для любого row — это **переводило
    активную premium-подписку на 10 лет и помечало её как bypass**,
    что и порождало баг «premium на 10 лет» у юзеров, докупивших
    пакет трафика.

    Returns:
        True on success.
    """
    pool = await get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_bypass_only BOOLEAN DEFAULT FALSE"
            )
        except Exception:
            pass
        existing = await conn.fetchrow(
            """SELECT telegram_id, expires_at, is_bypass_only, status
               FROM subscriptions WHERE telegram_id = $1""",
            telegram_id,
        )
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        if existing:
            is_active_paid = (
                existing["expires_at"] is not None
                and _from_db_utc(existing["expires_at"]) > datetime.now(timezone.utc)
                and not bool(existing["is_bypass_only"])
            )
            if is_active_paid:
                # Юзер с активной платной — bypass-пак добавляется
                # только в Remnawave (трафик), subscription не трогаем.
                logger.info(
                    "ensure_bypass_only_subscription: SKIP active paid sub "
                    "(user=%s expires=%s) — only Remnawave traffic top-up",
                    telegram_id, existing["expires_at"],
                )
                return True
            # Истёкшая или уже bypass_only — продлеваем на 10 лет и
            # помечаем как bypass_only.
            await conn.execute(
                """UPDATE subscriptions
                   SET is_bypass_only = TRUE, status = 'active',
                       expires_at = $2,
                       source = CASE WHEN status = 'expired' OR expires_at < NOW()
                                     THEN 'bypass_only' ELSE source END
                   WHERE telegram_id = $1""",
                telegram_id, _to_db_utc(far_future),
            )
        else:
            await conn.execute(
                """INSERT INTO subscriptions (telegram_id, status, subscription_type, is_bypass_only, expires_at, source)
                   VALUES ($1, 'active', 'basic', TRUE, $2, 'bypass_only')""",
                telegram_id, _to_db_utc(far_future),
            )
        logger.info(f"ensure_bypass_only_subscription: user={telegram_id} created/updated")
        return True


async def admin_switch_tariff(telegram_id: int, new_tariff: str, vpn_key_plus: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Flip subscription_type (Basic↔Plus) for the active subscription.

    Tariffs are bot-side metadata only — Remnawave serves both tariffs
    from the same entity, so this is a pure DB-side flag flip. We do
    NOT touch vpn_key_plus: that column holds the bypass subscription
    URL (tariff-agnostic) under the Remnawave model.

    `vpn_key_plus` parameter is kept only for backward signature
    compatibility and ignored.
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
        await conn.execute(
            """UPDATE subscriptions SET subscription_type = $1
               WHERE telegram_id = $2 AND status = 'active'""",
            tariff, telegram_id
        )
        row = await conn.fetchrow(
            "SELECT * FROM subscriptions WHERE telegram_id = $1 AND status = 'active'",
            telegram_id
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
