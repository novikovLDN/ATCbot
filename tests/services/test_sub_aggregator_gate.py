"""Гейт выдачи sub-aggregator — is_enabled_for().

Проверяет РАЗВЯЗКУ: выдача единой ссылки (is_enabled_for) отключается
независимо от эндпоинта /a/{token} (SUB_AGGREGATOR_ENABLED, монтируется
отдельно в app/api/__init__.py). Контракт «отключаем агрегатор, но уже
выданные ссылки у всех работают» = ISSUE_ENABLED=False при ENABLED=True.
"""
import config
from app.services import sub_aggregator as svc


def _set(monkeypatch, *, enabled=True, issue=True, admin_only=False,
         url="https://sub.example", admin_id=1):
    monkeypatch.setattr(config, "SUB_AGGREGATOR_ENABLED", enabled, raising=False)
    monkeypatch.setattr(config, "SUB_AGGREGATOR_ISSUE_ENABLED", issue, raising=False)
    monkeypatch.setattr(config, "SUB_AGGREGATOR_ADMIN_ONLY", admin_only, raising=False)
    monkeypatch.setattr(config, "SUB_AGGREGATOR_URL", url, raising=False)
    monkeypatch.setattr(config, "ADMIN_TELEGRAM_ID", admin_id, raising=False)


def test_issue_disabled_blocks_everyone_incl_admin(monkeypatch):
    # ISSUE_ENABLED=False → выдача выключена для ВСЕХ (в т.ч. админа),
    # но ENABLED=True → эндпоинт жив (existing links работают).
    _set(monkeypatch, enabled=True, issue=False, admin_id=777)
    assert svc.is_enabled_for(777) is False   # даже админ
    assert svc.is_enabled_for(12345) is False


def test_issue_enabled_serves_all(monkeypatch):
    _set(monkeypatch, enabled=True, issue=True, admin_only=False)
    assert svc.is_enabled_for(12345) is True


def test_master_kill_switch_blocks(monkeypatch):
    # ENABLED=False → всё выключено (и эндпоинт бы не смонтировался).
    _set(monkeypatch, enabled=False, issue=True)
    assert svc.is_enabled_for(12345) is False


def test_admin_only_still_respected_when_issuing(monkeypatch):
    # Если выдача включена, ADMIN_ONLY по-прежнему сужает до админа.
    _set(monkeypatch, enabled=True, issue=True, admin_only=True, admin_id=42)
    assert svc.is_enabled_for(42) is True
    assert svc.is_enabled_for(99) is False


def test_missing_issue_flag_defaults_enabled(monkeypatch):
    # Обратная совместимость: старый конфиг без ISSUE_ENABLED → getattr дефолт True.
    _set(monkeypatch, enabled=True, issue=True, admin_only=False)
    monkeypatch.delattr(config, "SUB_AGGREGATOR_ISSUE_ENABLED", raising=False)
    assert svc.is_enabled_for(12345) is True
