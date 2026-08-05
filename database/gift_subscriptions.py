"""Подарочные подписки: код купил один, активировал другой.

ЧТО ЗДЕСЬ
    Весь жизненный цикл строки gift_subscriptions: сгенерировать код после
    оплаты, отдать его покупателю, активировать у получателя, показать список
    купленных подарков.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ
    Подарок — не админское действие, хотя код годами лежал в database/admin.py.
    Его покупает и активирует обычный пользователь, а правят его тогда, когда
    меняется сценарий покупки, а не когда меняются отчёты админки.

ДВЕ ФАЗЫ ПРИ АКТИВАЦИИ
    Сущность в панели создаётся ДО открытия транзакции (Phase 1), и только
    потом одной транзакцией помечается подарок и выдаётся доступ (Phase 2).
    Обратный порядок оставлял бы живую сущность в панели при откате
    транзакции — человека с работающим VPN, которого нет в базе.

ЧТО ЛЕГКО СЛОМАТЬ
    Повторная проверка статуса под FOR UPDATE внутри транзакции. Без неё два
    одновременных нажатия «Активировать» выдают подписку дважды по одному
    коду: обе копии успевают пройти проверку до блокировки.

    Алфавит кода намеренно без O/0/I/1/L — код диктуют голосом и вводят
    руками. Вернуть их = поток обращений «код не подходит».
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import config
from app.utils.security import mask_secret
from database.core import get_pool, _to_db_utc, _from_db_utc, _ensure_utc

logger = logging.getLogger(__name__)


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
    # Код подарка — предъявительский токен: кто прочитал его в логе, тот и
    # активирует чужую оплаченную подписку. Здесь особенно важно, потому что
    # запись делается В МОМЕНТ СОЗДАНИЯ — читатель лога успевает погасить
    # подарок раньше получателя. В лог идёт маска и id строки, по которым
    # подарок находится в базе.
    logger.info(
        f"GIFT_CREATED buyer={buyer_telegram_id} gift_id={row['id']} "
        f"code={mask_secret(gift_code)} tariff={tariff} period={period_days}d"
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

    # См. GIFT_CREATED выше: код не пишем целиком. Здесь он уже погашен, но
    # правило одно на модуль — иначе при следующей правке маска потеряется.
    logger.info(
        f"GIFT_ACTIVATED code={mask_secret(gift_code)} by={activated_by} "
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
