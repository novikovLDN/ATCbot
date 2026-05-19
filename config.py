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
        30: {"price": 199},      # 1 месяц
        90: {"price": 499},      # 3 месяца
        180: {"price": 899},     # 6 месяцев
        365: {"price": 1599},    # 12 месяцев
    },
    "plus": {
        30: {"price": 349},      # 1 месяц
        90: {"price": 899},      # 3 месяца
        180: {"price": 1499},    # 6 месяцев
        365: {"price": 2599},    # 12 месяцев
    },
    # --- Бизнес-тарифы: выделенные VPN-серверы ---
    # 2 vCPU · 8 GB RAM · 20 TB трафик · до 5 пользователей
    "biz_starter": {
        30: {"price": 2900},     # 1 месяц
        180: {"price": 14900},   # 6 месяцев
        365: {"price": 24900},   # 12 месяцев
        730: {"price": 42900},   # 24 месяца
    },
    # 4 vCPU · 16 GB RAM · 20 TB трафик · до 15 пользователей
    "biz_team": {
        30: {"price": 5500},
        180: {"price": 28900},
        365: {"price": 48900},
        730: {"price": 84900},
    },
    # 8 vCPU · 32 GB RAM · 30 TB трафик · до 50 пользователей
    "biz_business": {
        30: {"price": 10900},
        180: {"price": 56900},
        365: {"price": 96900},
        730: {"price": 169900},
    },
    # 16 vCPU · 64 GB RAM · 40 TB трафик · до 100 пользователей
    "biz_pro": {
        30: {"price": 21500},
        180: {"price": 109900},
        365: {"price": 189900},
        730: {"price": 329900},
    },
    # 32 vCPU · 128 GB RAM · 50 TB трафик · до 250 пользователей
    "biz_enterprise": {
        30: {"price": 42900},
        180: {"price": 219900},
        365: {"price": 379900},
        730: {"price": 659900},
    },
    # 48 vCPU · 192 GB RAM · 60 TB трафик · до 500 пользователей
    "biz_ultimate": {
        30: {"price": 64900},
        180: {"price": 329900},
        365: {"price": 569900},
        730: {"price": 989900},
    },
}

# Список всех бизнес-тарифов (для проверок)
BIZ_TARIFFS = ("biz_starter", "biz_team", "biz_business", "biz_pro", "biz_enterprise", "biz_ultimate")

# Все допустимые типы подписок (для валидации в БД и хендлерах)
VALID_SUBSCRIPTION_TYPES = ("basic", "plus") + BIZ_TARIFFS

def is_biz_tariff(tariff: str) -> bool:
    """Проверяет, является ли тариф бизнес-тарифом."""
    return tariff in BIZ_TARIFFS

def tariff_for_vpn_api(tariff: str) -> str:
    """Маппинг тарифа на VPN API тип (basic/plus). Бизнес → plus."""
    if tariff in BIZ_TARIFFS:
        return "plus"
    if tariff == "plus":
        return "plus"
    return "basic"

# --- Страны для бизнес-тарифов ---
# Ценовые множители относительно базовой цены (Амстердам = 1.0)
# Основаны на реальной стоимости инфраструктуры в регионе + 10% выше конкурентов
BIZ_COUNTRIES = {
    "nl": {
        "name": "Амстердам",
        "flag": "🇳🇱",
        "multiplier": 1.0,  # Базовая цена (Hetzner NL)
    },
    "ru": {
        "name": "Россия",
        "flag": "🇷🇺",
        "multiplier": 0.90,  # Selectel/Timeweb дешевле, но +10% над рынком
    },
    "uk": {
        "name": "Великобритания",
        "flag": "🇬🇧",
        "multiplier": 1.20,  # UK дороже на ~20% (AWS/Vultr London)
    },
    "fr": {
        "name": "Франция",
        "flag": "🇫🇷",
        "multiplier": 1.05,  # OVH Франция, чуть дороже NL
    },
    "us": {
        "name": "США",
        "flag": "🇺🇸",
        "multiplier": 1.15,  # US дороже (Vultr/DO East Coast)
    },
}

# Конфигурации серверов для бизнес-тарифов (для отображения в профиле)
BIZ_TIER_SPECS = {
    "biz_starter":    {"cpu": 2,  "ram": 8,   "traffic": 20, "users": 5},
    "biz_team":       {"cpu": 4,  "ram": 16,  "traffic": 20, "users": 15},
    "biz_business":   {"cpu": 8,  "ram": 32,  "traffic": 30, "users": 50},
    "biz_pro":        {"cpu": 16, "ram": 64,  "traffic": 40, "users": 100},
    "biz_enterprise": {"cpu": 32, "ram": 128, "traffic": 50, "users": 250},
    "biz_ultimate":   {"cpu": 48, "ram": 192, "traffic": 60, "users": 500},
}

def get_biz_price(tariff: str, period_days: int, country: str = "nl") -> int:
    """Получить цену бизнес-тарифа для конкретной страны (в рублях)."""
    if tariff not in TARIFFS or period_days not in TARIFFS[tariff]:
        return 0
    base_price = TARIFFS[tariff][period_days]["price"]
    multiplier = BIZ_COUNTRIES.get(country, {}).get("multiplier", 1.0)
    return int(round(base_price * multiplier / 100) * 100)  # Округление до сотен

def get_biz_price_stars(tariff: str, period_days: int, country: str = "nl") -> int:
    """Получить цену бизнес-тарифа в Stars для конкретной страны."""
    if tariff not in TARIFFS_STARS or period_days not in TARIFFS_STARS[tariff]:
        return 0
    base_price = TARIFFS_STARS[tariff][period_days]["price"]
    multiplier = BIZ_COUNTRIES.get(country, {}).get("multiplier", 1.0)
    return int(round(base_price * multiplier))

# Тарифы для оплаты Telegram Stars (цены в Stars, +70% от рублёвых)
# 1 Star ≈ 1.85 RUB (курс приблизительный, цены округлены)
TARIFFS_STARS = {
    "basic": {
        30: {"price": 185},      # 199₽ × 1.7 / 1.85 ≈ 183 → 185⭐
        90: {"price": 460},      # 499₽ × 1.7 / 1.85 ≈ 459 → 460⭐
        180: {"price": 830},     # 899₽ × 1.7 / 1.85 ≈ 826 → 830⭐
        365: {"price": 1470},    # 1599₽ × 1.7 / 1.85 ≈ 1469 → 1470⭐
    },
    "plus": {
        30: {"price": 325},      # 349₽ × 1.7 / 1.85 ≈ 321 → 325⭐
        90: {"price": 830},      # 899₽ × 1.7 / 1.85 ≈ 826 → 830⭐
        180: {"price": 1380},    # 1499₽ × 1.7 / 1.85 ≈ 1378 → 1380⭐
        365: {"price": 2390},    # 2599₽ × 1.7 / 1.85 ≈ 2388 → 2390⭐
    },
    # Бизнес-тарифы Stars (price × 1.7 / 1.85, округление вверх)
    "biz_starter": {
        30: {"price": 2665},     # 2900 × 1.7 / 1.85 ≈ 2665⭐
        180: {"price": 13690},   # 14900 × 1.7 / 1.85 ≈ 13689⭐
        365: {"price": 22865},   # 24900 × 1.7 / 1.85 ≈ 22865⭐
        730: {"price": 39405},   # 42900 × 1.7 / 1.85 ≈ 39405⭐
    },
    "biz_team": {
        30: {"price": 5054},
        180: {"price": 26551},
        365: {"price": 44919},
        730: {"price": 78000},
    },
    "biz_business": {
        30: {"price": 10014},
        180: {"price": 52270},
        365: {"price": 89027},
        730: {"price": 156100},
    },
    "biz_pro": {
        30: {"price": 19757},
        180: {"price": 100981},
        365: {"price": 174519},
        730: {"price": 303081},
    },
    "biz_enterprise": {
        30: {"price": 39405},
        180: {"price": 202054},
        365: {"price": 349027},
        730: {"price": 606243},
    },
    "biz_ultimate": {
        30: {"price": 59627},
        180: {"price": 303081},
        365: {"price": 523581},
        730: {"price": 909297},
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

# CryptoBot (Crypto Pay) Configuration
# Криптовалютная оплата через @CryptoBot
CRYPTOBOT_API_TOKEN = env("CRYPTOBOT_API_TOKEN", default="")
CRYPTOBOT_API_URL = env("CRYPTOBOT_API_URL") or "https://pay.crypt.bot/api"

# Lava (Card) Configuration
# Оплата картой через Lava (api.lava.ru)
LAVA_WALLET_TO = env("LAVA_WALLET_TO", default="")
LAVA_JWT_TOKEN = env("LAVA_JWT_TOKEN", default="")  # Secret key (apikey in JWT payload)
LAVA_SIGN_KEY = env("LAVA_SIGN_KEY", default="")  # Additional key for JWT HMAC signing
LAVA_SHOP_ID = env("LAVA_SHOP_ID", default="")  # Project/shop ID
LAVA_API_URL = env("LAVA_API_URL") or "https://api.lava.ru"

# Site Sync API (Atlas Secure website ↔ Bot sync)
SITE_API_URL = env("SITE_API_URL", default="")  # e.g. https://qodev.dev/api/bot
SITE_BOT_API_KEY = env("SITE_BOT_API_KEY", default="")  # X-Bot-Api-Key header

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

# Mini App URL — used for WebApp buttons.
APP_URL = env("MINI_APP_URL", default="https://atlas-miniapp-production.up.railway.app").rstrip("/")

# Subscription link base URL (domain serving /api/sub/{token}?id={id}).
SUB_BASE_URL = env("SUB_BASE_URL", default="https://atlassecure.ru").rstrip("/")

# ====================================================================================
# REMNAWAVE PANEL CONFIGURATION (Bypass / Traffic limits)
# ====================================================================================
REMNAWAVE_API_URL = env("REMNAWAVE_API_URL", default="").rstrip("/")
# Task 2 TZ uses `REMNAWAVE_TOKEN` as the canonical name; we honour both
# spellings so an env-var rename isn't required to land the cut-over.
REMNAWAVE_API_TOKEN = env("REMNAWAVE_API_TOKEN", default="") or env("REMNAWAVE_TOKEN", default="")
REMNAWAVE_ENABLED = bool(REMNAWAVE_API_URL and REMNAWAVE_API_TOKEN)

if REMNAWAVE_ENABLED:
    _log.info("REMNAWAVE_ENABLED=true, API_URL=%s", REMNAWAVE_API_URL)
else:
    _log.info("REMNAWAVE_ENABLED=false (URL or TOKEN not set)")

# Traffic limits per tariff (in bytes). Trial has NO bypass.
TRAFFIC_LIMITS = {
    "basic": {
        30:  10 * 1024**3,    # 10 GB
        90:  10 * 1024**3,    # 10 GB
        180: 10 * 1024**3,    # 10 GB
        365: 10 * 1024**3,    # 10 GB
    },
    "plus": {
        30:  10 * 1024**3,    # 10 GB
        90:  10 * 1024**3,    # 10 GB
        180: 10 * 1024**3,    # 10 GB
        365: 10 * 1024**3,    # 10 GB
    },
}

# Shortcut: human-readable GB for button labels
TRAFFIC_LIMITS_GB = {
    "basic": {30: 10, 90: 10, 180: 10, 365: 10},
    "plus":  {30: 10, 90: 10, 180: 10, 365: 10},
}

# Device limits per tariff
DEVICE_LIMITS = {
    "basic": 5,
    "plus":  7,
}

# Traffic packs for purchase (gb -> {price, bytes, discount})
# Комбо-тарифы: подписка + ГБ обхода в одном пакете
COMBO_TARIFFS = {
    "combo_basic": {
        30:  {"price": 329,  "gb": 75,   "base_tariff": "basic"},   # 4.4₽/ГБ
        90:  {"price": 849,  "gb": 200,  "base_tariff": "basic"},   # 4.2₽/ГБ
        180: {"price": 1549, "gb": 400,  "base_tariff": "basic"},   # 3.9₽/ГБ
        365: {"price": 2749, "gb": 800,  "base_tariff": "basic"},   # 3.4₽/ГБ
        730: {"price": 6999, "gb": 1500, "base_tariff": "basic"},   # 4.7₽/ГБ
    },
    "combo_plus": {
        30:  {"price": 499,  "gb": 75,   "base_tariff": "plus"},    # 6.7₽/ГБ
        90:  {"price": 1299, "gb": 200,  "base_tariff": "plus"},    # 6.5₽/ГБ
        180: {"price": 2299, "gb": 400,  "base_tariff": "plus"},    # 5.7₽/ГБ
        365: {"price": 3999, "gb": 800,  "base_tariff": "plus"},    # 5.0₽/ГБ
        730: {"price": 7999, "gb": 1500, "base_tariff": "plus"},    # 5.3₽/ГБ
    },
}

TRAFFIC_PACKS = {
    15:  {"price": 89,   "bytes": 15  * 1024**3, "discount": ""},
    50:  {"price": 269,  "bytes": 50  * 1024**3, "discount": "🔥 -10%"},
    75:  {"price": 389,  "bytes": 75  * 1024**3, "discount": "🔥 -13%"},
    100: {"price": 469,  "bytes": 100 * 1024**3, "discount": "🔥 -22%"},
    150: {"price": 669,  "bytes": 150 * 1024**3, "discount": "🔥 -26%"},
    200: {"price": 859,  "bytes": 200 * 1024**3, "discount": "🔥 -28%"},
}

TRAFFIC_PACKS_EXTENDED = {
    300:  {"price": 1199,  "bytes": 300  * 1024**3, "discount": "🔥 -33%"},
    600:  {"price": 2299,  "bytes": 600  * 1024**3, "discount": "🔥 -36%"},
    1200: {"price": 4399,  "bytes": 1200 * 1024**3, "discount": "🔥 -39%"},
    2200: {"price": 7899,  "bytes": 2200 * 1024**3, "discount": "🔥 -40%"},
    5000: {"price": 17999, "bytes": 5000 * 1024**3, "discount": "🔥 -40%"},
    8000: {"price": 28799, "bytes": 8000 * 1024**3, "discount": "🔥 -40%"},
}

# Thresholds for traffic notifications (bytes remaining, flag key)
TRAFFIC_NOTIFY_THRESHOLDS = [
    (8 * 1024**3,       "traffic_notified_8gb"),
    (5 * 1024**3,       "traffic_notified_5gb"),
    (3 * 1024**3,       "traffic_notified_3gb"),
    (1 * 1024**3,       "traffic_notified_1gb"),
    (500 * 1024**2,     "traffic_notified_500mb"),
    (0,                 "traffic_notified_0"),
]

# Subscription link base for Remnawave bypass
REMNAWAVE_SUB_BASE_URL = env("REMNAWAVE_SUB_BASE_URL", default="https://rmnw.atlassecure.ru/api/sub").rstrip("/")

# Internal squad UUID for assigning new users (e.g. "Clients" squad).
# Task 2 TZ calls it `REMNAWAVE_CLIENTS_SQUAD_UUID`; both names point at
# the same squad and either may be set.
REMNAWAVE_SQUAD_UUID = (
    env("REMNAWAVE_SQUAD_UUID", default="")
    or env("REMNAWAVE_CLIENTS_SQUAD_UUID", default="")
)
# Alias kept for clarity in the new purchase flow code paths.
REMNAWAVE_CLIENTS_SQUAD_UUID = REMNAWAVE_SQUAD_UUID

# ── samopis → Remnawave premium migration knobs (migration 045) ─────────
# "MainServer" squad — premium tier (unlimited traffic on основные серверы).
REMNAWAVE_MAIN_SQUAD_UUID = env("REMNAWAVE_MAIN_SQUAD_UUID", default="")

# Whether the migration script and the new purchase flow should try to force
# the legacy samopis UUID as the Remnawave entity's full UUID.  Keeps legacy
# subscription URLs working when the panel honours the field.  Falls back to
# panel-assigned UUID automatically on 400/409/422.
def _envbool(name: str, default: bool = True) -> bool:
    val = env(name, default="").strip().lower() if env(name, default="") else ""
    if not val:
        return default
    return val in ("1", "true", "yes", "on")

REMNAWAVE_PREMIUM_FORCE_UUID = _envbool("REMNAWAVE_PREMIUM_FORCE_UUID", True)

# Username template for the premium entity. `{telegram_id}` and
# `{existing_username}` are available substitutions.  Capped to 32 chars.
REMNAWAVE_PREMIUM_USERNAME_PATTERN = env(
    "REMNAWAVE_PREMIUM_USERNAME_PATTERN",
    default="tg_{telegram_id}_premium",
)

# Device limit for the premium entity (per ТЗ Remnawave panel allows
# separate device caps).  Defaults to 5.
try:
    REMNAWAVE_PREMIUM_DEVICE_LIMIT = int(env("REMNAWAVE_PREMIUM_DEVICE_LIMIT", default="5"))
except (TypeError, ValueError):
    REMNAWAVE_PREMIUM_DEVICE_LIMIT = 5

# Task 6: External Squad UUID for premium users.  When set, every premium
# entity (POST on create, PATCH on adoption + renewal) carries this value
# in `externalSquadUuid`, which makes Remnawave override the subscription
# Template to "Unlimited" (RU split-routing + SDK/SMTP/mining blocklists).
# Bypass entities are NOT assigned this field — they stay on the Default
# Template.  Left unset (empty string → None) the bot silently skips the
# field, so existing local/dev environments keep working unchanged.
REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID = env(
    "REMNAWAVE_PREMIUM_EXTERNAL_SQUAD_UUID", default=""
) or None

# Master switch for the subscription-URL fallback FastAPI router
# (app/api/subscription_proxy.py).  Default OFF — turn on per environment
# once the public DNS for sub.atlassecure.ru points at this bot.
SUBSCRIPTION_PROXY_ENABLED = _envbool("SUBSCRIPTION_PROXY_ENABLED", False)

# ── Task 2 cut-over: Remnawave-only purchase flow ──────────────────────
# Defaults to TRUE — the bot is fully on Remnawave now and the samopis
# vpnapi master is decommissioned.  Flip to false ONLY for emergency
# rollback (e.g. samopis temporarily reinstated); legacy
# vpn_utils.add_vless_user / update / remove calls become no-ops while
# this flag is on.
PURCHASE_FLOW_REMNAWAVE = _envbool("PURCHASE_FLOW_REMNAWAVE", True)

# Bypass username pattern.  TZ asks for `tg_{telegram_id}_bypass`, but the
# existing ~2500 bypass entities in the panel are named just `{telegram_id}`.
# Default keeps the existing pattern so we don't have to rename them; set
# to `tg_{telegram_id}_bypass` on a fresh deployment.
REMNAWAVE_BYPASS_USERNAME_PATTERN = env(
    "REMNAWAVE_BYPASS_USERNAME_PATTERN",
    default="{telegram_id}",
)

# Trial-specific bypass allowance (1 GB per TZ — premium is duration-limited,
# bypass is byte-limited).
try:
    TRIAL_BYPASS_GB = int(env("TRIAL_BYPASS_GB", default="1"))
except (TypeError, ValueError):
    TRIAL_BYPASS_GB = 1

# Bypass entity device limit (default 5; TZ matches premium=5/7).
try:
    REMNAWAVE_BYPASS_DEVICE_LIMIT = int(env("REMNAWAVE_BYPASS_DEVICE_LIMIT", default="5"))
except (TypeError, ValueError):
    REMNAWAVE_BYPASS_DEVICE_LIMIT = 5

# Bypass far-future expireAt (TZ asks for 2099-12-31; bot historically uses
# now+10 years which is functionally identical).  Configurable for tests.
BYPASS_INFINITE_EXPIRE_ISO = env("BYPASS_INFINITE_EXPIRE", default="2099-12-31T23:59:59Z")

# Legacy samopis sub-URL base, used by the fallback endpoint to redirect
# unmigrated users back to the old infrastructure during the grace period.
LEGACY_SAMOPIS_SUB_BASE_URL = env(
    "LEGACY_SAMOPIS_SUB_BASE_URL",
    default="",
).rstrip("/")

# Redis for FSM storage
REDIS_URL = env("REDIS_URL", default="")

