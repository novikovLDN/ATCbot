"""Админские операции над подписками и балансом.

ЧТО ОСТАЛОСЬ ЗДЕСЬ ПОСЛЕ РАЗБИВКИ
    Только действия админа, меняющие состояние:
        admin_grant_access_atomic          выдать доступ на N дней
        admin_grant_access_minutes_atomic  выдать доступ на минуты (тесты)
        admin_revoke_access_atomic         отозвать доступ
    Плюс экспорты, сверка bypass-сущностей и удаление пользователя.

КУДА УЕХАЛО ОСТАЛЬНОЕ
    database/balance_purchases.py  покупка и пополнение с баланса
    database/broadcasts.py         рассылки, сегменты, A/B-тесты
    database/analytics.py          выручка, LTV, ARPU, разбивки
    database/discounts.py          персональные скидки и VIP
    Все имена по-прежнему доступны через пакет database.

ДВЕ ФАЗЫ ПРИ ВЫДАЧЕ ДОСТУПА
    Сначала сущность создаётся в панели (внешний вызов), и только потом
    открывается транзакция БД. Обратный порядок оставлял бы сироту в панели
    при откате транзакции. Там, где выдаётся комбо, начисление ГБ обхода
    тоже вынесено за транзакцию — по той же причине.

КОМБО-ТАРИФЫ
    В колонке subscription_type комбо не хранится: туда идёт базовый уровень
    доступа, а сам факт комбо помечается флагом is_combo и начислением
    трафика. Перечень тарифов, которые админ вправе выдать вручную, —
    config.GRANTABLE_TARIFF_TYPES.
"""
import asyncpg
import logging
import random
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING, List
import config
import vpn_utils
import database.core as _core
from database.core import (
    get_pool,
    _to_db_utc, _from_db_utc, _ensure_utc,
    _generate_subscription_uuid, safe_int,
    retry_async,
)

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

# Рассылки вынесены в database/broadcasts.py. Импортируются сюда, потому что
# на них годами ссылался код через database.admin.
# Скидки и VIP вынесены в database/discounts.py.
from database.discounts import (  # noqa: F401,E402
    get_user_discount,
    create_user_discount,
    has_claimed_referral_share_discount,
    record_referral_share_discount_claim,
    delete_user_discount,
    is_vip_user,
    grant_vip_status,
    revoke_vip_status,
)

# Аналитика вынесена в database/analytics.py — там только чтение.
from database.analytics import (  # noqa: F401,E402
    get_business_metrics,
    get_last_audit_logs,
    get_analytics_by_period,
    get_active_paid_subscriptions_count,
    get_revenue_for_period,
    get_payments_by_provider,
    get_payments_breakdown,
    get_recent_payments_feed,
    get_user_purchases,
    log_payment_error,
    get_recent_payment_errors,
    get_payment_errors_summary,
    get_traffic_stats,
    get_purchase_breakdown,
    get_extended_bot_stats,
    get_total_revenue,
    get_paying_users_count,
    get_user_ltv,
    get_average_ltv,
    get_arpu,
)

from database.broadcasts import (  # noqa: F401,E402
    create_broadcast,
    get_broadcast,
    save_broadcast_discount,
    save_broadcast_gift_reveal_percent,
    get_broadcast_discount,
    insert_admin_broadcast_record,
    update_admin_broadcast_record,
    get_users_by_segment,
    log_broadcast_send,
    get_broadcast_stats,
    get_broadcast_analytics,
    get_recent_broadcasts,
    get_broadcast_message_ids,
    mark_broadcast_messages_deleted,
    get_ab_test_broadcasts,
    get_incident_settings,
    set_incident_mode,
    get_ab_test_stats,
)

async def expire_old_pending_purchases() -> int:
    """
    Автоматически помечает истёкшие pending покупки как expired
    
    Returns:
        Количество истёкших покупок
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, expire_old_pending_purchases skipped")
        return 0
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, expire_old_pending_purchases skipped")
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pending_purchases SET status = 'expired' WHERE status = 'pending' AND expires_at <= NOW()"
        )
        
        # Извлекаем количество обновлённых строк из результата
        # Формат результата: "UPDATE N"
        if result and result.startswith("UPDATE "):
            count = int(result.split()[1])
            if count > 0:
                logger.info(f"Expired {count} old pending purchases")
            return count
        return 0


async def get_all_users_for_export() -> list:
    """Получить всех пользователей для экспорта
    
    Returns:
        Список словарей с данными пользователей
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
        return [dict(row) for row in rows]


async def get_active_subscriptions_for_export() -> list:
    """Получить все активные подписки для экспорта
    
    Returns:
        Список словарей с данными активных подписок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        rows = await conn.fetch(
            "SELECT * FROM subscriptions WHERE expires_at > $1 ORDER BY expires_at DESC",
            _to_db_utc(now)
        )
        return [dict(row) for row in rows]


# Функция get_vpn_keys_stats удалена - больше не используется
# VPN-ключи теперь создаются динамически через Outline API, статистика по пулу не актуальна


async def get_subscription_history(telegram_id: int, limit: int = 5) -> list:
    """Получить историю подписок пользователя
    
    Args:
        telegram_id: Telegram ID пользователя
        limit: Максимальное количество записей (по умолчанию 5)
    
    Returns:
        Список словарей с записями истории, отсортированные по created_at DESC
    """
    # Защита от работы с неинициализированной БД
    if not _core.DB_READY:
        logger.warning("DB not ready, get_subscription_history skipped")
        return []
    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_subscription_history skipped")
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM subscription_history 
               WHERE telegram_id = $1 
               ORDER BY created_at DESC 
               LIMIT $2""",
            telegram_id, limit
        )
        return [dict(row) for row in rows]


async def get_user_extended_stats(telegram_id: int) -> Dict[str, Any]:
    """Full financial + referral profile of a user for the admin card.

    Returns:
        renewals_count            — продлений подписки (subscription_history)
        reissues_count            — перевыпусков ключа
        total_spent_rubles        — сумма ВСЕХ approved-платежей в ₽
        total_payments_count      — общее число approved-платежей
        first_paid_at / last_paid_at — граничные даты платежей
        referrer_telegram_id      — кто пригласил (или NULL)
        referrer_username         — username пригласившего (для UI)
        referrals_invited_count   — сколько пригласил сам
        referrals_rewarded_count  — из них сколько «сработали» (bonus paid)
        traffic_gb_purchased_total — суммарно ГБ купил (bypass-паки)
        traffic_purchases_count   — количество GB-покупок
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Продления
        renewals_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history
               WHERE telegram_id = $1 AND action_type = 'renewal'""",
            telegram_id,
        )
        # Перевыпуски
        reissues_count = await conn.fetchval(
            """SELECT COUNT(*) FROM subscription_history
               WHERE telegram_id = $1
                 AND action_type IN ('reissue','manual_reissue')""",
            telegram_id,
        )
        # Финансы — оплаченные покупки по всем типам (подписки/трафик/etc).
        # price_kopecks / 100 = рубли; NULLs исключаем.
        #
        # Колонки называются именно так, а не иначе, и это важно: в
        # pending_purchases НЕТ ни amount_kopecks, ни paid_at. Запрос,
        # который их запрашивал, падал UndefinedColumnError на живой базе —
        # карточка пользователя в боте и в дашборде не открывалась вовсе.
        #
        # Момент оплаты берём из created_at — это старт чекаута, а не
        # приход денег. Расхождение до срока жизни счёта (минуты). Честная
        # колонка paid_at потребует миграции + записи в двух местах, где
        # статус переводится в 'paid' (database/subscriptions.py,
        # database/pending_purchases.py); до тех пор created_at — лучшее,
        # что есть, и им же считаются все оконные метрики выручки.
        pay_row = await conn.fetchrow(
            """SELECT
                   COUNT(*) AS n,
                   COALESCE(SUM(price_kopecks), 0)::BIGINT AS total_kopecks,
                   MIN(created_at) AS first_paid_at,
                   MAX(created_at) AS last_paid_at
               FROM pending_purchases
               WHERE telegram_id = $1
                 AND status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'""",
            telegram_id,
        )
        total_payments = int(pay_row["n"] or 0) if pay_row else 0
        total_kopecks = int(pay_row["total_kopecks"] or 0) if pay_row else 0
        first_paid_at = pay_row["first_paid_at"] if pay_row else None
        last_paid_at = pay_row["last_paid_at"] if pay_row else None

        # Пригласивший
        ref_row = await conn.fetchrow(
            """SELECT u.referrer_id, ru.username AS referrer_username
               FROM users u
               LEFT JOIN users ru ON ru.telegram_id = u.referrer_id
               WHERE u.telegram_id = $1""",
            telegram_id,
        )
        referrer_id = ref_row["referrer_id"] if ref_row else None
        referrer_username = ref_row["referrer_username"] if ref_row else None

        # Сколько сам пригласил
        inv_row = await conn.fetchrow(
            """SELECT COUNT(*) AS n,
                      COUNT(*) FILTER (WHERE is_rewarded) AS rewarded
               FROM referrals
               WHERE referrer_user_id = $1""",
            telegram_id,
        )
        invited = int(inv_row["n"] or 0) if inv_row else 0
        rewarded = int(inv_row["rewarded"] or 0) if inv_row else 0

        # Купил ГБ (bypass). Столбец gb_purchased.gb_amount, count и sum.
        gb_row = None
        try:
            gb_row = await conn.fetchrow(
                """SELECT COUNT(*) AS n,
                          COALESCE(SUM(gb_amount), 0)::INTEGER AS gb_total
                   FROM gb_purchased
                   WHERE telegram_id = $1""",
                telegram_id,
            )
        except Exception as e:
            logger.debug("gb_purchased query failed (табл. может отсутствовать): %s", e)
        gb_total = int(gb_row["gb_total"] or 0) if gb_row else 0
        gb_count = int(gb_row["n"] or 0) if gb_row else 0

        return {
            "renewals_count": renewals_count or 0,
            "reissues_count": reissues_count or 0,
            "total_spent_rubles": total_kopecks / 100.0,
            "total_payments_count": total_payments,
            "first_paid_at": first_paid_at.isoformat() if first_paid_at else None,
            "last_paid_at": last_paid_at.isoformat() if last_paid_at else None,
            "referrer_telegram_id": referrer_id,
            "referrer_username": referrer_username,
            "referrals_invited_count": invited,
            "referrals_rewarded_count": rewarded,
            "traffic_gb_purchased_total": gb_total,
            "traffic_purchases_count": gb_count,
        }


async def get_all_users_telegram_ids() -> list:
    """Получить список всех Telegram ID пользователей"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM users")
        return [row["telegram_id"] for row in rows]


async def get_eligible_no_subscription_broadcast_users() -> list:
    """Get users eligible for no-subscription broadcast.
    Eligible = no active paid subscription, no active trial, is_reachable=TRUE.
    Returns list of dicts with telegram_id. Defensive: fallback if is_reachable missing.
    """
    if not _core.DB_READY:
        logger.warning("DB not ready, get_eligible_no_subscription_broadcast_users skipped")
        return []
    pool = await get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        now = _to_db_utc(datetime.now(timezone.utc))
        query_with_reachable = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
              AND COALESCE(u.is_reachable, TRUE) = TRUE
        """
        fallback_query = """
            SELECT u.telegram_id
            FROM users u
            LEFT JOIN subscriptions paid_s ON paid_s.telegram_id = u.telegram_id
                AND paid_s.status = 'active'
                AND paid_s.expires_at > $1
                AND paid_s.source != 'trial'
            WHERE paid_s.id IS NULL
              AND (u.trial_expires_at IS NULL OR u.trial_expires_at <= $1)
        """
        try:
            rows = await conn.fetch(query_with_reachable, now)
        except asyncpg.UndefinedColumnError:
            logger.warning("DB_SCHEMA_OUTDATED: is_reachable missing, no_sub_broadcast fallback")
            rows = await conn.fetch(fallback_query, now)
        return [{"telegram_id": row["telegram_id"]} for row in rows]


async def check_user_still_eligible_for_no_sub_broadcast(conn, telegram_id: int, now: datetime) -> bool:
    """Race-condition re-check before sending. Returns True if still eligible."""
    from database.subscriptions import get_active_paid_subscription
    paid = await get_active_paid_subscription(conn, telegram_id, now)
    if paid:
        return False
    try:
        row = await conn.fetchrow(
            "SELECT trial_expires_at, is_reachable FROM users WHERE telegram_id = $1",
            telegram_id
        )
    except asyncpg.UndefinedColumnError:
        row = await conn.fetchrow(
            "SELECT trial_expires_at FROM users WHERE telegram_id = $1",
            telegram_id
        )
    if not row:
        return False
    trial_expires_at = row.get("trial_expires_at")
    if trial_expires_at:
        trial_expires_at_utc = _from_db_utc(trial_expires_at)
        now_utc = now if (getattr(now, "tzinfo", None) is not None) else datetime.now(timezone.utc)
        if trial_expires_at_utc > now_utc:
            return False
    is_reachable = row.get("is_reachable")
    if is_reachable is False:
        return False
    return True


async def admin_grant_access_atomic(telegram_id: int, days: int, admin_telegram_id: int, tariff: str = "basic") -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N дней (админ)

    Two-phase activation: Phase 1 add_vless_user outside tx, Phase 2 grant_access inside tx.
    Eliminates orphan UUID risk (no external call inside DB transaction).

    Args:
        telegram_id: Telegram ID пользователя
        days: Количество дней доступа (1, 7 или 14)
        admin_telegram_id: Telegram ID администратора
        tariff: "basic" или "plus" — тип тарифа для VPN API и подписки

    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)

    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
    from database.subscriptions import grant_access, _log_audit_event_atomic, _log_subscription_history_atomic

    duration = timedelta(days=days)
    now_pre = datetime.now(timezone.utc)
    subscription_end_pre = now_pre + duration

    pool = await get_pool()
    # Read existing sub once for the outer is_new_issuance heuristic. We
    # don't lock it — that happens inside the Phase 2 tx via grant_access.
    async with pool.acquire() as conn_pre:
        sub_row = await conn_pre.fetchrow("SELECT * FROM subscriptions WHERE telegram_id = $1", telegram_id)
        outer_is_new_issuance = True
        if sub_row:
            sub = dict(sub_row)
            exp_raw = sub.get("expires_at")
            exp = _from_db_utc(exp_raw) if exp_raw else None
            outer_is_new_issuance = (
                sub.get("status") != "active" or not exp or exp <= now_pre or not sub.get("uuid")
            )
        # Нормализация тарифа.
        #
        # Комбо («Комбо Базовый», «Комбо Плюс») — это подписка ПЛЮС пакет ГБ
        # обхода. В колонке subscription_type комбо не хранится: туда идёт
        # базовый уровень доступа, а сам факт комбо помечается флагом is_combo
        # и выдачей трафика после выдачи подписки.
        #
        # Раньше здесь любой тариф вне VALID_SUBSCRIPTION_TYPES молча
        # превращался в "basic". Это означало, что попытка выдать «Комбо Плюс»
        # давала пользователю Базовый без единой ошибки в логах.
        requested_tariff = (tariff or "basic").strip().lower()
        is_combo_grant = requested_tariff in getattr(config, "COMBO_TARIFF_TYPES", ())
        if is_combo_grant:
            # combo_plus → plus: уровень доступа берём из базового тарифа.
            tariff_normalized = requested_tariff.replace("combo_", "", 1)
        else:
            tariff_normalized = requested_tariff
        if tariff_normalized not in config.VALID_SUBSCRIPTION_TYPES:
            logger.warning(
                "ADMIN_GRANT_UNKNOWN_TARIFF: запрошен %s, выдаём basic (user=%s)",
                requested_tariff, telegram_id,
            )
            tariff_normalized = "basic"

    # Two-attempt loop. Attempt 1 trusts the outer `is_new_issuance` check.
    # Attempt 2 only runs if Phase 2 raised the invariant — i.e. grant_access
    # decided new issuance was needed even though the outer check said no
    # (race: a background worker expired the sub between the two reads, or
    # the row was stale). We force Phase 1 on the retry so pre_provisioned_uuid
    # is set when entering the tx again.
    last_error: Optional[BaseException] = None
    for attempt in (1, 2):
        force_provision = attempt == 2
        pre_provisioned_uuid = None
        uuid_to_cleanup_on_failure = None

        # PHASE 1 (outside DB transaction): Provision UUID via VPN API if needed
        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED:
            try:
                from app.services import purchase_flow
                vless_result = await purchase_flow.provision_subscription(
                    telegram_id,
                    tariff=tariff_normalized,
                    subscription_end=subscription_end_pre,
                    period_days=days,
                    is_trial=False,
                )
                pre_provisioned_uuid = {
                    "uuid": vless_result["uuid"].strip(),
                    "vless_url": vless_result["vless_url"],
                    "subscription_type": vless_result.get("subscription_type") or tariff_normalized,
                }
                if vless_result.get("vless_url_plus"):
                    pre_provisioned_uuid["vless_url_plus"] = vless_result["vless_url_plus"]
                uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                logger.info(
                    f"admin_grant_access_atomic: TWO_PHASE_PHASE1_DONE [user={telegram_id}, "
                    f"uuid={uuid_to_cleanup_on_failure[:8]}..., tariff={tariff_normalized}, attempt={attempt}]"
                )
            except Exception as phase1_err:
                # Loud, with traceback, so admin can diagnose Remnawave
                # outages directly from the bot logs without guessing.
                logger.error(
                    f"admin_grant_access_atomic: PHASE1_FAILED [user={telegram_id}, "
                    f"attempt={attempt}, error={phase1_err}]",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"VPN provisioning failed (Phase 1): {phase1_err}"
                ) from phase1_err

        # Defense in depth: if Phase 1 was supposed to run but didn't set
        # a UUID for any reason, bail out cleanly instead of letting the
        # invariant fire inside the tx with no actionable message.
        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED and not pre_provisioned_uuid:
            raise RuntimeError(
                f"Phase 1 produced no UUID for user {telegram_id} — refusing to enter tx"
            )

        ret_val = None
        grant_result_for_removal = None
        invariant_hit = False
        async with pool.acquire() as conn:
            async with conn.transaction():
                try:
                    grant_result_for_removal = result = await grant_access(
                        telegram_id=telegram_id,
                        duration=duration,
                        source="admin",
                        admin_telegram_id=admin_telegram_id,
                        admin_grant_days=days,
                        conn=conn,
                        pre_provisioned_uuid=pre_provisioned_uuid,
                        _caller_holds_transaction=True,
                        tariff=tariff_normalized,
                    )
                    expires_at = result["subscription_end"]
                    if result.get("vless_url"):
                        final_vpn_key = result["vless_url"]
                    else:
                        subscription_row = await conn.fetchrow(
                            "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                            telegram_id
                        )
                        if subscription_row and subscription_row.get("vpn_key"):
                            final_vpn_key = subscription_row["vpn_key"]
                        else:
                            final_vpn_key = result.get("uuid", "")
                    uuid_preview = f"{result['uuid'][:8]}..." if result.get('uuid') and len(result['uuid']) > 8 else (result.get('uuid') or "N/A")
                    logger.info(f"admin_grant_access_atomic: SUCCESS [admin={admin_telegram_id}, user={telegram_id}, days={days}, uuid={uuid_preview}, expires_at={expires_at.isoformat()}]")
                    ret_val = (expires_at, final_vpn_key)
                except RuntimeError as e:
                    last_error = e
                    if "INVARIANT_VIOLATION" in str(e) and attempt == 1 and not pre_provisioned_uuid:
                        # Race: outer check said no new issuance, but grant_access
                        # inside the locked tx decided otherwise. Retry once with
                        # forced Phase 1.
                        invariant_hit = True
                        logger.warning(
                            f"admin_grant_access_atomic: INVARIANT_HIT_RETRYING [user={telegram_id}] — "
                            "outer is_new_issuance was False but grant_access disagreed; "
                            "forcing Phase 1 on attempt 2"
                        )
                    else:
                        if uuid_to_cleanup_on_failure:
                            try:
                                await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                                logger.critical(
                                    f"ORPHAN_PREVENTED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason=admin_grant_access_atomic_tx_failed user={telegram_id} error={e}"
                                )
                            except Exception as remove_err:
                                logger.critical(
                                    f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason={remove_err} user={telegram_id}"
                                )
                        logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                        raise
                except Exception as e:
                    last_error = e
                    if uuid_to_cleanup_on_failure:
                        try:
                            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED uuid={uuid_preview} reason=admin_grant_access_atomic_tx_failed "
                                f"user={telegram_id} error={e}"
                            )
                        except Exception as remove_err:
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} user={telegram_id}"
                            )
                    logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                    raise

        if invariant_hit:
            # Loop body falls through, attempt=2 will force Phase 1.
            continue
        # ret_val is set when Phase 2 succeeded; break out of retry loop.
        if ret_val is not None:
            break

    if ret_val is None:
        # Both attempts failed. The exception from attempt 2 (or whatever
        # last bubbled) has already been re-raised above; getting here
        # means the retry loop fell through without a success. Surface
        # whatever error we saved.
        raise last_error or RuntimeError("admin_grant_access_atomic: unknown failure")
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
                "RENEWAL_REMNAWAVE_SYNC_FAILED",
                extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
            )

    # Комбо-часть выдачи: подписка уже выдана выше, осталось пометить её как
    # комбо и начислить пакет ГБ обхода. Делается ПОСЛЕ коммита, потому что
    # начисление трафика — внешний вызов к Remnawave, и держать его внутри
    # транзакции значило бы рисковать сиротами при откате.
    #
    # Сбой на этом шаге не отменяет уже выданную подписку: пользователь
    # получит доступ, а недостающие ГБ админ доначислит вручную по записи
    # ADMIN_GRANT_COMBO_BYPASS_FAILED в логе.
    if is_combo_grant and ret_val is not None:
        expires_at_granted = ret_val[0] if isinstance(ret_val, tuple) else None
        try:
            from app.constants import tariffs as tariff_ref
            from database.subscriptions import set_combo_flag

            await set_combo_flag(telegram_id, True)

            bypass_gb = tariff_ref.combo_bypass_gb(requested_tariff, days)
            if bypass_gb > 0:
                from app.services.remnawave_service import add_bypass_traffic
                ok = await add_bypass_traffic(
                    telegram_id,
                    extra_bytes=bypass_gb * 1024 ** 3,
                    subscription_type=tariff_normalized,
                    subscription_end=expires_at_granted,
                    period_days=days,
                )
                if ok:
                    logger.info(
                        "ADMIN_GRANT_COMBO_OK user=%s tariff=%s bypass_gb=%s",
                        telegram_id, requested_tariff, bypass_gb,
                    )
                else:
                    logger.error(
                        "ADMIN_GRANT_COMBO_BYPASS_FAILED user=%s tariff=%s bypass_gb=%s "
                        "— подписка выдана, трафик нужно доначислить вручную",
                        telegram_id, requested_tariff, bypass_gb,
                    )
            else:
                logger.warning(
                    "ADMIN_GRANT_COMBO_NO_GB user=%s tariff=%s days=%s — в таблице "
                    "COMBO_TARIFFS нет пакета ГБ для такого срока",
                    telegram_id, requested_tariff, days,
                )
        except Exception as e:
            logger.error(
                "ADMIN_GRANT_COMBO_BYPASS_FAILED user=%s tariff=%s error=%s "
                "— подписка выдана, трафик нужно доначислить вручную",
                telegram_id, requested_tariff, e,
            )

    return ret_val


async def admin_grant_access_minutes_atomic(telegram_id: int, minutes: int, admin_telegram_id: int) -> Tuple[datetime, str]:
    """Атомарно выдать доступ пользователю на N минут (админ)

    Two-phase activation: Phase 1 add_vless_user outside tx, Phase 2 grant_access inside tx.
    Eliminates orphan UUID risk (no external call inside DB transaction).

    Args:
        telegram_id: Telegram ID пользователя
        minutes: Количество минут доступа (например, 10)
        admin_telegram_id: Telegram ID администратора

    Returns:
        Tuple[datetime, str]: (expires_at, vpn_key)
        - expires_at: Дата истечения подписки
        - vpn_key: VPN ключ (vless_url для нового UUID, vpn_key из подписки для продления, или uuid как fallback)

    Raises:
        Exception: При любых ошибках (транзакция откатывается, исключение пробрасывается)
        Гарантированно возвращает значения или выбрасывает исключение. Никогда не возвращает None.
    """
    from database.subscriptions import grant_access, _log_audit_event_atomic, _log_subscription_history_atomic

    duration = timedelta(minutes=minutes)
    now_pre = datetime.now(timezone.utc)
    subscription_end_pre = now_pre + duration

    pool = await get_pool()
    async with pool.acquire() as conn_pre:
        sub_row = await conn_pre.fetchrow("SELECT * FROM subscriptions WHERE telegram_id = $1", telegram_id)
        outer_is_new_issuance = True
        if sub_row:
            sub = dict(sub_row)
            exp_raw = sub.get("expires_at")
            exp = _from_db_utc(exp_raw) if exp_raw else None
            outer_is_new_issuance = (
                sub.get("status") != "active" or not exp or exp <= now_pre or not sub.get("uuid")
            )

    # Two-attempt loop — same race-recovery pattern as the days-variant
    # of this function (see admin_grant_access_atomic above for the why).
    last_error: Optional[BaseException] = None
    ret_val = None
    grant_result_for_removal = None
    for attempt in (1, 2):
        force_provision = attempt == 2
        pre_provisioned_uuid = None
        uuid_to_cleanup_on_failure = None

        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED:
            try:
                from app.services import purchase_flow
                vless_result = await purchase_flow.provision_subscription(
                    telegram_id,
                    tariff="basic",
                    subscription_end=subscription_end_pre,
                    period_days=max(1, minutes // 1440),
                    is_trial=False,
                )
                pre_provisioned_uuid = {
                    "uuid": vless_result["uuid"].strip(),
                    "vless_url": vless_result["vless_url"],
                    "vless_url_plus": vless_result.get("vless_url_plus"),
                }
                uuid_to_cleanup_on_failure = pre_provisioned_uuid["uuid"]
                logger.info(
                    f"admin_grant_access_minutes_atomic: TWO_PHASE_PHASE1_DONE [user={telegram_id}, "
                    f"uuid={uuid_to_cleanup_on_failure[:8]}..., attempt={attempt}]"
                )
            except Exception as phase1_err:
                logger.error(
                    f"admin_grant_access_minutes_atomic: PHASE1_FAILED [user={telegram_id}, "
                    f"attempt={attempt}, error={phase1_err}]",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"VPN provisioning failed (Phase 1): {phase1_err}"
                ) from phase1_err

        if (force_provision or outer_is_new_issuance) and config.VPN_ENABLED and not pre_provisioned_uuid:
            raise RuntimeError(
                f"Phase 1 produced no UUID for user {telegram_id} — refusing to enter tx"
            )

        invariant_hit = False
        async with pool.acquire() as conn:
            async with conn.transaction():
                try:
                    grant_result_for_removal = result = await grant_access(
                        telegram_id=telegram_id,
                        duration=duration,
                        source="admin",
                        admin_telegram_id=admin_telegram_id,
                        admin_grant_days=None,
                        conn=conn,
                        pre_provisioned_uuid=pre_provisioned_uuid,
                        _caller_holds_transaction=True
                    )
                    expires_at = result["subscription_end"]
                    if result.get("vless_url"):
                        final_vpn_key = result["vless_url"]
                    else:
                        subscription_row = await conn.fetchrow(
                            "SELECT vpn_key FROM subscriptions WHERE telegram_id = $1",
                            telegram_id
                        )
                        if subscription_row and subscription_row.get("vpn_key"):
                            final_vpn_key = subscription_row["vpn_key"]
                        else:
                            final_vpn_key = result.get("uuid", "")
                    uuid_preview = f"{result['uuid'][:8]}..." if result.get('uuid') and len(result['uuid']) > 8 else (result.get('uuid') or "N/A")
                    logger.info(
                        f"admin_grant_access_minutes_atomic: SUCCESS [admin={admin_telegram_id}, user={telegram_id}, "
                        f"minutes={minutes}, uuid={uuid_preview}, expires_at={expires_at.isoformat()}]"
                    )
                    ret_val = (expires_at, final_vpn_key)
                except RuntimeError as e:
                    last_error = e
                    if "INVARIANT_VIOLATION" in str(e) and attempt == 1 and not pre_provisioned_uuid:
                        invariant_hit = True
                        logger.warning(
                            f"admin_grant_access_minutes_atomic: INVARIANT_HIT_RETRYING [user={telegram_id}] — "
                            "outer is_new_issuance was False but grant_access disagreed; "
                            "forcing Phase 1 on attempt 2"
                        )
                    else:
                        if uuid_to_cleanup_on_failure:
                            try:
                                await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                                logger.critical(
                                    f"ORPHAN_PREVENTED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason=admin_grant_access_minutes_atomic_tx_failed user={telegram_id} error={e}"
                                )
                            except Exception as remove_err:
                                logger.critical(
                                    f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_to_cleanup_on_failure[:8]}... "
                                    f"reason={remove_err} user={telegram_id}"
                                )
                        logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                        raise
                except Exception as e:
                    last_error = e
                    if uuid_to_cleanup_on_failure:
                        try:
                            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_cleanup_on_failure)
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED uuid={uuid_preview} reason=admin_grant_access_minutes_tx_failed "
                                f"user={telegram_id} error={e}"
                            )
                        except Exception as remove_err:
                            uuid_preview = f"{uuid_to_cleanup_on_failure[:8]}..." if len(uuid_to_cleanup_on_failure) > 8 else "***"
                            logger.critical(
                                f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={uuid_preview} reason={remove_err} user={telegram_id}"
                            )
                    logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                    raise

        if invariant_hit:
            continue
        if ret_val is not None:
            break

    if ret_val is None:
        raise last_error or RuntimeError("admin_grant_access_minutes_atomic: unknown failure")
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
                "RENEWAL_REMNAWAVE_SYNC_FAILED",
                extra={"telegram_id": sync_info["telegram_id"], "uuid": sync_info["uuid"][:8] + "...", "error": str(e)[:200]}
            )
    return ret_val


async def admin_revoke_access_atomic(telegram_id: int, admin_telegram_id: int) -> bool:
    """Атомарно лишить доступа пользователя (админ)

    В одной транзакции:
    - удаляет UUID из Xray API (если есть uuid)
    - устанавливает status = 'expired', expires_at = NOW()
    - очищает uuid и vpn_key
    - записывает в subscription_history (action = admin_revoke)
    - записывает событие в audit_log

    Args:
        telegram_id: Telegram ID пользователя
        admin_telegram_id: Telegram ID администратора

    Returns:
        True если доступ был отозван, False если активной подписки не было
    """
    from database.subscriptions import _log_audit_event_atomic, _log_subscription_history_atomic

    pool = await get_pool()
    uuid_to_remove = None
    ret = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                now = datetime.now(timezone.utc)
                now_db = _to_db_utc(now)

                # 1. Проверяем, есть ли активная подписка (FOR UPDATE для блокировки)
                subscription_row = await conn.fetchrow(
                    "SELECT * FROM subscriptions WHERE telegram_id = $1 AND expires_at > $2 FOR UPDATE",
                    telegram_id, now_db
                )
                
                if not subscription_row:
                    logger.info(f"No active subscription to revoke for user {telegram_id}")
                    return False
                
                subscription = dict(subscription_row)
                old_expires_at = subscription["expires_at"]
                vpn_key = subscription.get("vpn_key", "")
                # PHASE 1: Capture UUID for removal OUTSIDE transaction (no VPN API call inside tx)
                uuid_to_remove = subscription.get("uuid") if subscription.get("uuid") else None
                
                # 2. Очищаем подписку: устанавливаем expires_at = NOW(), очищаем uuid и vpn_key
                await conn.execute(
                    "UPDATE subscriptions SET expires_at = $1, status = 'expired', uuid = NULL, vpn_key = NULL WHERE telegram_id = $2",
                    now_db, telegram_id
                )
                
                # 4. Записываем в историю подписок (используем старый vpn_key для истории, если был)
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key or "", now, now, "admin_revoke")
                
                # 5. Записываем событие в audit_log
                vpn_key_preview = vpn_key[:20] + "..." if vpn_key else "N/A"
                details = f"Revoked access, Old expires_at: {old_expires_at.isoformat()}, VPN key: {vpn_key_preview}"
                await _log_audit_event_atomic(conn, "admin_revoke", admin_telegram_id, telegram_id, details)
                
                logger.info(f"Admin {admin_telegram_id} revoked access for user {telegram_id}")
                ret = True
                
            except Exception as e:
                logger.exception(f"Error in admin_revoke_access_atomic for user {telegram_id}, transaction rolled back")
                raise
        # ФАЗА 2 (вне транзакции): отключить доступ в панели.
        #
        # Раньше здесь вызывался только vpn_utils.safe_remove_vless_user_with_retry.
        # После перехода на Remnawave эта функция стала заглушкой, поэтому отзыв
        # доступа очищал запись в базе, но НЕ отключал пользователя в панели:
        # ссылка продолжала работать, и человек пользовался VPN после отзыва.
        #
        # Отключение вынесено за транзакцию намеренно: это внешний HTTP-вызов,
        # и держать транзакцию открытой на время сетевого запроса нельзя.
        # Сбой отключения не откатывает отзыв — доступ уже снят в базе, а
        # запись ADMIN_REVOKE_PREMIUM_DISABLE_FAILED говорит, что в панели
        # нужно отключить вручную.
        try:
            from app.services.remnawave_premium import disable_premium_user
            disabled = await disable_premium_user(telegram_id)
            if disabled:
                logger.info("ADMIN_REVOKE_PREMIUM_DISABLED user=%s", telegram_id)
            else:
                logger.warning(
                    "ADMIN_REVOKE_PREMIUM_NOT_DISABLED user=%s — сущность не найдена "
                    "в панели или панель отключена",
                    telegram_id,
                )
        except Exception as e:
            logger.critical(
                "ADMIN_REVOKE_PREMIUM_DISABLE_FAILED user=%s error=%s — доступ снят "
                "в базе, отключите пользователя в панели вручную",
                telegram_id, e,
            )

        if uuid_to_remove:
            try:
                await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)
                logger.info("ADMIN_REVOKE_UUID_REMOVED", extra={"uuid": uuid_to_remove[:8] + "..."})
            except Exception as e:
                logger.critical(
                    "ADMIN_REVOKE_UUID_REMOVAL_FAILED",
                    extra={"uuid": uuid_to_remove[:8] + "...", "error": str(e)[:200]}
                )
    return ret


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ПЕРСОНАЛЬНЫМИ СКИДКАМИ ====================

async def get_bypass_overwrite_victims() -> List[Dict[str, Any]]:
    """Список пострадавших от bypass-overwrite бага с детализацией.

    Для каждого юзера:
      - текущая subscription row;
      - все subscription_history записи покупок/продлений;
      - все traffic_purchases;
      - вычисленный корректный expires_at = max(end_date) по
        последней платной транзакции;
      - вердикт `can_fix`: достаточно ли данных для восстановления.

    Не делает никаких UPDATE — только read-only аудит.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        suspects = await conn.fetch(
            """
            SELECT s.telegram_id,
                   u.username,
                   s.expires_at AS current_expires_at,
                   s.is_bypass_only,
                   s.subscription_type AS current_subscription_type,
                   s.source AS current_source,
                   s.is_combo
            FROM subscriptions s
            JOIN users u ON u.telegram_id = s.telegram_id
            WHERE s.is_bypass_only = TRUE
              AND s.expires_at > (NOW() AT TIME ZONE 'UTC') + INTERVAL '3 years'
              AND EXISTS (
                  SELECT 1 FROM subscription_history sh
                  WHERE sh.telegram_id = s.telegram_id
                    AND sh.action_type IN ('purchase', 'renewal', 'auto_renew')
              )
            ORDER BY s.expires_at DESC
            """
        )

        victims: List[Dict[str, Any]] = []
        for row in suspects:
            tg = int(row["telegram_id"])
            history = await conn.fetch(
                """
                SELECT id, action_type, start_date, end_date, created_at, vpn_key
                FROM subscription_history
                WHERE telegram_id = $1
                ORDER BY created_at DESC
                LIMIT 50
                """,
                tg,
            )
            traffic = await conn.fetch(
                """
                SELECT id, gb_amount, price_rub, created_at
                FROM traffic_purchases
                WHERE telegram_id = $1
                ORDER BY created_at DESC
                LIMIT 50
                """,
                tg,
            )
            payments = await conn.fetch(
                """
                SELECT id, tariff, amount, paid_at, created_at, purchase_id
                FROM payments
                WHERE telegram_id = $1 AND status = 'approved'
                ORDER BY COALESCE(paid_at, created_at) DESC
                LIMIT 50
                """,
                tg,
            )

            # Источник истины для корректного expires_at — самая поздняя
            # end_date по платным action_type'ам в subscription_history.
            last_paid_end = await conn.fetchval(
                """
                SELECT MAX(end_date) FROM subscription_history
                WHERE telegram_id = $1
                  AND action_type IN ('purchase', 'renewal', 'auto_renew')
                """,
                tg,
            )
            last_paid_action = await conn.fetchrow(
                """
                SELECT action_type, end_date, created_at
                FROM subscription_history
                WHERE telegram_id = $1
                  AND action_type IN ('purchase', 'renewal', 'auto_renew')
                ORDER BY end_date DESC NULLS LAST
                LIMIT 1
                """,
                tg,
            )

            traffic_total_gb = sum(int(t["gb_amount"] or 0) for t in traffic)
            payments_count = len(payments)
            premium_payments_count = len(
                [p for p in payments if (p["tariff"] or "").startswith(("basic", "plus"))]
            )

            # Вердикт: восстановим если есть end_date в истории.
            # Grace-period: если эта end_date уже в прошлом, при
            # применении fix'а будет NOW + 1 day (Remnawave не
            # принимает даты в прошлом как активную подписку).
            can_fix = last_paid_end is not None
            now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            grace_will_apply = (
                last_paid_end is not None and last_paid_end <= now_utc_naive
            )
            proposed_after_grace = (
                _to_db_utc(datetime.now(timezone.utc) + timedelta(days=1))
                if grace_will_apply
                else last_paid_end
            )

            victims.append(
                {
                    "telegram_id": tg,
                    "username": row["username"],
                    "current_expires_at": row["current_expires_at"],
                    "current_is_bypass_only": bool(row["is_bypass_only"]),
                    "current_subscription_type": row["current_subscription_type"],
                    "current_source": row["current_source"],
                    "current_is_combo": bool(row["is_combo"]) if row["is_combo"] is not None else False,
                    "proposed_expires_at": proposed_after_grace,
                    "history_end_date": last_paid_end,
                    "grace_will_apply": grace_will_apply,
                    "last_paid_action_type": (
                        last_paid_action["action_type"] if last_paid_action else None
                    ),
                    "history": [
                        {
                            "id": int(h["id"]),
                            "action_type": h["action_type"],
                            "start_date": h["start_date"],
                            "end_date": h["end_date"],
                            "created_at": h["created_at"],
                        }
                        for h in history
                    ],
                    "payments": [
                        {
                            "id": int(p["id"]),
                            "tariff": p["tariff"],
                            "amount_rubles": float((p["amount"] or 0)) / 100.0,
                            "paid_at": p["paid_at"],
                            "created_at": p["created_at"],
                            "purchase_id": p["purchase_id"],
                        }
                        for p in payments
                    ],
                    "traffic_purchases": [
                        {
                            "id": int(t["id"]),
                            "gb_amount": int(t["gb_amount"] or 0),
                            "price_rub": int(t["price_rub"] or 0),
                            "created_at": t["created_at"],
                        }
                        for t in traffic
                    ],
                    "traffic_total_gb": traffic_total_gb,
                    "payments_count": payments_count,
                    "premium_payments_count": premium_payments_count,
                    "can_fix": can_fix,
                }
            )

        return victims


async def fix_bypass_overwrite_victim(telegram_id: int) -> Dict[str, Any]:
    """Восстановить корректную подписку для одного пострадавшего юзера.

    Алгоритм:
      1. Найти max(end_date) среди subscription_history с
         action_type IN ('purchase','renewal','auto_renew').
      2. Если эта end_date уже в прошлом — Remnawave-панель не
         примет дату из прошлого как активную подписку. Поэтому
         даём минимальный grace-period: NOW + 1 day. Юзер увидит
         «истекает завтра», что технически даёт ему 1 сутки и
         корректно проставится в панели.
      3. UPDATE subscriptions: is_bypass_only=FALSE,
         expires_at=<correct_or_grace>, source='payment',
         subscription_type — оставить как есть, кроме случая
         'bypass_only' → 'basic'.

    Не трогает Remnawave — там трафик хранится отдельно и его
    реставрировать не нужно (bypass GB всё равно у юзера остались).

    Returns dict с before/after + grace_applied для логирования.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT expires_at, is_bypass_only, subscription_type, source FROM subscriptions WHERE telegram_id = $1",
                telegram_id,
            )
            if not before:
                return {"ok": False, "reason": "no_subscription_row"}

            history_end = await conn.fetchval(
                """
                SELECT MAX(end_date) FROM subscription_history
                WHERE telegram_id = $1
                  AND action_type IN ('purchase', 'renewal', 'auto_renew')
                """,
                telegram_id,
            )
            if history_end is None:
                return {"ok": False, "reason": "no_paid_history_to_recover_from"}

            # Grace-period: если правильная end_date уже в прошлом —
            # ставим NOW + 1 сутки. Иначе берём saved end_date.
            now_utc = datetime.now(timezone.utc)
            history_end_aware = _from_db_utc(history_end)
            grace_applied = history_end_aware <= now_utc
            target_expires_at = (
                _to_db_utc(now_utc + timedelta(days=1)) if grace_applied else history_end
            )

            await conn.execute(
                """
                UPDATE subscriptions
                SET is_bypass_only = FALSE,
                    expires_at = $2,
                    source = CASE WHEN source = 'bypass_only' THEN 'payment' ELSE source END,
                    subscription_type = CASE WHEN subscription_type IS NULL OR subscription_type = ''
                                              THEN 'basic' ELSE subscription_type END
                WHERE telegram_id = $1
                """,
                telegram_id,
                target_expires_at,
            )
            after = await conn.fetchrow(
                "SELECT expires_at, is_bypass_only, subscription_type, source FROM subscriptions WHERE telegram_id = $1",
                telegram_id,
            )

    return {
        "ok": True,
        "telegram_id": telegram_id,
        "grace_applied": grace_applied,
        "history_end_date": history_end,
        "before": dict(before),
        "after": dict(after) if after else None,
    }


async def get_daily_timeseries(days: int) -> Dict[str, Any]:
    """Daily аггрегаты за последние `days` суток по Москве.

    Один payload — три серии: revenue, new_users, new_subscriptions.
    Все пустые дни в окне присутствуют с нулями (через generate_series),
    чтобы фронту не приходилось их добивать самому — графики получают
    непрерывный X.

    ПОЧЕМУ МОСКВА, А НЕ UTC.
    Раньше сутки резались по UTC, а тайл «Доход сегодня» на том же экране
    считался от полуночи МСК (фронт присылает since=mskTodayStartIso,
    см. dashboard/src/lib/format.ts). Три часа покупок — с 00:00 до 03:00
    МСК — у тайла попадали в сегодня, а у графика во вчера: две цифры про
    один и тот же день не сходились, и понять, какая из них правильная,
    было нельзя. Часовой график (get_hourly_timeseries) уже жил в МСК.
    Теперь весь дашборд режет сутки одинаково.

    Границу окна считаем от полуночи МСК того же дня, что и первая точка
    generate_series: иначе в первый день окна попадал бы хвост предыдущих
    суток и первая точка графика выглядела бы аномально низкой/высокой.

    Выручка — только внешние поступления: строки с payment_provider =
    'balance' это внутреннее движение денег (покупка с баланса), они уже
    посчитаны в момент пополнения. См. REVENUE_EXTERNAL_ONLY_SQL в
    database/analytics.py.

    Запись `col AT TIME ZONE 'Europe/Moscow'` рассчитана на TIMESTAMPTZ:
    pending_purchases.created_at и subscriptions.activated_at перевели
    миграцией 024, users.created_at — миграцией 025. Если кто-то заведёт
    здесь колонку без зоны, та же запись сдвинет сутки на три часа и
    ничего при этом не сломает — расхождение заметят по цифрам, не по
    ошибке.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH days AS (
                SELECT generate_series(
                    DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow')) - ($1::int - 1) * INTERVAL '1 day',
                    DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow')),
                    INTERVAL '1 day'
                )::date AS day
            ),
            win AS (
                SELECT (
                    (DATE_TRUNC('day', (NOW() AT TIME ZONE 'Europe/Moscow'))
                        - ($1::int - 1) * INTERVAL '1 day') AT TIME ZONE 'Europe/Moscow'
                ) AS since
            ),
            pay AS (
                SELECT DATE_TRUNC('day', created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COALESCE(SUM(price_kopecks), 0)::bigint AS revenue_kopecks,
                       COUNT(*)::int AS payments_count
                FROM pending_purchases
                WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
                  AND created_at >= (SELECT since FROM win)
                GROUP BY 1
            ),
            usr AS (
                SELECT DATE_TRUNC('day', created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COUNT(*)::int AS new_users
                FROM users
                WHERE created_at >= (SELECT since FROM win)
                GROUP BY 1
            ),
            sub AS (
                SELECT DATE_TRUNC('day', activated_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COUNT(*)::int AS new_subs,
                       COUNT(*) FILTER (WHERE source = 'payment')::int AS new_paid_subs
                FROM subscriptions
                WHERE activated_at IS NOT NULL
                  AND activated_at >= (SELECT since FROM win)
                GROUP BY 1
            )
            SELECT
                d.day,
                COALESCE(pay.revenue_kopecks, 0)  AS revenue_kopecks,
                COALESCE(pay.payments_count, 0)   AS payments_count,
                COALESCE(usr.new_users, 0)        AS new_users,
                COALESCE(sub.new_subs, 0)         AS new_subs,
                COALESCE(sub.new_paid_subs, 0)    AS new_paid_subs
            FROM days d
            LEFT JOIN pay ON pay.day = d.day
            LEFT JOIN usr ON usr.day = d.day
            LEFT JOIN sub ON sub.day = d.day
            ORDER BY d.day
            """,
            days,
        )

    series = [
        {
            "date": r["day"].isoformat(),
            "revenue_rubles": float(r["revenue_kopecks"]) / 100.0,
            "payments_count": int(r["payments_count"]),
            "new_users": int(r["new_users"]),
            "new_subscriptions": int(r["new_subs"]),
            "new_paid_subscriptions": int(r["new_paid_subs"]),
        }
        for r in rows
    ]
    # tz отдаём явно — чтобы на фронте не гадать, в каких сутках точка.
    return {"days": days, "tz": "Europe/Moscow", "series": series}


async def get_hourly_timeseries(days: int) -> Dict[str, Any]:
    """Hour-of-day аггрегаты за последние `days` суток.

    24 строки (0..23 — час Europe/Moscow). Суммируем revenue, платежи,
    новых юзеров и новые подписки. Извлекаем час из timestamptz после
    конвертации в МСК — админу удобнее видеть пики в местном времени,
    чем в UTC.

    Используем `generate_series(0, 23)` для гарантии полных 24 точек
    даже если в каком-то часу не было активности.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH hrs AS (
                SELECT generate_series(0, 23) AS hour
            ),
            pay AS (
                SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'Europe/Moscow')::int AS hour,
                       COALESCE(SUM(price_kopecks), 0)::bigint AS revenue_kopecks,
                       COUNT(*)::int AS payments_count
                FROM pending_purchases
                WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
                  AND created_at >= (NOW() AT TIME ZONE 'UTC') - $1::int * INTERVAL '1 day'
                GROUP BY 1
            ),
            usr AS (
                SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE 'Europe/Moscow')::int AS hour,
                       COUNT(*)::int AS new_users
                FROM users
                WHERE created_at >= (NOW() AT TIME ZONE 'UTC') - $1::int * INTERVAL '1 day'
                GROUP BY 1
            ),
            sub AS (
                SELECT EXTRACT(HOUR FROM activated_at AT TIME ZONE 'Europe/Moscow')::int AS hour,
                       COUNT(*)::int AS new_subs,
                       COUNT(*) FILTER (WHERE source = 'payment')::int AS new_paid_subs
                FROM subscriptions
                WHERE activated_at IS NOT NULL
                  AND activated_at >= (NOW() AT TIME ZONE 'UTC') - $1::int * INTERVAL '1 day'
                GROUP BY 1
            )
            SELECT
                hrs.hour,
                COALESCE(pay.revenue_kopecks, 0) AS revenue_kopecks,
                COALESCE(pay.payments_count, 0)  AS payments_count,
                COALESCE(usr.new_users, 0)       AS new_users,
                COALESCE(sub.new_subs, 0)        AS new_subs,
                COALESCE(sub.new_paid_subs, 0)   AS new_paid_subs
            FROM hrs
            LEFT JOIN pay ON pay.hour = hrs.hour
            LEFT JOIN usr ON usr.hour = hrs.hour
            LEFT JOIN sub ON sub.hour = hrs.hour
            ORDER BY hrs.hour
            """,
            days,
        )
    series = [
        {
            "hour": int(r["hour"]),
            "revenue_rubles": float(r["revenue_kopecks"]) / 100.0,
            "payments_count": int(r["payments_count"]),
            "new_users": int(r["new_users"]),
            "new_subscriptions": int(r["new_subs"]),
            "new_paid_subscriptions": int(r["new_paid_subs"]),
        }
        for r in rows
    ]
    return {"days": days, "tz": "Europe/Moscow", "series": series}


async def get_ltv() -> float:
    """
    Получить средний LTV (Lifetime Value) по всем платящим пользователям
    
    LTV = средняя сумма всех платежей пользователя за подписки
    
    Returns:
        Средний LTV в рублях
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем средний LTV через агрегацию (оптимизированный запрос)
        avg_ltv_kopecks = await conn.fetchval(
            """SELECT COALESCE(AVG(user_total), 0)
               FROM (
                   SELECT telegram_id, SUM(price_kopecks) as user_total
                   FROM pending_purchases
                   WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
                   GROUP BY telegram_id
               ) as user_ltvs"""
        ) or 0
        
        # PART D.8: Fix Decimal arithmetic bug
        # avg_ltv_kopecks may be Decimal from PostgreSQL
        # Use float() conversion to avoid TypeError: unsupported operand type(s) for /: 'Decimal' and 'float'
        return float(avg_ltv_kopecks) / 100.0  # Конвертируем из копеек в рубли


async def get_referral_analytics() -> Dict[str, Any]:
    """
    Получить реферальную аналитику
    
    Returns:
        Словарь с ключами:
        - referral_revenue: доход от рефералов (сумма платежей приглашенных пользователей)
        - cashback_paid: выплаченный кешбэк
        - net_profit: чистая прибыль (referral_revenue - cashback_paid)
        - referred_users_count: количество приглашенных пользователей
        - active_referrals: количество активных рефералов
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Доход от рефералов: сумма всех платежей пользователей, у которых есть referrer_id
            referral_revenue_kopecks = await conn.fetchval(
                """SELECT COALESCE(SUM(p.amount), 0)
                   FROM payments p
                   JOIN users u ON p.telegram_id = u.telegram_id
                   WHERE p.status = 'approved'
                   AND (u.referrer_id IS NOT NULL OR u.referred_by IS NOT NULL)"""
            ) or 0

            referral_revenue = referral_revenue_kopecks / 100.0

            # Выплаченный кешбэк (сумма всех транзакций типа cashback)
            cashback_paid_kopecks = await conn.fetchval(
                """SELECT COALESCE(SUM(amount), 0)
                   FROM balance_transactions
                   WHERE type = 'cashback'"""
            ) or 0

            cashback_paid = cashback_paid_kopecks / 100.0

            # Чистая прибыль
            net_profit = referral_revenue - cashback_paid

            # Количество приглашенных пользователей
            referred_users_count = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals"
            ) or 0

            # Количество активных рефералов (с активной подпиской)
            active_referrals = await conn.fetchval(
                """SELECT COUNT(DISTINCT r.referred_user_id)
                   FROM referrals r
                   JOIN subscriptions s ON r.referred_user_id = s.telegram_id
                   WHERE s.expires_at > NOW()"""
            ) or 0

            return {
                "referral_revenue": referral_revenue,
                "cashback_paid": cashback_paid,
                "net_profit": net_profit,
                "referred_users_count": referred_users_count,
                "active_referrals": active_referrals,
            }
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError) as e:
        logger.warning(f"referrals or related tables missing or inaccessible — skipping referral analytics: {e}")
        return {
            "referral_revenue": 0.0,
            "cashback_paid": 0.0,
            "net_profit": 0.0,
            "referred_users_count": 0,
            "active_referrals": 0
        }
    except Exception as e:
        logger.warning(f"Error getting referral analytics: {e}")
        return {
            "referral_revenue": 0.0,
            "cashback_paid": 0.0,
            "net_profit": 0.0,
            "referred_users_count": 0,
            "active_referrals": 0
        }


async def get_daily_summary(date: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Получить ежедневную сводку

    Args:
        date: Дата для сводки (если None, используется сегодня)

    Returns:
        Словарь с ключами: revenue, payments_count, new_users, new_subscriptions
    """
    _empty = {"date": "", "revenue": 0.0, "payments_count": 0, "new_users": 0, "new_subscriptions": 0}
    if not _core.DB_READY:
        logger.warning("DB not ready, get_daily_summary skipped")
        return _empty

    if date is None:
        date = datetime.now(timezone.utc)

    start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)

    pool = await get_pool()
    if pool is None:
        logger.warning("Pool is None, get_daily_summary skipped")
        return _empty
    async with pool.acquire() as conn:
        start_naive = _to_db_utc(start_date)
        end_naive = _to_db_utc(end_date)
        # Доход за день (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(price_kopecks), 0)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*)
               FROM subscriptions
               WHERE activated_at >= $1 AND activated_at < $2""",
            start_naive, end_naive
        ) or 0

        return {
            "date": start_date.strftime("%Y-%m-%d"),
            "revenue": revenue,
            "payments_count": payments_count,
            "new_users": new_users,
            "new_subscriptions": new_subscriptions
        }


async def get_monthly_summary(year: int, month: int) -> Dict[str, Any]:
    """
    Получить ежемесячную сводку
    
    Args:
        year: Год
        month: Месяц (1-12)
    
    Returns:
        Словарь с ключами: revenue, payments_count, new_users, new_subscriptions
    """
    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        start_naive = _to_db_utc(start_date)
        end_naive = _to_db_utc(end_date)
        # Доход за месяц (утвержденные платежи)
        revenue_kopecks = await conn.fetchval(
            """SELECT COALESCE(SUM(price_kopecks), 0)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        revenue = revenue_kopecks / 100.0
        
        # Количество платежей
        payments_count = await conn.fetchval(
            """SELECT COUNT(*)
               FROM pending_purchases
               WHERE status = 'paid' AND COALESCE(payment_provider, '') <> 'balance'
               AND created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые пользователи
        new_users = await conn.fetchval(
            """SELECT COUNT(*) 
               FROM users 
               WHERE created_at >= $1 AND created_at < $2""",
            start_naive, end_naive
        ) or 0
        
        # Новые подписки
        new_subscriptions = await conn.fetchval(
            """SELECT COUNT(*)
               FROM subscriptions
               WHERE activated_at >= $1 AND activated_at < $2""",
            start_naive, end_naive
        ) or 0

        return {
            "year": year,
            "month": month,
            "revenue": revenue,
            "payments_count": payments_count,
            "new_users": new_users,
            "new_subscriptions": new_subscriptions
        }


async def admin_delete_user_complete(telegram_id: int, admin_telegram_id: int) -> bool:
    """Удаление пользователя из БД с сохранением финансовой истории.

    ЧТО УДАЛЯЕТСЯ
        Всё, что описывает человека и его доступ: users, subscriptions,
        promo_usage_logs, user_discounts, vip_users, referrals,
        referral_rewards, broadcast_log, traffic_purchases,
        subscription_history. Плюс сущность в панели (вне транзакции).

    ЧТО ОСТАЁТСЯ И ПОЧЕМУ
        payments, pending_purchases и balance_transactions НЕ удаляются.
        Раньше удалялись — и выручка менялась задним числом: удалил админ
        одного платившего пользователя, и «Общий доход», ARPU, LTV и график
        по дням пересчитались на других числах. Отчёт за прошлый месяц
        переставал сходиться сам с собой, а понять причину по логам было
        нельзя: строк просто больше нет.

        Внешних ключей на users у этих таблиц нет, поэтому строки спокойно
        живут без пользователя. Персональных данных в них тоже нет — только
        telegram_id, суммы и тарифы.

        В audit_log записывается, сколько платёжных строк и на какую сумму
        осталось: это единственный способ потом объяснить, почему у выручки
        есть telegram_id, которого нет в users.

    Args:
        telegram_id: Telegram ID удаляемого пользователя
        admin_telegram_id: Telegram ID администратора

    Returns:
        True если пользователь был удалён, False если не найден
    """
    from database.subscriptions import _log_audit_event_atomic

    pool = await get_pool()
    uuid_to_remove = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Проверяем существование пользователя
            user_row = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1 FOR UPDATE", telegram_id
            )
            if not user_row:
                return False

            # Получаем UUID из подписки для удаления из Xray
            sub_row = await conn.fetchrow(
                "SELECT uuid FROM subscriptions WHERE telegram_id = $1", telegram_id
            )
            if sub_row and sub_row.get("uuid"):
                uuid_to_remove = sub_row["uuid"]

            # Считаем финансовый след ДО удаления — иначе объяснить
            # оставшиеся в выручке строки будет нечем.
            kept = await conn.fetchrow(
                """SELECT
                       (SELECT COUNT(*) FROM payments WHERE telegram_id = $1) AS payments_n,
                       (SELECT COALESCE(SUM(amount), 0) FROM payments
                         WHERE telegram_id = $1 AND status = 'approved') AS payments_kopecks,
                       (SELECT COUNT(*) FROM pending_purchases WHERE telegram_id = $1) AS purchases_n,
                       (SELECT COALESCE(SUM(price_kopecks), 0) FROM pending_purchases
                         WHERE telegram_id = $1 AND status = 'paid') AS purchases_kopecks,
                       (SELECT COUNT(*) FROM balance_transactions WHERE user_id = $1) AS balance_n
                """,
                telegram_id,
            )

            # Удаляем персональные данные и доступ.
            await conn.execute("DELETE FROM promo_usage_logs WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM user_discounts WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM vip_users WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM referral_rewards WHERE referrer_id = $1 OR buyer_id = $1", telegram_id)
            await conn.execute("DELETE FROM referrals WHERE referrer_user_id = $1 OR referred_user_id = $1", telegram_id)
            await conn.execute("DELETE FROM subscription_history WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM broadcast_log WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM traffic_purchases WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM subscriptions WHERE telegram_id = $1", telegram_id)
            await conn.execute("DELETE FROM users WHERE telegram_id = $1", telegram_id)

            # payments, pending_purchases и balance_transactions остаются
            # намеренно — см. докстринг. Не добавляйте сюда их удаление:
            # это молча перепишет уже сданные отчёты по выручке.

            details = (
                "Удалены персональные данные и доступ. Финансовая история сохранена: "
                f"payments={int(kept['payments_n'] or 0)} строк на "
                f"{int(kept['payments_kopecks'] or 0) / 100:.2f} ₽, "
                f"pending_purchases={int(kept['purchases_n'] or 0)} строк на "
                f"{int(kept['purchases_kopecks'] or 0) / 100:.2f} ₽, "
                f"balance_transactions={int(kept['balance_n'] or 0)} строк"
            ) if kept else "Удалены персональные данные и доступ."

            await _log_audit_event_atomic(
                conn, "admin_delete_user", admin_telegram_id, telegram_id, details,
            )

            logger.info(f"Admin {admin_telegram_id} deleted user {telegram_id} completely from DB")

    # PHASE 2 (outside transaction): Remove UUID from Xray API
    if uuid_to_remove:
        try:
            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)
            logger.info(f"ADMIN_DELETE_UUID_REMOVED uuid={uuid_to_remove[:8]}...")
        except Exception as e:
            logger.error(f"ADMIN_DELETE_UUID_REMOVAL_FAILED uuid={uuid_to_remove[:8]}... error={e}")

    # Delete Remnawave user (fire-and-forget)
    try:
        from app.services.remnawave_service import delete_remnawave_user_bg
        delete_remnawave_user_bg(telegram_id)
    except Exception as rmn_err:
        logger.warning("REMNAWAVE_ADMIN_DELETE_FAIL: tg=%s %s", telegram_id, rmn_err)

    return True


# ====================================================================================
# GIFT SUBSCRIPTIONS: Подарочные подписки
# ====================================================================================

def generate_gift_code() -> str:
    """Генерирует уникальный код подарочной подписки (12 символов, alphanumeric)."""
    import secrets
    import string
    alphabet = string.ascii_uppercase + string.digits
    # Убираем похожие символы для удобства: O/0, I/1/L
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "").replace("L", "")
    return "".join(secrets.choice(alphabet) for _ in range(12))


async def create_gift_subscription(
    buyer_telegram_id: int,
    tariff: str,
    period_days: int,
    price_kopecks: int,
    purchase_id: str,
) -> Dict[str, Any]:
    """
    Создаёт подарочную подписку после оплаты.

    Returns:
        {"gift_code": str, "id": int}
    """
    pool = await get_pool()
    gift_code = generate_gift_code()
    now = datetime.now(timezone.utc)
    # Подарок действителен 90 дней для активации
    gift_expires = now + timedelta(days=90)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO gift_subscriptions
               (gift_code, buyer_telegram_id, tariff, period_days, price_kopecks,
                purchase_id, status, created_at, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, 'paid', $7, $8)
               RETURNING id, gift_code""",
            gift_code, buyer_telegram_id, tariff, period_days, price_kopecks,
            purchase_id, _to_db_utc(now), _to_db_utc(gift_expires),
        )
    logger.info(
        f"GIFT_CREATED buyer={buyer_telegram_id} code={gift_code} "
        f"tariff={tariff} period={period_days}d"
    )
    return {"gift_code": row["gift_code"], "id": row["id"]}


async def get_gift_subscription(gift_code: str) -> Optional[Dict[str, Any]]:
    """Получает подарочную подписку по коду."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM gift_subscriptions WHERE gift_code = $1",
            gift_code.upper().strip(),
        )
    if row is None:
        return None
    d = dict(row)
    for k in ("created_at", "expires_at", "activated_at"):
        if k in d and d[k] is not None and isinstance(d[k], datetime):
            d[k] = _from_db_utc(d[k])
    return d


async def activate_gift_subscription(gift_code: str, activated_by: int) -> Dict[str, Any]:
    """
    Активирует подарочную подписку для пользователя.

    Двухфазная активация:
    - Phase 1: Проверяем подписку, при необходимости создаём UUID через VPN API (вне транзакции)
    - Phase 2: Атомарно обновляем подарок + выдаём доступ через grant_access (внутри транзакции)

    Returns:
        {"success": bool, "error": str | None, "tariff": str, "period_days": int}
    """
    from database.subscriptions import grant_access
    from database.users import process_referral_reward

    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # =========================================================================
    # PRE-CHECK: Валидация подарка (без блокировки — быстрая проверка)
    # =========================================================================
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM gift_subscriptions WHERE gift_code = $1",
            gift_code.upper().strip(),
        )
    if row is None:
        return {"success": False, "error": "not_found"}

    gift = dict(row)
    if gift["status"] == "activated":
        return {"success": False, "error": "already_activated"}
    if gift["status"] != "paid":
        return {"success": False, "error": "invalid_status"}

    expires_at = _from_db_utc(gift["expires_at"]) if gift["expires_at"] else None
    if expires_at and expires_at < now:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE gift_subscriptions SET status = 'expired' WHERE id = $1",
                gift["id"],
            )
        return {"success": False, "error": "expired"}

    if gift["buyer_telegram_id"] == activated_by:
        return {"success": False, "error": "self_activation"}

    tariff = gift["tariff"]
    period_days = gift["period_days"]
    duration = timedelta(days=period_days)

    # =========================================================================
    # PHASE 1: Провизия VPN UUID вне транзакции (если нужна новая выдача)
    # =========================================================================
    pre_provisioned = None
    if config.VPN_ENABLED:
        async with pool.acquire() as conn:
            sub_row = await conn.fetchrow(
                "SELECT status, expires_at, uuid FROM subscriptions WHERE telegram_id = $1",
                activated_by,
            )
        needs_new_issuance = True
        if sub_row:
            sub_expires = _ensure_utc(sub_row["expires_at"]) if sub_row["expires_at"] else None
            if (
                sub_row["status"] == "active"
                and sub_expires
                and sub_expires > now
                and sub_row["uuid"]
            ):
                needs_new_issuance = False

        if needs_new_issuance:
            subscription_end = now + duration
            # Task 2 cut-over: provision premium + bypass entities in
            # Remnawave instead of the legacy samopis xray master.
            from app.services import purchase_flow
            vless_result = await purchase_flow.provision_subscription(
                activated_by,
                tariff=tariff,
                subscription_end=subscription_end,
                period_days=period_days,
                is_trial=False,
            )
            pre_provisioned = {
                "uuid": vless_result["uuid"],
                "vless_url": vless_result["vless_url"],
                "vless_url_plus": vless_result.get("vless_url_plus"),
                "subscription_type": vless_result.get("subscription_type", tariff),
            }

    # =========================================================================
    # PHASE 2: Атомарная транзакция — обновление подарка + выдача доступа
    # =========================================================================
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Повторная проверка с блокировкой (защита от race condition)
            row = await conn.fetchrow(
                "SELECT * FROM gift_subscriptions WHERE gift_code = $1 FOR UPDATE",
                gift_code.upper().strip(),
            )
            if row is None:
                return {"success": False, "error": "not_found"}

            gift = dict(row)
            if gift["status"] != "paid":
                return {"success": False, "error": "already_activated" if gift["status"] == "activated" else "invalid_status"}

            # Помечаем подарок как активированный
            await conn.execute(
                """UPDATE gift_subscriptions
                   SET status = 'activated', activated_by = $1, activated_at = $2
                   WHERE id = $3""",
                activated_by, _to_db_utc(now), gift["id"],
            )

            # Активируем подписку через grant_access
            grant_result = await grant_access(
                telegram_id=activated_by,
                duration=duration,
                source="gift",
                tariff=tariff,
                conn=conn,
                _caller_holds_transaction=True,
                pre_provisioned_uuid=pre_provisioned,
            )

    logger.info(
        f"GIFT_ACTIVATED code={gift_code} by={activated_by} "
        f"tariff={tariff} period={period_days}d buyer={gift['buyer_telegram_id']}"
    )
    return {
        "success": True,
        "error": None,
        "tariff": tariff,
        "period_days": period_days,
        "grant_result": grant_result,
    }


async def get_user_gifts(telegram_id: int) -> list:
    """Получает список подарков, купленных пользователем."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM gift_subscriptions
               WHERE buyer_telegram_id = $1
               ORDER BY created_at DESC LIMIT 20""",
            telegram_id,
        )
    return [dict(r) for r in rows]


# ====================================================================
# RECOVERY: rollback of premium expireAt accidentally pushed to ~2036
#
# Bug (introduced by the admin reconcile tool): bypass-only rows in
# `subscriptions` carry expires_at = NOW + 10 years AND the original
# remnawave_premium_uuid (it's never cleared on transition). The earlier
# version of the scan treated them as active premium and PATCHed the
# panel's expireAt to 2036 — granting users a decade of free premium.
#
# To roll back, we need each user's REAL last paid premium end date,
# computed from pending_purchases (paid status, non-bypass tariff).
# ====================================================================

async def get_premium_recovery_candidates() -> list:
    """Users whose bypass-only row is still pinned to a premium uuid AND
    whose expires_at is parked in the far future (the 10-year marker).

    Returns dicts with: telegram_id, remnawave_premium_uuid, db_expires_at.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_premium_uuid, expires_at
               FROM subscriptions
               WHERE is_bypass_only = TRUE
                 AND remnawave_premium_uuid IS NOT NULL
                 AND expires_at > NOW() + INTERVAL '5 years'"""
        )
    return [dict(r) for r in rows]


async def get_user_paid_subscription_history(telegram_id: int) -> list:
    """Chronological list of the user's PAID subscription purchases
    (excludes balance top-ups, traffic packs, and pending/expired rows).

    Returns list of {created_at, period_days, tariff} in ascending order.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT created_at, period_days, tariff
               FROM pending_purchases
               WHERE telegram_id = $1
                 AND status = 'paid'
                 AND period_days > 0
                 AND tariff IN ('basic', 'plus', 'biz_starter', 'biz_team',
                                'biz_business', 'biz_pro', 'biz_enterprise',
                                'biz_ultimate')
               ORDER BY created_at ASC""",
            telegram_id,
        )
    return [dict(r) for r in rows]


async def get_paid_subscription_history_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch paid subscription history for many users in ONE query.

    Returns a dict: telegram_id -> [{created_at, period_days, tariff}, ...]
    sorted ascending by created_at. Missing users get an empty list.

    Used by the premium recovery scan instead of 1k+ separate roundtrips.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, created_at, period_days, tariff
               FROM pending_purchases
               WHERE telegram_id = ANY($1::bigint[])
                 AND status = 'paid'
                 AND period_days > 0
                 AND tariff IN ('basic', 'plus', 'biz_starter', 'biz_team',
                                'biz_business', 'biz_pro', 'biz_enterprise',
                                'biz_ultimate')
               ORDER BY telegram_id, created_at ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]].append({
            "created_at": r["created_at"],
            "period_days": r["period_days"],
            "tariff": r["tariff"],
        })
    return out


async def get_activated_gifts_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch activated gift subscriptions for users in ONE query.

    Returns dict: telegram_id -> [{activated_at, period_days}, ...]
    Ascending by activated_at. Missing users get empty list.

    Recovery uses this to honour gift subscriptions when computing
    real premium end date — paid history might be empty but a real
    gift still grants premium time.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT activated_by, activated_at, period_days
               FROM gift_subscriptions
               WHERE activated_by = ANY($1::bigint[])
                 AND status = 'activated'
                 AND activated_at IS NOT NULL
                 AND period_days > 0
               ORDER BY activated_by, activated_at ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["activated_by"]].append({
            "activated_at": r["activated_at"],
            "period_days": r["period_days"],
        })
    return out


async def get_max_subscription_end_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch the user's MAX(subscription_history.end_date) per user.

    subscription_history is the source-of-truth ledger for every
    subscription event — purchases, renewals, gifts, admin grants —
    so the maximum end_date is the user's actual last legitimate
    premium expiry, regardless of which acquisition path they came
    through. Recovery uses this as the primary signal instead of
    reconstructing dates from pending_purchases.

    Returns dict: telegram_id -> datetime | None.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, MAX(end_date) AS last_end
               FROM subscription_history
               WHERE telegram_id = ANY($1::bigint[])
               GROUP BY telegram_id""",
            telegram_ids,
        )
    out: dict = {tg: None for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]] = r["last_end"]
    return out


async def get_paid_payments_via_purchases_bulk(telegram_ids: list) -> dict:
    """Bulk-fetch settled `payments` rows joined onto pending_purchases.

    A user paid through a provider can have rows in `payments` even
    when pending_purchases status didn't flip to 'paid' for some reason
    (legacy flows, admin approve, edge-case webhooks). Joining on
    purchase_id reconstructs period_days from pending_purchases so we
    can still compute an end date.

    Returns dict: telegram_id -> [{created_at, period_days, tariff}, ...]
    ordered ascending. Used as belt-and-suspenders fallback in recovery.
    """
    if not telegram_ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.telegram_id,
                      COALESCE(p.paid_at, p.created_at) AS created_at,
                      pp.period_days,
                      COALESCE(p.tariff, pp.tariff) AS tariff
               FROM payments p
               LEFT JOIN pending_purchases pp ON pp.purchase_id = p.purchase_id
               WHERE p.telegram_id = ANY($1::bigint[])
                 AND p.status IN ('paid', 'approved')
                 AND pp.period_days IS NOT NULL
                 AND pp.period_days > 0
                 AND COALESCE(p.tariff, pp.tariff) IN
                     ('basic', 'plus', 'biz_starter', 'biz_team',
                      'biz_business', 'biz_pro', 'biz_enterprise',
                      'biz_ultimate')
               ORDER BY p.telegram_id, COALESCE(p.paid_at, p.created_at) ASC""",
            telegram_ids,
        )
    out: dict = {tg: [] for tg in telegram_ids}
    for r in rows:
        out[r["telegram_id"]].append({
            "created_at": r["created_at"],
            "period_days": r["period_days"],
            "tariff": r["tariff"],
        })
    return out



async def get_active_premium_subscribers() -> list:
    """All subscriptions currently considered active premium (NOT bypass-only).

    For the audit-tool: we want users whose premium subscription is
    nominally active in the bot's DB so we can cross-check it against
    payments and the Remnawave panel.

    Filters:
      - status='active' AND expires_at > NOW (still in their paid window)
      - NOT is_bypass_only (we never audit bypass-only rows, those are
        traffic-pack only and live on +10y by design)
      - subscription_type in the real premium tariffs

    Returns list of dicts: telegram_id, remnawave_premium_uuid,
    expires_at, subscription_type.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, remnawave_premium_uuid,
                      expires_at, subscription_type
               FROM subscriptions
               WHERE status = 'active'
                 AND expires_at > NOW()
                 AND COALESCE(is_bypass_only, FALSE) = FALSE
                 AND subscription_type IN
                     ('basic', 'plus', 'biz_starter', 'biz_team',
                      'biz_business', 'biz_pro', 'biz_enterprise',
                      'biz_ultimate')
               ORDER BY telegram_id"""
        )
    return [dict(r) for r in rows]


async def get_subscriptions_with_far_future_expires() -> list:
    """All subscriptions whose DB expires_at is parked in the far future.

    This is the symptom of the bug discovered during the audit: when a
    user's premium expired and they had a bypass entity, the
    fast_expiry_cleanup transition rewrote expires_at to NOW + 10 years
    as a bypass-only marker — but the user's subsequent purchases never
    overwrote that marker, leaving the bot UI showing "expires in 10
    years" even though the panel was rolled back to the real date.

    Returns dicts with telegram_id, expires_at, status,
    subscription_type, is_bypass_only.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT telegram_id, expires_at, status, subscription_type,
                      is_bypass_only, remnawave_premium_uuid
               FROM subscriptions
               WHERE status = 'active'
                 AND expires_at > NOW() + INTERVAL '2 years'
               ORDER BY telegram_id"""
        )
    return [dict(r) for r in rows]


async def update_subscription_expires_at_bulk(updates: list) -> int:
    """Bulk-update subscriptions.expires_at.

    Args:
        updates: list of {"telegram_id": int, "new_expires_at": datetime}

    Returns count of rows successfully updated.

    Uses asyncpg.executemany — one round-trip for the whole batch.
    """
    if not updates:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Coerce to naive UTC (the column is TIMESTAMPTZ but the bot's
        # other writers pass naive — keep consistent so equality
        # comparisons elsewhere don't drift across tz casts).
        rows = [
            (u["new_expires_at"], u["telegram_id"])
            for u in updates
        ]
        async with conn.transaction():
            await conn.executemany(
                "UPDATE subscriptions SET expires_at = $1 WHERE telegram_id = $2 AND status = 'active'",
                rows,
            )
    return len(updates)


async def get_active_trial_telegram_ids() -> list:
    """Telegram IDs of users currently on an active trial — and ONLY
    on a trial (no live PAID premium subscription).

    Trial activation writes a `subscriptions` row with source='trial',
    status='active', subscription_type='basic' (default tariff in
    grant_access), expires_at = trial end, is_bypass_only=FALSE. That
    means the "looks like an active paid sub" filter MUST exclude
    source='trial' explicitly — otherwise the audience comes out
    empty (every trial user gets filtered as if they were already
    paying).

    Time comparison uses an explicit `$1` parameter (not NOW()):
    `users.trial_expires_at` is TIMESTAMP without tz in this DB while
    `subscriptions.expires_at` is TIMESTAMPTZ. Mixing NOW() with a
    naive TIMESTAMP column triggers `operator does not exist`
    failures — the same class of error we hit before in this repo.
    `_to_db_utc` produces the naive-UTC datetime asyncpg can compare
    against both columns.

    Filters:
      - users.trial_expires_at > $1                  → trial running
      - NO subscriptions row with:
          - status='active', expires_at > $1
          - source != 'trial'                        → really paid
          - is_bypass_only=FALSE
          - subscription_type IN paid tariffs

    Returns sorted list of telegram_id integers.
    """
    pool = await get_pool()
    now = _to_db_utc(datetime.now(timezone.utc))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.telegram_id
               FROM users u
               WHERE u.trial_expires_at IS NOT NULL
                 AND u.trial_expires_at > $1
                 AND NOT EXISTS (
                     SELECT 1 FROM subscriptions s
                     WHERE s.telegram_id = u.telegram_id
                       AND s.status = 'active'
                       AND s.expires_at > $1
                       AND COALESCE(s.source, '') != 'trial'
                       AND COALESCE(s.is_bypass_only, FALSE) = FALSE
                       AND s.subscription_type IN (
                           'basic', 'plus', 'biz_starter', 'biz_team',
                           'biz_business', 'biz_pro', 'biz_enterprise',
                           'biz_ultimate'
                       )
                 )
               ORDER BY u.telegram_id""",
            now,
        )
    return [r["telegram_id"] for r in rows]
