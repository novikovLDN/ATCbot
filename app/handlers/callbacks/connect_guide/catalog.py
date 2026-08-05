"""Справочник инструкции: картинки экранов и ссылки на приложения.

ЧТО ЗДЕСЬ
    Данные, и только данные: file_id картинок для каждого шага и ссылки на
    магазины приложений. Ни одного обработчика — поэтому модуль можно
    править, не читая логику экранов.

ПОЧЕМУ ВЫДЕЛЕНО
    Меняется чаще всего остального: App Store переезжает, появляется новый
    клиент, картинку перерисовали. Раньше эти таблицы лежали в середине
    файла на тысячу строк — между обработчиком шага 2 и экраном выбора
    устройства, — и найти их можно было только поиском.

ЧТО ЛЕГКО СЛОМАТЬ
    file_id привязан к боту и окружению. Картинка, загруженная stage-ботом,
    у prod-бота не откроется — отсюда две ветки в каждом словаре. Ошибётесь
    ключом — экран деградирует до текста без картинки (это предусмотрено,
    ронять инструкцию из-за картинки нельзя), но выглядеть будет бедно.

    Ссылки на магазины — единая точка правды. _IOS_HAPP_LINKS используют и
    экран установки, и кнопки в рассылках; правьте здесь, а не копией.
"""
import os

import config

_DEVICE_SELECT_PHOTO = {
    "prod": "AgACAgQAAxkBAAFU07NqGqUXEmVZ5SivuY0gwUhd7TBCeAACXw9rGxA30FCkvieRMzznwwEAAwIAA3kAAzsE",
    "stage": "AgACAgQAAxkBAAIhc2oZ_tiD1jsG8eB-9HrSgTTiyjEUAAJfD2sbEDfQUDPuD983y47VAQADAgADeQADOwQ",
}

# Photo file IDs for setup screens
_SETUP_PHOTOS = {
    "install_app_ios": {
        "prod": "AgACAgQAAxkBAAEsTydp2K_IyYzWcQLdTzcx8R69LXkQPgAC6wxrG6gtyVKbKj2nQnrQggEAAwIAA3kAAzsE",
        "stage": "AgACAgQAAxkBAAIelmnYsCB_mV2UUCsZQxtCAUv6HfJkAALrDGsbqC3JUsb1k8gTRdgCAQADAgADeQADOwQ",
    },
    "install_app_android": {
        "prod": "AgACAgQAAxkBAAEsVZ9p2WKsEhB1jDTAYdA3TXJdqENHcAACzwxrG9Np0VKr7b7MS293SQEAAwIAA3cAAzsE",
        "stage": "AgACAgQAAxkBAAIeyGnZYtm7bZWgWSbQzaPQK9jDFIjxAALPDGsb02nRUmA2_j7leNc1AQADAgADdwADOwQ",
    },
    "install_keys": {
        "prod": "AgACAgQAAxkBAAEsTzVp2LGqLrhvY1TRSdQdmp_vmS_tEwAC7AxrG6gtyVLmvPzPSqNEwAEAAwIAA3cAAzsE",
        "stage": "AgACAgQAAxkBAAIeumnZWPxaNMkJApJ3JerkNYLX_kJbAALsDGsbqC3JUlRy7JVisnaVAQADAgADdwADOwQ",
    },
}

_IOS_HAPP_LINKS = {
    # 2026-XX: старая ссылка happ-proxy-utility/id6783623643 перестала
    # быть актуальной — App Store переехал на «Happ Proxy Utility Plus»
    # id6788279553. Единая точка правды для iOS-инсталляции и всех
    # broadcast-кнопок «Happ iOS».
    "ru": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6788279553?l=en-GB",
    "global": "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215",
}

_INCY_IOS_URL = "https://apps.apple.com/ru/app/incy/id6756943388?l=en-GB"
_INCY_ANDROID_URL = "https://play.google.com/store/apps/details?id=llc.itdev.incy&hl=en_IE"

_DOWNLOAD_LINKS = {
    # 2026-06-08: V2RayTun снят со всех платформ, Hiddify тоже снят.
    # 2026-07-07: Incy добавлен для Android и macOS — раньше был
    # только iOS. macOS использует ту же App Store ссылку что и iOS
    # (Mac с Apple Silicon умеет ставить iOS-приложения из App Store).
    "ios": {
        "happ": _IOS_HAPP_LINKS["ru"],
        "incy": _INCY_IOS_URL,
    },
    "android": {
        "happ": "https://play.google.com/store/apps/details?id=com.happproxy&hl=ru",
        "incy": _INCY_ANDROID_URL,
    },
    "macos": {
        # macOS ставит iOS-приложение Incy через App Store — та же ссылка.
        "happ": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973?l=en-GB",
        "incy": _INCY_IOS_URL,
    },
    "windows": {
        "happ": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe",
    },
}

# Допускаем override через env — чтобы не пересобирать образ ради смены
# App Store ссылки.
_incy_ios_env = os.getenv("INCY_IOS_APP_URL")
if _incy_ios_env:
    _DOWNLOAD_LINKS["ios"]["incy"] = _incy_ios_env
    _DOWNLOAD_LINKS["macos"]["incy"] = _incy_ios_env
_incy_android_env = os.getenv("INCY_ANDROID_APP_URL")
if _incy_android_env:
    _DOWNLOAD_LINKS["android"]["incy"] = _incy_android_env


def _get_photo_id(key: str) -> str:
    """Get photo file_id based on environment."""
    env_key = "prod" if config.IS_PROD else "stage"
    return _SETUP_PHOTOS.get(key, {}).get(env_key, "")
