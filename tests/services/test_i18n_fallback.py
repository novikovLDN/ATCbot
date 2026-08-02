"""Поведение get_text при отсутствующих ключах и плейсхолдерах.

Дефект: третьим позиционным параметром стоял strict, поэтому 99 вызовов вида
get_text(lang, key, "Запасной текст") молча теряли запасной текст, и
пользователь видел сырой ключ вместо сообщения.
"""
import pytest

from app.i18n import get_text, LANGUAGES


MISSING_KEY = "zzz.definitely.missing.key"


def test_missing_key_uses_provided_default():
    """Главный дефект: запасной текст должен использоваться, а не игнорироваться."""
    assert get_text("ru", MISSING_KEY, "Запасной текст") == "Запасной текст"


def test_missing_key_without_default_returns_key():
    """Прежнее поведение сохранено, когда запасного текста нет."""
    assert get_text("ru", MISSING_KEY) == MISSING_KEY


def test_default_supports_placeholders():
    result = get_text("ru", MISSING_KEY, "Осталось {days} дней", days=3)
    assert result == "Осталось 3 дней"


def test_existing_key_ignores_default():
    """Настоящий перевод важнее запасного текста."""
    real_key = next(iter(LANGUAGES["ru"]))
    assert get_text("ru", real_key, "не должно появиться") != "не должно появиться"


def test_empty_default_falls_back_to_key():
    assert get_text("ru", MISSING_KEY, "") == MISSING_KEY


def test_unknown_language_falls_back_to_default_language():
    real_key = next(iter(LANGUAGES["ru"]))
    assert get_text("xx", real_key) == LANGUAGES["ru"][real_key]


def test_missing_placeholder_does_not_raise():
    """Плохой плейсхолдер не должен ронять обработчик — пользователь
    получит неформатированный текст вместо отсутствия ответа."""
    result = get_text("ru", MISSING_KEY, "Привет, {name}!", wrong_arg="x")
    assert result == "Привет, {name}!"


def test_database_unavailable_key_has_usable_output():
    """errors.database_unavailable отсутствует в ru — запасной текст обязателен.

    Без него пользователь игр видел строку 'errors.database_unavailable'.
    """
    out = get_text("ru", "errors.database_unavailable", "База данных временно недоступна")
    assert out == "База данных временно недоступна"
    assert "errors." not in out


class TestBuyButtonKeys:
    """Кнопки выбора периода ломались на шести языках из семи.

    buy.button_price и buy.button_price_discount содержали плейсхолдер {gb},
    которого нет в аргументах вызова — это давало KeyError. Варианты с badge
    отсутствовали и в этих языках, и в английском фолбэке, поэтому кнопка
    подписывалась сырым ключом 'buy.button_price_badge'.
    """

    KEYS = (
        "buy.button_price",
        "buy.button_price_badge",
        "buy.button_price_discount",
        "buy.button_price_discount_badge",
    )

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    @pytest.mark.parametrize("key", KEYS)
    def test_key_present_in_every_language(self, lang, key):
        assert key in LANGUAGES[lang], f"{key} отсутствует в {lang}"

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    def test_button_price_renders(self, lang):
        out = get_text(lang, "buy.button_price", price=499, period="1 месяц")
        assert "{" not in out, f"неподставленный плейсхолдер в {lang}: {out}"
        assert "499" in out

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    def test_button_price_badge_renders(self, lang):
        out = get_text(lang, "buy.button_price_badge", price=499, period="1 месяц", badge="ХИТ")
        assert "{" not in out, f"неподставленный плейсхолдер в {lang}: {out}"
        assert "buy.button" not in out, f"вместо текста показан сырой ключ в {lang}"

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    def test_button_price_discount_renders(self, lang):
        out = get_text(lang, "buy.button_price_discount", base=699, final=499, period="1 месяц")
        assert "{" not in out, f"неподставленный плейсхолдер в {lang}: {out}"

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    def test_button_price_discount_badge_renders(self, lang):
        out = get_text(
            lang, "buy.button_price_discount_badge",
            base=699, final=499, period="1 месяц", badge="ХИТ",
        )
        assert "{" not in out, f"неподставленный плейсхолдер в {lang}: {out}"
        assert "buy.button" not in out, f"вместо текста показан сырой ключ в {lang}"


class TestMainScreenKeys:
    """Экраны главного меню на всех языках.

    Дефект: приветствие без подписки, экран после истечения и экран
    bypass-only существовали только на русском. Остальным шести языкам
    get_text отдавал сырой ключ — человек видел строку main.welcome_no_sub
    вместо текста.
    """

    KEYS = ("main.welcome", "main.welcome_no_sub",
            "main.welcome_expired", "main.welcome_bypass")

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    @pytest.mark.parametrize("key", KEYS)
    def test_key_present(self, lang, key):
        assert key in LANGUAGES[lang], f"{key} отсутствует в {lang}"

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    @pytest.mark.parametrize("key", KEYS)
    def test_no_raw_key_shown(self, lang, key):
        out = get_text(lang, key)
        assert out != key
        assert not out.startswith("main."), f"показан сырой ключ в {lang}"

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    @pytest.mark.parametrize("key", KEYS)
    def test_html_tags_balanced(self, lang, key):
        """Незакрытый тег ломает всё сообщение: Telegram отклонит его целиком."""
        out = get_text(lang, key)
        # Считаем открывающие теги точно: подстрока "<b" совпадает и с
        # "<blockquote", из-за чего наивный подсчёт даёт ложную тревогу.
        for tag in ("b", "blockquote", "tg-emoji"):
            opened = out.count(f"<{tag}>") + out.count(f"<{tag} ")
            closed = out.count(f"</{tag}>")
            assert opened == closed, f"{lang}/{key}: тег <{tag}> не закрыт"

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    @pytest.mark.parametrize("key", KEYS)
    def test_no_unfilled_placeholders(self, lang, key):
        """У этих ключей нет аргументов — фигурные скобки означают опечатку."""
        assert "{" not in get_text(lang, key)

    @pytest.mark.parametrize("lang", sorted(LANGUAGES))
    def test_brand_name_kept(self, lang):
        """Название продукта не переводится."""
        assert "Atlas Secure" in get_text(lang, "main.welcome_no_sub")
