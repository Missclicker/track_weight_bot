"""Food photos: download the largest size, ask Gemini, reply, store with the reply's message_id."""

from __future__ import annotations

from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.scheduler import user_now
from bot.sheets import SheetsRepo


async def on_photo(
    message: Message, bot: Bot, repo: SheetsRepo, ai: GeminiClient, settings: Settings
) -> None:
    assert message.photo is not None
    largest = max(message.photo, key=lambda p: p.width * p.height)
    buffer = BytesIO()
    await bot.download(largest, destination=buffer)
    user = await ensure_user(message, repo, settings)
    est = await ai.estimate_food(buffer.getvalue(), "image/jpeg", message.caption)
    if not est.is_food:
        await message.reply(i18n.FOOD_NOT_FOOD)
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
        )
    )
    await repo.add_food(
        user, est, user_now(user, settings), "photo", reply.message_id, largest.file_id
    )


def build() -> Router:
    router = Router(name="photos")
    router.message.register(on_photo, F.photo, F.from_user)
    return router
