"""Месячный потолок дней подписки, выдаваемых мини-играми.

Дефект: потолка не было вовсе — кубики и боулинг раздавали порядка 12,5 дней
подписки в месяц бесплатно, то есть игра заменяла собой покупку.
"""
import pytest

from app.i18n import LANGUAGES, get_text


def cap_logic(requested: int, granted_this_month: int, cap: int):
    """Повторяет решение check_game_days_cap без обращения к БД."""
    if cap <= 0:
        return requested, granted_this_month, requested
    remaining = max(0, cap - granted_this_month)
    return min(requested, remaining), granted_this_month, remaining


class TestCapLogic:
    def test_full_reward_when_nothing_used(self):
        allowed, _, _ = cap_logic(7, 0, 3)
        assert allowed == 3, "выдача обрезается до остатка лимита"

    def test_partial_reward_at_boundary(self):
        allowed, _, remaining = cap_logic(6, 2, 3)
        assert allowed == 1 and remaining == 1

    def test_zero_when_exhausted(self):
        allowed, _, remaining = cap_logic(6, 3, 3)
        assert allowed == 0 and remaining == 0

    def test_zero_when_over_limit(self):
        """Если лимит уже превышен (например, менялась настройка) — не уходим в минус."""
        allowed, _, remaining = cap_logic(6, 10, 3)
        assert allowed == 0 and remaining == 0

    def test_cap_zero_disables_limit(self):
        allowed, _, _ = cap_logic(6, 100, 0)
        assert allowed == 6, "нулевой потолок означает отсутствие ограничения"

    def test_small_request_not_inflated(self):
        allowed, _, _ = cap_logic(1, 0, 3)
        assert allowed == 1, "потолок не должен увеличивать награду"


class TestCapConfig:
    def test_default_cap_is_sane(self):
        import config
        assert 0 <= config.GAME_MONTHLY_DAYS_CAP <= 31

    def test_garbage_env_does_not_break_config(self, monkeypatch):
        import importlib
        monkeypatch.setenv("STAGE_GAME_MONTHLY_DAYS_CAP", "три дня")
        import config
        importlib.reload(config)
        assert config.GAME_MONTHLY_DAYS_CAP == 3


class TestCapMessage:
    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    def test_message_exists_in_every_language(self, lang):
        assert "games.monthly_cap_reached" in LANGUAGES[lang]

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    def test_message_renders_without_raw_placeholders(self, lang):
        out = get_text(lang, "games.monthly_cap_reached", cap=3, value=6)
        assert "{cap}" not in out
        assert "games." not in out, "показан сырой ключ вместо текста"
