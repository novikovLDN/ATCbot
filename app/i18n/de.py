# -*- coding: utf-8 -*-
"""German (de) strings."""

LANG = {
    "common.back": "← Zurück",
    "common.cancel": "❌ Abbrechen",
    "main.title": "Hauptmenü",
    "main.profile": "👤 Mein Profil",
    "main.buy": "🔐 Zugang kaufen",
    "main.referral": "💎 Treueprogramm",
    "main.help": "🛡 Support",
    "main.about": "ℹ️ Über den Service",
    "main.instruction": "🔌 Anleitung",
    "lang.select_title": "🌍 Wählen Sie Ihre Sprache:",
    "support.write_button": "💬 An Support schreiben",
    "errors.profile_load": "Fehler beim Laden des Profils. Bitte versuchen Sie es später erneut.",
    "lang.change": "🌍 Sprache ändern",
    "trial.button": "🎁 3-Tage-Testphase",
    "subscription.renew": "🔁 Zugang verlängern",
    "subscription.auto_renew_disable": "⏸ Auto-Verlängerung deaktivieren",
    "subscription.auto_renew_enable": "🔄 Auto-Verlängerung aktivieren",
    "profile.topup_balance": "➕ Guthaben aufladen",
    "profile.copy_key": "📋 Schlüssel kopieren",

    # Phase 3.1 — Main menu + profile
    "main.welcome": "🔐 Atlas Secure\n\nWillkommen bei Atlas Secure 🤝\n\nWir bieten:\n⚙️ stabilen Betrieb gewohnter Dienste\n⚡ schnelle und zuverlässige Verbindung\n🛡 Datenschutz standardmäßig\n\nSie nutzen das Internet wie gewohnt —\nwir kümmern uns um Stabilität und Schutz.",
    "main.service_unavailable": "⚠️ Service vorübergehend nicht verfügbar. Bitte versuchen Sie es später erneut.",
    "lang.changed_toast": "✅ Sprache geändert",
    "errors.db_init_stage_warning": "⚠️ Datenbank wird noch initialisiert (STAGE). Einige Funktionen sind möglicherweise nicht verfügbar.",
    "common.rate_limit_message": "Zu viele Anfragen. Bitte versuchen Sie es später erneut.",
    "incident.banner": "⚠️ Technische Arbeiten im Gange",
    "profile.welcome_full": "Willkommen bei Atlas Secure!\n\n👤 {username}\n\n💰 Guthaben: {balance:.2f} ₽",
    "profile.subscription_pending": "Abonnement:\n— ⏳ Aktivierung ausstehend\n\nGültig bis: {date}",
    "profile.subscription_active": "Abonnement:\n— 🟢 Aktiv bis {date}",
    "profile.subscription_inactive": "Abonnement:\n— 🔴 Inaktiv",
    "profile.auto_renew_enabled": "🔁 Auto-Verlängerung: {next_billing_date}",
    "profile.auto_renew_disabled": "🔁 Auto-Verlängerung: deaktiviert",
    "profile.renewal_hint": "Bei Verlängerung wird die gewählte Laufzeit\nautomatisch zur aktuellen hinzugefügt.",
    "profile.buy_hint": "Klicken Sie im Menü auf «Abonnement kaufen», um Zugang zu erhalten.",
    "subscription.auto_renew_enabled_toast": "✅ Auto-Verlängerung aktiviert",
    "subscription.auto_renew_disabled_toast": "⏸ Auto-Verlängerung deaktiviert",

    # Phase 3.2 — Buy flow
    "buy.tariff_basic": "✅ Basic\nFür den täglichen Gebrauch",
    "buy.tariff_plus": "🔑 Plus\nPrioritätszugang und dedizierter Server",
    "buy.tariff_corporate": "🧩 Unternehmenszugang\nIndividuelle Konfiguration für Unternehmensbedürfnisse.\nDedizierte Infrastruktur, Zugangskontrolle\nund persönliche Betreuung.",
    "buy.select_basic_button": "✅ Basic wählen",
    "buy.select_plus_button": "🔑 Plus wählen",
    "buy.enter_promo": "🎟 Promocode eingeben",
    "buy.corporate_button": "🧩 Unternehmenszugang",
    "buy.corporate_confirm": "✅ Bestätigen",
    "buy.corporate_back": "◀️ Zurück",
    "buy.corporate_consent": "Mit dem Absenden bestätigen Sie die Verarbeitung\nIhres Telegram-Benutzernamens und Ihrer ID\nsowie freiwillig bereitgestellter Informationen\nim Rahmen der Anfrage.",
    "buy.tariff_basic_desc": "🪙 Tarif: Basic\n\n🔹 Für den täglichen Gebrauch\n📲 Ideal für soziale Netzwerke\n🚀 Unterstützt: Instagram, YouTube 4K, TikTok, Web usw.\n🔒 Zuverlässiger Basistraffic-Schutz\n💡 Einfache, effiziente Verbindung\n\n👉 Ideal für den täglichen Gebrauch ohne komplexe Aufgaben",
    "buy.tariff_plus_desc": "🔑 Tarif: Plus\n\n🔥 Prioritätszugang zu Servern\n📶 Funktioniert mit 5G ohne Einschränkungen\n🛡 Erweiterter Schutz und Verschlüsselung\n🚫 Umgeht Whitelists und Blocker\n⚡ Priorität für maximale Geschwindigkeit bei Streaming, Gaming, Downloads\n\n👉 Für alle, die maximalen Komfort und Freiheit online wollen",
    "buy.select_tariff": "Tarif wählen:",
    "buy.tariff_label_basic": "🪙 Basic",
    "buy.tariff_label_plus": "🔑 Plus",
    "buy.back_to_tariffs": "← Zurück",
    "payment.select_method": "Zahlungsmethode wählen:\n\nBetrag: {price:.2f} ₽",
    "payment.balance": "💰 Guthaben (verfügbar: {balance:.2f} ₽)",
    "payment.card": "💳 Bankkarte",
    "payment.crypto": "🌏 Kryptowährung",
    "errors.tariff": "Tariffehler",
    "errors.session_expired": "Kaufsitzung abgelaufen. Bitte neu starten.",
    "errors.session_expired_processing": "Zahlung wird bereits bearbeitet. Bitte warten.",
    "errors.insufficient_balance": "Unzureichendes Guthaben.\n\nPreis: {amount:.2f} ₽\nGuthaben: {balance:.2f} ₽\nFehlbetrag: {shortage:.2f} ₽",
    "errors.payment_processing": "Ein Fehler ist aufgetreten. Bitte versuchen Sie es erneut.",
    "errors.payments_unavailable": "Zahlungen vorübergehend nicht verfügbar",
    "errors.payment_min_amount": "Betrag nach Rabatt liegt unter dem Minimum für Kartenzahlung (64 ₽).\nBitte wählen Sie einen anderen Tarif.",
    "errors.payment_create": "Fehler beim Erstellen der Zahlung. Bitte versuchen Sie es erneut.",
    "errors.invalid_amount": "Ungültiger Betrag",

    "buy.corporate_request_accepted": "Anfrage angenommen.\n\nSie wurde zur individuellen Prüfung vorgelegt.\nEin Manager wird Sie bezüglich\ndes Unternehmenszugangs kontaktieren, bitte warten Sie.",
    "buy.promo_applied": "🎁 Promocode angewendet. Rabatt bereits im Preis enthalten.",
    "buy.promo_applied_with_ttl": "🎁 Promocode angewendet. Rabatt im Preis enthalten. Noch {minutes} Min. gültig.",
    "buy.enter_promo_text": "Promocode eingeben:",
    "buy.promo_enter_text_hint": "Bitte geben Sie den Promocode als Text ein.",
    "buy.invoice_label": "Zu zahlen",
    "buy.invoice_description": "Atlas Secure VPN {tariff_name}-Tarif, {months}-Monats-Abonnement",

    "payment.crypto_unavailable": "Kryptowährungszahlung vorübergehend nicht verfügbar",
    "payment.crypto_waiting": "₿ Kryptowährungszahlung\n\nBetrag: {amount:.2f} ₽\n\n⏳ Warten auf Zahlungsbestätigung. In der Regel bis zu 5 Minuten. Zugang wird automatisch gewährt.",
    "payment.crypto_pay_button": "💳 Zur Zahlung",

    "buy.period_1": "1 Monat",
    "buy.period_2_4": "{months} Monate",
    "buy.period_5_plus": "{months} Monate",
    "buy.button_price": "{price} ₽ — {period}",
    "buy.button_price_discount": "{base} ₽ → {final} ₽ — {period}",
    "common.go_to_connection": "🔌 Zur Verbindung",
    "payment.success_first": "🎉 <b>Abonnement erfolgreich aktiviert</b>\n\n📅 <b>Gültig bis:</b> {date}\n\n🔐 <b>Ihr Verbindungsschlüssel:</b>\n<code>{vpn_key}</code>",
    "payment.success_renewal": "🔄 <b>Abonnement verlängert</b>\n\n📅 <b>Neue Gültigkeit:</b> bis {date}\n\n🔐 <b>Ihr aktueller Schlüssel</b> (gleiche UUID):\n<code>{vpn_key}</code>",
    "payment.pending_activation": "✅ Abonnement bestellt!\n\n📅 Gültig bis: {date}\n\n⏳ Aktivierung läuft.",
    "payment.fallback_first": "🎉 Abonnement aktiviert\n\n📅 Gültig bis: {date}",
    "payment.fallback_renewal": "🔄 Abonnement verlängert\n\n📅 Neue Gültigkeit: {date}",
    "common.username_not_set": "nicht angegeben",
    "common.user": "Benutzer",
    "referral.action_purchase": "Kauf",
    "referral.action_renewal": "Verlängerung",
    "referral.action_topup": "Aufladung",

    # Phase 3.3 — Referral flow
    "referral.screen_title": "📊 Aktivität & Zugangsstatus",
    "referral.total_invited": "👤 Insgesamt eingeladen: {count}",
    "referral.active_with_subscription": "💎 Aktiv mit Abo: {count}",
    "referral.current_status": "🏆 Aktueller Status: {status}",
    "referral.cashback_level": "📈 Cashback-Stufe: {percent}%",
    "referral.rewards_earned": "💎 Gutgeschriebene Belohnungen: {amount:.2f} ₽",
    "referral.last_activity": "📅 Letzte Aktivität: {date}",
    "referral.next_level_line": "🚀 Zur Stufe {next_status_name}:\n{remaining_invites} Verbindungen übrig",
    "referral.max_level_reached": "🏆 Sie haben das maximale Programmlevel erreicht",
    "referral.share_button": "📤 Link teilen",
    "referral.stats_button": "Mehr",
    "referral.link_copied": "✅ Link wurde in separater Nachricht gesendet",
    "referral.stats_screen": (
        "🔐 Atlas Secure Treueprogramm\n\n"
        "💎 Ihr Status bietet mehr Vorteile.\n"
        "Verdienen Sie Belohnungen durch Teilnahme am Atlas Secure Ökosystem — ohne Grenzen.\n\n"
        "⸻\n\n"
        "🏆 Zugangsstufen\n\n"
        "Silver Access\n"
        "— bis 24 Eingeladene\n"
        "— 10% Cashback auf Guthaben\n\n"
        "Gold Access\n"
        "— 25–49 Eingeladene\n"
        "— 25% Cashback\n"
        "— erweiterte Privilegien\n\n"
        "Platinum Access\n"
        "— 50+ Eingeladene\n"
        "— 45% Cashback\n"
        "— maximale Zugangsstufe\n\n"
        "⸻\n\n"
        "🔗 Ihr persönlicher Link:\n"
        "{referral_link}\n\n"
        "🪙 Belohnungen werden automatisch auf Ihr Guthaben gutgeschrieben.\n\n"
        "⸻\n\n"
        "📊 Aktueller Status: {current_status_name}\n"
        "{status_footer}"
    ),
    "referral.status_footer": "🚀 Zur nächsten Stufe: {remaining_invites} Einladungen übrig",
    "referral.how_it_works_text": (
        "📊 So funktioniert das Empfehlungsprogramm\n\n"
        "1. Senden Sie Ihren Empfehlungslink an einen Freund\n"
        "2. Freund klickt auf den Link und registriert sich\n"
        "3. Wenn der Freund ein Abo bezahlt, erhalten Sie Cashback\n\n"
        "🎁 Cashback-Stufen:\n"
        "• 0-24 Freunde → 10% Cashback\n"
        "• 25-49 Freunde → 25% Cashback\n"
        "• 50+ Freunde → 45% Cashback\n\n"
        "💰 Cashback wird bei jedem Empfehlungskauf automatisch\n"
        "auf Ihr Guthaben gutgeschrieben.\n\n"
        "💡 Die Stufe wird durch die Anzahl der Empfehlungen bestimmt,\n"
        "die mindestens einmal ein Abo bezahlt haben."
    ),
    "referral.cashback_title": "🎉 Ihre Empfehlung hat {action_type} durchgeführt!",
    "referral.cashback_referred": "👤 Empfehlung: {referred}",
    "referral.cashback_amount": "💳 {action_type}-Betrag: {amount:.2f} ₽",
    "referral.cashback_subscription_period": "⏰ Abozeitraum: {period}",
    "referral.cashback_reward": "💰 Cashback gutgeschrieben: {amount:.2f} ₽ ({percent}%)",
    "referral.cashback_progress": "👥 Zur nächsten Stufe: {needed} {friend} übrig",
    "referral.cashback_max_level": "🎯 Sie haben das maximale Level erreicht!",
    "referral.cashback_balance_auto": "Guthaben automatisch aufgeladen.",
    "referral.friend_singular": "Freund",
    "referral.friend_dual": "Freunde",
    "referral.friend_plural": "Freunde",
    "referral.registered_title": "🎉 Neuer Referral registriert!",
    "referral.registered_user": "👤 Benutzer: {user}",
    "referral.registered_date": "📅 Datum: {date}",
    "referral.first_payment_notification": "Wenn Ihr Referral die erste Zahlung tätigt, erhalten Sie Cashback!",
    "referral.trial_activated_title": "🎉 Ihr Referral hat die Testphase aktiviert!",
    "referral.trial_activated_user": "👤 Benutzer: {user}",
    "referral.trial_period": "⏰ Testphase: 3 Tage",
}
