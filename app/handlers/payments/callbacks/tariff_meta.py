"""Тарифы глазами экранов покупки: подписи, значки, текущий тариф.

ЧТО ЗДЕСЬ
    `_TARIFF_META` — иконка, название и ключ описания для четырёх тарифов;
    `_period_badge` — значок на кнопке периода (⭐ для 3 месяцев, 🔥 для
    года и дольше); `_current_tariff_key` — какой тариф у человека сейчас,
    с учётом флага комбо.

ПОЧЕМУ ВЫДЕЛЕНО
    Этим пользуются все экраны покупки, а `_period_badge` — ещё и подарки
    (app/handlers/callbacks/gift/wizard.py) и навигация. Держать их в
    одном из экранов значило бы тянуть экран ради константы.

ЧТО ЛЕГКО СЛОМАТЬ
    Ключи `_TARIFF_META` — это ещё и часть callback_data («tariff:basic»,
    «combo_tariff:combo_plus»). Переименуете ключ — кнопка перестанет
    находить адресата, а меню смены тарифа начнёт предлагать тот тариф,
    который у человека уже есть.

    `_current_tariff_key` склеивает комбо из двух полей (subscription_type
    + is_combo): в базе комбо не хранится отдельным типом.
"""


_TARIFF_META = {
    "basic":       {"icon": "⚡️", "name": "Basic",       "desc_key": "buy.tariff_basic_desc"},
    "plus":        {"icon": "👑", "name": "Plus",        "desc_key": "buy.tariff_plus_desc"},
    "combo_basic": {"icon": "🚀", "name": "Комбо Basic", "desc_key": "combo.tariff_basic"},
    "combo_plus":  {"icon": "🚀", "name": "Комбо Plus",  "desc_key": "combo.tariff_plus"},
}


def _period_badge(period_days: int) -> str:
    """Emotional badge for period buttons: ⭐ for 3 mo (popular), 🔥 for 12+ mo (best deal)."""
    if period_days == 90:
        return "⭐"
    if period_days >= 365:
        return "🔥"
    return ""

def _current_tariff_key(sub) -> str:
    """Determine effective tariff key including combo flag."""
    if not sub:
        return ""
    sub_type = (sub.get("subscription_type") or "basic").strip().lower()
    is_combo = sub.get("is_combo", False)
    if is_combo:
        return f"combo_{sub_type}"  # combo_basic / combo_plus
    return sub_type  # basic / plus
