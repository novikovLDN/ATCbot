"""Перевыпуск ключа: старая сущность в панели умирает, выдаётся новая.

ЧТО ЗДЕСЬ
    reissue_vpn_key_atomic   перевыпуск по telegram_id (админская кнопка,
                             массовые прогоны) — три фазы, см. ниже
    reissue_subscription_key перевыпуск по subscription_id (сервисный путь)

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ
    Перевыпуск — единственная операция, которая осознанно РВЁТ работающее
    соединение пользователя: старая ссылка перестаёт работать сразу. Он не
    продлевает срок, не берёт денег и правится по своим поводам (инциденты
    с утёкшими ключами, смена ноды). Держать его посреди выдачи и оплаты
    значило бы, что каждый разбор «почему у человека отвалился VPN»
    начинается с чтения finalize_purchase.

ТРИ ФАЗЫ И ПОЧЕМУ ИМЕННО ТАК
    Фаза 1 — короткое соединение: прочитать строку подписки и отпустить.
    Фаза 2 — HTTP к панели, соединения пула на руках НЕТ.
    Фаза 3 — новое соединение, транзакция с pg_advisory_xact_lock,
             перепроверка uuid и UPDATE.

    Раньше всё это шло под одним session-level локом и одним соединением,
    включая поход в панель — до нескольких секунд на пользователя. Пара
    админов или массовый перевыпуск выедали пул, и деградировали
    пользовательские хендлеры.

ЧТО ЛЕГКО СЛОМАТЬ
    Снятый session-лок страховал от двойного перевыпуска целиком, включая
    сетевую фазу. Взамен фаза 3 под xact-локом сверяет uuid: если пока мы
    ходили в панель кто-то уже перевыпустил ключ, UPDATE не применяется, а
    свежесозданная сущность удаляется тем же обработчиком, что и при любом
    другом сбое фазы 3. Уберёшь сверку uuid — получишь чужую сущность
    сиротой в панели и ключ, который затрут через секунду.

    Тесты (tests/services/test_reissue_pool_phases.py) подменяют get_pool и
    функции журнала КАК АТРИБУТЫ ЭТОГО МОДУЛЯ. Если перенести
    reissue_vpn_key_atomic куда-то ещё, подмена перестанет действовать
    молча — тест продолжит проходить, проверяя не тот код.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import config
from database.core import (
    get_pool,
    _to_db_utc,
    _ensure_utc,
    _generate_subscription_uuid,
)
from database.subscription_audit import (
    _log_audit_event_atomic,
    _log_subscription_history_atomic,
    _log_vpn_lifecycle_audit_async,
)
from database.subscription_queries import get_active_subscription
from database.subscription_state import update_subscription_uuid

logger = logging.getLogger(__name__)


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
    
    # Перевыпуск идёт через Remnawave.
    #
    # Раньше здесь вызывался vpn_utils.reissue_vpn_access, который внутри
    # обращается к add_vless_user. После снятия samopis xray эта функция стала
    # заглушкой и возвращает пустой vless_url, а следом стоит проверка
    # «пустая ссылка — ошибка». То есть админский перевыпуск ключа падал
    # гарантированно, при любом состоянии системы.
    #
    # reissue_premium_user_entity удаляет старую сущность в панели и создаёт
    # новую: старая ссылка перестаёт работать, выдаётся свежая — ровно то,
    # чего ждут от перевыпуска.
    try:
        from app.services import remnawave_premium

        new_uuid = _generate_subscription_uuid()
        result = await remnawave_premium.reissue_premium_user_entity(
            telegram_id,
            requested_uuid=new_uuid,
            expire_at=expires_at,
            description="Premium reissued by admin",
        )
        if not result.ok or not result.subscription_url:
            error_msg = (
                f"Remnawave reissue failed: status={getattr(result, 'status', None)} "
                f"error={getattr(result, 'error', None)}"
            )
            raise RuntimeError(error_msg)
        # Панель — источник истины по идентификатору сущности.
        new_uuid = result.panel_uuid or new_uuid
        vless_url = result.subscription_url
    except Exception as e:
        logger.error(
            f"reissue_subscription_key: REMNAWAVE_REISSUE_FAILED [subscription_id={subscription_id}, "
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


async def reissue_vpn_key_atomic(
    telegram_id: int,
    admin_telegram_id: int,
    correlation_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Атомарно перевыпустить VPN-ключ для пользователя.

    POOL_STABILITY: соединение пула НЕ удерживается во время похода в панель.
    Раньше функция брала conn, вешала session-level pg_advisory_lock и внутри
    этого блока делала DELETE + preflight + POST к Remnawave — до нескольких
    секунд на одного пользователя. Массовый перевыпуск или пара админов
    одновременно выедали пул, и деградировали пользовательские хендлеры.
    Разнесено на фазы по образцу app/services/activation/service.py:

      Фаза 1 — короткое соединение: прочитать строку подписки, отпустить conn.
      Фаза 2 — HTTP к панели (соединения пула на руках нет).
      Фаза 3 — новое соединение, транзакция с pg_advisory_xact_lock,
               перепроверка состояния и UPDATE.

    Про снятый session-lock. Он охватывал и сетевую фазу, то есть страховал
    от двойного перевыпуска целиком. Взамен в фазе 3 под xact-локом сверяем
    uuid: если пока мы ходили в панель кто-то уже перевыпустил ключ, наш UPDATE
    не применяется, а свежесозданная сущность удаляется тем же обработчиком,
    что и при любом другом сбое фазы 3. Двойной записи в базу не будет, сирота
    в панели не останется.
    """
    pool = await get_pool()

    # ── Фаза 1: читаем состояние и сразу отпускаем соединение ────────
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
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

    # ── Фаза 2 (вне транзакции И вне удерживаемого соединения) ───────
    # Task 2 cut-over: delete the user's current Remnawave premium
    # entity and create a fresh one — the old subscription URL and
    # connection UUID stop working, exactly like the legacy
    # add_vless_user + remove-old-uuid flow did.
    from app.services import remnawave_premium
    import uuid as _uuid_lib
    new_uuid = str(_uuid_lib.uuid4())
    reissue_result = await remnawave_premium.reissue_premium_user_entity(
        telegram_id,
        requested_uuid=new_uuid,
        expire_at=expires_at,
        description=f"Premium reissued via bot ({reissue_tariff})",
    )
    if not reissue_result.ok:
        raise RuntimeError(
            f"Remnawave premium reissue failed: status={reissue_result.status} "
            f"error={reissue_result.error}"
        )
    new_vpn_key = reissue_result.subscription_url
    if not new_vpn_key:
        raise RuntimeError("Remnawave reissue returned empty subscription URL")

    logger.info(
        "REISSUE_TWO_PHASE_ACTIVATION",
        extra={"user": telegram_id, "new_uuid": new_uuid[:8] + "...", "phase": "phase1_complete"}
    )

    # On Phase-3 failure we delete the freshly-created panel entity
    # (its panel UUID, not a samopis uuid).
    uuid_to_cleanup_on_failure = reissue_result.panel_uuid

    # ── Фаза 3: короткая транзакция под advisory-локом ───────────────
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", telegram_id)
                sub_check = await conn.fetchrow(
                    """SELECT telegram_id, uuid FROM subscriptions
                       WHERE telegram_id = $1 AND status = 'active' AND expires_at > $2""",
                    telegram_id, _to_db_utc(now)
                )
                if not sub_check:
                    raise Exception("Subscription no longer active")
                # Пока мы ходили в панель, строку мог переписать параллельный
                # перевыпуск. Тогда наш new_uuid уже неактуален: применять его
                # поверх чужого — значит оставить чужую панельную сущность
                # сиротой и выдать человеку ключ, который через секунду
                # затрут. Отказываемся и уходим в cleanup ниже.
                if sub_check["uuid"] != old_uuid:
                    raise Exception(
                        "Concurrent reissue detected: uuid changed while calling panel"
                    )
                new_subscription_type = reissue_tariff
                if new_subscription_type not in config.VALID_SUBSCRIPTION_TYPES:
                    new_subscription_type = "basic"
                await conn.execute(
                    """UPDATE subscriptions
                       SET uuid = $1, vpn_key = $2, subscription_type = $4,
                           remnawave_premium_uuid = $5,
                           remnawave_premium_sub_url = $6,
                           remnawave_premium_short_uuid = $7
                       WHERE telegram_id = $3""",
                    new_uuid, new_vpn_key, telegram_id, new_subscription_type,
                    reissue_result.panel_uuid,
                    reissue_result.subscription_url,
                    reissue_result.short_uuid,
                )
                await _log_subscription_history_atomic(conn, telegram_id, new_vpn_key, now, expires_at, "manual_reissue")
                old_key_preview = f"{old_vpn_key[:20]}..." if old_vpn_key and len(old_vpn_key) > 20 else (old_vpn_key or "N/A")
                new_key_preview = f"{new_vpn_key[:20]}..." if new_vpn_key and len(new_vpn_key) > 20 else (new_vpn_key or "N/A")
                details = f"User {telegram_id}, Old key: {old_key_preview}, New key: {new_key_preview}, Expires: {expires_at.isoformat()}"
                await _log_audit_event_atomic(conn, "admin_reissue", admin_telegram_id, telegram_id, details)
        except Exception as tx_err:
            # Фаза 3 (база) не прошла — свежая premium-сущность из фазы 2
            # осталась в панели сиротой; удаляем её.
            try:
                from app.services import remnawave_api
                if uuid_to_cleanup_on_failure:
                    await remnawave_api.delete_user(uuid_to_cleanup_on_failure)
                logger.critical(
                    f"ORPHAN_PREVENTED uuid={(uuid_to_cleanup_on_failure or '')[:8]}... reason=reissue_phase2_failed "
                    f"user={telegram_id} error={tx_err}"
                )
            except Exception as remove_err:
                logger.critical(
                    f"ORPHAN_PREVENTED_REMOVAL_FAILED uuid={(uuid_to_cleanup_on_failure or '')[:8]}... "
                    f"reason={remove_err} user={telegram_id}"
                )
            logger.exception(f"Error in reissue_vpn_key_atomic for user {telegram_id}, transaction rolled back")
            raise

    # The old premium entity was already deleted in Phase 2 by
    # reissue_premium_user_entity — no separate teardown needed here.
    if old_uuid:
        try:
            await _log_vpn_lifecycle_audit_async(
                action="vpn_remove_user",
                telegram_id=telegram_id,
                uuid=old_uuid,
                source="admin_reissue",
                result="success",
                details=f"Old premium entity deleted during reissue, expires_at={expires_at.isoformat()}"
            )
        except Exception:
            pass

    new_uuid_preview = f"{new_uuid[:8]}..." if len(new_uuid) > 8 else "***"
    logger.info(
        f"VPN key reissued [action=admin_reissue, user={telegram_id}, admin={admin_telegram_id}, "
        f"new_uuid={new_uuid_preview}]",
        extra={"correlation_id": correlation_id} if correlation_id else {}
    )
    return new_vpn_key, old_vpn_key
