import os
import sys

# ====================================================================================
# ENVIRONMENT CONFIGURATION: Изоляция PROD / STAGE / LOCAL через префиксы
# ====================================================================================
# ВАЖНО: Все переменные окружения должны использовать префикс окружения:
#   - PROD: PROD_BOT_TOKEN, PROD_DATABASE_URL, PROD_ADMIN_TELEGRAM_ID
#   - STAGE: STAGE_BOT_TOKEN, STAGE_DATABASE_URL, STAGE_ADMIN_TELEGRAM_ID
#   - LOCAL: LOCAL_BOT_TOKEN, LOCAL_DATABASE_URL, LOCAL_ADMIN_TELEGRAM_ID
# 
# Это гарантирует полную изоляцию окружений и предотвращает случайное
# использование неправильных переменных (например, STAGE бот не сможет
# использовать PROD_BOT_TOKEN даже если он случайно задан).
# ====================================================================================

APP_ENV = os.getenv("APP_ENV", "prod").lower()
if APP_ENV not in ("prod", "stage", "local"):
    print(f"ERROR: Invalid APP_ENV={APP_ENV}. Must be one of: prod, stage, local", file=sys.stderr)
    sys.exit(1)

# Флаги окружения для архитектурного разделения поведения
IS_LOCAL = APP_ENV == "local"
IS_STAGE = APP_ENV == "stage"
IS_PROD = APP_ENV == "prod"

def env(key: str, default: str = "") -> str:
    """
    Получить переменную окружения с префиксом окружения
    
    Args:
        key: Имя переменной без префикса (например, "BOT_TOKEN")
        default: Значение по умолчанию, если переменная не задана
    
    Returns:
        Значение переменной с префиксом (например, "STAGE_BOT_TOKEN")
    
    Example:
        env("BOT_TOKEN") -> "STAGE_BOT_TOKEN" (если APP_ENV=stage)
        env("DATABASE_URL") -> "PROD_DATABASE_URL" (если APP_ENV=prod)
        env("CHAOS_ENABLED", default="false") -> "false" если не задано
    """
    env_key = f"{APP_ENV.upper()}_{key}"
    return os.getenv(env_key, default)

# Защита от прямого использования переменных без префикса
# Это предотвращает случайное использование неправильных переменных
_direct_usage_vars = ["BOT_TOKEN", "DATABASE_URL", "ADMIN_TELEGRAM_ID", "TG_PROVIDER_TOKEN"]
for var in _direct_usage_vars:
    if os.getenv(var):
        print(f"ERROR: Direct usage of {var} is FORBIDDEN!", file=sys.stderr)
        print(f"ERROR: Use {APP_ENV.upper()}_{var} instead (via env('{var}'))", file=sys.stderr)
        print(f"ERROR: This prevents accidental PROD/STAGE configuration mix-up", file=sys.stderr)
        sys.exit(1)

print(f"INFO: Config loaded for environment: {APP_ENV.upper()}", flush=True)

# ====================================================================================
# STEP 4 — PART E: SECRET & CONFIG SAFETY
# ====================================================================================
# Secrets are validated at startup and never logged.
# Required secrets: BOT_TOKEN, ADMIN_TELEGRAM_ID, DATABASE_URL
# Optional secrets: TG_PROVIDER_TOKEN, XRAY_API_KEY, PLATEGA_SECRET (via env prefix)
# ====================================================================================

# Telegram Bot Token (получить у @BotFather)
BOT_TOKEN = env("BOT_TOKEN")
if not BOT_TOKEN:
    print(f"ERROR: {APP_ENV.upper()}_BOT_TOKEN environment variable is not set!", file=sys.stderr)
    sys.exit(1)
print(f"INFO: Using BOT_TOKEN from {APP_ENV.upper()}_BOT_TOKEN", flush=True)

# Telegram ID администратора (можно узнать у @userinfobot)
ADMIN_TELEGRAM_ID_STR = env("ADMIN_TELEGRAM_ID")
if not ADMIN_TELEGRAM_ID_STR:
    print(f"ERROR: {APP_ENV.upper()}_ADMIN_TELEGRAM_ID environment variable is not set!", file=sys.stderr)
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

# Тарифы для оплаты Telegram Stars (цены в Stars, +70% от рублёвых)
# 1 Star ≈ 1.85 RUB (курс приблизительный, цены округлены)
TARIFFS_STARS = {
    "basic": {
        30: {"price": 140},      # 149₽ × 1.7 / 1.85 ≈ 137 → 140⭐
        90: {"price": 370},      # 399₽ × 1.7 / 1.85 ≈ 367 → 370⭐
        180: {"price": 690},     # 749₽ × 1.7 / 1.85 ≈ 688 → 690⭐
        365: {"price": 1290},    # 1399₽ × 1.7 / 1.85 ≈ 1285 → 1290⭐
    },
    "plus": {
        30: {"price": 275},      # 299₽ × 1.7 / 1.85 ≈ 275⭐
        90: {"price": 645},      # 699₽ × 1.7 / 1.85 ≈ 642 → 645⭐
        180: {"price": 1100},    # 1199₽ × 1.7 / 1.85 ≈ 1102 → 1100⭐
        365: {"price": 2115},    # 2299₽ × 1.7 / 1.85 ≈ 2113 → 2115⭐
    }
}

# Время жизни инвойса (в секундах). После истечения инвойс удаляется.
INVOICE_TIMEOUT_SECONDS = 900  # 15 минут

# Суммы пополнения баланса (в рублях)
BALANCE_TOPUP_AMOUNTS = [250, 750, 999]

# Суммы пополнения баланса (в Stars, +70% от рублёвых)
BALANCE_TOPUP_AMOUNTS_STARS = [230, 690, 920]

# Реквизиты СБП (для оплаты)
SBP_DETAILS = {
    "bank": "Банк",
    "account": "12345678901234567890",
    "name": "ИП Иванов Иван Иванович",
}

# Поддержка
SUPPORT_EMAIL = "support@example.com"
SUPPORT_TELEGRAM = "@support"

# Telegram Payments provider token (получить через BotFather после подключения ЮKassa)
# В PROD: ОБЯЗАТЕЛЕН (иначе платежи не работают)
# В STAGE: опционален (платежи могут быть отключены)
TG_PROVIDER_TOKEN = env("TG_PROVIDER_TOKEN")
if not TG_PROVIDER_TOKEN:
    if APP_ENV == "prod":
        print(f"ERROR: {APP_ENV.upper()}_TG_PROVIDER_TOKEN is REQUIRED in PROD!", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"WARNING: {APP_ENV.upper()}_TG_PROVIDER_TOKEN is not set - payments will be disabled", file=sys.stderr)

# Xray Core API Configuration (OPTIONAL - бот работает без VPN API, но VPN-операции блокируются)
XRAY_API_URL = env("XRAY_API_URL")
XRAY_API_KEY = env("XRAY_API_KEY")
# Timeout для XRAY API запросов (в секундах, default 5s)
XRAY_API_TIMEOUT = float(env("XRAY_API_TIMEOUT", default="5.0"))

# Optional: public URL of VPN server (e.g. for future subscription link features).
VPN_SERVER_URL = env("VPN_SERVER_URL", default="").rstrip("/")

# Флаг доступности VPN API
VPN_ENABLED = bool(XRAY_API_URL and XRAY_API_KEY)

# Feature flag для VPN provisioning (по умолчанию true в STAGE, false если VPN_ENABLED=False)
VPN_PROVISIONING_ENABLED = env("VPN_PROVISIONING_ENABLED", default="true").lower() == "true" if VPN_ENABLED else False

if not VPN_ENABLED:
    print("INFO: ARCH_MODE: API_ONLY_VLESS_GENERATION (REALITY + XTLS Vision)", flush=True)
    print("WARNING: XRAY_API_URL or XRAY_API_KEY is not set!", file=sys.stderr)
    print("WARNING: VPN operations will be BLOCKED until XRAY_API_URL and XRAY_API_KEY are configured", file=sys.stderr)
    print("WARNING: Bot will continue running, but subscriptions cannot be activated", file=sys.stderr)
else:
    print(f"INFO: Using XRAY_API_URL from {APP_ENV.upper()}_XRAY_API_URL", flush=True)
    print(f"INFO: Using XRAY_API_KEY from {APP_ENV.upper()}_XRAY_API_KEY", flush=True)
    print(f"INFO: XRAY_API_TIMEOUT={XRAY_API_TIMEOUT}s", flush=True)
    print(f"INFO: VPN_PROVISIONING_ENABLED={VPN_PROVISIONING_ENABLED}", flush=True)
    print("INFO: VPN API configured successfully (VLESS + REALITY)", file=sys.stderr)
    print("INFO: ARCH_MODE: API_ONLY_VLESS_GENERATION (REALITY + XTLS Vision)", flush=True)

# Xray sync worker: sync DB subscriptions to Xray (default false for production safety)
XRAY_SYNC_ENABLED = env("XRAY_SYNC_ENABLED", default="false").lower() == "true"

# Bot uses ONLY XRAY_API_URL and XRAY_API_KEY.
# Port, SNI, public key, short id, fingerprint belong to API server only.
# Bot receives vless_link from API — never generates links locally.

# Platega (SBP) Configuration
# СБП оплата через Platega.io — наценка +11%
PLATEGA_MERCHANT_ID = env("PLATEGA_MERCHANT_ID") or "aa5f5d2d-162e-4dad-a58d-c6dedd89360f"
PLATEGA_SECRET = env("PLATEGA_SECRET")
PLATEGA_API_URL = env("PLATEGA_API_URL") or "https://app.platega.io"
# Процент наценки для СБП (11%)
SBP_MARKUP_PERCENT = 11

# Public base URL for webhooks (Railway + Cloudflare). Required for payment webhooks.
# Example: https://api.yourdomain.com
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="")

# Webhook configuration (MANDATORY)
# Bot uses ONLY webhook mode for receiving Telegram updates
WEBHOOK_URL = env("WEBHOOK_URL")
if not WEBHOOK_URL:
    print(f"ERROR: {APP_ENV.upper()}_WEBHOOK_URL environment variable is REQUIRED!", file=sys.stderr)
    sys.exit(1)
WEBHOOK_SECRET = env("WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    print(f"ERROR: {APP_ENV.upper()}_WEBHOOK_SECRET environment variable is REQUIRED!", file=sys.stderr)
    sys.exit(1)
WEBHOOK_PORT = int(os.getenv("PORT") or env("WEBHOOK_PORT") or "8080")
print(f"INFO: Using WEBHOOK_URL from {APP_ENV.upper()}_WEBHOOK_URL", flush=True)

# Redis for FSM storage
REDIS_URL = env("REDIS_URL", default="")

