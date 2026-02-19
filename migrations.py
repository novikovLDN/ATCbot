"""
Database Migration System

Manages versioned database schema migrations.
Each migration is applied in a transaction and recorded in schema_migrations table.

STEP 1.4 - SAFE DEPLOY & ROLLBACK GUARANTEES:
- All migrations are backward-compatible → can rollback safely
- No code assumes immediate presence of new DB fields → migrations applied separately
- Code can run against older schema → feature flags or conditional logic
- Rollback assumptions documented in migration comments
"""
import os
import re
import logging
from pathlib import Path
from typing import List, Set, Optional
import asyncpg

logger = logging.getLogger(__name__)

# Путь к папке с миграциями
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def ensure_migrations_table(conn: asyncpg.Connection):
    """
    Создать таблицу schema_migrations, если её нет
    
    Args:
        conn: Соединение с БД
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


async def get_applied_migrations(conn: asyncpg.Connection) -> Set[str]:
    """
    Получить список применённых миграций
    
    Args:
        conn: Соединение с БД
        
    Returns:
        Множество версий применённых миграций
    """
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}


def get_migration_files() -> List[tuple[str, Path]]:
    """
    Получить список файлов миграций, отсортированных по версии
    
    Returns:
        Список кортежей (version, path), отсортированный по version
    """
    if not MIGRATIONS_DIR.exists():
        logger.warning(f"Migrations directory not found: {MIGRATIONS_DIR}")
        return []
    
    migrations = []
    pattern = re.compile(r'^(\d+)_(.+)\.sql$')
    
    for file_path in MIGRATIONS_DIR.glob("*.sql"):
        match = pattern.match(file_path.name)
        if match:
            version = match.group(1)
            migrations.append((version, file_path))
        else:
            logger.warning(f"Migration file name doesn't match pattern: {file_path.name}")
    
    # Сортируем по числовому значению версии (не лексикографически)
    migrations.sort(key=lambda x: int(x[0]))
    
    return migrations


async def apply_migration(conn: asyncpg.Connection, version: str, migration_path: Path) -> bool:
    """
    Применить одну миграцию в транзакции
    
    Args:
        conn: Соединение с БД (уже в транзакции)
        version: Версия миграции (например, "001")
        migration_path: Путь к SQL файлу миграции
        
    Returns:
        True если миграция применена успешно, False если ошибка
        
    Raises:
        Exception: При ошибке выполнения SQL
    """
    try:
        # Читаем SQL из файла
        sql_content = migration_path.read_text(encoding='utf-8')
        
        if not sql_content.strip():
            logger.warning(f"Migration {version} is empty, skipping")
            return True
        
        logger.info(f"Applying migration {version}: {migration_path.name}")
        logger.debug(f"Migration SQL content:\n{sql_content}")
        
        # asyncpg executes multi-statement SQL natively; no custom parser needed
        await conn.execute(sql_content)
        
        # Записываем версию в schema_migrations
        await conn.execute(
            "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT (version) DO NOTHING",
            version
        )
        
        logger.info(f"Migration {version} applied successfully")
        return True
        
    except Exception as e:
        logger.error(f"CRITICAL: Failed to apply migration {version} ({migration_path.name})")
        logger.error(f"Migration SQL:\n{sql_content}")
        logger.exception(f"Error details: {e}")
        raise


async def run_migrations(conn: asyncpg.Connection) -> bool:
    """
    Применить все неприменённые миграции
    
    Args:
        conn: Соединение с БД (должно быть вне транзакции, мы создадим свою)
        
    Returns:
        True если все миграции применены успешно, False если ошибка
    """
    try:
        # Создаём таблицу миграций
        await ensure_migrations_table(conn)
        
        # Получаем список применённых миграций
        applied = await get_applied_migrations(conn)
        logger.info(f"Applied migrations: {sorted(applied)}")
        
        # Получаем список файлов миграций
        migration_files = get_migration_files()
        
        if not migration_files:
            logger.warning("No migration files found")
            return True
        
        logger.info(f"Found {len(migration_files)} migration files")
        
        # Применяем каждую неприменённую миграцию в транзакции
        for version, migration_path in migration_files:
            if version in applied:
                logger.debug(f"Migration {version} already applied, skipping")
                continue
            
            # Применяем миграцию в транзакции
            # Каждая миграция выполняется в отдельной транзакции
            # Если миграция падает, она откатывается, но уже применённые остаются
            try:
                async with conn.transaction():
                    await apply_migration(conn, version, migration_path)
            except Exception as e:
                logger.error(f"CRITICAL: Migration {version} ({migration_path.name}) FAILED")
                logger.error(f"This will prevent database initialization. Fix the migration and retry.")
                logger.exception(f"Migration error: {e}")
                raise  # Пробрасываем исключение, чтобы остановить процесс миграций
        
        logger.info("All migrations applied successfully")
        return True
        
    except Exception as e:
        logger.exception(f"Error running migrations: {e}")
        return False


async def run_migrations_safe(pool: asyncpg.Pool) -> bool:
    """
    Безопасное применение миграций с использованием пула соединений
    
    Args:
        pool: Пул соединений с БД
        
    Returns:
        True если все миграции применены успешно, False если ошибка
    """
    async with pool.acquire() as conn:
        return await run_migrations(conn)

