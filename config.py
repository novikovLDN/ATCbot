import logging
import os
import sys

_log = logging.getLogger(__name__)

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

APP_ENV = os.getenv("APP_ENV", "local").lower()
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

_log.info("Config loaded for environment: %s", APP_ENV.upper())

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
_log.info("Using BOT_TOKEN from %s_BOT_TOKEN", APP_ENV.upper())

# Telegram ID администраторов (можно узнать у @userinfobot)
# Поддерживает одного или нескольких: "123456" или "123456,789012"
ADMIN_TELEGRAM_ID_STR = env("ADMIN_TELEGRAM_ID")
if not ADMIN_TELEGRAM_ID_STR:
    print(f"ERROR: {APP_ENV.upper()}_ADMIN_TELEGRAM_ID environment variable is not set!", file=sys.stderr)
    sys.exit(1)

# Parse comma-separated list of admin IDs
ADMIN_TELEGRAM_IDS: set[int] = set()
for _id_str in ADMIN_TELEGRAM_ID_STR.split(","):
    _id_str = _id_str.strip()
    if not _id_str:
        continue
    try:
        ADMIN_TELEGRAM_IDS.add(int(_id_str))
    except ValueError:
        print(f"ERROR: ADMIN_TELEGRAM_ID must be number(s), got: {_id_str}", file=sys.stderr)
        sys.exit(1)
if not ADMIN_TELEGRAM_IDS:
    print(f"ERROR: ADMIN_TELEGRAM_ID must contain at least one valid ID", file=sys.stderr)
    sys.exit(1)

# Primary admin (first listed) — used for alerts and single-admin operations
ADMIN_TELEGRAM_ID = next(iter(ADMIN_TELEGRAM_IDS))
try:
    # Keep backwards-compatible: if single ID was given, use it as primary
    ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID_STR.split(",")[0].strip())
except ValueError:
    pass

# Database URL validation
DATABASE_URL = env("DATABASE_URL")
if not DATABASE_URL:
    print(f"ERROR: {APP_ENV.upper()}_DATABASE_URL environment variable is not set!", file=sys.stderr)
    sys.exit(1)
if not DATABASE_URL.startswith(("postgres://", "postgresql://")):
    print(f"ERROR: DATABASE_URL must start with postgres:// or postgresql://", file=sys.stderr)
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
    },
    # --- Бизнес-тарифы (генерация ключей для клиентов) ---
    "biz_client_25": {
        30: {"price": 599},
        90: {"price": 1499},
        365: {"price": 4999},
    },
    "biz_client_50": {
        30: {"price": 1099},
        90: {"price": 2799},
        365: {"price": 8999},
    },
    "biz_client_100": {
        30: {"price": 1899},
        90: {"price": 4799},
        365: {"price": 15999},
    },
    "biz_client_150": {
        30: {"price": 2499},       # выгода ~10%
        90: {"price": 6299},
        365: {"price": 20999},
    },
    "biz_client_250": {
        30: {"price": 3599},       # выгода ~15%
        90: {"price": 8999},
        365: {"price": 29999},
    },
    "biz_client_500": {
        30: {"price": 5999},       # выгода ~20%
        90: {"price": 14999},
        365: {"price": 49999},
    },
}

# Все допустимые типы подписок (для валидации в БД и хендлерах)
BIZ_CLIENT_TARIFF_NAMES = ("biz_client_25", "biz_client_50", "biz_client_100", "biz_client_150", "biz_client_250", "biz_client_500")
VALID_SUBSCRIPTION_TYPES = ("basic", "plus") + BIZ_CLIENT_TARIFF_NAMES

def is_biz_tariff(tariff: str) -> bool:
    """Проверяет, является ли тариф бизнес-тарифом (клиентским)."""
    return tariff in BIZ_CLIENT_TARIFF_NAMES

def is_biz_client_tariff(tariff: str) -> bool:
    """Проверяет, является ли тариф клиентским бизнес-тарифом."""
    return tariff in BIZ_CLIENT_TARIFF_NAMES

def tariff_for_vpn_api(tariff: str) -> str:
    """Маппинг тарифа на VPN API тип (basic/plus). Бизнес → plus."""
    if tariff in BIZ_CLIENT_TARIFF_NAMES:
        return "plus"
    if tariff == "plus":
        return "plus"
    return "basic"

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
    },
    # Бизнес-тарифы Stars (price × 1.7 / 1.85, округление вверх до 5)
    "biz_client_25": {
        30: {"price": 910},       # 990 × 1.7 / 1.85 ≈ 910⭐
        90: {"price": 2290},      # 2490 × 1.7 / 1.85 ≈ 2288⭐
        365: {"price": 8260},     # 8990 × 1.7 / 1.85 ≈ 8258⭐
    },
    "biz_client_50": {
        30: {"price": 1555},      # 1690 × 1.7 / 1.85 ≈ 1553⭐
        90: {"price": 4125},      # 4490 × 1.7 / 1.85 ≈ 4125⭐
        365: {"price": 14610},    # 15900 × 1.7 / 1.85 ≈ 14608⭐
    },
    "biz_client_100": {
        30: {"price": 2475},      # 2690 × 1.7 / 1.85 ≈ 2472⭐
        90: {"price": 6425},      # 6990 × 1.7 / 1.85 ≈ 6423⭐
        365: {"price": 22870},    # 24900 × 1.7 / 1.85 ≈ 22865⭐
    },
    "biz_client_150": {
        30: {"price": 3665},      # 3990 × 1.7 / 1.85 ≈ 3665⭐
        90: {"price": 9180},      # 9990 × 1.7 / 1.85 ≈ 9180⭐
        365: {"price": 33900},    # 36900 × 1.7 / 1.85 ≈ 33900⭐
    },
    "biz_client_250": {
        30: {"price": 5045},      # 5490 × 1.7 / 1.85 ≈ 5045⭐
        90: {"price": 12775},     # 13900 × 1.7 / 1.85 ≈ 12773⭐
        365: {"price": 45850},    # 49900 × 1.7 / 1.85 ≈ 45846⭐
    },
    "biz_client_500": {
        30: {"price": 8260},      # 8990 × 1.7 / 1.85 ≈ 8258⭐
        90: {"price": 21040},     # 22900 × 1.7 / 1.85 ≈ 21038⭐
        365: {"price": 73390},    # 79900 × 1.7 / 1.85 ≈ 73389⭐
    },
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
    _log.info("ARCH_MODE: API_ONLY_VLESS_GENERATION (REALITY + XTLS Vision)")
    _log.warning("XRAY_API_URL or XRAY_API_KEY is not set!")
    _log.warning("VPN operations will be BLOCKED until XRAY_API_URL and XRAY_API_KEY are configured")
    _log.warning("Bot will continue running, but subscriptions cannot be activated")
else:
    _log.info("Using XRAY_API_URL from %s_XRAY_API_URL", APP_ENV.upper())
    _log.info("Using XRAY_API_KEY from %s_XRAY_API_KEY", APP_ENV.upper())
    _log.info("XRAY_API_TIMEOUT=%ss", XRAY_API_TIMEOUT)
    _log.info("VPN_PROVISIONING_ENABLED=%s", VPN_PROVISIONING_ENABLED)
    _log.info("VPN API configured successfully (VLESS + REALITY)")
    _log.info("ARCH_MODE: API_ONLY_VLESS_GENERATION (REALITY + XTLS Vision)")

# Xray sync worker: sync DB subscriptions to Xray (default false for production safety)
XRAY_SYNC_ENABLED = env("XRAY_SYNC_ENABLED", default="false").lower() == "true"

# Bot uses ONLY XRAY_API_URL and XRAY_API_KEY.
# Port, SNI, public key, short id, fingerprint belong to API server only.
# Bot receives vless_link from API — never generates links locally.

# Platega (SBP) Configuration
# СБП оплата через Platega.io — наценка +11%
PLATEGA_MERCHANT_ID = env("PLATEGA_MERCHANT_ID", default="")
PLATEGA_SECRET = env("PLATEGA_SECRET")
PLATEGA_API_URL = env("PLATEGA_API_URL") or "https://app.platega.io"
# Процент наценки для СБП (11%)
SBP_MARKUP_PERCENT = 11

# YooKassa (ЮKassa) Direct API Configuration
# Прямая интеграция с YooKassa для рекуррентных платежей (сохранение карт)
YOOKASSA_SHOP_ID = env("YOOKASSA_SHOP_ID", default="")
YOOKASSA_SECRET_KEY = env("YOOKASSA_SECRET_KEY", default="")
YOOKASSA_RETURN_URL = env("YOOKASSA_RETURN_URL", default="")
YOOKASSA_ENABLED = bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)
if YOOKASSA_ENABLED:
    _log.info("YooKassa direct API configured (recurring payments enabled)")
else:
    _log.info("YooKassa direct API not configured (recurring payments disabled)")

# CryptoBot (Crypto Pay) Configuration
# Криптовалютная оплата через @CryptoBot
CRYPTOBOT_API_TOKEN = env("CRYPTOBOT_API_TOKEN", default="")
CRYPTOBOT_API_URL = env("CRYPTOBOT_API_URL") or "https://pay.crypt.bot/api"

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
_log.info("Using WEBHOOK_URL from %s_WEBHOOK_URL", APP_ENV.upper())

# Telegram Mini App deep-link settings (for t.me/<bot>/<app>?startapp=...)
BOT_USERNAME = env("BOT_USERNAME", default="atlassecure_bot")
MINI_APP_NAME = env("MINI_APP_NAME", default="app")

# --- Бизнес-клиентские тарифы (генерация ключей для клиентов) ---
# Тарифы с лимитами на максимальное количество генераций клиентских ключей в день
BIZ_CLIENT_TARIFFS = {
    "biz_client_25": {
        "max_clients_per_day": 25,
        "label": "До 25 клиентов/день",
        "discount": None,
        30: {"price": 599},
        90: {"price": 1499},
        365: {"price": 4999},
    },
    "biz_client_50": {
        "max_clients_per_day": 50,
        "label": "До 50 клиентов/день",
        "discount": None,
        30: {"price": 1099},
        90: {"price": 2799},
        365: {"price": 8999},
    },
    "biz_client_100": {
        "max_clients_per_day": 100,
        "label": "До 100 клиентов/день",
        "discount": None,
        30: {"price": 1899},
        90: {"price": 4799},
        365: {"price": 15999},
    },
    "biz_client_150": {
        "max_clients_per_day": 150,
        "label": "До 150 клиентов/день",
        "discount": 10,
        30: {"price": 2499},
        90: {"price": 6299},
        365: {"price": 20999},
    },
    "biz_client_250": {
        "max_clients_per_day": 250,
        "label": "До 250 клиентов/день",
        "discount": 15,
        30: {"price": 3599},
        90: {"price": 8999},
        365: {"price": 29999},
    },
    "biz_client_500": {
        "max_clients_per_day": 500,
        "label": "До 500 клиентов/день",
        "discount": 20,
        30: {"price": 5999},
        90: {"price": 14999},
        365: {"price": 49999},
    },
}

BIZ_CLIENT_TARIFF_KEYS = tuple(BIZ_CLIENT_TARIFFS.keys())

# Лимит по умолчанию для бизнес-клиентских ключей в день
BIZ_DEFAULT_MAX_CLIENTS_PER_DAY = 25

# Redis for FSM storage
REDIS_URL = env("REDIS_URL", default="")

