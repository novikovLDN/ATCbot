"""Опознание своих сущностей в панели Remnawave.

Дефект: сущности, созданные до перехода на Remnawave, не имеют ни поля
telegramId, ни маркера в описании. Панель отвечала «имя занято», код считал
запись чужой и отказывал с conflict_unrelated_user — выдача таким
пользователям падала, хотя это была ровно та же самая запись бота.
"""
import pytest

from app.services.remnawave_bypass import _is_our_entity as bypass_is_ours
from app.services.remnawave_bypass import build_bypass_username
from app.services.remnawave_premium import _is_our_entity as premium_is_ours
from app.services.remnawave_premium import build_premium_username

TG = 424242


class TestBypassOwnership:
    def test_matches_by_telegram_id(self):
        assert bypass_is_ours({"telegramId": TG}, TG) is True

    def test_matches_by_snake_case_field(self):
        """Панель отдаёт поле по-разному в зависимости от версии."""
        assert bypass_is_ours({"telegram_id": TG}, TG) is True

    def test_matches_legacy_entity_by_username(self):
        """Главный дефект: легаси-запись без telegramId и без маркера."""
        entity = {"username": build_bypass_username(TG), "description": ""}
        assert bypass_is_ours(entity, TG) is True

    def test_matches_by_description_marker(self):
        assert bypass_is_ours({"description": "Bypass via bot"}, TG) is True

    def test_foreign_entity_rejected(self):
        """Чужую запись принимать нельзя — иначе перезапишем её."""
        entity = {"telegramId": 999999, "username": "someone_else", "description": ""}
        assert bypass_is_ours(entity, TG) is False

    def test_username_of_another_user_rejected(self):
        entity = {"username": build_bypass_username(999999), "description": ""}
        assert bypass_is_ours(entity, TG) is False

    def test_explicit_foreign_owner_beats_matching_username(self):
        """Имя наше, но владелец указан явно и он чужой — запись чужая.

        Имя мог занять админ вручную; перезапись отобрала бы доступ
        у другого человека.
        """
        entity = {"username": build_bypass_username(TG), "telegramId": 999999}
        assert bypass_is_ours(entity, TG) is False

    @pytest.mark.parametrize("bad", [None, "", 42, []])
    def test_non_dict_is_safe(self, bad):
        assert bypass_is_ours(bad, TG) is False


class TestPremiumOwnership:
    def test_matches_by_telegram_id(self):
        assert premium_is_ours({"telegramId": TG}, TG) is True

    def test_matches_legacy_entity_by_username(self):
        entity = {"username": build_premium_username(TG), "description": ""}
        assert premium_is_ours(entity, TG) is True

    def test_matches_by_import_marker(self):
        assert premium_is_ours({"description": "imported from samopis"}, TG) is True

    def test_foreign_entity_rejected(self):
        entity = {"telegramId": 111111, "username": "other", "description": ""}
        assert premium_is_ours(entity, TG) is False

    def test_broken_telegram_id_does_not_crash(self):
        assert premium_is_ours({"telegramId": "не-число"}, TG) is False

    def test_explicit_foreign_owner_beats_matching_username(self):
        entity = {"username": build_premium_username(TG), "telegramId": 999999,
                  "description": "manually created by admin"}
        assert premium_is_ours(entity, TG) is False
