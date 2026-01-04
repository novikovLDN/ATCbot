import os
import sys

# Telegram Bot Token (получить у @BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN environment variable is not set!", file=sys.stderr)
    sys.exit(1)

# Telegram ID администратора (можно узнать у @userinfobot)
ADMIN_TELEGRAM_ID_STR = os.getenv("ADMIN_TELEGRAM_ID")
if not ADMIN_TELEGRAM_ID_STR:
    print("ERROR: ADMIN_TELEGRAM_ID environment variable is not set!", file=sys.stderr)
    sys.exit(1)

try:
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID_STR)
except ValueError:
    print(f"ERROR: ADMIN_TELEGRAM_ID must be a number, got: {ADMIN_TELEGRAM_ID_STR}", file=sys.stderr)
    sys.exit(1)

# Тарифы (в месяцах и их стоимость в рублях)
TARIFFS = {
    "1": {"months": 1, "price": 299},
    "3": {"months": 3, "price": 799},
    "6": {"months": 6, "price": 1199},
    "12": {"months": 12, "price": 1699},
}

# Реквизиты СБП (для оплаты)
SBP_DETAILS = {
    "bank": "Банк",
    "account": "12345678901234567890",
    "name": "ИП Иванов Иван Иванович",
}

# Поддержка
SUPPORT_EMAIL = "support@example.com"
SUPPORT_TELEGRAM = "@support"

# Файл с VPN-ключами
VPN_KEYS_FILE = "vpn_keys.txt"

# Telegram Payments provider token (получить через BotFather после подключения ЮKassa)
TG_PROVIDER_TOKEN = os.getenv("TG_PROVIDER_TOKEN", "")

# Outline Management API URL (получить из Outline Manager)
OUTLINE_API_URL = os.getenv("OUTLINE_API_URL", "")

