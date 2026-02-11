# -*- coding: utf-8 -*-
"""English (en) strings."""

LANG = {
    "common.back": "← Back",
    "common.cancel": "❌ Cancel",
    "main.title": "Main menu",
    "main.profile": "👤 My Profile",
    "main.buy": "🔐 Buy Access",
    "main.referral": "💎 Loyalty Program",
    "main.help": "🛡 Support",
    "main.about": "ℹ️ About",
    "main.instruction": "🔌 Instruction",
    "lang.select_title": "🌍 Select your language:",
    "support.write_button": "💬 Write to support",
    "errors.profile_load": "Error loading profile. Please try again later.",
    "lang.change": "🌍 Change language",
    "trial.button": "🎁 3-Day Trial Period",
    "subscription.renew": "🔁 Renew Access",
    "subscription.auto_renew_disable": "⏸ Disable auto-renewal",
    "subscription.auto_renew_enable": "🔄 Enable auto-renewal",
    "profile.topup_balance": "➕ Top Up Balance",
    "profile.copy_key": "📋 Copy Key",

    # Phase 3.1 — Main menu + profile
    "main.welcome": "🔐 Atlas Secure\n\nWelcome to Atlas Secure 🤝\n\nWe provide:\n⚙️ stable operation of familiar services\n⚡ fast and reliable connection\n🛡 privacy by default\n\nYou use the internet as usual —\nwe take care of stability and protection.",
    "main.service_unavailable": "⚠️ Service temporarily unavailable. Please try again later.",
    "lang.changed_toast": "✅ Language changed",
    "errors.db_init_stage_warning": "⚠️ Database is still initializing (STAGE). Some features may be unavailable.",
    "common.rate_limit_message": "Too many requests. Please try again later.",
    "incident.banner": "⚠️ Technical work in progress",
    "profile.welcome_full": "Welcome to Atlas Secure!\n\n👤 {username}\n\n💰 Balance: {balance:.2f} ₽",
    "profile.subscription_pending": "Subscription:\n— ⏳ Pending activation\n\nValid until: {date}",
    "profile.subscription_active": "Subscription:\n— 🟢 Active until {date}",
    "profile.subscription_inactive": "Subscription:\n— 🔴 Inactive",
    "profile.auto_renew_enabled": "🔁 Auto-renewal: {next_billing_date}",
    "profile.auto_renew_disabled": "🔁 Auto-renewal: disabled",
    "profile.renewal_hint": "When renewing, the selected period\nis automatically added to current access.",
    "profile.buy_hint": "Click «Buy Subscription» in the menu to get access.",
    "subscription.auto_renew_enabled_toast": "✅ Auto-renewal enabled",
    "subscription.auto_renew_disabled_toast": "⏸ Auto-renewal disabled",

    # Phase 3.2 — Buy flow
    "buy.tariff_basic": "✅ Basic\nFor everyday use",
    "buy.tariff_plus": "🔑 Plus\nPriority access and dedicated server",
    "buy.tariff_corporate": "🧩 Corporate Access\nCustom configuration for company needs.\nDedicated infrastructure, access control\nand personal support.",
    "buy.select_basic_button": "✅ Select Basic",
    "buy.select_plus_button": "🔑 Select Plus",
    "buy.enter_promo": "🎟 Enter promo code",
    "buy.corporate_button": "🧩 Corporate Access",
    "buy.corporate_confirm": "✅ Confirm",
    "buy.corporate_back": "◀️ Back",
    "buy.corporate_consent": "By submitting the request, you consent\nto the processing of your Telegram Username and ID,\nas well as information voluntarily provided\nby you in the course of the request.",
    "buy.tariff_basic_desc": "🪙 Tariff: Basic\n\n🔹 For everyday use\n📲 Great for social networks\n🚀 Supports: Instagram, YouTube 4K, TikTok, Web, etc.\n🔒 Reliable basic traffic protection\n💡 Simple, efficient connection\n\n👉 Ideal for daily use without complex tasks",
    "buy.tariff_plus_desc": "🔑 Tariff: Plus\n\n🔥 Priority access to servers\n📶 Works with 5G without limits\n🛡 Enhanced protection and encryption\n🚫 Bypasses whitelists and blocks\n⚡ Priority for max speed for streaming, gaming, downloads\n\n👉 For those who want maximum comfort and freedom online",
    "buy.select_tariff": "Select tariff:",
    "buy.tariff_label_basic": "🪙 Basic",
    "buy.tariff_label_plus": "🔑 Plus",
    "buy.back_to_tariffs": "← Back",
    "payment.select_method": "Choose payment method:\n\nAmount: {price:.2f} ₽",
    "payment.balance": "💰 Balance (available: {balance:.2f} ₽)",
    "payment.card": "💳 Bank Card",
    "payment.crypto": "🌏 Cryptocurrency",
    "errors.tariff": "Tariff error",
    "errors.session_expired": "Purchase session expired. Please start over.",
    "errors.session_expired_processing": "Payment is already being processed. Please wait.",
    "errors.insufficient_balance": "Insufficient balance.\n\nPrice: {amount:.2f} ₽\nBalance: {balance:.2f} ₽\nShortage: {shortage:.2f} ₽",
    "errors.payment_processing": "An error occurred. Please try again.",
    "errors.payments_unavailable": "Payments temporarily unavailable",
    "errors.payment_min_amount": "Amount after discount is below minimum for card payment (64 ₽).\nPlease select another tariff.",
    "errors.payment_create": "Error creating payment. Please try again.",
    "errors.invalid_amount": "Invalid amount",

    "buy.corporate_request_accepted": "Request accepted.\n\nIt has been submitted for individual consideration.\nA manager will contact you to discuss\ncorporate access, please wait.",
    "buy.promo_applied": "🎁 Promo code applied. Discount already included in price.",
    "buy.promo_applied_with_ttl": "🎁 Promo code applied. Discount already included in price. Valid for {minutes} more min.",
    "buy.enter_promo_text": "Enter promo code:",
    "buy.promo_enter_text_hint": "Please enter the promo code as text.",
    "buy.invoice_label": "To pay",
    "buy.invoice_description": "Atlas Secure VPN {tariff_name} tariff, {months}-month subscription",

    "payment.crypto_unavailable": "Cryptocurrency payment temporarily unavailable",
    "payment.crypto_waiting": "₿ Cryptocurrency Payment\n\nAmount: {amount:.2f} ₽\n\n⏳ Waiting for payment confirmation. This usually takes up to 5 minutes. Access will be granted automatically.",
    "payment.crypto_pay_button": "💳 Go to Payment",

    # Buy flow — period buttons, payment success
    "buy.period_1": "1 month",
    "buy.period_2_4": "{months} months",
    "buy.period_5_plus": "{months} months",
    "buy.button_price": "{price} ₽ — {period}",
    "buy.button_price_discount": "{base} ₽ → {final} ₽ — {period}",
    "common.go_to_connection": "🔌 Go to connection",
    "payment.success_first": "🎉 <b>Subscription successfully activated</b>\n\n📅 <b>Valid until:</b> {date}\n\n🔐 <b>Your connection key:</b>\n<code>{vpn_key}</code>\n\nYou can use it in the VPN app.",
    "payment.success_renewal": "🔄 <b>Subscription renewed</b>\n\n📅 <b>New validity:</b> until {date}\n\n🔐 <b>Your current key</b> (same UUID):\n<code>{vpn_key}</code>\n\nYou can continue using your current connection key.",
    "payment.pending_activation": "✅ Subscription placed!\n\n📅 Valid until: {date}\n\n⏳ Activation in progress. VPN key will be sent to you shortly.\n\nIf the key has not arrived within an hour, contact support.",
    "payment.fallback_first": "🎉 Subscription successfully activated\n\n📅 Valid until: {date}\n\n🔐 Your connection key will be sent in the next message.",
    "payment.fallback_renewal": "🔄 Subscription renewed\n\n📅 New validity: until {date}\n\n🔐 Your current key (same UUID) will be sent in the next message.",
    "common.username_not_set": "not set",
    "common.user": "user",
    "referral.action_purchase": "purchase",
    "referral.action_renewal": "renewal",
    "referral.action_topup": "top-up",

    # Phase 3.3 — Referral flow
    "referral.screen_title": "📊 Activity & Access Status",
    "referral.total_invited": "👤 Total invited: {count}",
    "referral.active_with_subscription": "💎 Active with subscription: {count}",
    "referral.current_status": "🏆 Current status: {status}",
    "referral.cashback_level": "📈 Cashback level: {percent}%",
    "referral.rewards_earned": "💎 Rewards credited: {amount:.2f} ₽",
    "referral.last_activity": "📅 Last activity: {date}",
    "referral.next_level_line": "🚀 To level {next_status_name}:\n{remaining_invites} connections left",
    "referral.max_level_reached": "🏆 You have reached the maximum program level",
    "referral.share_button": "📤 Share link",
    "referral.stats_button": "More",
    "referral.link_copied": "✅ Link sent in a separate message",
    "referral.stats_screen": (
        "🔐 Atlas Secure Loyalty Program\n\n"
        "💎 Your status unlocks more benefits.\n"
        "Earn rewards for participating in the Atlas Secure ecosystem — no limits.\n\n"
        "⸻\n\n"
        "🏆 Access levels\n\n"
        "Silver Access\n"
        "— up to 24 invited\n"
        "— 10% cashback to balance\n\n"
        "Gold Access\n"
        "— 25–49 invited\n"
        "— 25% cashback\n"
        "— extended privileges\n\n"
        "Platinum Access\n"
        "— 50+ invited\n"
        "— 45% cashback\n"
        "— maximum access level\n\n"
        "⸻\n\n"
        "🔗 Your personal link:\n"
        "{referral_link}\n\n"
        "🪙 Rewards are credited to your account balance automatically.\n\n"
        "⸻\n\n"
        "📊 Current status: {current_status_name}\n"
        "{status_footer}"
    ),
    "referral.status_footer": "🚀 To next level: {remaining_invites} invites left",
    "referral.how_it_works_text": (
        "📊 How the referral program works\n\n"
        "1. Send your referral link to a friend\n"
        "2. Friend clicks the link and registers\n"
        "3. When friend pays for a subscription, you get cashback\n\n"
        "🎁 Cashback levels:\n"
        "• 0-24 friends → 10% cashback\n"
        "• 25-49 friends → 25% cashback\n"
        "• 50+ friends → 45% cashback\n\n"
        "💰 Cashback is credited to your balance automatically\n"
        "on each referral purchase.\n\n"
        "💡 Level is determined by the number of referrals\n"
        "who have paid at least once."
    ),
    "referral.cashback_title": "🎉 Your referral made a {action_type}!",
    "referral.cashback_referred": "👤 Referral: {referred}",
    "referral.cashback_amount": "💳 {action_type} amount: {amount:.2f} ₽",
    "referral.cashback_subscription_period": "⏰ Subscription period: {period}",
    "referral.cashback_reward": "💰 Cashback credited: {amount:.2f} ₽ ({percent}%)",
    "referral.cashback_progress": "👥 To next level: {needed} {friend} remaining",
    "referral.cashback_max_level": "🎯 You've reached the maximum level!",
    "referral.cashback_balance_auto": "Balance topped up automatically.",
    "referral.friend_singular": "friend",
    "referral.friend_dual": "friends",
    "referral.friend_plural": "friends",
    "referral.registered_title": "🎉 New referral registered!",
    "referral.registered_user": "👤 User: {user}",
    "referral.registered_date": "📅 Date: {date}",
    "referral.first_payment_notification": "When your referral makes their first payment, you'll receive cashback!",
    "referral.trial_activated_title": "🎉 Your referral activated a trial!",
    "referral.trial_activated_user": "👤 User: {user}",
    "referral.trial_period": "⏰ Trial period: 3 days",
}
