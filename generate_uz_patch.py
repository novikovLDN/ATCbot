#!/usr/bin/env python3
"""
Generate Uzbek translations for English bleed keys.
Reads translation_tasks.json, processes uz only, outputs translation_patch_uz.json.
Uses Latin script (O'zbek).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "translation_tasks.json"
OUTPUT_FILE = ROOT / "translation_patch_uz.json"

# Overrides for keys with English bleed - full Uzbek (Latin) translations
UZ_OVERRIDES = {
    "buy.select_tariff_type": "Tarifni tanlang:",
    "buy.tariff_basic_description": "🪙 Tarif: Basic\n\n🔹 Kundalik foydalanish uchun\n📲 Ijtimoiy tarmoqlar uchun yaxshi\n🚀 Qo'llab-quvvatlaydi: Instagram, YouTube 4K, TikTok, Web va boshqalar\n🔒 Ishonchli asosiy trafik himoyasi\n💡 Oddiy, samarali ulanish\n\n👉 Murakkab vazifalarsiz kundalik foydalanish uchun ideal",
    "errors.function_disabled": "Bu funksiya mavjud emas.",
    "errors.payment_already_processed": "To'lov allaqachon qayta ishlangan.",
    "errors.payment_not_found": "To'lov topilmadi.",
    "errors.pending_payment_exists": "Sizda allaqachon kutilayotgan to'lov mavjud.",
    "payment.test": "Xizmat rejimi mavjud emas",
    "referral.stats_screen": "🔐 Atlas Secure Sodiqlik dasturi\n\n💎 Statusingiz qo'shimcha imkoniyatlar beradi.\nAtlas Secure ekotizimida ishtirok etish uchun mukofot oling — cheklovsiz.\n\n⸻\n\n🏆 Kirish darajalari\n\nKumush kirish\n— 24 gacha taklif qilingan\n— 10% balansga qaytarish\n\nOltin kirish\n— 25–49 taklif qilingan\n— 25% qaytarish\n— kengaytirilgan imtiyozlar\n\nPlatina kirish\n— 50+ taklif qilingan\n— 45% qaytarish\n— maksimal kirish darajasi\n\n⸻\n\n🔗 Shaxsiy havolangiz:\n{referral_link}\n\n🪙 Mukofotlar balansga avtomatik hisoblanadi.\n\n⸻\n\n📊 Joriy status: {current_status_name}\n{status_footer}",
}


def main() -> int:
    if not INPUT_FILE.exists():
        print(f"ERROR: {INPUT_FILE} not found.", file=sys.stderr)
        return 1
    data = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    uz_keys = data.get("uz", {})
    patch = {}
    for key, val in uz_keys.items():
        patch[key] = UZ_OVERRIDES.get(key, val)
    OUTPUT_FILE.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Keys: {len(patch)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
