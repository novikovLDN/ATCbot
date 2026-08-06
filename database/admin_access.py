"""Админ меняет доступ пользователя: выдать, отозвать, удалить.

ПОЧЕМУ ВЫДЕЛЕНО
    Это единственные функции бывшего database/admin.py, которые меняют
    состояние доступа и трогают внешнюю панель. Всё остальное там было
    чтением — отчёты, выгрузки, аудит. Держать их в одном файле означало
    править двухфазную выдачу подписки посреди SQL для графиков.

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

ЧТО ЛЕГКО СЛОМАТЬ
    Порядок фаз. Любой внешний HTTP-вызов, затащенный внутрь
    `async with conn.transaction()`, держит транзакцию открытой на время
    сети и при откате оставляет живую сущность в панели — то есть человека
    с работающим VPN, которого нет в базе.

    Импорты database.subscriptions здесь локальные, внутри функций, и это
    намеренно: subscriptions импортирует пакет database, а тот — этот
    модуль. Импорт наверху замкнёт кольцо и уронит старт бота.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import config
import vpn_utils
from database.core import get_pool, _to_db_utc, _from_db_utc

logger = logging.getLogger(__name__)


async def _cleanup_orphan_after_rollback(
    telegram_id: int,
    connection_uuid: str,
    reason: str,
    error: BaseException,
) -> None:
    """Убрать из панели сущность, созданную фазой 1, когда транзакция откатилась.

    Раньше на этом месте стояла vpn_utils.safe_remove_vless_user_with_retry —
    заглушка снятого с эксплуатации xray. Она всегда возвращала успех, а
    запись ORPHAN_PREVENTED утверждала, что сироту предотвратили: сущность
    при этом оставалась в панели, и человек, за которого никто не заплатил,
    продолжал пользоваться платным VPN до её expireAt.

    ЗВАТЬ ТОЛЬКО ПОСЛЕ ВЫХОДА ИЗ БЛОКА СОЕДИНЕНИЯ. Внутри except транзакция
    ещё открыта (откатится она при выходе из `async with`), и сетевой вызов
    оттуда держал бы её и соединение пула всё время запроса — ровно то, от
    чего защищает двухфазный порядок, описанный в шапке модуля.
    """
    from app.services.orphan_cleanup import delete_orphan_premium_entity

    deleted, entity = await delete_orphan_premium_entity(telegram_id, connection_uuid)
    entity_id = entity or connection_uuid
    uuid_preview = f"{entity_id[:8]}..." if len(entity_id) > 8 else "***"
    if deleted:
        logger.critical(
            f"ORPHAN_DELETED uuid={uuid_preview} reason={reason} "
            f"user={telegram_id} error={error}"
        )
    else:
        logger.critical(
            f"ORPHAN_NOT_CLEANED uuid={uuid_preview} reason={reason} "
            f"user={telegram_id} error={error} — платный доступ остался у "
            f"человека, которому его не выдали, удалите сущность в панели вручную"
        )


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
        # Что придётся убрать из панели, если транзакция не пройдёт. Сам вызов
        # к панели делается ниже, ЗА пределами блока соединения: except-ветки
        # выполняются внутри ещё открытой транзакции, и HTTP оттуда держал бы
        # её и соединение пула всё время сети.
        orphan_after_rollback = None
        try:
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
                                orphan_after_rollback = (
                                    uuid_to_cleanup_on_failure,
                                    "admin_grant_access_atomic_tx_failed",
                                    e,
                                )
                            logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                            raise
                    except Exception as e:
                        last_error = e
                        if uuid_to_cleanup_on_failure:
                            orphan_after_rollback = (
                                uuid_to_cleanup_on_failure,
                                "admin_grant_access_atomic_tx_failed",
                                e,
                            )
                        logger.exception(f"Error in admin_grant_access_atomic for user {telegram_id}, transaction rolled back")
                        raise
        except Exception:
            # Транзакция откачена, соединение отдано в пул — только теперь
            # идём в панель. Компенсация не имеет права подменить исходную
            # ошибку: она внутри себя ничего не бросает.
            if orphan_after_rollback:
                await _cleanup_orphan_after_rollback(telegram_id, *orphan_after_rollback)
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
            # «Пропущено», а не «удалено»: старый uuid — это vlessUuid
            # прошлой выдачи, provision_subscription переиспользует ту же
            # premium-сущность и вшивает в неё этот же uuid. Отдельной
            # сущности под ним нет, а под вызовом — заглушка снятого xray.
            logger.info(
                "OLD_UUID_REMOVAL_SKIPPED",
                extra={"uuid": old_uuid[:8] + "...", "reason": "panel_entity_reused"}
            )
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
            from database.subscriptions import set_combo_flag

            # Флаг ставим до начисления и независимо от его исхода: даже если
            # пакет ГБ не доехал, подписка остаётся комбо — иначе автопродление
            # спишет цену обычного тарифа.
            await set_combo_flag(telegram_id, True)

            # Объём пакета считает app/services/combo_traffic — единственное
            # место, где он берётся из COMBO_TARIFFS. Здесь была своя копия
            # расчёта и начисления; копии расходились молча.
            from app.services.combo_traffic import grant_combo_traffic
            outcome = await grant_combo_traffic(
                telegram_id,
                requested_tariff,
                days,
                is_combo=True,
                purchase_id=f"admin_grant_{telegram_id}",
                subscription_end=expires_at_granted,
                source="admin_grant",
            )
            if not outcome.granted:
                logger.error(
                    "ADMIN_GRANT_COMBO_BYPASS_FAILED user=%s tariff=%s days=%s reason=%s "
                    "— подписка выдана, трафик нужно доначислить вручную",
                    telegram_id, requested_tariff, days, outcome.reason,
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
        # См. комментарий в admin_grant_access_atomic: компенсацию отката
        # делаем после выхода из блока соединения, а не в except внутри
        # открытой транзакции.
        orphan_after_rollback = None
        try:
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
                                orphan_after_rollback = (
                                    uuid_to_cleanup_on_failure,
                                    "admin_grant_access_minutes_atomic_tx_failed",
                                    e,
                                )
                            logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                            raise
                    except Exception as e:
                        last_error = e
                        if uuid_to_cleanup_on_failure:
                            orphan_after_rollback = (
                                uuid_to_cleanup_on_failure,
                                "admin_grant_access_minutes_atomic_tx_failed",
                                e,
                            )
                        logger.exception(f"Error in admin_grant_access_minutes_atomic for user {telegram_id}, transaction rolled back")
                        raise
        except Exception:
            if orphan_after_rollback:
                await _cleanup_orphan_after_rollback(telegram_id, *orphan_after_rollback)
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
            # «Пропущено», а не «удалено»: старый uuid — это vlessUuid
            # прошлой выдачи, provision_subscription переиспользует ту же
            # premium-сущность и вшивает в неё этот же uuid. Отдельной
            # сущности под ним нет, а под вызовом — заглушка снятого xray.
            logger.info(
                "OLD_UUID_REMOVAL_SKIPPED",
                extra={"uuid": old_uuid[:8] + "...", "reason": "panel_entity_reused"}
            )
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
                # Под вызовом нет действия: vpn_utils.remove_vless_user —
                # заглушка снятого с эксплуатации xray. ADMIN_REVOKE_UUID_REMOVED
                # утверждала удаление, которого не бывает никогда, и при разборе
                # «отозвали, а VPN работает» уводила в «человек путает».
                # Настоящий отзыв — disable_premium_user выше по функции.
                logger.info(
                    "ADMIN_REVOKE_LEGACY_UUID_CLEARED user=%s uuid=%s — xray-заглушка, "
                    "ничего не удалено; доступ снимает disable_premium_user",
                    telegram_id, uuid_to_remove[:8],
                )
            except Exception as e:
                logger.critical(
                    "ADMIN_REVOKE_UUID_REMOVAL_FAILED user=%s uuid=%s error=%s",
                    telegram_id, uuid_to_remove[:8], str(e)[:200],
                )
    return ret


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

    # ФАЗА 2 (вне транзакции): легаси-путь по uuid подключения.
    #
    # Под вызовом стоит заглушка снятого с эксплуатации xray — удаления не
    # происходит. Запись ADMIN_DELETE_UUID_REMOVED утверждала обратное, и
    # разбор «удалили пользователя, а VPN у него работает» упирался в неё как
    # в доказательство, что доступ снят. Сущность в панели убирает
    # delete_remnawave_user_bg ниже.
    if uuid_to_remove:
        try:
            await vpn_utils.safe_remove_vless_user_with_retry(uuid_to_remove)
            logger.info(
                f"ADMIN_DELETE_LEGACY_UUID_CLEARED uuid={uuid_to_remove[:8]}... — "
                f"xray-заглушка, ничего не удалено; сущность в панели убирает "
                f"delete_remnawave_user_bg"
            )
        except Exception as e:
            logger.error(f"ADMIN_DELETE_UUID_REMOVAL_FAILED uuid={uuid_to_remove[:8]}... error={e}")

    # Delete Remnawave user (fire-and-forget)
    try:
        from app.services.remnawave_service import delete_remnawave_user_bg
        delete_remnawave_user_bg(telegram_id)
    except Exception as rmn_err:
        logger.warning("REMNAWAVE_ADMIN_DELETE_FAIL: tg=%s %s", telegram_id, rmn_err)

    return True
