"""Sport entries. Recording is reached only through `/sport` (or a reply to its prompt) in
`commands.py`: free text is deliberately not scanned for sport keywords any more, the false
positives in a chatty group outweighed the convenience.

The router here carries a single handler: a delete word ("видали") in reply to the bot's own
confirmation throws the activity away.
"""

from __future__ import annotations

from functools import partial

from aiogram import Bot, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.parsing import is_delete_request
from bot.scheduler import user_now
from bot.sheets import SheetsRepo


async def record_sport(
    message: Message, text: str, repo: SheetsRepo, ai: GeminiClient, settings: Settings, source: str
) -> None:
    """Parse `text` with Gemini + MET table and store it for the sender."""
    user = await ensure_user(message, repo, settings)
    now = user_now(user, settings)
    previous = await repo.last_weight(user.user_id, now)
    entry = await ai.parse_sport(
        text, previous[1] if previous else None, on_retry=partial(message.reply, i18n.AI_RETRYING)
    )
    if entry is None:
        await message.reply(i18n.SPORT_NOT_RECOGNIZED)
        return
    # reply first, then store: the row is keyed to the confirmation the user will reply to,
    # exactly like `photos.record_food` does it
    reply = await message.reply(
        i18n.sport_saved(entry.title, entry.minutes, entry.distance_km, entry.kcal)
    )
    await repo.add_sport(
        user,
        entry.title,
        entry.minutes,
        entry.distance_km,
        entry.kcal,
        now,
        source,
        message_id=reply.message_id,
    )


class SportDeleteReply(BaseFilter):
    """A delete word in reply to one of the bot's own sport confirmations.

    The confirmation is recognised by `SPORT_PREFIX`, so it survives a restart (the same trick as
    `FOOD_PREFIX` and `PING_PREFIX`). Any other text replying to it stays unhandled: re-estimating
    an activity is not a thing the bot does.
    """

    async def __call__(self, message: Message, bot: Bot) -> bool:
        reply = message.reply_to_message
        if reply is None or reply.from_user is None or reply.from_user.id != bot.id:
            return False
        if not (reply.text or "").startswith(i18n.SPORT_PREFIX):
            return False
        return is_delete_request(message.text)


async def on_delete(message: Message, repo: SheetsRepo) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    deleted = await repo.delete_sport_entry(
        message.from_user.id, message.reply_to_message.message_id
    )
    await message.reply(i18n.SPORT_DELETED if deleted else i18n.SPORT_NOT_FOUND_FOR_DELETE)


def build() -> Router:
    router = Router(name="sport")
    router.message.register(on_delete, SportDeleteReply())
    return router
