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

# Пул животных. Каждая капча берёт случайные 4 из этого списка,
# один из четырёх — expected (тот, чьё фото и подпись «Кот»/«Волк»/…
# показывается в тексте изображения). Юзер выбирает соответствующую
# кнопку из четырёх.
#
# key       — стабильный ASCII-идентификатор (уходит в callback_data)
# emoji     — не показываем в тексте, оставили для будущей отладки/логов
# name      — русское имя, отображается на кнопке
# photo_id  — Telegram file_id заранее загруженной картинки для этой цели
#             (создание видел в чате: см. https://t.me/AtlasSecure на 1:42
#             от 18.08.2026). Все file_id получены при аплоаде на
#             prod-бота, устойчивы к рестартам.
_ANIMALS: list[tuple[str, str, str, str]] = [
    ("bear",   "🐻", "Медведь",  "AgACAgQAAxkBAAGDLS5qhYhZRo-5B6K_vdS3AAH3-KdF-PYAApsPaxtYFzBQEB7MPSm-1SkBAAMCAAN3AAM9BA"),
    ("rabbit", "🐰", "Кролик",   "AgACAgQAAxkBAAGDLTBqhYhiCpA4gVR3jMmLWFtCdzbWrwACnA9rG1gXMFBs4nT73ujyhAEAAwIAA3cAAz0E"),
    ("wolf",   "🐺", "Волк",     "AgACAgQAAxkBAAGDLTJqhYhm-UQzLLfMq-lCIpJItEd1TQACnQ9rG1gXMFBkgvIy7UUEAgEAAwIAA3cAAz0E"),
    ("cat",    "🐱", "Кот",      "AgACAgQAAxkBAAGDLTZqhYhxw1h3PfPXSMS0GQHrcYy_agACnw9rG1gXMFA10tquCwABPFcBAAMCAAN3AAM9BA"),
    ("fox",    "🦊", "Лиса",     "AgACAgQAAxkBAAGDLThqhYh22bzPNwFGVZLkJ4hgIKe8cAACoA9rG1gXMFDXp0MrxGi-LQEAAwIAA3cAAz0E"),
    ("dog",    "🐶", "Собака",   "AgACAgQAAxkBAAGDLTpqhYh-TegVb06U_Vngb2hbx54BLgACoQ9rG1gXMFDNfRzngp_DuwEAAwIAA3cAAz0E"),
    ("frog",   "🐸", "Лягушка",  "AgACAgQAAxkBAAGDLTxqhYiGa9jWjZO-0x-3s4EskyFQ9gACog9rG1gXMFBPmxBHUZjCUAEAAwIAA3cAAz0E"),
]

_ANIMALS_BY_KEY: dict[str, tuple[str, str, str, str]] = {a[0]: a for a in _ANIMALS}

MAX_ATTEMPTS = 4           # ошибок подряд
COOLDOWN_SEC = 60           # длительность лока после MAX_ATTEMPTS (1 минута)


@dataclass
class Challenge:
    """Одна выданная капча — что показывать + expected-ключ."""
    expected_key: str
    expected_emoji: str
    expected_name: str
    expected_photo_id: str
    options: list[tuple[str, str, str, str]]  # 4 из _ANIMALS, включая expected


def build_challenge() -> Challenge:
    """Собрать новую капчу: 4 случайных, один — expected."""
    options = random.sample(_ANIMALS, 4)
    expected = random.choice(options)
    return Challenge(
        expected_key=expected[0],
        expected_emoji=expected[1],
        expected_name=expected[2],
        expected_photo_id=expected[3],
        options=options,
    )


def render_prompt_text(challenge: Challenge) -> str:
    """Caption под фото капчи. Имя цели дублируем в текст жирным для
    страховки — на случай если картинка не подгрузилась / read-only-
    режим / VoiceOver. HTML-экранирование не требуется — имена в
    _ANIMALS зашиты кодом, спецсимволов не содержат."""
    return (
        f"🤖 Пожалуйста, выберите <b>{challenge.expected_name}</b> "
        "из списка ниже, чтобы подтвердить, что вы не робот."
    )


def render_keyboard(challenge: Challenge) -> InlineKeyboardMarkup:
    """4 кнопки-варианта. callback_data = captcha:{expected}:{chosen}.
    Все кнопки одного стиля danger — правильную не выделяем, иначе
    капча теряет anti-bot смысл (бот считывает style)."""
    rows: list[list[InlineKeyboardButton]] = []
    for key, _emoji, name, _photo in challenge.options:
        rows.append([InlineKeyboardButton(
            text=name,
            callback_data=f"captcha:{challenge.expected_key}:{key}",
            style="danger",
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
