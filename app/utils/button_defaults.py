"""
Selective per-pattern `style` + automatic `icon_custom_emoji_id`
injection for every InlineKeyboardButton in the bot — Bot API 9.4 button color.

Palette policy (post-redesign 2026-06-12):
    Color = signal, not decoration. Default for most buttons is the
    Telegram neutral grey (style not set). Three opt-in lists raise
    a button to a colored state when its text matches:

      ✅ STYLE_SUCCESS_PATTERNS — green: recommended payment methods
                                  (Банковская карта, СБП, Международные)
      🔵 STYLE_PRIMARY_PATTERNS — blue: main CTA buttons (купить
                                  подписку, продлить подписку,
                                  купить со скидкой)
      ⚠️  STYLE_DANGER_PATTERNS — red: truly destructive actions only
                                  (удалить, отозвать, отменить подписку)

    Order of evaluation: success → primary → danger → default (none).
    Explicit `style=...` from the caller always wins.

How `style` works:
    `InlineKeyboardButton` is a Pydantic v2 model. Pydantic builds field
    descriptors at class-creation time, so changing the field's default
    afterwards has no effect. Instead, we wrap the class's `__init__`
    and inject `style` only when a pattern matches.

How `icon_custom_emoji_id` auto-injection works:
    Maintaining premium emoji ids on every call site (hundreds of
    `InlineKeyboardButton(text="…")` instances spread across the
    handler tree, many coming from i18n) is impractical. Instead,
    смысл кнопки определяется в три ступени, от надёжной к запасной:

      1. `callback_data` — язык на него не влияет вообще. Самый
         честный источник смысла: `pay:sbp` остаётся `pay:sbp` и для
         немца, и для араба.
      2. Локализованные подписи, развёрнутые из i18n на старте модуля
         (см. `_build_localized_maps`). Привязка идёт к i18n-КЛЮЧУ, а
         не к конкретной строке.
      3. Ручные таблицы `TEXT_EMOJI_MAP` / `TEXT_EMOJI_PATTERNS` —
         для подписей, которых нет в словарях (админка, хардкод).

    ПОЧЕМУ так, а не «просто список русских подписей», как было
    раньше: подписи приходят из i18n и на каждом языке свои. Русский
    видел «💳 Банковская карта» с премиум-иконкой и зелёной подсветкой
    «рекомендуем», немец — голую серую «Bankkarte». Ключевой визуальный
    сигнал пропадал ровно на тех языках, где конверсия и так хуже.

Про юникод-эмодзи в тексте:
    Ведущий эмодзи из текста НЕ вырезается — см.
    `STRIP_UNICODE_EMOJI_ON_PREMIUM_ICON` ниже.

When to import:
    Once, as early as possible — BEFORE the handler modules load. The
    canonical place is the top of `main.py`, right after `setup_logging()`.

Adding new entries:
    Лучше всего — добавить i18n-ключ в `I18N_KEY_EMOJI_MAP` /
    `I18N_KEY_STYLE_MAP`: он сам развернётся во все семь языков.
    Если подписи в словарях нет — exact-текст в `TEXT_EMOJI_MAP` или
    регулярка в `TEXT_EMOJI_PATTERNS`.
"""

import os
import re

from aiogram.types import InlineKeyboardButton

_original_init = InlineKeyboardButton.__init__

# Anything that isn't a word char (Unicode-aware) or whitespace at the
# very start of the text — covers emoji, pictographs, dingbats. Also
# eats the trailing space the prefix is usually followed by.
_LEAD_EMOJI_RE = re.compile(r"^[^\w\s]+\s*", flags=re.UNICODE)

# Вырезать ли ведущий юникод-эмодзи из текста кнопки, когда мы поставили
# премиум-иконку.
#
# По умолчанию — НЕТ, и вот почему. У `icon_custom_emoji_id`, в отличие
# от инлайнового `<tg-emoji>`, нет тела с запасным глифом: клиент либо
# умеет это поле Bot API, либо не показывает вообще ничего. Если мы уже
# вырезали «💳» из «💳 Банковская карта», то на старом клиенте кнопка
# останется вовсе без иконки — вся навигация по эмодзи разваливается
# разом на всём боте, и откатить это на лету нельзя.
#
# Цена решения — на новых клиентах рядом могут оказаться две иконки
# (премиум + юникод). Это косметика; отсутствие иконки — сломанная
# навигация. Владелец может включить срезку обратно переменной
# окружения BUTTON_STRIP_UNICODE_EMOJI=1, когда убедится, что доля
# клиентов без Bot API 9.4 в аудитории пренебрежимо мала.
STRIP_UNICODE_EMOJI_ON_PREMIUM_ICON = (
    os.getenv("BUTTON_STRIP_UNICODE_EMOJI", "").strip().lower()
    in ("1", "true", "yes", "on")
)

# ── Exact-text → premium emoji_id ────────────────────────────────
# Keys are post-strip (no leading unicode emoji), case-sensitive.
# IDs come from the «EMOJI» tables sent by the product owner.
TEXT_EMOJI_MAP: dict[str, str] = {
    # «Назад» — все возможные формы
    "Назад":               "5416117059207572332",
    "Назад в меню":        "5416117059207572332",
    "Назад к выбору":      "5416117059207572332",
    "Назад на главную":    "5416117059207572332",
    "На главную":          "5416117059207572332",
    "В меню":              "5416117059207572332",
    "Back":                "5416117059207572332",

    # ── Способы оплаты ─────────────────────────────────────
    "Банковская карта":          "5377377923076476823",
    "СБП":                       "5217837965547427903",
    "Международные платежи":     "5375114475311484868",
    "Карта резерв":              "5375493342966597701",
    "СБП резерв 3%":             "5217961106554769883",
    "СБП резерв":                "5217961106554769883",
    "Telegram Stars":            "5269768891864746432",
    "Stars":                     "5269768891864746432",
    "Telegram Premium":          "5987901013032441141",
    "Пополнить Apple ID":        "5269296209238959231",
    "Пополнить Steam":           "4956506857901392912",
    # ── Игры (главное меню Игрового клуба) ─────────────────
    "Боулинг":                   "5370853837689070338",
    "Кубики":                    "5972061723400605896",
    "Бомбер":                    "5280569974404966639",
    "CryptoBot":                 "5463219974132746636",
    "Crypto (CryptoBot)":        "5463219974132746636",
    "Криптовалюта":              "5463219974132746636",
    "Bank Card":                 "5377377923076476823",
    "Card (Lava)":               "5375493342966597701",
    "Card (Robocassa)":          "5375493342966597701",
    "SBP":                       "5217837965547427903",

    # «С баланса» / «Баланс» — оплата с внутреннего баланса
    "С баланса":                 "5402186569006210455",
    "Оплата с баланса":          "5402186569006210455",

    # ── Кнопки выбора устройства ─────────────────────────
    "iPhone / iPad":            "5821379843861778259",
    "iOS":                      "5821379843861778259",
    "iPhone":                   "5821379843861778259",
    "iPad":                     "5821379843861778259",
    "Android":                  "6048857619848761040",
    "Mac":                      "5454100049166357274",
    "macOS":                    "5454100049166357274",
    "Windows":                  "5454081378943518859",
}

# ── Regex-based mapping for templated texts (with prices etc.) ──
# Each entry: (compiled fullmatch regex over post-strip text, emoji_id).
# Match wins → inject + use the original text (stripped of unicode prefix).
TEXT_EMOJI_PATTERNS: list[tuple[re.Pattern, str]] = [
    # «СБП (1234 ₽)» / «СБП 3%»
    (re.compile(r"^СБП(?:\s*[\(\d].*)?$"),       "5217837965547427903"),
    # «СБП 3%» (резерв)
    (re.compile(r"^СБП\s*\d+\s*%.*$"),            "5217961106554769883"),
    # «Карта (Lava)» / «Карта банк» — пометки разные, всё ведём как «Карта резерв»
    (re.compile(r"^Карта(?:\s*\(.+\))?$"),        "5375493342966597701"),
    # «Баланс (доступно: 1234.56 ₽)»
    (re.compile(r"^Баланс(?:\s*\(.+\))?$"),       "5402186569006210455"),
    # «Telegram Stars (123 ⭐)»
    (re.compile(r"^Telegram\s+Stars(?:\s*\(.+\))?$"),  "5364173187858839320"),
    # «Оплатить через СБП» / «Оплатить картой» — URL-кнопки на оплату
    (re.compile(r"^Оплатить\s+(?:через\s+)?СБП.*$"),    "5217837965547427903"),
    (re.compile(r"^Оплатить\s+(?:через\s+)?CryptoBot.*$"), "5463219974132746636"),
    (re.compile(r"^Оплатить\s+(?:по\s+)?СБП.*$"),       "5217837965547427903"),
    (re.compile(r"^Оплатить\s+картой$"),               "5377377923076476823"),
    # «iPhone / iPad»
    (re.compile(r"^iPhone\s*[/\\]\s*iPad$"),       "5821379843861778259"),
    (re.compile(r"^Android(?:\s*TV)?$"),           "6048857619848761040"),
]


# ── Per-button style overrides ───────────────────────────────────
# Texts whose buttons should render `style="success"` (green) instead
# of the default `"danger"` (red). Per product owner: «Банковская
# карта», «СБП», «Международные платежи» — основные платёжные методы
# выделены зелёным, всё остальное (резервы, Stars, CryptoBot, баланс)
# — красным. Pattern checked AFTER the leading-emoji strip, exactly
# like TEXT_EMOJI_MAP — so «🏦 СБП» / «📱 СБП (1234 ₽)» оба
# попадают на success.
STYLE_SUCCESS_PATTERNS: list[re.Pattern] = [
    re.compile(r"^Банковская карта$"),
    re.compile(r"^Bank Card$"),
    # «СБП», «СБП (1234 ₽)» — но НЕ «СБП резерв ...»
    re.compile(r"^СБП(?:\s*\(.+\))?$"),
    re.compile(r"^SBP(?:\s*[\(\+].+)?$"),
    re.compile(r"^Международные платежи$"),
    re.compile(r"^International payments$"),
]

# Texts whose buttons should render `style="primary"` (синий) — основные
# CTA-кнопки покупки/продления подписки. ГБ-трафик намеренно не сюда
# (он не подписка → остаётся красным).
STYLE_PRIMARY_PATTERNS: list[re.Pattern] = [
    # Подписка — основной CTA. ГБ-трафик намеренно сюда не входит:
    # это альтернатива, а не главное действие, поэтому остаётся
    # neutral. Если решим выделить — добавим сюда же.
    re.compile(r"^Купить подписку(?:\s+.+)?$"),
    re.compile(r"^Купить основную(?:\s+подписку)?$"),
    re.compile(r"^Купить VPN$"),
    re.compile(r"^Купить Комбо$"),
    re.compile(r"^Купить$"),                          # broadcast CTA
    re.compile(r"^Купить со скидкой\s+\d+%.*$"),
    re.compile(r"^Продлить подписку$"),
    re.compile(r"^Продлить основную подписку$"),
    re.compile(r"^Продлить со скидкой\s+\d+%.*$"),
]

# Texts whose buttons should render `style="danger"` (красный) — реально
# деструктивные действия. Цвет сохраняет силу как "стоп-сигнал" — юзер
# реально видит и думает прежде чем нажать.
STYLE_DANGER_PATTERNS: list[re.Pattern] = [
    re.compile(r"^Удалить.*$"),          # «Удалить», «Удалить ключ», «Удалить аккаунт», «Удалить у юзеров»…
    re.compile(r"^Отозвать.*$"),         # «Отозвать», «Отозвать доступ», «Отозвать VIP»
    re.compile(r"^Отменить подписку$"),
    re.compile(r"^Отключить здесь$"),    # push-уведомления
    re.compile(r"^Очистить.*$"),         # «Очистить FAQ» (admin)
    re.compile(r"^Стоп$"),               # стоп удаления рассылки и т.п.
    re.compile(r"^Delete.*$"),           # английские варианты
    re.compile(r"^Remove.*$"),
    re.compile(r"^Revoke.*$"),
]


# ── Ступень 1: смысл по callback_data ────────────────────────────
# callback_data не переводится — это самый надёжный признак того, что
# за кнопка. Экран выбора способа оплаты (app/handlers/payments/
# method_select.py) собирает подписи из i18n, а callback_data держит
# фиксированным, поэтому немец и араб получают ту же иконку и тот же
# цвет, что и русский, даже если текст в словаре потом перепишут.
CALLBACK_EMOJI_MAP: dict[str, str] = {
    "pay:card_pl":  "5377377923076476823",   # Банковская карта (основной)
    "pay:sbp":      "5217837965547427903",   # СБП (основной)
    "pay:intl_pl":  "5375114475311484868",   # Международные платежи
    "pay:card":     "5375493342966597701",   # Карта резерв
    "pay:lava":     "5217961106554769883",   # СБП резерв
    "pay:stars":    "5269768891864746432",   # Telegram Stars
    "pay:crypto":   "5463219974132746636",   # CryptoBot
    "pay:balance":  "5402186569006210455",   # Оплата с внутреннего баланса
}

# Те же три рекомендованных способа оплаты — зелёные, независимо от языка.
CALLBACK_STYLE_MAP: dict[str, str] = {
    "pay:card_pl": "success",
    "pay:sbp":     "success",
    "pay:intl_pl": "success",
}


# ── Ступень 2: смысл по i18n-ключу ───────────────────────────────
# Здесь перечислены КЛЮЧИ словарей app/i18n/*.py, а не подписи. На
# старте модуля каждый ключ разворачивается в семь локализованных
# подписей (см. _build_localized_maps), и все они получают одну
# иконку/цвет. Состав списков ровно повторяет то, что русский юзер
# видит сегодня по текстовым таблицам ниже, — то есть русский UI не
# меняется, а остальные шесть языков наконец получают то же самое.
I18N_KEY_EMOJI_MAP: dict[str, str] = {
    # «Назад» во всех его видах (включая пагинацию «⬅️ Назад»).
    "common.back":                 "5416117059207572332",
    "admin.back":                  "5416117059207572332",
    "admin.back_to_broadcast":     "5416117059207572332",
    "admin.back_to_keys":          "5416117059207572332",
    "admin.prev":                  "5416117059207572332",
    "gift.page_prev":              "5416117059207572332",
    "buy.back_to_tariffs":         "5416117059207572332",
    "buy.corporate_back":          "5416117059207572332",
    "premium.back_button":         "5416117059207572332",
    "farm.back":                   "5416117059207572332",

    # Способы оплаты и кнопки «Оплатить …» на экранах провайдеров.
    "payment.card_pl":             "5377377923076476823",
    "main.pay_card":               "5377377923076476823",
    "payment.card_pl_pay_button":  "5377377923076476823",
    "main.pay_with_card":          "5377377923076476823",
    "payment.sbp":                 "5217837965547427903",
    "payment.sbp_pay_button":      "5217837965547427903",
    "payment.lava_pay_button":     "5217837965547427903",
    "traffic.pay_sbp":             "5217837965547427903",
    "farm.pay_sbp":                "5217837965547427903",
    "farm.shield_sbp_button":      "5217837965547427903",
    "payment.intl_pl":             "5375114475311484868",
    "payment.card":                "5375493342966597701",
    "payment.lava":                "5217961106554769883",
    "payment.stars":               "5269768891864746432",
    "payment.crypto":              "5463219974132746636",
    "payment.crypto_pay_button":   "5463219974132746636",
    "payment.balance":             "5402186569006210455",
    "main.pay_balance":            "5402186569006210455",

    # Игровой клуб.
    "games.button_bowling":        "5370853837689070338",
    "games.button_dice":           "5972061723400605896",
    "games.button_bomber":         "5280569974404966639",

    # Магазин.
    "shop.steam_main_button":      "4956506857901392912",

    # Выбор устройства в инструкциях.
    "instruction._device_ios":       "5821379843861778259",
    "instruction._download_ios":     "5821379843861778259",
    "instruction._device_android":   "6048857619848761040",
    "instruction._download_android": "6048857619848761040",
    "instruction._download_desktop": "5454081378943518859",
}

I18N_KEY_STYLE_MAP: dict[str, str] = {
    # Зелёные — рекомендованные способы оплаты.
    "payment.card_pl": "success",
    "main.pay_card":   "success",
    "payment.sbp":     "success",
    "farm.pay_sbp":    "success",
    "payment.intl_pl": "success",

    # Синие — главный CTA: купить / продлить подписку.
    "buy.vpn":                        "primary",
    "main.buy":                       "primary",
    "main.buy_new":                   "primary",
    "main.buy_renew":                 "primary",
    "traffic.buy_subscription":       "primary",
    "reminder.paid_7d_btn":           "primary",
    "reminder.paid_3h_discount_btn":  "primary",
    "trial.expired_discount_btn":     "primary",
    "trial.reminder_3h_discount_btn": "primary",

    # Красные — деструктивное.
    "admin.delete_discount": "danger",
}


# Плейсхолдер вида {balance:.2f} — подпись собирается через str.format,
# поэтому такие значения нельзя класть в exact-таблицу: до кнопки
# доедет «Баланс (доступно: 512.30 ₽)», а не сам шаблон.
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")


def _template_to_pattern(text: str) -> re.Pattern:
    """Шаблон i18n → регулярка, которой можно матчить готовую подпись.

    Литеральные куски экранируем, места плейсхолдеров заменяем на «.+?».
    Иначе локализованные кнопки с ценой/балансом внутри не опознаются
    ни на одном языке.
    """
    parts = [re.escape(p) for p in _PLACEHOLDER_RE.split(text)]
    return re.compile(".+?".join(parts))


# Автоматически собранные таблицы «локализованная подпись → значение».
LOCALIZED_EMOJI_MAP: dict[str, str] = {}
LOCALIZED_EMOJI_PATTERNS: list[tuple[re.Pattern, str]] = []
LOCALIZED_STYLE_MAP: dict[str, str] = {}
LOCALIZED_STYLE_PATTERNS: list[tuple[re.Pattern, str]] = []


def _build_localized_maps() -> None:
    """Развернуть i18n-ключи в подписи всех языков.

    Импортируем словари лениво, внутри функции: модуль подключается в
    main.py самым первым, до config, и тащить туда лишние зависимости
    на уровне импорта не хочется. app.i18n — плоские dict без побочных
    эффектов, так что это дёшево и безопасно.

    Если словари почему-то не поднялись, молча остаёмся на текстовых
    таблицах: кнопки без иконки — неприятно, упавший бот — хуже.
    """
    try:
        from app.i18n import LANGUAGES
    except Exception:          # pragma: no cover — защитная ветка
        return

    def _register(payload: str, value: str, target_map: dict, target_patterns: list) -> None:
        """payload — emoji_id или style; value — локализованная подпись."""
        if not isinstance(value, str) or "\n" in value or not value.strip():
            return
        stripped = _LEAD_EMOJI_RE.sub("", value, count=1).strip()
        if not stripped:
            return
        if _PLACEHOLDER_RE.search(stripped):
            target_patterns.append((_template_to_pattern(stripped), payload))
        else:
            # setdefault: первый язык, объявивший подпись, её и держит —
            # так одинаковые строки в разных языках не переопределяют
            # друг друга случайным порядком обхода.
            target_map.setdefault(stripped, payload)

    for lang_dict in LANGUAGES.values():
        for key, emoji_id in I18N_KEY_EMOJI_MAP.items():
            if key in lang_dict:
                _register(emoji_id, lang_dict[key], LOCALIZED_EMOJI_MAP, LOCALIZED_EMOJI_PATTERNS)
        for key, style in I18N_KEY_STYLE_MAP.items():
            if key in lang_dict:
                _register(style, lang_dict[key], LOCALIZED_STYLE_MAP, LOCALIZED_STYLE_PATTERNS)


_build_localized_maps()


def _has_success_style(stripped_text: str) -> bool:
    return any(p.fullmatch(stripped_text) for p in STYLE_SUCCESS_PATTERNS)


def _has_primary_style(stripped_text: str) -> bool:
    return any(p.fullmatch(stripped_text) for p in STYLE_PRIMARY_PATTERNS)


def _has_danger_style(stripped_text: str) -> bool:
    return any(p.fullmatch(stripped_text) for p in STYLE_DANGER_PATTERNS)


def _lookup_emoji(stripped_text: str, callback_data: str | None = None) -> str | None:
    """Подобрать премиум-иконку: callback_data → i18n → текстовые таблицы."""
    if callback_data:
        eid = CALLBACK_EMOJI_MAP.get(callback_data)
        if eid:
            return eid
    eid = TEXT_EMOJI_MAP.get(stripped_text)
    if eid:
        return eid
    for pattern, eid in TEXT_EMOJI_PATTERNS:
        if pattern.fullmatch(stripped_text):
            return eid
    eid = LOCALIZED_EMOJI_MAP.get(stripped_text)
    if eid:
        return eid
    for pattern, eid in LOCALIZED_EMOJI_PATTERNS:
        if pattern.fullmatch(stripped_text):
            return eid
    return None


def _lookup_style(stripped_text: str, callback_data: str | None = None) -> str | None:
    """Подобрать цвет кнопки: callback_data → текстовые правила → i18n.

    Порядок внутри текстовых правил прежний: success → primary → danger
    → нейтральный (style вообще не выставляем). Цвет — сигнал, а не
    украшение: 80% кнопок остаются серыми.
    """
    if callback_data:
        style = CALLBACK_STYLE_MAP.get(callback_data)
        if style:
            return style
    if _has_success_style(stripped_text):
        return "success"
    if _has_primary_style(stripped_text):
        return "primary"
    if _has_danger_style(stripped_text):
        return "danger"
    style = LOCALIZED_STYLE_MAP.get(stripped_text)
    if style:
        return style
    for pattern, style in LOCALIZED_STYLE_PATTERNS:
        if pattern.fullmatch(stripped_text):
            return style
    return None


def _danger_default_init(self, **kwargs):
    # Auto-injection only kicks in for plain-text buttons that the caller
    # didn't already decorate. Anything explicit (caller passed their own
    # icon_custom_emoji_id, style, or non-text-only button like url/web_app)
    # is left untouched on those particular fields.

    raw_text = kwargs.get("text", "") or ""
    stripped = _LEAD_EMOJI_RE.sub("", raw_text, count=1).strip()
    callback_data = kwargs.get("callback_data")
    if not isinstance(callback_data, str):
        callback_data = None

    if "icon_custom_emoji_id" not in kwargs:
        emoji_id = _lookup_emoji(stripped, callback_data)
        if emoji_id:
            kwargs["icon_custom_emoji_id"] = emoji_id
            # Ведущий юникод-эмодзи оставляем в тексте: он единственный
            # запасной глиф, если клиент не умеет icon_custom_emoji_id.
            # Подробности — в комментарии к флагу.
            if STRIP_UNICODE_EMOJI_ON_PREMIUM_ICON and stripped != raw_text:
                kwargs["text"] = stripped

    if "style" not in kwargs:
        style = _lookup_style(stripped, callback_data)
        if style:
            kwargs["style"] = style
        # else: leave kwargs without `style` — neutral default.
    _original_init(self, **kwargs)


# Idempotent: re-import doesn't double-wrap.
if not getattr(InlineKeyboardButton, "_atlas_danger_patched", False):
    InlineKeyboardButton.__init__ = _danger_default_init
    InlineKeyboardButton._atlas_danger_patched = True
