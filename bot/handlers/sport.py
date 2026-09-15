"""Sport entries. Reached only through `/sport` (or a reply to its prompt) in `commands.py`:
free text is deliberately not scanned for sport keywords any more, the false positives in a
chatty group outweighed the convenience.
"""

from __future__ import annotations

from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
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
