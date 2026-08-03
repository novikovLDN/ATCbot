"""SystemState должен описывать то, что есть, а не то, чего никогда не было.

Два дефекта.

1. Докстринг модуля утверждал: «Workers read SystemState at iteration start to
   decide skip/continue». Ни один воркер этого не делал никогда — управляющий
   контур у них другой (database.DB_READY плюс feature-флаги). При разборе
   инцидента по такому докстрингу делают вывод, что жёлтый vpn_api остановил
   активации, — а активации всё это время шли.

2. Статус vpn_api вычислялся по XRAY_API_URL / XRAY_API_KEY. Ветка xray снята,
   эти переменные больше не заполняются: дашборд показывал бы вечный жёлтый
   при полностью исправной выдаче. Жёлтый, который горит всегда, перестают
   замечать — и он скрывает настоящую поломку панели.
"""
from pathlib import Path

import pytest

import config
from app.core import system_state
from app.core.system_state import ComponentStatus, recalculate_from_runtime


def test_module_says_plainly_that_workers_do_not_read_it():
    """Ложное утверждение о контроле потока опаснее его отсутствия.

    Старая формулировка в докстринге осталась, но теперь как цитата с
    пометкой, что так никогда не работало. Требуем именно прямого отрицания:
    читающий не должен гадать.
    """
    doc = system_state.__doc__ or ""
    assert "Фоновые воркеры SystemState НЕ читают" in doc


def test_module_names_its_real_consumer():
    """Кто реально читает модуль, должно быть написано прямо."""
    doc = system_state.__doc__ or ""
    assert "дашборд" in doc.lower()
    assert "DB_READY" in doc, "не сказано, чем на самом деле управляются воркеры"


def test_vpn_status_follows_remnawave(monkeypatch):
    """Настроенная панель Remnawave — зелёный vpn_api."""
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    state = recalculate_from_runtime()
    assert state.vpn_api.status == ComponentStatus.HEALTHY


def test_vpn_degrades_when_remnawave_missing(monkeypatch):
    """Ненастроенная панель — жёлтый, но не падение всей системы."""
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", False)
    state = recalculate_from_runtime()
    assert state.vpn_api.status == ComponentStatus.DEGRADED
    assert "Remnawave" in (state.vpn_api.error or "")


def test_vpn_status_ignores_removed_xray_config(monkeypatch):
    """XRAY_* больше не заполняются — они не должны влиять на светофор."""
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True)
    monkeypatch.setattr(config, "XRAY_API_URL", "", raising=False)
    monkeypatch.setattr(config, "XRAY_API_KEY", "", raising=False)
    state = recalculate_from_runtime()
    assert state.vpn_api.status == ComponentStatus.HEALTHY


def test_source_has_no_xray_dependency():
    src = Path("app/core/system_state.py").read_text(encoding="utf-8")
    code_lines = [
        line for line in src.split("\n")
        if "XRAY" in line and not line.strip().startswith("#")
    ]
    assert not code_lines, f"vpn_api снова считается по xray: {code_lines}"


@pytest.mark.parametrize("workers_file", [
    "activation_worker.py", "auto_renewal.py", "reminders.py",
    "fast_expiry_cleanup.py", "trial_notifications.py",
])
def test_workers_really_do_not_use_system_state(workers_file):
    """Проверяем факт, на котором построено решение не подключать модуль.

    Если воркер когда-нибудь начнёт читать SystemState, докстринг придётся
    переписывать обратно — тест об этом напомнит.
    """
    src = Path(workers_file).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.split("\n") if not line.strip().startswith("#")
    )
    assert "system_state" not in code, (
        f"{workers_file} читает SystemState — появился второй источник правды "
        "о том, работать воркеру или нет"
    )
