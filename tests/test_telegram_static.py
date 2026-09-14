"""Static checks against Telegram Bot API limits and rules (docs/audit/10_telegram_static.md).

Telegram rejects the WHOLE message (not just the offending part) when any of
these is violated, so a single bad button or tag silently kills a screen:

* callback_data: 1-64 bytes (BUTTON_DATA_INVALID);
* HTML parse_mode: only the supported tags, properly nested ("can't parse entities");
* text 4096 / caption 1024 characters after entity parsing;
* /start payload: up to 64 chars of A-Za-z0-9_-;
* bot command: 1-32 chars of a-z0-9_, description 1-256;
* invoice: title 1-32, description 1-255, payload 1-128 bytes; Stars (XTR) — empty provider token.

Plus two things Telegram cannot see but users do: a get_text() call that does
not pass every {placeholder} (KeyError, or a raw "{name}" shown to the user),
and a button whose callback_data no handler answers (endless spinner).
"""
from __future__ import annotations

import ast
import itertools
import re
import string
import types
from pathlib import Path

import pytest

from app.i18n import LANGUAGES
from app.utils.telegram_html import TEXT_LIMIT, telegram_html_errors, visible_length

ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {".venv", "venv", "tests", "node_modules", "scripts", "dashboard", "docs", ".claude", ".git"}
_FORMATTER = string.Formatter()


def _prod_files() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*.py")
        if not (_SKIP_DIRS & set(p.relative_to(ROOT).parts))
    )


_TREES: dict[Path, ast.AST] = {}


def _tree(path: Path) -> ast.AST:
    if path not in _TREES:
        _TREES[path] = ast.parse(path.read_text(encoding="utf-8"))
    return _TREES[path]


def _loc(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}"


def _fields(text: str) -> set[str]:
    return {f.split(".")[0].split("[")[0] for _, f, _, _ in _FORMATTER.parse(text) if f is not None}


class _Any:
    """Dummy placeholder value: satisfies any format spec ({x:.2f}, {x:,})."""

    def __format__(self, spec):
        return "1"

    def __str__(self):
        return "1"


class _AnyDict(dict):
    def __missing__(self, key):
        return _Any()


def _render(text: str) -> str:
    return text.format_map(_AnyDict()) if _fields(text) else text


# ── 4. HTML parse_mode validity of every i18n string ─────────────────────

@pytest.mark.parametrize("lang", ["ru", "en"])
def test_every_i18n_string_is_valid_telegram_html(lang):
    bad = {}
    for key, text in LANGUAGES[lang].items():
        errors = telegram_html_errors(_render(text))
        if errors:
            bad[key] = errors
    assert bad == {}


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_every_i18n_string_fits_a_text_message(lang):
    too_long = {k: visible_length(_render(v)) for k, v in LANGUAGES[lang].items()
                if visible_length(_render(v)) > TEXT_LIMIT}
    assert too_long == {}


def test_validator_rejects_what_telegram_rejects():
    assert telegram_html_errors("<b>ok</b> & <i>fine</i> 5 > 3 &lt;x&gt;") == []
    assert telegram_html_errors('<tg-emoji emoji-id="5416117059207572332">⬅️</tg-emoji>') == []
    assert telegram_html_errors('<a href="https://t.me/x">x</a><blockquote expandable>q</blockquote>') == []
    assert telegram_html_errors("<pre><code class=\"language-python\">x</code></pre>") == []
    for bad in ("<b>x", "x</b>", "<b><i>x</b></i>", "<br>", "<div>x</div>", "a < b", "<span>x</span>",
                "<a>x</a>", "<tg-emoji>x</tg-emoji>", "&nbsp;"):
        assert telegram_html_errors(bad), bad


# ── 5. Format placeholders ───────────────────────────────────────────────

# EN strings that show one value more than RU. Every call site passes it
# (test_get_text_calls_supply_every_placeholder guards that).
_EN_ONLY_PLACEHOLDERS = {
    "buy.button_price": {"gb"},
    "buy.button_price_discount": {"gb"},
    "trial.activated": {"expires_date"},
}


def test_ru_and_en_use_the_same_placeholders():
    ru, en = LANGUAGES["ru"], LANGUAGES["en"]
    diff = {}
    for key in ru.keys() & en.keys():
        fr, fe = _fields(ru[key]), _fields(en[key])
        if fr != fe and (fe - fr != _EN_ONLY_PLACEHOLDERS.get(key) or fr - fe):
            diff[key] = {"ru_only": sorted(fr - fe), "en_only": sorted(fe - fr)}
    assert diff == {}


_GET_TEXT_NAMES = {"get_text", "i18n_get_text", "_t"}


def test_get_text_calls_supply_every_placeholder():
    """get_text formats only when kwargs are given: a missing kwarg is a
    KeyError at runtime, no kwargs at all shows the raw "{name}" to the user."""
    problems, scanned = [], 0
    for path in _prod_files():
        tree = _tree(path)
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            key_node = node.args[1]
            if name not in _GET_TEXT_NAMES or not (
                isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
            ):
                continue
            key = key_node.value
            if key not in LANGUAGES["ru"] and key not in LANGUAGES["en"]:
                continue
            if any(kw.arg is None for kw in node.keywords):  # **kwargs: dynamic
                continue
            scanned += 1
            supplied = {kw.arg for kw in node.keywords} - {"strict"}
            parent = parents.get(node)
            formatted_later = isinstance(parent, ast.Attribute) and parent.attr in ("format", "format_map")
            for lang in ("ru", "en"):
                text = LANGUAGES[lang].get(key)
                if text is None:
                    continue
                need = _fields(text)
                if not supplied:
                    if need and not formatted_later:
                        problems.append(f"{_loc(path, node)} {lang}:{key} shows raw {sorted(need)}")
                elif need - supplied:
                    problems.append(f"{_loc(path, node)} {lang}:{key} KeyError {sorted(need - supplied)}")
    assert scanned > 500, f"scanner found only {scanned} get_text calls"
    assert problems == []


# ── 1. callback_data ≤ 64 bytes ──────────────────────────────────────────

def _placeholder_name(node: ast.FormattedValue) -> str:
    v = node.value
    while isinstance(v, (ast.Attribute, ast.Subscript, ast.BinOp)):
        if isinstance(v, ast.Attribute):
            return v.attr
        if isinstance(v, ast.Subscript):
            s = v.slice
            if isinstance(s, ast.Constant):
                return str(s.value)
            v = v.value
        else:
            v = v.left
    return getattr(v, "id", "")


def _placeholder_bounds() -> dict[str, int]:
    """Worst-case length of a formatted value by its name. Anything not listed
    is assumed to be an int64 id (Telegram user id, DB id): 20 chars."""
    from app.handlers.callbacks.navigation import _APPLE_NOMINALS

    return {
        # 🔒 shop: apple_send_key:{buyer_id}:{region}:{nominal}
        "region": max(len(r) for r in _APPLE_NOMINALS),
        "nominal": max(len(str(v)) for vs in _APPLE_NOMINALS.values() for v in vs),
    }


def _callback_data_nodes():
    for path in _prod_files():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.keyword) and node.arg == "callback_data":
                yield path, node.value


def test_callback_data_fits_64_bytes():
    bounds = _placeholder_bounds()
    too_long, checked = [], 0
    for path, value in _callback_data_nodes():
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            size = len(value.value.encode("utf-8"))
        elif isinstance(value, ast.JoinedStr):
            size = sum(
                len(part.value.encode("utf-8")) if isinstance(part, ast.Constant)
                else bounds.get(_placeholder_name(part), 20)
                for part in value.values
            )
        else:
            continue  # variables: their literal values are checked where assigned
        checked += 1
        if not 1 <= size <= 64:
            too_long.append(f"{_loc(path, value)} {ast.unparse(value)} -> {size} bytes")
    assert checked > 300
    assert too_long == []


def test_purchase_id_in_callback_data_fits():
    """pay:wata:check:{purchase_id} — the pending-purchase id format."""
    import inspect

    import database.subscriptions as subs

    src = inspect.getsource(subs)
    assert 'purchase_id = f"purchase_{uuid_lib.uuid4().hex[:16]}"' in src
    assert len("pay:wata:check:" + "purchase_" + "f" * 16) <= 64


# ── 7. every emitted callback_data has a handler ─────────────────────────

_SAMPLES = ["1", "basic", "plus", "combo_basic", "ios", "android", "standard", "bypass", "on", "off",
            "card", "sbp", "wata", "happ", "incy", "0", "30", "10", "premium", "family", "individual",
            "usa", "cat"]


def _callback_handlers():
    from magic_filter import MagicFilter

    from app.handlers import router as root_router

    def walk(r):
        yield r
        for s in r.sub_routers:
            yield from walk(s)

    handlers = [h for r in walk(root_router) for h in r.callback_query.handlers]

    def matches(handler, data: str) -> bool:
        event = types.SimpleNamespace(data=data, message=None, from_user=types.SimpleNamespace(id=1))
        saw_data_filter = False
        for f in handler.filters or []:
            cb = f.callback
            is_magic = getattr(f, "magic", None) is not None or isinstance(getattr(cb, "__self__", None), MagicFilter)
            is_lambda = isinstance(cb, types.FunctionType) and cb.__name__ == "<lambda>"
            if not (is_magic or is_lambda):
                continue  # state / custom filters: not about data
            saw_data_filter = True
            try:
                if not cb(event):
                    return False
            except Exception:
                return False
        return saw_data_filter

    return handlers, matches


def _candidates(value: ast.AST) -> list[str]:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return [value.value]
    if isinstance(value, ast.JoinedStr):
        holes = sum(isinstance(p, ast.FormattedValue) for p in value.values)
        if not holes:
            return ["".join(p.value for p in value.values)]
        out = []
        for combo in itertools.product(_SAMPLES, repeat=min(holes, 2)):
            fill = iter(list(combo) + [combo[-1]] * holes)
            out.append("".join(p.value if isinstance(p, ast.Constant) else next(fill) for p in value.values))
        return out
    return []


def test_every_emitted_callback_data_has_a_handler():
    handlers, matches = _callback_handlers()
    assert len(handlers) > 100
    assert not any(matches(h, "zz_no_such_button") for h in handlers), "matcher is vacuous"
    orphans, checked = [], 0
    for path, value in _callback_data_nodes():
        cands = _candidates(value)
        if not cands:
            continue
        checked += 1
        if not any(matches(h, c) for c in cands for h in handlers):
            orphans.append(f"{_loc(path, value)} {cands[0]}")
    assert checked > 300
    assert orphans == []


# ── 7. button shape: exactly one action, valid URL ───────────────────────

_ACTION_FIELDS = {"callback_data", "url", "web_app", "login_url", "switch_inline_query",
                  "switch_inline_query_current_chat", "switch_inline_query_chosen_chat",
                  "copy_text", "callback_game", "pay"}


def test_inline_buttons_have_one_action_and_valid_url():
    problems = []
    for path in _prod_files():
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)) != "InlineKeyboardButton":
                continue
            if any(kw.arg is None for kw in node.keywords):
                continue
            kws = {kw.arg: kw.value for kw in node.keywords}
            actions = _ACTION_FIELDS & kws.keys()
            if len(actions) != 1:
                problems.append(f"{_loc(path, node)} actions={sorted(actions)}")
            url = kws.get("url")
            if isinstance(url, ast.Constant) and not str(url.value).startswith(("https://", "tg://")):
                problems.append(f"{_loc(path, node)} url={url.value!r}")
            text = kws.get("text")
            if isinstance(text, ast.Constant) and not (1 <= len(str(text.value)) <= 64):
                problems.append(f"{_loc(path, node)} text length {len(str(text.value))}")
    assert problems == []


def test_premium_emoji_ids_are_numeric():
    from app.handlers.common.keyboards import CE
    from app.utils import button_defaults as bd

    ids = list(CE.values()) + list(bd.TEXT_EMOJI_MAP.values()) + [eid for _, eid in bd.TEXT_EMOJI_PATTERNS]
    assert ids and all(isinstance(i, str) and i.isdigit() for i in ids)


# ── 2. deep links ────────────────────────────────────────────────────────

_START_PAYLOAD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def test_deep_link_payloads_are_valid():
    import database.bypass_gift_links as bgl
    import database.marketing_links as ml
    from database.admin import generate_gift_code
    from database.users import _random_referral_code, generate_referral_code

    payloads = []
    for _ in range(50):
        payloads += [
            "gift_" + generate_gift_code(),
            "ref_" + _random_referral_code(),
            "refd_" + _random_referral_code(),
            "s-" + ml._gen_slug(),
            "p-" + ml._gen_slug(),
            "bgift_" + bgl.generate_bypass_gift_code(),
        ]
    payloads += ["ref_" + generate_referral_code(tid) for tid in (1, 7_999_999_999, 9_999_999_999_999)]
    payloads += ["ref_9999999999999", "refd_9999999999999"]  # legacy fallback: ref_<telegram_id>
    assert [p for p in payloads if not _START_PAYLOAD.match(p)] == []


def test_deep_links_use_the_real_bot_username():
    """t.me links must not hardcode the bot: the stage bot would send users to prod."""
    hardcoded = []
    for path in _prod_files():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "?start=" in node.value \
                    and re.search(r"t\.me/[A-Za-z]", node.value):
                hardcoded.append(f"{_loc(path, node)} {node.value}")
    # The STAGE-only gate (_show_stage_gate) deliberately sends a tester to the prod bot.
    allowed = "https://t.me/atlassecure_bot?start=ref_RC26QG"
    assert [h for h in hardcoded if not (h.startswith("app/handlers/user/start.py:") and h.endswith(allowed))] == []


# ── 8. bot commands ──────────────────────────────────────────────────────

def _menu_commands() -> list[tuple[str, str]]:
    out = []
    for node in ast.walk(_tree(ROOT / "main.py")):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "BotCommand":
            kw = {k.arg: k.value.value for k in node.keywords if isinstance(k.value, ast.Constant)}
            out.append((kw["command"], kw["description"]))
    return out


def test_menu_commands_are_valid_and_handled():
    from aiogram.filters import Command

    from app.handlers import router as root_router

    def walk(r):
        yield r
        for s in r.sub_routers:
            yield from walk(s)

    handled = set()
    for r in walk(root_router):
        for h in r.message.handlers:
            for f in h.filters or []:
                if isinstance(f.callback, Command):
                    handled |= {c for c in f.callback.commands if isinstance(c, str)}
    menu = _menu_commands()
    assert len(menu) >= 10
    for cmd, desc in menu:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", cmd), cmd
        assert 1 <= len(desc) <= 256, cmd
        assert cmd in handled, f"/{cmd} is in the menu but has no handler"


# ── 9. invoices (VPN part; the shop is only reported) ────────────────────

def test_invoice_texts_fit_limits():
    worst = {"tariff_name": "W" * 20, "months": 24, "amount": 100000}
    limits = {
        "buy.invoice_description": 255, "payment.stars_invoice_description": 255,
        "main.topup_invoice_description": 255, "main.topup_invoice_title": 32,
    }
    for lang in ("ru", "en"):
        for key, limit in limits.items():
            text = LANGUAGES[lang][key].format(**worst)
            assert 1 <= len(text) <= limit, (lang, key, len(text))
        names = [v for k, v in LANGUAGES[lang].items() if k.startswith("tariff.name_")]
        assert max(map(len, names)) <= 20


def test_send_invoice_calls_follow_the_rules():
    problems, seen = [], 0
    for path in _prod_files():
        for node in ast.walk(_tree(path)):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "send_invoice"):
                continue
            seen += 1
            kw = {k.arg: k.value for k in node.keywords}
            title = kw.get("title")
            if isinstance(title, ast.Constant) and not 1 <= len(title.value) <= 32:
                problems.append(f"{_loc(path, node)} title {len(title.value)}")
            cur = kw.get("currency")
            if isinstance(cur, ast.Constant) and cur.value == "XTR":
                tok = kw.get("provider_token")
                if tok is not None and not (isinstance(tok, ast.Constant) and tok.value == ""):
                    problems.append(f"{_loc(path, node)} XTR with a provider token")
            payload = kw.get("payload")
            if isinstance(payload, ast.JoinedStr):
                size = sum(len(p.value) if isinstance(p, ast.Constant) else 36 for p in payload.values)
                if size > 128:
                    problems.append(f"{_loc(path, node)} payload up to {size} bytes")
    assert seen >= 5
    assert problems == []


def test_stars_amount_is_a_positive_integer():
    from app.services import tariffs

    for key in ("basic", "plus", "combo_basic", "combo_plus"):
        for days in (30, 90, 180, 365):
            try:
                full = tariffs.renewal_price_rub(key, days)
            except Exception:
                continue
            for kop in (100, full * 50, full * 100, 0):
                stars = tariffs.stars_for_purchase(key, days, kop)
                assert isinstance(stars, int) and stars >= 1, (key, days, kop, stars)
