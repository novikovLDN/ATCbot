"""Установка дашборда на домашний экран не должна запирать админа снаружи.

Дефект. Приложение с домашнего экрана на iOS получает своё хранилище:
ни куки atlas_admin_session, ни bootstrap-токена из localStorage там
нет — оно стартует с формы логина. Повторно пройти первичную настройку
нельзя (409 already_setup), а забытый пароль лечится только кнопкой
«Сбросить пароль» в боте, которая заодно сносит все passkey. Админ,
который прошёл setup во встроенном браузере Telegram и не запомнил
пароль, упирается в форму логина и теряет вообще все способы входа.

Как чиним. Одноразовость setup оставляем — это защита от захвата
аккаунта по magic-ссылке, которая навсегда остаётся в переписке. Вместо
этого: сразу после установки пароля предлагаем завести ключ устройства
(он переживает установку на домашний экран), а на экранах явно пишем,
что установленное приложение спросит вход заново и что сброс пароля
убьёт passkey.
"""
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("APP_ENV", "stage")

from app.api.dashboard import auth as auth_mod


@pytest.mark.asyncio
async def test_setup_stays_one_shot(monkeypatch):
    """Главный регресс-гард: «починить» PWA повторным setup нельзя.

    Magic-ссылка живёт в истории чата вечно. Если разрешить по ней
    второй setup, любой, кто пролистал переписку, меняет пароль без
    знания старого — это захват админки.
    """
    monkeypatch.setattr(
        auth_mod, "verify_token", lambda _t: {"role": "admin", "sub": "42"},
    )
    monkeypatch.setattr(auth_mod.admin_auth, "is_admin", lambda tg: True)
    monkeypatch.setattr(
        auth_mod.admin_auth, "credentials_exist", AsyncMock(return_value=True),
    )
    set_creds = AsyncMock(return_value=True)
    monkeypatch.setattr(auth_mod.admin_auth, "set_credentials", set_creds)

    body = auth_mod.SetupRequest(
        username="atlas", password="verysecret1", bootstrap_token="x" * 20,
    )
    with pytest.raises(auth_mod.HTTPException) as e:
        await auth_mod.auth_setup(body, response=None)

    assert e.value.status_code == 409
    assert set_creds.await_count == 0, "пароль перезаписали по magic-ссылке"


def test_setup_screen_offers_a_device_key():
    """После установки пароля фронт обязан предложить passkey.

    Это единственный способ входа, который переживает установку на
    домашний экран, не требуя помнить пароль.
    """
    src = Path("dashboard/src/pages/SetupPassword.tsx").read_text(encoding="utf-8")
    assert "registerPasskey" in src
    assert "isPasskeySupported" in src
    assert "домашн" in src, "нет предупреждения про домашний экран"


def test_login_screen_explains_the_standalone_case():
    """В установленном приложении форма логина не должна выглядеть поломкой."""
    src = Path("dashboard/src/pages/Login.tsx").read_text(encoding="utf-8")
    assert "isStandalone" in src
    assert "отдельное" in src


def test_login_screen_warns_that_reset_kills_passkeys():
    """Сброс пароля из бота удаляет все ключи устройства — об этом надо
    предупреждать до нажатия, а не после."""
    src = Path("dashboard/src/pages/Login.tsx").read_text(encoding="utf-8")
    assert "ключи устройства" in src
