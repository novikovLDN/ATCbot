"""Выдача и продление доступа — единственная точка, где рождается UUID.

ЧТО ЗДЕСЬ
    Одна функция, grant_access, и весь её объём — это разбор случаев:
    продление тем же тарифом, Basic→Plus, Plus→Basic, новая выдача,
    отложенная активация при недоступной панели.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ
    Тысяча строк в одном файле с проведением оплаты означала, что любая
    правка выдачи требовала не задеть finalize_purchase и наоборот.
    Функцию НЕ разрезали на части: у всех веток общий пролог (определение
    состояния подписки), общий finally (возврат соединения в пул) и общий
    обработчик ошибок. Разнести их по функциям — отдельная работа с
    отдельной проверкой, а не побочный эффект переезда файла.

ЖЕЛЕЗНЫЕ ПРАВИЛА (нарушение каждого уже стоило инцидента)
    1. UUID НЕ меняется, пока подписка активна. Продление только двигает
       expires_at — соединение пользователя не рвётся.
    2. UUID удаляется сразу при истечении.
    3. Админская выдача ведёт себя ровно как платная.
    4. Истёкшая подписка + новая покупка = новый UUID.
    5. Провижининг в панели НИКОГДА не выполняется внутри чужой
       транзакции. Если вызывающий держит транзакцию, он обязан передать
       pre_provisioned_uuid — иначе функция падает с INVARIANT_VIOLATION.
       Это не паранойя: HTTP внутри транзакции держит соединение пула, а
       откат оставляет созданную сущность сиротой в панели.

ЧТО ЛЕГКО СЛОМАТЬ
    Флаги reminder_* сбрасываются при КАЖДОЙ выдаче и продлении. Они
    означают «напоминание об окончании ЭТОГО срока уже ушло». Забудешь
    сбросить — человек получит предупреждение один раз за всю жизнь и
    больше никогда.

    Синхронизация с панелью после продления. Когда транзакцию держит
    вызывающий, функция НЕ ходит в панель сама, а возвращает
    renewal_panel_sync_after_commit — вызывающий обязан вызвать
    purchase_flow.sync_renewal_to_remnawave уже после commit. Проигноришь
    этот ключ — в базе срок продлён, в панели нет, доступ отвалится в
    старую дату.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# config и asyncio НЕ импортируются здесь намеренно: grant_access делает
# `import config` и `import asyncio` внутри своего тела. Из-за этого оба
# имени становятся локальными для всей функции, и модульный импорт был бы
# просто мёртвым — его всё равно затеняет локальный. Раскомментируешь
# сверху и уберёшь снизу — получишь UnboundLocalError на ветках, которые
# до внутреннего import не доходят.
from database.core import (
    get_pool,
    _to_db_utc,
    _from_db_utc,
    _ensure_utc,
    _generate_subscription_uuid,
)
from database.subscription_audit import (
    _notify_watchdog_expires_at,
    _log_audit_event_atomic,
    _log_subscription_history_atomic,
    _log_vpn_lifecycle_audit_async,
)

logger = logging.getLogger(__name__)


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
            incoming_tariff = (tariff.strip().lower() if tariff else None) or current_sub_type

            # Basic→Plus upgrade: tariffs live only in subscription_type
            # (Remnawave entity is identical for both tariffs).  Just flip
            # the column, extend dates, and let the standard renewal sync
            # update Remnawave premium expireAt + top-up bypass with the
            # plus-tier traffic limits.  NO legacy Xray call — the
            # upgrade_vless_user path 404s after the Remnawave cut-over
            # and was the root cause of "Basic→Plus upgrade failed:
            # User not found for upgrade" alerts.
            if source == "payment" and incoming_tariff == "plus" and current_sub_type == "basic":
                logger.info(
                    f"grant_access: BASIC_TO_PLUS_UPGRADE [user={telegram_id}, uuid={uuid[:8]}..., source={source}]"
                )
                try:
                    old_expires_at = expires_at
                    subscription_end = max(expires_at, now) + duration
                    _start_raw = subscription.get("activated_at") or subscription.get("expires_at") or now
                    subscription_start = _ensure_utc(_start_raw) if _start_raw else now
                    if subscription_end <= old_expires_at:
                        raise Exception(f"Invalid upgrade: new_end={subscription_end} <= old_end={old_expires_at}")
                    await conn.execute(
                        """UPDATE subscriptions
                           SET expires_at = $1, subscription_type = 'plus',
                               status = 'active', source = $2,
                               reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE,
                               reminder_3h_sent = FALSE, reminder_6h_sent = FALSE,
                               reminder_7d_sent = FALSE, reminder_1d_sent = FALSE, activation_status = 'active'
                           WHERE telegram_id = $3""",
                        _to_db_utc(subscription_end), source, telegram_id
                    )
                    _notify_watchdog_expires_at(
                        telegram_id,
                        grant_action="basic_to_plus_upgrade",
                        old_expires_at=old_expires_at,
                        new_expires_at=subscription_end,
                        source=source, tariff="plus",
                        admin_telegram_id=admin_telegram_id,
                        admin_grant_days=admin_grant_days,
                    )
                    vpn_key_existing = subscription.get("vpn_key")
                    vpn_key_plus_existing = subscription.get("vpn_key_plus")
                    await _log_subscription_history_atomic(conn, telegram_id, vpn_key_existing or uuid, subscription_start, subscription_end, "renewal")
                    logger.info(
                        f"grant_access: BASIC_TO_PLUS_UPGRADE_SUCCESS [user={telegram_id}, uuid={uuid[:8]}..., "
                        f"new_expires={subscription_end.isoformat()}]"
                    )
                    result_dict = {
                        "uuid": uuid,
                        "vless_url": vpn_key_existing,
                        "vpn_key": vpn_key_existing,
                        "vpn_key_plus": vpn_key_plus_existing,
                        "subscription_end": subscription_end,
                        "action": "renewal",
                        "subscription_type": "plus",
                        "is_basic_to_plus_upgrade": True,
                    }
                    # Same post-commit / inline Remnawave sync pattern as
                    # the normal renewal branch below — passes the NEW
                    # tariff so bypass top-up uses plus-tier limits.
                    _upgrade_period_days = max(1, int(duration.total_seconds() // 86400))
                    if _caller_holds_transaction:
                        result_dict["renewal_panel_sync_after_commit"] = {
                            "telegram_id": telegram_id,
                            "uuid": uuid,
                            "subscription_end": subscription_end,
                            "tariff": "plus",
                            "period_days": _upgrade_period_days,
                        }
                        return result_dict
                    from app.services import purchase_flow
                    await purchase_flow.sync_renewal_to_remnawave({
                        "telegram_id": telegram_id,
                        "uuid": uuid,
                        "subscription_end": subscription_end,
                        "tariff": "plus",
                        "period_days": _upgrade_period_days,
                    })
                    return result_dict
                except Exception as e:
                    logger.error(f"grant_access: BASIC_TO_PLUS_UPGRADE_FAILED [user={telegram_id}, error={e}]")
                    raise Exception(f"Basic→Plus upgrade failed: {e}") from e

            # Plus→Basic downgrade: symmetric — bot-side tariff flip only,
            # no panel-side change.  Remnawave entity stays identical.
            if source == "payment" and incoming_tariff == "basic" and current_sub_type == "plus":
                logger.info(
                    f"grant_access: PLUS_TO_BASIC_DOWNGRADE [user={telegram_id}, uuid={uuid[:8]}..., source={source}]"
                )
                old_expires_at = expires_at
                subscription_end = max(expires_at, now) + duration
                _start_raw = subscription.get("activated_at") or subscription.get("expires_at") or now
                subscription_start = _ensure_utc(_start_raw) if _start_raw else now
                if subscription_end <= old_expires_at:
                    raise Exception(f"Invalid downgrade: new_end={subscription_end} <= old_end={old_expires_at}")
                vpn_key_existing = subscription.get("vpn_key")
                vpn_key_plus_existing = subscription.get("vpn_key_plus")
                await conn.execute(
                    """UPDATE subscriptions
                       SET expires_at = $1, subscription_type = 'basic',
                           status = 'active', source = $2,
                           reminder_sent = FALSE, reminder_3d_sent = FALSE, reminder_24h_sent = FALSE,
                           reminder_3h_sent = FALSE, reminder_6h_sent = FALSE,
                           reminder_7d_sent = FALSE, reminder_1d_sent = FALSE, activation_status = 'active'
                       WHERE telegram_id = $3""",
                    _to_db_utc(subscription_end), source, telegram_id
                )
                _notify_watchdog_expires_at(
                    telegram_id,
                    grant_action="plus_to_basic_downgrade",
                    old_expires_at=old_expires_at,
                    new_expires_at=subscription_end,
                    source=source, tariff="basic",
                    admin_telegram_id=admin_telegram_id,
                    admin_grant_days=admin_grant_days,
                )
                await _log_subscription_history_atomic(conn, telegram_id, vpn_key_existing or uuid, subscription_start, subscription_end, "renewal")
                logger.info(
                    f"grant_access: PLUS_TO_BASIC_DOWNGRADE_SUCCESS [user={telegram_id}, uuid={uuid[:8]}..., "
                    f"new_expires={subscription_end.isoformat()}]"
                )
                result_dict = {
                    "uuid": uuid,
                    "vless_url": vpn_key_existing,
                    "vpn_key": vpn_key_existing,
                    "vpn_key_plus": vpn_key_plus_existing,
                    "subscription_end": subscription_end,
                    "action": "renewal",
                    "subscription_type": "basic",
                }
                _downgrade_period_days = max(1, int(duration.total_seconds() // 86400))
                if _caller_holds_transaction:
                    result_dict["renewal_panel_sync_after_commit"] = {
                        "telegram_id": telegram_id,
                        "uuid": uuid,
                        "subscription_end": subscription_end,
                        "tariff": "basic",
                        "period_days": _downgrade_period_days,
                    }
                    return result_dict
                from app.services import purchase_flow
                await purchase_flow.sync_renewal_to_remnawave({
                    "telegram_id": telegram_id,
                    "uuid": uuid,
                    "subscription_end": subscription_end,
                    "tariff": "basic",
                    "period_days": _downgrade_period_days,
                })
                return result_dict

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
                # Bypass-only: фиктивный expires_at (10 лет), считаем от now
                _is_bypass = subscription.get("is_bypass_only", False)
                if _is_bypass:
                    subscription_end = now + duration
                else:
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
                
                # ПРОДЛЕНИЕ В ДВЕ ФАЗЫ: сначала БД (источник истины),
                # синхронизация с панелью — ВНЕ транзакции.
                #
                # Обращение к панели идёт по сети и может ждать секунды.
                # Сделать его внутри открытой транзакции значит держать
                # соединение пула и блокировки строк всё это время —
                # при нескольких продлениях подряд пул исчерпывается.
                #
                # Поэтому: если транзакцию держит вызывающий, возвращаем
                # ему renewal_panel_sync_after_commit, и он вызывает
                # purchase_flow.sync_renewal_to_remnawave уже после commit.
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
                               reminder_7d_sent = FALSE,
                               reminder_1d_sent = FALSE,
                               activation_status = 'active',
                               is_bypass_only = FALSE
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

                    _notify_watchdog_expires_at(
                        telegram_id,
                        grant_action="renewal",
                        old_expires_at=old_expires_at,
                        new_expires_at=subscription_end,
                        source=source, tariff=incoming_tariff,
                        admin_telegram_id=admin_telegram_id,
                        admin_grant_days=admin_grant_days,
                    )
                    
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
                # Task 2 cut-over: renewal extends the Remnawave entities
                # (premium expireAt + bypass top-up) instead of syncing to
                # the legacy samopis xray master.
                _renewal_period_days = max(1, int(duration.total_seconds() // 86400))
                if _caller_holds_transaction:
                    # provision_subscription opens its own connections — it
                    # MUST run post-commit, never inside the caller's tx.
                    result_dict["renewal_panel_sync_after_commit"] = {
                        "telegram_id": telegram_id,
                        "uuid": uuid,
                        "subscription_end": subscription_end,
                        "tariff": incoming_tariff,
                        "period_days": _renewal_period_days,
                    }
                    return result_dict
                # Standalone: no transaction held — safe to sync inline.
                from app.services import purchase_flow
                await purchase_flow.sync_renewal_to_remnawave({
                    "telegram_id": telegram_id,
                    "uuid": uuid,
                    "subscription_end": subscription_end,
                    "tariff": incoming_tariff,
                    "period_days": _renewal_period_days,
                })
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
                           reminder_7d_sent = FALSE,
                           reminder_1d_sent = FALSE,
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
                           subscription_type = COALESCE($7, subscriptions.subscription_type),
                           is_bypass_only = FALSE""",
                    telegram_id, _to_db_utc(subscription_end), source, admin_grant_days, _to_db_utc(subscription_start), country, pending_sub_type,
                )
                _notify_watchdog_expires_at(
                    telegram_id,
                    grant_action="new_issuance_pending",
                    old_expires_at=None,
                    new_expires_at=subscription_end,
                    source=source, tariff=pending_sub_type,
                    admin_telegram_id=admin_telegram_id,
                    admin_grant_days=admin_grant_days,
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
                    # Task 2 cut-over: the bot is fully on Remnawave.  We
                    # provision two entities (premium + bypass) and never
                    # dial the legacy samopis xray master.
                    # provision_subscription returns the SAME shape as
                    # vpn_utils.add_vless_user used to so the surrounding
                    # code path (DB INSERT, retry loop, audit logging) is
                    # unchanged.
                    from app.services import purchase_flow
                    _is_trial = (source == "trial")
                    _period_days = max(1, int(duration.total_seconds() // 86400))
                    vless_result = await purchase_flow.provision_subscription(
                        telegram_id,
                        tariff=tariff or "basic",
                        subscription_end=subscription_end,
                        period_days=_period_days,
                        is_trial=_is_trial,
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

            # Все флаги reminder_* сбрасываются при каждой выдаче/продлении:
            # они означают «напоминание об окончании ЭТОГО срока уже ушло».
            # Не сбросить флаг = человек получит напоминание один раз за всю
            # жизнь, а при следующих продлениях останется без предупреждения.
            #
            # reminder_7d_sent и reminder_1d_sent появились позже (миграция
            # 036) и в сброс не попали — их выставляли в TRUE, а в FALSE не
            # возвращал никто. Новые колонки в INSERT не перечисляем: DEFAULT
            # FALSE, а важен именно DO UPDATE ниже.
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
                       reminder_7d_sent = FALSE,
                       reminder_1d_sent = FALSE,
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
                       country = COALESCE($11, subscriptions.country),
                       is_bypass_only = FALSE""",
                *args
            )
            _notify_watchdog_expires_at(
                telegram_id,
                grant_action="new_issuance",
                old_expires_at=None,
                new_expires_at=subscription_end,
                source=source, tariff=subscription_type_value,
                admin_telegram_id=admin_telegram_id,
                admin_grant_days=admin_grant_days,
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
