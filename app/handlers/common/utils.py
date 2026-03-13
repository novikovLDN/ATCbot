"""
Shared handler utilities: safe edits, formatting, validation, message builders.
"""
import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional

import database
from aiogram.types import Message
from aiogram.types import InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from app.i18n import get_text as i18n_get_text

logger = logging.getLogger(__name__)

# Максимальная длина отображаемого имени
MAX_DISPLAY_NAME_LENGTH = 64

# Допустимые символы в callback_data
_CALLBACK_DATA_RE = re.compile(r"^[a-zA-Z0-9_:.\-]+$")
MAX_CALLBACK_DATA_LENGTH = 64

# Regex для удаления опасных Unicode символов
_DANGEROUS_UNICODE_RE = re.compile(
    r"[\u0000-\u001f"
    r"\u007f-\u009f"
    r"\u200b-\u200f"
    r"\u2028-\u202f"
    r"\u2060-\u2069"
    r"\u206a-\u206f"
    r"\ufeff"
    r"\ufff0-\uffff"
    r"\U000e0000-\U000e007f"
    r"]"
)

# Запрещённые слова/подстроки в username и first_name
# Проверяется в lowercase, пробелы удаляются. Покрывает: CSAM, порно, насилие,
# экстремизм, терроризм, наркотики, мошенничество, нецензурщину (RU/EN/транслит).
# Если хотя бы одна подстрока найдена — имя заменяется на «Пользователь».
_BANNED_WORDS = frozenset({
    # ── CSAM / child exploitation (EN) ──
    "childporn", "child_porn", "cp_links", "cplinks", "kidporn", "kid_porn",
    "pedo", "pedoph", "preteen", "lolita", "jailbait", "underage",
    "csam", "childabuse", "child_abuse", "minor_sex", "minorsex",
    "youngporn", "young_porn", "toddler", "infantporn", "kiddie",
    "childmodel", "child_model", "teenmodel", "teen_model",
    "babyj", "babysex", "baby_sex", "childsex", "child_sex",
    "boylove", "boy_love", "girllove", "girl_love",
    "shotacon", "shotakon", "shota_", "loli_",
    # ── CSAM (RU / транслит) ──
    "детскоепорно", "дп_ссылки", "малолетк", "педофил", "цп_",
    "детпорно", "школьниц", "несовершеннолетн", "детскийсекс",
    "детскоетело", "маленькийребен", "pedofil", "detiporno",
    "малолетка", "малолеток",
    # ── Porn (EN) ──
    "porn", "p0rn", "p0rno", "pr0n", "xxx_", "_xxx",
    "xvideos", "pornhub", "xhamster", "redtube", "youporn",
    "brazzers", "onlyfans", "chaturbate", "livejasmin",
    "hentai", "rule34", "r34_", "nsfw_", "fap_",
    "blowjob", "blow_job", "handjob", "hand_job",
    "cumshot", "cum_shot", "creampie", "cream_pie",
    "gangbang", "gang_bang", "deepthroat", "deep_throat",
    "milf_", "_milf", "dildo", "vibrator",
    "anal_", "_anal", "analsex", "anal_sex",
    "oralsex", "oral_sex", "hardcore_", "_hardcore",
    "softcore", "stripper", "escort_", "_escort",
    "sexchat", "sex_chat", "sextape", "sex_tape",
    "camgirl", "cam_girl", "camboy", "cam_boy",
    "sexting", "nudes_", "_nudes", "dickpic", "dick_pic",
    "pussy_", "_pussy", "vagina_", "penis_",
    "erotic_", "_erotic", "fetish_", "_fetish",
    "bondage", "bdsm_", "_bdsm",
    "orgasm", "orgazm", "masturbat",
    "bukkake", "tentacle", "futanari",
    "incest", "stepmom", "stepdad", "stepsist",
    "zoophil", "bestial",
    # ── Porn (RU / транслит) ──
    "порно", "порн_", "порнух", "порнуш", "порнограф",
    "секс_", "_секс", "сиськ", "сисек", "письк",
    "вагин", "пенис", "минет", "миньет", "кунилинг",
    "анал_", "анальн", "оральн", "оргазм", "мастурб",
    "эротик", "эскорт", "стриптиз", "проститу",
    "шлюха", "шалав", "давалк", "потаскух",
    "интим_", "интимус", "интимфото", "интимвидео",
    "seks_", "siski", "porno_", "intim_",
    # ── Violence / gore / murder ──
    "snuff", "gore_", "_gore", "killin", "murder_", "beheading",
    "execution_", "torture_", "dismember", "bloodbath",
    "massacre", "genocide_", "massmurd", "mass_murd",
    "skinning", "cannibal", "necrophil", "corpse_",
    "убийств", "убийца", "расчленен", "пытк", "казн",
    "кровав", "живодёр", "живодер", "некрофил",
    # ── Extremism / terrorism ──
    "isis_", "_isis", "jihad", "alqaeda", "terrorist",
    "nazism", "nazi_", "_nazi", "hitler", "heil_",
    "whitepow", "whitepower", "race_war", "racewar",
    "swastika", "sieg_heil", "siegheil", "neonazi",
    "aryanrace", "aryan_race", "whitesupr", "kkk_",
    "skinhead_", "88_hh", "1488_", "_1488",
    "fascis", "tercell", "terrorcell",
    "blackpower", "jihadi", "mujahid", "shahid",
    "калифат", "халифат", "джихад", "терроризм",
    "террорист", "фашиз", "фашист", "нацизм", "нацист",
    "зигхайл", "свастик", "рейхс", "расоваявойна",
    "белоесупремас", "скинхед",
    # ── Suicide / self-harm ──
    "killmyself", "kill_myself", "suicide_", "_suicide",
    "selfharm", "self_harm", "cutmyself", "cut_myself",
    "wanttodie", "want_to_die", "howtodie", "how_to_die",
    "суицид", "самоубийств", "покончитьссобой", "повеситьс",
    "синийкит", "тихийдом",
    # ── Drugs / narcotics ──
    "buydrugs", "buy_drugs", "drugdealer", "drug_dealer",
    "cocain", "heroin_", "meth_lab", "methlab",
    "buyweed", "buy_weed", "marijuana", "cannabis_",
    "amphetamin", "ecstasy", "mdma_", "_mdma",
    "lsd_", "_lsd", "fentanyl", "opioid",
    "ketamin", "morphin", "crackcocain",
    "drugshop", "drug_shop", "darkmarket", "dark_market",
    "silkroad", "silk_road", "darknet_", "_darknet",
    "закладк", "купитьнарк", "наркоторг", "наркотик",
    "амфетамин", "метамфетамин", "героин", "кокаин",
    "марихуан", "гашиш", "экстази", "фентанил",
    "спайс_", "мефедрон", "наркоман", "наркобар",
    "наркодил", "наркомаг", "наркошоп", "соль_наркот",
    "кристалл_нарк", "купитьсоль", "купитьмеф",
    "купитькристалл", "купитьгашиш", "купитьтраву",
    "narkotik", "zakladk", "kupit_mef",
    # ── Weapons / illegal trade ──
    "buygun", "buy_gun", "buypistol", "buy_pistol",
    "buyrifle", "buy_rifle", "sellgun", "sell_gun",
    "illegal_weapon", "illegalweapon", "blackmarket",
    "black_market", "hitman_", "_hitman", "killforh",
    "купитьоруж", "купитьпистол", "чёрныйрынок", "черныйрынок",
    "оружие_прод", "заказатьубийств", "киллер_",
    # ── Scam / phishing / fraud ──
    "freebitcoin", "free_bitcoin", "cryptoscam", "crypto_scam",
    "sendmoney", "send_money", "freemoney", "free_money",
    "hackaccount", "hack_account", "stolencards", "stolen_cards",
    "carding_", "carder_", "cvv_shop", "cvvshop",
    "phishing", "scam_", "_scam", "frauder",
    "stolen_data", "stolendata", "dumpshop", "dump_shop",
    "fakeid", "fake_id", "fakepassport", "fake_passport",
    "обнал", "кардинг", "фишинг", "мошенник",
    "поддельныйпасп", "фальшивыедок", "взломаккаунт",
    "украстьданн", "слитьданн", "пробивлюд",
    "пробитьномер", "пробитьчелов",
    # ── Нецензурная лексика (RU) — все корни и вариации ──
    "хуй", "хуя", "хуе", "хуё", "хуи", "хуёв", "хуев",
    "пизд", "пизж",
    "ёб_", "еба", "ебат", "ебан", "ебну", "ебал", "ебла",
    "ёбан", "ёбат", "ёбну", "ёбал", "ёбла",
    "блядь", "бляд", "блят", "блядин", "блядищ",
    "сука_", "суки_", "сучк", "сучар",
    "пидор", "пидар", "пидр", "пидорас", "пидарас",
    "залуп", "муда", "мудак", "мудил",
    "шлюх", "шлюш",
    "гандон", "гондон",
    "дрочи", "дрочк", "дрочер",
    "манда", "манды",
    "елда", "елдак",
    "хер_", "_хер", "херов", "херня",
    "жопа_", "жоп_", "жопу", "жопе", "засранец", "засранк",
    "говно", "говня", "говнюк", "говнюш",
    "срать", "сруть", "насрать", "обосра",
    "целка", "целк_",
    "даун_", "дебил", "идиот", "кретин", "имбецил",
    "уёбок", "уебок", "уёбищ", "уебищ",
    "выблядок", "выблядк",
    "шалава", "потаскуха", "подстилк",
    # ── Нецензурная лексика (RU транслит) ──
    "huy_", "hui_", "pizd", "blyad", "blyat", "suka_",
    "pidor", "pidar", "pidr", "ebat", "eban", "ebal",
    "gandon", "gondon", "mudak", "nahui", "nahuy",
    "nahren", "zaebis", "zaebal", "otebis",
    "ebanuy", "pizdec", "pizdez",
    # ── Нецензурная лексика (EN) ──
    "fuck_", "_fuck", "fucker", "fuckin", "motherfuck",
    "nigger", "nigg3r", "n1gger", "nigga", "n1gga",
    "faggot", "f4ggot", "fag_", "_fag",
    "retard", "r3tard",
    "cunt_", "_cunt", "twat_",
    "asshole", "a$$hole", "arsehole",
    "shit_", "_shit", "bullshit", "horseshit",
    "bitch_", "_bitch", "son_of_a_bitch",
    "whore_", "_whore", "slut_", "_slut",
    "cock_", "_cock", "cocksucker",
    "wanker", "tosser", "bellend",
    "dickhead", "dick_head", "shithead", "shit_head",
    # ── Discrimination / hate speech ──
    "homophob", "transphob", "xenophob",
    "antisemit", "islamophob", "racist_",
    "гомофоб", "трансфоб", "ксенофоб", "антисемит", "расист",
    # ── Spam patterns ──
    "t.me/", "http://", "https://", "bit.ly", "tinyurl",
    "@everyone", "@here",
    "t.ly/", "goo.gl/", "is.gd/", "v.gd/", "cutt.ly",
    "telegra.ph/", "tg://", "telegram.me/",
})

# Дополнительные паттерны (regex) для обхода фильтров через разделители (p.o.r.n и т.д.)
_SEP = r"[\s._\-*|/\\,;:!?0]*"  # разделитель между буквами
_BANNED_PATTERNS_RE = re.compile(
    r"(?:c{sep}p{sep}l{sep}i{sep}n{sep}k)|"  # c.p.l.i.n.k
    r"(?:п{sep}о{sep}р{sep}н)|"  # п.о.р.н
    r"(?:п{sep}е{sep}д{sep}о)|"  # п.е.д.о
    r"(?:p{sep}e{sep}d{sep}o)|"  # p.e.d.o
    r"(?:p{sep}o{sep}r{sep}n)|"  # p.o.r.n
    r"(?:х{sep}у{sep}[йяеё])|"  # х.у.й / х.у.я
    r"(?:п{sep}и{sep}з{sep}д)|"  # п.и.з.д
    r"(?:б{sep}л{sep}я{sep}[дт])|"  # б.л.я.д / б.л.я.т
    r"(?:е{sep}б{sep}а{sep}[тнл])|"  # е.б.а.т / е.б.а.н
    r"(?:n{sep}i{sep}g{sep}g)|"  # n.i.g.g
    r"(?:f{sep}u{sep}c{sep}k)|"  # f.u.c.k
    r"(?:s{sep}u{sep}i{sep}c{sep}i{sep}d)|"  # s.u.i.c.i.d
    r"(?:н{sep}а{sep}р{sep}к)|"  # н.а.р.к
    r"(?:с{sep}у{sep}и{sep}ц{sep}и{sep}д)"  # с.у.и.ц.и.д
    .format(sep=_SEP),
    re.IGNORECASE,
)

# Leetspeak / замена символов: а→@/4, о→0, е→3, и→1, s→$, a→@ и т.д.
_LEET_MAP = str.maketrans({
    "@": "a", "4": "a", "$": "s", "3": "e", "1": "i", "!": "i",
    "0": "o", "+": "t", "¥": "y",
    # Кириллица: визуально похожие латинские → кириллические
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с",
    "x": "х", "y": "у", "k": "к", "m": "м", "t": "т",
    "b": "б", "h": "н",
})


def _normalize_text(text: str) -> str:
    """Нормализация текста: lowercase, удаление пробелов/разделителей, лит-спик."""
    if not text:
        return ""
    text = text.lower()
    # Удалить пробелы и типичные разделители
    text = re.sub(r"[\s._\-*|/\\,;:!?]+", "", text)
    return text


def _contains_banned_word(text: str) -> bool:
    """Проверяет содержит ли текст запрещённые слова (с нормализацией и anti-bypass)."""
    if not text:
        return False

    # 1. Прямая проверка (lowercase без пробелов)
    normalized = _normalize_text(text)
    for word in _BANNED_WORDS:
        if word in normalized:
            return True

    # 2. Leetspeak-нормализация
    leet_normalized = normalized.translate(_LEET_MAP)
    if leet_normalized != normalized:
        for word in _BANNED_WORDS:
            if word in leet_normalized:
                return True

    # 3. Regex для обхода через разделители
    if _BANNED_PATTERNS_RE.search(text):
        return True

    return False


def sanitize_display_name(name: str) -> str:
    """
    Санитизация имени пользователя для безопасного отображения.

    - Удаляет опасные Unicode символы (RTL override, zero-width, control chars)
    - Обрезает до MAX_DISPLAY_NAME_LENGTH символов
    - Удаляет ведущие/завершающие пробелы
    - Если содержит запрещённые слова — возвращает пустую строку (вызывающий подставит fallback)
    """
    if not name:
        return ""

    name = _DANGEROUS_UNICODE_RE.sub("", name)
    name = name.strip()
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        name = name[:MAX_DISPLAY_NAME_LENGTH].rstrip()

    if _contains_banned_word(name):
        return ""

    return name


def validate_callback_data(data: str) -> bool:
    """Валидация callback_data: длина и символы."""
    if not data or len(data) > MAX_CALLBACK_DATA_LENGTH:
        return False
    return bool(_CALLBACK_DATA_RE.match(data))


def safe_resolve_username(user_obj, language: str, telegram_id: int = None) -> str:
    """
    Безопасное разрешение username для отображения.

    Priority:
    1. user_obj.username (Telegram username) — санитизируется
    2. user_obj.first_name (имя пользователя) — санитизируется
    3. localized fallback (user_fallback key)

    Args:
        user_obj: Telegram user object (Message.from_user, CallbackQuery.from_user, etc.)
        language: User language for fallback text (from DB)
        telegram_id: Optional telegram ID for logging

    Returns:
        Строка для отображения (никогда не None)
    """
    if not user_obj:
        return i18n_get_text(language, "common.user")

    if hasattr(user_obj, "username") and user_obj.username:
        sanitized = sanitize_display_name(user_obj.username)
        if sanitized:
            return sanitized

    if hasattr(user_obj, "first_name") and user_obj.first_name:
        sanitized = sanitize_display_name(user_obj.first_name)
        if sanitized:
            return sanitized

    return i18n_get_text(language, "common.user")


def safe_resolve_username_from_db(
    user_dict: Optional[Dict], language: str, telegram_id: int = None
) -> str:
    """
    Безопасное разрешение username из словаря пользователя из БД.
    Все поля санитизируются через sanitize_display_name().

    Priority:
    1. user_dict.get("username")
    2. user_dict.get("first_name")
    3. "ID: <telegram_id>" if telegram_id provided
    4. localized fallback (user_fallback key)
    """
    if not user_dict:
        if telegram_id:
            return f"ID: {telegram_id}"
        return i18n_get_text(language, "common.user")

    username = user_dict.get("username")
    if username:
        sanitized = sanitize_display_name(username)
        if sanitized:
            return sanitized

    first_name = user_dict.get("first_name")
    if first_name:
        sanitized = sanitize_display_name(first_name)
        if sanitized:
            return sanitized

    if telegram_id:
        return f"ID: {telegram_id}"

    return i18n_get_text(language, "common.user")


def _markups_equal(markup1: InlineKeyboardMarkup, markup2: InlineKeyboardMarkup) -> bool:
    """
    Упрощённое сравнение клавиатур (проверка по callback_data)

    Args:
        markup1: Первая клавиатура
        markup2: Вторая клавиатура

    Returns:
        True если клавиатуры идентичны, False иначе
    """
    try:
        if markup1 is None and markup2 is None:
            return True
        if markup1 is None or markup2 is None:
            return False

        kb1 = markup1.inline_keyboard if hasattr(markup1, 'inline_keyboard') else []
        kb2 = markup2.inline_keyboard if hasattr(markup2, 'inline_keyboard') else []

        if len(kb1) != len(kb2):
            return False

        for row1, row2 in zip(kb1, kb2):
            if len(row1) != len(row2):
                return False
            for btn1, btn2 in zip(row1, row2):
                if btn1.callback_data != btn2.callback_data:
                    return False

        return True
    except Exception:
        return False


async def safe_edit_text(message: Message, text: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = None, bot=None):
    """
    Безопасное редактирование текста сообщения с обработкой ошибок

    Сравнивает текущий контент с новым перед редактированием, чтобы избежать ненужных вызовов API.
    Если сообщение недоступно (inaccessible), использует send_message вместо edit_message.

    Args:
        message: Message объект для редактирования
        text: Новый текст сообщения
        reply_markup: Новая клавиатура (опционально) - MUST be InlineKeyboardMarkup, NOT coroutine
        parse_mode: Режим парсинга (HTML, Markdown и т.д.)
        bot: Bot instance (требуется для fallback на send_message)
    """
    if asyncio.iscoroutine(reply_markup):
        raise RuntimeError("reply_markup coroutine passed without await. Must await keyboard builder before passing to safe_edit_text.")

    if not hasattr(message, 'chat'):
        if bot is None:
            logger.warning("Message is inaccessible (no chat attr) and bot not provided, cannot send fallback message")
            return
        try:
            chat_id = None
            if hasattr(message, 'from_user') and hasattr(message.from_user, 'id'):
                chat_id = message.from_user.id

            if chat_id:
                await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                logger.info(f"Message inaccessible (no chat attr), sent new message instead: chat_id={chat_id}")
            else:
                logger.warning("Message inaccessible (no chat attr) and cannot determine chat_id")
        except Exception as send_error:
            logger.error(f"Failed to send fallback message after inaccessible check: {send_error}")
        return

    current_text = None
    try:
        if hasattr(message, 'text'):
            text_attr = getattr(message, 'text', None)
            if text_attr:
                current_text = text_attr
        if not current_text and hasattr(message, 'caption'):
            caption_attr = getattr(message, 'caption', None)
            if caption_attr:
                current_text = caption_attr
    except AttributeError:
        logger.debug("AttributeError while checking message text/caption, treating as inaccessible")
        current_text = None

    if current_text and current_text == text:
        current_markup = None
        try:
            if hasattr(message, 'reply_markup'):
                markup_attr = getattr(message, 'reply_markup', None)
                if markup_attr:
                    current_markup = markup_attr
        except AttributeError:
            current_markup = None

        if reply_markup is None:
            if current_markup is None:
                return
        else:
            if current_markup and _markups_equal(current_markup, reply_markup):
                return

    has_photo = getattr(message, "photo", None) and len(message.photo) > 0
    if has_photo:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                logger.debug(f"Caption not modified (expected): {e}")
                return
            if any(k in err for k in ["message to edit not found", "message can't be edited", "chat not found", "message is inaccessible"]):
                if bot is not None:
                    chat_id = getattr(getattr(message, "chat", None), "id", None) or (getattr(getattr(message, "from_user", None), "id", None) if getattr(message, "from_user", None) else None)
                    if chat_id:
                        await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                        logger.info(f"Photo message inaccessible, sent new message instead: chat_id={chat_id}")
                return
            raise

    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if "message is not modified" in error_msg:
            logger.debug(f"Message not modified (expected): {e}")
            return
        elif any(keyword in error_msg for keyword in ["message to edit not found", "message can't be edited", "chat not found", "message is inaccessible"]):
            if bot is None:
                logger.warning(f"Message inaccessible and bot not provided, cannot send fallback message: {e}")
                return

            try:
                chat_id = None
                try:
                    if hasattr(message, 'chat'):
                        chat_obj = getattr(message, 'chat', None)
                        if chat_obj and hasattr(chat_obj, 'id'):
                            chat_id = getattr(chat_obj, 'id', None)
                except AttributeError:
                    pass

                if not chat_id:
                    try:
                        if hasattr(message, 'from_user'):
                            user_obj = getattr(message, 'from_user', None)
                            if user_obj and hasattr(user_obj, 'id'):
                                chat_id = getattr(user_obj, 'id', None)
                    except AttributeError:
                        pass

                if chat_id:
                    await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                    logger.info(f"Message inaccessible, sent new message instead: chat_id={chat_id}")
                else:
                    logger.warning(f"Message inaccessible and cannot determine chat_id: {e}")
            except Exception as send_error:
                logger.error(f"Failed to send fallback message after edit failure: {send_error}")
        else:
            raise
    except AttributeError as e:
        logger.warning(f"AttributeError in safe_edit_text, message may be inaccessible: {e}")
        if bot is not None:
            try:
                chat_id = None
                try:
                    if hasattr(message, 'chat'):
                        chat_obj = getattr(message, 'chat', None)
                        if chat_obj and hasattr(chat_obj, 'id'):
                            chat_id = getattr(chat_obj, 'id', None)
                except AttributeError:
                    pass

                if not chat_id:
                    try:
                        if hasattr(message, 'from_user'):
                            user_obj = getattr(message, 'from_user', None)
                            if user_obj and hasattr(user_obj, 'id'):
                                chat_id = getattr(user_obj, 'id', None)
                    except AttributeError:
                        pass

                if chat_id:
                    await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                    logger.info(f"AttributeError handled, sent new message instead: chat_id={chat_id}")
                else:
                    logger.warning(f"AttributeError handled but cannot determine chat_id: {e}")
            except Exception as send_error:
                logger.error(f"Failed to send fallback message after AttributeError: {send_error}")


async def safe_edit_reply_markup(message: Message, reply_markup: InlineKeyboardMarkup = None):
    """
    Безопасное редактирование клавиатуры сообщения с обработкой ошибки "message is not modified"

    Args:
        message: Message объект для редактирования
        reply_markup: Новая клавиатура (или None для удаления)
    """
    if reply_markup is None:
        if message.reply_markup is None:
            return
    else:
        if message.reply_markup and _markups_equal(message.reply_markup, reply_markup):
            return

    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
        logger.debug(f"Reply markup not modified (expected): {e}")


async def get_promo_session(state: FSMContext) -> Optional[Dict[str, Any]]:
    """
    Получить активную промо-сессию из FSM state

    Returns:
        {
            "promo_code": str,
            "discount_percent": int,
            "expires_at": float (unix timestamp)
        } или None если сессия отсутствует или истекла
    """
    fsm_data = await state.get_data()
    promo_session = fsm_data.get("promo_session")

    if not promo_session:
        return None

    expires_at = promo_session.get("expires_at")
    current_time = time.time()

    if expires_at and current_time > expires_at:
        await state.update_data(promo_session=None)
        telegram_id = fsm_data.get("_telegram_id", "unknown")
        logger.info(
            f"promo_session_expired: user={telegram_id}, "
            f"promo_code={promo_session.get('promo_code')}"
        )
        return None

    return promo_session


async def create_promo_session(
    state: FSMContext,
    promo_code: str,
    discount_percent: int,
    telegram_id: int,
    ttl_seconds: int = 300
) -> Dict[str, Any]:
    """
    Создать промо-сессию с TTL

    Args:
        state: FSM context
        promo_code: Код промокода
        discount_percent: Процент скидки
        telegram_id: Telegram ID пользователя (для логирования)
        ttl_seconds: Время жизни в секундах (по умолчанию 300 = 5 минут)

    Returns:
        Созданная промо-сессия
    """
    current_time = time.time()
    expires_at = current_time + ttl_seconds

    promo_session = {
        "promo_code": promo_code.upper(),
        "discount_percent": discount_percent,
        "expires_at": expires_at
    }

    await state.update_data(promo_session=promo_session, _telegram_id=telegram_id)

    expires_in = int(expires_at - current_time)
    logger.info(
        f"promo_session_created: user={telegram_id}, promo_code={promo_code.upper()}, "
        f"discount_percent={discount_percent}%, expires_in={expires_in}s"
    )

    return promo_session


async def clear_promo_session(state: FSMContext):
    """Удалить промо-сессию"""
    await state.update_data(promo_session=None)


async def format_text_with_incident(text: str, language: str) -> str:
    """Добавить баннер инцидента к тексту, если режим активен"""
    try:
        if not database.DB_READY:
            return text
        incident = await database.get_incident_settings()
        if incident and incident.get("is_active"):
            banner = i18n_get_text(language, "incident.banner")
            incident_text = incident.get("incident_text")
            if incident_text:
                banner += f"\n{incident_text}"
            return f"{banner}\n\n⸻\n\n{text}"
        return text
    except Exception as e:
        logger.warning(f"Error getting incident settings: {e}")
        return text


def detect_platform(callback_or_message) -> str:
    """
    Определить платформу пользователя (iOS, Android, или unknown)

    Args:
        callback_or_message: CallbackQuery или Message объект из aiogram

    Returns:
        "ios", "android", или "unknown"
    """
    try:
        if hasattr(callback_or_message, 'from_user'):
            user = callback_or_message.from_user
        elif hasattr(callback_or_message, 'user'):
            user = callback_or_message.user
        else:
            return "unknown"

        language_code = getattr(user, 'language_code', None)

        if language_code:
            lang_lower = language_code.lower()
            if '-' in language_code:
                pass

        return "unknown"

    except Exception as e:
        logger.debug(f"Platform detection error: {e}")
        return "unknown"


def format_promo_stats_text(stats: list) -> str:
    """Форматировать статистику промокодов в текст"""
    if not stats:
        return "Промокоды не найдены."

    text = "📊 Статистика промокодов\n\n"

    for promo in stats:
        code = promo["code"]
        discount_percent = promo["discount_percent"]
        max_uses = promo["max_uses"]
        used_count = promo["used_count"]
        is_active = promo["is_active"]

        text += f"{code}\n"
        text += f"— Скидка: {discount_percent}%\n"

        if max_uses is not None:
            text += f"— Использовано: {used_count} / {max_uses}\n"
            if is_active:
                text += "— Статус: активен\n"
            else:
                text += "— Статус: исчерпан\n"
        else:
            text += f"— Использовано: {used_count}\n"
            text += "— Статус: без ограничений\n"

        text += "\n"

    return text


_REISSUE_LOCKS: Dict[int, asyncio.Lock] = {}


def get_reissue_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _REISSUE_LOCKS:
        _REISSUE_LOCKS[user_id] = asyncio.Lock()
    return _REISSUE_LOCKS[user_id]


def get_reissue_notification_text(vpn_key: str, language: str = "ru") -> str:
    """Текст уведомления о перевыпуске VPN-ключа"""
    title = i18n_get_text(language, "main.reissue_notification_title")
    text_body = i18n_get_text(language, "main.reissue_notification_text", vpn_key=vpn_key)
    return f"{title}\n\n{text_body}"
