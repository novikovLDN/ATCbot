"""
Registry автоуведомлений. При старте бота upsert'ит defaults в БД,
чтобы admin-dashboard видел все известные ключи и мог их переопределять.

Регистрация:
    register_notification(NotificationSpec(
        key="trial.reminder_3h",
        title="Триал: за 3 часа до истечения",
        description="Финальный push для триала (со скидкой 15%).",
        category="trial",
        default_text_ru="...HTML...",
        template_vars=["username"],
        default_trigger={"before_expiry_hours": 3, "tolerance_hours": 1},
    ))

Использование в bot-коде:
    language = await resolve_user_language(telegram_id)
    text = await get_notification_text(
        "trial.reminder_3h", language=language,
        params={"username": user.get("username")},
    )
    # None значит одно из двух: уведомление выключено админом ИЛИ язык не 'ru'
    # (в таблице лежит только русский текст). В обоих случаях берём i18n:
    text = text or i18n.get_text(language, "trial.reminder_3h")
    await bot.send_message(telegram_id, text, parse_mode="HTML")
    await log_notification_send("trial.reminder_3h", telegram_id, status="sent")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


VALID_CATEGORIES = {
    "trial", "subscription", "welcome", "payment", "referral",
    "gift", "reminder", "other",
}


@dataclass(frozen=True)
class NotificationSpec:
    """Метаданные одного notification-key."""
    key: str
    title: str
    description: str
    category: str
    default_text_ru: str
    template_vars: List[str] = field(default_factory=list)
    default_trigger: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.category not in VALID_CATEGORIES:
            raise ValueError(
                f"invalid category {self.category!r} for {self.key} — "
                f"must be one of {sorted(VALID_CATEGORIES)}"
            )
        if not self.key or "." not in self.key:
            raise ValueError(f"key must be namespaced 'a.b': got {self.key!r}")


# Registry — dict keyed by notification-key.
REGISTRY: Dict[str, NotificationSpec] = {}


def register_notification(spec: NotificationSpec) -> None:
    """Register a notification. Idempotent — при повторной регистрации
    того же ключа перезапишем (нужно для hot-reload)."""
    REGISTRY[spec.key] = spec


def all_specs() -> List[NotificationSpec]:
    return sorted(REGISTRY.values(), key=lambda s: (s.category, s.key))


# ── Известные уведомления ────────────────────────────────────────────

# Триал — reminders. Дефолты из ru.py 783-784 — оставлены синхронно.
register_notification(NotificationSpec(
    key="trial.reminder_24h",
    title="Триал: за 24 часа до истечения",
    description=(
        "Отправляется, когда до конца пробного периода осталось "
        "~24 часа (±1ч допуск). Ключевой pre-churn push для триальной "
        "воронки — «завтра доступ отключится»."
    ),
    category="trial",
    default_text_ru=(
        "⏳ <b>Пробный период заканчивается завтра</b>\n\n"
        "Завтра доступ отключится — сайты и приложения вернутся к "
        "блокировкам.\n\n"
        "Оформите подписку сейчас — ключ и настройки сохранятся, "
        "ничего не нужно переустанавливать."
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 24, "tolerance_hours": 1},
))

register_notification(NotificationSpec(
    key="trial.reminder_6h",
    title="Триал: за 6 часов до истечения (legacy 71h-slot)",
    description=(
        "Старая ветка (i18n main.trial_notification_71h) — «через 6 часов "
        "пробный доступ завершится». Работает параллельно с новым "
        "trial.reminder_3h. Wire-up идёт через TRIAL_NOTIFICATION_SCHEDULE "
        "в app/services/trials — при желании можно отключить через "
        "тумблер тут, чтобы не дублировать 3h reminder."
    ),
    category="trial",
    default_text_ru=(
        "🔔 Через 6 часов пробный доступ завершится и VPN отключится\n\n"
        "Не теряйте защиту — подключите подписку от 199₽ и пользуйтесь "
        "без ограничений 💎"
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 6, "tolerance_hours": 0.5},
))

register_notification(NotificationSpec(
    key="trial.notification_71h",
    title="Триал: «последний час» (за 1ч до истечения)",
    description=(
        "Финальный push триал-воронки — «Последний час пробного доступа». "
        "Ключ говорит про 71h (для BC), реальный тайминг — 1 час до конца. "
        "Заходит с кнопкой «Купить со скидкой»."
    ),
    category="trial",
    default_text_ru=(
        "🚨 Последний час пробного доступа\n\n"
        "Через час VPN будет отключён.\n\n"
        "Оформите подписку, чтобы оставаться на связи, "
        "даже когда глушат связь!"
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 1, "tolerance_hours": 0.5},
))

register_notification(NotificationSpec(
    key="trial.reminder_3h",
    title="Триал: за 3 часа до истечения",
    description=(
        "Финальный push триальной воронки — 3 часа до конца. "
        "Обычно шлётся с оффером скидки 15%, кнопка ниже. "
        "Один из самых конверсионных reminder'ов."
    ),
    category="trial",
    default_text_ru=(
        "🔥 <b>3 часа до отключения</b>\n\n"
        "После этого VPN перестанет работать. Сайты, стриминг, "
        "мессенджеры — всё вернётся к блокировкам.\n\n"
        "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> "
        "Успейте — <b>скидка 15%</b> действует до конца триала."
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 3, "tolerance_hours": 1},
))

# Подписки — reminders.
# Тексты синхронизированы с i18n/ru.py (reminder.paid_*) — если админ
# ничего не менял, отправляется тот же текст что и до реестра.
register_notification(NotificationSpec(
    key="subscription.reminder_7d",
    title="Подписка: за 7 дней до окончания",
    description=(
        "Ранний pre-churn напоминающий — 7 дней до конца платной "
        "подписки. Хорошо ложится оффер «продли заранее — "
        "фиксируешь цену»."
    ),
    category="subscription",
    default_text_ru=(
        "<tg-emoji emoji-id=\"5454415424319931791\">📅</tg-emoji> "
        "Подписка заканчивается через 7 дней. Продлите заранее — "
        "доступ не прервётся."
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 24 * 7, "tolerance_hours": 12},
))

register_notification(NotificationSpec(
    key="subscription.reminder_3d",
    title="Подписка: за 3 дня до окончания",
    description=(
        "3 дня до конца платной подписки. Классическая точка "
        "renewal-подсказки. На проде отправляется с photo-header'ом."
    ),
    category="subscription",
    default_text_ru=(
        "<tg-emoji emoji-id=\"5454415424319931791\">📅</tg-emoji> "
        "Ваша подписка Atlas Secure активна ещё 3 дня\n\n"
        "Продлите заранее — и доступ не прервётся ни на секунду 🤍"
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 24 * 3, "tolerance_hours": 6},
))

register_notification(NotificationSpec(
    key="subscription.reminder_1d",
    title="Подписка: за 1 день до окончания",
    description="Финальный напоминающий за сутки до конца платной подписки.",
    category="subscription",
    default_text_ru=(
        "<tg-emoji emoji-id=\"5190806721286657692\">🔴</tg-emoji> "
        "Подписка заканчивается завтра. Продлите сейчас, чтобы "
        "VPN продолжил работать."
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 24, "tolerance_hours": 2},
))

register_notification(NotificationSpec(
    key="subscription.reminder_24h",
    title="Подписка: за 24 часа (legacy)",
    description=(
        "Старая ветка reminder-логики — 24 часа до истечения. "
        "Действует одновременно с reminder_1d, обычно один из них "
        "выигрывает первым. Оставлен для BC."
    ),
    category="subscription",
    default_text_ru=(
        "<tg-emoji emoji-id=\"5456140674028019486\">⚡️</tg-emoji> "
        "Осталось менее 24 часов подписки\n\n"
        "Продлите сейчас одним нажатием, чтобы VPN продолжил работать "
        "без перерыва 🛡"
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 24, "tolerance_hours": 1},
))

# ── Payment success (event-triggered, окно не применимо) ─────────────
register_notification(NotificationSpec(
    key="payment.success_welcome_basic",
    title="Оплата: приветствие после первой покупки Basic",
    description=(
        "Отправляется юзеру сразу после успешной оплаты первой "
        "подписки Basic (не renewal). Используется в payment webhook."
    ),
    category="payment",
    default_text_ru=(
        "🎉 <b>Добро пожаловать в Atlas Secure!</b>\n\n"
        "📦 Тариф: <b>Basic</b>\n"
        "📅 До: {date}\n\n"
        "Подписка активна — VPN готов к работе.\n\n"
        "🤍 Atlas Secure"
    ),
    template_vars=["date"],
    default_trigger={},
))

register_notification(NotificationSpec(
    key="payment.success_welcome_plus",
    title="Оплата: приветствие после первой покупки Plus",
    description=(
        "Отправляется юзеру сразу после успешной оплаты первой "
        "подписки Plus (не renewal)."
    ),
    category="payment",
    default_text_ru=(
        "🎉 <b>Добро пожаловать в Atlas Secure!</b>\n\n"
        "⭐️ Тариф: <b>Plus</b>\n"
        "📅 До: {date}\n\n"
        "Подписка активна — VPN готов к работе.\n\n"
        "🤍 Atlas Secure"
    ),
    template_vars=["date"],
    default_trigger={},
))

register_notification(NotificationSpec(
    key="payment.success_renewal_compact",
    title="Оплата: продление (compact)",
    description=(
        "Отправляется при renewal — юзер продлил уже действующую "
        "или недавно истёкшую подписку. Компактный формат."
    ),
    category="payment",
    default_text_ru=(
        "✅ <b>Подписка продлена!</b>\n\n"
        "{tariff_icon} Тариф: {tariff}\n"
        "📅 До: {date}\n\n"
        "VPN продолжает работать.\n\n"
        "🤍 Atlas Secure"
    ),
    template_vars=["tariff_icon", "tariff", "date"],
    default_trigger={},
))

register_notification(NotificationSpec(
    key="gift.activated_welcome",
    title="Подарок: приветствие после активации gift-подписки",
    description=(
        "Юзер активировал подаренную подписку через promo-код. Первое "
        "сообщение бота — приветствие + предложение выбрать язык."
    ),
    category="gift",
    default_text_ru=(
        "🎉 <b>Добро пожаловать в Atlas Secure!</b>\n\n"
        "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> "
        "Вам подарили подписку:\n"
        "📦 Тариф: {tariff_name}\n"
        "⏳ Срок: {period}\n\n"
        "Подписка уже активна.\nВыберите язык интерфейса:"
    ),
    template_vars=["tariff_name", "period"],
    default_trigger={},
))

register_notification(NotificationSpec(
    key="referral.reward_notification",
    title="Реферальный кэшбэк начислен",
    description=(
        "Отправляется юзеру-рефереру когда его приглашённый оформил "
        "подписку и начислился кэшбэк на баланс."
    ),
    category="referral",
    default_text_ru=(
        "🔥 Вам начислен реферальный кешбэк!\n\n"
        "Ваш друг оформил подписку.\n"
        "💰 Начислено: {amount:.2f} ₽\n"
        "Баланс: {balance:.2f} ₽"
    ),
    template_vars=["amount", "balance"],
    default_trigger={},
))

register_notification(NotificationSpec(
    key="subscription.reminder_3h",
    title="Подписка: за 3 часа до окончания (со скидкой 15%)",
    description=(
        "3 часа до конца платной подписки. Шлётся со спец-кнопкой "
        "скидки 15% на продление (её текст меняется через 'reminder."
        "paid_3h_discount_btn' в i18n, если понадобится)."
    ),
    category="subscription",
    default_text_ru=(
        "<tg-emoji emoji-id=\"5190806721286657692\">🚨</tg-emoji> "
        "<b>3 часа до отключения</b>\n\n"
        "После этого VPN перестанет работать. Сайты и приложения "
        "вернутся к блокировкам.\n\n"
        "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> "
        "Успейте — <b>скидка 15%</b> на продление. Действует 3 часа."
    ),
    template_vars=[],
    default_trigger={"before_expiry_hours": 3, "tolerance_hours": 1},
))
