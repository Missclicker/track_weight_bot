"""Slash commands: /start /help /w /food /sport /today /kcal /target /week."""

from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.handlers import ensure_user
from bot.handlers.photos import record_food
from bot.handlers.sport import record_sport
from bot.handlers.weight import record_weight
from bot.parsing import (
    KCAL_TARGET_MAX,
    KCAL_TARGET_MIN,
    is_target_clear_request,
    parse_kcal_target,
    parse_weight,
)
from bot.reports import day_food, today_summary
from bot.scheduler import Jobs, user_now
from bot.sheets import SheetsRepo


async def cmd_start(message: Message, repo: SheetsRepo, settings: Settings) -> None:
    user = await ensure_user(message, repo, settings)
    await message.answer(
        i18n.START_REGISTERED.format(name=escape(user.name), deadline=settings.weigh_in_deadline)
    )


async def cmd_help(message: Message) -> None:
    await message.answer(i18n.HELP)


async def cmd_weight(
    message: Message, command: CommandObject, repo: SheetsRepo, settings: Settings
) -> None:
    kg = parse_weight(command.args, settings.weight_min, settings.weight_max)
    if kg is None:
        text = (
            i18n.WEIGHT_USAGE
            if not command.args
            else i18n.WEIGHT_OUT_OF_RANGE.format(lo=settings.weight_min, hi=settings.weight_max)
        )
        await message.reply(text)
        return
    await record_weight(message, kg, repo, settings, source="command")


async def cmd_food(
    message: Message, command: CommandObject, repo: SheetsRepo, ai: GeminiClient, settings: Settings
) -> None:
    if not command.args:
        await message.reply(i18n.FOOD_USAGE)
        return
    user = await ensure_user(message, repo, settings)
    est = await ai.estimate_food(None, None, command.args)
    if not est.is_food:
        await message.reply(i18n.FOOD_NOT_FOOD)
        return
    await record_food(message, user, est, repo, settings, "text")


async def cmd_sport(
    message: Message, command: CommandObject, repo: SheetsRepo, ai: GeminiClient, settings: Settings
) -> None:
    if not command.args:
        await message.reply(i18n.SPORT_USAGE)
        return
    await record_sport(message, command.args, repo, ai, settings, source="command")


async def cmd_today(message: Message, repo: SheetsRepo, settings: Settings) -> None:
    user = await ensure_user(message, repo, settings)
    today = user_now(user, settings).date()
    s = await today_summary(repo, user, today)
    await message.reply(
        i18n.today_summary(
            user.name,
            today.isoformat(),
            s.kcal_in,
            s.alcohol_kcal,
            s.sport_kcal,
            s.sport_minutes,
            s.weight,
            s.food_entries,
            user.daily_kcal_target,
        )
    )


async def cmd_kcal(message: Message, repo: SheetsRepo, settings: Settings) -> None:
    user = await ensure_user(message, repo, settings)
    today = user_now(user, settings).date()
    food = await day_food(repo, user.user_id, today)
    await message.reply(
        i18n.kcal_today(user.name, today.isoformat(), food.items, user.daily_kcal_target)
    )


async def cmd_target(
    message: Message, command: CommandObject, repo: SheetsRepo, settings: Settings
) -> None:
    user = await ensure_user(message, repo, settings)
    if not command.args:
        if user.daily_kcal_target:
            text = i18n.TARGET_CURRENT.format(kcal=f"{user.daily_kcal_target:.0f}")
        else:
            text = i18n.TARGET_NONE
        await message.reply(text)
        return
    if is_target_clear_request(command.args):
        await repo.set_daily_kcal_target(user.user_id, user.chat_id, None)
        await message.reply(i18n.TARGET_CLEARED)
        return
    target = parse_kcal_target(command.args)
    if target is None:
        await message.reply(i18n.TARGET_USAGE.format(lo=KCAL_TARGET_MIN, hi=KCAL_TARGET_MAX))
        return
    await repo.set_daily_kcal_target(user.user_id, user.chat_id, target)
    await message.reply(i18n.TARGET_SET.format(kcal=f"{target:.0f}"))


async def cmd_week(message: Message, jobs: Jobs) -> None:
    await jobs.run_weekly_report(message.chat.id)


def build() -> Router:
    router = Router(name="commands")
    aliases = i18n.COMMANDS
    # CommandStart keeps deep-link payload handling for the canonical /start; the Ukrainian
    # spelling is a plain alias.
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_start, Command(*aliases["start"][1:]))
    router.message.register(cmd_help, Command(*aliases["help"]))
    router.message.register(cmd_weight, Command(*aliases["w"]))
    router.message.register(cmd_food, Command(*aliases["food"]))
    router.message.register(cmd_sport, Command(*aliases["sport"]))
    router.message.register(cmd_today, Command(*aliases["today"]))
    router.message.register(cmd_kcal, Command(*aliases["kcal"]))
    router.message.register(cmd_target, Command(*aliases["target"]))
    router.message.register(cmd_week, Command(*aliases["week"]))
    return router
