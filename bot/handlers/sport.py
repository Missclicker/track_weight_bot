"""Sport entries. Recording is reached only through `/sport` (or a reply to its prompt) in
`commands.py`: free text is deliberately not scanned for sport keywords any more, the false
positives in a chatty group outweighed the convenience.

The router here carries a single handler: a delete word ("видали") in reply to the bot's own
confirmation throws the activity away.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial

from aiogram import Bot, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.i18n import DEFAULT_LANG, Lang
from bot.parsing import is_delete_request, strip_yesterday
from bot.scheduler import user_now
from bot.sheets import SheetsRepo


async def record_sport(
    message: Message,
    text: str,
    repo: SheetsRepo,
    ai: GeminiClient,
    settings: Settings,
    source: str,
    *,
    yesterday: bool = False,
    lang: Lang = DEFAULT_LANG,
) -> None:
    """Parse `text` with Gemini + MET table and store it for the sender; reply in `lang`.

    This is the single entry point for both `/sport` and its prompt reply, so the "вчора" marker
    is taken here: the text handed to Gemini then never carries it, which keeps the word out of
    the activity title the model returns. `yesterday` lets a caller force the same thing.
    """
    user = await ensure_user(message, repo, settings)
    text, marked = strip_yesterday(text)
    yesterday = yesterday or marked
    now = user_now(user, settings)
    day = now.date() - timedelta(days=1) if yesterday else None
    previous = await repo.last_weight(user.user_id, now)
    strings = i18n.t(lang)
    entry = await ai.parse_sport(
        text,
        previous[1] if previous else None,
        on_retry=partial(message.reply, strings.AI_RETRYING),
        lang=lang,
    )
    if entry is None:
        await message.reply(strings.SPORT_NOT_RECOGNIZED)
        return
    # reply first, then store: the row is keyed to the confirmation the user will reply to,
    # exactly like `photos.record_food` does it
    reply = await message.reply(
        strings.sport_saved(
            entry.title,
            entry.minutes,
            entry.distance_km,
            entry.kcal,
            day.isoformat() if day else None,
        )
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
        day=day,
    )


class SportDeleteReply(BaseFilter):
    """A delete word in reply to one of the bot's own sport confirmations.

    The confirmation is recognised by its prefix in any language (`SPORT_PREFIXES`), so it
    survives a restart and its reader switching language (the same trick as `FOOD_PREFIX` and
    `PING_PREFIXES`). Any other text replying to it stays unhandled: re-estimating an activity is
    not a thing the bot does.
    """

    async def __call__(self, message: Message, bot: Bot) -> bool:
        reply = message.reply_to_message
        if reply is None or reply.from_user is None or reply.from_user.id != bot.id:
            return False
        if not (reply.text or "").startswith(i18n.SPORT_PREFIXES):
            return False
        return is_delete_request(message.text)


async def on_delete(message: Message, repo: SheetsRepo, lang: Lang) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    deleted = await repo.delete_sport_entry(
        message.from_user.id, message.reply_to_message.message_id
    )
    strings = i18n.t(lang)
    await message.reply(strings.SPORT_DELETED if deleted else strings.SPORT_NOT_FOUND_FOR_DELETE)


def build() -> Router:
    router = Router(name="sport")
    router.message.register(on_delete, SportDeleteReply())
    return router
