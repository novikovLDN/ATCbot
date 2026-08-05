"""Мелкие помощники экранов трафика, нужные сразу нескольким модулям.

ПОЧЕМУ ОТДЕЛЬНО
    Форматирование объёма и полоса прогресса нужны и экрану расхода, и обоим
    checkout-модулям. Держать их внутри одного из экранов значит заставить
    соседа импортировать чужой модуль целиком и получить кольцо импортов.

ЧЕГО ЗДЕСЬ БОЛЬШЕ НЕТ
    Тут жила пара LAVA_INVOICE_TIMEOUT + _auto_delete_lava_msg — точный
    близнец общего _schedule_invoice_deletion, только со своей константой
    15 минут и без записи INVOICE_EXPIRED в лог. Оба checkout-модуля теперь
    зовут app.handlers.callbacks._invoice_cleanup напрямую. Не заводите
    здесь автоудаление заново: срок жизни счёта задаёт
    config.INVOICE_TIMEOUT_SECONDS, и его же lava_service шлёт провайдеру
    в поле expire — вторая константа рядом означает, что сообщение и счёт
    протухают в разное время.

ЧТО ЛЕГКО СЛОМАТЬ
    _strikethrough клеит U+0336 к каждому символу — это единственный способ
    показать зачёркнутую старую цену в подписи КНОПКИ: HTML-разметка внутри
    подписи кнопки Telegram не работает, теги вылезут пользователю как текст.
"""


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
