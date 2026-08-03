"""Все иконки PWA, на которые кто-то ссылается, обязаны существовать.

Дефект из аудита: манифест ссылается на /dashboard/icon-192.png,
icon-512.png, icon-mask-512.png, index.html — на apple-touch-icon.png,
sw.js и push_notifications.py — на icon-192.png, а в dashboard/public
лежат только два SVG. Вывод в аудите был «все четыре ссылки отдают 404».

Проверка показала, что PNG не лежат в репозитории намеренно: их
рисует dashboard/scripts/generate-icons.mjs, привязанный к npm-скрипту
prebuild, а Dockerfile собирает дашборд через `npm run build` — то есть
в образ PNG попадают. Поэтому иконки из манифеста не выпиливаем.

Что тут действительно ломается и ради чего тест. Связь «манифест →
генератор» держится на совпадении имён файлов и не проверяется ничем.
Достаточно переименовать иконку в манифесте, добавить новый размер или
уронить prebuild из package.json — и в проде тихо появятся 404: на iOS
иконка на домашнем экране собирается из скриншота страницы, у пуша
пропадают icon и badge, Chrome ругается на невалидные записи манифеста.
Ничего из этого не заметно из Python-тестов, поэтому проверяем связь
явно.
"""
import json
import re
from pathlib import Path

import pytest

PUBLIC = Path("dashboard/public")
GENERATOR = Path("dashboard/scripts/generate-icons.mjs")
PKG = Path("dashboard/package.json")


def _generated_names() -> set[str]:
    """Имена файлов, которые генератор кладёт в dashboard/public."""
    src = GENERATOR.read_text(encoding="utf-8")
    return set(re.findall(r'out:\s*"([^"]+)"', src))


def _available() -> set[str]:
    """Всё, что будет в public к моменту сборки: файлы репозитория плюс
    то, что дорисует генератор."""
    return {p.name for p in PUBLIC.iterdir() if p.is_file()} | _generated_names()


def _referenced() -> set[str]:
    """Ссылки вида /dashboard/<файл> из манифеста, index.html, sw.js и
    отправки веб-пушей."""
    refs: set[str] = set()

    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    for icon in manifest["icons"]:
        refs.add(icon["src"])

    for path in (
        Path("dashboard/index.html"),
        PUBLIC / "sw.js",
        Path("app/services/push_notifications.py"),
    ):
        text = path.read_text(encoding="utf-8")
        refs.update(re.findall(r"/dashboard/[\w.-]+\.(?:png|svg)", text))

    return refs


@pytest.mark.parametrize("ref", sorted(_referenced()))
def test_every_referenced_icon_will_exist(ref):
    """Каждая ссылка на иконку либо лежит в репозитории, либо рисуется
    генератором. Иначе это 404 в проде."""
    assert ref.startswith("/dashboard/"), f"иконка вне scope дашборда: {ref}"
    name = ref.rsplit("/", 1)[-1]
    assert name in _available(), (
        f"{ref} никто не кладёт в dashboard/public — ни файлом, ни генератором"
    )


def test_generator_is_wired_into_the_build():
    """PNG существуют только потому, что их рисует prebuild. Если скрипт
    отвяжут от build, dist уедет в прод без иконок."""
    pkg = json.loads(PKG.read_text(encoding="utf-8"))
    scripts = pkg["scripts"]
    assert "generate-icons.mjs" in scripts.get("prebuild", "")
    assert "prebuild" in scripts["build"], (
        "build больше не вызывает генератор иконок"
    )


def test_docker_build_runs_the_dashboard_build():
    """В образ dist кладётся из stage сборки — если там перестанут
    вызывать `npm run build`, генератор не отработает."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "npm run build" in dockerfile


def test_svg_sources_are_present():
    """Сам источник растеризации должен лежать в репозитории."""
    assert (PUBLIC / "icon.svg").is_file()
    assert (PUBLIC / "icon-mono.svg").is_file()
