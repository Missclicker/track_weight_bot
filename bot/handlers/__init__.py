"""Router assembly, the allowed-chat gate, the reader's language and shared handler helpers.

Dependencies (`repo`, `ai`, `settings`, `jobs`) are injected by aiogram from the dispatcher's
workflow data (see `bot/__main__.py`), so handlers declare them as keyword arguments. `lang`, the
sender's language, arrives the same way from `SenderLang`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import ErrorEvent, Message, TelegramObject

from bot import i18n
from bot.ai import ModelOverloaded, QuotaExceeded
from bot.config import Settings
from bot.i18n import DEFAULT_LANG, Lang
from bot.sheets import SheetsRepo, User

log = logging.getLogger(__name__)

_rejected_chats_logged: set[int] = set()
# Serialises first-contact registration: aiogram handles each update in its own task, so a new
# member posting an album would otherwise race three `get_user` misses into three `users` rows.
_register_lock = asyncio.Lock()


class AllowedChat(BaseFilter):
    """Pass only messages from chats listed in ALLOWED_CHAT_IDS."""

    async def __call__(self, message: Message, settings: Settings) -> bool:
        allowed = message.chat.id in settings.allowed_chat_ids
        if not allowed and message.chat.id not in _rejected_chats_logged:
            # INFO on purpose: this is how a first-time operator discovers their group's chat id
            # (README, step 2). Logged once per chat so spam can't flood the log.
            _rejected_chats_logged.add(message.chat.id)
            log.info(
                "ignoring chat %s (%s, %r) - add it to ALLOWED_CHAT_IDS if this is your group",
                message.chat.id,
                message.chat.type,
                message.chat.title or message.chat.full_name,
            )
        return allowed


class SenderLang(BaseMiddleware):
    """Inject `lang`, the language of the person who sent the message, into the handler.

    Registered as an *inner* middleware on the root router's message observer: aiogram runs the
    inner middlewares of a router and all its parents, so every nested router gets it, and only
    once a handler's filters matched - ordinary group chatter and chats outside ALLOWED_CHAT_IDS
    cost no lookup. The language is the sender's in a group and a DM alike: a reply answers one
    person, so it speaks that person's language (`SheetsRepo.get_lang`, from the cached `users`
    tab; somebody unknown or who never ran /lang gets the default).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["lang"] = await _sender_lang(event, data["repo"])
        return await handler(event, data)


async def _sender_lang(event: TelegramObject, repo: SheetsRepo) -> Lang:
    """The sender's language, or the default when it cannot be read.

    Never raises: a Sheets outage must not take down handlers that need no Sheets at all
    (`/help`, the bare `/food` prompt) - they answer in the default language instead.
    """
    user = getattr(event, "from_user", None)
    if user is None:
        return DEFAULT_LANG
    try:
        return await repo.get_lang(user.id)
    except Exception:
        log.warning("could not read the language of user %s, using the default", user.id)
        return DEFAULT_LANG


def display_name(message: Message) -> str:
    user = message.from_user
    if user is None:
        return "?"
    return user.full_name or user.username or str(user.id)


async def ensure_user(message: Message, repo: SheetsRepo, settings: Settings) -> User:
    """Return the sender's `User`, registering them on first contact."""
    assert message.from_user is not None
    existing = await repo.get_user(message.from_user.id, message.chat.id)
    if existing is not None:
        return existing
    async with _register_lock:
        existing = await repo.get_user(message.from_user.id, message.chat.id)
        if existing is not None:
            return existing
        user = User(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            name=display_name(message),
            username=message.from_user.username or "",
            tz=settings.default_tz,
            joined_at=datetime.now(settings.tzinfo).isoformat(timespec="seconds"),
        )
        await repo.upsert_user(user)
    log.info("registered user %s (%s) in chat %s", user.user_id, user.name, user.chat_id)
    return user


async def find_member(user_id: int, repo: SheetsRepo, settings: Settings) -> User | None:
    """The sender's row from the first allowed chat they belong to, or None.

    Used by handlers that can be reached from a private chat: a DM must never register anybody
    (its `chat.id` is the user, not a group), so someone unknown is told to use the group.
    """
    for chat_id in sorted(settings.allowed_chat_ids):
        user = await repo.get_user(user_id, chat_id)
        if user is not None:
            return user
    return None


def build_router() -> Router:
    """Root router: allowed-chat handlers first, then the private-chat handlers."""
    from bot.handlers import commands, corrections, photos, sport, water, weight

    root = Router(name="root")

    # `guarded` goes first so a private chat that *is* in ALLOWED_CHAT_IDS (e.g. the owner testing
    # in a DM) gets the real handlers. From other private chats only /start, /вода and /мова
    # reach `private`; every other message from them is dropped.
    private = Router(name="private")
    private.message.register(private_start, F.chat.type == ChatType.PRIVATE, CommandStart())
    private.message.register(
        water.cmd_water, F.chat.type == ChatType.PRIVATE, Command(*i18n.COMMANDS["water"])
    )
    private.message.register(
        commands.cmd_lang, F.chat.type == ChatType.PRIVATE, Command(*i18n.COMMANDS["lang"])
    )

    guarded = Router(name="guarded")
    guarded.message.filter(AllowedChat())
    # order matters: explicit commands, then replies (corrections before weight), then the rest
    guarded.include_routers(
        commands.build(),
        water.build(),
        corrections.build(),
        weight.build(),
        photos.build(),
        sport.build(),
    )

    root.include_routers(guarded, private)
    # on the root's observer, so that both children and every router inside them inherit it
    root.message.middleware(SenderLang())
    root.errors.register(on_error)
    return root


async def private_start(message: Message, repo: SheetsRepo, settings: Settings, lang: Lang) -> None:
    """/start in a private chat: a member learns the bot can now DM them, a stranger is refused."""
    if message.from_user is not None and await find_member(message.from_user.id, repo, settings):
        await message.answer(i18n.t(lang).WATER_DM_READY)
        return
    await message.answer(i18n.t(lang).PRIVATE_CHAT_ONLY_GROUP)


async def on_error(event: ErrorEvent, repo: SheetsRepo) -> None:
    """Log any handler exception and tell the user, in the sender's language.

    This only runs when a handler matched the message and raised, so the sender was always
    waiting for a reaction (a weight or correction that silently fails would look "saved").
    """
    exception = event.exception
    message = event.update.message
    # The error observer does not see what `SenderLang` put into the handler's data (that dict is
    # the handler's own), so the language is looked up again - from the cached `users` tab. The
    # failure being reported may well be Sheets itself; `_sender_lang` then falls back to the
    # default and the reply still goes out.
    lang = await _sender_lang(message, repo) if message is not None else DEFAULT_LANG
    strings = i18n.t(lang)
    if isinstance(exception, QuotaExceeded):
        # WARNING, not a traceback: a spent Gemini quota is an expected, self-healing condition
        # of the free tier, and it gets a message that says what the user can still do. With the
        # Groq fallback on, either outage only gets here once Groq has failed too.
        log.warning(
            "Gemini %s is out of quota for another %.0f s (daily=%s)",
            exception.model,
            exception.retry_after_s,
            exception.daily,
        )
        text = strings.quota_notice(vision=exception.vision, daily=exception.daily)
    elif isinstance(exception, ModelOverloaded):
        # Same treatment as a spent quota: an overloaded model is Google's weather, not our bug,
        # and it clears itself - so a WARNING line, no traceback, and a reply that says when to
        # come back and what still works meanwhile.
        log.warning(
            "Gemini %s is overloaded for another %.0f s",
            exception.model,
            exception.retry_after_s,
        )
        text = strings.busy_notice(vision=exception.vision)
    else:
        log.exception("handler failed: %s", exception)
        text = strings.ERROR_TRY_AGAIN
    if message is None:
        return
    try:
        await message.reply(text)
    except Exception:
        log.exception("could not send error reply")
