import os
import sys

print("🚨 STAGE CONFIG LOADED 🚨", flush=True)
print("APP_ENV =", os.getenv("APP_ENV"), flush=True)
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

# Тарифы Basic и Plus с периодами
# Структура: tariff_type -> period_days -> price
TARIFFS = {
    "basic": {
        30: {"price": 149},      # 1 месяц
        90: {"price": 399},      # 3 месяца
        180: {"price": 749},     # 6 месяцев
        365: {"price": 1399},    # 12 месяцев
    },
    "plus": {
        30: {"price": 299},      # 1 месяц
        90: {"price": 699},      # 3 месяца
        180: {"price": 1199},    # 6 месяцев
        365: {"price": 2299},    # 12 месяцев
    }
}

# Суммы пополнения баланса (в рублях)
BALANCE_TOPUP_AMOUNTS = [250, 750, 999]

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

# Crypto Bot (Telegram Crypto Pay) Configuration
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "")
CRYPTOBOT_API_URL = os.getenv("CRYPTOBOT_API_URL", "https://pay.crypt.bot/api")
CRYPTOBOT_WEBHOOK_SECRET = os.getenv("CRYPTOBOT_WEBHOOK_SECRET", "")

