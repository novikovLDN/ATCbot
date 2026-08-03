"""Мелкие помощники экранов трафика, нужные сразу нескольким модулям.

ПОЧЕМУ ОТДЕЛЬНО
    Форматирование объёма и полоса прогресса нужны экрану расхода, а
    автоудаление сообщения Lava — обоим checkout-модулям. Держать их внутри
    одного из экранов значит заставить соседа импортировать чужой модуль
    целиком и получить кольцо импортов.

ЧТО ЛЕГКО СЛОМАТЬ
    _strikethrough клеит U+0336 к каждому символу — это единственный способ
    показать зачёркнутую старую цену в подписи КНОПКИ: HTML-разметка внутри
    подписи кнопки Telegram не работает, теги вылезут пользователю как текст.
"""
import asyncio

LAVA_INVOICE_TIMEOUT = 15 * 60  # 15 minutes


async def _auto_delete_lava_msg(bot, chat_id: int, msg):
    """Delete Lava invoice message after timeout."""
    try:
        await asyncio.sleep(LAVA_INVOICE_TIMEOUT)
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass


def _strikethrough(text: str) -> str:
    """Apply Unicode strikethrough to text (works in Telegram button labels)."""
    return "".join(ch + "\u0336" for ch in str(text))


def _format_bytes(b: int) -> str:
    """Format bytes to human-readable GB/MB string."""
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} ГБ"
    if b >= 1024**2:
        return f"{b / 1024**2:.0f} МБ"
    return f"{b / 1024:.0f} КБ"


def _progress_bar(used: int, limit: int, length: int = 10) -> str:
    if limit <= 0:
        return "🤍" * length
    ratio = min(used / limit, 1.0)
    filled = int(ratio * length)
    return "🤍" * filled + "🩶" * (length - filled)
