"""
Global default `style="danger"` + automatic `icon_custom_emoji_id`
injection for every InlineKeyboardButton in the bot — Bot API 9.4 button color.

How `style` works:
    `InlineKeyboardButton` is a Pydantic v2 model. Pydantic builds field
    descriptors at class-creation time, so changing the field's default
    afterwards has no effect. Instead, we wrap the class's `__init__`:
    if `style` wasn't explicitly passed, we inject `"danger"` for most
    buttons, or `"success"` for the green-paid-method patterns in
    STYLE_SUCCESS_PATTERNS. Explicit `style="primary"` / `"success"` /
    `"danger"` from the caller is always respected.

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

_DEFAULT_STYLE = "danger"
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
    "Telegram Stars":            "5364173187858839320",
    "Stars":                     "5364173187858839320",
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


def _has_success_style(stripped_text: str) -> bool:
    return any(p.fullmatch(stripped_text) for p in STYLE_SUCCESS_PATTERNS)


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
        # Per-button green override for primary payment methods —
        # see STYLE_SUCCESS_PATTERNS above. Anything else stays on
        # the global default (`"danger"`, red).
        kwargs["style"] = "success" if _has_success_style(stripped) else _DEFAULT_STYLE
    _original_init(self, **kwargs)


# Idempotent: re-import doesn't double-wrap.
if not getattr(InlineKeyboardButton, "_atlas_danger_patched", False):
    InlineKeyboardButton.__init__ = _danger_default_init
    InlineKeyboardButton._atlas_danger_patched = True
