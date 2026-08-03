"""Каждый тумблер в дашборде должен что-то делать.

Дефект: в реестре автоуведомлений висели ключи, которые код нигде не
запрашивает. Админ видел живой на вид переключатель и поле для текста —
и они не влияли ни на что:

  • subscription.reminder_24h — окно «24 часа» целиком занято REMINDER_1D,
    should_send_reminder не возвращает REMINDER_24H ни в одной ветке, ветка
    в reminders.py была недостижима;
  • trial.reminder_6h — расписание legacy-уведомлений о триале пустое
    (осознанно: дубли летели в тот же слот, что и основные напоминания),
    вместе с ним было мертво ~90 строк кода;
  • referral.reward_notification — текст кешбэка собирается из ШЕСТИ
    i18n-ключей, одним полем им управлять нельзя;
  • gift.activated_welcome — сообщение существовало и отправлялось, но
    брало текст напрямую из i18n, мимо реестра.

Первые три удалены, четвёртый подключён по-настоящему.
"""
import re
from pathlib import Path

import pytest

# Ключи реестра, читаемые из кода этими функциями.
_READERS = ("get_notification_text", "_get_text_impl", "_autonotif_text",
            "is_notification_enabled", "_is_enabled_impl", "_autonotif_enabled")

SEARCH_ROOTS = [Path("app"), Path("reminders.py"), Path("trial_notifications.py")]


def _keys_requested_in_code() -> set:
    """Ключи, которые код действительно спрашивает у реестра."""
    found = set()
    files = []
    for root in SEARCH_ROOTS:
        files.extend(root.rglob("*.py") if root.is_dir() else [root])
    pattern = re.compile(
        r"(?:%s)\(\s*[\"']([\w.]+)[\"']" % "|".join(_READERS)
    )
    for f in files:
        if "automated_notifications" in str(f):
            continue  # сам реестр не считается потребителем
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found |= set(pattern.findall(text))
        # Ключи, собираемые в переменную: _key = "subscription.reminder_3d"
        found |= set(re.findall(r'_key\s*=\s*["\']([\w.]+)["\']', text))
        found |= set(re.findall(r'notif_key\s*=\s*["\']([\w.]+)["\']', text))
        # И объявленные в конфиге как значение поля: так приходит
        # trial.notification_71h из get_final_reminder_config().
        found |= set(re.findall(r'"notification_key":\s*["\']([\w.]+)["\']', text))
    return found


def test_every_registered_key_is_actually_used():
    from app.services.automated_notifications import REGISTRY

    unused = sorted(set(REGISTRY) - _keys_requested_in_code())
    assert not unused, (
        "в дашборде появятся тумблеры, которые ничего не делают: "
        f"{unused}"
    )


@pytest.mark.parametrize("key", [
    "subscription.reminder_24h",
    "trial.reminder_6h",
    "referral.reward_notification",
])
def test_dead_keys_stay_removed(key):
    from app.services.automated_notifications import REGISTRY

    assert key not in REGISTRY, f"мёртвый тумблер {key} вернулся"


def test_gift_welcome_goes_through_the_registry():
    """Тумблер должен влиять на текст, а не висеть для вида."""
    src = Path("app/handlers/user/start.py").read_text(encoding="utf-8")
    block = src[src.index("gift.activated_welcome") - 700:]
    block = block[: block.index("gift.activated_welcome") + 400]
    assert "_autonotif_text" in block, "подарочное приветствие идёт мимо реестра"


def test_unreachable_reminder_branch_is_gone():
    """Ветка REMINDER_24H не могла выполниться: окно занято REMINDER_1D."""
    src = Path("reminders.py").read_text(encoding="utf-8")
    assert "ReminderType.REMINDER_24H" not in src


def test_dead_trial_schedule_code_is_gone():
    """Расписание пустое, значит цикл по нему и фаза 2+3 недостижимы."""
    src = Path("trial_notifications.py").read_text(encoding="utf-8")
    assert "for notification in TRIAL_NOTIFICATION_SCHEDULE" not in src
    assert "pending_notifications" not in src
