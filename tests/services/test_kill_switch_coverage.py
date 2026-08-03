"""Аварийный рубильник останавливает все фоновые воркеры, а не половину.

Дефект: FEATURE_BACKGROUND_WORKERS_ENABLED проверяли четыре воркера из
девяти. Напоминания, ферма, монитор трафика, синхронизация с сайтом и
отложенные рассылки продолжали работать как ни в чём не бывало.

Рубильник, останавливающий меньше половины, опаснее его отсутствия: им
пользуются в аварийной ситуации, считая, что фон встал, — и принимают
решения исходя из этого.
"""
import re
from pathlib import Path

import pytest

# Воркер → файл. Девять фоновых задач, зависящих от БД.
WORKERS = {
    "reminders": "reminders.py",
    "auto_renewal": "auto_renewal.py",
    "fast_expiry_cleanup": "fast_expiry_cleanup.py",
    "activation_worker": "activation_worker.py",
    "trial_notifications": "trial_notifications.py",
    "farm_notifications": "app/workers/farm_notifications.py",
    "traffic_monitor": "app/workers/traffic_monitor.py",
    "site_sync": "app/workers/site_sync_worker.py",
    "scheduled_broadcasts": "app/services/scheduled_broadcasts_worker.py",
}


@pytest.mark.parametrize("name, path", sorted(WORKERS.items()))
def test_worker_checks_the_kill_switch(name, path):
    src = Path(path).read_text(encoding="utf-8")
    checks_flag = (
        "background_workers_paused" in src
        or "background_workers_enabled" in src
    )
    assert checks_flag, f"{name}: рубильник не проверяется — воркер не остановить"


@pytest.mark.parametrize("name, path", sorted(WORKERS.items()))
def test_check_is_inside_the_loop_not_at_startup(name, path):
    """Флаг читается из окружения и может смениться без перезапуска.
    Проверка на старте остановила бы воркер только при следующем деплое —
    то есть тогда, когда рубильник уже не нужен."""
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    loop_at = next(
        (i for i, line in enumerate(lines) if re.match(r"^\s*while True:\s*$", line)),
        None,
    )
    assert loop_at is not None, f"{name}: не найден основной цикл"
    # Ищем именно ВЫЗОВ, а не строку импорта: импорт всегда выше цикла.
    check_at = next(
        (i for i, line in enumerate(lines)
         if not line.lstrip().startswith(("from ", "import "))
         and ("background_workers_paused(" in line
              or "feature_flags.background_workers_enabled" in line)),
        None,
    )
    assert check_at is not None and check_at > loop_at, (
        f"{name}: проверка рубильника вне цикла — сработает только при рестарте"
    )


def test_helper_returns_false_when_workers_are_enabled():
    from app.core.feature_flags import background_workers_paused

    assert background_workers_paused("test") is False


def test_helper_pauses_when_flag_is_off(monkeypatch):
    """Ровно тот случай, ради которого рубильник и существует.

    FeatureFlags — frozen dataclass, поэтому подменяем не поле, а сам
    источник флагов: так проверяется путь, которым ходит рабочий код.
    """
    import app.core.feature_flags as ff

    current = ff.get_feature_flags()
    paused = ff.FeatureFlags(
        payments_enabled=current.payments_enabled,
        vpn_provisioning_enabled=current.vpn_provisioning_enabled,
        auto_renewal_enabled=current.auto_renewal_enabled,
        background_workers_enabled=False,
        admin_actions_enabled=current.admin_actions_enabled,
    )
    monkeypatch.setattr(ff, "get_feature_flags", lambda: paused)
    assert ff.background_workers_paused("test") is True
