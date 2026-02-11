#!/usr/bin/env python3
"""
Translation validation script for ATCS project.

Validates that all languages have identical key structures and placeholder consistency.
"""

import json
import sys
from typing import Dict, Set, List, Tuple
import re

# Import localization module
import localization


def flatten_dict(d: Dict, parent_key: str = '', sep: str = '.') -> Dict[str, str]:
    """Flatten nested dictionary to dot-notation keys."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def extract_placeholders(text: str) -> Set[str]:
    """Extract placeholder names from format string (e.g., {count}, {level})."""
    if not isinstance(text, str):
        return set()
    pattern = r'\{(\w+)\}'
    return set(re.findall(pattern, text))


def validate_translations() -> Tuple[bool, List[str]]:
    """Validate translation coverage and consistency."""
    errors = []
    warnings = []
    
    # Get all language codes
    languages = list(localization.TEXTS.keys())
    if not languages:
        errors.append("No languages found in TEXTS dictionary")
        return False, errors
    
    # Use Russian as canonical reference
    if "ru" not in languages:
        errors.append("Russian (ru) language not found - required as canonical reference")
        return False, errors
    
    ru_keys = set(localization.TEXTS["ru"].keys())
    
    # Check each language
    for lang in languages:
        if lang == "ru":
            continue
        
        lang_keys = set(localization.TEXTS[lang].keys())
        
        # Check for missing keys
        missing_keys = ru_keys - lang_keys
        if missing_keys:
            errors.append(f"Language '{lang}' missing {len(missing_keys)} keys: {sorted(missing_keys)[:10]}...")
        
        # Check for extra keys (not in Russian)
        extra_keys = lang_keys - ru_keys
        if extra_keys:
            warnings.append(f"Language '{lang}' has {len(extra_keys)} extra keys not in Russian: {sorted(extra_keys)[:10]}...")
        
        # Check placeholder consistency
        for key in ru_keys & lang_keys:
            ru_text = localization.TEXTS["ru"].get(key, "")
            lang_text = localization.TEXTS[lang].get(key, "")
            
            ru_placeholders = extract_placeholders(ru_text)
            lang_placeholders = extract_placeholders(lang_text)
            
            if ru_placeholders != lang_placeholders:
                errors.append(
                    f"Placeholder mismatch in '{lang}' for key '{key}': "
                    f"RU has {ru_placeholders}, {lang.upper()} has {lang_placeholders}"
                )
    
    # Summary
    if errors:
        print("❌ VALIDATION FAILED")
        print(f"\nFound {len(errors)} error(s):")
        for error in errors[:20]:  # Show first 20 errors
            print(f"  - {error}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more errors")
        
        if warnings:
            print(f"\n⚠️  Found {len(warnings)} warning(s):")
            for warning in warnings[:10]:
                print(f"  - {warning}")
        
        return False, errors
    
    if warnings:
        print("⚠️  VALIDATION PASSED WITH WARNINGS")
        print(f"\nFound {len(warnings)} warning(s):")
        for warning in warnings[:10]:
            print(f"  - {warning}")
        return True, warnings
    
    print("✅ VALIDATION PASSED")
    print(f"\nAll {len(languages)} languages have consistent key structures:")
    for lang in languages:
        key_count = len(localization.TEXTS[lang].keys())
        print(f"  - {lang}: {key_count} keys")
    
    return True, []


if __name__ == "__main__":
    success, issues = validate_translations()
    sys.exit(0 if success else 1)
