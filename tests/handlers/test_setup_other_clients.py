"""Connection screens: Windows «Скачать Incy», iOS Karing (download + one-tap),
«Другие клиенты» (Premium + Обход plain subscription keys, one-tap buttons for
v2RayTun / Karing / Stash / Clash Verge).
"""
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, quote, urlsplit

import pytest

import config
from app.handlers.callbacks import navigation as nav
from app.i18n import LANGUAGES
from app.services import happ_crypto, sub_aggregator
from app.services import user_subscription_links as links
from app.utils.telegram_html import telegram_html_errors

BASE = "https://bot.example"
PREMIUM = "https://sub.atlassecure.ru/api/sub/PREM_tok?x=1&y=2"
BYPASS = "https://sub.atlassecure.ru/api/sub/BYP_tok"


def _callback(data: str, tg_id: int = 4242):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = tg_id
    cb.answer = AsyncMock()
    cb.message.delete = AsyncMock()
    cb.bot.send_photo = AsyncMock()
    cb.bot.send_message = AsyncMock()
    return cb


def _sent(cb):
    """(text, keyboard) of the single message a handler sent via the bot."""
    call = cb.bot.send_photo.await_args or cb.bot.send_message.await_args
    kw = call.kwargs
    return kw.get("caption") or kw.get("text"), kw["reply_markup"]


def _rows(kb):
    return kb.inline_keyboard


def _flat(kb):
    return [b for row in kb.inline_keyboard for b in row]


def _texts(kb):
    return [b.text for b in _flat(kb)]


@pytest.fixture
def env(monkeypatch):
    """Premium + bypass available; language ru; aggregator off."""
    state = {"sub": {"subscription_type": "basic"}, "premium": PREMIUM, "bypass": BYPASS}
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", BASE, raising=False)
    monkeypatch.setattr(config, "REMNAWAVE_ENABLED", True, raising=False)
    monkeypatch.setattr(nav, "resolve_user_language", AsyncMock(return_value="ru"))
    monkeypatch.setattr(nav.database, "get_subscription",
                        AsyncMock(side_effect=lambda _id: state["sub"]), raising=False)
    monkeypatch.setattr(links, "get_user_primary_subscription_url",
                        AsyncMock(side_effect=lambda _id: state["premium"]))
    monkeypatch.setattr(links, "get_user_bypass_url",
                        AsyncMock(side_effect=lambda _id: state["bypass"]))
    monkeypatch.setattr(sub_aggregator, "is_enabled_for", lambda _id: False)
    sent = {}

    async def fake_send(bot, telegram_id, text, **kwargs):
        sent["text"], sent["kb"], sent["kwargs"] = text, kwargs["reply_markup"], kwargs
        return MagicMock()

    async def fake_edit(message, text, reply_markup=None, **kwargs):
        sent["edit_text"], sent["edit_kb"] = text, reply_markup

    monkeypatch.setattr(nav, "safe_send_message", fake_send)
    monkeypatch.setattr(nav, "safe_edit_text", fake_edit)
    state["sent"] = sent
    return state


# ── Step 1: install app ──────────────────────────────────────────────────

async def test_windows_step1_has_happ_and_incy_download(env):
    cb = _callback("setup_step1:windows")
    await nav.callback_setup_step1(cb)
    _text, kb = _sent(cb)
    urls = {b.text: b.url for b in _flat(kb) if b.url}
    assert urls["📲 Скачать Happ"].startswith("https://github.com/Happ-proxy/happ-desktop/")
    assert urls["📲 Скачать Incy"] == "https://github.com/INCY-DEV/incy-platforms/releases"


async def test_ios_step1_keeps_happ_incy_and_adds_karing(env):
    cb = _callback("setup_step1:ios")
    await nav.callback_setup_step1(cb)
    _text, kb = _sent(cb)
    urls = {b.text: b.url for b in _flat(kb) if b.url}
    assert "📲 Скачать Incy" in urls
    assert "📲 Скачать Happ (Россия)" in urls
    assert "📲 Скачать Happ (другой регион)" in urls
    assert urls["📲 Скачать Karing"] == "https://apps.apple.com/us/app/karing/id6472431552"


async def test_macos_step1_has_no_karing_store_button(env):
    cb = _callback("setup_step1:macos")
    await nav.callback_setup_step1(cb)
    assert "📲 Скачать Karing" not in _texts(_sent(cb)[1])


@pytest.mark.parametrize("platform", ["ios", "android", "macos", "windows"])
async def test_download_screen_has_no_other_clients_button(env, platform):
    """Owner 2026-09-14: «Другие клиенты» lives on the one-tap key screen."""
    cb = _callback(f"setup_step1:{platform}")
    await nav.callback_setup_step1(cb)
    datas = [b.callback_data for b in _flat(_sent(cb)[1])]
    assert f"setup_other:{platform}" not in datas
    assert f"setup_step2:{platform}" in datas


@pytest.mark.parametrize("aggregator", [False, True], ids=["dual-key", "aggregator"])
@pytest.mark.parametrize("platform", ["ios", "android", "macos", "windows"])
async def test_other_clients_button_sits_right_above_done(env, monkeypatch, platform, aggregator):
    if aggregator:
        monkeypatch.setattr(sub_aggregator, "is_enabled_for", lambda _id: True)
        monkeypatch.setattr(sub_aggregator, "ensure_pair",
                            AsyncMock(return_value="https://sub.atlassecure.ru/agg/ONE"))
    cb = _callback(f"setup_step2:{platform}")
    await nav.callback_setup_step2(cb)
    rows = _rows(_sent(cb)[1])
    datas = [row[0].callback_data for row in rows]
    i = datas.index(f"setup_other:{platform}")
    assert datas[i + 1] == "setup_done"
    assert rows[i][0].text == "🧩 Другие клиенты"


# ── Step 2: one-tap keys (iOS Karing) ────────────────────────────────────

async def test_ios_step2_legacy_has_karing_row_for_both_keys(env):
    cb = _callback("setup_step2:ios")
    await nav.callback_setup_step2(cb)
    _text, kb = _sent(cb)
    karing = {b.text: b.url for b in _flat(kb) if b.url and "/open/karing" in b.url}
    assert set(karing) == {"Karing VPN", "Karing Обход"}
    assert karing["Karing VPN"] == (
        f"{BASE}/open/karing?url={quote(PREMIUM, safe='')}&name={quote('Atlas Secure', safe='')}"
    )
    assert parse_qs(urlsplit(karing["Karing Обход"]).query)["url"] == [BYPASS]
    # Happ / Incy untouched
    assert {"Happ VPN", "Incy VPN", "Happ Обход", "Incy Обход"} <= set(_texts(kb))


async def test_android_step2_has_no_karing(env):
    cb = _callback("setup_step2:android")
    await nav.callback_setup_step2(cb)
    assert not [b for b in _flat(_sent(cb)[1]) if b.url and "/open/karing" in b.url]


async def test_ios_step2_aggregator_has_add_key_to_karing(env, monkeypatch):
    agg = "https://sub.atlassecure.ru/agg/ONE"
    monkeypatch.setattr(sub_aggregator, "is_enabled_for", lambda _id: True)
    monkeypatch.setattr(sub_aggregator, "ensure_pair", AsyncMock(return_value=agg))
    cb = _callback("setup_step2:ios")
    await nav.callback_setup_step2(cb)
    urls = {b.text: b.url for b in _flat(_sent(cb)[1]) if b.url}
    assert parse_qs(urlsplit(urls["🔷 Добавить ключ в Karing"]).query)["url"] == [agg]
    assert "📥 Добавить ключ в Happ" in urls


# ── «Другие клиенты» ─────────────────────────────────────────────────────

async def test_other_clients_keys_are_the_manual_screen_keys(env, monkeypatch):
    """Same source as «Установить вручную»: the manual screen seals the very
    same premium/bypass URLs for Happ; «Другие клиенты» shows them plain."""
    monkeypatch.setattr(happ_crypto, "format_for_user", lambda u: f"HAPP[{u}]")
    cb = _callback("setup_manual:windows")
    await nav.callback_setup_manual(cb)
    manual = env["sent"]["edit_text"]
    assert f"HAPP[{PREMIUM}]" in manual and f"HAPP[{BYPASS}]" in manual

    cb = _callback("setup_other:windows")
    await nav.callback_setup_other(cb)
    text, kb = env["sent"]["text"], env["sent"]["kb"]
    esc_premium = PREMIUM.replace("&", "&amp;")
    assert f"<code>{esc_premium}</code>" in text
    assert f"<code>{BYPASS}</code>" in text
    assert "crypt" not in text  # plain links, not Happ/Incy crypt links
    copies = {b.text: b.copy_text.text for b in _flat(kb) if b.copy_text}
    assert copies == {"📋 Скопировать Premium": PREMIUM, "📋 Скопировать Обход": BYPASS}
    assert env["sent"]["kwargs"]["link_preview_options"].is_disabled


@pytest.mark.parametrize("platform,clients", [
    ("ios", ["v2raytun", "karing", "stash"]),
    ("android", ["v2raytun", "karing"]),
    ("macos", ["karing", "clash"]),
    ("windows", ["karing", "clash"]),
])
async def test_other_clients_one_tap_buttons_per_platform(env, platform, clients):
    cb = _callback(f"setup_other:{platform}")
    await nav.callback_setup_other(cb)
    kb = env["sent"]["kb"]
    one_tap = [b.url for b in _flat(kb) if b.url and "/open/" in b.url]
    assert sorted({urlsplit(u).path.rsplit("/", 1)[1] for u in one_tap}) == sorted(clients)
    for url in one_tap:
        assert url.startswith(f"{BASE}/open/")
        assert parse_qs(urlsplit(url).query)["url"][0] in (PREMIUM, BYPASS)
    assert len(one_tap) == 2 * len(clients)  # Premium + Обход per client
    back = _flat(kb)[-1]
    assert back.callback_data == f"setup_step2:{platform}"   # back to the one-tap key screen


async def test_other_clients_bypass_only_user(env):
    env["sub"], env["premium"] = None, ""
    cb = _callback("setup_other:ios")
    await nav.callback_setup_other(cb)
    text, kb = env["sent"]["text"], env["sent"]["kb"]
    assert BYPASS in text and "PREM_tok" not in text
    assert "только ключ обхода" in text
    assert all("Premium" not in t for t in _texts(kb))
    assert "Karing · Обход" in _texts(kb)


async def test_other_clients_premium_without_bypass(env):
    env["bypass"] = None
    cb = _callback("setup_other:android")
    await nav.callback_setup_other(cb)
    text, kb = env["sent"]["text"], env["sent"]["kb"]
    assert "Ключ обхода появится" in text
    assert "v2RayTun · Premium" in _texts(kb) and "v2RayTun · Обход" not in _texts(kb)


async def test_other_clients_without_keys(env):
    env["sub"], env["premium"], env["bypass"] = None, "", None
    cb = _callback("setup_other:windows")
    await nav.callback_setup_other(cb)
    text, kb = env["sent"]["text"], env["sent"]["kb"]
    assert "Ключей пока нет" in text
    assert [b.callback_data for b in _flat(kb) if b.callback_data] == ["setup_step2:windows"]


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("platform", ["ios", "android", "macos", "windows"])
def test_other_clients_screen_is_valid_telegram_html(monkeypatch, lang, platform):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", BASE, raising=False)
    for premium, bypass in [(PREMIUM, BYPASS), (None, BYPASS), (PREMIUM, None), (None, None)]:
        text, kb = nav._other_clients_screen(lang, platform, premium, bypass)
        assert telegram_html_errors(text) == []
        assert len(text) < 4096
        for b in _flat(kb):
            assert 1 <= len(b.text) <= 64
            if b.callback_data:
                assert len(b.callback_data.encode()) <= 64


def test_client_description_keys_exist_in_ru_and_en():
    for key in nav._OTHER_CLIENT_ABOUT.values():
        for lang in ("ru", "en"):
            assert key in LANGUAGES[lang], (lang, key)
    assert set(nav._OTHER_CLIENT_ABOUT) == set(nav._OTHER_CLIENT_NAMES)
    for clients in nav._OTHER_CLIENTS.values():
        for client, dl_url in clients:
            assert client in nav._OTHER_CLIENT_NAMES
            assert dl_url.startswith("https://")


def test_one_tap_clients_are_served_by_redirect():
    from app.api import deeplink_redirect
    for clients in nav._OTHER_CLIENTS.values():
        for client, _ in clients:
            assert client in deeplink_redirect._SCHEMES
