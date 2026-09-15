"""Quick action on the Overview block «Истекают за 7 дней»: «Предложить
продление со скидкой».

A normal broadcast to active PAID subscriptions ending in the next 7 days
(segment `paid_expiring:7d`, or `paid_expiring_manual:7d` without the
auto-renew ones), with the existing «🎁 Купить со скидкой» button
(`promo_buy` + broadcast_discounts). The click goes through
callback_broadcast_promo_buy → create_user_discount(keep_max=True), and the
price picks the single largest discount (pick_largest_discount), so the
owner rule «the largest one wins, nothing stacks» holds unchanged.

The broadcast pipeline has no per-user placeholders, so the texts are
date-free («в ближайшие дни»); {discount} and {hours} are substituted once,
before sending.
"""
from __future__ import annotations

from typing import Optional

WINDOW = "7d"
DISCOUNT_CHOICES = (10, 15, 20)
DEFAULT_DISCOUNT = 15
DEFAULT_HOURS = 72
MAX_HOURS = 168
BUTTON = "promo_buy"

# Same premium emoji as the retention reminders (app/i18n/ru.py reminder.paid_*).
_E_CAL = '<tg-emoji emoji-id="5454415424319931791">📅</tg-emoji>'
_E_GIFT = '<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji>'
_E_RED = '<tg-emoji emoji-id="5190806721286657692">🔴</tg-emoji>'

TEMPLATES: tuple[dict, ...] = (
    {
        "id": "early",
        "title": "Продлите заранее",
        "text": (f"{_E_CAL} Подписка Atlas Secure скоро заканчивается. Продлите сейчас со скидкой "
                 "<b>{discount}%</b> — доступ не прервётся ни на секунду 🤍\n\n"
                 "Скидка действует {hours} ч."),
    },
    {
        "id": "long",
        "title": "Выгоднее на длинный срок",
        "text": (f"{_E_GIFT} Продлите подписку со скидкой <b>{{discount}}%</b> — на 3, 6 или 12 месяцев "
                 "выгоднее всего.\n\nПредложение действует {hours} ч."),
    },
    {
        "id": "keep",
        "title": "Не потеряйте доступ",
        "text": (f"{_E_RED} В ближайшие дни подписка закончится — основные серверы отключатся "
                 "(обход продолжит работать, пока есть ГБ).\n\n"
                 "Продлите со скидкой <b>{discount}%</b> — действует {hours} ч."),
    },
)


def segment_key(exclude_auto_renew: bool) -> str:
    base = "paid_expiring_manual" if exclude_auto_renew else "paid_expiring"
    return f"{base}:{WINDOW}"


def render(text: str, discount: int, hours: int) -> str:
    """Substitute {discount}/{hours}. str.replace, not format: any other brace
    in an edited text stays as it is."""
    return text.replace("{discount}", str(int(discount))).replace("{hours}", str(int(hours)))


def title(discount: int, exclude_auto_renew: bool) -> str:
    who = "без автопродления" if exclude_auto_renew else "все"
    return f"Продление со скидкой {int(discount)}% · истекают за 7 дней ({who})"


def discount_label(hours: int) -> str:
    return f"{int(hours)} ч"


def validate(discount: int, hours: int) -> Optional[str]:
    """Error text for a bad discount / lifetime, None when fine."""
    if discount not in DISCOUNT_CHOICES:
        return f"discount_percent must be one of {list(DISCOUNT_CHOICES)}"
    if not 1 <= int(hours) <= MAX_HOURS:
        return f"discount_hours must be in [1, {MAX_HOURS}]"
    return None
