#!/usr/bin/env python3
"""
Generate English bleed extraction report for i18n languages.
Exports problematic keys per language to translation_tasks.json.
"""

import json
import re
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent
I18N_DIR = ROOT / "app" / "i18n"
OUTPUT_FILE = ROOT / "translation_tasks.json"

# Languages to scan (de, kk, uz, tj, ar; exclude ru, en)
TARGET_LANGUAGES = ["de", "kk", "uz", "tj", "ar"]

# Latin-script non-English (e.g. Uzbek): skip regex, only substring check
LATIN_SCRIPT_LANGS = frozenset({"uz"})

# English-only regex: value is purely A-Za-z0-9, space, common punctuation
ENGLISH_ONLY_PATTERN = re.compile(r"^[A-Za-z0-9 ,.!?:;'\"()\-\n]+$")
MIN_LENGTH = 5  # length > 4

# Substrings that indicate untranslated English
ENGLISH_SUBSTRINGS = frozenset({
    "Error", "Payment", "Subscription", "Balance",
    "Access", "Trial", "Renew", "Profile", "Support", "Policy",
})


def load_lang(code: str) -> dict:
    """Load LANG dict from a language module."""
    module_path = I18N_DIR / f"{code}.py"
    if not module_path.exists():
        return {}
    try:
        ns = {}
        exec(module_path.read_text(encoding="utf-8"), ns)
        return ns.get("LANG", {})
    except Exception as e:
        print(f"ERROR loading {code}.py: {e}", file=sys.stderr)
        return {}


def is_english_bleed(value: str, lang_code: str = "") -> bool:
    """
    Detect English bleed: value matches English-only regex with length > 4,
    or contains known English substrings. For Latin-script langs (uz), skip regex.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    # Regex: purely English chars, length > 4 (skip for Latin-script non-English)
    if lang_code not in LATIN_SCRIPT_LANGS:
        if len(value.strip()) > MIN_LENGTH and ENGLISH_ONLY_PATTERN.match(value):
            return True
    # Contains English substring
    for sub in ENGLISH_SUBSTRINGS:
        if sub in value:
            return True
    return False


def extract_tasks() -> dict[str, dict[str, str]]:
    """Extract keys with English bleed per language."""
    result = {}
    for code in TARGET_LANGUAGES:
        lang = load_lang(code)
        tasks = {}
        for key, value in lang.items():
            if is_english_bleed(value, code):
                tasks[key] = value
        result[code] = dict(sorted(tasks.items()))
    return result


def main() -> int:
    print("Extracting English bleed keys...")
    tasks = extract_tasks()

    # Write JSON
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

    # Print summary
    print(f"\nOutput: {OUTPUT_FILE}\n")
    print("Summary:")
    for code in TARGET_LANGUAGES:
        count = len(tasks.get(code, {}))
        print(f"  {code}: {count} keys")

    return 0


if __name__ == "__main__":
    sys.exit(main())
