"""
Runtime helper для автоуведомлений.

- sync_registry_to_db() — upsert defaults при старте бота.
- get_notification_text(key, lang, **params) — вернуть готовый
  HTML-текст или None если отключено.
- is_notification_enabled(key) — быстрый bool-чек без рендера.
- log_notification_send(key, telegram_id, status=...) — метрика.
- get_trigger_config(key) — dict для reminder-логики (allows
  админу подвинуть окно отправки без редиплоя).

Кэш: помним состояние в памяти на 30с, чтобы не бить БД на каждом
чеке в reminder-loop. Cache invalidation через touch_cache() —
вызывается из API при PATCH.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from database.core import get_pool

from .registry import REGISTRY, NotificationSpec

logger = logging.getLogger(__name__)

# In-memory cache — {key: {is_enabled, text, trigger_config}}.
# invalidated через touch_cache() при админ-правке.
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL_SEC = 30.0


def touch_cache() -> None:
    """Сбросить кэш (вызывается из API endpoint'а при PATCH)."""
    global _CACHE_TS, _CACHE
    _CACHE_TS = 0.0
    _CACHE = {}


async def sync_registry_to_db() -> int:
    """Upsert всех specs из REGISTRY в БД.

    Правила слияния:
      - Если строки нет → INSERT со всеми полями из spec.
      - Если строка есть → обновляется ТОЛЬКО default_text_ru + title +
        description + category + template_vars + default в trigger_config
        (если админ ещё не менял trigger_config, оставляем дефолт;
        сравнение по ключам верхнего уровня).
      - custom_text_ru, is_enabled, кастомные trigger_config-ключи
        НЕ трогаются — админ-правки в приоритете.

    Возвращает количество upsert'нутых строк.
    """
    pool = await get_pool()
    if pool is None:
        return 0
    count = 0
    async with pool.acquire() as conn:
        for spec in REGISTRY.values():
            existing = await conn.fetchrow(
                "SELECT trigger_config FROM automated_notifications WHERE key = $1",
                spec.key,
            )
            if existing is None:
                await conn.execute(
                    """INSERT INTO automated_notifications
                            (key, title, description, category,
                             default_text_ru, template_vars, trigger_config)
                       VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
                    spec.key, spec.title, spec.description, spec.category,
                    spec.default_text_ru, list(spec.template_vars),
                    _to_json(spec.default_trigger),
                )
            else:
                # Обновляем метаданные + default. trigger_config пропускаем,
                # чтобы не затирать возможные админ-правки.
                await conn.execute(
                    """UPDATE automated_notifications
                       SET title = $2,
                           description = $3,
                           category = $4,
                           default_text_ru = $5,
                           template_vars = $6
                       WHERE key = $1""",
                    spec.key, spec.title, spec.description, spec.category,
                    spec.default_text_ru, list(spec.template_vars),
                )
            count += 1
    logger.info("automated_notifications: upserted %d specs", count)
    touch_cache()
    return count


def _to_json(d: Dict[str, Any]) -> str:
    import json
    return json.dumps(d, ensure_ascii=False)


async def _load_cache() -> None:
    """Полная загрузка cache из БД (все ключи)."""
    global _CACHE, _CACHE_TS
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT key, is_enabled, custom_text_ru, default_text_ru,
                      trigger_config
               FROM automated_notifications""",
        )
    _CACHE = {
        r["key"]: {
            "is_enabled": bool(r["is_enabled"]),
            "text": r["custom_text_ru"] or r["default_text_ru"],
            "trigger_config": r["trigger_config"] or {},
        }
        for r in rows
    }
    _CACHE_TS = time.monotonic()


async def _ensure_cache() -> None:
    if time.monotonic() - _CACHE_TS > _CACHE_TTL_SEC:
        try:
            await _load_cache()
        except Exception as e:
            logger.warning("automated_notifications cache load failed: %s", e)


async def get_row(key: str) -> Optional[Dict[str, Any]]:
    """Прочитать одну строку. С учётом кэша."""
    await _ensure_cache()
    return _CACHE.get(key)


async def is_notification_enabled(key: str) -> bool:
    """Быстрый чек для reminder-логики (можно ли отправлять).

    Если ключ не зарегистрирован в БД (например, миграция ещё не
    прошла) → True (fallback на код-дефолт, чтобы не сломать
    работающего бота).
    """
    row = await get_row(key)
    if row is None:
        return True
    return row["is_enabled"]


async def get_notification_text(
    key: str, *, language: str = "ru", params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Вернуть готовый текст (с str.format) или None если отключено.

    ВАЖНО про язык. В таблице automated_notifications хранится ТОЛЬКО
    русский текст: и default_text_ru, и админский custom_text_ru. Поэтому
    для нерусских пользователей эта функция ничего не отдаёт — вызывающий
    код обязан взять перевод из i18n.

    Почему это отдельное правило: раньше параметра language не было, и
    типовой вызов `(await get_notification_text(key)) or i18n.get_text(lang, ...)`
    никогда не доходил до правой части. Строка в БД существует всегда
    (sync_registry_to_db создаёт её при старте бота), значит выражение слева
    не None — и все автонапоминания уходили на русском независимо от языка
    пользователя. Английский и остальные локали были мёртвым кодом.

    Fallback: если ключа нет в БД или что-то сломалось, берём default_text_ru
    из REGISTRY, чтобы не крашить bot-код.

    Args:
        language: язык получателя. Всё, кроме 'ru', → None (текст берётся
            из i18n вызывающим кодом).
    """
    if (language or "ru").strip().lower() != "ru":
        return None
    row = await get_row(key)
    if row is not None and not row["is_enabled"]:
        return None
    if row is not None:
        text = row["text"]
    else:
        spec = REGISTRY.get(key)
        text = spec.default_text_ru if spec else None
    if text is None:
        return None
    if params:
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError) as e:
            logger.warning(
                "automated_notifications render failed key=%s err=%s", key, e,
            )
            return text  # без подстановки лучше, чем None
    return text


async def get_trigger_config(key: str) -> Dict[str, Any]:
    """Вернуть trigger_config (с fallback на дефолт из REGISTRY)."""
    row = await get_row(key)
    if row is not None and row["trigger_config"]:
        return dict(row["trigger_config"])
    spec = REGISTRY.get(key)
    return dict(spec.default_trigger) if spec else {}


async def is_user_in_segment(telegram_id: int, segment_key: str) -> bool:
    """Проверить, входит ли юзер в admin-сегмент (из broadcasts.segments_list).

    Используется в reminder-логике для опционального segment_filter
    (например «шлём только тем, у кого нет активной подписки»).
    Реализация: делегируем к database.get_users_by_segment — тот
    возвращает список ID; ищем в нём. Для реально больших сегментов
    (100k+) стоит переделать на dedicated SQL-check, но пока
    достаточно.

    Fail-open: если сегмент неизвестен или произошла ошибка чтения,
    возвращаем True (не блокируем отправку из-за проблем валидации).
    """
    if not segment_key:
        return True
    try:
        import database
        ids = await database.get_users_by_segment(segment_key)
        return int(telegram_id) in {int(x) for x in ids}
    except Exception as e:
        logger.warning(
            "is_user_in_segment failed segment=%s user=%s: %s",
            segment_key, telegram_id, e,
        )
        return True


async def log_notification_send(
    key: str, telegram_id: int, *,
    status: str = "sent", error: Optional[str] = None,
) -> None:
    """Записать факт отправки в automated_notification_sends.

    status: 'sent' | 'failed' | 'skipped_disabled' | 'blocked'
    """
    pool = await get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO automated_notification_sends
                        (key, telegram_id, status, error)
                   VALUES ($1, $2, $3, $4)""",
                key, telegram_id, status, error,
            )
    except Exception as e:
        # Не крашим bot если лог не записался.
        logger.warning("log_notification_send failed key=%s: %s", key, e)


async def get_stats(key: str, hours: int = 168) -> Dict[str, int]:
    """Статистика по ключу за N часов: {sent, failed, blocked, skipped}."""
    pool = await get_pool()
    if pool is None:
        return {"sent": 0, "failed": 0, "blocked": 0, "skipped": 0}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT
                    COUNT(*) FILTER (WHERE status = 'sent')             AS sent,
                    COUNT(*) FILTER (WHERE status = 'failed')           AS failed,
                    COUNT(*) FILTER (WHERE status = 'blocked')          AS blocked,
                    COUNT(*) FILTER (WHERE status = 'skipped_disabled') AS skipped
                FROM automated_notification_sends
                WHERE key = $1
                  AND sent_at >= NOW() - INTERVAL '{int(hours)} hours'""",
            key,
        )
    return {
        "sent": int(row["sent"] or 0),
        "failed": int(row["failed"] or 0),
        "blocked": int(row["blocked"] or 0),
        "skipped": int(row["skipped"] or 0),
    }


async def update_notification(
    key: str, *,
    custom_text_ru: Optional[str] = None,
    is_enabled: Optional[bool] = None,
    trigger_config: Optional[Dict[str, Any]] = None,
    edited_by: Optional[int] = None,
) -> bool:
    """PATCH одного ключа. Возвращает True если строка найдена и обновлена.

    Передавай только то поле что хочешь изменить (None = не трогаем).
    Для reset custom_text — передай пустую строку.
    """
    pool = await get_pool()
    if pool is None:
        return False
    fields: list[str] = []
    args: list[Any] = []
    idx = 2  # $1 будет key
    if custom_text_ru is not None:
        fields.append(f"custom_text_ru = ${idx}")
        # Пустая строка → NULL (reset к default).
        args.append(custom_text_ru if custom_text_ru else None)
        idx += 1
    if is_enabled is not None:
        fields.append(f"is_enabled = ${idx}")
        args.append(bool(is_enabled))
        idx += 1
    if trigger_config is not None:
        fields.append(f"trigger_config = ${idx}::jsonb")
        args.append(_to_json(trigger_config))
        idx += 1
    if edited_by is not None:
        fields.append(f"last_edited_by = ${idx}")
        args.append(int(edited_by))
        idx += 1
    fields.append("updated_at = NOW()")
    if not fields:
        return False
    async with pool.acquire() as conn:
        res = await conn.execute(
            f"""UPDATE automated_notifications
                SET {', '.join(fields)}
                WHERE key = $1""",
            key, *args,
        )
    touch_cache()
    return res.startswith("UPDATE ") and res != "UPDATE 0"
