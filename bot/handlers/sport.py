"""Sport entries from free text ("пробіг 5 км за 30 хв") or `/sport`."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.parsing import looks_like_sport
from bot.scheduler import user_now
from bot.sheets import SheetsRepo


async def record_sport(
    message: Message, text: str, repo: SheetsRepo, ai: GeminiClient, settings: Settings, source: str
) -> None:
    """Parse `text` with Gemini + MET table and store it for the sender."""
    user = await ensure_user(message, repo, settings)
    now = user_now(user, settings)
    previous = await repo.last_weight(user.user_id, now)
    entry = await ai.parse_sport(text, previous[1] if previous else None)
    if entry is None:
        await message.reply(i18n.SPORT_NOT_RECOGNIZED)
        return
    await repo.add_sport(
        user, entry.title, entry.minutes, entry.distance_km, entry.kcal, now, source
    )
    await message.reply(i18n.sport_saved(entry.title, entry.minutes, entry.distance_km, entry.kcal))


async def on_sport_text(
    message: Message, repo: SheetsRepo, ai: GeminiClient, settings: Settings
) -> None:
    assert message.text is not None
    await record_sport(message, message.text, repo, ai, settings, source="text")


def build() -> Router:
    router = Router(name="sport")
    router.message.register(on_sport_text, F.text.func(looks_like_sport), F.from_user)
    return router
