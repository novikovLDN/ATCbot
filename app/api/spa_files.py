"""Безопасная отдача файлов собранного дашборда (SPA).

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ
    Логика «этот путь — файл внутри dist или нет» — единственное место, где
    пользовательская строка превращается в путь на диске. Ошибка здесь
    отдаёт наружу произвольный файл сервера, поэтому она вынесена из
    app/api/__init__.py (там она была объявлена внутри try-блока и не
    поддавалась тестированию) в отдельную функцию с тестами.

ПОЧЕМУ SPA ВООБЩЕ ОТДАЁТ ФАЙЛЫ САМ
    StaticFiles(html=True) не годится: он возвращает index.html только для
    каталогов, а на отсутствующий файл поднимает 404. Роутер дашборда —
    BrowserRouter с настоящими путями (/dashboard/users, /dashboard/payments),
    поэтому F5 на любом разделе и прямая ссылка из мессенджера давали 404.
    Правило простое: существующий файл отдаём файлом, всё остальное —
    index.html, дальше роутит React.
"""
from __future__ import annotations

import os
from typing import Optional


def safe_asset_path(dist_real: str, spa_path: str) -> Optional[str]:
    """Вернуть путь к файлу внутри dist или None, если это не он.

    Args:
        dist_real: каталог сборки, УЖЕ пропущенный через os.path.realpath.
            Разрешать симлинки на каждый запрос незачем — каталог не меняется.
        spa_path: хвост URL после /dashboard/, как его отдал FastAPI.

    Три проверки, и каждая закрывает свою дыру:

    1. Явный отказ на «..» и абсолютный путь. FastAPI отдаёт {path:path} уже
       раскодированным, поэтому «%2e%2e%2f» доезжает сюда обычными точками:
       на уровне URL такой запрос не нормализуется.
    2. realpath вместо normpath. normpath чистит «..» только лексически и
       ничего не знает о симлинках — ссылка внутри dist на /etc прошла бы
       проверку.
    3. Сравнение с разделителем на конце. Голый startswith(dist) пропускает
       соседний каталог dist-backup или dist.old: его имя тоже начинается
       с «dist».
    """
    if not spa_path:
        return None
    if os.path.isabs(spa_path) or ".." in spa_path.replace("\\", "/").split("/"):
        return None
    candidate = os.path.realpath(os.path.join(dist_real, spa_path))
    if candidate != dist_real and not candidate.startswith(dist_real + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


__all__ = ["safe_asset_path"]
