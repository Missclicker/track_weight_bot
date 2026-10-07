"""Sport entries. Recording is reached only through `/sport` (or a reply to its prompt) in
`commands.py`: free text is deliberately not scanned for sport keywords any more, the false
positives in a chatty group outweighed the convenience.

The router here handles replies to the bot's own sport confirmation, the way `corrections.py`
handles replies to a food estimate: a delete word ("видали") throws the activity away, a bare
number replaces its kcal, and any other text ("це було 45 хв", "не біг, а ходьба") goes to Gemini
together with the earlier record and the activity is parsed again. The food-only regret phrases
("це жарт", "я випадково") are matched by nothing here and so stay unanswered.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from typing import Literal

from aiogram import Bot, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.handlers.weight import weigh_in
from bot.i18n import DEFAULT_LANG, Lang
from bot.parsing import (
    is_delete_request,
    is_food_cancel_request,
    parse_correction,
    strip_yesterday,
)
from bot.scheduler import user_now
from bot.sheets import SheetsRepo

_MAX_CORRECTION_TEXT = 500


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


class SportReply(BaseFilter):
    """A text reply to one of the bot's own sport confirmations.

    The confirmation is recognised by its prefix in any language (`SPORT_PREFIXES`), so it
    survives a restart and its reader switching language (the same trick as `FOOD_PREFIX` and
    `PING_PREFIXES`). `kind="delete"` matches a delete word; `kind="kcal"` matches a bare number
    and injects `kcal`; `kind="text"` matches any other text and injects `correction_text`. The
    three kinds are mutually exclusive, so the registration order in `build()` only decides which
    handler is tried first, never what a message means.

    Two kinds of reply are refused by all three. A food regret phrase ("це жарт", "я випадково")
    deletes food rows only - under an activity it is the sort of thing said in passing, so it must
    neither delete the row nor reach Gemini as a correction. And a reply `weight.weigh_in` claims
    ("84.3", "84 кг") belongs to the `weight` router, so no message is claimed by both.
    """

    def __init__(self, kind: Literal["delete", "kcal", "text"]) -> None:
        self.kind = kind

    async def __call__(
        self, message: Message, bot: Bot, settings: Settings
    ) -> bool | dict[str, object]:
        reply = message.reply_to_message
        if reply is None or reply.from_user is None or reply.from_user.id != bot.id:
            return False
        if not (reply.text or "").startswith(i18n.SPORT_PREFIXES):
            return False
        text = (message.text or "").strip()
        if not text:
            return False
        if is_delete_request(text):
            return self.kind == "delete"
        if self.kind == "delete" or is_food_cancel_request(text):
            return False
        if weigh_in(message, bot, settings) is not None:
            return False
        kcal = parse_correction(text)
        if self.kind == "kcal":
            return {"kcal": kcal} if kcal is not None else False
        return {"correction_text": text[:_MAX_CORRECTION_TEXT]} if kcal is None else False


async def on_delete(message: Message, repo: SheetsRepo, lang: Lang) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    deleted = await repo.delete_sport_entry(
        message.from_user.id, message.reply_to_message.message_id
    )
    strings = i18n.t(lang)
    await message.reply(strings.SPORT_DELETED if deleted else strings.SPORT_NOT_FOUND_FOR_DELETE)


async def on_kcal_correction(message: Message, kcal: float, repo: SheetsRepo, lang: Lang) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    strings = i18n.t(lang)
    # scoped to the sender: only the author of an activity can correct it. The row keeps its
    # message_id, so the confirmation replied to stays the one to correct or delete it by.
    updated = await repo.update_sport_kcal(
        message.from_user.id, message.reply_to_message.message_id, kcal
    )
    if not updated:
        await message.reply(strings.CORRECTION_NOT_FOUND)
        return
    await message.reply(strings.CORRECTION_SAVED.format(kcal=f"{kcal:.0f}"))


async def on_text_correction(
    message: Message,
    correction_text: str,
    repo: SheetsRepo,
    ai: GeminiClient,
    settings: Settings,
    lang: Lang,
) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    strings = i18n.t(lang)
    original_id = message.reply_to_message.message_id
    entry = await repo.get_sport_entry(message.from_user.id, original_id)
    if entry is None:
        await message.reply(strings.CORRECTION_NOT_FOUND)
        return
    user = await ensure_user(message, repo, settings)
    now = user_now(user, settings)
    # the same weight `record_sport` would use now: the MET kcal of the corrected activity
    previous = await repo.last_weight(user.user_id, now)
    revised = await ai.revise_sport(
        entry,
        correction_text,
        previous[1] if previous else None,
        on_retry=partial(message.reply, strings.AI_RETRYING),
        lang=lang,
    )
    if revised is None:
        await message.reply(strings.SPORT_CORRECTION_NOT_UNDERSTOOD)
        return
    entry_date = str(entry.get("date", ""))
    # a backdated row keeps saying which day it counts towards
    date_str = entry_date if entry_date and entry_date != now.date().isoformat() else None
    reply = await message.reply(
        strings.sport_saved(
            revised.title,
            revised.minutes,
            revised.distance_km,
            revised.kcal,
            date_str,
            corrected=True,
        )
    )
    # the row is re-keyed to the new confirmation, so the next correction replies to that one
    await repo.update_sport_entry(message.from_user.id, original_id, revised, reply.message_id)


def build() -> Router:
    router = Router(name="sport")
    # delete first: "видали" must never reach Gemini as a correction of the activity
    router.message.register(on_delete, SportReply("delete"))
    router.message.register(on_kcal_correction, SportReply("kcal"))
    router.message.register(on_text_correction, SportReply("text"))
    return router
