"""
Plain-text helpers for texts that leave the bot's own HTML messages.

Why this exists:
    Bot messages are sent with parse_mode="HTML", so i18n strings contain
    <b>, <code>, <blockquote> and premium-emoji markup
    (<tg-emoji emoji-id="…">🎁</tg-emoji>). None of that is parsed when the
    same string goes into a channel that Telegram treats as plain text:

      * https://t.me/share/url?url=…&text=… — the text is pre-filled in the
        user's own compose box and sent as a user message; no HTML parsing,
        and custom (premium) emoji cannot be sent by a bot-built link;
      * switch_inline_query / SwitchInlineQueryChosenChat query strings;
      * callback.answer(..., show_alert=True) alerts and toasts.

    Putting an HTML i18n string there shows the raw markup to the user.
    Run it through html_to_plain() first, and build share links with
    build_share_url() so the text is also correctly URL-encoded.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote

# <tg-emoji emoji-id="…">X</tg-emoji> → X (the fallback unicode emoji inside).
_TG_EMOJI_RE = re.compile(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", re.IGNORECASE | re.DOTALL)
# <br>, <br/> → newline (not Telegram HTML, but harmless to support).
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Block-level tags whose content should stand on its own lines.
_BLOCK_OPEN_RE = re.compile(r"\s*<(blockquote|pre)\b[^>]*>", re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r"</(blockquote|pre)\s*>\s*", re.IGNORECASE)
# Any remaining tag (<b>, </i>, <a href="…">, <tg-spoiler>, <code>, …).
_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*\b[^>]*>")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")


def html_to_plain(text: str | None) -> str:
    """Convert a Telegram-HTML string to clean plain text.

    - <tg-emoji emoji-id="…">🎁</tg-emoji> → 🎁 (the fallback emoji);
    - <blockquote>/<pre> content is kept on its own paragraph;
    - every other tag (<b>, <i>, <u>, <s>, <code>, <a>, <tg-spoiler>, …) is
      dropped, its content is kept;
    - entities (&amp; &lt; &gt; &quot; &#39; …) are unescaped *after* the
      tags are removed, so an escaped "&lt;b&gt;" survives as literal "<b>";
    - trailing spaces are trimmed and runs of 3+ newlines collapse to one
      blank line.
    """
    if not text:
        return ""
    out = _TG_EMOJI_RE.sub(r"\1", text)
    out = _BR_RE.sub("\n", out)
    out = _BLOCK_OPEN_RE.sub("\n\n", out)
    out = _BLOCK_CLOSE_RE.sub("\n\n", out)
    out = _TAG_RE.sub("", out)
    out = html.unescape(out)
    out = _TRAILING_WS_RE.sub("\n", out)
    out = _MANY_NEWLINES_RE.sub("\n\n", out)
    return out.strip()


def build_share_url(url: str, text: str | None = None) -> str:
    """Build a https://t.me/share/url link with a plain, fully encoded text.

    The text is passed through html_to_plain() so i18n strings with HTML or
    premium-emoji markup never reach the user's compose box raw. Both
    parameters are encoded with quote(safe="") so '&', '?', '#', '/' and
    newlines cannot break the query string.
    """
    share = f"https://t.me/share/url?url={quote(url, safe='')}"
    plain = html_to_plain(text)
    if plain:
        share += f"&text={quote(plain, safe='')}"
    return share
