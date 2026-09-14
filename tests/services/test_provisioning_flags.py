"""USE_NEW_PROVISIONING / NEW_PROVISIONING_ENTRYPOINTS flag semantics."""
import pytest

import config
from app.services import provisioning_flags as pf

MODE_VAR = f"{config.APP_ENV.upper()}_USE_NEW_PROVISIONING"
EP_VAR = f"{config.APP_ENV.upper()}_NEW_PROVISIONING_ENTRYPOINTS"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(MODE_VAR, raising=False)
    monkeypatch.delenv(EP_VAR, raising=False)
    pf._warned.clear()


def test_default_is_on_everywhere():
    """Owner decision 2026-09-14: the new core is the default; "off" is a kill-switch."""
    assert pf.global_mode() == "on"
    for ep in pf.ALL_ENTRYPOINTS:
        assert pf.mode_for(ep) == "on"
        assert pf.is_on(ep) is True


def test_off_is_an_emergency_kill_switch(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "off")
    assert pf.global_mode() == "off"
    for ep in pf.ALL_ENTRYPOINTS:
        assert pf.is_on(ep) is False


def test_on_without_list_enables_every_entrypoint(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    for ep in pf.ALL_ENTRYPOINTS:
        assert pf.is_on(ep) is True


def test_on_with_list_enables_only_listed(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "ON")
    monkeypatch.setenv(EP_VAR, " webhook , telegram ")
    assert pf.is_on("webhook") and pf.is_on("telegram")
    assert pf.mode_for("balance") == "off"
    assert pf.is_on("autorenew") is False


def test_shadow_is_reported_but_not_on(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "shadow")
    assert pf.mode_for("webhook") == "shadow"
    assert pf.is_on("webhook") is False


def test_invalid_mode_falls_back_to_the_default_on(monkeypatch, caplog):
    monkeypatch.setenv(MODE_VAR, "yes")
    assert pf.global_mode() == "on"
    assert pf.is_on("webhook") is True
    assert "invalid" in caplog.text


def test_unknown_entries_are_ignored(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "webhook,bogus")
    assert pf.enabled_entrypoints() == frozenset({"webhook"})
    assert pf.is_on("gift") is False


def test_only_unknown_entries_enable_nothing(monkeypatch):
    monkeypatch.setenv(MODE_VAR, "on")
    monkeypatch.setenv(EP_VAR, "bogus")
    assert pf.enabled_entrypoints() == frozenset()
    assert not any(pf.is_on(ep) for ep in pf.ALL_ENTRYPOINTS)


def test_unknown_entrypoint_name_is_a_programming_error():
    with pytest.raises(ValueError):
        pf.mode_for("webhooks")
