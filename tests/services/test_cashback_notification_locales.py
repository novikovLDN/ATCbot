"""Уведомление о реферальном кешбэке рендерится на всех языках.

Дефект: шаблон referral.cashback_amount у de, ar, kk, tj и uz содержит
{action_type} («Сумма покупки / продления / пополнения»), а вызывающий код
передавал только amount. Строка падала с KeyError('action_type'), и человек
видел либо сырой шаблон, либо пустую строку вместо суммы покупки.

Само слово тоже локализуется: referral.action_purchase / _renewal / _topup.
"""
import re
from pathlib import Path

import pytest

LOCALES = ["ru", "en", "de", "ar", "kk", "tj", "uz"]


@pytest.mark.parametrize("language", LOCALES)
@pytest.mark.parametrize("action_type", ["purchase", "renewal", "topup"])
def test_notification_renders_without_placeholders(language, action_type):
    from app.services.notifications.service import format_referral_notification_text

    text = format_referral_notification_text(
        purchase_amount=499.0,
        cashback_amount=49.9,
        cashback_percent=10,
        paid_referrals_count=3,
        referrals_needed=2,
        action_type=action_type,
        subscription_period="3 месяца",
        language=language,
    )

    assert text, f"пустой текст для языка {language}"
    leftovers = re.findall(r"\{(\w+)[^}]*\}", text)
    assert not leftovers, (
        f"неподставленные плейсхолдеры {leftovers} в языке {language}"
    )


@pytest.mark.parametrize("language", LOCALES)
def test_unknown_action_type_does_not_break_rendering(language):
    """Неизвестный тип действия не должен ронять уведомление о деньгах."""
    from app.services.notifications.service import format_referral_notification_text

    text = format_referral_notification_text(
        purchase_amount=100.0, cashback_amount=10.0, cashback_percent=10,
        paid_referrals_count=1, referrals_needed=1,
        action_type="something_new", language=language,
    )
    assert text
    assert "{" not in text


@pytest.mark.parametrize("language", LOCALES)
def test_action_words_exist_in_every_locale(language):
    """Слово подставляется из словаря — оно обязано быть у каждого языка."""
    src = Path(f"app/i18n/{language}.py").read_text(encoding="utf-8")
    for key in ("referral.action_purchase", "referral.action_renewal",
                "referral.action_topup"):
        assert key in src, f"{language}: нет ключа {key}"
