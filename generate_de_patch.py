#!/usr/bin/env python3
"""
Generate German translations for English bleed keys.
Reads translation_tasks.json, processes de only, outputs translation_patch_de.json.
Consistent terminology: Abonnement, Zugriff, Guthaben, Zahlung, Testphase, Verlängerung, Schlüssel.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "translation_tasks.json"
OUTPUT_FILE = ROOT / "translation_patch_de.json"

# Overrides for keys with English bleed - full German translations
DE_OVERRIDES = {
    "admin.test_trial": "🎁 Testphase-Benachrichtigung testen",
    "buy.select_tariff": "🕒 Abonnementzeitraum wählen\n\nAtlas Secure bietet stabilen Zugriff,\nder einfach funktioniert.\n\nIn jedem Abo:\n🔑 Persönlicher Schlüssel — nur für Sie\n⚡️ Stabile Geschwindigkeit ohne Limits\n📱💻 Läuft auf allen Geräten\n💬 Kundenservice in Telegram jederzeit\n\nJe länger die Laufzeit — desto weniger\nmüssen Sie ans Verlängern denken.\n\nDie meisten Nutzer wählen Abos ab 3 Monaten.",
    "buy.tariff_button_6": "6 Monate · Seltener verlängern · 599 ₽",
    "main.contact_manager_button": "💬 VIP-Zugriff verbinden",
    "main.pay_balance": "💰 Guthaben (verfügbar: {balance:.2f} ₽)",
    "main.privacy_policy_text": "🔐 Atlas Secure Datenschutz\n\nAtlas Secure basiert auf dem Prinzip\nder Datenminimierung.\n\nWir sammeln und speichern keine Informationen,\ndie für den Betrieb nicht erforderlich sind.\n\nWas wir NICHT speichern:\n• Verbindungshistorie\n• IP-Adressen und Netzverkehr\n• DNS-Anfragen\n• Daten über besuchte Ressourcen\n• Nutzeraktivitäts-Metadaten\n\nDie Architektur folgt dem Zero-Logs-Prinzip.\n\nWas verarbeitet werden kann:\n• Zugangsstatus\n• Abo-Gültigkeitszeitraum\n• technischer Schlüssel-Identifikator\n\nDiese Daten sind nicht mit Ihrer\nNetzaktivität verknüpft.\n\nZahlungen:\nAtlas Secure verarbeitet und speichert\nkeine Zahlungsdaten.\nZahlung erfolgt über Bank- und Zahlungssysteme\naußerhalb unserer Infrastruktur.\n\nDatenweitergabe:\nWir geben keine Daten an Dritte weiter\nund nutzen keine Tracker,\nAnalysen oder Werbe-SDKs.\n\nKundenservice:\nWir verarbeiten nur Informationen,\ndie Sie freiwillig zur Bearbeitung\neines Anliegens bereitstellen.\n\nAtlas Secure.\nDatenschutz ist in die Architektur integriert.",
    "main.service_status_text": "📊 Atlas Secure Dienststatus\n\nAktueller Status: 🟢 Dienst arbeitet stabil\n\nAlle Hauptkomponenten funktionieren\nim Normalmodus:\n• Zugang ist aktiv\n• Schlüsselausgabe funktioniert\n• Kundenservice ist verfügbar\n\nAtlas Secure ist als private\ndigitale Infrastruktur aufgebaut\nmit Fokus auf Stabilität\nund vorhersehbarem Betrieb.\n\nUnsere Grundsätze:\n• Ziel-Uptime — 99,9%\n• geplante Arbeiten erfolgen im Voraus\n• kritische Vorfälle werden\n  prioritär behoben\n• Datenverlust ist architektonisch ausgeschlossen\n\nBei technischen Arbeiten oder Änderungen\nwerden Nutzer vorab über den Bot informiert.\n\nLetzte Statusaktualisierung:\nautomatisch",
    "main.help": "🛡 Kundenservice",
    "main.smart_notif_vip_offer": "Für Nutzer mit aktivem Zugriff\nist erweiterter Kundenservice verfügbar.\n\nEr wird nicht automatisch verkauft\nund individuell betrachtet.",
    "main.support": "🛡 Kundenservice",
    "main.support_button": "🆘 Kundenservice",
    "main.support_text": "🛡 Atlas Secure Kundenservice\n\nBei Fragen zu Zugriff,\nZahlung oder Dienstbetrieb —\nschreiben Sie uns direkt.\n\nWir antworten manuell\nund bearbeiten Anfragen\nnach Priorität.\n\nKundenservice ist jederzeit erreichbar — wir sind da.",
    "main.trial_notification_54h": "⌛ Letzte 18 Stunden\n\nDie Testphase endet bald.",
    "main.trial_activation_error": "❌ Fehler bei der Aktivierung der Testphase. Bitte später erneut versuchen oder Kundenservice kontaktieren.",
    "main.vip_access_text": "👑 VIP-Zugriff Atlas Secure\n\nVIP ist erweiterter Kundenservice\nfür alle, die Stabilität und Priorität schätzen.\n\nWas VIP bietet:\n⚡️ Prioritäts-Infrastruktur und minimale Verzögerung\n🛠 Persönliche Zugriffskonfiguration\n💬 Prioritäts-Kundenservice ohne Warten\n🚀 Frühzugang zu Updates\n\nVIP passt, wenn Sie:\n• den Zugriff täglich nutzen\n• sich nicht mit Einstellungen befassen wollen\n• vorhersehbaren Betrieb schätzen\n\nPreis:\n1.990 ₽ / Monat\noder 9.990 ₽ / 6 Monate\n\nVIP wird mit aktivem Abo aktiviert.\nHinterlassen Sie eine Anfrage — wir erledigen den Rest.\n\nVIP — wenn der Zugriff einfach da ist\nund Sie nicht darüber nachdenken müssen.",
    "payment.pending_activation": "✅ Abonnement erstellt!\n\n📅 Gültig bis: {date}\n\n⏳ Aktivierung läuft. VPN-Schlüssel wird Ihnen bald zugesendet.\n\nFalls der Schlüssel innerhalb einer Stunde nicht ankommt, kontaktieren Sie den Kundenservice.",
    "payment.rejected": "❌ Zahlung nicht bestätigt.\n\nFalls Sie bezahlt haben —\nkontaktieren Sie den Kundenservice.",
    "referral.link_copied": "Link gesendet",
    "errors.payment_processing": "Zahlungsfehler. Bitte kontaktieren Sie den Kundenservice.",
    "errors.subscription_activation": "Fehler bei der Abonnement-Aktivierung. Bitte Kundenservice kontaktieren.",
    "support.write_button": "💬 An Kundenservice schreiben",
}


def main() -> int:
    if not INPUT_FILE.exists():
        print(f"ERROR: {INPUT_FILE} not found.", file=sys.stderr)
        return 1
    data = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    de_keys = data.get("de", {})
    patch = {k: DE_OVERRIDES.get(k, v) for k, v in de_keys.items()}
    OUTPUT_FILE.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Keys: {len(patch)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
