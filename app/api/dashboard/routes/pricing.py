"""
Управление ценами тарифов и глобальной скидкой.

GET    /pricing/tariffs                        — все цены (список)
PATCH  /pricing/tariffs/{tariff}/{period_days} — переопределить цену пары
DELETE /pricing/tariffs/{tariff}/{period_days} — снять переопределение

GET    /pricing/global-discount                — текущая скидка
PUT    /pricing/global-discount                — установить/обновить
DELETE /pricing/global-discount                — отключить (= percent 0)

Все маршруты требуют admin-JWT (Depends(require_admin) на роутере).

ЗДЕСЬ ДЕНЬГИ
    Любая правка на этих маршрутах действует на всех будущих
    покупателей сразу — кэш цен живёт 30 секунд и сбрасывается
    принудительно. Поэтому каждое изменяющее обращение пишется в лог
    вместе с тем, кто его сделал и что именно поменялось: «цена не та»
    без журнала не расследуется никак.

ПОРЯДОК МАРШРУТОВ
    Литеральный /global-discount объявлен ПОСЛЕ /tariffs/{tariff}/…, и
    это безопасно: пути не пересекаются ни одним сегментом. А вот
    добавите путь вида /{something} — он перехватит и /tariffs, и
    /global-discount, и оба экрана начнут отвечать 422. Такой путь
    обязан идти последним.

СЕКРЕТЫ
    Ни одна строка из текста исключения не уходит наружу без
    scrub_secrets: сюда прилетает текст ошибки asyncpg, а в нём бывает
    строка подключения к базе целиком.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.api.dashboard.deps import require_admin
from app.services import pricing
from app.utils.security import scrub_secrets

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/tariffs")
async def list_tariffs():
    """Все тарифы × периоды с эффективными ценами.

    Формат строки:
      {
        tariff: "basic",
        period_days: 30,
        base_price: 199,        # переопределение или config.TARIFFS
        config_price: 199,      # всегда из config.TARIFFS
        effective_price: 149,   # после глобальной скидки
        discount_percent: 25,
        is_overridden: false,
        has_discount: true,
        editable: true,         # false у комбо — см. list_all_prices
        bypass_gb: 75,          # только у комбо
      }
    """
    try:
        rows = await pricing.list_all_prices()
    except Exception as e:
        logger.error("pricing.list_tariffs failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"list_tariffs_failed: {scrub_secrets(e)}")
    logger.info("pricing.list_tariffs ok: %d rows", len(rows))
    return rows


class OverrideBody(BaseModel):
    price_rub: int = Field(..., gt=0, le=10_000_000)


@router.patch("/tariffs/{tariff}/{period_days}")
async def set_tariff_override(
    body: OverrideBody,
    tariff: str = Path(..., min_length=1, max_length=40),
    period_days: int = Path(..., gt=0, le=3650),
    admin: dict = Depends(require_admin),
):
    """Переопределить цену пары «тариф + период». Кэш цен сбрасывается."""
    try:
        await pricing.set_override(
            tariff, period_days, body.price_rub, int(admin["sub"]),
        )
    except ValueError as ve:
        # Неизвестный тариф/период или цена ≤ 0 — ошибка запроса, не сбой.
        logger.warning(
            "pricing.set_override rejected %s/%s by admin=%s: %s",
            tariff, period_days, admin.get("sub"), scrub_secrets(ve),
        )
        raise HTTPException(400, str(scrub_secrets(ve)))
    except Exception as e:
        logger.error(
            "pricing.set_override failed %s/%s: %s",
            tariff, period_days, scrub_secrets(e),
        )
        raise HTTPException(500, f"set_override_failed: {scrub_secrets(e)}")
    logger.info(
        "pricing.set_override %s/%s = %s ₽ by admin=%s",
        tariff, period_days, body.price_rub, admin.get("sub"),
    )
    return {"ok": True, "tariff": tariff, "period_days": period_days,
            "price_rub": body.price_rub}


@router.delete("/tariffs/{tariff}/{period_days}")
async def clear_tariff_override(
    tariff: str = Path(..., min_length=1, max_length=40),
    period_days: int = Path(..., gt=0, le=3650),
    admin: dict = Depends(require_admin),
):
    """Снять переопределение — вернётся цена из config.TARIFFS.

    Это ТОЖЕ изменение цены, а не «отмена правки»: покупатель завтра
    увидит другое число. Логируется наравне с установкой.
    """
    try:
        removed = await pricing.clear_override(tariff, period_days)
    except Exception as e:
        logger.error(
            "pricing.clear_override failed %s/%s: %s",
            tariff, period_days, scrub_secrets(e),
        )
        raise HTTPException(500, f"clear_override_failed: {scrub_secrets(e)}")
    if not removed:
        logger.info(
            "pricing.clear_override: нечего снимать %s/%s", tariff, period_days,
        )
        raise HTTPException(404, "no override for that tariff/period")
    logger.info(
        "pricing.clear_override %s/%s by admin=%s",
        tariff, period_days, admin.get("sub"),
    )
    return {"ok": True, "tariff": tariff, "period_days": period_days,
            "cleared": True}


@router.get("/global-discount")
async def get_global_discount():
    """Текущие настройки глобальной скидки."""
    try:
        data = await pricing.get_global_discount()
    except Exception as e:
        logger.error("pricing.get_global_discount failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"get_discount_failed: {scrub_secrets(e)}")
    logger.info(
        "pricing.get_global_discount ok: %s%%",
        data.get("global_discount_percent"),
    )
    return data


class DiscountBody(BaseModel):
    """Тело PUT /global-discount.

    percent: 0..99. 0 = отключено (то же самое, что DELETE).
    reason: короткая подпись, которую видит покупатель («Летняя акция»).
    until_at_iso: ISO 8601 с часовым поясом. None = бессрочная скидка.
    """
    percent: int = Field(..., ge=0, le=99)
    reason: Optional[str] = Field(None, max_length=200)
    until_at_iso: Optional[str] = Field(None, max_length=40)


@router.put("/global-discount")
async def put_global_discount(
    body: DiscountBody,
    admin: dict = Depends(require_admin),
):
    """Установить или обновить глобальную скидку на все тарифы."""
    until_at = None
    if body.until_at_iso:
        try:
            until_at = datetime.fromisoformat(body.until_at_iso.replace("Z", "+00:00"))
            if until_at.tzinfo is None:
                until_at = until_at.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(400, "until_at_iso must be ISO 8601 datetime")
        # Срок в прошлом означал бы скидку, которая уже истекла в момент
        # включения: человек увидел бы «включено» и нулевой эффект.
        if until_at <= datetime.now(timezone.utc):
            raise HTTPException(400, "until_at must be in the future")
    try:
        await pricing.set_global_discount(
            body.percent, body.reason, until_at, int(admin["sub"]),
        )
    except ValueError as ve:
        logger.warning(
            "pricing.set_global_discount rejected by admin=%s: %s",
            admin.get("sub"), scrub_secrets(ve),
        )
        raise HTTPException(400, str(scrub_secrets(ve)))
    except Exception as e:
        logger.error("pricing.set_global_discount failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"set_discount_failed: {scrub_secrets(e)}")
    logger.info(
        "pricing.set_global_discount %s%% until=%s by admin=%s",
        body.percent, until_at, admin.get("sub"),
    )
    return {"ok": True, "percent": body.percent}


@router.delete("/global-discount")
async def delete_global_discount(admin: dict = Depends(require_admin)):
    """Быстрое выключение — эквивалент PUT percent=0, reason=None."""
    try:
        await pricing.set_global_discount(0, None, None, int(admin["sub"]))
    except Exception as e:
        logger.error("pricing.clear_global_discount failed: %s", scrub_secrets(e))
        raise HTTPException(500, f"clear_discount_failed: {scrub_secrets(e)}")
    logger.info("pricing.clear_global_discount by admin=%s", admin.get("sub"))
    return {"ok": True, "cleared": True}
