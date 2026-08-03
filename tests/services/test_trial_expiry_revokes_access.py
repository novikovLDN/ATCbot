"""Конец триала должен отбирать premium-доступ, а не делать вид.

ЧТО ЛОМАЛОСЬ

    Единственным «отзывом доступа» при истечении триала был вызов
    vpn_utils.remove_vless_user — после снятия samopis это заглушка,
    которая пишет строчку в лог и возвращает None. Отключить в панели
    она не могла ничего.

    Хуже: следом стояла ветка «если отзыв не удался — не помечаем
    подписку истёкшей, повторим в следующем цикле». Заглушка не падает
    никогда, поэтому ветка была недостижима — а premium-сущность в
    панели не гасил вообще никто.

ПОЧЕМУ ЭТО НЕ ВСЕГДА ЗАМЕТНО

    Обычно сущность гаснет сама: её создают с expireAt на конец триала.
    Но если триал продлевали, если ленивый провижининг выставил свою
    дату (now + 3 дня при пустом expires_at) или админ трогал даты
    руками — сущность остаётся ACTIVE, и триальщик пользуется premium
    сколько угодно.

ЧТО ПРОВЕРЯЕМ

    Что панель действительно гасится, что это происходит после выхода
    из соединения с базой, и что сущность обхода не задета: она своя и
    должна жить дальше, если у человека остались гигабайты.
"""
import inspect
from pathlib import Path

import trial_notifications


SRC = Path("trial_notifications.py").read_text(encoding="utf-8")


def test_premium_entity_is_disabled_on_expiry():
    assert "disable_premium_user" in SRC, (
        "истечение триала снова никак не отражается в панели"
    )


def test_noop_stub_is_not_called_anymore():
    """remove_vless_user — заглушка, её «успех» ничего не значит."""
    live = [
        ln for ln in SRC.split("\n")
        if "remove_vless_user" in ln and not ln.lstrip().startswith("#")
    ]
    assert not live, f"вернулся вызов заглушки: {live}"


def test_unreachable_early_return_is_gone():
    """Ветка «отзыв не удался — повторим позже» не могла сработать:
    заглушка не бросает исключений."""
    src = inspect.getsource(trial_notifications._process_single_trial_expiration)
    assert "Don't mark subscription as expired if VPN removal failed" not in src


def test_panel_is_disabled_after_the_connection_is_released():
    """HTTP при занятом соединении съедает место в пуле на всё время
    запроса — а цикл идёт по всем истёкшим триалам подряд."""
    src = inspect.getsource(trial_notifications._process_single_trial_expiration)

    def _indent_of(needle: str) -> int:
        pos = src.index(needle)
        return pos - (src.rindex("\n", 0, pos) + 1)

    acquire_indent = _indent_of("async with pool.acquire() as conn:")
    guard_indent = _indent_of("if disable_premium_after_release:")

    assert guard_indent == acquire_indent, (
        f"вызов панели вложен в блок соединения "
        f"(отступ {guard_indent} против {acquire_indent})"
    )
    assert src.index("async with pool.acquire()") < src.index(
        "if disable_premium_after_release:"
    ), "панель гасится до работы с базой"


def test_bypass_entity_is_left_alone():
    """Гигабайты обхода человек мог купить отдельно — они не при чём."""
    src = inspect.getsource(trial_notifications._process_single_trial_expiration)
    tail = src[src.index("if disable_premium_after_release:"):]
    assert "disable_bypass" not in tail
    assert "remove_bypass" not in tail
    # А продление обхода при переходе в bypass-only, наоборот, на месте.
    assert "extend_remnawave_for_bypass_bg" in src


def test_paid_subscriber_is_never_touched():
    """Триал не должен отбирать доступ у того, кто уже оплатил."""
    src = inspect.getsource(trial_notifications._process_single_trial_expiration)
    flag = src.index("disable_premium_after_release = True")
    recheck = src.rindex("active_paid_recheck", 0, flag)
    assert recheck < flag, "флаг ставится до перепроверки платной подписки"
