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
    "1": {"months": 1, "price": 149},
    "3": {"months": 3, "price": 399},
    "6": {"months": 6, "price": 599},
    "12": {"months": 12, "price": 899},
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

# Файл с VPN-ключами (DEPRECATED - больше не используется, ключи создаются через Xray API)
VPN_KEYS_FILE = "vpn_keys.txt"

# Telegram Payments provider token (получить через BotFather после подключения ЮKassa)
TG_PROVIDER_TOKEN = os.getenv("TG_PROVIDER_TOKEN", "")

# Xray Core API Configuration (OPTIONAL - бот работает без VPN API, но VPN-операции блокируются)
XRAY_API_URL = os.getenv("XRAY_API_URL", "")
XRAY_API_KEY = os.getenv("XRAY_API_KEY", "")

# Флаг доступности VPN API
VPN_ENABLED = bool(XRAY_API_URL and XRAY_API_KEY)

if not VPN_ENABLED:
    print("WARNING: XRAY_API_URL or XRAY_API_KEY is not set!", file=sys.stderr)
    print("WARNING: VPN operations will be BLOCKED until XRAY_API_URL and XRAY_API_KEY are configured", file=sys.stderr)
    print("WARNING: Bot will continue running, but subscriptions cannot be activated", file=sys.stderr)
else:
    print("INFO: VPN API configured successfully (VLESS + REALITY)", file=sys.stderr)

# Xray VLESS REALITY Server Constants (REQUIRED)
# Эти параметры используются для генерации VLESS ссылок
XRAY_SERVER_IP = os.getenv("XRAY_SERVER_IP", "172.86.67.9")
XRAY_PORT = int(os.getenv("XRAY_PORT", "443"))
XRAY_SNI = os.getenv("XRAY_SNI", "www.cloudflare.com")
XRAY_PUBLIC_KEY = os.getenv("XRAY_PUBLIC_KEY", "fDixPEehAKSEsRGm5Q9HY-BNs9uMmN5NIzEDKngDOk8")
XRAY_SHORT_ID = os.getenv("XRAY_SHORT_ID", "a1b2c3d4")
# XRAY_FLOW удалён: параметр flow ЗАПРЕЩЁН для REALITY протокола
# VLESS с REALITY не использует flow параметр
XRAY_FP = os.getenv("XRAY_FP", "ios")  # По умолчанию ios согласно требованиям

