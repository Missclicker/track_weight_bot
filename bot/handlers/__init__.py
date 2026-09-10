"""Router assembly, the allowed-chat gate and shared handler helpers.

Dependencies (`repo`, `ai`, `settings`, `jobs`) are injected by aiogram from the dispatcher's
workflow data (see `bot/__main__.py`), so handlers declare them as keyword arguments.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, CommandStart
from aiogram.types import ErrorEvent, Message

from bot import i18n
from bot.config import Settings
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


def build_router() -> Router:
    """Root router: allowed-chat handlers first, then the /start reply for other private chats."""
    from bot.handlers import commands, corrections, photos, sport, weight

    root = Router(name="root")

    # `guarded` goes first so a private chat that *is* in ALLOWED_CHAT_IDS (e.g. the owner testing
    # in a DM) gets the real handlers. Only /start from other private chats reaches `private`,
    # which explains the bot is for a group; every other message from them is dropped.
    private = Router(name="private")
    private.message.register(private_start, F.chat.type == ChatType.PRIVATE, CommandStart())

    guarded = Router(name="guarded")
    guarded.message.filter(AllowedChat())
    # order matters: explicit commands, then replies (corrections before weight), then the rest
    guarded.include_routers(
        commands.build(), corrections.build(), weight.build(), photos.build(), sport.build()
    )

    root.include_routers(guarded, private)
    root.errors.register(on_error)
    return root


async def private_start(message: Message) -> None:
    await message.answer(i18n.PRIVATE_CHAT_ONLY_GROUP)


async def on_error(event: ErrorEvent) -> None:
    """Log any handler exception and tell the user.

    This only runs when a handler matched the message and raised, so the sender was always
    waiting for a reaction (a weight or correction that silently fails would look "saved").
    """
    log.exception("handler failed: %s", event.exception)
    message = event.update.message
    if message is None:
        return
    try:
        await message.reply(i18n.ERROR_TRY_AGAIN)
    except Exception:
        log.exception("could not send error reply")
