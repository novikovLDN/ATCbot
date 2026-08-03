"""Форматирование реферального уведомления: недостижимая ветка ru.

Дефект: format_referral_notification_text для language == "ru" делает ранний
return через pick_purchase_push. Ниже, внутри `if referrals_needed > 0`,
стояла ещё одна проверка `if language == "ru"` со склонением слова «друг» —
русские пользователи до неё не доходили никогда.

Функционального сбоя это не давало, поэтому и жило долго. Вред другой:
разработчик, которого просят поправить склонение, правит именно этот блок,
перезапускает бота и не видит никакого эффекта — а причину видно только если
дочитать функцию до начала и заметить ранний return.
"""
import inspect

import pytest

from app.services.notifications.service import format_referral_notification_text


def _src():
    return inspect.getsource(format_referral_notification_text)


def test_ru_returns_before_the_pluralisation_block():
    """Ранний return для ru стоит выше блока склонений — значит блока быть не должно."""
    src = _src()
    early_return = src.index("return pick_purchase_push(")
    plural_block = src.index("if referrals_needed > 0:")
    assert early_return < plural_block, (
        "порядок изменился — тест ниже перестал что-либо доказывать"
    )


def test_no_second_ru_branch():
    """Второй `if language == \"ru\"` в этой функции недостижим по определению."""
    src = "\n".join(
        line for line in _src().split("\n") if not line.lstrip().startswith("#")
    )
    assert src.count('language == "ru"') == 1, (
        "вернулась недостижимая ветка ru — правки склонений в ней ни на что не влияют"
    )
    for key in ("referral.friend_singular", "referral.friend_dual"):
        assert key not in src, f"{key} запрашивается из недостижимой ветки"


@pytest.mark.parametrize("needed", [1, 2, 5, 11, 21])
def test_ru_text_comes_from_loyalty_push_whatever_the_count(needed):
    """Русский текст всегда идёт из «Круга Амбассадоров», а не из общей сборки.

    Проверяем на числах, которые как раз и различаются в русских склонениях
    (1 — «друг», 2 — «друга», 5 и 11 — «друзей»): если бы ветка была
    достижима, тексты для них расходились бы по структуре.
    """
    text = format_referral_notification_text(
        purchase_amount=1000.0,
        cashback_amount=100.0,
        cashback_percent=10,
        paid_referrals_count=3,
        referrals_needed=needed,
        action_type="purchase",
        language="ru",
    )
    assert isinstance(text, str) and text
    # Общая сборка всегда содержит заголовок из referral.cashback_title;
    # push-шаблоны — нет.
    assert "referral.cashback_title" not in text


def test_non_russian_still_gets_the_plural_form():
    """Остальные языки продолжают получать текст с формой множественного числа."""
    text = format_referral_notification_text(
        purchase_amount=1000.0,
        cashback_amount=100.0,
        cashback_percent=10,
        paid_referrals_count=3,
        referrals_needed=2,
        action_type="purchase",
        language="en",
    )
    assert isinstance(text, str) and text
    assert "{" not in text, "в тексте остался неподставленный плейсхолдер"
