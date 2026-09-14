"""Every literal key passed to get_text()/i18n_get_text() exists in ru and en.

get_text's third positional argument is a deprecated `strict` flag, not a
default text: a missing key is shown to the user as the raw key.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.i18n import LANGUAGES

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r"""\b(?:i18n_get_text|get_text|_t)\(\s*[\w.\[\]"']+\s*,\s*["']([a-z0-9_]+(?:\.[a-z0-9_]+)+)["']""")


def _used_keys() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in (ROOT / "app").rglob("*.py"):
        for m in CALL.finditer(path.read_text(encoding="utf-8")):
            if not m.group(1).endswith("_"):  # "prefix_" + suffix: dynamic key
                found.setdefault(m.group(1), str(path.relative_to(ROOT)))
    return found


def test_every_literal_i18n_key_exists_in_ru_and_en():
    used = _used_keys()
    assert used, "scanner found no get_text calls"
    missing = {
        lang: sorted(f"{k} ({src})" for k, src in used.items() if k not in LANGUAGES[lang])
        for lang in ("ru", "en")
    }
    assert missing == {"ru": [], "en": []}
