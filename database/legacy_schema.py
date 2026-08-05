"""Аварийный бутстрап схемы: тот самый DDL, который раньше шёл при каждом старте.

ЧТО ЗДЕСЬ
    ~640 строк императивного CREATE TABLE / ALTER TABLE / CREATE INDEX
    с IF NOT EXISTS и посев промокодов. Всё это умеет поднять схему с нуля
    на пустой базе — и больше ничего.

ПОЧЕМУ ВЫНЕСЕНО ИЗ core.py
    Источник схемы — каталог migrations/. Этот блок оставлен только для
    случая, когда миграции применить нельзя: локальная разработка на чистой
    базе, ручное восстановление. Включается переменной окружения
    LEGACY_SCHEMA_BOOTSTRAP=1 и по умолчанию не выполняется.

    В core.py он занимал половину файла и половину функции init_db, из-за
    чего в фундаменте (пул, DB_READY, инициализация) было не разглядеть
    собственно фундамент. Правят эти вещи по совершенно разным поводам:
    DDL — когда чинят чужую базу, пул — когда бот не держит нагрузку.

ЧТО ЛЕГКО СЛОМАТЬ
    1. Добавить сюда новую таблицу или колонку вместо миграции. На боевой
       базе этот код не выполняется — изменение просто не доедет. Пишите
       миграцию в migrations/, тест
       tests/services/test_schema_single_source.py следит за тем, чтобы
       всё отсюда существовало и там.

    2. Убрать `except Exception: pass` вокруг ALTER'ов, посчитав их
       небрежностью. Они намеренные: блок обязан доезжать до конца на
       любой базе, в том числе там, где колонка уже есть. Ровно поэтому
       он и не годится как источник схемы — ошибка здесь молчит.

    3. Забыть, что каждый оператор просит ACCESS EXCLUSIVE на свою
       таблицу. На живой базе одна idle-in-transaction сессия — и ALTER
       встаёт в очередь, а за ним все читающие запросы к users,
       subscriptions, payments. Вызывающий (database/core.py, init_db)
       заранее ставит lock_timeout=5s и statement_timeout=20s — не
       вызывайте этот модуль на соединении без них.
"""
import logging

logger = logging.getLogger(__name__)


async def apply_legacy_schema_bootstrap(conn) -> None:
    """Выполнить легаси-DDL на переданном соединении.

    Соединение приходит снаружи намеренно: вызывающий уже выставил на нём
    lock_timeout / statement_timeout, и брать своё — значит остаться без
    этой защиты.
    """
    # Таблица users
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT,
            language TEXT DEFAULT 'ru',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Миграция: добавляем referral_level, если его нет
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_level TEXT DEFAULT 'base' CHECK (referral_level IN ('base', 'vip'))")
    except Exception:
        pass

    # Привязка аккаунта на сайте QoDev. Колонку читает воркер синхронизации
    # (app/workers/site_sync_worker.py) и админский экран связок.
    #
    # ПОЧЕМУ ЗДЕСЬ, А НЕ В ОБРАБОТЧИКЕ
    #     Тот же ALTER TABLE выполнялся прямо внутри /start — на каждой
    #     привязке сайта. ALTER берёт ACCESS EXCLUSIVE на users: он
    #     дожидается всех текущих запросов к таблице и всё это время
    #     держит новые. На горячем пути это управляемый по чужому таймингу
    #     стоп-кран для всего бота, ради колонки, которая нужна один раз.
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS site_linked BOOLEAN DEFAULT FALSE")
    except Exception:
        pass

    # Таблица pending_purchases - контекст покупки для защиты от устаревших кнопок
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_purchases (
            id SERIAL PRIMARY KEY,
            purchase_id TEXT UNIQUE NOT NULL,
            telegram_id BIGINT NOT NULL,
            tariff TEXT NOT NULL CHECK (tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate')),
            period_days INTEGER NOT NULL,
            price_kopecks INTEGER NOT NULL,
            promo_code TEXT,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'expired')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)

    # Миграция: устанавливаем expires_at для существующих pending purchases с NULL expires_at
    try:
        await conn.execute("""
            UPDATE pending_purchases 
            SET expires_at = created_at + INTERVAL '30 minutes'
            WHERE expires_at IS NULL
            AND status = 'pending'
        """)
    except Exception:
        pass

    # Создаем индексы для быстрого поиска
    try:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_status ON pending_purchases(status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_telegram_id ON pending_purchases(telegram_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_purchase_id ON pending_purchases(purchase_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_purchases_expires_at ON pending_purchases(expires_at)")
    except Exception:
        # Индексы уже существуют
        pass

    # Таблица payments
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            tariff TEXT NOT NULL,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            purchase_id TEXT
        )
    """)

    # P0 HOTFIX: Ensure idempotency columns exist (migration 012 compatibility)
    # These columns are added by migration 012, but if table is recreated,
    # we need to add them here to prevent schema drift
    try:
        await conn.execute("""
            ALTER TABLE payments
            ADD COLUMN IF NOT EXISTS telegram_payment_charge_id TEXT
        """)
        await conn.execute("""
            ALTER TABLE payments
            ADD COLUMN IF NOT EXISTS cryptobot_payment_id TEXT
        """)
    except Exception:
        # Columns may already exist or migration handles this
        pass

    # SECURITY: Unique constraint on purchase_id for approved/paid payments (idempotency)
    try:
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_unique_purchase_approved
            ON payments(purchase_id)
            WHERE purchase_id IS NOT NULL AND status IN ('approved', 'paid')
        """)
    except Exception:
        pass

    # Таблица subscriptions
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            outline_key_id INTEGER,
            vpn_key TEXT,
            expires_at TIMESTAMP NOT NULL,
            reminder_sent BOOLEAN DEFAULT FALSE,
            reminder_3d_sent BOOLEAN DEFAULT FALSE,
            reminder_24h_sent BOOLEAN DEFAULT FALSE,
            reminder_3h_sent BOOLEAN DEFAULT FALSE,
            reminder_6h_sent BOOLEAN DEFAULT FALSE,
            admin_grant_days INTEGER DEFAULT NULL,
            auto_renew BOOLEAN DEFAULT FALSE
        )
    """)

    # Миграция: добавляем auto_renew, если его нет
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN DEFAULT FALSE")
    except Exception:
        pass

    # Миграция: добавляем поле для защиты от повторного автопродления
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_auto_renewal_at TIMESTAMP")
    except Exception:
        pass

    # Миграция: добавляем last_notification_sent_at для автопродления
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_notification_sent_at TIMESTAMP")
    except Exception:
        pass

    # Миграция: добавляем новые поля для напоминаний, если их нет
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_3d_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS outline_key_id INTEGER")
        # Делаем vpn_key nullable для поддержки старых записей
        await conn.execute("ALTER TABLE subscriptions ALTER COLUMN vpn_key DROP NOT NULL")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_3h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_6h_sent BOOLEAN DEFAULT FALSE")

        # Trial notification flags (без миграции - используем существующую структуру)
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_6h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_18h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_30h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_42h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_54h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_60h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_notif_71h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS admin_grant_days INTEGER DEFAULT NULL")
        # Поля для умных уведомлений
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMP")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_bytes BIGINT DEFAULT 0")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS first_traffic_at TIMESTAMP")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_no_traffic_20m_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_no_traffic_24h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_first_connection_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_3days_usage_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_7days_before_expiry_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_expiry_day_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_expired_24h_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS smart_notif_vip_offer_sent BOOLEAN DEFAULT FALSE")
        # Поле для anti-spam защиты (минимальный интервал между уведомлениями)
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_notification_sent_at TIMESTAMP")

        # Xray Core migration: добавляем uuid, status, source для VLESS
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS uuid TEXT")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'payment'")
    except Exception:
        # Колонки уже существуют
        pass

    # Миграция: добавляем поле notification_sent в payments для идемпотентности уведомлений
    try:
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS notification_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP")
    except Exception:
        pass

    # Миграция: добавляем поля для delayed activation (premium flow)
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_status TEXT DEFAULT 'active'")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activation_attempts INTEGER DEFAULT 0")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_activation_error TEXT")
    except Exception:
        pass

    # Миграция 032: subscription_type для VPN API tariff (basic / plus)
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS subscription_type TEXT DEFAULT 'basic'")
    except Exception:
        pass

    # Миграция 033: vpn_key_plus для Plus (второй vless-ключ)
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS vpn_key_plus TEXT")
    except Exception:
        pass

    # Миграция 045: вторая Remnawave entity для премиум-тарифа (MainServer squad).
    # remnawave_uuid остаётся для bypass-тарифа; remnawave_premium_uuid
    # хранится для безлимитных основных серверов.
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_premium_uuid TEXT")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS samopis_migrated_at TIMESTAMPTZ")
        # Миграция 046: кэш subscriptionUrl, чтобы fallback-роутер не
        # дёргал панель на каждый /sub/{uuid} запрос.
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_premium_sub_url TEXT")
        # Миграция 047: кэш shortUuid для пересборки sub URL при
        # необходимости (Remnawave v2.7+ разделил uuid / vlessUuid / shortUuid).
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_premium_short_uuid TEXT")
        # Миграция 048: симметричный кэш sub_url / short_uuid для bypass-entity
        # (нужно после Task 2 cut-over чтобы UI мог отдавать обе ссылки без
        # лишних round-trip'ов к панели).
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_bypass_sub_url TEXT")
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS remnawave_bypass_short_uuid TEXT")
        # Миграция 049: маркер для одноразовой рассылки уведомления о
        # миграции инфраструктуры (Task 3).  Background-сендер фильтрует
        # по этому полю чтобы не задвоить.
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS migration_notice_sent_at TIMESTAMPTZ")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscriptions_remnawave_premium_uuid "
            "ON subscriptions(remnawave_premium_uuid) WHERE remnawave_premium_uuid IS NOT NULL"
        )
    except Exception:
        pass

    # Миграция: добавляем поле balance в users (хранится в копейках как INTEGER)
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    # Trial usage tracking (без миграций - используем ALTER TABLE IF NOT EXISTS)
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_used_at TIMESTAMP")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMP")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_completed_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_offer_sent BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS special_offer_created_at TIMESTAMP")
    except Exception:
        pass

    # Таблица balance_transactions
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS balance_transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount NUMERIC NOT NULL,
            type TEXT NOT NULL,
            source TEXT,
            description TEXT,
            related_user_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Миграция: добавляем related_user_id, если его нет
    try:
        await conn.execute("ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS related_user_id BIGINT")
    except Exception:
        pass

    # Миграция: добавляем поле source в balance_transactions, если его нет
    try:
        await conn.execute("ALTER TABLE balance_transactions ADD COLUMN IF NOT EXISTS source TEXT")
        # Меняем тип amount на NUMERIC для точности
        await conn.execute("ALTER TABLE balance_transactions ALTER COLUMN amount TYPE NUMERIC USING amount::NUMERIC")
    except Exception:
        # Колонка уже существует или ошибка миграции
        pass

    # Миграция: добавляем поля для реферальной программы
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT")
        # Добавляем referrer_id (или referred_by для обратной совместимости)
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT")
        # Если есть referred_by, но нет referrer_id - копируем данные
        await conn.execute("""
            UPDATE users 
            SET referrer_id = referred_by 
            WHERE referrer_id IS NULL AND referred_by IS NOT NULL
        """)
        # Создаем индекс для быстрого поиска по referral_code
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL")
        # Создаем индекс для быстрого поиска по referrer_id
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_referrer_id ON users(referrer_id) WHERE referrer_id IS NOT NULL")
    except Exception:
        # Колонки уже существуют
        pass

    # Таблица referrals (партнёрская программа)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_user_id BIGINT NOT NULL,
            referred_user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_rewarded BOOLEAN DEFAULT FALSE,
            reward_amount INTEGER DEFAULT 0,
            UNIQUE (referred_user_id)
        )
    """)

    # Создаём индекс для быстрого поиска по партнёру
    try:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_user_id)")
    except Exception:
        pass

    # Миграция: переименовываем колонки, если они еще старые
    try:
        await conn.execute("ALTER TABLE referrals RENAME COLUMN referrer_id TO referrer_user_id")
    except Exception:
        pass
    try:
        await conn.execute("ALTER TABLE referrals RENAME COLUMN referred_id TO referred_user_id")
    except Exception:
        pass
    try:
        await conn.execute("ALTER TABLE referrals RENAME COLUMN rewarded TO is_rewarded")
    except Exception:
        pass
    try:
        await conn.execute("ALTER TABLE referrals ADD COLUMN IF NOT EXISTS reward_amount INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        await conn.execute("ALTER TABLE referrals ADD COLUMN IF NOT EXISTS first_paid_at TIMESTAMP")
    except Exception:
        pass

    # Таблица referral_rewards - история всех начислений реферального кешбэка
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_rewards (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL,
            buyer_id BIGINT NOT NULL,
            purchase_id TEXT,
            purchase_amount INTEGER NOT NULL,
            percent INTEGER NOT NULL,
            reward_amount INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Создаём индексы для быстрого поиска
    try:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON referral_rewards(referrer_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_buyer ON referral_rewards(buyer_id)")
        # Частичный уникальный индекс для предотвращения дубликатов начислений по одному purchase_id
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_rewards_unique_buyer_purchase ON referral_rewards(buyer_id, purchase_id) WHERE purchase_id IS NOT NULL")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_purchase_id ON referral_rewards(purchase_id) WHERE purchase_id IS NOT NULL")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_created_at ON referral_rewards(created_at)")
    except Exception:
        pass

    # Здесь создавалась таблица vpn_keys (vpn_key, is_used,
    # assigned_to, assigned_at) — реликт пула заранее нарезанных
    # ключей. Ни одного SELECT/INSERT/UPDATE/DELETE по ней в коде
    # нет: ключ подписки живёт в колонке subscriptions.vpn_key, а
    # выдаёт его Remnawave. Стартовый DDL брал на пустую таблицу
    # ACCESS EXCLUSIVE и создавал впечатление, что пул существует.
    #
    # Саму таблицу этим не удалить: она есть в migrations/001_init.sql
    # и в проде уже создана, а DROP TABLE — отдельная миграция схемы
    # и решение владельца. Здесь снят только повторный CREATE.

    # Таблица audit_log
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            action TEXT NOT NULL,
            telegram_id BIGINT NOT NULL,
            target_user BIGINT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Миграция: добавляем колонки для VPN lifecycle audit (если их нет)
    try:
        await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS uuid TEXT")
        await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS source TEXT")
        await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS result TEXT CHECK (result IN ('success', 'error'))")
        # STEP 5 — PART C: CORRELATION & TRACEABILITY
        # Add correlation_id column for traceability
        await conn.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS correlation_id TEXT")
        # Создаём индекс для быстрого поиска по UUID
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_uuid ON audit_log(uuid) WHERE uuid IS NOT NULL")
        # Создаём индекс для быстрого поиска по action
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")
        # Создаём индекс для быстрого поиска по source
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_source ON audit_log(source) WHERE source IS NOT NULL")
        # STEP 5 — PART C: CORRELATION & TRACEABILITY
        # Index for correlation_id for fast incident timeline reconstruction
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_correlation_id ON audit_log(correlation_id) WHERE correlation_id IS NOT NULL")
    except Exception:
        # Колонки уже существуют
        pass

    # Таблица subscription_history
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS subscription_history (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            vpn_key TEXT NOT NULL,
            start_date TIMESTAMP NOT NULL,
            end_date TIMESTAMP NOT NULL,
            action_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица broadcasts
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            message TEXT,
            message_a TEXT,
            message_b TEXT,
            is_ab_test BOOLEAN DEFAULT FALSE,
            type TEXT NOT NULL,
            segment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_by BIGINT NOT NULL
        )
    """)

    # Добавляем колонки для миграции
    try:
        await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS segment TEXT")
        await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS is_ab_test BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_a TEXT")
        await conn.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS message_b TEXT")
    except Exception:
        # Колонки уже существуют или таблицы нет
        pass

    # Таблица broadcast_log
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_log (
            id SERIAL PRIMARY KEY,
            broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id) ON DELETE CASCADE,
            telegram_id BIGINT NOT NULL,
            status TEXT NOT NULL,
            variant TEXT,
            message_id BIGINT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Добавляем колонку variant для миграции
    try:
        await conn.execute("ALTER TABLE broadcast_log ADD COLUMN IF NOT EXISTS variant TEXT")
    except Exception:
        # Колонка уже существует или таблицы нет
        pass

    # Добавляем колонку message_id для миграции
    try:
        await conn.execute("ALTER TABLE broadcast_log ADD COLUMN IF NOT EXISTS message_id BIGINT")
    except Exception:
        pass

    # Таблица broadcast_discounts (скидки для кнопок уведомлений)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_discounts (
            id SERIAL PRIMARY KEY,
            broadcast_id INTEGER NOT NULL UNIQUE REFERENCES broadcasts(id) ON DELETE CASCADE,
            discount_percent INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица incident_settings (режим инцидента)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS incident_settings (
            id SERIAL PRIMARY KEY,
            is_active BOOLEAN DEFAULT FALSE,
            incident_text TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица user_discounts (персональные скидки)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_discounts (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            discount_percent INTEGER NOT NULL,
            expires_at TIMESTAMP NULL,
            created_by BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица user_traffic_discounts (промо-скидки на трафик из рассылок)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_traffic_discounts (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            discount_percent INTEGER NOT NULL,
            expires_at TIMESTAMP NULL,
            created_by BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица vip_users (VIP-статус)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS vip_users (
            telegram_id BIGINT UNIQUE NOT NULL PRIMARY KEY,
            granted_by BIGINT NOT NULL,
            granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица promo_codes (промокоды)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT UNIQUE NOT NULL PRIMARY KEY,
            discount_percent INTEGER NOT NULL,
            max_uses INTEGER NULL,
            used_count INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица promo_usage_logs (логи использования промокодов)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_usage_logs (
            id SERIAL PRIMARY KEY,
            promo_code TEXT NOT NULL,
            telegram_id BIGINT NOT NULL,
            tariff TEXT NOT NULL,
            discount_percent INTEGER NOT NULL,
            price_before INTEGER NOT NULL,
            price_after INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Создаём одну строку, если её нет
    existing = await conn.fetchval("SELECT COUNT(*) FROM incident_settings")
    if existing == 0:
        await conn.execute("""
            INSERT INTO incident_settings (is_active, incident_text)
            VALUES (FALSE, NULL)
        """)

    # Инициализируем промокоды, если их нет
    await _init_promo_codes(conn)

    # Миграция 034: расширяем CHECK constraint для бизнес-тарифов в pending_purchases
    try:
        await conn.execute("""
            ALTER TABLE pending_purchases DROP CONSTRAINT IF EXISTS pending_purchases_tariff_check
        """)
        await conn.execute("""
            ALTER TABLE pending_purchases ADD CONSTRAINT pending_purchases_tariff_check
            CHECK (tariff IS NULL OR tariff IN ('basic', 'plus', 'biz_starter', 'biz_team', 'biz_business', 'biz_pro', 'biz_enterprise', 'biz_ultimate', 'telegram_premium') OR tariff LIKE 'traffic_%' OR tariff LIKE 'apple_id_%')
        """)
    except Exception:
        pass

    # Миграция 035: добавляем колонку country для бизнес-тарифов
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS country TEXT")
    except Exception:
        pass
    try:
        await conn.execute("ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS country TEXT")
    except Exception:
        pass

    # Миграция 036: is_combo и is_bypass_only для комбо/bypass подписок
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE")
    except Exception:
        pass
    try:
        await conn.execute("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS is_bypass_only BOOLEAN DEFAULT FALSE")
    except Exception:
        pass

    # Миграция 037: is_combo для pending_purchases
    try:
        await conn.execute("ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS is_combo BOOLEAN DEFAULT FALSE")
    except Exception:
        pass

    # Миграция 038: traffic_notified_8gb и traffic_notified_5gb
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_8gb BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS traffic_notified_5gb BOOLEAN DEFAULT FALSE")
    except Exception:
        pass

    # Таблица gift_subscriptions — подарочные подписки
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS gift_subscriptions (
            id SERIAL PRIMARY KEY,
            gift_code TEXT UNIQUE NOT NULL,
            buyer_telegram_id BIGINT NOT NULL,
            tariff TEXT NOT NULL,
            period_days INTEGER NOT NULL,
            price_kopecks INTEGER NOT NULL,
            purchase_id TEXT,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'activated', 'expired')),
            activated_by BIGINT,
            activated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)
    try:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_subscriptions_code ON gift_subscriptions(gift_code)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_subscriptions_buyer ON gift_subscriptions(buyer_telegram_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_subscriptions_status ON gift_subscriptions(status)")
    except Exception:
        pass

    # Миграция: purchase_type для gift в pending_purchases
    try:
        await conn.execute("ALTER TABLE pending_purchases ADD COLUMN IF NOT EXISTS purchase_type TEXT DEFAULT 'subscription'")
    except Exception:
        pass


async def _init_promo_codes(conn):
    """Инициализация промокодов в базе данных"""
    # Check if promo_codes has id/deleted_at (post-021 schema)
    has_id = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'id'"
    )
    has_deleted_at = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'promo_codes' AND column_name = 'deleted_at'"
    )
    use_new_schema = bool(has_id and has_deleted_at)

    # 1. Деактивируем устаревший промокод
    if use_new_schema:
        await conn.execute("""
            UPDATE promo_codes
            SET is_active = FALSE, deleted_at = NOW()
            WHERE UPPER(code) = 'COURIER40' AND (deleted_at IS NULL OR is_active = TRUE)
        """)
    else:
        await conn.execute("""
            UPDATE promo_codes SET is_active = FALSE WHERE code = 'COURIER40'
        """)

    # 2. Добавляем актуальные промокоды
    if use_new_schema:
        # Partial unique index: ON CONFLICT (code) WHERE is_active AND deleted_at IS NULL
        for row in [
            ("ELVIRA064", 64, 50),
            ("YAbx30", 30, None),
            ("FAM50", 50, 50),
            ("COURIER30", 30, 40),
        ]:
            code, discount, max_uses = row
            await conn.execute("""
                INSERT INTO promo_codes (code, discount_percent, max_uses, is_active, deleted_at)
                VALUES ($1, $2, $3, TRUE, NULL)
                ON CONFLICT (code) WHERE (is_active = true AND deleted_at IS NULL)
                DO UPDATE SET discount_percent = EXCLUDED.discount_percent, max_uses = EXCLUDED.max_uses
            """, code, discount, max_uses)
    else:
        await conn.execute("""
            INSERT INTO promo_codes (code, discount_percent, max_uses, is_active)
            VALUES
                ('ELVIRA064', 64, 50, TRUE),
                ('YAbx30', 30, NULL, TRUE),
                ('FAM50', 50, 50, TRUE),
                ('COURIER30', 30, 40, TRUE)
            ON CONFLICT (code) DO UPDATE SET
                discount_percent = EXCLUDED.discount_percent,
                max_uses = EXCLUDED.max_uses,
                is_active = EXCLUDED.is_active
        """)
