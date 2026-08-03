"""Премиум-иконки и цвет кнопок не должны зависеть от языка интерфейса.

Дефект 1 (button_defaults.py:63). Таблицы TEXT_EMOJI_MAP и
STYLE_*_PATTERNS сопоставлялись с ТЕКСТОМ кнопки, а текст приходит из
i18n и на каждом языке свой. Русский видел «💳 Банковская карта» с
премиум-иконкой и зелёной подсветкой «рекомендуем», немец — серую
«Bankkarte» без иконки. Пропадал ключевой визуальный сигнал ровно на
тех языках, где конверсия и так хуже. Починка: смысл кнопки берём из
callback_data и из i18n-КЛЮЧА, а не из строки.

Дефект 2 (button_defaults.py:227). При установке icon_custom_emoji_id
из текста вырезался ведущий юникод-эмодзи. У этого поля, в отличие от
инлайнового <tg-emoji>, нет тела с запасным глифом: на клиенте без
поддержки Bot API 9.4 кнопка оставалась вообще без иконки, потому что
исходный 💳 уже удалён. Починка: эмодзи остаётся в тексте.
"""
import pytest

from aiogram.types import InlineKeyboardButton

import app.utils.button_defaults as bd
from app.i18n import LANGUAGES, get_text

ALL_LANGS = sorted(LANGUAGES)


# ── Дефект 1: смысл вместо русской подписи ───────────────────────────

@pytest.mark.parametrize("lang", ALL_LANGS)
@pytest.mark.parametrize(
    "i18n_key, callback_data, emoji_id",
    [
        ("payment.card_pl", "pay:card_pl", "5377377923076476823"),
        ("payment.sbp", "pay:sbp", "5217837965547427903"),
        ("payment.intl_pl", "pay:intl_pl", "5375114475311484868"),
        ("payment.card", "pay:card", "5375493342966597701"),
        ("payment.lava", "pay:lava", "5217961106554769883"),
        ("payment.stars", "pay:stars", "5269768891864746432"),
        ("payment.crypto", "pay:crypto", "5463219974132746636"),
    ],
)
def test_payment_buttons_get_same_icon_on_every_language(lang, i18n_key, callback_data, emoji_id):
    """Экран выбора оплаты собирается из i18n, но callback_data фиксирован —
    иконка обязана быть одна и та же на всех семи языках."""
    button = InlineKeyboardButton(text=get_text(lang, i18n_key), callback_data=callback_data)
    assert button.icon_custom_emoji_id == emoji_id


@pytest.mark.parametrize("lang", ALL_LANGS)
@pytest.mark.parametrize("callback_data", ["pay:card_pl", "pay:sbp", "pay:intl_pl"])
def test_recommended_payment_methods_stay_green_on_every_language(lang, callback_data):
    """Зелёный на трёх основных способах оплаты — это сигнал «рекомендуем».
    Раньше его видел только русскоязычный."""
    key = {"pay:card_pl": "payment.card_pl", "pay:sbp": "payment.sbp",
           "pay:intl_pl": "payment.intl_pl"}[callback_data]
    button = InlineKeyboardButton(text=get_text(lang, key), callback_data=callback_data)
    assert button.style == "success"


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_back_button_gets_icon_without_any_callback_hint(lang):
    """У «Назад» callback_data каждый раз свой (menu_main, menu_profile, …),
    поэтому смысл берётся из i18n-ключа common.back. Немецкое «← Zurück» и
    узбекское «← Orqaga» должны получить ту же стрелку, что и «Назад»."""
    button = InlineKeyboardButton(text=get_text(lang, "common.back"), callback_data="menu_main")
    assert button.icon_custom_emoji_id == "5416117059207572332"


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_balance_button_with_placeholder_is_recognised(lang):
    """payment.balance собирается через format («Баланс (доступно: 512.30 ₽)»),
    поэтому exact-таблица его не ловит — нужен шаблонный матч на каждом языке."""
    text = get_text(lang, "payment.balance", balance=512.3)
    button = InlineKeyboardButton(text=text, callback_data="какой-угодно")
    assert button.icon_custom_emoji_id == "5402186569006210455"


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_buy_subscription_cta_is_primary_on_every_language(lang):
    """Синий CTA «Купить подписку» — главное действие бота."""
    button = InlineKeyboardButton(text=get_text(lang, "main.buy"), callback_data="menu_buy_vpn")
    assert button.style == "primary"


def test_localized_maps_are_actually_populated():
    """Если импорт app.i18n внутри button_defaults молча упадёт, таблицы
    останутся пустыми и всё вернётся к «только русский» — без единой
    ошибки в логах. Проверяем, что они наполнены."""
    assert bd.LOCALIZED_EMOJI_MAP, "локализованные подписи не собрались"
    assert bd.LOCALIZED_STYLE_MAP, "локализованные стили не собрались"
    assert bd.LOCALIZED_EMOJI_PATTERNS, "шаблонные подписи (с {placeholder}) не собрались"


def test_explicit_arguments_still_win():
    """Явное всегда сильнее автоподстановки — иначе call site не сможет
    переопределить ни иконку, ни цвет."""
    button = InlineKeyboardButton(
        text="Банковская карта",
        callback_data="pay:card_pl",
        icon_custom_emoji_id="1",
        style="danger",
    )
    assert button.icon_custom_emoji_id == "1"
    assert button.style == "danger"


def test_russian_behaviour_did_not_change():
    """Контрольная точка: русский UI трогать не собирались."""
    card = InlineKeyboardButton(text="Банковская карта", callback_data="pay:card_pl")
    assert card.icon_custom_emoji_id == "5377377923076476823"
    assert card.style == "success"

    delete = InlineKeyboardButton(text="🗑 Удалить ключ", callback_data="admin:key_del")
    assert delete.style == "danger"

    neutral = InlineKeyboardButton(text="Профиль", callback_data="menu_profile")
    assert neutral.icon_custom_emoji_id is None
    assert neutral.style is None


# ── Дефект 2: запасной глиф остаётся в тексте ────────────────────────

def test_unicode_emoji_survives_premium_icon_injection():
    """У icon_custom_emoji_id нет тела с fallback-глифом. Вырежем 💳 —
    и на клиенте без Bot API 9.4 кнопка останется вообще без иконки."""
    button = InlineKeyboardButton(text="💳 Банковская карта", callback_data="pay:card_pl")
    assert button.icon_custom_emoji_id == "5377377923076476823"
    assert button.text == "💳 Банковская карта", (
        "ведущий эмодзи вырезан — на старом клиенте кнопка останется без иконки"
    )


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_localized_texts_are_never_rewritten(lang):
    """Ни один язык не должен получить кнопку с изменённым текстом:
    подпись — это то, что владелец написал в словаре."""
    original = get_text(lang, "payment.sbp")
    button = InlineKeyboardButton(text=original, callback_data="pay:sbp")
    assert button.text == original


def test_strip_flag_is_off_by_default():
    """Срезка эмодзи необратима и остаётся выключенной, пока владелец
    сам не включит её через BUTTON_STRIP_UNICODE_EMOJI."""
    assert bd.STRIP_UNICODE_EMOJI_ON_PREMIUM_ICON is False
