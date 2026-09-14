# -*- coding: utf-8 -*-
"""en strings."""

LANG = {
    'admin.access_denied': "Insufficient access rights",
    'admin.activation_error_action': "Manual activation required.",
    'admin.activation_error_attempts': "Attempts: {attempts}/{max_attempts}",
    'admin.activation_error_error': "Error: <code>{error_msg}</code>",
    'admin.activation_error_status': "Subscription marked as <code>failed</code>.",
    'admin.activation_error_subscription_id': "Subscription ID: <code>{subscription_id}</code>",
    'admin.activation_error_title': "⚠️ <b>VPN SUBSCRIPTION ACTIVATION ERROR</b>",
    'admin.activation_error_user': "User: <code>{telegram_id}</code>",
    'admin.copy_key': "📋 Copy Key",
    'admin.degraded_mode': "⚠️ <b>BOT RUNNING IN DEGRADED MODE</b>\n\nDatabase is unavailable.\n\n• Bot is running and responding to commands\n• Critical operations are blocked\n• Users receive temporary unavailability messages\n\nBot will automatically retry DB connection every 30 seconds.\n\nCheck:\n• PostgreSQL availability\n• DATABASE_URL correctness\n• Network settings",
    'admin.go_to_instruction': "🔌 Go to Instruction",
    'admin.my_profile': "👤 My Profile",
    'admin.pending_activations_row': "{idx}. ID: <code>{subscription_id}</code> | User: <code>{telegram_id}</code> | Attempts: {attempts} | Since {pending_since}\n   Error: <code>{error}</code>\n",
    'admin.pending_activations_title': "⚠️ <b>PENDING VPN ACTIVATIONS</b>\n",
    'admin.pending_activations_top': "\n<b>Top 5 oldest:</b>\n",
    'admin.pending_activations_total': "Total pending subscriptions: <b>{count}</b>\n",
    'admin.recovered': "✅ <b>SERVICE RESTORED</b>\n\nDatabase is available again.\n\n• Bot is fully operational\n• All operations restored\n• Background tasks running",
    'buy.button_price': "{price} ₽ — {period} · {gb} GB",
    'buy.button_price_discount': "{base} ₽ → {final} ₽ — {period} · {gb} GB",
    'buy.enter_promo_text': "Enter promo code:",
    'buy.invoice_description': "Atlas Secure VPN {tariff_name} tariff, {months}-month subscription",
    'buy.invoice_label': "To pay",
    'buy.period_1': "1 month",
    'buy.period_2_4': "{months} months",
    'buy.period_5_plus': "{months} months",
    'buy.promo_applied': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Promo code applied. Discount already included in price.",
    'buy.promo_enter_text_hint': "Please enter the promo code as text.",
    'buy.renew_button': "🔐 Buy / Renew Subscription",
    'buy.select_basic_new': "⚡️ Select Basic",
    'buy.select_basic_renew': "⚡️ Renew Basic",
    'buy.select_basic_switch': "⚡️ Switch to Basic",
    'buy.select_plus_new': "👑 Select Plus",
    'buy.select_plus_renew': "👑 Renew Plus",
    'buy.select_plus_switch': "👑 Switch to Plus",
    'buy.tariff_basic': "⚡️ <b>Basic</b> — for you and your family\n<blockquote>No blocks, no slowdowns, no hassle\n10 devices · from 199 ₽/mo</blockquote>",
    'buy.tariff_basic_desc': "⚡️ <b>Plan: Basic</b>\n\n<blockquote>🚀 Up to 25 Gbps — YouTube 4K without lag\n🌐 10 GB of whitelist bypass with every payment\n👨‍👩‍👧‍👦 One subscription for the whole family — up to 10 devices\n➕ One-tap connection</blockquote>",
    'buy.tariff_basic_description': "⚡️ <b>Plan: Basic</b>\n\n<blockquote>🚀 Up to 25 Gbps — YouTube 4K without lag\n🌐 10 GB of whitelist bypass with every payment\n👨‍👩‍👧‍👦 One subscription for the whole family — up to 10 devices\n➕ One-tap connection</blockquote>",
    'buy.tariff_basic_selected': "🔐 Basic tariff selected\nWhat period are you interested in?",
    'buy.tariff_button_1': "1 month · For trial · 149 ₽",
    'buy.tariff_button_12': "12 months · Don't think about access · 899 ₽",
    'buy.tariff_button_3': "3 months · Most popular · 399 ₽ ⭐",
    'buy.tariff_button_6': "6 months · Renew less often · 599 ₽",
    'buy.tariff_label_basic': "🪙 Basic",
    'buy.tariff_label_plus': "👑 Plus",
    'buy.tariff_plus': "👑 <b>Plus</b> — when speed matters\n<blockquote>Priority channel · 3x faster than Basic\n14 devices · from 349 ₽/mo</blockquote>",
    'buy.tariff_plus_desc': "👑 <b>Plan: Plus</b>\n\n<blockquote>⚡️ Up to 75 Gbps — streams and games without lag\n🔄 Backup channels — the connection always works\n🌐 10 GB of whitelist bypass with every payment\n👨‍👩‍👧‍👦 One subscription for the whole family — up to 14 devices</blockquote>",
    'buy.tariff_plus_description': "👑 <b>Plan: Plus</b>\n\n<blockquote>⚡️ Up to 75 Gbps — streams and games without lag\n🔄 Backup channels — the connection always works\n🌐 10 GB of whitelist bypass with every payment\n👨‍👩‍👧‍👦 One subscription for the whole family — up to 14 devices</blockquote>",
    'buy.tariff_plus_selected': "🔐 Plus tariff selected\nWhat period are you interested in?",
    'buy.tariff_select_basic_button': "⚡️ Select Basic",
    'buy.tariff_select_plus_button': "👑 Select Plus",
    'common.back': "Back",
    'common.button_outdated': "This button is outdated. Please open the menu again.",
    'common.rate_limit_message': "Too many requests. Please try again later.",
    'common.user': "User",
    'errors.db_init_stage_warning': "⚠️ Database is still initializing (STAGE). Some features may be unavailable.",
    'errors.generic': "Error",
    'errors.insufficient_balance': "Insufficient balance.\n\nPrice: {amount:.2f} ₽\nBalance: {balance:.2f} ₽\nShortage: {shortage:.2f} ₽",
    'errors.invalid_amount': "Invalid amount",
    'errors.payment_create': "Error creating invoice. Please try again later.",
    'errors.payment_min_amount': "Amount after discount is below minimum for card payment (64 ₽).\nPlease select another tariff.",
    'errors.payment_processing': "Payment processing error. Please contact support.",
    'errors.payments_unavailable': "Payments temporarily unavailable",
    'errors.profile_load': "Error loading profile. Please try again later.",
    'errors.session_expired': "⏳ Session expired. Please try again.",
    'errors.session_expired_processing': "Payment is already being processed. Please wait.",
    'errors.database_unavailable': "⚠️ Service temporarily unavailable. Please try again in a minute.",
    'errors.start_command': "Please start with /start command",
    'errors.subscription_activation': "Subscription activation error. Please contact support.",
    'errors.tariff': "Tariff error",
    'errors.try_later': "⚠️ An error occurred. Please try again later.",
    'get_key.no_subscription': "❌ You don't have an active subscription.",
    'setup.connect_ios': "📱 <b>Connect on iOS</b>\n\n<b>Step 1.</b> Copy the key — tap it at the bottom of this message\n\n<b>Step 2.</b> Open the installed app and add the key:\n\n━━━━━━━━━━━━━━━\n\n📲 <b>Happ</b>\nOn the main screen tap the clipboard icon 🗒️ at the bottom\nThe key will be added automatically\nTap the big connect button\n\n",
    'setup.connect_android': "🤖 <b>Connect on Android</b>\n\n<b>Step 1.</b> Copy the key — tap it at the bottom of this message\n\n<b>Step 2.</b> Open the installed app and add the key:\n\n━━━━━━━━━━━━━━━\n\n📲 <b>Happ</b>\nOn the main screen tap the clipboard icon 🗒️ at the bottom\nThe key will be added automatically\nTap the big connect button\n\n",
    'setup.connect_macos': "🍎 <b>Connect on macOS</b>\n\n<b>Step 1.</b> Copy the key — tap it at the bottom of this message\n\n<b>Step 2.</b> Open the installed app and add the key:\n\n━━━━━━━━━━━━━━━\n\n📲 <b>Happ</b>\nOn the main screen tap the clipboard icon 🗒️ at the bottom\nThe key will be added automatically\nTap the big connect button\n\n",
    'setup.connect_windows': "🪟 <b>Connect on Windows</b>\n\n<b>Step 1.</b> Copy the key — tap it at the bottom of this message\n\n<b>Step 2.</b> Open Happ and add the key:\n\n━━━━━━━━━━━━━━━\n\n📲 <b>Happ</b>\nOn the main screen click the clipboard icon 🗒️\nThe key will be added automatically\nClick the big connect button ▶️",
    'setup.manual_button': "📖 Manual setup",
    'setup.key_vpn_label': "🔑 <b>VPN key</b> (regular servers):",
    'setup.key_bypass_label': "🔑 <b>Bypass key</b> (RU whitelists):",
    'setup.done_button': "✅ Done",
    'setup.qr_button': "📲 Add device",
    'setup.qr_choose_type': "📲 <b>Add device</b>\n\nChoose connection type:",
    'setup.qr_standard_btn': "Standard servers (unlimited)",
    'setup.qr_bypass_btn': "Bypass whitelist",
    'setup.qr_bypass_unavailable': "❌ Whitelist bypass is not available.\n\nMake sure you have an active subscription and bypass traffic.",
    'setup.qr_instruction': "📲 <b>Add device</b>\n\n<b>Via QR code:</b>\nScan the code above in Happ → 📸 camera icon.\n\n<b>Manually:</b>\nTap the key below — it will be copied, then in Happ → clipboard icon at bottom.",
    'setup.download_happ': "📲 Download Happ",
    'setup.download_v2rayn': "📲 Download v2rayN",
    'setup.download_v2raytun': "📲 Download V2RayTun",
    'setup.select_device': "📱 <b>Select your device:</b>",
    'setup.combined_ios': "📱 <b>Connect on iOS</b>\n\n1️⃣ Download the app if not installed yet\n2️⃣ Tap the app name:\n\n🌐 — standard servers (unlimited)\n🤍 — new servers (whitelist bypass)",
    'setup.combined_android': "🤖 <b>Connect on Android</b>\n\n1️⃣ Download the app if not installed yet\n2️⃣ Tap the app name:\n\n🌐 — standard servers (unlimited)\n🤍 — new servers (whitelist bypass)",
    'setup.combined_macos': "🍎 <b>Connect on macOS</b>\n\n1️⃣ Download the app if not installed yet\n2️⃣ Tap the app name:\n\n🌐 — standard servers (unlimited)\n🤍 — new servers (whitelist bypass)",
    'setup.combined_windows': "🪟 <b>Connect on Windows</b>\n\n1️⃣ Download the app if not installed yet\n2️⃣ Tap the app name:\n\n🌐 — standard servers (unlimited)\n🤍 — new servers (whitelist bypass)",
    'incident.banner': "⚠️ Technical work in progress",
    'instruction._open_guide': "📖 Setup Guide",
    'instruction._text': "📖 Setup Guide\n\nTo set up your connection, open\nthe mini app — you'll find step-by-step\ninstructions for installing and connecting\non your device.",
    'lang.button_en': "🇺🇸 English",
    'lang.button_ru': "🇷🇺 Русский",
    'start_lang.title': "🌍 Select language / Выберите язык",
    'lang.changed_toast': "✅ Language changed",
    'lang.select': "Welcome to Atlas Secure\n\nPrivate secure access\nwithout complex setup.\n\nPlease select your language:",
    'main.about_text': "Atlas Secure — digital ecosystem,\ndeployed within Telegram.\n\n🔐 Architecture without log storage\n⚡ High and stable connection speed\n📶 Proper operation in LTE / 5G / Wi-Fi\n🧩 Personal access keys\n🇪🇺 Yellow \n🛡 Privacy by default\n\n🌍 Multilingual interface\n💳 Secure payment methods\n\nThe ecosystem is built so\nthat the connection remains stable,\nand management — simple and transparent.",
    'main.about_title': "🔎 About Atlas Secure",
    'main.balance_topup_success': "✅ Balance successfully topped up by {amount:.2f} ₽",
    'main.buy': "🔐 Buy Subscription",
    'main.invalid_promo': "❌ Invalid promo code",
    'main.our_channel': "Our Channel",
    'main.pay_balance': "💰 Balance (available: {balance:.2f} ₽)",
    'main.pay_with_card': "💳 Pay with card",
    'main.privacy_policy': "Privacy Policy",
    'main.privacy_policy_text': "🔐 Privacy Policy Atlas Secure\n\nAtlas Secure is built on the principle\nof data minimization.\n\nWe don't collect and don't store information\nthat is not required for service operation.\n\nWhat we DON'T store:\n• connection history\n• IP addresses and network traffic\n• DNS queries\n• data about visited resources\n• user activity metadata\n\nService architecture is implemented\non Zero-Logs principle.\n\nWhat may be processed:\n• access status\n• subscription validity period\n• technical key identifier\n\nThis data is not linked\nto your network activity.\n\nPayments:\nAtlas Secure doesn't process\nand doesn't store payment data.\nPayment goes through\nbanking and payment systems\noutside our infrastructure.\n\nData sharing:\nWe don't share data with third parties\nand don't use trackers,\nanalytics or advertising SDKs.\n\nSupport:\nWe process only information\nthat you voluntarily provide\nfor resolving a specific request.\n\n🔒 Privacy Policy: <a href=\"https://telegra.ph/Politika-konfidencialnosti-02-16-32\">read</a>\n📜 User Agreement: <a href=\"https://telegra.ph/Polzovatelskoe-soglashenie-04-10-27\">read</a>\n\nAtlas Secure.\nPrivacy is embedded\nin service architecture.",
    'games.menu_title': "<tg-emoji emoji-id=\"5319247469165433798\">🎮</tg-emoji> Welcome to the Game Hall!\nHere you can relax and try your luck — and win prizes and bonuses.\n\n<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> Bowling — knock down pins and get bonus subscription days\n<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Dice — roll the dice and get as many days as the number rolled\n<tg-emoji emoji-id=\"5226813248900187912\">💣</tg-emoji> Bomber — strategic survival game\n<tg-emoji emoji-id=\"5474417568053745249\">🌱</tg-emoji> Farm — grow plants and earn rubles to your balance\n\nChoose a game and try your luck! <tg-emoji emoji-id=\"5258040062028822951\">🍀</tg-emoji>",
    'games.button_bowling': "🎳 Bowling",
    'games.button_dice': "🎲 Dice",
    'games.button_bomber': "💣 Bomber",
    'games.back_to_games': "🔙 Back to games",
    'games.bowling_cooldown': "Bowling club closed <tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji>\nNext game available in: {days}d {hours}h",
    'games.bowling_paywall': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> Bowling club is for subscribers only!\n\nPurchase a subscription to play.",
    'games.bowling_strike_success': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> <b>Strike!</b> All pins knocked down!\n\n🎉 Congratulations! You won +7 days subscription.\n\nAccess until: {date}",
    'games.bowling_strike_error': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> <b>Strike!</b> All pins knocked down!\n\n🎉 Congratulations! You won +7 days subscription.\n\n⚠️ Error crediting. Please contact support.",
    'games.bowling_no_strike': "<tg-emoji emoji-id=\"5370853837689070338\">🎳</tg-emoji> You knocked down {value} pins out of 6.\n\nAlas, no strike 😔 Try again in 7 days!",
    'games.dice_cooldown': "⏳ You already rolled the dice!\nNext roll available in: {days} days {hours} hours",
    'games.dice_paywall': "<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Dice game is for subscribers only!\n\nPurchase a subscription to play.",
    'games.dice_success': "<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Rolled: {value}!\n\n🎉 You've been credited {value} days subscription!\n\nYour subscription is valid until: {date}",
    'games.dice_error': "<tg-emoji emoji-id=\"5280816565657300091\">🎲</tg-emoji> Rolled: {value}!\n\n🎉 You've been credited {value} days subscription!\n\n⚠️ Error crediting. Please contact support.",
    'games.bomber_rules': "<tg-emoji emoji-id=\"5226813248900187912\">💣</tg-emoji> Bomber\n\nRules:\n• Place bombs on the field, avoiding bot mines\n• If you step on your own bomb — explosion! 💥\n• If you step on a bot mine — explosion! 💥\n• Press 'Finish' to exit safely\n\nGood luck! <tg-emoji emoji-id=\"5258040062028822951\">🍀</tg-emoji>",
    'games.bomber_finish': "🚩 Finish",
    'games.bomber_self_destruct': "🧨 BOOM! You blew up on your own bomb!\n\nGame over. Try again!",
    'games.bomber_mine_exploded': "💥 BOOM! You blew up on a bot mine!\n\nGame over. Try again!",
    'games.bomber_safe_exit': "😮‍💨 You exited the game safely!\n\nBombs survived: {count}",
    'main.profile': "👤 Dashboard",
    'main.promo_applied': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Promo code applied. Discount already included in price.",
    'main.reissue_notification_text': "Your VPN key has been updated\nand migrated to a new server version.\n\nFor proper operation:\n— remove the old key from VPN app\n— add the new access key\n\nKey:\n\n{sub_url}\n\nUpdate is necessary to maintain\nstability and connection performance.",
    'main.reissue_notification_title': "🔐 VPN Key Update",
    'main.service_unavailable': "⚠️ Service temporarily unavailable. Please try again later.",
    'main.service_unavailable_payment': "✅ <b>Payment received.</b>\n\nThe service is temporarily unavailable, so we will grant what you paid for manually shortly — we have already been notified.\n\n<b>You do not need to pay again.</b>\n\nIf you have questions, contact support. Reference: <code>{ref}</code>",
    'main.support': "🛡 Support",
    'main.support_button': "🆘 Support",
    'main.topup_amount_invalid': "Please enter a number.",
    'main.topup_amount_too_high': "Maximum top-up amount: 100,000 ₽. Please enter a smaller amount.",
    'main.topup_amount_too_low': "Minimum top-up amount: 100 ₽. Please enter an amount of at least 100 ₽.",
    'main.topup_balance_select_amount': "💳 <b>Balance top-up</b>\n\nCurrent balance: {balance} ₽\n\nTop up to pay for subscriptions, traffic and gifts from your balance in one tap.",
    'main.topup_balance_success': "✅ <b>Balance topped up by {amount:.2f} ₽</b>\n\nBalance: {balance:.2f} ₽",
    'main.topup_custom_amount': "Custom amount",
    'main.topup_enter_amount': "Enter your amount from 100 ₽",
    'main.topup_invoice_description': "Balance top-up for {amount} ₽",
    'main.topup_invoice_label': "Balance Top-Up",
    'main.topup_invoice_title': "Atlas Secure Balance Top-Up",
    'main.topup_select_payment_method': "Balance top-up for {amount} ₽\n\nSelect payment method:",
    'main.trial_activation_error': "❌ Error activating trial period. Please try again later or contact support.",
    'main.trial_not_available': "❌ Trial period is not available. You have already used it or have an active subscription.",
    'main.welcome': "🔐 Atlas Secure\n\n🧩 Private digital access\n⚙️ Stable operation of familiar services\n🛡 Privacy by default\n\nYou connect —\neverything else works in the background.",
    'main.welcome_bypass': "💎 <b>Atlas Secure</b> — your digital shield\n\n🌐 Bypass — active\n\n⚡️ Lightning-fast connection\n🛡 Traffic protected 24/7\n🌍 The internet as it should be",
    'payment.already_processed': "✅ This payment has already been processed.",
    'payment.approved': "✅ Access activated\n\nYour personal access key is ready.\n\n🔑 Personal access key will be sent in the next message.\n\n🟢 Access valid until:\nuntil {date}\n\nThe key is assigned to you\nand will be available in your profile.\n\n👉 Connection takes no more than 1 minute.\nIf you need help — we're here.",
    'payment.balance': "💰 Balance (available: {balance:.2f} ₽)",
    'payment.card': "💳 Bank Card",
    'payment.stars': "⭐ Telegram Stars",
    'payment.stars_invoice_label': "Stars payment",
    'payment.stars_invoice_description': "Atlas Secure VPN {tariff_name} — {months} mo. (⭐ Stars)",
    'payment.invoice_timeout': "⏱ Please pay within 15 minutes. The invoice will be cancelled after expiration.",
    'payment.expired': "❌ Payment expired. Please create a new payment.",
    'payment.fallback_first': "🎉 Subscription successfully activated\n\n📅 Valid until: {date}\n\n🔐 Your connection key will be sent in the next message.",
    'payment.fallback_renewal': "🔄 Subscription renewed\n\n📅 New validity: until {date}\n\n🔐 Your current key (same UUID) will be sent in the next message.",
    'payment.paid_button': "Confirm payment",
    'payment.pending': "Confirmation in process\n\nPayment registered.\nVerification takes up to 5 minutes.\nAccess activation is performed automatically.",
    'payment.pending_activation': "✅ Subscription created!\n\n📅 Valid until: {date}\n\n⏳ Activation is in progress. VPN key will be sent to you shortly.\n\nIf the key doesn't arrive within an hour, please contact support.",
    'payment.rejected': "❌ Payment not confirmed.\n\nIf you are sure you paid —\ncontact support.",
    'payment.sbp': "🏦 SBP",
    'payment.sbp_waiting': "🏦 <b>SBP Payment</b>\n\nAmount: {amount:.2f} ₽\n\nTap the button below to open the payment form.\n\nWaiting for payment <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Processing takes up to 5 minutes — depends on the bank.</i>",
    'payment.sbp_pay_button': "🏦 Pay via SBP",
    'payment.sbp_unavailable': "SBP payment is temporarily unavailable",
    'payment.wata_check_button': "🔄 Check payment",
    'payment.wata_check_cooldown': "Try again in {seconds} sec.",
    'payment.wata_check_not_paid': "⌛ Payment not received yet. Usually takes up to 1 minute.",
    'payment.wata_check_paid': "✅ Payment confirmed! Activating access…",
    'payment.wata_check_already': "✅ Payment already confirmed.",
    'payment.wata_check_error': "Could not verify payment, try again later.",
    'payment.crypto': "🌎 CryptoBot",
    'payment.crypto_waiting': "₿ <b>Cryptocurrency Payment</b>\n\nAmount: {amount:.2f} ₽\n\nTap the button below to open @CryptoBot.\n\nWaiting for payment <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Processing takes up to 5 minutes — depends on the network.</i>",
    'payment.crypto_pay_button': "₿ Pay via CryptoBot",
    'payment.crypto_unavailable': "Cryptocurrency payment is temporarily unavailable",
    'payment.lava': "💳 Card / SBP",
    'payment.lava_pay_button': "💳 Pay via Lava",
    'payment.crypto_success': "🎉 Payment received!\n{tariff_icon} Plan: {tariff}\n📅 Until: {date}",
    'payment.select_method': "Choose payment method:\n\nAmount: {price:.2f} ₽",
    'payment.success': "✅ Payment processed successfully!",
    'payment.success_first': "🎉 <b>Subscription successfully activated</b>\n\n📅 <b>Valid until:</b> {date}\n\n🔐 <b>Your connection key:</b>\n<code>{sub_url}</code>\n\nYou can use it in the VPN app.",
    'payment.success_renewal': "🔄 <b>Subscription renewed</b>\n\n📅 <b>New validity:</b> until {date}\n\n🔐 <b>Your current key</b> (same UUID):\n<code>{sub_url}</code>\n\nYou can continue using your current connection key.",
    'payment.success_renewal_compact': "✅ Subscription renewed\n{tariff_icon} Tariff: {tariff}\n📅 Until: {date}",
    'payment.success_welcome_plus': "🎉 Welcome to Atlas Secure!\n⭐️ Tariff: Plus\n📅 Until: {date}",
    'payment.success_welcome_basic': "🎉 Welcome to Atlas Secure!\n📦 Tariff: Basic\n📅 Until: {date}",
    'payment.test': "Service mode Unavailable",
    'referral.cashback_amount': "💳 Purchase amount: {amount:.2f} ₽",
    'referral.cashback_balance_auto': "Balance topped up automatically.",
    'referral.cashback_invite_button': "👥 Invite more friends",
    'referral.cashback_level': "📊 Your level: {percent}%",
    'referral.cashback_max_level': "🎯 You've reached the maximum level!",
    'referral.cashback_progress': "👥 To next level: {needed} {friend} remaining",
    'referral.cashback_reward': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Cashback: {amount:.2f} ₽ ({percent}%)",
    'referral.cashback_subscription_period': "⏰ Subscription: {period}",
    'referral.cashback_title': "💰 Cashback credited!",
    'referral.friend_dual': "friends",
    'referral.friend_plural': "friends",
    'referral.friend_singular': "friend",
    'referral.how_it_works': "📊 How the program works",
    'referral.how_it_works_text': "📊 How the referral program works\n\n1. Send your referral link to a friend\n2. Your friend opens it and registers\n3. For every purchase your friend makes, you get cashback to your balance\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Cashback levels (friends who have paid at least once):\n• 0–24 → 10%\n• 25–49 → 20%\n• 50–74 → 30%\n• 75–99 → 40%\n• 100+ → 45%\n\n💰 Cashback is credited to your balance automatically on every purchase of your referrals.",
    'referral.max_level_reached': "🏆 You have reached the maximum program level",
    'referral.reward_notification': "🔥 You've earned referral cashback!\n\nYour friend has purchased a subscription.\n💰 Awarded: {amount:.2f} ₽\nBalance: {balance:.2f} ₽",
    'referral.share_button': "📤 Share link",
    'referral.stats_button': "More",
    'referral.stats_screen': "🔐 Atlas Secure Loyalty Program\n\n💎 Your status unlocks more benefits.\nEarn rewards for participating in the Atlas Secure ecosystem — no limits.\n\n⸻\n\n🏆 Cashback levels (friends who have paid)\n\n• 0–24 — 10% cashback\n• 25–49 — 20% cashback\n• 50–74 — 30% cashback\n• 75–99 — 40% cashback\n• 100+ — 45% cashback\n\n⸻\n\n🔗 Your personal link:\n{referral_link}\n\n🪙 Rewards are credited to your account balance automatically.\n\n⸻\n\n📊 Current status: {current_status_name}\n{status_footer}",
    'referral.status_footer': "🚀 To next level: {remaining_invites} invites left",
    'subscription.auto_renew_disabled_toast': "⏸ Auto-renewal disabled",
    'subscription.auto_renew_enabled_toast': "✅ Auto-renewal enabled",
    'subscription.renew': "🔁 Renew Subscription",
    'trial.activating': "⏳ <b>Activating trial…</b>\n\nCreating your access. This usually takes 2–3 seconds.",
    'trial.activated': "✅ <b>Trial access activated</b>\n\n📦 Plan: Basic · 3 days\n📅 Until: {expires_date}\n\nTo get started — tap <b>📲 Set up device</b> and follow the simple step-by-step guide.",
    'trial.activated_btn_connect': "🚀 Connect",

    # === BROADCAST: «🎁 Get trial key» ===
    'broadcast.trial_key_activated': "🎁 <b>Gift activated!</b>\n\nYou've received: <b>1 day</b> of subscription access and <b>1 GB</b> of whitelist-bypass traffic.\n\nLet's connect your device 👇",
    'broadcast.trial_key_already': "🎁 Gift already claimed",
    'broadcast.trial_key_error': "Couldn't activate the gift, please try again later.",
    'trial.expired': "🔓 <b>Trial access ended</b>\n\nYour trial period has expired.\n\n🎁 A <b>30% discount</b> on your subscription is now active on your account for 7 days — it is applied automatically at checkout.\n\nTap the button below to continue using secure access.",
    'trial.notification_71h': "🚨 Last hour of trial access\n\nVPN will be disabled in one hour.\n\nSubscribe now to continue using secure access.",
    'reminder.admin_1day_6h': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Your free access to Atlas Secure ends in 6 hours\n\nLiked it? Get a subscription and use it without limits 💙",
    'reminder.admin_7days_24h': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Your free access to Atlas Secure expires in 24 hours\n\nDuring this time your traffic has been safely protected. Enable a subscription from {price} ₽/month 🤍",
    'reminder.paid_3d': "📅 Your Atlas Secure subscription is active for 3 more days\n\nRenew in advance so your access never stops for a second 🤍",
    'reminder.paid_24h': "⚡️ Less than 24 hours of subscription left\n\nRenew now with one tap so VPN keeps working without interruption 🛡",
    'connect.press_button': "📲 <b>Connection</b>\n\nTap <b>«Connect»</b> and follow the instructions — setup takes just a couple of minutes.",

    # Gift subscription
    'main.gift_subscription': "🎁 Gift Subscription",
    'gift.intro': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Gift a Subscription</b>\n\nGive your loved one secure internet access — the best sign of care in the digital age.\n\nChoose a plan for the gift:",
    'gift.choose_period': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Gift — {tariff_name}</b>\n\nChoose the subscription period for the gift:",
    'gift.choose_payment': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Gift — {tariff_name}</b>\n\n📦 Plan: {tariff_name}\n⏳ Period: {period}\n💰 Price: {price} ₽\n\nChoose a payment method:",
    'gift.success': "🎉 <b>Your gift is ready!</b>\n\n📦 Plan: {tariff_name}\n⏳ Period: {period}\n\n🔗 Activation link:\n<code>{gift_link}</code>\n\n📌 <b>Important:</b>\n• The link can only be activated <b>once</b>\n• Send it to the person you want to gift\n• The recipient taps the link — and the subscription activates automatically",
    # Plain text only: goes into t.me/share/url (the user's compose box), where
    # HTML and <tg-emoji> are not rendered. Regular emoji are fine.
    'gift.share_text': "🎁 Hi! I'm gifting you an Atlas Secure subscription — {tariff_name} for {period}.\n\nInternet without blocks: tap the link to activate your gift.",
    'gift.btn_share': "📤 Share Link",
    'gift.activated': "🎉 <b>Gift Activated!</b>\n\n📦 Plan: {tariff_name}\n⏳ Period: {period}\n\nYour subscription is now active.\nOpen «My subscription» → «Connect» to start using it.",
    'gift.activated_welcome': "🎉 <b>Welcome to Atlas Secure!</b>\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> You've received a gift subscription:\n📦 Plan: {tariff_name}\n⏳ Period: {period}\n\nYour subscription is already active.\nOpen «My subscription» → «Connect» to set up VPN.",
    'gift.error_not_found': "❌ Gift link not found.\n\nCheck the link or contact the sender.",
    'gift.error_already_activated': "⚠️ This gift subscription has already been activated.\n\nEach gift can only be used once.",
    'gift.error_expired': "⏰ This gift link has expired.\n\nAsk the sender to purchase a new gift.",
    'gift.error_self_activation': "🚫 You cannot activate your own gift.\n\nSend the link to someone else.",
    'gift.error_invalid': "❌ This gift link is invalid.",
    'gift.my_gifts_title': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>My Gifts</b>",
    'gift.my_gifts_empty': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> You don't have any gifts yet.\n\nYou can purchase a gift from the main menu.",
    'gift.buy_gift_btn': "🎁 Gift a Subscription",
    'gift.back_to_profile': "👤 Back to Profile",
    'gift.back_to_gifts': "🎁 Back to Gifts",
    'gift.page_prev': "⬅️ Back",
    'gift.page_next': "Next ➡️",
    'gift.status_activated': "✅ Activated",
    'gift.status_pending': "❌ Not activated",
    'gift.detail_activated': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>{tariff_name} — {period}</b>\n\n✅ This gift has already been activated.",
    'gift.detail_pending': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Send this gift to someone special!</b>\n\n📦 Plan: {tariff_name}\n⏳ Period: {period}\n\n❌ Not activated\n\n🔗 Activation link:\n<code>{gift_link}</code>",


    # --- Missing translations ---
    # admin

    # biz

    # buy
    'buy.period_24_months': '24 months',

    # farm

    # games
    'games.button_farm': '🌾 Farm',

    # profile

    # referral

    # subscription

    # --- Telegram Premium ---
    'premium.main_button': "⚡️ Buy Telegram Premium ⚡️",
    'premium.enter_username': "💎 <b>Buy Telegram Premium</b>\n\nEnter your Telegram username if buying for yourself, or a friend's username if buying as a gift.\n\n⚠️ Must start with <b>@</b>\nExample: <code>@username</code>",
    'premium.invalid_username': "❌ Invalid username. Enter a valid username starting with @\n\nExample: <code>@username</code>\n\nAttempts left: {attempts}",
    'premium.attempts_exhausted': "❌ You've used all attempts. Please try again.",
    'premium.choose_period': "💎 <b>Choose Telegram Premium subscription period</b>\n\n👤 Username: <code>{username}</code>",
    'premium.period_3m': "3 months | 1 590 ₽",
    'premium.period_6m': "6 months | 2 690 ₽",
    'premium.period_12m': "12 months | 3 790 ₽",
    'premium.choose_payment': "💎 <b>Telegram Premium Payment</b>\n\n👤 Username: <code>{username}</code>\n📅 Period: {period}\n💰 Amount: {price} ₽\n\nChoose payment method:",
    'premium.success': "✅ <b>Payment successful!</b>\n\n💎 Product: Telegram Premium\n👤 Username: <code>{username}</code>\n📅 Period: {period}\n💰 Amount: {price} ₽\n\n⏳ Expect to receive Telegram Premium within <b>5-15 minutes</b>.\n\nIf you haven't received Premium, contact us:",
    'premium.support_button': "💬 Support",
    'premium.back_button': "🔙 Back",
    'premium.admin_notification': "💎 <b>TELEGRAM PREMIUM PURCHASE</b>\n\n👤 Buyer: {buyer_id}\n🎯 Username: <code>{username}</code>\n📅 Period: {period}\n💰 Amount: {price} ₽\n🕐 Date: {date}",

    # --- Traffic / Bypass (Remnawave) ---
    'traffic.info': "📊 <b>Bypass Blocks</b> 🇷🇺\n\n📥 {used} / {limit}\n{bar} {pct}%\n\n⏳ Until: {expires}\n\n🔗 <b>Key for Happ</b> <i>(tap to copy)</i>\n<blockquote expandable><code>{happ_url}</code></blockquote>\n\n🔗 <b>Key for Incy</b> <i>(tap to copy)</i>\n<blockquote expandable><code>{incy_url}</code></blockquote>\n\n<b>One-tap install</b>\nTap a button below — the app opens and imports the key automatically.\n\n<i>If auto-import fails:</i>\n└ <b>Happ:</b> Home → <b>+</b> → Paste from clipboard\n└ <b>Incy:</b> Settings → Import → From clipboard",
    'traffic.no_subscription': "📊 <b>Bypass Blocks</b> 🇷🇺\n\n🔒 No active subscription.",
    'traffic.trial_upgrade_hint': "Get Basic or Plus subscription to unlock more GB and traffic purchases",
    'traffic.not_provisioned': "📊 Bypass is not set up yet. Try again later.",
    'traffic.fetch_error': "⚠️ Failed to load traffic data. Try again later.",
    'traffic.warning_low': "{remaining} of bypass traffic remaining",
    'traffic.warning_critical': "Bypass traffic is almost depleted!",
    'traffic.subscription_expired_bypass_active': "⚠️ <b>Your main subscription has expired</b>\n\nBypass continues to work — your GB are intact.\n\nDon't forget to buy more traffic if it's running low 👇",
    'traffic.buy_subscription': "📈 Buy Subscription",
    'traffic.buy_traffic_btn': "💳 Buy Traffic",
    'traffic.install_insy_btn': "📥 Install in Incy",
    'traffic.install_incy_btn': "📥 Install in Incy",
    'traffic.install_happ_btn': "📥 Install in Happ",
    'traffic.buy_gb_btn': "📈 Buy more GB",
    'traffic.main_menu_btn': "🏠 Main menu",
    'traffic.back_to_traffic': "📊 Back to Traffic",
    'traffic.buy_title': "📦 <b>Buy Traffic</b> 🇷🇺\n\nAdded to your current balance.\n\n📊 <b>The pack is your personal GB balance</b>\nIt doesn't expire and isn't tied to a subscription — it only depletes when you actually use it.\n\n✨ Get just what you need — and use it at your own pace.\nWhen it runs out, top up whenever it's convenient ⭐️\n\n<blockquote>📊 Estimated usage:\n├ 15 GB — ~1 week\n├ 50 GB — ~3 weeks\n├ 75 GB — ~1 month\n├ 100 GB — ~1.5 months\n├ 150 GB — ~2.5 months\n└ 200 GB — ~3 months</blockquote>\n\n💎 The bigger the pack — the cheaper per GB",
    'traffic.buy_title_extended': "🌐 <b>More Volume</b> 🇷🇺\n\nAdded to your current balance.\n\n<blockquote>📊 Estimated usage:\n├ 300 GB — ~5 months\n├ 600 GB — ~10 months\n├ 1,200 GB — ~1.5 years\n├ 2,200 GB — ~3 people for a year\n├ 5,000 GB — ~7 people for a year\n└ 8,000 GB — ~11 people for a year</blockquote>",
    'traffic.confirm_purchase': "💳 <b>Payment: {gb} GB — {price} ₽</b>\n\n💰 Your balance: {balance} ₽",
    'traffic.pay_wata': "Pay",
    'traffic.pay_reserve': "Reserve",
    'traffic.purchase_success': "✅ <b>Traffic added!</b>\n\n📦 +{gb} GB\n💰 {price} ₽",
    'traffic.notify_3gb': "⚠️ {remaining} of bypass traffic remaining 🇷🇺\n\nBuy additional traffic to keep bypass working.",
    'traffic.notify_1gb': "🔴 Less than 1 GB of bypass traffic! Bypass will stop soon.",
    'traffic.notify_500mb': "❗️ Only {remaining} of bypass traffic left!",
    'traffic.notify_zero': "🚫 Bypass traffic depleted.\n\nAtlas Fast 🇩🇪 continues working without limits.",

    # Bypass gift links — user-facing redemption messages
    'bypass_gift.activated': "🎁 <b>Gift activated!</b>\n\nYou received <b>{gb} GB</b> of bypass traffic.\n\nThese GB are now available and work independently of your subscription. Open «Enable bypass» to use them.",
    'bypass_gift.error_already_redeemed': "🎁 <b>This link was already redeemed by your account.</b>\n\nEach link can be used only once per account. If you need more traffic, ask the sender for a new link.",
    'bypass_gift.error_not_found': "❌ <b>Gift link not found.</b>\n\nCheck that the link is correct or contact the sender.",
    'bypass_gift.error_expired': "⏰ <b>This gift link has expired.</b>\n\nAsk the sender to create a new one.",
    'bypass_gift.error_max_uses': "🚫 <b>Redemption limit reached.</b>\n\nThis link has already been used the maximum number of times. Ask the sender for a new link.",
    'bypass_gift.error_remnawave': "⚠️ <b>Could not credit your GB — please try again later.</b>\n\nIf the problem persists, contact support.",
    'bypass_gift.connect_btn': "🌐 Connect Bypass",

    # Bypass gift — dedicated setup flow (only reachable from gift link)
    'bgift_setup.select_device': "📱 <b>Choose your device</b>\n\nWe'll set up bypass in two short steps. First, pick the device you'll use.",
    'bgift_setup.connect_screen': (
        "⚡️ <b>Connect in one tap</b>\n\n"
        "Add the key to start using bypass.\n\n"
        "🌐 <b>Bypass</b> — Russian whitelist routing, the internet works from anywhere in the world.\n\n"
        "<b>Your bypass key:</b>\n"
        "<blockquote><code>{sub_url}</code></blockquote>\n"
        "<i>Tap the key to copy it.</i>\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "<b>📲 Manual setup</b>\n\n"
        "<b>In Happ:</b>\n"
        "1. Open Happ\n"
        "2. Tap <b>+</b> in the top-right corner\n"
        "3. Choose <b>«Add from clipboard»</b>\n"
        "4. Toggle the connection on\n\n"
        "<blockquote>💡 The «Add key» button above does this automatically — no copying needed.</blockquote>"
    ),
    'bgift_setup.connect_no_key': (
        "⚡️ <b>Connect in one tap</b>\n\n"
        "🌐 <b>Bypass</b> — Russian whitelist routing, the internet works from anywhere in the world.\n\n"
        "⚠️ We couldn't fetch your bypass key. "
        "Wait a minute and tap «Back → Next» again. "
        "If the error persists, please contact support."
    ),

    # === Sync with ru.py — missing translations ===

    # main
    'main.welcome_no_sub': "<tg-emoji emoji-id=\"5462902520215002477\">💎</tg-emoji> <b>Atlas Secure</b> — free, fast and secure internet\n\n<tg-emoji emoji-id=\"5447410659077661506\">🌐</tg-emoji> Bypass whitelists\n<tg-emoji emoji-id=\"5188481279963715781\">🚀</tg-emoji> Speed up to 75 Gbps\n<tg-emoji emoji-id=\"5456140674028019486\">⚡️</tg-emoji> Connect in a minute — no setup, no headaches\n📱 One subscription — the whole family covered\n\n<tg-emoji emoji-id=\"5193065010795911968\">🛍</tg-emoji> In our shop — top up Steam, buy Telegram Premium and more\n\n100,000+ users trust us.",
    'main.welcome_expired': "<tg-emoji emoji-id=\"5462902520215002477\">💎</tg-emoji> <b>Atlas Secure</b>\n\nYour subscription ended — but bringing it back is easy.\n\nYour key and settings are saved.\nTap the button — VPN will work again.\n\n<tg-emoji emoji-id=\"5193065010795911968\">🛍</tg-emoji> In our shop — top up Steam, buy Telegram Premium and more\n\n<blockquote>100,000+ users trust us</blockquote>",

    # buy
    'buy.button_price_badge': "{period} — {price} ₽ {badge}",
    'buy.button_price_discount_badge': "{period} — {base} → {final} ₽ {badge}",

    # bypass
    'bypass.buy_title': "🌐 <b>Whitelist Bypass 🇷🇺</b>\n\nAccess blocked sites via the latest bypass protocol.\nPay only for traffic — no monthly fees.\n\n<blockquote>📊 Estimated usage:\n├ 15 GB — ~1.5 months\n├ 25 GB — ~2.5 months\n├ 45 GB — ~4 months\n├ 60 GB — ~6 months\n└ 120 GB — ~1 year</blockquote>\n\n💡 Text and photos — light. Video and Reels — heavier.",
    'bypass.buy_title_trial': "\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Bonus:</b> 3 days of free access to the main servers!",
    'bypass.purchase_success': "✅ <b>Whitelist bypass activated!</b>\n\n📦 +{gb} GB of traffic added",
    'bypass.gift_premium_granted': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Gift: 3 days of premium</b> — until {until} MSK.\n\nThe main-server key is in your dashboard. When the gift ends, bypass keeps working on the GB you bought.",
    'bypass.activation_delayed': "⚠️ Your GB are delayed. If they do not appear within an hour, contact support.",
    'bypass.btn_profile': "👤 Dashboard",
    'bypass.btn_buy_more_gb': "🌐 Buy more GB",
    'bypass.btn_main_menu': "← Main menu",

    # bypass_setup
    'bypass_setup.title': (
        "🌐 <b>Set up bypass</b>\n\n"
        "Open one of the clients below — setup takes 30 seconds, "
        "the key is already generated and assigned to you.\n\n"
        "<b>📲 Happ</b> — universal, works on iOS / Android / Mac / Windows.\n"
        "<b>💚 Incy</b> — fast iOS client with great UX.\n\n"
        "<b>How to connect:</b>\n"
        "1. Make sure the client is installed (if not — install from App Store / Play Market).\n"
        "2. Tap <b>«➕ Add bypass»</b> — Telegram will open the client "
        "and import the key automatically.\n"
        "3. Didn't open? Tap <b>«🔑 Show key manually»</b>, "
        "copy the key and paste it into the client from clipboard."
    ),
    'bypass_setup.add_happ_btn': "➕ Add bypass to Happ",
    'bypass_setup.add_incy_btn': "➕ Add bypass to Incy",
    'bypass_setup.manual_btn': "🔑 Show key manually",
    'bypass_setup.no_key_yet': (
        "⏳ <b>Bypass key not ready yet</b>\n\n"
        "Usually it takes less than a minute — check back shortly."
    ),
    'bypass_setup.manual_screen_header': (
        "🔑 <b>Bypass keys</b>\n\n"
        "Tap the key below — it will <b>copy to clipboard</b>. Then "
        "open the client → paste / clipboard button."
    ),
    'bypass_setup.manual_screen_happ_block': (
        "📲 <b>Key for Happ</b> (crypt4)\n"
        "<blockquote expandable><code>{happ_key}</code></blockquote>"
    ),
    'bypass_setup.manual_screen_incy_block': (
        "💚 <b>Key for Incy</b> (crypt1)\n"
        "<blockquote expandable><code>{incy_key}</code></blockquote>"
    ),
    'bypass_setup.manual_screen_footer': (
        "💡 If the key didn't import — go back and try "
        "the <b>«➕ Add bypass»</b> button. It opens the client directly "
        "via deeplink."
    ),

    # combo
    'combo.screen_title': "🚀 <b>Combo Subscription</b>\n\nMain servers + whitelist bypass in one pack.\nChoose a plan:",
    'combo.tariff_basic': "⚡ <b>Combo Basic</b>\n\n<blockquote>🌐 Main servers (unlimited) · up to 25 Gbps\n🌐 Whitelist bypass + traffic included\n👨‍👩‍👧‍👦 One subscription for the whole family — up to 10 devices</blockquote>\n\n📊 <b>The pack is your personal GB balance</b>\n<blockquote>It doesn't expire and isn't tied to a subscription — it only depletes when you actually use servers marked <b>LTE</b>.</blockquote>\n\n<i>When it runs out — top up whenever it's convenient</i> ⭐️",
    'combo.tariff_plus': "👑 <b>Combo Plus</b>\n\n<blockquote>🌐 Priority servers (unlimited) · up to 75 Gbps\n🔄 Backup channels · connection always works\n🌐 Whitelist bypass + traffic included\n👨‍👩‍👧‍👦 One subscription for the whole family — up to 14 devices</blockquote>\n\n📊 <b>The pack is your personal GB balance</b>\n<blockquote>It doesn't expire and isn't tied to a subscription — it only depletes when you actually use servers marked <b>LTE</b>.</blockquote>\n\n<i>When it runs out — top up whenever it's convenient</i> ⭐️",
    'combo.select_basic': "⚡ Combo Basic",
    'combo.select_plus': "👑 Combo Plus",
    'combo.period_1': "1 mo · {gb} GB bypass · {price} ₽",
    'combo.period_3': "3 mo · {gb} GB bypass · {price} ₽",
    'combo.period_6': "6 mo · {gb} GB bypass · {price} ₽",
    'combo.period_12': "12 mo · {gb} GB bypass · {price} ₽",
    'combo.period_24': "24 mo · {gb} GB bypass · {price} ₽",
    'tariff_switch.menu_title': "📦 <b>Change plan</b>",
    'tariff_switch.applies_now': "The new plan is activated right after payment and applies to the whole subscription, including the days you have left.",
    'tariff_switch.available': "Available plans:",

    # help / FAQ
    'help.menu_title': "❓ <b>Help</b>\n\nChoose an option below:\n\n<blockquote>📖 <b>Frequently asked questions</b>\nShort solutions to common issues</blockquote>\n\n<blockquote>📲 <b>Service guides</b>\nHow to set up VPN on your device</blockquote>\n\n<blockquote>📞 <b>Contacts</b>\nSupport and sales emails</blockquote>\n\n<blockquote>💬 <b>Help</b>\nMessage a live operator in Telegram</blockquote>",
    'help.contacts_title': "📞 <b>Contacts</b>\n\nReach us by email — <b>tap an address to copy it</b>.\n\n<blockquote>📧 <b>Technical support</b></blockquote>\n<code>support@atlassecure.uk</code>\nAtlas Secure and QoDev projects\n\n<blockquote>💼 <b>Sales</b></blockquote>\n<code>sales@atlassecure.uk</code>\nAtlas Secure infrastructure solutions",
    'help.faq_title': "📖 <b>Frequently asked questions</b>\n\nPick your question — we'll show a short solution.",
    'help.faq_q1': "🚫 VPN not working",
    'help.faq_q2': "📲 How to connect / set up",
    'help.faq_q3': "🐌 Slow speed",
    'help.faq_q4': "💳 Payment failed",
    'help.faq_q5': "📱 How to add another device",
    'help.faq_q6': "🔑 How to update the key",
    'help.faq_q7': "🌐 How bypass servers work",
    'help.faq_q8': "📊 How bypass gigabytes work",
    'help.faq_q9': "⚠️ Happ — Xray core error",
    'help.faq_a1': "🚫 <b>VPN not working</b>\n\nWalk through these steps — usually one of them fixes it.\n\n<blockquote>1️⃣ <b>Check the internet without VPN</b>\nTurn off VPN, open any site. If it doesn't work — the problem is with your provider, not us.</blockquote>\n\n<blockquote>2️⃣ <b>Is mobile connectivity being throttled in your area?</b>\nIn the app pick a server marked <b>LTE</b> — it's built specifically to bypass mobile carrier restrictions.</blockquote>\n\n<blockquote>3️⃣ <b>Restart the app</b>\nFully close <b>Happ</b> (swipe it away) → open again → toggle connection on.</blockquote>\n\n<blockquote>4️⃣ <b>Re-import the key</b>\nIn the bot: <b>«📲 Connect»</b> → your device → <b>«Import key»</b>. The subscription URL may have updated.</blockquote>\n\n<i>Still stuck? Message the operator — we reply within 5–10 minutes.</i> 💬",
    'help.faq_a2': "📲 <b>How to connect</b>\n\nIt takes less than a minute.\n\n<blockquote>1️⃣ In the bot tap <b>«📲 Connect»</b>\n\n2️⃣ Pick your device — <b>iPhone · Android · Mac · Windows</b>\n\n3️⃣ Install the app via our link\n\n4️⃣ Tap <b>«Import key»</b> — subscription will be added automatically\n\n5️⃣ Turn on VPN in the app 🚀</blockquote>\n\n<i>💡 Every step is illustrated — hard to get lost. If anything doesn't work — message the operator.</i> 💬",
    'help.faq_a3': "🐌 <b>Slow speed</b>\n\nWalk through these steps.\n\n<blockquote>1️⃣ <b>Check speed without VPN</b>\nMeasure at <a href=\"https://fast.com\">fast.com</a> with VPN off. If your base internet is slow — VPN physically cannot make it faster.</blockquote>\n\n<blockquote>2️⃣ <b>Switch server</b>\nIn the app pick another one — sometimes neighboring countries are faster (e.g. Germany instead of Netherlands).</blockquote>\n\n<blockquote>3️⃣ <b>On LTE / 5G?</b>\nMobile speed depends on tower load and time of day. Evening — slower, night — faster.</blockquote>\n\n<blockquote>4️⃣ <b>Reboot your Wi-Fi router</b>\nSometimes the router itself hangs, not the VPN. Unplug for 30 seconds → plug back in.</blockquote>\n\n<i>Consistently slow on every server? Message the operator.</i> 💬",
    'help.faq_a4': "💳 <b>Payment failed</b>\n\nWalk through these steps.\n\n<blockquote>1️⃣ <b>Switch payment method</b>\n<b>SBP → card → Telegram Stars → bot balance</b>. If one doesn't work — try another.</blockquote>\n\n<blockquote>2️⃣ <b>Payment hangs?</b>\nWait 10–15 minutes. Money will either be charged and the subscription activated, or refunded to the card automatically — we don't hold anything.</blockquote>\n\n<blockquote>3️⃣ <b>Charged but no subscription?</b>\nJust message the operator — we'll definitely help.</blockquote>\n\n<i>💬 Support replies within 5–10 minutes.</i>",
    'help.faq_a5': "📱 <b>How to add another device</b>\n\nOne subscription — several devices simultaneously:\n\n<blockquote>• <b>Basic</b> — up to 10 devices\n• <b>Plus</b> — up to 14 devices</blockquote>\n\n<b>How to add a new device</b>\n\n<blockquote>1️⃣ In the bot on the <b>main</b> device tap the <b>«Menu»</b> button (blue icon left of the input field) → <b>«📲 Add device»</b>\n\n2️⃣ Choose the type of device you want to add\n\n3️⃣ The bot will send a <b>QR code</b> and a short guide</blockquote>\n\n<blockquote>4️⃣ On the <b>new</b> device install the <b>Happ</b> app and open it\n\n5️⃣ Tap <b>«+»</b> in the top-right corner → <b>«Scan QR code»</b>\n\n6️⃣ Point the camera at the QR from your main device</blockquote>\n\nDone 🚀\n\n<i>💡 No extra charge — it's the <b>same</b> subscription, just on another device.</i>",
    'help.faq_a6': "🔑 <b>How to update the key</b>\n\nIf in <b>Happ</b> you see <i>«Update key in the bot»</i> or <i>«This version is not supported»</i> — you need to reinstall the key.\n\n<blockquote>1️⃣ Tap the <b>«Menu»</b> button 🔵 (blue icon left of the input field)\n\n2️⃣ Select <b>«📲 Connect»</b>\n\n3️⃣ Pick your device\n\n4️⃣ Install the <b>Happ</b> app\n<i>If the app is already installed — tap «Next» and skip this step.</i>\n\n5️⃣ Import the key — go through the standard setup</blockquote>\n\n<b>Which key to pick?</b>\n\n<blockquote>🌐 <b>Add VPN</b> — main unlimited servers\n\n🛡 <b>Add bypass</b> — whitelist bypass servers</blockquote>\n\n<i>Didn't work? Message the operator — we'll help.</i> 💬",
    'help.faq_a7': "🌐 <b>How bypass servers work</b>\n\nWhitelist bypass servers come in two flavors:\n\n<blockquote>🇷🇺 <b>With Russian flag</b>\n🇪🇺 <b>With European flag</b></blockquote>\n\n<b>Which to pick?</b>\n\n<blockquote>We recommend <b>European-flag servers</b> — they're more stable and work better with <b>Telegram</b>, <b>Instagram</b> and other RU-blocked services.</blockquote>\n\n<b>Connected but server doesn't work?</b>\n\n<blockquote>1️⃣ Pick a different server and try again\n\n2️⃣ Toggle airplane mode on and off ✈️\n\n3️⃣ Close the app and reopen it\n\n4️⃣ Check mobile connectivity and make sure the connection is fine</blockquote>\n\n<i>Didn't help? Message the operator.</i> 💬",
    'help.faq_a8': "📊 <b>How bypass gigabytes work</b>\n\nGigabytes for bypass servers are a <b>separate traffic pack</b>. Buy once and spend at your own pace.\n\n<blockquote>♾ <b>No expiration</b>\nThey don't burn. Not tied to a month, day or subscription. What you use is what's deducted, the rest stays yours.</blockquote>\n\n<blockquote>🔓 <b>Independent of subscription</b>\nIt's <b>not</b> part of the main VPN subscription. Subscription ended — bypass gigabytes stay in your account waiting for you.</blockquote>\n\n<b>Example</b>\n\n<blockquote>💰 You bought <b>30 GB</b>\n📉 In a week you used <b>5 GB</b>\n✅ Remaining — <b>25 GB</b>\n\nThose 25 GB aren't going anywhere: use them tomorrow, in a month or in six months.</blockquote>\n\n<b>Where to see the remainder</b>\n\n<blockquote>📱 In <b>Happ</b> — next to the bypass server connection\n\n👤 In the <b>bot</b> — <b>«👤 Profile»</b> section</blockquote>\n\n<i>Ran out? Top up any time — new GB simply add to the remainder.</i> 💬",
    'help.faq_a9': "⚠️ <b>Happ — Xray core error</b>\n\nCommon issue, fixed in a minute.\n\n<b>Why it happens</b>\n\n<blockquote>Many VPN services automatically push <b>routing files</b> onto your device — without your knowledge. We don't do that: <b>all settings live on our servers</b>, not on your phone.\n\nBut sometimes files left behind by other VPNs conflict with our core — and Happ shows the Xray error.</blockquote>\n\n<b>How to fix it</b>\n\n<blockquote>1️⃣ Open <b>Happ</b>\n\n2️⃣ Tap the <b>gear ⚙️</b> in the top-left corner\n\n3️⃣ Choose <b>«Routing»</b>\n\n4️⃣ Delete <b>every</b> routing file from the list\n\n5️⃣ Restart the app</blockquote>\n\nConnection will work normally 🚀\n\n<i>Didn't help? Message the operator — we'll sort it out in 5–10 minutes.</i> 💬",

    # payment
    'payment.card_pl': "Bank card",
    'payment.card_pl_waiting': "💳 <b>Card payment</b>\n\nAmount: {amount:.2f} ₽\n\nTap the button below to open the payment form.\n\nWaiting for payment <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Processing takes up to 5 minutes — depends on the bank.</i>",
    'payment.card_pl_pay_button': "💳 Pay by card",
    'payment.card_pl_unavailable': "Card payment temporarily unavailable",
    'payment.intl_pl': "🌎 International payments",
    'payment.intl_pl_waiting': "🌎 <b>International payment</b>\n\nAmount: {amount:.2f} ₽\n\nTap the button below to open the payment form.\n\nWaiting for payment <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Processing takes up to 5 minutes — depends on the bank.</i>",
    'payment.intl_pl_pay_button': "🌎 Pay",
    'payment.intl_pl_unavailable': "International payments temporarily unavailable",

    # promo_tpl

    # purchase
    'purchase.success_first': "🎉 <b>Subscription activated!</b>\n\n📦 Plan: <b>{tariff_name}</b>\n⏳ Period: {period}\n📅 Until: {expires_date}{gb_line}\n\nJust one step left — tap «Connect» below, it takes a minute.\n\n🤍 Your traffic is protected by Atlas Secure.",
    'purchase.success_renewal': "✅ <b>Subscription renewed!</b>\n\n📦 Plan: <b>{tariff_name}</b>\n⏳ Period: {period}\n📅 Until: {expires_date}{gb_line}\n\nVPN keeps working — nothing to configure.\n\n🤍 Thanks for staying with Atlas Secure!",
    'purchase.success_tariff_changed': "✅ <b>Plan changed to {tariff_name}</b>\n\n⏳ Paid: {period}\n📅 Until: {expires_date}{gb_line}\n\nThe new plan is already active — VPN keeps working.",
    'purchase.success_gb_line': "\n🌐 Whitelist bypass: +{gb} GB",
    'purchase.activated_later': "🎉 <b>Subscription activated!</b>\n\n📦 Plan: <b>{tariff_name}</b>\n📅 Until: {expires_date}\n\nTap «Connect» below to set up your device.",
    'autorenew.insufficient_balance': "⚠️ <b>Not enough money for auto-renewal</b>\n\nThe renewal costs {amount} ₽, your balance is {balance} ₽ — <b>{missing} ₽</b> short.\n\nTop up before {deadline} and we renew your subscription automatically.",
    'autorenew.failed_debit':"⚠️ <b>Auto-renewal did not go through</b>\n\nWe could not charge {amount:.2f} ₽ from your balance — nothing was charged.\n\nRenew your subscription manually so VPN keeps working.",
    'autorenew.failed_refunded': "⚠️ <b>Auto-renewal was not completed</b>\n\n{amount:.2f} ₽ is back on your balance — we are looking into it.\n\nRenew your subscription manually so VPN keeps working.",
    'tariff.name_basic': "Basic",
    'tariff.name_plus': "Plus",
    'tariff.name_combo_basic': "Combo Basic",
    'tariff.name_combo_plus': "Combo Plus",
    'buy.manage_title': "📦 <b>Manage subscription</b>\n\nYour current plan:\n\n{tariff_desc}\n\nChoose an action:",
    'buy.manage_renew': "Renew {name}",
    'buy.manage_switch': "Change plan",
    'buy.manage_buy_gb': "Buy bypass GB",
    'tariff_switch.title': "{icon} <b>Switch to {name}</b>",
    'tariff_switch.choose_period': "Choose a period:",
    'tariff_switch.combo_benefits': "💡 <b>Why Combo:</b>\n✅ Bypass traffic is already included\n✅ No need to buy GB separately\n✅ Up to 30% cheaper than buying separately",
    'payment.wata_waiting': "💳 <b>Pay by card or SBP</b>\n\nAmount: {amount} ₽\n\nTap the button below — the payment form opens (card, SBP, T-Pay).\n\nWaiting for the payment <tg-emoji emoji-id=\"5886538930148350129\">⏳</tg-emoji>\n<i>Processing takes up to 5 minutes, depending on the bank.</i>",
    'payment.wata_pay_button': "💳 Pay {amount} ₽",
    'payment.wata_beta_only': "This payment method is not available yet.",
    'payment.markup_suffix': " (+{percent}%)",
    'payment.btn_cancel': "❌ Cancel",
    'payment.wata_declined': "❌ <b>Payment failed</b>\n\nOrder <code>{order}</code> was not confirmed by the bank. Nothing was activated.\n\n🎫 <b>Reference:</b> <code>{ticket}</code>\n\n<b>If money was charged</b> — keep this reference and contact support, we will refund it.\n\nIf nothing was charged, you can try to pay again.",
    'payment.wata_retry_button': "🔄 Try again",
    'errors.insufficient_balance_topup_hint': "Top up your balance to pay from it in one tap:",
    'errors.promo_no_longer_valid': "⚠️ The promo code is no longer valid — the payment did not go through, your balance was not charged.\n\nChoose the plan again: the price will be without the code.",
    'gift.btn_my_gifts': "🎁 My gifts",
    'common.msk': "Moscow time",
    'common.unit_gb': "GB",
    'common.unit_mb': "MB",
    'common.unit_kb': "KB",
    'subscription.expired_paid': "⌛️ <b>Your Atlas Secure subscription has ended.</b>\n\nVPN is off. Renew your subscription to use the service again.",
    'subscription.expired_offer_line': "\n\n🎁 <b>15% off</b> your renewal — valid until {deadline}.",
    'subscription.expired_free': "<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> <b>Your free access to Atlas Secure has ended.</b>\n\nVPN is off. Liked it? Get a subscription — from {price} ₽/month 🤍",
    'subscription.expired_gb_left':"⚠️ <b>Your main subscription has ended</b>\n\nThe main servers are off. Bypass keeps working — <b>{remaining}</b> left.\n\nRenew your subscription to get the main servers back, or top up GB when they run low 👇",
    'subscription.expired_gb_works': "⚠️ <b>Your main subscription has ended</b>\n\nThe main servers are off. Bypass keeps working on the GB you have left — see «My subscription» for the balance.\n\nRenew your subscription to get the main servers back 👇",
    'subscription.expired_gb_spent': "⌛️ <b>Your Atlas Secure subscription has ended.</b>\n\nVPN is off: your bypass traffic is used up too.\n\nRenew your subscription or buy bypass GB to use the service again.",
    'subscription.btn_renew_discount_15': "🔥 Renew with 15% off",
    'reminder.paid_3h_no_offer': "🚨 <b>3 hours until shutdown</b>\n\nAfter that VPN will stop working. Renew your subscription to keep your access.",
    'purchase.period_days': "{days} days",
    'purchase.tariff_basic': "⚡️ Basic",
    'purchase.tariff_plus': "👑 Plus",
    'purchase.tariff_combo_basic': "🚀 Combo Basic",
    'purchase.tariff_combo_plus': "🚀 Combo Plus",
    'purchase.link_premium': "🌍 <b>Premium</b> (main servers):\n<code>{url}</code>",
    'purchase.link_bypass': "🚧 <b>Bypass</b> (whitelist bypass):\n<code>{url}</code>",
    'purchase.auto_renewal_success': "<tg-emoji emoji-id=\"5456140674028019486\">🔄</tg-emoji> <b>Subscription auto-renewed</b>\n\n📦 Plan: {tariff_name}\n<tg-emoji emoji-id=\"5454415424319931791\">⏳</tg-emoji> Period: {period}\n📅 Valid until: {expires_date}\n💳 Charged from balance: {amount:.2f} ₽\n\nYour VPN keeps working without interruption.\n\n🛡 Thanks for trusting Atlas Secure!",

    # reminder (paid)
    'reminder.paid_7d': "<tg-emoji emoji-id=\"5454415424319931791\">📅</tg-emoji> Subscription ends in 7 days. Renew in advance — access won't be interrupted.",
    'reminder.paid_7d_btn': "🔁 Renew subscription",
    'reminder.paid_1d_gb': "<tg-emoji emoji-id=\"5190806721286657692\">🔴</tg-emoji> Your subscription ends tomorrow.\n\nTomorrow the main servers turn off; bypass keeps working on your GB — {remaining} left.\n\nRenew now to keep the main servers.",
    'reminder.paid_3h_special_gb': "<tg-emoji emoji-id=\"5190806721286657692\">🚨</tg-emoji> <b>3 hours until your subscription ends</b>\n\nThen the main servers turn off; bypass keeps working on your GB — {remaining} left.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Don't miss out — <b>15% off</b> renewal. Valid until {deadline}.",
    'reminder.paid_3h_no_offer_gb': "🚨 <b>3 hours until your subscription ends</b>\n\nThen the main servers turn off; bypass keeps working on your GB — {remaining} left.\n\nRenew your subscription to keep the main servers.",
    'reminder.paid_autorenew_ok':"🔄 <b>Your subscription renews automatically</b>\n\nIt is active until {date}. Auto-renewal is on: before it ends we charge <b>{amount} ₽</b> from your balance — you have {balance} ₽, that is enough.\n\nNothing to do, VPN keeps working.",
    'reminder.paid_autorenew_topup': "📅 <b>Your subscription is active until {date}</b>\n\nAuto-renewal is on, but your balance is {balance} ₽ and the renewal costs {amount} ₽ — <b>{missing} ₽</b> short.\n\nTop up {missing} ₽ before {deadline} and the subscription renews by itself.",
    'reminder.paid_3d_btn': "🔁 Renew",
    'reminder.paid_1d': "<tg-emoji emoji-id=\"5190806721286657692\">🔴</tg-emoji> Subscription ends tomorrow. Renew now to keep VPN running.",
    'reminder.paid_1d_btn': "🔁 Renew",
    'reminder.paid_3h_special': "<tg-emoji emoji-id=\"5190806721286657692\">🚨</tg-emoji> <b>3 hours until shutdown</b>\n\nAfter that VPN will stop working. Sites and apps will return to blocks.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Don't miss out — <b>15% off</b> renewal. Valid until {deadline}.",
    'reminder.paid_3h_discount_btn': "🔥 Buy with 15% off",

    # retention

    # setup (missing)
    'setup.key_vpn_incy_label': "💚 <b>VPN key for Incy</b> (regular unlimited servers):",
    'setup.key_bypass_incy_label': "💚 <b>Bypass key for Incy</b> (RU whitelists):",
    'setup.manual_alt_hint': "❓ <b>Not working?</b>\nTry the alternative key below — works with any client (V2Box, Incy, Happ, etc.). Just copy and paste it into the app:",
    'setup.alt_key_premium': "🔑 <b>Premium key</b> (raw):",
    'setup.alt_key_bypass': "🌐 <b>Bypass key</b> (raw):",
    'setup.key_install_title': "⚡️ <b>Connect in one tap</b>\n\nTap the buttons below — keys will be added automatically.\nAdd both for full functionality.\n\n<blockquote>🔑 VPN — main servers, all the internet without blocks\n🌐 Bypass — RU whitelists, internet works anywhere in the world</blockquote>",
    'setup.key_install_title_agg': "⚡️ <b>Connect in one tap</b>\n\nTap <b>«Add key»</b> for your app — the subscription will be imported automatically.\n\nIf it didn't open — tap <b>«Manual setup»</b> below.\n\n<blockquote>🌐 Traffic is used <b>only</b> on servers marked <b>LTE</b>.\n🚀 Unlimited servers work without using your GB.</blockquote>",
    'setup.btn_add_happ': "📥 Add key to Happ",
    'setup.btn_add_incy': "💚 Add key to Incy",
    'setup.btn_add_v2raytun': "🚀 Add key to V2RayTun",
    'setup.btn_manual_setup': "⚙️ Manual setup",
    'setup.key_happ_label': "🔑 <b>Happ key</b> (copy and paste into the app):",
    'setup.key_incy_label': "💚 <b>Incy key</b> (copy and paste into the app):",
    'setup.btn_done': "✅ Done",
    'setup.btn_manual': "📋 Install manually",
    'setup.btn_need_help': "💬 Need help",
    'setup.install_app': "📲 <b>Just install the app</b>\n\nIncy — a free VPN app.\nAlternative — Happ.\nTap the button — the app store will open.\n\n<blockquote>Already have Happ or Incy? Tap «Next».</blockquote>",
    'setup.install_happ_ru': "📲 Download Happ (Russia)",
    'setup.install_happ_global': "📲 Download Happ (other region)",
    'setup.next_step': "➡️ Next",
    'setup.qr_choose_app': "📲 <b>Add device</b>\n\nChoose the app you'll connect through:",
    'setup.qr_app_btn_incy': "💚 Incy",
    'setup.qr_app_btn_happ': "Happ",
    'setup.qr_instruction_incy': "📲 <b>Add device</b>\n\n<b>Via QR code:</b>\nScan the code above in Incy → 📸 camera icon.\n\n<b>Manually:</b>\nTap the key below — it will be copied, then in Incy → <b>«Paste»</b>.",

    # share_discount
    'share_discount.screen': (
        "🎁 <b>Gift a friend 30% off</b>\n\n"
        "Send a friend the link — they'll get <b>30%</b> off any "
        "plan (Basic / Plus / Combo).\n\n"
        "⏱ Discount is valid <b>24 hours</b> from activation.\n"
        "👤 One person can activate the discount only once.\n\n"
        "<b>Your link:</b>\n"
        "<blockquote expandable><code>{link}</code></blockquote>"
    ),
    'share_discount.send_button': "📤 Send link to friend",
    'share_discount.share_text': (
        "🎁 Grab 30% off Atlas Secure — valid 24 hours after "
        "following the link."
    ),
    'share_discount.activated': (
        "🎉 <b>30% discount activated!</b>\n\n"
        "Valid <b>24 hours</b> on Basic, Plus and Combo plans.\n"
        "Pick a plan and subscribe 👇"
    ),
    'share_discount.already_claimed': (
        "ℹ️ You've already activated this discount before — it can't "
        "be used again."
    ),
    'share_discount.self_blocked': (
        "🚫 You can't activate a discount from your own link — "
        "send it to a friend."
    ),

    # shop
    'shop.title': "<tg-emoji emoji-id=\"5193065010795911968\">🛍</tg-emoji> <b>Mini Shop</b>\n\nBuy useful digital goods here — quick, painless, no hassle.\n\n<blockquote><tg-emoji emoji-id=\"5456140674028019486\">⚡️</tg-emoji> <b>Telegram Premium</b> — unlock the full messenger\n<tg-emoji emoji-id=\"5422545633112249830\">🍎</tg-emoji> <b>Apple ID top-up</b> — top up App Store in any region\n<tg-emoji emoji-id=\"5319247469165433798\">🎮</tg-emoji> <b>Steam top-up</b> — Steam wallet without a VPN\n<tg-emoji emoji-id=\"5226639745106330551\">🧠</tg-emoji> <b>Claude Pro/Max</b> <i>(soon)</i> — the most powerful AI assistant for work and creativity</blockquote>\n\nPick what interests you:",
    'shop.claude_coming_soon': "💪 Working hard to bring this online sooner",
    'shop.apple_title': "🍎 <b>Apple ID top-up</b>\n\nSelect your Apple ID region:\n\n<blockquote>Not sure of your region?\nOpen <b>Settings → Apple ID → Media & Purchases → View</b> — country/region is shown there.</blockquote>",
    'shop.apple_amount_title': "🍎 <b>Apple ID top-up</b>\n\nRegion: {region}\n\nChoose top-up amount:",
    'shop.apple_confirm': "🍎 <b>Apple ID top-up</b>\n\nRegion: {region}\nAmount: {nominal}\nTo pay: <b>{price:.2f} ₽</b>\n\n<blockquote>The top-up code will be sent to this chat within 15 minutes of payment.</blockquote>",
    'shop.apple_success': "✅ <b>Payment successful!</b>\n\n🍎 Product: Apple ID top-up\n🌍 Region: {region}\n💰 Amount: {nominal}\n💳 Total: {price} ₽\n\n⏳ Expect the top-up code within <b>5–15 minutes</b>.\n\nIf you didn't get the code, message us:",
    'shop.apple_admin': "🍎 <b>APPLE ID PURCHASE</b>\n\n👤 Покупатель: <code>{buyer_id}</code>\n📛 Username: {buyer_username}\n🌍 Регион: {region}\n💰 Номинал: {nominal}\n💳 Сумма: {price} ₽\n🕐 Дата: {date}\n\n💬 <i>Отправьте код через «Написать пользователю»</i>",
    'shop.steam_main_button': "🎮 Top up Steam",
    'shop.steam_disclaimer': (
        "🎮 <b>Steam top-up</b>\n\n"
        "This top-up is available only for Russia and CIS countries:\n"
        "🇷🇺 Russian Federation\n"
        "🇦🇲 Republic of Armenia\n"
        "🇧🇾 Republic of Belarus\n"
        "🇰🇿 Republic of Kazakhstan\n"
        "🇰🇬 Kyrgyz Republic\n"
        "🇲🇩 Republic of Moldova\n"
        "🇹🇯 Republic of Tajikistan\n"
        "🇹🇲 Turkmenistan\n"
        "🇺🇿 Republic of Uzbekistan\n\n"
        "<blockquote>Conversion follows Steam's internal rate. "
        "We don't use a currency calculator so as not to mislead you.</blockquote>"
    ),
    'shop.steam_disclaimer_ack_btn': "✅ Got it",
    'shop.steam_amount_title': (
        "🎮 <b>Steam top-up</b>\n\n"
        "Choose top-up amount (₽):"
    ),
    'shop.steam_login_prompt': (
        "🎮 <b>Steam top-up</b>\n\n"
        "Top-up amount: <b>{amount} ₽</b>\n\n"
        "Enter your Steam <b>login</b>:"
    ),
    'shop.steam_invalid_login': (
        "❌ Invalid Steam login.\n\n"
        "Login must be 3–32 characters: Latin letters, digits, "
        "hyphens and underscores. Try again."
    ),
    'shop.steam_choose_payment': (
        "🎮 <b>Steam top-up</b>\n\n"
        "📥 To your Steam account: <b>{amount} ₽</b>\n"
        "👤 Steam login: <code>{login}</code>\n"
        "💳 To pay: <b>{price} ₽</b> <i>(including service fee {fee} ₽)</i>\n\n"
        "Choose payment method:"
    ),
    'shop.steam_success': (
        "✅ <b>Payment successful!</b>\n\n"
        "🎮 Product: Steam top-up\n"
        "👤 Login: <code>{login}</code>\n"
        "💰 Top-up amount: {amount} ₽\n"
        "💳 Payment amount: {price} ₽\n\n"
        "⏳ Expect the funds on your Steam account within <b>5–15 minutes</b>.\n\n"
        "If the funds don't arrive, message us:"
    ),
    'shop.steam_admin': (
        "🎮 <b>STEAM PURCHASE</b>\n\n"
        "👤 Покупатель: <code>{buyer_id}</code>\n"
        "📛 Username: {buyer_username}\n"
        "🎮 Логин Steam: <code>{login}</code>\n"
        "💰 Сумма пополнения: {amount} ₽\n"
        "💳 Сумма оплаты: {price} ₽\n"
        "🕐 Дата: {date}\n\n"
        "💬 <i>Пополните Steam-аккаунт и подтвердите пользователю через «Написать пользователю»</i>"
    ),

    # traffic (missing)
    'traffic.bypass_provisioning': "⏳ Setting up whitelist bypass...\nTap 🔄 in a few seconds.",
    'traffic.notify_8gb': "<tg-emoji emoji-id=\"5456140674028019486\">💡</tg-emoji> {remaining} of bypass traffic left.\n\nYou can top up in advance — traffic doesn't expire.",
    'traffic.notify_5gb': "<tg-emoji emoji-id=\"5190806721286657692\">📉</tg-emoji> {remaining} of bypass traffic left.\n\nWe recommend topping up so bypass keeps working.",

    # trial (missing)
    'trial.activated_btn_support': "💬 Need help",
    'trial.bypass_activated': (
        "🛡 <b>Whitelist bypass connected</b>\n\n"
        "We've gifted you <b>500 MB of bypass traffic</b> — with it "
        "sites and services filtered by whitelists open up.\n\n"
        "To start using it — install the key in Happ or Incy "
        "by tapping the button below."
    ),
    'trial.bypass_activated_btn_setup': "🌐 Enable bypass",
    'trial.bypass_activated_btn_help': "💬 Need help",
    'trial.expired_discount_btn': "🔥 Buy with 30% off",
    'trial.reminder_24h_gb': "⏳ <b>Trial ends tomorrow</b>\n\nTomorrow the main servers turn off. Bypass keeps working on your GB.\n\nStart your subscription to keep the main servers — your key and settings stay.",
    'trial.reminder_3h_gb': "🔥 <b>3 hours left of the trial</b>\n\nThen the main servers turn off; bypass keeps working on your GB.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Don't miss out — <b>15% off</b> valid until {deadline}.",
    'trial.notification_71h_gb': "🚨 Last hour of the trial\n\nIn one hour the main servers turn off. Bypass keeps working on your GB.\n\nSubscribe to keep the main servers.",
    'trial.reminder_24h': "⏳ <b>Trial ends tomorrow</b>\n\nTomorrow access will turn off — sites and apps will return to blocks.\n\nStart your subscription now — your key and settings stay, nothing to reinstall.",
    'trial.reminder_3h': "🔥 <b>3 hours until shutdown</b>\n\nAfter that VPN will stop working. Sites, streaming, messengers — all back to blocks.\n\n<tg-emoji emoji-id=\"5449800250032143374\">🎁</tg-emoji> Don't miss out — <b>15% off</b> valid until {deadline}.",
    'trial.reminder_3h_discount_btn': "🔥 Buy with 15% off",

    # admin notif (kept in RU — admin-facing per instructions to leave admin RU alone,
    # but ru.py has these keys so we need them present in en.py. Keeping literal RU as fallback.)

    # === Batch 2: hardcoded strings extracted from handlers ===

    # common buttons / toasts
    'common.support_short': "💬 Support",
    'common.help_button': "💬 Help",
    'common.back_arrow': "🔙 Back",

    # main menu / settings

    # main menu — main buttons
    'main.btn_connect_short': "⚡️ Connect",
    'main.btn_need_help': "💬 Need help",
    'main.btn_buy_vpn': "Buy VPN",
    'main.btn_renew_vpn': "Renew VPN",
    'main.btn_buy_gb': "Top up bypass GB",
    'main.btn_trial_free': "Try free — 3 days",
    'main.btn_renew_discount_15': "Renew with 15% off | ⏳ {remaining}",
    'main.btn_bypass_only': "🌐 Bypass only",
    'main.btn_my_subscription': "My subscription",
    'main.btn_invite_friends': "Invite friends",
    'main.btn_my_profile': "My profile",
    'main.btn_shop': "Shop",
    'main.btn_games': "Games",
    'main.btn_help': "Help",
    'main.btn_devices': "My devices",
    'main.btn_topup_balance': "Top up balance",
    'main.btn_auto_renew_on': "🔁 Auto-renew from balance ✅",
    'main.btn_auto_renew_off': "🔁 Auto-renew from balance",
    'main.btn_change_language_full': "Change language / Сменить язык",
    'main.btn_legal': "Legal",

    # shop buttons
    'shop.premium_button': "⚡️ Telegram Premium",
    'shop.apple_id_button': "🍎 Top up Apple ID",
    'shop.steam_top_up_button': "🎮 Top up Steam",
    'shop.spotify_button': "🎧 Spotify Premium",
    'shop.claude_coming_soon_button': "🧠 Claude Pro/Max (soon)",
    'shop.mt_proxy_button': "Buy Telegram MT Proxy",

    # setup buttons (auto-install)
    'setup.install_incy_btn': "📲 Download Incy",
    'setup.install_happ_btn': "📲 Install Happ",
    'setup.download_happ_btn': "📲 Download Happ",
    'setup.happ_vpn_label': "Happ VPN",
    'setup.incy_vpn_label': "Incy VPN",
    'setup.happ_bypass_label': "Happ Bypass",
    'setup.incy_bypass_label': "Incy Bypass",
    'setup.download_incy': "📲 Download Incy",
    'setup.install_karing_btn': "📲 Download Karing",
    'setup.karing_vpn_label': "Karing VPN",
    'setup.karing_bypass_label': "Karing Bypass",
    'setup.btn_add_karing': "🔷 Add key to Karing",
    'setup.other_clients_btn': "🧩 Other clients",
    'setup.other_title': "🧩 <b>Other clients</b>\n\nThe keys below work in v2RayTun, Karing, Stash, Clash Verge (Clash Meta / Mihomo) and any other app that can add a subscription by link.",
    'setup.other_clients_header': "<b>For your device:</b>",
    'setup.other_about_v2raytun': "• <a href=\"{url}\">v2RayTun</a> — a simple, lightweight client",
    'setup.other_about_karing': "• <a href=\"{url}\">Karing</a> — Clash, sing-box and V2Ray in one app",
    'setup.other_about_stash': "• <a href=\"{url}\">Stash</a> — a Clash-based client for iPhone and iPad",
    'setup.other_about_clash': "• <a href=\"{url}\">Clash Verge</a> — a Clash Meta (Mihomo) client for computers",
    'setup.other_howto': "<b>How to add a key</b>\n<blockquote>1. Tap a key below to copy it\n2. In the app, tap «+», «Add subscription» or «Import from clipboard»\n3. Paste the link and update the subscription\n4. Pick a server and connect</blockquote>\nOr tap a button with the app's name and the key is added automatically.",
    'setup.other_key_premium': "🔑 <b>Premium</b> — main VPN, unlimited servers:",
    'setup.other_key_bypass': "🌐 <b>Bypass</b> — gets around whitelists and blocks, uses GB:",
    'setup.other_only_bypass_note': "<i>You only have a bypass key right now. The Premium key appears once you subscribe.</i>",
    'setup.other_only_premium_note': "<i>The bypass key appears once bypass traffic is available.</i>",
    'setup.other_no_keys': "<i>No keys yet. Subscribe and they will appear here.</i>",
    'setup.other_btn_premium': "{client} · Premium",
    'setup.other_btn_bypass': "{client} · Bypass",
    'setup.other_copy_premium': "📋 Copy Premium",
    'setup.other_copy_bypass': "📋 Copy Bypass",
    'setup.other_profile_premium': "Atlas Secure",
    'setup.other_profile_bypass': "Atlas Secure Bypass",
    'setup.key_hint_press': "One-tap key install 👇",

    # errors — payment / general
    'errors.card_payment_unavailable': "Card payment is temporarily unavailable",
    'errors.sbp_unavailable_toast': "SBP is temporarily unavailable",
    'errors.min_card_amount': "Amount below minimum for card payment (64 ₽)",
    'errors.payment_creation': "Failed to create payment",
    'errors.sbp_creation': "Failed to create SBP payment",
    'errors.special_offer_expired': "⏰ Special offer has expired. You can still buy a subscription at the regular price.",

    # main — 15% auto-discount notification
    'main.discount_applied_choose_tariff': "🎁 15% discount applied — valid until {deadline}.\n\nChoose a plan:",
    # the user already has a bigger discount — it stays (the −15 % did not replace it)
    'main.discount_bigger_kept': "🎁 You already have a {percent}% discount — it is bigger, so it stays and applies at checkout.\n\nChoose a plan:",

    # combo flow
    'combo.promo_period_prompt': "\n\n🎁 Promo code: {discount_pct}% off\nChoose period:",
    'combo.choose_period_prompt': "\n\nChoose period:",

    # traffic / bypass buttons
    'traffic.btn_more_volume': "More volume →",
    'traffic.promo_active_line': "\n\n🎁 Promo discount {discount_pct}% active!",
    'traffic.refresh_btn': "🔄 Refresh",

    # buy screen
    'buy.combo_button': "🚀 Combo (VPN + bypass)",
    'buy.have_promo_button': "I have a promo code",
    'buy.select_tariff_new': '<tg-emoji emoji-id="5427168083074628963">💎</tg-emoji> <b>Choose a plan</b>\n\n{tariffs}\n\n<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji> <b>Combo</b> — VPN + bypass in one pack\n<blockquote>Bypass traffic included · from 329 ₽/mo</blockquote>',
    'buy.select_tariff_bypass_active': "🌐 <b>Bypass is active for you</b>\n\nChoose a plan for the main subscription:\n\n{tariffs}\n\n<tg-emoji emoji-id=\"5445284980978621387\">🚀</tg-emoji> <b>Combo</b> — VPN + bypass in one pack\n<blockquote>Bypass traffic included · from 329 ₽/mo</blockquote>",

    # profile screen (personal cabinet)
    'profile.info_active_until': "📆 Subscription: active until {date}",
    'profile.info_tariff': "⭐️ Plan: {tariff}",
    'profile.info_inactive': "📆 Subscription: inactive",
    'profile.info_tariff_none': "⭐️ Plan: —",
    'profile.info_bypass_none': "💎 Traffic: —",
    'profile.info_bypass_left': "💎 Traffic left: {remaining} of {limit}",
    'profile.info_auto_renew_on': "🔁 Auto-renewal: on",
    'profile.info_auto_renew_none': "🔁 Auto-renewal: —",
    'profile.info_balance': "💰 Balance: {balance} ₽",
    'profile.info_invited_friends': "👥 Friends invited: {count}",

    # my subscription screen
    'main.my_sub_title': "<b>Subscription info</b>",
    'main.my_sub_active_until': "Active until: {date}",
    'main.my_sub_active_until_none': "Active until: —",
    'main.my_sub_bypass_none': "Traffic: —",
    'main.my_sub_bypass_left': "Traffic left: {remaining} of {limit}",
    'main.my_sub_bypass_unlimited': "Traffic: unlimited",
    'main.my_sub_btn_connect': "Connect VPN",
    'main.my_sub_btn_renew': "Renew subscription",
    'main.my_sub_btn_buy_vpn': "Buy VPN",
    'main.my_sub_btn_topup_gb': "Top up bypass GB",
    'main.my_sub_btn_my_proxy': "My proxy",
    'main.my_sub_btn_mt_proxy': "Telegram MT Proxy",

    # legal
    'main.legal_title': "📰 <b>Legal documents</b>\n\nChoose a document to read:",
    'main.legal_terms_btn': "Terms of Service",
    'main.legal_privacy_btn': "Privacy Policy",

    # help / faq screen buttons
    'help.faq_button': "📖 Frequently asked questions",
    'help.instructions_button': "📲 Service guides",
    'help.contacts_button': "📞 Contacts",

    # referral screen

    # Invoice descriptions & minor payment strings
    'buy.period_text_1': "1 month",
    'buy.period_text_2_4': "{months} months",
    'buy.period_text_5_plus': "{months} months",
    'trial.degradation_notice': "\n\n⏳ Slight delays possible",

    # Plus→Basic downgrade confirmation
    'buy.downgrade_confirm_text': "⚠️ You're switching from Plus to Basic.\n\nYour key will be rotated from the dedicated server to the basic one.\n\nConfirm the switch?",
    'buy.downgrade_confirm_yes': "⚡️ Yes, switch to Basic",
    'buy.downgrade_confirm_no': "❌ Cancel",

    # global discount notice
    'buy.global_discount_default_reason': "Special prices",
    'buy.global_discount_notice': "\n\n🎁 <b>{pct}% off</b> · {reason}",
    'buy.global_discount_notice_dated': "\n\n🎁 <b>{pct}% off</b> · {reason} · until {date}",

    # start.py — site link + promo/gift link errors
    'promo_link.not_found': "⚠️ <b>Link not found</b>\n\nIt may have been removed, or the address was entered incorrectly.",
    'promo_link.activation_failed': "⚠️ <b>Couldn't activate the link</b>\n\nTry again in a moment.",
    'promo_link.error_inactive': "🚫 <b>Link disabled</b>\n\nAn admin deactivated it.",
    'promo_link.error_expired': "⏳ <b>Link has expired</b>",
    'promo_link.error_exhausted': "🚫 <b>Link fully used</b>\n\nActivation limit reached.",
    'promo_link.error_already_redeemed_by_user': "ℹ️ <b>You've already used this link</b>\n\nOne activation per user.",
    'promo_link.error_not_found': "⚠️ <b>Link not found</b>",
    'promo_link.error_db_not_ready': "⚠️ <b>Service restarting</b>\n\nTry again in a minute.",
    'promo_link.error_generic': "⚠️ <b>Activation failed</b>",
    'promo_link.reward_not_applied': "⚠️ <b>Reward hasn't been applied yet</b>\n\nTry again in a minute or contact support — we'll deliver it.",
    'promo_link.header_activated': "🎉 <b>Reward activated!</b>\n\n",
    'promo_link.reward_subscription': "📦 <b>Subscription</b> · {tariff}\n⏳ <b>{days} days</b>\n📅 Until: <b>{end}</b>",
    'promo_link.reward_discount_subscription': "🎁 <b>Your gift is activated</b>\n\n<blockquote>— <b>{percent}%</b> off any plan\n— Valid for <b>{hours} more hours</b></blockquote>\n\nPick a plan below ↓",
    'promo_link.reward_discount_traffic': "🎁 <b>Your gift is activated</b>\n\n<blockquote>— <b>{percent}%</b> off bypass GB packs\n— Valid for <b>{hours} more hours</b></blockquote>\n\nPick a plan below ↓",
    'promo_link.reward_bypass_gb': "📊 <b>+{gb} GB</b> bypass traffic added\n\nThe GB pack doesn't expire — it's spent only on LTE servers.",
    'promo_link.fallback_success_hint': "Open «Buy subscription» — the discount will apply automatically.",

    # stage-only user picker
    'stage.user_role_prompt': "Hi 👋\n\nAre you an Atlas Secure developer or a user?\nPick an option below 👇",
    'stage.role_user_btn': "👤 User",
    'stage.role_dev_btn': "💻 Developer",
    # --- Sales funnel (docs/audit/SCOPE.md «Воронка продаж», app/services/sales_funnel) ---
    'funnel.start_1h': "👋 <b>You are one step away from Atlas Secure</b>\n\nTurn on the <b>3-day free trial</b> — it is free, nothing to pay.\n\nYouTube, Instagram, Telegram and your favourite sites — without blocks and slowdowns again 🚀",
    'funnel.start_1d': "🛡 <b>What Atlas Secure gives you</b>\n\n<blockquote>🚀 Up to 25 Gbit/s — 4K video without buffering\n🌐 Whitelist bypass — works even when mobile internet is restricted\n👨‍👩‍👧‍👦 Several devices on one subscription\n➕ One-tap setup</blockquote>\n\nSee for yourself: <b>3 days free</b>, no payment.",
    'funnel.start_3d': "🎁 <b>Two ways to start</b>\n\n1️⃣ <b>Free</b> — a 3-day trial.\n2️⃣ <b>A subscription right away with {percent}% off</b> — valid until <b>{deadline}</b>.\n\nThe discount is already on your account and applies automatically at checkout.",
    'funnel.start_7d': "🔥 <b>{percent}% off your first month</b>\n\nWe kept a <b>{percent}%</b> discount on Atlas Secure for you — the first month costs noticeably less, and setup takes a minute.\n\n⏰ Valid until <b>{deadline}</b>, applied automatically.",
    'funnel.start_30d': "💎 <b>{percent}% off — just for you</b>\n\nYou have not tried Atlas Secure yet. Now is the time: <b>{percent}% off</b> any subscription until <b>{deadline}</b>.\n\nNot ready to pay — start with the free trial.",
    'funnel.trial_1d': "⏳ <b>Your {percent}% discount is still active</b>\n\nAfter the trial you got <b>{percent}% off</b> a subscription. {days_left} left — until <b>{deadline}</b>.\n\nIt applies automatically at checkout.",
    'funnel.trial_6d': "⏰ <b>Your {percent}% discount ends soon</b>\n\nIt is valid until <b>{deadline}</b> — then the price goes back to normal.\n\nSubscribe now and enjoy the internet without blocks again.",
    'funnel.trial_14d': "👋 <b>Come back to Atlas Secure — {percent}% off</b>\n\nMissing fast internet without blocks? Here is <b>{percent}% off</b> any subscription.\n\n⏰ Valid until <b>{deadline}</b>, applied automatically.",
    'funnel.trial_30d': "💎 <b>{percent}% off Atlas Secure</b>\n\nA month ago you tried Atlas Secure. Come back with <b>{percent}% off</b> — valid until <b>{deadline}</b>.\n\nFast servers, whitelist bypass and one-tap setup — all still here.",
    'funnel.trial_90d': "🎁 <b>Our best discount — {percent}%</b>\n\nAtlas Secure has never been this cheap: <b>{percent}% off</b> any subscription until <b>{deadline}</b>.\n\nCome back — we will be glad to see you 🤍",
    'funnel.paid_6h': "🔌 <b>Your Atlas Secure subscription has ended</b>\n\nRenew it — the VPN works again right after payment.\n\n🎁 You have <b>{percent}% off</b> the renewal — until <b>{deadline}</b>.",
    'funnel.paid_1d': "⏳ <b>{percent}% off the renewal — {days_left} left</b>\n\nValid until <b>{deadline}</b>, applied automatically at checkout.\n\nRenew and enjoy the internet without blocks again.",
    'funnel.paid_3d': "🔥 <b>{percent}% off the renewal</b>\n\nCome back to Atlas Secure with <b>{percent}% off</b> any plan and period. Valid until <b>{deadline}</b>.",
    'funnel.paid_7d': "💎 <b>{percent}% off renewing Atlas Secure</b>\n\nA week without fast internet is enough 🙂 Renew with <b>{percent}% off</b> until <b>{deadline}</b>.",
    'funnel.paid_30d': "👋 <b>We miss you! {percent}% off to come back</b>\n\n<b>{percent}% off</b> any Atlas Secure subscription until <b>{deadline}</b>. Setup takes a minute.",
    'funnel.paid_90d': "🎁 <b>Our best discount — {percent}%</b>\n\n<b>{percent}% off</b> any Atlas Secure subscription until <b>{deadline}</b>.\n\nCome back — we will be glad to see you 🤍",
    'funnel.btn_trial': "🎁 Try for free",
    'funnel.btn_buy_discount': "🔥 Buy with discount",
    'funnel.btn_renew_discount': "🔄 Renew with discount",
    'funnel.discount_active_note': "🎁 Your <b>{percent}%</b> discount is already in the prices — valid until <b>{deadline}</b>.",
    'funnel.discount_expired_note': "⏰ The discount has expired — regular prices apply now.",
    'funnel.no_deadline': "no end date",
    'funnel.days_one': "{n} day",
    'funnel.days_few': "{n} days",
    'funnel.days_many': "{n} days",
}