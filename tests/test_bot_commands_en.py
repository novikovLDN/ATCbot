"""docs/audit/10_telegram_static.md §8 / open item 5: the command menu was
registered only in Russian (set_my_commands without language_code), so
English users saw Russian descriptions. main.py now registers the same
commands a second time with language_code="en" and English descriptions.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _menus() -> dict[str | None, list[tuple[str, str]]]:
    """language_code (None = default) -> [(command, description)] per set_my_commands call."""
    menus: dict[str | None, list[tuple[str, str]]] = {}
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "set_my_commands":
            kw = {k.arg: k.value for k in node.keywords}
            lang = kw["language_code"].value if "language_code" in kw else None
            cmds_node = node.args[0] if node.args else kw["commands"]
            items = []
            for c in cmds_node.elts:
                ck = {k.arg: k.value.value for k in c.keywords}
                items.append((ck["command"], ck["description"]))
            menus[lang] = items
    return menus


def test_english_menu_has_the_same_commands_in_english():
    menus = _menus()
    assert None in menus, "default (Russian) menu is missing"
    assert "en" in menus, "no set_my_commands(..., language_code='en')"
    assert [c for c, _ in menus["en"]] == [c for c, _ in menus[None]]
    for cmd, desc in menus["en"]:
        assert 1 <= len(desc) <= 256, cmd
        assert not CYRILLIC.search(desc), f"/{cmd}: {desc!r} is not English"
