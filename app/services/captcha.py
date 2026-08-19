"""Anti-bot капча перед выбором языка на /start.

Логика:
  - Юзер жмёт /start
  - Если users.captcha_passed_at NOT NULL — пропускаем сразу к языку
  - Иначе показываем 4 кнопки-эмодзи, одну из которых надо нажать
  - При успехе — mark_passed(tg_id) + продолжение flow (язык)
  - При ошибке — новая капча (случайная новая цель)
  - Лимит попыток: MAX_ATTEMPTS ошибок за COOLDOWN_SEC → «попробуй позже»

Callback-контракт: `captcha:{expected}:{chosen}` — expected хранится
прямо в кнопке, не в FSM, чтобы устоять при рестартах бота.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Пул эмодзи-животных. Каждая капча берёт случайные 4 из этого списка.
# key — стабильный ASCII-идентификатор (уезжает в callback_data, ≤64 байт).
_ANIMALS: list[tuple[str, str, str]] = [
    ("frog",   "🐸", "Лягушка"),
    ("dog",    "🐶", "Собака"),
    ("fox",    "🦊", "Лиса"),
    ("cat",    "🐱", "Кошка"),
    ("bear",   "🐻", "Медведь"),
    ("wolf",   "🐺", "Волк"),
    ("rabbit", "🐰", "Кролик"),
    ("panda",  "🐼", "Панда"),
]

_ANIMALS_BY_KEY: dict[str, tuple[str, str, str]] = {a[0]: a for a in _ANIMALS}

MAX_ATTEMPTS = 4           # ошибок подряд
COOLDOWN_SEC = 60           # длительность лока после MAX_ATTEMPTS (1 минута)


@dataclass
class Challenge:
    """Одна выданная капча — что показывать + expected-ключ."""
    expected_key: str
    expected_emoji: str
    expected_name: str
    options: list[tuple[str, str, str]]  # 4 из _ANIMALS, включая expected


def build_challenge() -> Challenge:
    """Собрать новую капчу: 4 случайных, один — expected."""
    options = random.sample(_ANIMALS, 4)
    expected = random.choice(options)
    return Challenge(
        expected_key=expected[0],
        expected_emoji=expected[1],
        expected_name=expected[2],
        options=options,
    )


def render_prompt_text(challenge: Challenge) -> str:
    """Готовая HTML-строка для caption/text капчи."""
    return (
        f"🤖 Пожалуйста, выберите {challenge.expected_emoji} "
        f"<b>{challenge.expected_name}</b> из списка ниже, чтобы "
        f"подтвердить, что вы не робот."
    )


def render_keyboard(challenge: Challenge) -> InlineKeyboardMarkup:
    """4 кнопки-варианта. callback_data = captcha:{expected}:{chosen}."""
    rows: list[list[InlineKeyboardButton]] = []
    for key, emoji, name in challenge.options:
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {name}",
            callback_data=f"captcha:{challenge.expected_key}:{key}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_callback(data: str) -> Optional[tuple[str, str]]:
    """captcha:frog:dog → ('frog', 'dog'). None если формат кривой."""
    if not data or not data.startswith("captcha:"):
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    expected, chosen = parts[1], parts[2]
    if expected not in _ANIMALS_BY_KEY or chosen not in _ANIMALS_BY_KEY:
        return None
    return expected, chosen


# ── Rate limit / lockout (Redis, in-memory fallback) ─────────────────

_MEM_ATTEMPTS: dict[int, int] = {}
_MEM_LOCKED_UNTIL: dict[int, float] = {}


async def _redis():
    try:
        from app.utils.redis_client import get_redis, is_configured
        if not is_configured():
            return None
        return await get_redis()
    except Exception:
        return None


def _lock_key(tg: int) -> str:
    return f"captcha:lock:{tg}"


def _fail_key(tg: int) -> str:
    return f"captcha:fails:{tg}"


async def is_locked(telegram_id: int) -> Optional[int]:
    """Сколько секунд осталось до разлока, или None если не залочен."""
    r = await _redis()
    if r is not None:
        try:
            ttl = await r.ttl(_lock_key(telegram_id))
            if ttl and ttl > 0:
                return int(ttl)
        except Exception as e:
            logger.warning("captcha lock ttl read failed: %s", e)
    # fallback
    import time
    until = _MEM_LOCKED_UNTIL.get(telegram_id, 0.0)
    remain = int(until - time.monotonic())
    return remain if remain > 0 else None


async def register_failure(telegram_id: int) -> tuple[int, bool]:
    """Инкремент счётчика ошибок. Возвращает (attempts_so_far, is_now_locked).

    Хиты за COOLDOWN_SEC. При достижении MAX_ATTEMPTS ставим lock-ключ
    и сбрасываем счётчик.
    """
    r = await _redis()
    if r is not None:
        try:
            key = _fail_key(telegram_id)
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, COOLDOWN_SEC)
            if n >= MAX_ATTEMPTS:
                await r.set(_lock_key(telegram_id), "1", ex=COOLDOWN_SEC)
                await r.delete(key)
                return int(n), True
            return int(n), False
        except Exception as e:
            logger.warning("captcha register_failure redis failed: %s", e)
    # in-memory fallback
    import time
    n = _MEM_ATTEMPTS.get(telegram_id, 0) + 1
    _MEM_ATTEMPTS[telegram_id] = n
    if n >= MAX_ATTEMPTS:
        _MEM_LOCKED_UNTIL[telegram_id] = time.monotonic() + COOLDOWN_SEC
        _MEM_ATTEMPTS.pop(telegram_id, None)
        return n, True
    return n, False


async def reset_failures(telegram_id: int) -> None:
    """При успехе — обнулить счётчик."""
    r = await _redis()
    if r is not None:
        try:
            await r.delete(_fail_key(telegram_id))
        except Exception:
            pass
    _MEM_ATTEMPTS.pop(telegram_id, None)


# ── Persisted «passed» flag on users.captcha_passed_at ───────────────

async def has_passed(telegram_id: int) -> bool:
    """True если users.captcha_passed_at IS NOT NULL. При ошибках БД
    возвращаем True (fail-open) — не хотим лочить юзеров из-за проблем
    с базой на самом входе."""
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return True
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT captcha_passed_at FROM users WHERE telegram_id = $1",
                telegram_id,
            )
            if not row:
                # Юзера ещё нет — создадим при первом успехе. До капчи
                # /start сам вызывает create_user, поэтому редкий случай:
                # если запись до захода в капчу не появилась — считаем
                # не-пройденной, покажем капчу.
                return False
            return row["captcha_passed_at"] is not None
    except Exception as e:
        logger.warning("captcha has_passed read failed tg=%s: %s", telegram_id, e)
        return True


async def mark_passed(telegram_id: int) -> None:
    """Проставить users.captcha_passed_at = NOW() если ещё не стоял."""
    try:
        import database
        pool = await database.get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET captcha_passed_at = NOW() "
                "WHERE telegram_id = $1 AND captcha_passed_at IS NULL",
                telegram_id,
            )
    except Exception as e:
        logger.warning("captcha mark_passed failed tg=%s: %s", telegram_id, e)
