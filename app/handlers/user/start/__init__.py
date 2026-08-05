"""Вход в бота: /start и всё, что приезжает с ним в диплинке.

ЧТО ЗДЕСЬ
    Только сборка роутера. Содержимое разложено так:

        command.py          сама команда /start и порядок разбора ссылок
        share_discount.py   /start refd_<код> — скидка «подари другу»
        marketing_links.py  /start s-<slug> и p-<slug> — клики и награды
        stage_gate.py       развилка для новых пользователей в STAGE

    Разложено так, потому что в одном файле на 994 строки лежали вход в
    бота, выдача наград по промо-ссылкам, отдельная механика скидки и
    stage-only экран, который в проде не выполняется никогда.

ЧТО ЛЕГКО СЛОМАТЬ
    Забытый include_router. Ошибок не будет: /start просто перестанет
    отвечать — самый заметный отказ из всех возможных, но в логах он
    выглядит как тишина.

    Порядок подключения на поведение не влияет: команда ловится
    message-обработчиком, развилка stage — callback'ом, пересечься им
    негде.
"""
from aiogram import Router

from app.handlers.user.start import command, stage_gate

user_router = Router()

user_router.include_router(command.router)
user_router.include_router(stage_gate.router)

# Реэкспорт: раньше всё лежало одним модулем. Список явный, чтобы пропажу
# было видно глазами при ревью.
from app.handlers.user.start.command import cmd_start  # noqa: F401,E402
from app.handlers.user.start.stage_gate import (  # noqa: F401,E402
    _show_stage_gate,
    callback_stage_gate_dev,
)
from app.handlers.user.start.share_discount import (  # noqa: F401,E402
    _SHARE_DISCOUNT_HOURS,
    _SHARE_DISCOUNT_PERCENT,
    _handle_share_discount_start,
)
from app.handlers.user.start.marketing_links import (  # noqa: F401,E402
    _apply_promo_reward,
    _handle_promo_link_start,
    _handle_stats_link_click,
)

__all__ = [
    "user_router",
    "cmd_start",
    "callback_stage_gate_dev",
    "_show_stage_gate",
    "_handle_share_discount_start",
    "_handle_stats_link_click",
    "_handle_promo_link_start",
    "_apply_promo_reward",
    "_SHARE_DISCOUNT_PERCENT",
    "_SHARE_DISCOUNT_HOURS",
]
