"""Учётные данные Spotify не должны попадать в отчётную выдачу.

Дефект: у покупок Spotify поля promo_code и country заняты не по назначению —
там лежат пароль и email от аккаунта клиента. Это исторический хак ради
экономии колонок, но обе колонки отдавались в ленту платежей дашборда:
пароль видел любой, у кого есть доступ к админке.
"""
import inspect
import re

import pytest

import database.analytics as analytics_mod

FEED_FUNCS = ["get_recent_payments_feed", "get_user_purchases"]


@pytest.mark.parametrize("name", FEED_FUNCS)
def test_promo_code_masked_for_spotify(name):
    src = inspect.getsource(getattr(analytics_mod, name))
    assert "purchase_type = 'spotify'" in src, (
        f"{name}: пароль Spotify уедет в выдачу без маскировки"
    )


@pytest.mark.parametrize("name", FEED_FUNCS)
def test_no_bare_promo_code_column(name):
    """Голая колонка означает, что маскировка обойдена."""
    src = inspect.getsource(getattr(analytics_mod, name))
    bare = re.findall(r"^\s*pp\.promo_code\s*,\s*$", src, re.M)
    assert not bare, f"{name}: promo_code отдаётся без CASE-маскировки"


@pytest.mark.parametrize("name", FEED_FUNCS)
def test_no_bare_country_column(name):
    src = inspect.getsource(getattr(analytics_mod, name))
    bare = re.findall(r"^\s*pp\.country\s*,\s*$", src, re.M)
    assert not bare, f"{name}: country (email клиента) отдаётся без маскировки"


@pytest.mark.parametrize("name", FEED_FUNCS)
def test_column_aliases_preserved(name):
    """Потребители читают поля по именам promo_code и country —
    алиасы обязаны сохраниться, иначе отвалится вся лента."""
    src = inspect.getsource(getattr(analytics_mod, name))
    assert "AS promo_code" in src
    assert "AS country" in src


@pytest.mark.parametrize("name", FEED_FUNCS)
def test_non_spotify_purchases_keep_data(name):
    """Для обычных покупок промокод и страна нужны — маскировать их нельзя."""
    src = inspect.getsource(getattr(analytics_mod, name))
    assert "ELSE pp.promo_code END" in src
    assert "ELSE pp.country END" in src
