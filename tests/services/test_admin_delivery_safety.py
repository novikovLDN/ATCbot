"""Админские экраны выдачи: не отправить не тому и не соврать в отчёте.

Три дефекта:

1. Apple ID: buyer_id приходит из callback_data, а текст ключа — из FSM,
   который у админа один на чат. Начал выдачу по заказу A, прислал ключ,
   не подтвердил, начал заказ B и прислал ключ B — превью перезаписалось.
   Нажатие на старую кнопку под заказом A отправило бы покупателю A ЧУЖОЙ
   ключ. Кнопки живут в чате вечно, так что сценарий не гипотетический.

2. Кнопка «💬 Написать пользователю» редактировала сообщение, под которым
   висела, — а висит она в том числе под уведомлениями о заказах Spotify,
   Apple ID и Steam, где лежат email, пароль и кнопка «Выполнено». Админ
   нажимал «написать», терял данные заказа и не мог его выдать.

3. Админу писали «Пользователь уведомлён» сразу по флагу, тогда как
   отправка шла ниже под условием `notify_user and vpn_key`. При пустом
   ключе (активация ещё в процессе) уведомление не уходило вовсе — админ
   читал, что человек предупреждён, и не перезванивал.
"""
from pathlib import Path

import pytest


def test_apple_delivery_verifies_the_buyer():
    """Ключ не должен уходить покупателю, для которого его не готовили."""
    src = Path("app/handlers/admin/apple_id_delivery.py").read_text(encoding="utf-8")
    assert "APPLE_KEY_BUYER_MISMATCH" in src, "нет сверки покупателя из кнопки и из FSM"
    block = src[src.index('prepared_for = int(data.get("buyer_id") or 0)'):]
    block = block[:900]
    assert "prepared_for != buyer_id" in block
    assert "return" in block, "при расхождении отправка обязана прерваться"


def test_admin_chat_does_not_overwrite_the_order_card():
    """Иначе вместе с сообщением исчезают email, пароль и кнопка выдачи.

    Ищем обработчик по всему админскому разделу, а не в конкретном файле:
    он уже переезжал при разбивке base.py, и привязка к имени файла
    ломает тест на ровном месте.
    """
    import inspect
    from app.handlers.admin import chat

    block = inspect.getsource(chat.callback_admin_chat_start)
    assert "safe_edit_text" not in block, (
        "экран чата снова правит сообщение, под которым висела кнопка"
    )
    assert "callback.message.answer(" in block


# Дефекты 3 и 4 (отчёт «уведомлён» по намерению вместо факта и русская
# f-строка вместо перевода) жили в app/handlers/admin/access_grant.py.
# Экран выдачи доступа удалён целиком — выдача идёт через веб-дашборд,
# поэтому проверять там нечего. Проверка переводов ниже осталась: ключ
# admin.user_granted_access продолжает использоваться в уведомлении,
# которое шлёт дашборд, и пустой перевод сломал бы его так же.


@pytest.mark.parametrize("lang", ["ru", "en", "de", "ar", "kk", "tj", "uz"])
def test_grant_keys_exist_in_every_locale(lang):
    from app.i18n import get_text

    unit = get_text(lang, "units.days")
    assert unit and unit != "units.days", f"{lang}: нет перевода единицы времени"

    text = get_text(
        lang, "admin.user_granted_access",
        value=3, unit=unit, vpn_key="K", date="01.01.2026",
    )
    assert "admin.user_granted_access" != text, f"{lang}: нет текста выдачи"
    assert "{" not in text, f"{lang}: остались неподставленные плейсхолдеры"
