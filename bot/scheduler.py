"""Scheduled jobs: the morning weigh-in ping, the weekly report and the water reminders.

Pings are scheduled per timezone: every night the set of distinct timezones among active users
is recomputed and one cron job per timezone is (re)created, so a user in another country is pinged
at their own `WEIGH_IN_DEADLINE`.

Water reminders use a single job instead: `water_tick` runs every minute and walks an in-memory
copy of the active subscriptions, so an arbitrary per-user interval costs no Sheets read and no
job per subscriber. The copy is refreshed together with the ping jobs (startup and nightly) and
whenever a handler changes a subscription.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.parsing import is_water_due
from bot.reports import build_weekly_payload
from bot.sheets import SheetsRepo, User, WaterSubscription

log = logging.getLogger(__name__)

PING_JOB_PREFIX = "ping:"
WATER_JOB_ID = "water_tick"


def zone_or_default(key: str, settings: Settings) -> ZoneInfo:
    """`ZoneInfo` for a stored zone key, falling back to DEFAULT_TZ on empty/invalid values."""
    if key:
        try:
            return ZoneInfo(key)
        # ZoneInfo raises ValueError (not *NotFound*) for keys like ".." or "/etc/x"
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("unknown timezone %r, using default", key)
    return settings.tzinfo


def user_tz(user: User, settings: Settings) -> ZoneInfo:
    """User's timezone from the sheet, falling back to DEFAULT_TZ on empty/invalid values."""
    return zone_or_default(user.tz, settings)


def is_unreachable_chat(exc: Exception) -> bool:
    """True for the Telegram errors that mean "this private chat can never receive a message".

    `Forbidden` = the user blocked the bot or never pressed Start; "chat not found" = the account
    or the private chat is gone. Anything else (rate limit, network) is transient and must not
    cost the user their subscription.
    """
    if isinstance(exc, TelegramForbiddenError):
        return True
    return isinstance(exc, TelegramBadRequest) and "chat not found" in exc.message.lower()


def user_now(user: User, settings: Settings) -> datetime:
    return datetime.now(user_tz(user, settings))


def is_personal_chat(chat_id: int) -> bool:
    """True for a DM: Telegram gives a private chat the user's own id, and groups a negative one."""
    return chat_id > 0


class Jobs:
    """Job implementations bound to the bot's dependencies."""

    def __init__(
        self,
        bot: Bot,
        repo: SheetsRepo,
        ai: GeminiClient,
        settings: Settings,
        scheduler: AsyncIOScheduler,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.ai = ai
        self.settings = settings
        self.scheduler = scheduler
        self._water: list[WaterSubscription] = []

    # -- morning ping -----------------------------------------------------------------------

    async def refresh_ping_jobs(self) -> None:
        """(Re)create one ping job per distinct user timezone and reload the water schedules."""
        zones = {self.settings.default_tz}
        for chat_id in self.settings.allowed_chat_ids:
            for user in await self.repo.get_active_users(chat_id):
                zones.add(user_tz(user, self.settings).key)
        wanted = {f"{PING_JOB_PREFIX}{z}" for z in zones}
        for job in self.scheduler.get_jobs():
            if job.id.startswith(PING_JOB_PREFIX) and job.id not in wanted:
                job.remove()
        deadline = self.settings.weigh_in_time
        for zone in zones:
            self.scheduler.add_job(
                self.send_pings,
                CronTrigger(hour=deadline.hour, minute=deadline.minute, timezone=ZoneInfo(zone)),
                args=[zone],
                id=f"{PING_JOB_PREFIX}{zone}",
                replace_existing=True,
                misfire_grace_time=600,
            )
        log.info("ping jobs scheduled for %s at %s", sorted(zones), self.settings.weigh_in_deadline)
        # last, and isolated: a broken `water` tab must never cost the group its weigh-in pings
        try:
            await self.reload_water_subscriptions()
        except Exception:
            log.exception("could not reload water subscriptions, keeping the previous list")

    async def send_pings(self, zone: str) -> None:
        """Mention everyone in `zone` who has no weight entry for today."""
        today = datetime.now(ZoneInfo(zone)).date()
        for chat_id in self.settings.allowed_chat_ids:
            users = [
                u
                for u in await self.repo.get_active_users(chat_id)
                if user_tz(u, self.settings).key == zone
            ]
            if not users:
                continue
            weighed = await self.repo.weights_for_date(chat_id, today)
            missing = [u for u in users if u.user_id not in weighed]
            if not missing:
                continue
            mentions = ", ".join(i18n.mention(u.user_id, u.name) for u in missing)
            await self.bot.send_message(chat_id, i18n.PING.format(mentions=mentions))
            log.info("pinged %d users in chat %s", len(missing), chat_id)

    # -- water reminders --------------------------------------------------------------------

    async def reload_water_subscriptions(self) -> None:
        """Re-read the `water` tab into memory (hand edits of the sheet are picked up here)."""
        subs = await self.repo.get_water_subscriptions()
        self._water = [s for s in subs if s.active]
        log.info("water reminders: %d active subscription(s)", len(self._water))

    async def water_tick(self, now_utc: datetime | None = None) -> None:
        """Send a reminder to everyone whose schedule fires this minute."""
        # seconds must not matter: the job may start a moment late and `is_water_due` compares
        # whole minutes against the schedule's grid
        now = (now_utc or datetime.now(UTC)).replace(second=0, microsecond=0)
        for sub in list(self._water):
            try:
                local = now.astimezone(zone_or_default(sub.tz, self.settings))
                if not is_water_due(sub.schedule, local):
                    continue
                await self.bot.send_message(sub.user_id, i18n.WATER_PING)
            except Exception as exc:
                if is_unreachable_chat(exc):
                    await self._drop_unreachable(sub.user_id)
                else:
                    log.exception("water reminder failed for user %s", sub.user_id)

    async def _drop_unreachable(self, user_id: int) -> None:
        """The user blocked the bot or deleted the private chat - stop trying.

        Memory first so the very next tick already skips them; the sheet write may fail on its
        own and must not take the remaining subscribers' reminders down with it.
        """
        log.warning("water: user %s cannot be messaged, unsubscribing", user_id)
        self._water = [s for s in self._water if s.user_id != user_id]
        try:
            await self.repo.deactivate_water_subscription(user_id)
        except Exception:
            log.exception("could not mark water subscription of user %s inactive", user_id)

    # -- weekly report ----------------------------------------------------------------------

    async def group_member_ids(self) -> set[int]:
        """User ids active in any allowed group chat (used to skip redundant personal reports)."""
        members: set[int] = set()
        for chat_id in self.settings.allowed_chat_ids:
            if is_personal_chat(chat_id):
                continue
            members.update(u.user_id for u in await self.repo.get_active_users(chat_id))
        return members

    async def weekly_reports(self) -> None:
        # Somebody who tracks in a group already gets their numbers in the group report; a second,
        # identical report in their DM would just be noise. A DM's chat id is the user's own id.
        try:
            in_a_group = await self.group_member_ids()
        except Exception:
            # Reading the group rosters is only an optimisation: if Sheets is flaky here, fall back
            # to an empty set so every chat still gets its report. A redundant DM report is
            # recoverable noise; a week with no report at all is lost data nobody notices in time.
            log.exception("could not list group members, sending every report including DMs")
            in_a_group = set()
        for chat_id in self.settings.allowed_chat_ids:
            if is_personal_chat(chat_id) and chat_id in in_a_group:
                log.info("skipping personal weekly report for %s: covered by a group", chat_id)
                continue
            try:
                await self.run_weekly_report(chat_id)
            except Exception:
                log.exception("weekly report failed for chat %s", chat_id)

    async def run_weekly_report(self, chat_id: int, today: date | None = None) -> str:
        """Build, send and store the report for the 7 full days before `today`.

        Today is deliberately left out: the job runs on Monday morning, so the report covers the
        week that just ended (previous Monday..Sunday) and never a half-logged current day.
        """
        today = today or datetime.now(self.settings.tzinfo).date()
        week_end = today - timedelta(days=1)
        week_start = week_end - timedelta(days=6)
        payload = await build_weekly_payload(self.repo, chat_id, week_start)
        header = i18n.WEEKLY_HEADER.format(start=week_start.isoformat(), end=week_end.isoformat())
        if not payload["users"]:
            text = f"{header}\n{i18n.WEEKLY_NO_DATA}"
        else:
            try:
                # deliberately no `on_retry` notice: this is a background job, nobody is waiting
                # on it, and a failure already degrades to the numbers-only fallback below
                body = await self.ai.weekly_report(payload, personal=is_personal_chat(chat_id))
                text = f"{header}\n\n{body}"
            except Exception:
                log.exception("Gemini weekly report failed, sending numbers only")
                stats = "\n".join(i18n.weekly_stats_block(u) for u in payload["users"])
                text = f"{header}\n{i18n.WEEKLY_AI_FAILED}\n\n{stats}"
        # parse_mode=None: the AI text is free-form and would trip Telegram's HTML parser
        await self.bot.send_message(chat_id, text, parse_mode=None)
        await self.repo.add_report(week_start, chat_id, text, datetime.now(self.settings.tzinfo))
        return text


def build_scheduler(
    bot: Bot, repo: SheetsRepo, ai: GeminiClient, settings: Settings
) -> tuple[AsyncIOScheduler, Jobs]:
    """Create the scheduler with the nightly refresh, the water tick and the weekly report."""
    scheduler = AsyncIOScheduler(timezone=settings.tzinfo)
    jobs = Jobs(bot, repo, ai, settings, scheduler)
    scheduler.add_job(
        jobs.refresh_ping_jobs,
        CronTrigger(hour=0, minute=5, timezone=settings.tzinfo),
        id="refresh_ping_jobs",
        replace_existing=True,
    )
    scheduler.add_job(
        jobs.water_tick,
        CronTrigger(minute="*", timezone=settings.tzinfo),
        id=WATER_JOB_ID,
        replace_existing=True,
        misfire_grace_time=30,
    )
    report_at = settings.weekly_report_at
    scheduler.add_job(
        jobs.weekly_reports,
        CronTrigger(
            day_of_week=settings.weekly_report_day,
            hour=report_at.hour,
            minute=report_at.minute,
            timezone=settings.tzinfo,
        ),
        id="weekly_report",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return scheduler, jobs
