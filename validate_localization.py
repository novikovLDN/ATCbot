#!/usr/bin/env python3
"""
Localization validation script for ATCS project.

Validates:
1. RU keys vs all other languages - detects missing keys
2. Placeholder mismatch (e.g. {count} vs {count} consistency)
3. Extra keys in non-RU languages
4. Structured report for CI

Exit code 1 if validation fails (for CI).
"""

import re
import sys
from typing import Dict, Set, List, Tuple

import localization


def extract_placeholders(text: str) -> Set[str]:
    """Extract placeholder names from format string (e.g., {count}, {level})."""
    if not isinstance(text, str):
        return set()
    pattern = r'\{(\w+)\}'
    return set(re.findall(pattern, text))


def validate_localization() -> Tuple[bool, List[str], List[str]]:
    """
    Validate translation coverage and consistency.
    
    Returns:
        Tuple of (success, errors, warnings)
    """
    errors: List[str] = []
    warnings: List[str] = []
    
    if not hasattr(localization, 'TEXTS'):
        errors.append("localization.TEXTS not found")
        return False, errors, warnings
    
    languages = list(localization.TEXTS.keys())
    if not languages:
        errors.append("No languages found in TEXTS")
        return False, errors, warnings
    
    if "ru" not in languages:
        errors.append("Russian (ru) not found - required as canonical reference")
        return False, errors, warnings
    
    ru_keys = set(localization.TEXTS["ru"].keys())
    
    for lang in languages:
        if lang == "ru":
            continue
        
        lang_keys = set(localization.TEXTS[lang].keys())
        
        # Missing keys
        missing = ru_keys - lang_keys
        if missing:
            errors.append(
                f"[{lang}] Missing {len(missing)} keys: "
                f"{', '.join(sorted(missing)[:15])}"
                + (f" ... and {len(missing) - 15} more" if len(missing) > 15 else "")
            )
        
        # Extra keys
        extra = lang_keys - ru_keys
        if extra:
            warnings.append(
                f"[{lang}] Extra keys not in RU: "
                f"{', '.join(sorted(extra)[:10])}"
                + (f" ... and {len(extra) - 10} more" if len(extra) > 10 else "")
            )
        
        # Placeholder consistency
        common_keys = ru_keys & lang_keys
        for key in common_keys:
            ru_text = localization.TEXTS["ru"].get(key, "")
            lang_text = localization.TEXTS[lang].get(key, "")
            
            ru_placeholders = extract_placeholders(ru_text)
            lang_placeholders = extract_placeholders(lang_text)
            
            if ru_placeholders != lang_placeholders:
                errors.append(
                    f"[{lang}] Placeholder mismatch for '{key}': "
                    f"RU has {ru_placeholders}, {lang.upper()} has {lang_placeholders}"
                )
    
    return len(errors) == 0, errors, warnings


def main() -> int:
    """Run validation and print report."""
    success, errors, warnings = validate_localization()
    
    if errors:
        print("❌ LOCALIZATION VALIDATION FAILED\n")
        print(f"Errors ({len(errors)}):")
        for e in errors[:30]:
            print(f"  • {e}")
        if len(errors) > 30:
            print(f"  ... and {len(errors) - 30} more errors")
    
    if warnings:
        print(f"\n⚠️  Warnings ({len(warnings)}):")
        for w in warnings[:15]:
            print(f"  • {w}")
        if len(warnings) > 15:
            print(f"  ... and {len(warnings) - 15} more warnings")
    
    if success and not errors:
        print("✅ LOCALIZATION VALIDATION PASSED")
        langs = list(localization.TEXTS.keys())
        for lang in langs:
            print(f"  • {lang}: {len(localization.TEXTS[lang])} keys")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
