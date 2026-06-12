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
    every InlineKeyboardButton's `text` is matched against
    `TEXT_EMOJI_MAP` and `TEXT_EMOJI_PATTERNS` after a leading
    unicode-emoji prefix is stripped. On a hit:
      • `icon_custom_emoji_id` is set (unless the caller already
        passed one — explicit always wins),
      • the leading unicode emoji is removed from `text` so we don't
        get two emojis side-by-side on clients with Bot API 9.4.

When to import:
    Once, as early as possible — BEFORE the handler modules load. The
    canonical place is the top of `main.py`, right after `setup_logging()`.

Adding new entries:
    Either drop an exact-text → emoji_id pair into `TEXT_EMOJI_MAP`,
    or, if the text varies (currency, locale, traffic amount), add a
    compiled regex + emoji_id to `TEXT_EMOJI_PATTERNS`.
"""

import re

from aiogram.types import InlineKeyboardButton

_original_init = InlineKeyboardButton.__init__

# Anything that isn't a word char (Unicode-aware) or whitespace at the
# very start of the text — covers emoji, pictographs, dingbats. Also
# eats the trailing space the prefix is usually followed by.
_LEAD_EMOJI_RE = re.compile(r"^[^\w\s]+\s*", flags=re.UNICODE)

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


def _has_success_style(stripped_text: str) -> bool:
    return any(p.fullmatch(stripped_text) for p in STYLE_SUCCESS_PATTERNS)


def _has_primary_style(stripped_text: str) -> bool:
    return any(p.fullmatch(stripped_text) for p in STYLE_PRIMARY_PATTERNS)


def _has_danger_style(stripped_text: str) -> bool:
    return any(p.fullmatch(stripped_text) for p in STYLE_DANGER_PATTERNS)


def _lookup_emoji(stripped_text: str) -> str | None:
    eid = TEXT_EMOJI_MAP.get(stripped_text)
    if eid:
        return eid
    for pattern, eid in TEXT_EMOJI_PATTERNS:
        if pattern.fullmatch(stripped_text):
            return eid
    return None


def _danger_default_init(self, **kwargs):
    # Auto-injection only kicks in for plain-text buttons that the caller
    # didn't already decorate. Anything explicit (caller passed their own
    # icon_custom_emoji_id, style, or non-text-only button like url/web_app)
    # is left untouched on those particular fields.

    raw_text = kwargs.get("text", "") or ""
    stripped = _LEAD_EMOJI_RE.sub("", raw_text, count=1).strip()

    if "icon_custom_emoji_id" not in kwargs:
        emoji_id = _lookup_emoji(stripped)
        if emoji_id:
            kwargs["icon_custom_emoji_id"] = emoji_id
            # Replace text with the stripped version — otherwise
            # supported clients show both unicode + premium emoji.
            if stripped != raw_text:
                kwargs["text"] = stripped

    if "style" not in kwargs:
        # Priority: success → primary → danger → default (None).
        # Default means «не ставим style» → Telegram render как
        # нейтральная сероватая кнопка. 80% UI остаётся таким —
        # цветом подкрашиваем только акцент.
        if _has_success_style(stripped):
            kwargs["style"] = "success"
        elif _has_primary_style(stripped):
            kwargs["style"] = "primary"
        elif _has_danger_style(stripped):
            kwargs["style"] = "danger"
        # else: leave kwargs without `style` — neutral default.
    _original_init(self, **kwargs)


# Idempotent: re-import doesn't double-wrap.
if not getattr(InlineKeyboardButton, "_atlas_danger_patched", False):
    InlineKeyboardButton.__init__ = _danger_default_init
    InlineKeyboardButton._atlas_danger_patched = True
