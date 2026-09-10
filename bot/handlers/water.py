"""Water reminders: `/вода будні дні з 9 до 18 кожні 30 хвилин`.

The pings are private messages, and Telegram only lets a bot write to someone who has pressed
Start in the DM - so the command sends the confirmation to the private chat first and stores the
subscription only when that succeeded. The command itself works in the group and in the DM.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import i18n
from bot.config import Settings
from bot.handlers import ensure_user, find_member
from bot.parsing import parse_water_schedule
from bot.scheduler import Jobs, is_unreachable_chat, user_tz
from bot.sheets import SheetsRepo, User, WaterSubscription

_STOP_WORDS = frozenset({"стоп", "stop", "off", "вимкнути", "видалити", "скасувати"})


async def _acting_user(message: Message, repo: SheetsRepo, settings: Settings) -> User | None:
    """The sender as a member of the group, or None when they are not one.

    In an allowed chat first contact registers the sender as everywhere else; in an ordinary
    private chat only an existing member is served.
    """
    assert message.from_user is not None
    if message.chat.id in settings.allowed_chat_ids:
        return await ensure_user(message, repo, settings)
    return await find_member(message.from_user.id, repo, settings)


async def _status(repo: SheetsRepo, user: User) -> str:
    subs = await repo.get_water_subscriptions()
    mine = next((s for s in subs if s.user_id == user.user_id and s.active), None)
    if mine is None:
        return i18n.WATER_USAGE
    return i18n.WATER_STATUS.format(schedule=i18n.fmt_water_schedule(mine.schedule), tz=mine.tz)


async def cmd_water(
    message: Message,
    command: CommandObject,
    repo: SheetsRepo,
    settings: Settings,
    jobs: Jobs,
    bot: Bot,
) -> None:
    """Show, set or cancel the sender's water reminders."""
    user = await _acting_user(message, repo, settings)
    if user is None:
        await message.answer(i18n.PRIVATE_CHAT_ONLY_GROUP)
        return
    args = (command.args or "").strip()
    if not args:
        await message.reply(await _status(repo, user))
        return
    if args.lower() in _STOP_WORDS:
        stopped = await repo.deactivate_water_subscription(user.user_id)
        await jobs.reload_water_subscriptions()
        await message.reply(i18n.WATER_STOPPED if stopped else i18n.WATER_NOT_SUBSCRIBED)
        return
    schedule = parse_water_schedule(args)
    if schedule is None:
        await message.reply(i18n.WATER_USAGE)
        return
    summary = i18n.fmt_water_schedule(schedule)
    try:
        await bot.send_message(user.user_id, i18n.WATER_SUBSCRIBED_DM.format(schedule=summary))
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        # "bot can't initiate conversation with a user" / "chat not found": nothing is stored,
        # otherwise every tick would try to write into a chat that does not exist. Any other
        # 400 is our bug, not a missing Start - let the error handler report it.
        if not is_unreachable_chat(exc):
            raise
        me = await bot.me()
        link = f"https://t.me/{me.username}?start=water"
        await message.reply(i18n.WATER_NEED_DM.format(link=link))
        return
    tz = user_tz(user, settings)
    await repo.upsert_water_subscription(
        WaterSubscription(
            user_id=user.user_id,
            chat_id=message.chat.id,
            name=user.name,
            tz=tz.key,
            schedule=schedule,
            updated_at=datetime.now(tz).isoformat(timespec="seconds"),
        )
    )
    await jobs.reload_water_subscriptions()
    if message.chat.type != ChatType.PRIVATE:
        # in the DM the message above is already the confirmation
        await message.reply(i18n.WATER_SUBSCRIBED.format(schedule=summary, tz=tz.key))


def build() -> Router:
    router = Router(name="water")
    router.message.register(cmd_water, Command(*i18n.COMMANDS["water"]))
    return router
