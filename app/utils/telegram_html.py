"""Telegram Bot API HTML (parse_mode="HTML") helpers.

`telegram_html_errors(text)` mirrors what Telegram's parser rejects with
"Bad Request: can't parse entities": a `<` that does not open a supported
tag, an unsupported tag, a mismatched / unclosed tag, a `span` without
class="tg-spoiler", an `a` without href, a `tg-emoji` without a numeric
emoji-id. A bare `&` or `>` is accepted by Telegram as literal text; an
unknown named entity (`&nbsp;`) is not decoded, so it is reported too.

`visible_length(text)` is the length Telegram checks against the 4096 (text)
and 1024 (caption) limits: tags stripped, entities decoded, counted in
UTF-16 code units (an emoji outside the BMP counts as 2).
"""
from __future__ import annotations

import html
import re

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024

ALLOWED_TAGS = frozenset({
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "span", "tg-spoiler", "a", "tg-emoji", "code", "pre", "blockquote",
})
_NAMED_ENTITIES = frozenset({"lt", "gt", "amp", "quot"})

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s[^<>]*)?)>")
_ENTITY_RE = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z]+);")
_ATTR_RE = re.compile(r"""([a-zA-Z-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")


def _attrs(raw: str) -> dict[str, str]:
    return {m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or "") for m in _ATTR_RE.finditer(raw or "")}


def telegram_html_errors(text: str) -> list[str]:
    """Reasons Telegram would reject `text` sent with parse_mode="HTML" ([] = valid)."""
    errors: list[str] = []
    stack: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "<":
            m = _TAG_RE.match(text, i)
            if not m:
                errors.append(f"'<' does not open a tag at {i}: {text[i:i + 20]!r}")
                i += 1
                continue
            closing, name, raw_attrs = m.group(1), m.group(2).lower(), m.group(3)
            if name not in ALLOWED_TAGS:
                errors.append(f"unsupported tag <{closing}{name}>")
            elif closing:
                if not stack or stack[-1] != name:
                    errors.append(f"</{name}> does not close the open tag (open: {stack})")
                else:
                    stack.pop()
            else:
                attrs = _attrs(raw_attrs)
                if name == "span" and attrs.get("class") != "tg-spoiler":
                    errors.append('<span> without class="tg-spoiler"')
                if name == "a" and not attrs.get("href"):
                    errors.append("<a> without href")
                if name == "tg-emoji" and not attrs.get("emoji-id", "").isdigit():
                    errors.append("<tg-emoji> without a numeric emoji-id")
                if name in ("pre", "code") and stack and stack[-1] in ("pre", "code") and not (
                    name == "code" and stack[-1] == "pre"
                ):
                    errors.append(f"<{name}> nested in <{stack[-1]}>")
                stack.append(name)
            i = m.end()
            continue
        if ch == "&":
            m = _ENTITY_RE.match(text, i)
            if m:
                ent = m.group(1)
                if not ent.startswith("#") and ent not in _NAMED_ENTITIES:
                    errors.append(f"unsupported entity &{ent};")
                i = m.end()
                continue
        i += 1
    if stack:
        errors.append(f"unclosed tags {stack}")
    return errors


def visible_text(text: str) -> str:
    """Text as the user sees it (tags stripped, entities decoded)."""
    return html.unescape(_TAG_RE.sub("", text))


def visible_length(text: str) -> int:
    """Length Telegram checks against TEXT_LIMIT / CAPTION_LIMIT (UTF-16 units)."""
    return len(visible_text(text).encode("utf-16-le")) // 2


def truncate_plain(text: str, limit: int) -> str:
    """Cut plain (not HTML) text to `limit` UTF-16 units, ending with «…»."""
    if limit <= 0:
        return ""
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    out, used = [], 0
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if used + width > limit - 1:
            break
        out.append(ch)
        used += width
    return "".join(out).rstrip() + "…"
