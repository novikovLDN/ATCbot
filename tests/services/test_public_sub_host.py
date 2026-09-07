"""
Regression: rewrite_to_public_sub_host — панельный host (rmnw) → публичный sub.

«Премиум/обход» альтернативный ключ на экране ручной установки должен отдавать
ссылку sub.atlassecure.ru, а не rmnw.atlassecure.ru.
"""
from app.services.user_subscription_links import rewrite_to_public_sub_host as r


def test_panel_host_rewritten_to_public_sub():
    assert r("https://rmnw.atlassecure.ru/api/sub/ABC") == "https://sub.atlassecure.ru/api/sub/ABC"


def test_public_and_other_hosts_untouched():
    assert r("https://sub.atlassecure.ru/api/sub/ABC") == "https://sub.atlassecure.ru/api/sub/ABC"
    assert r("https://app.atlassecure.ru/sub/xyz") == "https://app.atlassecure.ru/sub/xyz"


def test_none_and_empty_safe():
    assert r(None) is None
    assert r("") == ""


def test_path_and_query_preserved():
    out = r("https://rmnw.atlassecure.ru/api/sub/ABC?x=1#frag")
    assert out == "https://sub.atlassecure.ru/api/sub/ABC?x=1#frag"
