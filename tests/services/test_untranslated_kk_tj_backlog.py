"""Треть казахского и таджикского словарей — английские заглушки.

Дефект (app/i18n/kk.py:214 и tj.py): сотни значений в kk.py и tj.py
дословно совпадают с en.py. Пользователь с language=kk применяет
промокод и получает ответ на английском посреди казахского интерфейса;
экран «Как работает реферальная программа» — целиком английский абзац.

Переводы намеренно НЕ придуманы: машинный казахский и таджикский в
проде хуже честного бэклога. Вместо этого собран список
docs/audit-2026-07/untranslated-kk-tj.json с ключом, текущим
значением, английским и русским оригиналом — его можно отдать живому
переводчику как есть.

Что здесь проверяется:
  • список существует и не разошёлся со словарями (нет ключей-призраков);
  • цепочка фолбэков язык → английский → русский работает, и человек
    видит осмысленный текст, а не служебную строку вида «steam.title».
"""
import json
import re
from pathlib import Path

import pytest

from app.i18n import LANGUAGES, get_text

BACKLOG = Path("docs/audit-2026-07/untranslated-kk-tj.json")

# Весь кириллический блок целиком: казахскому нужны ә қ ң ғ ү ұ ө і,
# таджикскому — ғ ӣ қ ӯ ҳ ҷ. Диапазон А-Яа-яЁё их не покрывает, из-за
# чего наивная проверка объявляла нормальные строки латиницей.
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
LATIN = re.compile(r"[A-Za-z]")


@pytest.fixture(scope="module")
def backlog():
    assert BACKLOG.exists(), (
        "нет списка непереведённых ключей — переводчику нечего отдать"
    )
    return json.loads(BACKLOG.read_text(encoding="utf-8"))


def test_backlog_has_both_languages(backlog):
    assert backlog["kk"], "казахский бэклог пуст"
    assert backlog["tj"], "таджикский бэклог пуст"


@pytest.mark.parametrize("lang", ["kk", "tj"])
def test_counts_match_sections(backlog, lang):
    """_counts — то, что читает человек, не открывая весь файл. Он не
    должен расходиться с содержимым."""
    assert backlog["_counts"][lang] == len(backlog[lang])


@pytest.mark.parametrize("lang", ["kk", "tj"])
def test_backlog_entries_are_real_keys(backlog, lang):
    """Ключ-призрак в бэклоге — это час работы переводчика впустую."""
    dictionary = LANGUAGES[lang]
    phantom = [k for k in backlog[lang] if k not in dictionary]
    assert not phantom, f"{lang}: ключей нет в словаре: {phantom[:10]}"


@pytest.mark.parametrize("lang", ["kk", "tj"])
def test_backlog_entries_carry_source_texts(backlog, lang):
    """Переводчику нужен исходник. Хотя бы один из en/ru обязан быть."""
    without_source = [
        k for k, v in backlog[lang].items() if not v.get("en") and not v.get("ru")
    ]
    assert not without_source, (
        f"{lang}: записи без исходного текста: {without_source[:10]}"
    )


@pytest.mark.parametrize("lang", ["kk", "tj"])
def test_native_language_names_are_not_in_backlog(backlog, lang):
    """«🇰🇿 Қазақша» и «🇷🇺 Русский» одинаковы во всех словарях специально —
    в бэклог им нельзя, иначе переводчик «починит» селектор языка."""
    assert not [k for k in backlog[lang] if k.startswith("lang.button_")]


# ── Цепочка фолбэков: сырой ключ на экран не попадает ────────────────

@pytest.mark.parametrize("lang", ["kk", "tj"])
def test_no_dictionary_key_ever_returns_raw_key(lang):
    """Ключ есть хоть где-то в семье словарей → пользователь обязан
    получить текст, а не «referral.how_it_works_text»."""
    all_keys = set()
    for d in LANGUAGES.values():
        all_keys |= set(d)
    raw = [k for k in sorted(all_keys) if get_text(lang, k) == k and LANGUAGES[lang].get(k) != k]
    assert not raw, f"{lang}: сырой ключ ушёл на экран: {raw[:10]}"


@pytest.mark.parametrize("lang", ["kk", "tj"])
def test_russian_only_keys_fall_back_to_russian_not_to_key(lang):
    """Русский — конец цепочки. Сотня ключей (Steam, Spotify, подключение)
    живёт только в ru.py; без этой ступени экран показывал бы имя ключа."""
    ru_only = [k for k in LANGUAGES["ru"] if k not in LANGUAGES["en"] and k not in LANGUAGES[lang]]
    assert ru_only, "тест устарел: русский словарь больше не шире остальных"
    for key in ru_only[:50]:
        assert get_text(lang, key) == LANGUAGES["ru"][key]


@pytest.mark.parametrize("lang", ["kk", "tj"])
def test_backlog_values_really_look_untranslated(backlog, lang):
    """Запись в бэклоге обязана быть обоснованной: либо дословная копия
    английского, либо строка без единой кириллической буквы. Иначе
    переводчик получит уже переведённое."""
    en = LANGUAGES["en"]
    bogus = []
    for key, info in backlog[lang].items():
        value = LANGUAGES[lang][key]
        looks_english = (en.get(key) is not None and value == en[key]) or (
            LATIN.search(value) and not CYRILLIC.search(value)
        )
        if not looks_english:
            bogus.append(key)
    assert not bogus, f"{lang}: в бэклоге уже переведённые ключи: {bogus[:10]}"
