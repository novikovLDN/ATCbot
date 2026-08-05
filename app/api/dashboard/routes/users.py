"""Карточка пользователя и админские действия над ней в веб-дашборде.

ЧТО ЗДЕСЬ ЕСТЬ
    Поиск пользователей, полная карточка (баланс, подписка, триал, скидки,
    VIP, кешбэк), выдача и отзыв доступа, смена тарифа, правка баланса.

ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА
    Чтение просто проксирует функции database.*. Запись идёт через ТЕ ЖЕ
    атомарные помощники, которыми пользуются админские обработчики в боте
    (admin_grant_access_atomic, admin_revoke_access_atomic и другие).
    Благодаря этому журнал аудита, синхронизация с Remnawave и побочные
    эффекты одинаковы независимо от того, пришло действие из чата или из
    веб-интерфейса. Никогда не дублируйте здесь бизнес-логику — вызывайте
    существующий атомарный помощник.

ЧЕГО ЗДЕСЬ СОЗНАТЕЛЬНО НЕТ
    Операции, доступные только боту: grant_access, finalize_purchase,
    mark_trial_used. Они завязаны на платёжный поток и не должны
    запускаться из веба.

ТАРИФЫ
    Название тарифа для человека берётся из app.constants.tariffs, а не
    собирается здесь: в базе комбо-подписка может храниться двумя способами
    (subscription_type='combo_plus' либо 'plus' с флагом is_combo), и
    единая точка перевода избавляет интерфейс от этой двусмысленности.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

import config
import database
from app.api.dashboard.deps import require_admin
from app.constants import tariffs as tariff_ref
from app.events import bus

# Модульного логгера здесь не было вовсе: logging импортировался локально в
# двух хелперах уведомлений, а ни один пишущий эндпоинт — выдача доступа,
# отзыв, смена тарифа, правка баланса, удаление — не оставлял в логе ни одной
# строки. Ни до, ни после. Восстановить «кто кому что выдал» по логам
# приложения было нельзя: единственным следом оставался bus.publish, а это
# in-memory шина для SSE, а не журнал.
logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


def _serialize_match(row: dict) -> dict:
    out: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.get("/search")
async def users_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(25, gt=0, le=100),
):
    """Substring search across the whole users table.

    Matches telegram_id (digits typed anywhere — as text, so prefixes
    like "123" find tg:1234567890) and username (case-insensitive
    substring, leading @ stripped). Returns up to `limit` ranked
    matches as `{matches: [...], total: N}`.

    Empty result returns 200 with `matches: []` rather than 404 so the
    UI can render "ничего не нашлось" without an exception path.
    """
    try:
        rows = await database.search_users_dashboard(q, limit=limit)
    except Exception as e:
        raise HTTPException(500, f"search_failed: {e}")
    return {
        "query": q.strip(),
        "matches": [_serialize_match(r) for r in rows],
        "total": len(rows),
    }


def _describe_subscription(subscription: Optional[dict]) -> Optional[dict]:
    """Дополнить подписку понятным описанием тарифа для интерфейса.

    Зачем: в базе комбо-подписка встречается в двух видах — либо
    subscription_type='combo_plus', либо 'plus' с отдельным флагом is_combo.
    Дашборд не должен знать про эту двойственность, поэтому здесь к записи
    добавляются готовые поля:
        tariff_display  — «Комбо Плюс» вместо «plus»
        is_combo        — единый признак независимо от способа хранения
        base_tariff     — уровень доступа без учёта пакета обхода
        bypass_gb       — сколько ГБ обхода входит в комбо за этот период

    Исходные поля не меняются: старые потребители продолжают работать.
    """
    if not subscription:
        return subscription

    tariff = subscription.get("subscription_type")
    legacy_combo_flag = bool(subscription.get("is_combo"))
    period_days = subscription.get("period_days")

    enriched = dict(subscription)
    enriched["tariff_display"] = tariff_ref.display_name(tariff, is_combo=legacy_combo_flag)
    enriched["is_combo"] = tariff_ref.is_combo_tariff(tariff) or legacy_combo_flag
    enriched["base_tariff"] = tariff_ref.base_tariff_of(tariff)
    enriched["bypass_gb"] = tariff_ref.combo_bypass_gb(tariff, period_days)
    return enriched


@router.get("/{telegram_id}")
async def user_detail(telegram_id: int = Path(..., gt=0)):
    """Полная карточка: пользователь, баланс, подписка, скидки, VIP, триал.

    Подписка проходит через _describe_subscription, поэтому в ответе всегда
    есть человекочитаемое название тарифа и однозначный признак комбо.
    """
    try:
        user = await database.get_user(telegram_id)
        if not user:
            raise HTTPException(404, "User not found")
        balance = await database.get_user_balance(telegram_id)
        subscription = await database.get_subscription(telegram_id)
        trial = await database.get_trial_info(telegram_id)
        discount = await database.get_user_discount(telegram_id)
        traffic_discount = await database.get_user_traffic_discount(telegram_id)
        is_vip = await database.is_vip_user(telegram_id)
        cashback_fixed = await database.get_cashback_fixed_percent(telegram_id)
        cashback_effective = await database.get_effective_cashback_percent(telegram_id)
        return {
            "user": user,
            "balance_rubles": balance,
            "subscription": _describe_subscription(subscription),
            "trial": trial,
            "discount": discount,
            "traffic_discount": traffic_discount,
            "is_vip": is_vip,
            "cashback_fixed_percent": cashback_fixed,
            "cashback_effective_percent": cashback_effective,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"user_detail_failed: {e}")


@router.get("/{telegram_id}/history")
async def user_history(
    telegram_id: int = Path(..., gt=0),
    limit: int = Query(20, gt=0, le=200),
):
    try:
        return await database.get_subscription_history(telegram_id, limit)
    except Exception as e:
        raise HTTPException(500, f"history_failed: {e}")


@router.get("/{telegram_id}/extended-stats")
async def user_extended_stats(telegram_id: int = Path(..., gt=0)):
    try:
        return await database.get_user_extended_stats(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"extended_stats_failed: {e}")


@router.get("/{telegram_id}/payments")
async def user_payments(
    telegram_id: int = Path(..., gt=0),
    limit: int = Query(100, gt=0, le=500),
):
    """Все покупки пользователя — paid / pending / expired —
    из pending_purchases (там лежат подписки, traffic-паки, balance,
    telegram premium, steam, прокси, фарм-участки). Старая таблица
    `payments` тут не используется: она устарела и пропускает большую
    часть потоков."""
    try:
        rows = await database.get_user_purchases(telegram_id, limit=limit)
    except Exception as e:
        raise HTTPException(500, f"payments_failed: {e}")
    return [_serialize(r) for r in rows]


def _serialize(row: dict) -> dict:
    """Make datetimes / Decimals JSON-friendly without pulling in a
    custom encoder. Bytes values are skipped (none expected here)."""
    out: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            continue
        else:
            out[k] = v
    return out


# ──────────────────────────────────────────────────────────────────────
# WRITE endpoints — all go through the same atomic helpers as the
# in-bot admin handlers. Side effects: DB updates, audit log,
# Remnawave sync (where the helper does it), event publication.
# ──────────────────────────────────────────────────────────────────────

class GrantRequest(BaseModel):
    """Ручная выдача доступа админом.

    tariff принимает и комбо-тарифы: администратор должен уметь выдать
    «Комбо Плюс» так же, как обычную подписку. Раньше проверка опиралась
    на VALID_SUBSCRIPTION_TYPES, где комбо нет, поэтому выдать его через
    дашборд было невозможно вовсе.
    """

    days: int = Field(..., gt=0, le=3650)
    tariff: str = Field("basic")
    notify: bool = Field(True)

    @field_validator("tariff")
    @classmethod
    def _valid_tariff(cls, v: str) -> str:
        if v not in config.GRANTABLE_TARIFF_TYPES:
            raise ValueError(f"invalid tariff: {v}")
        return v


async def _notify_granted(
    telegram_id: int, value: int, unit_key: str, vpn_key: str, expires_at,
) -> bool:
    """Сказать человеку, что ему выдали доступ. Вернуть, дошло ли.

    ЗАЧЕМ ЭТО ЗДЕСЬ
        Пока выдача жила в боте, уведомление отправлял тот же экран.
        Экран уехал в дашборд — уведомление обязано было уехать с ним,
        иначе человеку выдают доступ, а он об этом не узнаёт и не
        получает ключ.

    ЧТО ЛЕГКО СЛОМАТЬ
        Отчитываться «уведомлён» по намерению, а не по факту. Отправка
        не удаётся регулярно и по бытовым причинам: человек заблокировал
        бота, никогда ему не писал, удалил аккаунт. Поэтому возвращаем
        РЕЗУЛЬТАТ отправки, и он уходит в ответ endpoint'а флагом
        notify_sent — админ видит, что надо связаться иначе.

        Сама выдача уже в БД. Неудача уведомления её не отменяет и не
        должна ронять запрос — отсюда широкий except.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        from app.api import telegram_webhook
        bot = getattr(telegram_webhook, "_bot", None)
        if bot is None:
            logger.warning("GRANT_NOTIFY_NO_BOT tg=%s", telegram_id)
            return False

        from app.i18n import get_text
        from app.services.language_service import resolve_user_language
        from app.utils.telegram_safe import safe_send_message

        # Язык получателя, а не язык админа: раньше текст собирался
        # русской f-строкой независимо от того, на каком языке человек
        # пользуется ботом.
        language = await resolve_user_language(telegram_id)
        text = get_text(
            language, "admin.user_granted_access",
            value=value,
            unit=get_text(language, unit_key),
            vpn_key=vpn_key or "—",
            date=expires_at.strftime("%d.%m.%Y") if hasattr(expires_at, "strftime") else expires_at,
        )
        return bool(await safe_send_message(bot, telegram_id, text))
    except Exception:
        logger.exception("GRANT_NOTIFY_FAILED tg=%s", telegram_id)
        return False


@router.post("/{telegram_id}/grant")
async def user_grant(
    telegram_id: int = Path(..., gt=0),
    body: GrantRequest = ...,
    admin: dict = Depends(require_admin),
):
    # Запись ДО действия: если процесс упадёт внутри атомарного помощника,
    # останется след, что выдачу вообще начинали и кто её начал.
    logger.info(
        "DASHBOARD_GRANT_START admin=%s user=%s days=%s tariff=%s notify=%s",
        admin.get("sub"), telegram_id, body.days, body.tariff, body.notify,
    )
    try:
        expires_at, vpn_key = await database.admin_grant_access_atomic(
            telegram_id, body.days, int(admin["sub"]), tariff=body.tariff,
        )
    except Exception as e:
        # Провал выдачи доступа уходил только в HTTP-ответ админу. В логах
        # приложения его не было — ни сообщения, ни трейсбэка.
        logger.exception(
            "DASHBOARD_GRANT_FAILED admin=%s user=%s days=%s tariff=%s error=%s",
            admin.get("sub"), telegram_id, body.days, body.tariff, e,
        )
        raise HTTPException(500, f"grant_failed: {e}")
    bus.publish({
        "type": "admin:grant",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "days": body.days,
        "tariff": body.tariff,
    })
    notify_sent = (
        await _notify_granted(telegram_id, body.days, "units.days", vpn_key, expires_at)
        if body.notify else False
    )
    # Исход уведомления берётся из результата отправки, а не из намерения:
    # notify_sent=False означает «не просили слать» ИЛИ «не дошло», и эти два
    # случая надо различать в логе, иначе «выдали, а человек не знает»
    # неотличимо от «выдали молча намеренно».
    logger.info(
        "DASHBOARD_GRANT_OK admin=%s user=%s days=%s tariff=%s expires_at=%s "
        "key_issued=%s notify_requested=%s notify_delivered=%s",
        admin.get("sub"), telegram_id, body.days, body.tariff, expires_at,
        bool(vpn_key), body.notify, notify_sent,
    )
    if body.notify and not notify_sent:
        logger.warning(
            "DASHBOARD_GRANT_NOTIFY_UNDELIVERED admin=%s user=%s — доступ выдан, "
            "человек уведомления не получил",
            admin.get("sub"), telegram_id,
        )
    return {
        "ok": True,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "vpn_key": vpn_key,
        "notify_sent": notify_sent,
    }


class GrantMinutesRequest(BaseModel):
    minutes: int = Field(..., gt=0, le=525600)  # ≤ 1 year
    notify: bool = Field(True)


@router.post("/{telegram_id}/grant-minutes")
async def user_grant_minutes(
    telegram_id: int = Path(..., gt=0),
    body: GrantMinutesRequest = ...,
    admin: dict = Depends(require_admin),
):
    logger.info(
        "DASHBOARD_GRANT_MINUTES_START admin=%s user=%s minutes=%s notify=%s",
        admin.get("sub"), telegram_id, body.minutes, body.notify,
    )
    try:
        expires_at, vpn_key = await database.admin_grant_access_minutes_atomic(
            telegram_id, body.minutes, int(admin["sub"]),
        )
    except Exception as e:
        logger.exception(
            "DASHBOARD_GRANT_MINUTES_FAILED admin=%s user=%s minutes=%s error=%s",
            admin.get("sub"), telegram_id, body.minutes, e,
        )
        raise HTTPException(500, f"grant_minutes_failed: {e}")
    bus.publish({
        "type": "admin:grant_minutes",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "minutes": body.minutes,
    })
    notify_sent = (
        await _notify_granted(telegram_id, body.minutes, "units.minutes", vpn_key, expires_at)
        if body.notify else False
    )
    logger.info(
        "DASHBOARD_GRANT_MINUTES_OK admin=%s user=%s minutes=%s expires_at=%s "
        "key_issued=%s notify_requested=%s notify_delivered=%s",
        admin.get("sub"), telegram_id, body.minutes, expires_at,
        bool(vpn_key), body.notify, notify_sent,
    )
    return {
        "ok": True,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "vpn_key": vpn_key,
        "notify_sent": notify_sent,
    }


@router.post("/{telegram_id}/revoke")
async def user_revoke(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    logger.info(
        "DASHBOARD_REVOKE_START admin=%s user=%s", admin.get("sub"), telegram_id,
    )
    try:
        ok = await database.admin_revoke_access_atomic(telegram_id, int(admin["sub"]))
    except Exception as e:
        logger.exception(
            "DASHBOARD_REVOKE_FAILED admin=%s user=%s error=%s",
            admin.get("sub"), telegram_id, e,
        )
        raise HTTPException(500, f"revoke_failed: {e}")
    bus.publish({
        "type": "admin:revoke",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    # ok=False означает, что отзывать было нечего (подписки нет, UPDATE не
    # задел ни строки). Раньше это значение нигде не фиксировалось, а событие
    # «отозвано» уходило в шину одинаково при любом исходе.
    if ok:
        logger.info("DASHBOARD_REVOKE_OK admin=%s user=%s", admin.get("sub"), telegram_id)
    else:
        logger.warning(
            "DASHBOARD_REVOKE_NOOP admin=%s user=%s — отзывать было нечего, "
            "активной подписки не найдено",
            admin.get("sub"), telegram_id,
        )
    return {"ok": bool(ok)}


class SwitchTariffRequest(BaseModel):
    """Смена тарифа у действующей подписки.

    Принимает комбо наравне с обычными тарифами: перевести пользователя
    с «Плюс» на «Комбо Плюс» — обычная админская операция, и запрещать её
    на уровне валидации не было причин.
    """

    tariff: str = Field(...)

    @field_validator("tariff")
    @classmethod
    def _valid_tariff(cls, v: str) -> str:
        if v not in config.GRANTABLE_TARIFF_TYPES:
            raise ValueError(f"invalid tariff: {v}")
        return v


@router.post("/{telegram_id}/switch-tariff")
async def user_switch_tariff(
    telegram_id: int = Path(..., gt=0),
    body: SwitchTariffRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        updated = await database.admin_switch_tariff(telegram_id, body.tariff)
    except Exception as e:
        raise HTTPException(500, f"switch_tariff_failed: {e}")
    if not updated:
        raise HTTPException(404, "no_active_subscription")
    bus.publish({
        "type": "admin:switch_tariff",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
        "tariff": body.tariff,
    })
    return {"ok": True, "subscription": updated}


class DiscountRequest(BaseModel):
    percent: int = Field(..., ge=1, le=100)
    expires_in_hours: Optional[int] = Field(None, gt=0, le=8760)  # ≤ 1 year


@router.post("/{telegram_id}/discount")
async def user_discount_create(
    telegram_id: int = Path(..., gt=0),
    body: DiscountRequest = ...,
    admin: dict = Depends(require_admin),
):
    expires_at = None
    if body.expires_in_hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    try:
        ok = await database.create_user_discount(
            telegram_id=telegram_id,
            discount_percent=body.percent,
            expires_at=expires_at,
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"discount_create_failed: {e}")
    if not ok:
        raise HTTPException(500, "discount_create_failed")
    bus.publish({
        "type": "admin:discount_create",
        "telegram_id": telegram_id,
        "percent": body.percent,
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "percent": body.percent,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.delete("/{telegram_id}/discount")
async def user_discount_delete(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.delete_user_discount(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"discount_delete_failed: {e}")
    bus.publish({
        "type": "admin:discount_delete",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


class TrafficDiscountRequest(BaseModel):
    """Скидка на пакеты GB для «Обхода» (bypass traffic packs).
    Хранится в отдельной таблице user_traffic_discounts (не путать с
    личной скидкой на подписку). Применяется в чекауте GB паков.
    """
    percent: int = Field(..., ge=1, le=100)
    expires_in_hours: Optional[int] = Field(None, gt=0, le=8760)  # ≤ 1 year


@router.post("/{telegram_id}/traffic-discount")
async def user_traffic_discount_create(
    telegram_id: int = Path(..., gt=0),
    body: TrafficDiscountRequest = ...,
    admin: dict = Depends(require_admin),
):
    expires_at = None
    if body.expires_in_hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    try:
        ok = await database.create_user_traffic_discount(
            telegram_id=telegram_id,
            discount_percent=body.percent,
            expires_at=expires_at,
            created_by=int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"traffic_discount_create_failed: {e}")
    if not ok:
        raise HTTPException(500, "traffic_discount_create_failed")
    bus.publish({
        "type": "admin:traffic_discount_create",
        "telegram_id": telegram_id,
        "percent": body.percent,
        "by": admin.get("sub"),
    })
    return {
        "ok": True,
        "percent": body.percent,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.delete("/{telegram_id}/traffic-discount")
async def user_traffic_discount_delete(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.delete_user_traffic_discount(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"traffic_discount_delete_failed: {e}")
    bus.publish({
        "type": "admin:traffic_discount_delete",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


# ── Cashback fixed % (admin-managed override) ─────────────────────────
class CashbackFixRequest(BaseModel):
    """Фиксирует конкретный % кешбэка для пользователя. Жёстко перекрывает
    и тир, и floor. При выключении (DELETE) — юзер возвращается к
    обычной логике (тир + floor)."""
    percent: int = Field(..., ge=0, le=100)


@router.post("/{telegram_id}/cashback-fix")
async def user_cashback_fix_set(
    telegram_id: int = Path(..., gt=0),
    body: CashbackFixRequest = ...,
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.set_cashback_fixed_percent(telegram_id, body.percent)
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"cashback_fix_set_failed: {e}")
    if not ok:
        raise HTTPException(404, "user_not_found")
    bus.publish({
        "type": "admin:cashback_fix_set",
        "telegram_id": telegram_id,
        "percent": body.percent,
        "by": admin.get("sub"),
    })
    # Уведомление партнёру. НЕ используем create_task без сохранения
    # ссылки — GC может убить task до запуска. Await'им inline: ~200мс
    # добавляется к ответу API, но admin action всё равно редкое и не
    # чувствительное к латентности. Флаг notify_sent показывает в ответе,
    # получилось ли отправить (для UI диагностики).
    # На отзыве фикса ничего не шлём (по требованию).
    notify_sent = await _send_partner_congrats(telegram_id, body.percent)
    effective = await database.get_effective_cashback_percent(telegram_id)
    return {
        "ok": True,
        "percent": body.percent,
        "effective_percent": effective,
        "notify_sent": notify_sent,
    }


async def _send_partner_congrats(telegram_id: int, percent: int) -> bool:
    """Поздравительное уведомление партнёру при активации fix-статуса.

    Содержит: приветствие, назначенный %, реферальную ссылку в цитате
    (тап → copy в буфер Telegram) и inline-кнопку «Поделиться», которая
    открывает штатный t.me/share/url? UI Telegram и подставляет ссылку.

    Все ошибки логируем с полным трейсом (logger.exception), возвращаем
    False. Сама fix-настройка уже в БД — если Telegram отказал (403 =
    юзер заблокировал бота / никогда не писал; 400 = невалидный HTML),
    admin видит это в ответе endpoint'а по флагу notify_sent.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        from urllib.parse import quote
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from app.api import telegram_webhook
        bot = getattr(telegram_webhook, "_bot", None)
        if bot is None:
            logger.warning(
                "CASHBACK_FIX_CONGRATS_NO_BOT tg=%s (bot not initialized yet)",
                telegram_id,
            )
            return False
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        from app.utils.referral_link import build_referral_link
        referral_link = await build_referral_link(telegram_id, bot_username)
        text = (
            "🎉 <b>Поздравляем — ты теперь партнёр!</b>\n\n"
            "Группа компаний <b>Atlas Secure &amp; QoDev</b> подтверждает "
            f"твой статус партнёра с фиксированной ставкой "
            f"<b>{percent}%</b> с каждой продажи по твоей рекомендации.\n\n"
            "<blockquote expandable>"
            f"💰 За каждую покупку по твоей ссылке — <b>{percent}% на баланс</b>.\n"
            "📈 Процент зафиксирован и не зависит от количества приглашённых — "
            "это отдельный VIP-статус."
            "</blockquote>\n\n"
            "<b>🔗 Твоя партнёрская ссылка</b>\n"
            f"<blockquote expandable><code>{referral_link}</code></blockquote>\n"
            "<i>Тапни на ссылку — она скопируется в буфер обмена. "
            "Или воспользуйся кнопкой «Поделиться» ниже.</i>"
        )
        share_url = f"https://t.me/share/url?url={quote(referral_link, safe='')}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Поделиться ссылкой", url=share_url)],
        ])
        await bot.send_message(
            chat_id=telegram_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        # Реферальная ссылка в запись не идёт: она содержит личный код
        # пользователя и вместе с telegram_id рядом даёт готовую связку
        # «человек → его монетизируемый идентификатор». Для разбора хватает
        # telegram_id, ссылка выводится из него.
        logger.info(
            "CASHBACK_FIX_CONGRATS_SENT user=%s percent=%s",
            telegram_id, percent,
        )
        return True
    except Exception as e:
        # exception() пишет полный traceback — иначе диагностировать
        # невозможно (раньше только .warning без trace, ловили пустоту).
        logger.exception(
            "CASHBACK_FIX_CONGRATS_FAIL user=%s err=%s", telegram_id, e,
        )
        return False


@router.delete("/{telegram_id}/cashback-fix")
async def user_cashback_fix_clear(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.clear_cashback_fixed_percent(telegram_id)
    except Exception as e:
        raise HTTPException(500, f"cashback_fix_clear_failed: {e}")
    if not ok:
        raise HTTPException(404, "user_not_found")
    bus.publish({
        "type": "admin:cashback_fix_clear",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    effective = await database.get_effective_cashback_percent(telegram_id)
    return {"ok": True, "effective_percent": effective}


@router.post("/{telegram_id}/vip")
async def user_vip_grant(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.grant_vip_status(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"vip_grant_failed: {e}")
    bus.publish({
        "type": "admin:vip_grant",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


@router.delete("/{telegram_id}/vip")
async def user_vip_revoke(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    try:
        ok = await database.revoke_vip_status(telegram_id, int(admin["sub"]))
    except Exception as e:
        raise HTTPException(500, f"vip_revoke_failed: {e}")
    bus.publish({
        "type": "admin:vip_revoke",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": bool(ok)}


class BalanceRequest(BaseModel):
    delta_rubles: float = Field(..., description="Positive credits, negative debits")
    reason: Optional[str] = Field(None, max_length=200)

    @field_validator("delta_rubles")
    @classmethod
    def _nonzero(cls, v: float) -> float:
        if v == 0:
            raise ValueError("delta cannot be zero")
        if abs(v) > 1_000_000:
            raise ValueError("absolute value too large")
        return v


@router.delete("/{telegram_id}")
async def user_delete(
    telegram_id: int = Path(..., gt=0),
    admin: dict = Depends(require_admin),
):
    """Удалить пользователя: персональные данные и доступ. Необратимо.

    Финансовая история (payments, pending_purchases, balance_transactions)
    сохраняется намеренно — иначе выручка, ARPU, LTV и график по дням
    пересчитывались бы задним числом после каждого удаления. Подробности и
    причина — в докстринге database.admin.admin_delete_user_complete.

    Заодно чистит сущность в Remnawave и пишет в audit_log, сколько
    платёжных строк и на какую сумму осталось.
    """
    try:
        ok = await database.admin_delete_user_complete(
            telegram_id, int(admin["sub"]),
        )
    except Exception as e:
        raise HTTPException(500, f"delete_failed: {e}")
    if not ok:
        raise HTTPException(404, "User not found or delete blocked")
    bus.publish({
        "type": "admin:user_deleted",
        "telegram_id": telegram_id,
        "by": admin.get("sub"),
    })
    return {"ok": True}


@router.post("/{telegram_id}/balance")
async def user_balance_change(
    telegram_id: int = Path(..., gt=0),
    body: BalanceRequest = ...,
    admin: dict = Depends(require_admin),
):
    """Credit (positive) or debit (negative) the user's balance.
    Routes through increase_balance / decrease_balance with source='admin'
    so the change appears in balance_transactions with proper attribution.
    """
    reason = body.reason or f"Web dashboard adjustment by admin {admin.get('sub')}"
    # Личность админа обязана попасть в лог отдельным полем.
    #
    # В balance_transactions уходит только source='admin' и свободный текст
    # description: если админ прислал собственный reason, его ID не
    # сохраняется НИГДЕ, и начисление денег становится анонимным. Пока это
    # так, лог — единственное место, где видно, кто именно двигал баланс.
    logger.info(
        "DASHBOARD_BALANCE_CHANGE_START admin=%s user=%s delta_rub=%s custom_reason=%s",
        admin.get("sub"), telegram_id, body.delta_rubles, bool(body.reason),
    )
    try:
        if body.delta_rubles > 0:
            ok = await database.increase_balance(
                telegram_id, body.delta_rubles,
                source="admin", description=reason,
            )
        else:
            ok = await database.decrease_balance(
                telegram_id, abs(body.delta_rubles),
                source="admin", description=reason,
            )
    except Exception as e:
        logger.exception(
            "DASHBOARD_BALANCE_CHANGE_FAILED admin=%s user=%s delta_rub=%s error=%s",
            admin.get("sub"), telegram_id, body.delta_rubles, e,
        )
        raise HTTPException(500, f"balance_change_failed: {e}")
    if not ok:
        logger.warning(
            "DASHBOARD_BALANCE_CHANGE_REJECTED admin=%s user=%s delta_rub=%s — "
            "операция отклонена (недостаточно средств или нет пользователя)",
            admin.get("sub"), telegram_id, body.delta_rubles,
        )
        raise HTTPException(400, "balance_change_rejected")
    bus.publish({
        "type": "admin:balance_change",
        "telegram_id": telegram_id,
        "delta": body.delta_rubles,
        "by": admin.get("sub"),
    })
    new_balance = 0.0
    balance_read_ok = False
    try:
        new_balance = await database.get_user_balance(telegram_id)
        balance_read_ok = True
    except Exception as e:
        # Молчаливый except: pass на денежном пути. Админ видел «баланс 0 ₽»
        # после успешного начисления и, скорее всего, начислял второй раз —
        # при этом в логах не оставалось ничего.
        logger.error(
            "DASHBOARD_BALANCE_READBACK_FAILED admin=%s user=%s error=%s: %s — "
            "изменение баланса ПРИМЕНЕНО, но в ответ уйдёт 0 ₽; повторное "
            "начисление по этому экрану будет ошибкой",
            admin.get("sub"), telegram_id, type(e).__name__, e,
        )
    logger.info(
        "DASHBOARD_BALANCE_CHANGE_OK admin=%s user=%s delta_rub=%s new_balance=%s",
        admin.get("sub"), telegram_id, body.delta_rubles,
        new_balance if balance_read_ok else "unknown",
    )
    return {"ok": True, "new_balance_rubles": new_balance}
