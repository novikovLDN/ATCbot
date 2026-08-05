"""Рассылки в дашборде: сборка роутера и допуск к нему.

ЧТО ЗДЕСЬ
    Только гард админа и подключение подроутеров. Сами эндпоинты
    разложены по соседям:

        read.py             читающие экраны: история, сегменты, карточка,
                            счётчики доставки, аналитика
        segments_catalog.py данные каталога сегментов (ключ/подпись/тултип)
        uploads.py          загрузка фото и GIF ради Telegram file_id
        keyboard.py         кнопки письма и premium-эмодзи
        send.py             создание рассылки, тест себе, удаление из чатов
        scheduled.py        отложенные и повторяющиеся задания
        common.py           живой Bot и сериализация строки БД

    Разложено так, потому что в одном файле на 1033 строки лежали пять
    вещей, которые правят по разным поводам: текст каталога сегментов,
    вёрстка кнопок, фоновая отправка, загрузка файлов и планировщик.

ПОРЯДОК ПОДКЛЮЧЕНИЯ — ЧАСТЬ ПОВЕДЕНИЯ
    FastAPI отдаёт запрос ПЕРВОМУ подошедшему маршруту. `/{broadcast_id}`
    ловит любой односегментный путь, поэтому `/recent` и `/segments`
    обязаны быть объявлены раньше — иначе экран истории начнёт отвечать
    422 «broadcast_id не число».

    Порядок ниже в точности повторяет порядок объявления в исходном
    файле: read → uploads → send → scheduled. Менять его без нужды
    нельзя, а если менять — смотреть на весь список маршрутов целиком,
    а не на один добавляемый.

ЧТО ЕЩЁ ЛЕГКО СЛОМАТЬ
    Гард `require_admin` висит на ЭТОМ роутере, а эндпоинты живут в
    подроутерах. FastAPI приклеивает зависимости родителя к маршрутам при
    include_router, поэтому проверка работает и там. Заведёте подроутер
    мимо этого файла — эндпоинт окажется открытым, и об этом не скажет
    ни один тест, кроме того, что сторожит этот список.
"""
from fastapi import APIRouter, Depends

from app.api.dashboard.deps import require_admin
from app.api.dashboard.routes.broadcasts import (
    read,
    scheduled,
    send,
    uploads,
)

# Реэкспорт: на эти имена ссылались, когда всё лежало одним файлом.
# Список явный, а не `import *`, чтобы пропажу было видно глазами.
from app.api.dashboard.routes.broadcasts.common import (  # noqa: F401
    _get_bot,
    _serialize,
)
from app.api.dashboard.routes.broadcasts.keyboard import (  # noqa: F401
    _BUTTON_TYPES,
    _MD_TG_EMOJI_RE,
    _build_reply_markup,
    normalize_premium_emoji,
)
from app.api.dashboard.routes.broadcasts.segments_catalog import SEGMENTS  # noqa: F401
from app.api.dashboard.routes.broadcasts.send import (  # noqa: F401
    BroadcastCreateRequest,
)
from app.api.dashboard.routes.broadcasts.scheduled import (  # noqa: F401
    ScheduleBroadcastRequest,
    _parse_msk,
)

router = APIRouter(dependencies=[Depends(require_admin)])

router.include_router(read.router)
router.include_router(uploads.router)
router.include_router(send.router)

# Создание рассылки — POST на сам /broadcasts, без хвостового слеша.
# Вешаем здесь, а не в send.py: FastAPI отказывается включать подроутер, у
# которого пустые и префикс, и путь маршрута. Место в списке выбрано не
# случайно — ровно между test-self и /schedule, как было в исходном файле.
router.post("")(send.broadcast_create)

router.include_router(scheduled.router)
