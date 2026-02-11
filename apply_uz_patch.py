#!/usr/bin/env python3
"""Apply translation_patch_uz.json to app/i18n/uz.py."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATCH_FILE = ROOT / "translation_patch_uz.json"
UZ_FILE = ROOT / "app" / "i18n" / "uz.py"


def main() -> int:
    patch = json.loads(PATCH_FILE.read_text(encoding="utf-8"))
    content = UZ_FILE.read_text(encoding="utf-8")

    ns = {}
    exec(content, ns)
    lang = ns["LANG"]
    orig_count = len(lang)
    orig_keys = set(lang.keys())

    applied = 0
    for key, value in patch.items():
        if key in lang:
            lang[key] = value
            applied += 1

    lines = ["# -*- coding: utf-8 -*-", '"""uz strings."""', "", "LANG = {"]
    for k, v in lang.items():
        lines.append(f"    {repr(k)}: {repr(v)},")
    lines.append("}")

    UZ_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if len(lang) != orig_count:
        print(f"ERROR: Key count changed {orig_count} -> {len(lang)}", file=sys.stderr)
        return 1
    if set(lang.keys()) != orig_keys:
        print("ERROR: Keys changed", file=sys.stderr)
        return 1

    try:
        exec(UZ_FILE.read_text(encoding="utf-8"), {})
    except SyntaxError as e:
        print(f"ERROR: Syntax error: {e}", file=sys.stderr)
        return 1

    print(f"Applied {applied} translations to {UZ_FILE}")
    print(f"Key count: {len(lang)} (unchanged)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
