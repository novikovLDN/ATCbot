#!/usr/bin/env python3
"""Apply translation_patch_tj.json to app/i18n/tj.py."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATCH_FILE = ROOT / "translation_patch_tj.json"
TJ_FILE = ROOT / "app" / "i18n" / "tj.py"


def main() -> int:
    patch = json.loads(PATCH_FILE.read_text(encoding="utf-8"))
    content = TJ_FILE.read_text(encoding="utf-8")

    # Load LANG via exec
    ns = {}
    exec(content, ns)
    lang = ns["LANG"]
    orig_count = len(lang)
    orig_keys = set(lang.keys())

    # Apply patch (only for keys that exist in LANG)
    applied = 0
    for key, value in patch.items():
        if key in lang:
            lang[key] = value
            applied += 1

    # Build new content: header + LANG dict
    lines = ["# -*- coding: utf-8 -*-", '"""tj strings."""', "", "LANG = {"]
    for k, v in lang.items():
        # Use repr to escape properly for Python
        lines.append(f"    {repr(k)}: {repr(v)},")
    lines.append("}")

    new_content = "\n".join(lines) + "\n"
    TJ_FILE.write_text(new_content, encoding="utf-8")

    # Validate
    if len(lang) != orig_count:
        print(f"ERROR: Key count changed {orig_count} -> {len(lang)}", file=sys.stderr)
        return 1
    if set(lang.keys()) != orig_keys:
        print("ERROR: Keys changed", file=sys.stderr)
        return 1

    # Syntax check
    try:
        ns2 = {}
        exec(TJ_FILE.read_text(encoding="utf-8"), ns2)
    except SyntaxError as e:
        print(f"ERROR: Syntax error: {e}", file=sys.stderr)
        return 1

    print(f"Applied {applied} translations to {TJ_FILE}")
    print(f"Key count: {len(lang)} (unchanged)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
