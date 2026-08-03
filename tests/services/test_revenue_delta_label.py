"""Подпись дельты выручки должна описывать реально посчитанное окно.

Дефект. Дельта считается как «вторая половина выбранного окна против
первой» (окно переключается кнопками 7/30/90/180), а подпись под цифрой
была прибита гвоздями: «vs prev 30d». Врала она дважды. Админ, выбравший
7 дней, читал «+42.0% vs prev 30d» и считал, что видит месячную
динамику. И даже в дефолтном режиме 30 дней сравнивались две половины по
15 дней, а не 30 против предыдущих 30.

Тестов на TypeScript в проекте нет (в dashboard/ отсутствует
node_modules), поэтому проверяем структурно — по исходнику.
"""
import re
from pathlib import Path

import pytest

DASHBOARD = Path("dashboard/src/pages/Dashboard.tsx")


def _without_comments(text: str) -> str:
    """Комментарии объясняют дефект и обязаны упоминать старую подпись —
    искать её надо в коде, а не в объяснении."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


@pytest.fixture()
def src() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_hardcoded_window_is_gone(src):
    """Ни одного зашитого горизонта в подписи."""
    assert "vs prev 30d" not in _without_comments(src)


def test_delta_reports_the_arm_it_actually_compared(src):
    """Вместе с процентом наружу отдаётся длина плеча сравнения."""
    body = src.split("const revenueDelta = useMemo(", 1)[1].split("}, [daily.data]);", 1)[0]
    assert "const half = Math.floor(s.length / 2);" in body
    assert "return { percent:" in body and "half };" in body


def test_label_is_built_from_that_arm(src):
    """Подпись собирается из half, а не из константы."""
    subline = src.split("revenueDelta != null", 1)[1].split("positive:", 1)[0]
    assert "revenueDelta.half" in subline
    assert "против предыдущих" in subline


def test_percent_sign_still_follows_the_number(src):
    """Знак берётся из percent, а не из объекта целиком: `{...} >= 0`
    для объекта всегда истина, и минус бы пропал."""
    subline = src.split("revenueDelta != null", 1)[1].split("loading=", 1)[0]
    assert "revenueDelta.percent >= 0" in subline
    assert "revenueDelta >= 0" not in subline
