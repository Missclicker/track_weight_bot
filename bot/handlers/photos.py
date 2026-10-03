"""Food photos: download the largest size, ask Gemini, reply, store with the reply's message_id.

`record_food` is shared with `/food` in `commands.py`: it adds the running total for the day to
the reply and appends the row.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from functools import partial
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot import i18n
from bot.ai import FoodEstimate, GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.i18n import DEFAULT_LANG, Lang
from bot.parsing import strip_yesterday
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
    *,
    yesterday: bool = False,
    at: time | None = None,
    lang: Lang = DEFAULT_LANG,
) -> None:
    """Reply with the estimate plus the day's total (this entry included) and store the row.

    With `yesterday` the meal counts towards the previous day in the user's timezone: the running
    total is read for that day and the reply names it, so nobody has to guess which day the
    number covers. The row's `ts` says now - when it was typed - unless `at` states the time of
    the meal ("/їжа 14:00 борщ"): then `ts` is that clock on the meal's own day, with the offset
    the user's zone has on that date, so `/kcal` and the late-meal count read the stated time.
    A time later than now is taken as stated. The reply is in `lang`, the sender's language.
    """
    strings = i18n.t(lang)
    now = user_now(user, settings)
    day = now.date() - timedelta(days=1) if yesterday else now.date()
    when = now if at is None else datetime.combine(day, at, tzinfo=now.tzinfo)
    before = await day_food(repo, user.user_id, day)
    total_line = strings.day_total(
        before.total_kcal + est.kcal,
        user.daily_kcal_target,
        day.isoformat() if yesterday else None,
    )
    reply = await message.reply(
        strings.food_estimate(
            est.dish,
            est.kcal,
            est.alcohol_kcal,
            est.protein_g,
            est.fat_g,
            est.carbs_g,
            est.veg_share,
            est.notes,
            portion=est.portion,
            day_total_line=total_line,
        )
    )
    await repo.add_food(
        user, est, when, source, reply.message_id, photo_file_id, day=day if yesterday else None
    )


async def on_photo(
    message: Message, bot: Bot, repo: SheetsRepo, ai: GeminiClient, settings: Settings, lang: Lang
) -> None:
    # First, before anything costs us (only the sender's language has been read, from the cached
    # `users` tab): when the vision quota is spent or the model is riding out a demand spike,
    # paying for a photo download and a fresh Sheets read only to hear it from Gemini
    # would be wasteful. With the Groq fallback configured this never raises: Groq can still
    # answer, so the photo is worth downloading after all.
    ai.check_available(ai.vision_model)
    assert message.photo is not None
    largest = max(message.photo, key=lambda p: p.width * p.height)
    buffer = BytesIO()
    await bot.download(largest, destination=buffer)
    user = await ensure_user(message, repo, settings)
    # "вчора" in the caption dates the meal, so it is cut out before the model sees it: left in,
    # it would end up in the dish name and invite the model to reason about the date itself.
    caption, yesterday = strip_yesterday(message.caption)
    # a photo estimate can take a couple of minutes on a busy hour; say so once if we have to retry
    est = await ai.estimate_food(
        buffer.getvalue(),
        "image/jpeg",
        caption or None,
        on_retry=partial(message.reply, i18n.t(lang).AI_RETRYING),
        lang=lang,
    )
    if not est.is_food:
        await message.reply(i18n.t(lang).FOOD_NOT_FOOD)
        return
    await record_food(
        message,
        user,
        est,
        repo,
        settings,
        "photo",
        largest.file_id,
        yesterday=yesterday,
        lang=lang,
    )


def build() -> Router:
    router = Router(name="photos")
    router.message.register(on_photo, F.photo, F.from_user)
    return router
