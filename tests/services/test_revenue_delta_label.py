"""Подпись дельты выручки должна описывать реально посчитанное окно.

ЧТО БЫЛО. Дельта считалась как «вторая половина выбранного окна против
первой» (окно переключалось кнопками 7/30/90/180), а подпись под цифрой
была прибита гвоздями: «vs prev 30d». Врала она дважды. Владелец,
выбравший 7 дней, читал «+42.0% vs prev 30d» и считал, что видит месячную
динамику. И даже в режиме по умолчанию сравнивались две половины по
15 дней, а не 30 против предыдущих 30.

ЧТО СТАЛО. Сводку переделали, и половин окна больше нет: главное число —
выручка за сегодня, а сравнивается она со вчерашним днём В ТО ЖЕ ВРЕМЯ
СУТОК. Окно режет Postgres
(database/analytics_revenue.py::get_revenue_today_vs_yesterday), фронт
получает готовые числа и elapsed_minutes.

Дефект при этом не исчез, а сменил форму: подпись по-прежнему можно
оторвать от посчитанного окна — достаточно написать «против вчера»
вместо конкретного часа, и в одиннадцать утра человек снова будет думать,
что сравнивают полные сутки. Поэтому тесты остались, но смотрят на новое
место.

Тестов на TypeScript в проекте нет, поэтому проверяем структурно — по
исходнику.
"""
import re
from pathlib import Path

import pytest

MONEY = Path("dashboard/src/components/summary/MoneyToday.tsx")
REVENUE = Path("database/analytics_revenue.py")
SRC_DIR = Path("dashboard/src")


def _without_comments(text: str) -> str:
    """Комментарии объясняют дефект и обязаны упоминать старую подпись —
    искать её надо в коде, а не в объяснении."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


@pytest.fixture()
def src() -> str:
    return MONEY.read_text(encoding="utf-8")


def test_hardcoded_window_is_gone():
    """Ни одного зашитого горизонта в подписи — нигде во фронте."""
    for path in SRC_DIR.rglob("*.tsx"):
        assert "vs prev 30d" not in _without_comments(
            path.read_text(encoding="utf-8")
        ), f"зашитая подпись окна вернулась в {path}"


def test_comparison_arm_is_computed_on_the_server(src):
    """Плечо сравнения приходит с сервера, а не выдумывается на фронте.

    Считать «сколько прошло с полуночи» в браузере значит взять часовой
    пояс машины владельца. Сутки в этом проекте режет Postgres по Москве —
    иначе тайл и суточный график расходятся на три часа, что уже было.
    """
    assert "elapsed_minutes" in src
    assert "Europe/Moscow" in REVENUE.read_text(encoding="utf-8")


def test_label_names_the_hour_it_actually_compared(src):
    """Подпись собирается из elapsed_minutes, а не из общих слов.

    «против вчера» — это и есть та самая ложь, только другими словами:
    в одиннадцать утра сравнивают не сутки, а одиннадцать часов, и это
    обязано быть написано.
    """
    body = src.split("function DeltaChip", 1)[1]
    assert "elapsedClock(data.elapsed_minutes)" in body
    assert "к вчерашнему на {clock}" in body


def test_percent_sign_follows_the_number(src):
    """Знак берётся из числа, а не из объекта целиком: `{...} >= 0` для
    объекта всегда истина, и минус бы пропал."""
    body = src.split("function DeltaChip", 1)[1]
    assert "const up = pct >= 0;" in body
    assert "data >= 0" not in body


def test_empty_yesterday_is_not_reported_as_growth(src):
    """Вчера в это время не было оплат — это не «+∞ %» и не «+100 %».

    Деление на ноль дало бы Infinity, а печать — «∞ %», число, из которого
    ничего не следует. Показываем словами.
    """
    fn = src.split("function deltaPercent", 1)[1].split("}", 1)[0]
    assert "if (yesterday <= 0) return null;" in fn
    assert "не было оплат" in src
