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
# Optional secrets: TG_PROVIDER_TOKEN, PLATEGA_SECRET (via env prefix)
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

# ====================================================================================
# Admin Web Dashboard (mounted at /dashboard inside the existing FastAPI app)
# ====================================================================================
# JWT_SECRET — used to sign magic-link tokens issued by the /admin bot command and
# to verify them on REST/WebSocket calls. Generate with `openssl rand -hex 32`.
# DASHBOARD_BASE_URL — public origin of the bot (same as webhook), used to build
# the magic-link URL in the /admin command, e.g. https://atlas.up.railway.app.
# DASHBOARD_ENABLED is True only when BOTH are set; otherwise the dashboard routers
# are silently skipped — bot still works fine without them.
JWT_SECRET = env("JWT_SECRET")
DASHBOARD_BASE_URL = env("DASHBOARD_BASE_URL")
DASHBOARD_ENABLED = bool(JWT_SECRET and DASHBOARD_BASE_URL)
if not DASHBOARD_ENABLED:
    _log.info(
        "DASHBOARD disabled — set %s_JWT_SECRET and %s_DASHBOARD_BASE_URL to enable",
        APP_ENV.upper(), APP_ENV.upper(),
    )

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
}

# ── Telegram MTProto-прокси ─────────────────────────────────────────────
# Разовая покупка (навсегда). Одна статичная ссылка на всех покупателей —
# никакой сущности в Remnawave, подписка не активируется.
PROXY_PRICE_RUBLES = 69
# tg:// форма — открывает диалог подключения в Telegram.
PROXY_TG_LINK = (
    "tg://proxy?server=mtproxytg.atlassecure.uk&port=443"
    "&secret=7u9aLwhaS5PXI6GiK2T4OjphenVyZS5taWNyb3NvZnQuY29t"
)
# https://t.me/proxy форма — используется в URL-кнопке (надёжно работает
# в inline-кнопках, в отличие от tg://).
PROXY_HTTPS_LINK = (
    "https://t.me/proxy?server=mtproxytg.atlassecure.uk&port=443"
    "&secret=7u9aLwhaS5PXI6GiK2T4OjphenVyZS5taWNyb3NvZnQuY29t"
)

# Все допустимые типы подписок (для валидации в БД и хендлерах).
# Бизнес-тарифы удалены (решение владельца 2026-09-14): легаси biz_* в БД
# читаются как "plus" через app.services.tariffs.normalize_tier.
VALID_SUBSCRIPTION_TYPES = ("basic", "plus")

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
}

# RUB → Stars rule for prices without a row in TARIFFS_STARS (combo, gifts,
# top-ups): ceil(rub × STARS_MARKUP / RUB_PER_STAR). The same 1.7 / 1.85 as
# the table above and the top-up / gift invoices (app.services.tariffs.stars_for_rub).
STARS_MARKUP = 1.7
RUB_PER_STAR = 1.85

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

# Platega (SBP) Configuration
# Platega.io — единый провайдер: СБП (2), Карта (11), Международные (12)
PLATEGA_MERCHANT_ID = env("PLATEGA_MERCHANT_ID", default="")
PLATEGA_SECRET = env("PLATEGA_SECRET")
PLATEGA_API_URL = env("PLATEGA_API_URL") or "https://app.platega.io"
# Наценки на методы Platega (override через env при необходимости)
SBP_MARKUP_PERCENT = int(env("SBP_MARKUP_PERCENT", default="0") or "0")
PLATEGA_CARD_MARKUP_PERCENT = int(env("PLATEGA_CARD_MARKUP_PERCENT", default="0") or "0")
PLATEGA_INTL_MARKUP_PERCENT = int(env("PLATEGA_INTL_MARKUP_PERCENT", default="0") or "0")

# CryptoBot (Crypto Pay) Configuration
# Криптовалютная оплата через @CryptoBot
CRYPTOBOT_API_TOKEN = env("CRYPTOBOT_API_TOKEN", default="")
CRYPTOBOT_API_URL = env("CRYPTOBOT_API_URL") or "https://pay.crypt.bot/api"

# Wata (wata.pro) Configuration — H2H REST API.
# Access token (Bearer JWT) выдаётся в личном кабинете мерчанта.
# WATA_SANDBOX=true → https://api-sandbox.wata.pro (тестовые карты).
WATA_ACCESS_TOKEN = env("WATA_ACCESS_TOKEN", default="")
WATA_SANDBOX = env("WATA_SANDBOX", default="false").lower() in ("1", "true", "yes")
# Публичный ключ WATA для проверки X-Signature webhook'ов (PEM, RSA). Опционально:
# если задан — используется без сетевого запроса GET /public-key. Можно одной
# строкой с литеральными "\n". Пусто → ключ грузится лениво с api.wata.pro.
WATA_PUBLIC_KEY_PEM = env("WATA_PUBLIC_KEY_PEM", default="")

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

# Cutover 2026-08: samopis Xray-мастер выведен из эксплуатации, единственный
# источник provisioning — Remnawave 3.x. Все call-sites `config.VPN_ENABLED`
# семантически означают "можно ли боту выдавать/продлять VPN" — это статус
# Remnawave.
VPN_ENABLED = REMNAWAVE_ENABLED

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

# Devices on the PREMIUM panel entity (hwidDeviceLimit) — owner decision
# 2026-09-14: Basic 10, Plus 14, as the tariff texts say. Combo = its base tier,
# legacy biz_* = Plus (app.services.tariffs.premium_device_limit).
# REMNAWAVE_PREMIUM_DEVICE_LIMIT is only the fallback for an unknown tier.
PREMIUM_DEVICE_LIMITS = {
    "basic": 10,
    "plus": 14,
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

# ── Task 2 cut-over: Remnawave-only purchase flow ──────────────────────
# Defaults to TRUE — the bot is fully on Remnawave now and the samopis
# vpnapi master is decommissioned.  Flip to false ONLY for emergency
# rollback (e.g. samopis temporarily reinstated).
PURCHASE_FLOW_REMNAWAVE = _envbool("PURCHASE_FLOW_REMNAWAVE", True)

# Bypass username pattern.  TZ asks for `tg_{telegram_id}_bypass`, but the
# existing ~2500 bypass entities in the panel are named just `{telegram_id}`.
# Default keeps the existing pattern so we don't have to rename them; set
# to `tg_{telegram_id}_bypass` on a fresh deployment.
REMNAWAVE_BYPASS_USERNAME_PATTERN = env(
    "REMNAWAVE_BYPASS_USERNAME_PATTERN",
    default="{telegram_id}",
)

# Trial-specific bypass allowance in megabytes (premium is duration-limited,
# bypass is byte-limited).  Default 500 MB.
try:
    TRIAL_BYPASS_MB = int(env("TRIAL_BYPASS_MB", default="500"))
except (TypeError, ValueError):
    TRIAL_BYPASS_MB = 500

# Bypass entity device limit (default 5; TZ matches premium=5/7).
try:
    REMNAWAVE_BYPASS_DEVICE_LIMIT = int(env("REMNAWAVE_BYPASS_DEVICE_LIMIT", default="5"))
except (TypeError, ValueError):
    REMNAWAVE_BYPASS_DEVICE_LIMIT = 5

# Bypass far-future expireAt (TZ asks for 2099-12-31; bot historically uses
# now+10 years which is functionally identical).  Configurable for tests.
BYPASS_INFINITE_EXPIRE_ISO = env("BYPASS_INFINITE_EXPIRE", default="2099-12-31T23:59:59Z")

# Redis for FSM storage
REDIS_URL = env("REDIS_URL", default="")


# ─────────────────────────────────────────────────────────────────────────
# Sub-aggregator service — константы (без ENV).
#
# Меняешь прямо здесь и рестартишь бота. Держим захардкоженным чтобы не
# плодить переменные окружения в Railway UI.
#
# INTERNAL_SECRET должен совпадать с тем, что в .env сервиса-агрегатора
# (sub-aggregator/.env → INTERNAL_SECRET). Пустая строка → invalidate
# skip-нётся, кеш обновится через CACHE_TTL (5 мин) автоматически —
# приемлемо для беты.
# ─────────────────────────────────────────────────────────────────────────

# ── ДВА НЕЗАВИСИМЫХ ПЕРЕКЛЮЧАТЕЛЯ ──────────────────────────────────────
# SUB_AGGREGATOR_ENABLED — монтирует эндпоинт /a/{token} (обслуживает уже
#   добавленные пользователями ссылки). ДЕРЖИМ True, иначе у всех, кто уже
#   добавил единый ключ, он перестанет открываться.
# SUB_AGGREGATOR_ISSUE_ENABLED — выдаём/показываем ли НОВУЮ единую ссылку в
#   экранах бота (подключение/профиль). False → фича выключена для всех,
#   юзеры идут по legacy-флоу (2 отдельных ключа), НО эндпоинт продолжает
#   отдавать уже выданные ссылки. Так «отключаем агрегатор, но ссылки у всех
#   работают».
SUB_AGGREGATOR_ENABLED = True          # эндпоинт /a/{token} живёт (existing links работают)
SUB_AGGREGATOR_ISSUE_ENABLED = False   # выдача новым/показ в боте ВЫКЛ для всех
SUB_AGGREGATOR_URL = "https://subscription.palantirdns.uk"
SUB_AGGREGATOR_ADMIN_ONLY = False  # (не влияет пока ISSUE_ENABLED=False; при True гейтил бы выдачу только на админа)
SUB_AGGREGATOR_INTERNAL_SECRET = ""  # заполни после генерации в sub-aggregator/.env

# ЖИВОЙ host панели, откуда агрегатор качает upstream-подписки. Что бы ни
# лежало в БД (subscription.vps-cloud.uk, старый rewrite, etc.) — агрегатор
# принудительно бьёт СЮДА. Иначе fetch падает → пустая склейка → 503 →
# клиент пишет «неизвестный тип контента».
# ⚠️ Должен совпадать с public-доменом подписок в панели Remnawave.
# Если панель отдаёт subscriptionUrl на другом хосте — поставь его сюда.
# Пусто → агрегатор качает URL как есть (без подмены host).
SUB_AGGREGATOR_UPSTREAM_HOST = "sub.atlassecure.ru"

