"""Corrections of a food estimate: reply to the bot's "≈ ..." message.

A number replaces the kcal value directly. A delete word ("видали") throws the row away. Any
other text ("це 300 г", "без хліба", "це солянка, а не борщ") goes to Gemini together with the
earlier estimate - text only, never the original photo - and the whole row is re-estimated.
"""

from __future__ import annotations

from datetime import date
from functools import partial
from typing import Literal

from aiogram import Bot, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.parsing import is_delete_request, parse_correction
from bot.reports import day_food
from bot.scheduler import user_now
from bot.sheets import SheetsRepo, User

_MAX_CORRECTION_TEXT = 500


class CorrectionReply(BaseFilter):
    """A text reply to one of the bot's own food-estimate messages.

    `kind="kcal"` matches a bare number and injects `kcal`; `kind="delete"` matches a delete word;
    `kind="text"` matches any other text and injects `correction_text`. The three kinds are
    mutually exclusive, so the registration order in `build()` only decides which handler is
    tried first, never what a message means.
    """

    def __init__(self, kind: Literal["kcal", "text", "delete"]) -> None:
        self.kind = kind

    async def __call__(self, message: Message, bot: Bot) -> bool | dict[str, object]:
        reply = message.reply_to_message
        if reply is None or reply.from_user is None or reply.from_user.id != bot.id:
            return False
        if not (reply.text or "").startswith(i18n.FOOD_PREFIX):
            return False
        text = (message.text or "").strip()
        if not text:
            return False
        if is_delete_request(text):
            return self.kind == "delete"
        if self.kind == "delete":
            return False
        kcal = parse_correction(text)
        if self.kind == "kcal":
            return {"kcal": kcal} if kcal is not None else False
        return {"correction_text": text[:_MAX_CORRECTION_TEXT]} if kcal is None else False


async def _day_total_line(
    repo: SheetsRepo, settings: Settings, user: User, entry_date: str, kcal_delta: float = 0
) -> str:
    """Total for the day the corrected entry belongs to. `kcal_delta` accounts for an update
    that is written only after the reply is sent."""
    today = user_now(user, settings).date()
    day = date.fromisoformat(entry_date) if entry_date else today
    total = (await day_food(repo, user.user_id, day)).total_kcal + kcal_delta
    return i18n.day_total(total, user.daily_kcal_target, None if day == today else day.isoformat())


async def on_correction(
    message: Message, kcal: float, repo: SheetsRepo, settings: Settings
) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    original_id = message.reply_to_message.message_id
    # scoped to the sender: only the author of a food entry can correct it
    entry = await repo.get_food_entry(message.from_user.id, original_id)
    if entry is None or not await repo.update_food_kcal(message.from_user.id, original_id, kcal):
        await message.reply(i18n.CORRECTION_NOT_FOUND)
        return
    user = await ensure_user(message, repo, settings)
    total_line = await _day_total_line(repo, settings, user, str(entry.get("date", "")))
    saved = i18n.CORRECTION_SAVED.format(kcal=f"{kcal:.0f}")
    await message.reply(f"{saved} {total_line}")


async def on_delete(message: Message, repo: SheetsRepo, settings: Settings) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    # scoped to the sender, like every other branch here: only the author may delete an entry
    entry = await repo.delete_food_entry(message.from_user.id, message.reply_to_message.message_id)
    if entry is None:
        await message.reply(i18n.CORRECTION_NOT_FOUND)
        return
    user = await ensure_user(message, repo, settings)
    # the row is already gone, so the total read back here needs no delta
    total_line = await _day_total_line(repo, settings, user, str(entry.get("date", "")))
    await message.reply(f"{i18n.FOOD_DELETED} {total_line}")


async def on_text_correction(
    message: Message,
    correction_text: str,
    repo: SheetsRepo,
    ai: GeminiClient,
    settings: Settings,
) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    original_id = message.reply_to_message.message_id
    entry = await repo.get_food_entry(message.from_user.id, original_id)
    if entry is None:
        await message.reply(i18n.CORRECTION_NOT_FOUND)
        return
    est = await ai.revise_food(
        entry, correction_text, on_retry=partial(message.reply, i18n.AI_RETRYING)
    )
    if not est.is_food:
        await message.reply(i18n.CORRECTION_NOT_UNDERSTOOD)
        return
    user = await ensure_user(message, repo, settings)
    # the row is rewritten only after the reply, so swap the old kcal for the new one here
    total_line = await _day_total_line(
        repo, settings, user, str(entry.get("date", "")), est.kcal - float(entry["kcal"])
    )
    reply = await message.reply(
        i18n.food_estimate(
            est.dish,
            est.kcal,
            est.alcohol_kcal,
            est.protein_g,
            est.fat_g,
            est.carbs_g,
            est.veg_share,
            est.notes,
            portion=est.portion,
            corrected=True,
            day_total_line=total_line,
        )
    )
    # the row is re-keyed to the new reply, so the next correction replies to the latest estimate
    await repo.update_food_entry(message.from_user.id, original_id, est, reply.message_id)


def build() -> Router:
    router = Router(name="corrections")
    # delete first: "видали" must never reach Gemini as a correction of the dish
    router.message.register(on_delete, CorrectionReply("delete"))
    router.message.register(on_correction, CorrectionReply("kcal"))
    router.message.register(on_text_correction, CorrectionReply("text"))
    return router
