"""Экранирование имени пользователя для сообщений с parse_mode=HTML.

Дефект: sanitize_display_name чистила опасный Unicode, но не экранировала
HTML. Имя вида "<b>test" подставлялось в <b>{display_name}</b>, Telegram
отклонял сообщение целиком, и экран профиля не приходил вовсе.
"""
import pytest

from app.handlers.common.utils import sanitize_display_name


class TestSanitizeDisplayNameEscaping:
    def test_angle_brackets_escaped(self):
        assert "<" not in sanitize_display_name("<b>жирный")

    def test_ampersand_escaped(self):
        assert sanitize_display_name("Rock & Roll") == "Rock &amp; Roll"

    def test_closing_tag_escaped(self):
        out = sanitize_display_name("</b>")
        assert "<" not in out and ">" not in out

    def test_plain_name_unchanged(self):
        assert sanitize_display_name("Иван Петров") == "Иван Петров"

    def test_emoji_preserved(self):
        assert sanitize_display_name("Иван 🎮") == "Иван 🎮"

    def test_empty_stays_empty(self):
        assert sanitize_display_name("") == ""

    def test_whitespace_trimmed(self):
        assert sanitize_display_name("  Иван  ") == "Иван"

    @pytest.mark.parametrize("payload", [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<a href='http://evil'>клик</a>",
    ])
    def test_markup_never_survives(self, payload):
        out = sanitize_display_name(payload)
        assert "<" not in out, f"разметка прошла: {out}"


class TestModerationStopList:
    """Стоп-лист имён: не игровой инвентарь, а модерация отображаемых имён.

    Строки buy_weed / buy_drugs / buy_gun / buy_pistol / buy_rifle в
    _BANNED_WORDS выглядят как предметы мини-игры и уже один раз были
    приняты за неё в отчёте о мёртвом коде. Никакого магазина оружия и
    наркотиков в боте нет: экономика «Фермы» — 15 культур, плёнка от
    шторма и покупка грядок. Это подстроки, при совпадении с которыми
    username заменяется на «Пользователь». Удалить их — сломать
    модерацию, а не убрать мёртвый код. Тест стоит именно затем, чтобы
    следующая попытка «почистить игровые предметы» упала здесь.

    Вторая проверка — про _BANNED_PATTERNS_RE. Он собран из неявно
    склеенных литералов, к которым в конце применён .format(sep=_SEP).
    Питон склеивает соседние строковые литералы ДО обращения к атрибуту,
    поэтому подстановка попадает во все ветки — но выглядит это как
    классическая ловушка «отформатировали только последнюю строку»,
    и проверить дешевле, чем каждый раз перечитывать.
    """

    @pytest.mark.parametrize("name", [
        "buy_weed", "buydrugs", "buy_gun", "buypistol", "buy_rifle",
    ])
    def test_stop_words_still_block_the_name(self, name):
        assert sanitize_display_name(name) == "", (
            f"{name} перестал фильтроваться — это стоп-лист модерации, "
            f"а не предмет мини-игры"
        )

    @pytest.mark.parametrize("bypass", [
        "п.о.р.н", "p.o.r.n", "c.p.l.i.n.k", "н.а.р.к",
        "с.у.и.ц.и.д", "s.u.i.c.i.d", "f.u.c.k", "n.i.g.g",
    ])
    def test_separator_bypass_is_caught_in_every_branch(self, bypass):
        """Если .format подставился не во все ветки, часть из них не сработает."""
        assert sanitize_display_name(bypass) == "", f"обход прошёл: {bypass}"

    def test_no_unsubstituted_placeholder_left_in_the_pattern(self):
        from app.handlers.common.utils import _BANNED_PATTERNS_RE

        assert "{sep}" not in _BANNED_PATTERNS_RE.pattern

    def test_ordinary_name_survives_the_filter(self):
        assert sanitize_display_name("Гунько") == "Гунько"
