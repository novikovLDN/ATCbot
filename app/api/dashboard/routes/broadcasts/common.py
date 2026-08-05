"""Рассылки: две мелочи, нужные почти всем модулям пакета.

ЧТО ЗДЕСЬ
    `_get_bot` — живой aiogram Bot, который main.py кладёт в
    telegram_webhook при старте; `_serialize` — приведение строки БД к
    JSON-совместимому виду.

ПОЧЕМУ ВЫДЕЛЕНО
    Обе нужны и читающим эндпоинтам, и отправке, и планировщику.
    Оставить их в любом из них — значит завязать три модуля друг на друга
    ради десяти строк.

ЧТО ЛЕГКО СЛОМАТЬ
    `_get_bot` берёт бота через getattr на модуле, а не импортом имени:
    во время деплоя `_bot` ещё None, и импорт-время-снимок навсегда
    запомнил бы None. Отсюда же 503 вместо AttributeError.
"""
from __future__ import annotations

from fastapi import HTTPException


def _get_bot():
    """Pull the live aiogram Bot from the telegram_webhook module —
    set there by main.py at startup. Raises 503 if it isn't ready
    yet (extremely rare after startup but possible during deploy)."""
    from app.api import telegram_webhook
    bot = getattr(telegram_webhook, "_bot", None)
    if bot is None:
        raise HTTPException(503, "bot_not_ready")
    return bot


def _serialize(row) -> dict:
    if not isinstance(row, dict):
        return {}
    out: dict = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            continue
        else:
            out[k] = v
    return out
