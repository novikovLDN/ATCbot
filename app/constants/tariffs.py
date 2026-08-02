"""Справочник тарифов: единое место, где живёт вся логика их различения.

ЗАЧЕМ ЭТОТ МОДУЛЬ
    Раньше проверки вида `tariff.startswith("combo_")`, вычисление ГБ обхода
    и подбор человекочитаемого названия были разбросаны по двенадцати файлам.
    Каждое добавление тарифа означало правку во всех этих местах, и любое
    забытое место давало расхождение: пользователь видел одно, платил другое.
    Здесь собраны все ответы на вопрос «что это за тариф и что в него входит».

КАКИЕ ТАРИФЫ ЕСТЬ
    basic       — базовая подписка, VPN без пакета обхода
    plus        — расширенная подписка, VPN без пакета обхода
    combo_basic — basic + пакет ГБ обхода в одной покупке
    combo_plus  — plus + пакет ГБ обхода в одной покупке
    biz_*       — бизнес-тарифы с выделенными серверами (см. config.BIZ_TARIFFS)

    У каждого тарифа своя цена и свой набор периодов, а у комбо дополнительно
    своё количество ГБ на каждый период. Источник цен и ГБ — config.TARIFFS
    и config.COMBO_TARIFFS; этот модуль их не дублирует, а только читает.

ГДЕ ИСКАТЬ СМЕЖНОЕ
    config.TARIFFS        — цены обычных подписок по периодам
    config.COMBO_TARIFFS  — цены и ГБ комбо-тарифов по периодам
    config.BIZ_TARIFFS    — перечень бизнес-тарифов
    app/services/purchase_flow.py — выдача доступа после оплаты
"""
from typing import Any, Dict, Optional, Tuple

import config

# Префикс, по которому опознаётся комбо-тариф. Вынесен в константу, потому
# что раньше строка "combo_" была написана вручную в разных файлах.
COMBO_PREFIX = "combo_"

# Человекочитаемые названия для интерфейса и админки.
# Ключ — технический код тарифа, значение — то, что видит человек.
TARIFF_DISPLAY_NAMES: Dict[str, str] = {
    "basic": "Базовый",
    "plus": "Плюс",
    "combo_basic": "Комбо Базовый",
    "combo_plus": "Комбо Плюс",
    "trial": "Пробный",
}


def is_combo_tariff(tariff: Optional[str]) -> bool:
    """Комбо ли это — подписка вместе с пакетом ГБ обхода.

    Комбо отличается от обычной подписки тем, что при выдаче доступа к нему
    прилагается трафик обхода, а цена берётся из отдельной таблицы.
    """
    return bool(tariff) and str(tariff).startswith(COMBO_PREFIX)


def is_biz_tariff(tariff: Optional[str]) -> bool:
    """Бизнес-тариф ли это — выделенный сервер под клиента."""
    return bool(tariff) and str(tariff) in getattr(config, "BIZ_TARIFFS", ())


def base_tariff_of(tariff: Optional[str]) -> str:
    """Базовый тариф, на котором построен переданный.

    Для комбо возвращает подписку, лежащую в его основе: combo_plus → plus.
    Нужно там, где важен уровень доступа, а не способ покупки — например
    при продлении, чтобы не понизить Плюс до Базового.
    """
    if not tariff:
        return "basic"
    tariff = str(tariff)
    if is_combo_tariff(tariff):
        # Внутри COMBO_TARIFFS у каждого периода записан свой base_tariff.
        # Берём его из любого периода: для одного комбо он одинаковый.
        periods = (getattr(config, "COMBO_TARIFFS", {}) or {}).get(tariff, {})
        for info in periods.values():
            base = info.get("base_tariff")
            if base:
                return str(base)
        # Запасной вариант, если таблица не заполнена: combo_plus → plus
        return tariff[len(COMBO_PREFIX):] or "basic"
    return tariff


def combo_bypass_gb(tariff: Optional[str], period_days: Optional[int]) -> int:
    """Сколько ГБ обхода входит в комбо за указанный период.

    Возвращает 0 для некомбо-тарифов и для периодов, которых нет в таблице —
    вызывающий код может смело складывать результат, не проверяя тип.
    """
    if not is_combo_tariff(tariff) or not period_days:
        return 0
    periods = (getattr(config, "COMBO_TARIFFS", {}) or {}).get(str(tariff), {})
    info = periods.get(int(period_days)) or {}
    try:
        return int(info.get("gb", 0) or 0)
    except (TypeError, ValueError):
        return 0


def combo_price_rubles(tariff: Optional[str], period_days: Optional[int]) -> Optional[int]:
    """Цена комбо за период в рублях. None, если такой комбинации нет."""
    if not is_combo_tariff(tariff) or not period_days:
        return None
    periods = (getattr(config, "COMBO_TARIFFS", {}) or {}).get(str(tariff), {})
    info = periods.get(int(period_days)) or {}
    price = info.get("price")
    return int(price) if price is not None else None


def display_name(tariff: Optional[str], *, is_combo: bool = False) -> str:
    """Название тарифа для показа человеку.

    Флаг is_combo нужен для исторических записей: в базе встречаются подписки,
    где subscription_type хранит базовый тариф ('plus'), а признак комбо лежит
    отдельной колонкой is_combo. В таком случае покажем «Комбо Плюс».
    """
    if not tariff:
        return "—"
    tariff = str(tariff).strip().lower()

    if is_combo and not is_combo_tariff(tariff):
        combo_key = f"{COMBO_PREFIX}{tariff}"
        if combo_key in TARIFF_DISPLAY_NAMES:
            return TARIFF_DISPLAY_NAMES[combo_key]

    if tariff in TARIFF_DISPLAY_NAMES:
        return TARIFF_DISPLAY_NAMES[tariff]

    if is_biz_tariff(tariff):
        # biz_starter → «Бизнес Starter»
        suffix = tariff.replace("biz_", "", 1).replace("_", " ").title()
        return f"Бизнес {suffix}"

    return tariff


def available_periods(tariff: Optional[str]) -> Tuple[int, ...]:
    """Периоды в днях, доступные для тарифа, по возрастанию.

    У комбо и обычных подписок наборы периодов различаются, поэтому
    интерфейс обязан спрашивать их здесь, а не хардкодить список.
    """
    if not tariff:
        return ()
    tariff = str(tariff)
    if is_combo_tariff(tariff):
        table: Dict[int, Any] = (getattr(config, "COMBO_TARIFFS", {}) or {}).get(tariff, {})
    else:
        table = (getattr(config, "TARIFFS", {}) or {}).get(tariff, {})
    return tuple(sorted(int(p) for p in table.keys()))


def describe(tariff: Optional[str], period_days: Optional[int] = None,
             *, is_combo: bool = False) -> Dict[str, Any]:
    """Полное описание тарифа одним словарём — для админки и дашборда.

    Собирает в одном месте всё, что обычно приходится вычислять по кусочкам:
    название, базовый тариф, признак комбо, ГБ обхода и цену.
    """
    combo = is_combo_tariff(tariff) or is_combo
    return {
        "tariff": tariff,
        "display_name": display_name(tariff, is_combo=is_combo),
        "base_tariff": base_tariff_of(tariff),
        "is_combo": combo,
        "is_biz": is_biz_tariff(tariff),
        "bypass_gb": combo_bypass_gb(tariff, period_days),
        "price_rubles": combo_price_rubles(tariff, period_days),
        "period_days": period_days,
    }
