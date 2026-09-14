"""FakeTelegram — an aiogram session that answers every Bot API call locally.

`Bot(token, session=FakeTelegram())` runs the REAL aiogram client code
(method objects, default parse mode, `.as_(bot)` binding) and only replaces
the HTTP round-trip to api.telegram.org: every call is recorded and gets a
well-formed result (a Message bound to the bot, True, a User for getMe, …).

Recorded calls are the "screen" of the test:
    tg.to(chat_id)          → [Sent] outgoing messages / edits / photos to a chat
    tg.last(chat_id)        → the last one
    tg.buttons(sent)        → [(text, callback_data | url)]
    tg.alerts_to(admin_id)  → texts sent to the admin chat
    tg.answers              → callback-query answers (text, show_alert)

Update builders produce the pydantic Update objects Telegram would POST to
/telegram/webhook: message(), callback(), pre_checkout(), successful_payment().
"""
from __future__ import annotations

import itertools
import typing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import (
    Chat, InlineKeyboardMarkup, Message, MessageId, User, WebhookInfo,
)

BOT_ID = 7_000_000_001
_TEXT_FIELDS = ("text", "caption")


@dataclass
class Sent:
    method: str
    chat_id: Optional[int]
    text: str
    reply_markup: Any = None
    message_id: Optional[int] = None
    raw: Any = None

    def buttons(self) -> List[tuple]:
        return buttons_of(self.reply_markup)

    def callback_data(self) -> List[str]:
        return [b[1] for b in self.buttons() if b[1] and not str(b[1]).startswith("http")]


def buttons_of(markup: Any) -> List[tuple]:
    if not isinstance(markup, InlineKeyboardMarkup):
        return []
    out = []
    for row in markup.inline_keyboard:
        for b in row:
            target = b.callback_data or b.url or (b.web_app.url if b.web_app else None) \
                or getattr(b, "copy_text", None)
            out.append((b.text, target))
    return out


@dataclass
class Answer:
    callback_query_id: str
    text: Optional[str]
    show_alert: bool


class FakeTelegram(BaseSession):
    """Records every Bot API call. `fail_chats`: chat ids whose sends raise
    TelegramForbiddenError (the user blocked the bot)."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: List[TelegramMethod] = []
        self.sent: List[Sent] = []
        self.answers: List[Answer] = []
        self.invoices: List[Any] = []
        self.pre_checkout_answers: List[Any] = []
        self.unknown: List[str] = []
        self.fail_chats: set[int] = set()
        self._mid = itertools.count(10_000)
        # Telegram-runtime errors (docs/audit/11_telegram_runtime.md):
        # inject(method, factory) makes the next call(s) of `method` raise.
        self.injected: List[Dict[str, Any]] = []
        # strict_html: validate parse_mode=HTML texts like Telegram does and
        # answer "can't parse entities" on an unsupported / unbalanced tag.
        self.strict_html = False
        self.html_errors: List[tuple] = []

    def inject(self, method: str, factory, *, chat_id: Optional[int] = None,
               times: Optional[int] = None) -> None:
        """Make calls of Bot API `method` (e.g. "AnswerCallbackQuery",
        "SendMessage") raise `factory(method_obj)`. chat_id — only for that
        chat; times — only the next N matching calls (None = every call)."""
        self.injected.append({"method": method, "factory": factory, "chat_id": chat_id, "times": times})

    def _injected_error(self, name: str, chat_id: Any, method: TelegramMethod):
        for rule in list(self.injected):
            if rule["method"] != name or (rule["chat_id"] is not None and rule["chat_id"] != chat_id):
                continue
            if rule["times"] is not None:
                rule["times"] -= 1
                if rule["times"] <= 0:
                    self.injected.remove(rule)
            return rule["factory"](method)
        return None

    # ── BaseSession ──────────────────────────────────────────────────
    async def close(self) -> None:  # noqa: D401
        return None

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536,
                             raise_for_status=True):  # pragma: no cover
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: Optional[int] = None):
        self.calls.append(method)
        name = type(method).__name__
        chat_id = getattr(method, "chat_id", None)
        if isinstance(chat_id, int) and chat_id in self.fail_chats and name.startswith("Send"):
            from aiogram.exceptions import TelegramForbiddenError
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        err = self._injected_error(name, chat_id, method)
        if err is not None:
            raise err
        if self.strict_html and getattr(method, "parse_mode", None) == "HTML":
            for f in _TEXT_FIELDS:
                v = getattr(method, f, None)
                problem = html_problem(v) if isinstance(v, str) else None
                if problem:
                    from aiogram.exceptions import TelegramBadRequest
                    self.html_errors.append((name, chat_id, problem, v[:200]))
                    raise TelegramBadRequest(method=method, message=f"Bad Request: can't parse entities: {problem}")

        if name == "AnswerCallbackQuery":
            self.answers.append(Answer(method.callback_query_id, method.text, bool(method.show_alert)))
            return True
        if name == "AnswerPreCheckoutQuery":
            self.pre_checkout_answers.append(method)
            return True
        if name == "GetMe":
            return User(id=BOT_ID, is_bot=True, first_name="Atlas", username="atlassecure_bot")
        if name == "GetWebhookInfo":
            import config
            return WebhookInfo(url=config.WEBHOOK_URL, has_custom_certificate=False, pending_update_count=0)
        if name == "CreateInvoiceLink":
            return f"https://t.me/$invoice-{next(self._mid)}"
        if name == "CopyMessage":
            return MessageId(message_id=next(self._mid))

        returning = getattr(method, "__returning__", None)
        if _returns_message(returning):
            text = ""
            for f in _TEXT_FIELDS:
                v = getattr(method, f, None)
                if isinstance(v, str):
                    text = v
                    break
            if name == "SendInvoice":
                self.invoices.append(method)
                text = f"[invoice] {method.title}: {method.description}"
            mid = getattr(method, "message_id", None) if name.startswith("Edit") else None
            mid = mid or next(self._mid)
            cid = chat_id if isinstance(chat_id, int) else (int(chat_id) if str(chat_id or "").lstrip("-").isdigit() else 0)
            self.sent.append(Sent(name, cid, text, getattr(method, "reply_markup", None), mid, method))
            msg = Message(
                message_id=mid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=cid, type="private"),
                from_user=User(id=BOT_ID, is_bot=True, first_name="Atlas"),
                text=text or None,
                reply_markup=getattr(method, "reply_markup", None)
                if isinstance(getattr(method, "reply_markup", None), InlineKeyboardMarkup) else None,
            )
            return msg.as_(bot)
        if returning is bool or _is_bool_union(returning):
            return True
        self.unknown.append(name)
        return True

    # ── queries ──────────────────────────────────────────────────────
    def to(self, chat_id: int) -> List[Sent]:
        return [s for s in self.sent if s.chat_id == chat_id]

    def texts(self, chat_id: int) -> List[str]:
        return [s.text for s in self.to(chat_id)]

    def last(self, chat_id: int) -> Optional[Sent]:
        items = self.to(chat_id)
        return items[-1] if items else None

    def find(self, chat_id: int, needle: str) -> List[Sent]:
        return [s for s in self.to(chat_id) if needle in (s.text or "")]

    def with_button(self, chat_id: int, callback_data: str) -> Optional[Sent]:
        for s in reversed(self.to(chat_id)):
            if callback_data in s.callback_data():
                return s
        return None

    def mark(self) -> int:
        """Position marker: `tg.since(mark)` returns only what was sent after it."""
        return len(self.sent)

    def since(self, mark: int, chat_id: Optional[int] = None) -> List[Sent]:
        items = self.sent[mark:]
        return [s for s in items if chat_id is None or s.chat_id == chat_id]

    def clear(self) -> None:
        self.calls.clear()
        self.sent.clear()
        self.answers.clear()
        self.invoices.clear()
        self.pre_checkout_answers.clear()


def _returns_message(tp: Any) -> bool:
    if tp is Message:
        return True
    return Message in (typing.get_args(tp) or ())


def _is_bool_union(tp: Any) -> bool:
    return bool in (typing.get_args(tp) or ())


# ── Telegram HTML (parse_mode=HTML) validation ─────────────────────────
_TG_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler",
            "a", "code", "pre", "blockquote", "tg-emoji", "br"}


def html_problem(text: str) -> Optional[str]:
    """What Telegram would reject in a parse_mode=HTML text (unsupported or
    unbalanced tag, bare '<'), or None. A lightweight model of the Bot API
    parser — enough for user-controlled data (names, input) in templates."""
    import re
    stack: List[str] = []
    for m in re.finditer(r"<(/?)([^\s<>/]*)[^<>]*?(/?)>|<", text):
        if m.group(0) == "<":
            return f"Unexpected end of name token at byte offset {m.start()}"
        closing, tag = m.group(1), m.group(2).lower()
        if tag not in _TG_TAGS:
            return f'Unsupported start tag "{tag}" at byte offset {m.start()}'
        if tag == "br" or m.group(3):
            continue
        if closing:
            if not stack or stack[-1] != tag:
                return f'Unmatched end tag at byte offset {m.start()}, expected "</{stack[-1] if stack else ""}>"'
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        return f'Can\'t find end tag corresponding to start tag "{stack[-1]}"'
    return None


# ── error factories for FakeTelegram.inject ─────────────────────────────
def bad_request(message: str):
    from aiogram.exceptions import TelegramBadRequest
    return lambda m: TelegramBadRequest(method=m, message=message)


def forbidden(message: str = "Forbidden: bot was blocked by the user"):
    from aiogram.exceptions import TelegramForbiddenError
    return lambda m: TelegramForbiddenError(method=m, message=message)


def retry_after(seconds: int):
    from aiogram.exceptions import TelegramRetryAfter
    return lambda m: TelegramRetryAfter(method=m, message=f"Too Many Requests: retry after {seconds}",
                                        retry_after=seconds)


def network_error(message: str = "Request timeout error"):
    from aiogram.exceptions import TelegramNetworkError
    return lambda m: TelegramNetworkError(method=m, message=message)


QUERY_TOO_OLD = "Bad Request: query is too old and response timeout expired or query ID is invalid"
EDIT_NOT_FOUND = "Bad Request: message to edit not found"
CANT_BE_EDITED = "Bad Request: message can't be edited"
NOT_MODIFIED = ("Bad Request: message is not modified: specified new message content and reply "
                "markup are exactly the same as a current content and reply markup of the message")


# ── Update builders ─────────────────────────────────────────────────────

_update_ids = itertools.count(500_000)
_msg_ids = itertools.count(1)


@dataclass
class TgUser:
    id: int
    first_name: str = "Test"
    username: Optional[str] = None
    language_code: Optional[str] = "ru"
    last_name: Optional[str] = None

    def as_user(self) -> Dict[str, Any]:
        d = {"id": self.id, "is_bot": False, "first_name": self.first_name}
        if self.language_code is not None:
            d["language_code"] = self.language_code
        if self.username:
            d["username"] = self.username
        if self.last_name:
            d["last_name"] = self.last_name
        return d


def _chat(user: TgUser) -> Dict[str, Any]:
    return {"id": user.id, "type": "private", "first_name": user.first_name}


def message(user: TgUser, text: str) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "message_id": next(_msg_ids),
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": _chat(user),
        "from": user.as_user(),
        "text": text,
    }
    if text.startswith("/"):
        cmd = text.split()[0]
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(cmd)}]
    return {"update_id": next(_update_ids), "message": msg}


def callback(user: TgUser, data: str, *, message_id: Optional[int] = None,
             message_text: str = "…") -> Dict[str, Any]:
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": str(next(_update_ids)),
            "from": user.as_user(),
            "chat_instance": str(user.id),
            "data": data,
            "message": {
                "message_id": message_id or next(_msg_ids),
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": _chat(user),
                "from": {"id": BOT_ID, "is_bot": True, "first_name": "Atlas"},
                "text": message_text,
            },
        },
    }


def pre_checkout(user: TgUser, payload: str, total_amount: int, currency: str = "RUB") -> Dict[str, Any]:
    return {
        "update_id": next(_update_ids),
        "pre_checkout_query": {
            "id": str(next(_update_ids)),
            "from": user.as_user(),
            "currency": currency,
            "total_amount": total_amount,
            "invoice_payload": payload,
        },
    }


def successful_payment(user: TgUser, payload: str, total_amount: int, currency: str = "RUB",
                       charge_id: Optional[str] = None) -> Dict[str, Any]:
    n = next(_update_ids)
    return {
        "update_id": n,
        "message": {
            "message_id": next(_msg_ids),
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": _chat(user),
            "from": user.as_user(),
            "successful_payment": {
                "currency": currency,
                "total_amount": total_amount,
                "invoice_payload": payload,
                "telegram_payment_charge_id": charge_id or f"tg-charge-{n}",
                "provider_payment_charge_id": f"prov-charge-{n}",
            },
        },
    }


def refunded_payment(user: TgUser, payload: str, total_amount: int, currency: str = "XTR",
                     charge_id: str = "tg-charge-refunded") -> Dict[str, Any]:
    """Service message Telegram sends when a Stars payment is refunded."""
    return {
        "update_id": next(_update_ids),
        "message": {
            "message_id": next(_msg_ids),
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": _chat(user),
            "from": user.as_user(),
            "refunded_payment": {
                "currency": currency,
                "total_amount": total_amount,
                "invoice_payload": payload,
                "telegram_payment_charge_id": charge_id,
            },
        },
    }


def message_with(user: TgUser, **content: Any) -> Dict[str, Any]:
    """A private message with arbitrary content (sticker=…, photo=[…], …)."""
    msg: Dict[str, Any] = {
        "message_id": next(_msg_ids),
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": _chat(user),
        "from": user.as_user(),
    }
    msg.update(content)
    return {"update_id": next(_update_ids), "message": msg}


STICKER = {"file_id": "CAACAgIAAxkBAAE", "file_unique_id": "AgADsticker", "type": "regular",
           "width": 512, "height": 512, "is_animated": False, "is_video": False}
PHOTO = [{"file_id": "AgACAgIAAxkBAAE", "file_unique_id": "AQADphoto", "width": 90, "height": 90}]


def group_message(user: TgUser, text: str, chat_type: str = "group") -> Dict[str, Any]:
    upd = message(user, text)
    upd["message"]["chat"] = {"id": -1_000_000_000 - user.id, "type": chat_type, "title": "Some group"}
    return upd


def channel_post(text: str) -> Dict[str, Any]:
    return {"update_id": next(_update_ids), "channel_post": {
        "message_id": next(_msg_ids), "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": -1_009_999_999, "type": "channel", "title": "Some channel"}, "text": text}}


def edited_message(user: TgUser, text: str) -> Dict[str, Any]:
    upd = message(user, text)
    upd["message"]["edit_date"] = upd["message"]["date"]
    return {"update_id": upd["update_id"], "edited_message": upd["message"]}


def inline_query(user: TgUser, query: str) -> Dict[str, Any]:
    return {"update_id": next(_update_ids), "inline_query": {
        "id": str(next(_update_ids)), "from": user.as_user(), "query": query, "offset": ""}}


def chat_join_request(user: TgUser) -> Dict[str, Any]:
    now = int(datetime.now(timezone.utc).timestamp())
    return {"update_id": next(_update_ids), "chat_join_request": {
        "chat": {"id": -1_009_999_998, "type": "supergroup", "title": "Some group"},
        "from": user.as_user(), "user_chat_id": user.id, "date": now}}


def my_chat_member(user: TgUser, new_status: str, old_status: str = "member") -> Dict[str, Any]:
    """The user blocked (new_status="kicked") / unblocked ("member") the bot."""
    bot_user = {"id": BOT_ID, "is_bot": True, "first_name": "Atlas", "username": "atlassecure_bot"}
    now = int(datetime.now(timezone.utc).timestamp())

    def member(status: str) -> Dict[str, Any]:
        d: Dict[str, Any] = {"status": status, "user": bot_user}
        if status == "kicked":
            d["until_date"] = 0
        return d
    return {"update_id": next(_update_ids), "my_chat_member": {
        "chat": _chat(user), "from": user.as_user(), "date": now,
        "old_chat_member": member(old_status), "new_chat_member": member(new_status)}}


__all__ = [
    "Answer", "CANT_BE_EDITED", "EDIT_NOT_FOUND", "FakeTelegram", "NOT_MODIFIED", "PHOTO",
    "QUERY_TOO_OLD", "STICKER", "Sent", "TgUser", "bad_request", "buttons_of", "callback",
    "channel_post", "chat_join_request", "edited_message", "forbidden", "group_message",
    "html_problem", "inline_query", "message", "message_with", "my_chat_member", "network_error",
    "pre_checkout", "refunded_payment", "retry_after", "successful_payment",
]
