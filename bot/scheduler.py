"""Scheduled jobs: the morning weigh-in ping and the weekly report.

Pings are scheduled per timezone: every night the set of distinct timezones among active users
is recomputed and one cron job per timezone is (re)created, so a user in another country is pinged
at their own `WEIGH_IN_DEADLINE`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import i18n
from bot.ai import GeminiClient
from bot.config import Settings
from bot.reports import build_weekly_payload
from bot.sheets import SheetsRepo, User

log = logging.getLogger(__name__)

PING_JOB_PREFIX = "ping:"


def user_tz(user: User, settings: Settings) -> ZoneInfo:
    """User's timezone from the sheet, falling back to DEFAULT_TZ on empty/invalid values."""
    if user.tz:
        try:
            return ZoneInfo(user.tz)
        except ZoneInfoNotFoundError:
            log.warning("user %s has unknown tz %r, using default", user.user_id, user.tz)
    return settings.tzinfo


def user_now(user: User, settings: Settings) -> datetime:
    return datetime.now(user_tz(user, settings))


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

    # -- morning ping -----------------------------------------------------------------------

    async def refresh_ping_jobs(self) -> None:
        """(Re)create one ping job per distinct user timezone."""
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

    # -- weekly report ----------------------------------------------------------------------

    async def weekly_reports(self) -> None:
        for chat_id in self.settings.allowed_chat_ids:
            try:
                await self.run_weekly_report(chat_id)
            except Exception:
                log.exception("weekly report failed for chat %s", chat_id)

    async def run_weekly_report(self, chat_id: int, today: date | None = None) -> str:
        """Build, send and store the report for the 7 days ending today. Returns the text."""
        today = today or datetime.now(self.settings.tzinfo).date()
        week_start = today - timedelta(days=6)
        payload = await build_weekly_payload(self.repo, chat_id, week_start)
        header = i18n.WEEKLY_HEADER.format(start=week_start.isoformat(), end=today.isoformat())
        if not payload["users"]:
            text = f"{header}\n{i18n.WEEKLY_NO_DATA}"
        else:
            try:
                body = await self.ai.weekly_report(payload)
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
    """Create the scheduler with the nightly refresh and the weekly report registered."""
    scheduler = AsyncIOScheduler(timezone=settings.tzinfo)
    jobs = Jobs(bot, repo, ai, settings, scheduler)
    scheduler.add_job(
        jobs.refresh_ping_jobs,
        CronTrigger(hour=0, minute=5, timezone=settings.tzinfo),
        id="refresh_ping_jobs",
        replace_existing=True,
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
