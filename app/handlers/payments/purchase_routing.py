"""Опознание типа оплаченной покупки и суммы платежа.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ
    Эти два вопроса — «что человек купил» и «сколько он заплатил в рублях» —
    нужны и обработчику успешной оплаты, и модулю выдачи товаров. Держать
    их в одном из них значит завязать модули друг на друга кольцом.

ЧЕМ ОПАСНА ОШИБКА ЗДЕСЬ
    Неопознанный тип покупки уходит в финализацию VPN-подписки: деньги
    списаны, товар не выдан, заказ до админа не дошёл. Именно так однажды
    терялись оплаты Spotify.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Типы покупок, у которых есть собственная ветка обработки в
# process_successful_payment. Всё, что сюда не попало, финализируется как
# VPN-подписка — поэтому забытый тип означает «деньги списаны, товар не выдан».
_ROUTED_PURCHASE_TYPES = frozenset({
    "gift", "telegram_premium", "telegram_stars", "steam",
    "apple_id", "traffic_pack", "spotify", "proxy",
})

# Некоторые покупки опознаются по префиксу тарифа, а не по purchase_type:
# так же, как это делает app/services/payments/confirmation.py для вебхуков.
_TARIFF_PREFIX_ROUTES = (
    ("spotify_", "spotify"),
    ("steam_", "steam"),
    ("apple_id_", "apple_id"),
)


def resolve_payment_amount_rubles(
    total_amount: int, is_stars: bool, pending_purchase: Optional[dict]
) -> float:
    """Рублёвая сумма платежа.

    Для Stars total_amount — это количество звёзд, а не рубли: записывать его
    как рублёвую сумму нельзя, иначе выручка и реферальный кешбэк считаются
    от числа звёзд. Берём цену, зафиксированную при создании покупки.
    Для карты total_amount приходит в копейках.
    """
    if not is_stars:
        return total_amount / 100.0

    price_kopecks = (pending_purchase or {}).get("price_kopecks")
    if price_kopecks:
        return price_kopecks / 100.0

    logger.error(
        "STARS_PRICE_MISSING: у покупки нет price_kopecks, выручка будет занижена, stars=%s",
        total_amount,
    )
    return float(total_amount)


def classify_purchase(pending_purchase: Optional[dict]) -> str:
    """Определить, как обрабатывать оплаченную покупку.

    Возвращает 'subscription' только для настоящих VPN-подписок. Любой
    товар со своей веткой обязан вернуть собственный тип, иначе оплата
    уйдёт в финализацию подписки и товар не будет выдан.
    """
    purchase = pending_purchase or {}
    purchase_type = (purchase.get("purchase_type") or "").strip()
    if purchase_type in _ROUTED_PURCHASE_TYPES:
        return purchase_type

    tariff = str(purchase.get("tariff") or "")
    for prefix, route in _TARIFF_PREFIX_ROUTES:
        if tariff.startswith(prefix):
            return route

    return "subscription"
