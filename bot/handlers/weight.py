"""Weight entries: a number on its own, or a number in reply to one of the bot's own messages."""

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


def replied_bot_text(message: Message, bot: Bot) -> str | None:
    """The text of the bot's own message `message` answers, or None when it answers nothing or
    somebody else.

    Every caller then decides by the text *prefix* which of our messages it was - a remembered
    message id would not survive a restart, and the bot is restarted far more often than a
    conversation ends.
    """
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or reply.from_user.id != bot.id:
        return None
    return reply.text or ""


def weigh_in(message: Message, bot: Bot, settings: Settings) -> tuple[float, str] | None:
    """The weight and the `weight.source` this text message should be stored under, or None when
    it is not a weigh-in.

    People do answer whatever bot message is on screen with today's number, so any reply to us
    counts - "ping" for the morning ping, "reply" for everything else, "text" for a number that
    replies to nothing. A reply to another *person* is conversation, never a measurement.

    The one bot message that takes a stricter number is the `≈` food estimate, where a bare
    integer in the weight range is far more likely a kcal correction of the portion: there the
    number has to carry a decimal, a unit or a label (`parse_weight(require_marker=True)`).
    `corrections.CorrectionReply` asks this same function, so the two filters stay mutually
    exclusive and the router order decides only which one is tried first.
    """
    if message.from_user is None:
        return None
    replied = replied_bot_text(message, bot)
    if replied is None and message.reply_to_message is not None:
        return None
    kg = parse_weight(
        message.text,
        settings.weight_min,
        settings.weight_max,
        require_marker=replied is not None and replied.startswith(i18n.FOOD_PREFIX),
    )
    if kg is None:
        return None
    if replied is None:
        return kg, "text"
    if replied.startswith(i18n.PING_PREFIX):
        return kg, "ping"
    return kg, "reply"


class WeightText(BaseFilter):
    """Match a weight number that is not a reply, or one replying to a bot message (`weigh_in`).

    On success injects `kg` and `source` into the handler. A reply to the `/їжа` or `/спорт`
    prompt is not excluded here: `commands` is the first router inside `guarded`, so a number
    answering a prompt is recorded as food or sport before this filter is ever asked.
    """

    async def __call__(
        self, message: Message, settings: Settings, bot: Bot
    ) -> bool | dict[str, object]:
        found = weigh_in(message, bot, settings)
        if found is None:
            return False
        kg, source = found
        return {"kg": kg, "source": source}


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
