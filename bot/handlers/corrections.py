"""Corrections of a food estimate: reply to the bot's "≈ ..." message.

A number replaces the kcal value directly. Any other text ("це 300 г", "без хліба", "це солянка,
а не борщ") goes to Gemini together with the earlier estimate - and the original photo when the
entry came from one - and the whole row is re-estimated.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Literal

from aiogram import Bot, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.parsing import parse_correction
from bot.sheets import SheetsRepo

log = logging.getLogger(__name__)
_MAX_CORRECTION_TEXT = 500


class CorrectionReply(BaseFilter):
    """A text reply to one of the bot's own food-estimate messages.

    `kind="kcal"` matches a bare number and injects `kcal`; `kind="text"` matches any other text
    and injects `correction_text`.
    """

    def __init__(self, kind: Literal["kcal", "text"]) -> None:
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
        kcal = parse_correction(text)
        if self.kind == "kcal":
            return {"kcal": kcal} if kcal is not None else False
        return {"correction_text": text[:_MAX_CORRECTION_TEXT]} if kcal is None else False


async def on_correction(message: Message, kcal: float, repo: SheetsRepo) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    # scoped to the sender: only the author of a food entry can correct it
    updated = await repo.update_food_kcal(
        message.from_user.id, message.reply_to_message.message_id, kcal
    )
    text = (
        i18n.CORRECTION_SAVED.format(kcal=f"{kcal:.0f}") if updated else i18n.CORRECTION_NOT_FOUND
    )
    await message.reply(text)


async def on_text_correction(
    message: Message, correction_text: str, bot: Bot, repo: SheetsRepo, ai: GeminiClient
) -> None:
    assert message.reply_to_message is not None and message.from_user is not None
    original_id = message.reply_to_message.message_id
    entry = await repo.get_food_entry(message.from_user.id, original_id)
    if entry is None:
        await message.reply(i18n.CORRECTION_NOT_FOUND)
        return
    image = await _download_photo(bot, entry.get("photo_file_id") or "")
    est = await ai.revise_food(image, "image/jpeg", entry, correction_text)
    if not est.is_food:
        await message.reply(i18n.CORRECTION_NOT_UNDERSTOOD)
        return
    reply = await message.reply(
        i18n.food_estimate(
            est.dish,
            est.kcal,
            est.alcohol_kcal,
            est.protein_g,
            est.fat_g,
            est.carbs_g,
            est.veg_share,
            est.confidence,
            est.notes,
            corrected=True,
        )
    )
    # the row is re-keyed to the new reply, so the next correction replies to the latest estimate
    await repo.update_food_entry(message.from_user.id, original_id, est, reply.message_id)


async def _download_photo(bot: Bot, file_id: str) -> bytes | None:
    if not file_id:
        return None
    buffer = BytesIO()
    try:
        await bot.download(file_id, destination=buffer)
    except Exception:  # stale file id: fall back to a text-only revision rather than failing
        log.warning("could not re-download photo %s, revising from text only", file_id)
        return None
    return buffer.getvalue()


def build() -> Router:
    router = Router(name="corrections")
    router.message.register(on_correction, CorrectionReply("kcal"))
    router.message.register(on_text_correction, CorrectionReply("text"))
    return router
