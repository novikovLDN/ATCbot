"""
Loyalty / Ambassador push texts (рандомизированные).

Содержит готовые шаблоны пушей для «Круга Амбассадоров»:
— signup: «по твоей ссылке кто-то зарегался»
— trial:  «друг взял пробник»
— purchase: «друг купил подписку — тебе кэшбэк»

Каждый вызов выбирает СЛУЧАЙНЫЙ вариант из набора — это убирает
рутину и оживляет ленту. См. промт-задачу «Круг Амбассадоров».

Все шаблоны под parse_mode='HTML'. Подставляются плейсхолдеры:
{percent} — текущий тир-процент реферрера, {amount}, {balance}.
"""

from __future__ import annotations

import random
from typing import Optional


_SIGNUP_VARIANTS: tuple[str, ...] = (
    (
        "👀 <b>Кто-то по твоей ссылке заглянул в Atlas Secure.</b>\n\n"
        "Если оформит подписку — твой <b>{percent}%</b> упадёт на баланс автоматически.\n\n"
        "<blockquote>Уровни «Круга Амбассадоров» только растут. Каждый новый купивший — шаг к 45% навсегда.</blockquote>"
    ),
    (
        "🚀 <b>По твоей ссылке зашёл новый человек.</b>\n\n"
        "Когда он оплатит подписку — <b>{percent}%</b> от его покупки прилетит тебе на баланс.\n\n"
        "<blockquote>Дальше — за тобой: подели ссылкой ещё с кем-нибудь, пока этот думает.</blockquote>"
    ),
    (
        "🔗 <b>Твоя ссылка сработала.</b> Один человек уже в боте.\n\n"
        "Решится оформить подписку — заберёшь <b>{percent}%</b> на баланс. Без лимитов, навсегда.\n\n"
        "<blockquote>Чем больше людей по твоей ссылке покупают, тем выше твой тир и %.</blockquote>"
    ),
)

_TRIAL_VARIANTS: tuple[str, ...] = (
    (
        "👀 <b>Тот, кто пришёл по твоей ссылке, взял пробник на 3 дня.</b>\n\n"
        "Если оформит подписку после триала — <b>{percent}%</b> от покупки тебе на баланс.\n\n"
        "<blockquote>Ничего делать не нужно. Просто дождись.</blockquote>"
    ),
    (
        "🎯 <b>Твой реферал активировал пробный период.</b>\n\n"
        "3 дня — и момент истины. Решит остаться → твой <b>{percent}%</b> прилетит автоматически.\n\n"
        "<blockquote>Лучшее время напомнить ему о себе — сейчас.</blockquote>"
    ),
    (
        "🚀 <b>Друг пробует Atlas.</b> 3 дня бесплатно.\n\n"
        "Перейдёт на платную — заберёшь <b>{percent}%</b> с каждой его покупки на баланс. Навсегда.\n\n"
        "<blockquote>Пока он тестит — приглашай следующего. Уровень только растёт.</blockquote>"
    ),
)

_PURCHASE_VARIANTS: tuple[str, ...] = (
    (
        "💰 <b>Кэшбэк зачислен.</b>\n\n"
        "+<b>{amount:.2f} ₽</b> на баланс. Это {percent}% с покупки твоего реферала.\n\n"
        "{progress_block}"
    ),
    (
        "🎁 <b>+{amount:.2f} ₽ на баланс.</b>\n\n"
        "Друг по твоей ссылке оплатил подписку. Твой кусок: <b>{percent}%</b>.\n\n"
        "{progress_block}"
    ),
    (
        "🪙 <b>Ссылка приносит деньги.</b>\n\n"
        "Реферал купил подписку. Тебе {percent}% — это <b>{amount:.2f} ₽</b> сразу на баланс.\n\n"
        "{progress_block}"
    ),
)


def pick_signup_push(percent: int, *, seed: Optional[int] = None) -> str:
    """Случайный вариант текста пуша «друг перешёл по ссылке»."""
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(_SIGNUP_VARIANTS).format(percent=percent)


def pick_trial_push(percent: int, *, seed: Optional[int] = None) -> str:
    """Случайный вариант текста пуша «друг активировал пробник»."""
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(_TRIAL_VARIANTS).format(percent=percent)


def _purchase_progress_block(
    next_level_name: Optional[str], referrals_needed: int, current_percent: int,
) -> str:
    """Собирает blockquote-блок прогресса для пуша покупки."""
    if not next_level_name or referrals_needed <= 0:
        return (
            f"<blockquote>👑 Ты на вершине. <b>{current_percent}%</b> навсегда. "
            f"Делись ссылкой — каждый кэшбэк твой.</blockquote>"
        )
    # Локальный маппинг тира → % следующего, чтобы не плодить импорты в hot-path.
    next_pct_map = {"Хранитель": 20, "Инсайдер": 30, "Лидер": 40, "Амбассадор": 45}
    next_pct = next_pct_map.get(next_level_name, "?")
    # Род. падеж — лёгкая копия из tier_genitive (без импорта, чтобы push был
    # самодостаточен и не зависал на цикле импорта).
    gen_map = {
        "Хранитель": "Хранителя", "Инсайдер": "Инсайдера",
        "Лидер": "Лидера", "Амбассадор": "Амбассадора",
    }
    next_gen = gen_map.get(next_level_name, next_level_name)
    return (
        f"<blockquote>📈 До <b>{next_gen}</b> ({next_pct}%) — "
        f"<b>{referrals_needed}</b> купивших.</blockquote>"
    )


def pick_purchase_push(
    amount: float,
    percent: int,
    next_level_name: Optional[str] = None,
    referrals_needed: int = 0,
    *,
    seed: Optional[int] = None,
) -> str:
    """Случайный вариант текста пуша «друг купил → кэшбэк»."""
    rng = random.Random(seed) if seed is not None else random
    progress = _purchase_progress_block(next_level_name, referrals_needed, percent)
    return rng.choice(_PURCHASE_VARIANTS).format(
        amount=amount, percent=percent, progress_block=progress,
    )
