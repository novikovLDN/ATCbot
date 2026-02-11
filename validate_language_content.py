#!/usr/bin/env python3
"""
Production-grade i18n validation script.
Validates app/i18n language files for key consistency and translation quality.
"""

import re
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent
I18N_DIR = ROOT / "app" / "i18n"

LANGUAGE_CODES = ["ru", "en", "de", "kk", "uz", "tj", "ar"]
RU_CODE = "ru"
EN_CODE = "en"

# Keys that intentionally contain Cyrillic (native language names in selector)
CYRILLIC_ALLOWED_KEYS = frozenset({"lang.button_ru", "lang.button_kk", "lang.button_tj"})

# Languages to check for English bleed (de, kk, ar, uz, tj only; exclude ru, en)
ENGLISH_BLEED_CHECK_LANGS = frozenset({"de", "kk", "ar", "uz", "tj"})

# English bleed: detect by exact match with en.py (copy-paste without translation)
# Short single-word English suspects (e.g. "Error", "Payment")
ENGLISH_SUSPECT_WORDS = frozenset({
    "error", "payment", "success", "cancel", "confirm", "back", "select",
    "enter", "access", "invalid", "unlimited", "reject", "approve", "purchase",
    "renewal", "top-up", "friends", "friend", "link", "sent", "until",
})
MIN_SUSPECT_LENGTH = 4

# Cyrillic range
CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")


def load_languages() -> dict[str, dict]:
    """Load LANG dict from each language module."""
    result = {}
    for code in LANGUAGE_CODES:
        module_path = I18N_DIR / f"{code}.py"
        if not module_path.exists():
            print(f"WARNING: {module_path} not found, skipping.", file=sys.stderr)
            continue
        try:
            ns = {}
            exec(module_path.read_text(encoding="utf-8"), ns)
            result[code] = ns.get("LANG", {})
        except Exception as e:
            print(f"ERROR loading {code}.py: {e}", file=sys.stderr)
            result[code] = {}
    return result


def validate_keys(languages: dict[str, dict]) -> tuple[dict[str, list], dict[str, list]]:
    """
    Compare key sets across all languages.
    Returns: (missing_per_lang, extra_per_lang)
    """
    # Canonical key set = union of all keys
    all_keys_set = set()
    for lang_dict in languages.values():
        all_keys_set.update(lang_dict.keys())

    missing_per_lang = {}
    extra_per_lang = {}

    for code, lang_dict in languages.items():
        lang_keys = set(lang_dict.keys())
        missing = sorted(all_keys_set - lang_keys)
        extra = sorted(lang_keys - all_keys_set)
        if missing:
            missing_per_lang[code] = missing
        if extra:
            extra_per_lang[code] = extra

    return missing_per_lang, extra_per_lang


def detect_cyrillic(languages: dict[str, dict]) -> list[tuple[str, str, str]]:
    """
    Report Cyrillic in non-ru files (excluding lang.button_* native names).
    Returns list of (lang_code, key, value_preview).
    """
    violations = []
    for code, lang_dict in languages.items():
        if code == RU_CODE:
            continue
        for key, value in lang_dict.items():
            if key in CYRILLIC_ALLOWED_KEYS:
                continue
            if isinstance(value, str) and CYRILLIC_PATTERN.search(value):
                preview = value[:60] + "..." if len(value) > 60 else value
                preview = preview.replace("\n", "\\n")
                violations.append((code, key, preview))
    return violations


def _is_english_bleed(
    code: str, key: str, value: str, en_lang: dict
) -> bool:
    """
    Detect English bleed: value is exact copy from en.py, or short
    English suspect word.
    """
    if not isinstance(value, str) or code == EN_CODE:
        return False
    en_val = en_lang.get(key)
    # Exact match with en = copy-paste without translation
    if en_val is not None and value == en_val:
        return True
    # Short single-word English suspects (e.g. "Error", "Payment")
    stripped = value.strip()
    if len(stripped) >= MIN_SUSPECT_LENGTH and " " not in stripped:
        word_lower = stripped.lower()
        if word_lower in ENGLISH_SUSPECT_WORDS:
            return True
    return False


def detect_english_bleed(languages: dict[str, dict]) -> list[tuple[str, str, str]]:
    """
    Report English bleed in de/kk/ar/uz/tj: exact en.py copy or short English words.
    Returns list of (lang_code, key, value_preview).
    """
    en_lang = languages.get(EN_CODE, {})
    violations = []
    for code, lang_dict in languages.items():
        if code not in ENGLISH_BLEED_CHECK_LANGS:
            continue
        for key, value in lang_dict.items():
            if _is_english_bleed(code, key, value, en_lang):
                preview = value[:60] + "..." if len(value) > 60 else value
                preview = preview.replace("\n", "\\n")
                violations.append((code, key, preview))
    return violations


def main() -> int:
    print("Loading language modules...")
    languages = load_languages()
    if not languages:
        print("ERROR: No languages loaded.", file=sys.stderr)
        return 1

    has_violations = False

    # --- SECTION: Key mismatches ---
    print("\n" + "=" * 60)
    print("SECTION: Key mismatches")
    print("=" * 60)
    missing_per_lang, extra_per_lang = validate_keys(languages)

    if missing_per_lang:
        has_violations = True
        for code in sorted(missing_per_lang.keys()):
            missing = missing_per_lang[code]
            print(f"\n{code}.py — MISSING {len(missing)} keys:")
            for k in missing[:20]:
                print(f"  - {k}")
            if len(missing) > 20:
                print(f"  ... and {len(missing) - 20} more")

    if extra_per_lang:
        has_violations = True
        for code in sorted(extra_per_lang.keys()):
            extra = extra_per_lang[code]
            print(f"\n{code}.py — EXTRA {len(extra)} keys (not in canonical set):")
            for k in extra[:20]:
                print(f"  - {k}")
            if len(extra) > 20:
                print(f"  ... and {len(extra) - 20} more")

    if not missing_per_lang and not extra_per_lang:
        print("OK — All languages have identical key sets.")

    # --- SECTION: Cyrillic violations ---
    print("\n" + "=" * 60)
    print("SECTION: Cyrillic violations (Cyrillic outside ru.py)")
    print("=" * 60)
    cyrillic_violations = detect_cyrillic(languages)
    if cyrillic_violations:
        has_violations = True
        for code, key, preview in cyrillic_violations:
            print(f"\n{code}.py :: {key}")
            print(f"  Value: {preview}")
    else:
        print("OK — No Cyrillic outside ru.py.")

    # --- SECTION: English bleed ---
    print("\n" + "=" * 60)
    print("SECTION: English bleed (English-only values in non-en files)")
    print("=" * 60)
    english_bleed = detect_english_bleed(languages)
    if english_bleed:
        has_violations = True
        for code, key, preview in english_bleed:
            print(f"\n{code}.py :: {key}")
            print(f"  Value: {preview}")
    else:
        print("OK — No English bleed detected.")

    # --- Result ---
    print("\n" + "=" * 60)
    if has_violations:
        print("VALIDATION FAILED")
        return 1
    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
