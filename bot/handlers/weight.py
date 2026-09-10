"""Weight entries: a bare number in range, or a number in reply to the morning ping."""

from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot import i18n
from bot.config import Settings
from bot.handlers import ensure_user
from bot.parsing import parse_weight
from bot.scheduler import user_now
from bot.sheets import SheetsRepo


def is_ping_reply(message: Message, bot: Bot) -> bool:
    """True when `message` replies to one of the bot's own morning pings.

    Recognised by the message text rather than a remembered id so it survives bot restarts.
    """
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or reply.from_user.id != bot.id:
        return False
    return (reply.text or "").startswith(i18n.PING_PREFIX)


class WeightText(BaseFilter):
    """Match a weight number that is either not a reply or a reply to the bot's morning ping.

    On success injects `kg` and `source` into the handler.
    """

    async def __call__(
        self, message: Message, settings: Settings, bot: Bot
    ) -> bool | dict[str, object]:
        if message.from_user is None:
            return False
        kg = parse_weight(message.text, settings.weight_min, settings.weight_max)
        if kg is None:
            return False
        if message.reply_to_message is None:
            return {"kg": kg, "source": "text"}
        if is_ping_reply(message, bot):
            return {"kg": kg, "source": "ping"}
        return False


async def record_weight(
    message: Message, kg: float, repo: SheetsRepo, settings: Settings, source: str
) -> None:
    """Store a weigh-in for the sender and confirm with the delta vs the previous entry."""
    user = await ensure_user(message, repo, settings)
    now = user_now(user, settings)
    previous = await repo.last_weight(user.user_id, now)
    await repo.add_weight(user, kg, now, source)
    if previous is None:
        text = i18n.WEIGHT_FIRST.format(kg=i18n.fmt_kg(kg))
    else:
        prev_date, prev_kg = previous
        delta = round(kg - prev_kg, 1)
        if delta == 0:
            text = i18n.WEIGHT_SAME.format(kg=i18n.fmt_kg(kg), prev_date=prev_date.isoformat())
        else:
            text = i18n.WEIGHT_WITH_DELTA.format(
                kg=i18n.fmt_kg(kg),
                prev=i18n.fmt_kg(prev_kg),
                prev_date=prev_date.isoformat(),
                delta=i18n.fmt_delta(delta),
            )
    await message.reply(text)


async def on_weight(
    message: Message, kg: float, source: str, repo: SheetsRepo, settings: Settings
) -> None:
    await record_weight(message, kg, repo, settings, source)


def build() -> Router:
    router = Router(name="weight")
    router.message.register(on_weight, WeightText())
    return router
