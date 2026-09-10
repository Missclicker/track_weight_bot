"""Food photos: download the largest size, ask Gemini, reply, store with the reply's message_id.

`record_food` is shared with `/food` in `commands.py`: it adds the running total for the day to
the reply and appends the row.
"""

from __future__ import annotations

from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot import i18n
from bot.ai import FoodEstimate, GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.reports import day_food
from bot.scheduler import user_now
from bot.sheets import SheetsRepo, User


async def record_food(
    message: Message,
    user: User,
    est: FoodEstimate,
    repo: SheetsRepo,
    settings: Settings,
    source: str,
    photo_file_id: str = "",
) -> None:
    """Reply with the estimate plus today's total (this entry included) and store the row."""
    now = user_now(user, settings)
    before = await day_food(repo, user.user_id, now.date())
    total_line = i18n.day_total(before.total_kcal + est.kcal, user.daily_kcal_target)
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
            day_total_line=total_line,
        )
    )
    await repo.add_food(user, est, now, source, reply.message_id, photo_file_id)


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
    await record_food(message, user, est, repo, settings, "photo", largest.file_id)


def build() -> Router:
    router = Router(name="photos")
    router.message.register(on_photo, F.photo, F.from_user)
    return router
