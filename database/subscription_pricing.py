"""Цена покупки: база из конфига плюс ровно одна скидка.

ЧТО ЗДЕСЬ
    calculate_final_price        единственное место, где считается сумма к оплате
    _calculate_subscription_days перевод месяцев в дни (1/3/6/12 → 30/90/180/365)

ПОЧЕМУ ОТДЕЛЬНО
    Расчёт цены не трогает базу подписок и вообще ничего не меняет — он
    только читает конфиг и справочники скидок. Правят его чаще всего
    (акции, новые тарифы, страны), и каждая такая правка не должна
    начинаться с открытия модуля, где рядом лежит проведение платежа.

СКИДКИ НЕ СКЛАДЫВАЮТСЯ — ЭТО ГЛАВНОЕ
    Приоритет строгий и взаимоисключающий:
        промокод → VIP 30% → спецпредложение 15% → персональная скидка
    Каждая следующая проверяется только если предыдущих нет. Заменишь
    цепочку elif на последовательное применение — человек получит подписку
    за копейки, а расхождение всплывёт в отчётах через месяц.

    Промокод ограничен сверху 100%: скидка больше сотни давала
    отрицательную цену, а из неё — отрицательный платёж.

ЧТО ЛЕГКО СЛОМАТЬ
    Порог MIN_PRICE_KOPECKS = 6400 (64 ₽) — это не наша прихоть, а нижняя
    граница платёжных провайдеров. Результат ниже неё возвращается с
    is_valid=False, и вызывающий обязан это проверить: провайдер иначе
    отклонит инвойс уже после того, как человек нажал «оплатить».
"""
import logging
from typing import Any, Dict, Optional

from database.promo import check_promo_code_valid
from database.trials_queries import get_special_offer_info

logger = logging.getLogger(__name__)


def _calculate_subscription_days(months: int) -> int:
    """
    Рассчитать количество дней для подписки на основе количества месяцев
    
    Args:
        months: Количество месяцев (1, 3, 6, 12)
    
    Returns:
        Количество дней (30, 90, 180, 365)
    """
    days_map = {
        1: 30,
        3: 90,
        6: 180,
        12: 365
    }
    return days_map.get(months, months * 30)


async def calculate_final_price(
    telegram_id: int,
    tariff: str,
    period_days: int,
    promo_code: Optional[str] = None,
    country: Optional[str] = None,
    base_price_override_rubles: Optional[int] = None
) -> Dict[str, Any]:
    """
    ЕДИНАЯ ФУНКЦИЯ РАСЧЕТА ФИНАЛЬНОЙ ЦЕНЫ (SINGLE SOURCE OF TRUTH)
    
    Рассчитывает финальную цену тарифа с учетом всех скидок:
    - Базовая цена из config.TARIFFS
    - Промокод (высший приоритет)
    - VIP-скидка 30% (если нет промокода)
    - Спецпредложение -15% (если нет промокода и VIP, подписка истекла)
    - Персональная скидка (если нет промокода, VIP и спецпредложения)
    
    Args:
        telegram_id: Telegram ID пользователя
        tariff: Тип тарифа ("basic" или "plus")
        period_days: Период в днях (30, 90, 180, 365)
        promo_code: Промокод (опционально)
    
    Returns:
        {
            "base_price_kopecks": int,      # Базовая цена в копейках
            "discount_amount_kopecks": int, # Размер скидки в копейках
            "final_price_kopecks": int,     # Финальная цена в копейках
            "discount_percent": int,        # Процент скидки (0-100)
            "discount_type": str,           # "promo", "vip", "personal", None
            "promo_code": Optional[str],    # Промокод (если применен)
            "is_valid": bool                # True если цена >= 64 RUB
        }
    
    Raises:
        ValueError: Если тариф или период не найдены в конфиге
    """
    import config

    if base_price_override_rubles is not None:
        # Комбо-тарифы: базовая цена приходит из config.COMBO_TARIFFS,
        # её нет в config.TARIFFS — берём как есть, скидки применяются ниже.
        base_price_rubles = base_price_override_rubles
    else:
        # Проверяем валидность тарифа и периода
        if tariff not in config.TARIFFS:
            raise ValueError(f"Invalid tariff: {tariff}")

        if period_days not in config.TARIFFS[tariff]:
            raise ValueError(f"Invalid period_days: {period_days} for tariff {tariff}")

        # Получаем базовую цену в рублях из конфига
        base_price_rubles = config.TARIFFS[tariff][period_days]["price"]
        # Для бизнес-тарифов применяем множитель страны
        if country and config.is_biz_tariff(tariff):
            multiplier = config.BIZ_COUNTRIES.get(country, {}).get("multiplier", 1.0)
            base_price_rubles = int(round(base_price_rubles * multiplier / 100) * 100)
    base_price_kopecks = round(base_price_rubles * 100)
    
    # ПРИОРИТЕТ 0: Промокод (высший приоритет, перекрывает все остальные скидки)
    promo_data = None
    if promo_code:
        promo_data = await check_promo_code_valid(promo_code.upper())
    
    has_promo = promo_data is not None
    
    # ПРИОРИТЕТ 1: VIP-статус (только если нет промокода)
    from database.admin import is_vip_user as _is_vip, get_user_discount as _get_discount
    is_vip = await _is_vip(telegram_id) if not has_promo else False

    # ПРИОРИТЕТ 2: Спецпредложение -15% (только если нет промокода и VIP)
    special_offer = None
    if not has_promo and not is_vip:
        special_offer = await get_special_offer_info(telegram_id)

    # ПРИОРИТЕТ 3: Персональная скидка (только если нет промокода, VIP и спецпредложения)
    personal_discount = None
    if not has_promo and not is_vip and not special_offer:
        personal_discount = await _get_discount(telegram_id)

    # Применяем скидку в порядке приоритета
    discount_amount_kopecks = 0
    discount_percent = 0
    discount_type = None
    final_price_kopecks = base_price_kopecks

    if has_promo:
        discount_percent = promo_data["discount_percent"]
        # КРИТИЧНО: Защита от скидки > 100% - ограничиваем до 100%
        discount_percent = min(discount_percent, 100)
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        # КРИТИЧНО: Гарантируем, что финальная цена >= 0
        final_price_kopecks = max(final_price_kopecks, 0)
        discount_type = "promo"
        applied_promo_code = promo_code.upper()
    elif is_vip:
        discount_percent = 30
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "vip"
        applied_promo_code = None
    elif special_offer:
        discount_percent = special_offer["discount_percent"]
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "special_offer"
        applied_promo_code = None
    elif personal_discount:
        discount_percent = personal_discount["discount_percent"]
        discount_amount_kopecks = int(base_price_kopecks * discount_percent / 100)
        final_price_kopecks = base_price_kopecks - discount_amount_kopecks
        discount_type = "personal"
        applied_promo_code = None
    else:
        applied_promo_code = None
    
    # Округляем до целых копеек
    final_price_kopecks = int(final_price_kopecks)
    
    # Проверяем минимальную цену (64 RUB = 6400 kopecks)
    MIN_PRICE_KOPECKS = 6400
    is_valid = final_price_kopecks >= MIN_PRICE_KOPECKS
    
    return {
        "base_price_kopecks": base_price_kopecks,
        "discount_amount_kopecks": discount_amount_kopecks,
        "final_price_kopecks": final_price_kopecks,
        "discount_percent": discount_percent,
        "discount_type": discount_type,
        "promo_code": applied_promo_code,
        "is_valid": is_valid
    }
