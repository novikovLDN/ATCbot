import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import config


DATABASE_FILE = "bot.db"


async def init_db():
    """Инициализация базы данных и создание таблиц"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        # Таблица users
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                language TEXT DEFAULT 'ru',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица payments
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                tariff TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Таблица subscriptions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                telegram_id INTEGER PRIMARY KEY,
                vpn_key TEXT,
                expires_at DATETIME,
                is_active INTEGER DEFAULT 1
            )
        """)
        
        await db.commit()


async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить пользователя по Telegram ID"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_user(telegram_id: int, username: Optional[str] = None, language: str = "ru"):
    """Создать нового пользователя"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, language) VALUES (?, ?, ?)",
            (telegram_id, username, language)
        )
        await db.commit()


async def update_user_language(telegram_id: int, language: str):
    """Обновить язык пользователя"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "UPDATE users SET language = ? WHERE telegram_id = ?",
            (language, telegram_id)
        )
        await db.commit()


async def update_username(telegram_id: int, username: Optional[str]):
    """Обновить username пользователя"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "UPDATE users SET username = ? WHERE telegram_id = ?",
            (username, telegram_id)
        )
        await db.commit()


async def get_pending_payment_by_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить pending платеж пользователя"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payments WHERE telegram_id = ? AND status = 'pending'",
            (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_payment(telegram_id: int, tariff: str) -> Optional[int]:
    """Создать платеж и вернуть его ID. Возвращает None, если уже есть pending платеж"""
    # Проверяем наличие pending платежа
    existing_payment = await get_pending_payment_by_user(telegram_id)
    if existing_payment:
        return None  # У пользователя уже есть pending платеж
    
    async with aiosqlite.connect(DATABASE_FILE) as db:
        cursor = await db.execute(
            "INSERT INTO payments (telegram_id, tariff, status) VALUES (?, ?, 'pending')",
            (telegram_id, tariff)
        )
        await db.commit()
        return cursor.lastrowid


async def get_payment(payment_id: int) -> Optional[Dict[str, Any]]:
    """Получить платеж по ID"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_payment_status(payment_id: int, status: str):
    """Обновить статус платежа"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "UPDATE payments SET status = ? WHERE id = ?",
            (status, payment_id)
        )
        await db.commit()


async def get_subscription(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить активную подписку пользователя"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE telegram_id = ? AND is_active = 1",
            (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_subscription(telegram_id: int, vpn_key: str, months: int):
    """Создать подписку для пользователя"""
    expires_at = datetime.now() + timedelta(days=months * 30)
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO subscriptions (telegram_id, vpn_key, expires_at, is_active) VALUES (?, ?, ?, 1)",
            (telegram_id, vpn_key, expires_at.isoformat())
        )
        await db.commit()


async def get_pending_payments() -> list:
    """Получить все pending платежи (для админа)"""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

