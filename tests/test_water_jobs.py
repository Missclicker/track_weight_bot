"""The per-minute water reminder tick.

`Jobs.water_tick` is driven with an explicit UTC instant, so the whole schedule -> timezone ->
private message path is testable without a clock or a network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import i18n
from bot.config import Settings
from bot.parsing import parse_water_schedule
from bot.scheduler import WATER_JOB_ID, Jobs, build_scheduler
from bot.sheets import WaterSubscription
from tests.conftest import FakeRepo

# a Monday; 07:00 UTC is 09:00 in Kyiv (winter, UTC+2) and 07:00 in Lisbon (UTC+0)
MONDAY_0700_UTC = datetime(2026, 1, 5, 7, 0, tzinfo=UTC)


class FakeBot:
    """Records `send_message` calls; raises for chats listed in the failure sets."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.forbidden: set[int] = set()  # the user blocked the bot
        self.gone: set[int] = set()  # the account / private chat no longer exists
        self.flaky: set[int] = set()  # some other 400 - a bug on our side, not the user's
        self.failing: set[int] = set()  # network / 5xx

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        method = SendMessage(chat_id=chat_id, text=text)
        if chat_id in self.forbidden:
            raise TelegramForbiddenError(
                method=method, message="Forbidden: bot was blocked by the user"
            )
        if chat_id in self.gone:
            raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
        if chat_id in self.flaky:
            raise TelegramBadRequest(method=method, message="Bad Request: message is too long")
        if chat_id in self.failing:
            raise RuntimeError("telegram is having a bad day")
        self.sent.append((chat_id, text))


def _sub(user_id: int, text: str, tz: str = "Europe/Kyiv") -> WaterSubscription:
    schedule = parse_water_schedule(text)
    assert schedule is not None
    return WaterSubscription(
        user_id=user_id, chat_id=-100, name=f"user{user_id}", tz=tz, schedule=schedule
    )


@pytest.fixture
def water(repo: FakeRepo, settings: Settings) -> tuple[Jobs, FakeBot, FakeRepo]:
    bot = FakeBot()
    jobs = Jobs(bot, repo, None, settings, AsyncIOScheduler())  # type: ignore[arg-type]
    return jobs, bot, repo


async def _load(jobs: Jobs, repo: FakeRepo, *subs: WaterSubscription) -> None:
    for sub in subs:
        await repo.upsert_water_subscription(sub)
    await jobs.reload_water_subscriptions()


async def test_only_due_subscribers_are_pinged(water) -> None:
    jobs, bot, repo = water
    await _load(
        jobs,
        repo,
        _sub(1, "будні з 9 до 18 кожні 30 хвилин"),  # 09:00 Kyiv - due
        _sub(2, "вихідні з 9 до 18 кожні 30 хвилин"),  # Monday is not a weekend
        _sub(3, "будні з 10 до 18 кожні 30 хвилин"),  # window has not started
    )
    await jobs.water_tick(MONDAY_0700_UTC)
    assert bot.sent == [(1, i18n.WATER_PING)]


async def test_seconds_do_not_shift_the_grid(water) -> None:
    jobs, bot, repo = water
    await _load(jobs, repo, _sub(1, "будні з 9 до 18 кожні 30 хвилин"))
    await jobs.water_tick(MONDAY_0700_UTC.replace(second=41, microsecond=500))
    assert bot.sent == [(1, i18n.WATER_PING)]


async def test_each_subscriber_is_evaluated_in_their_own_timezone(water) -> None:
    jobs, bot, repo = water
    await _load(
        jobs,
        repo,
        _sub(1, "будні з 9 до 18 кожні 30 хвилин"),  # Kyiv: 09:00
        _sub(2, "будні з 9 до 18 кожні 30 хвилин", tz="Europe/Lisbon"),  # Lisbon: 07:00
        _sub(3, "будні з 9 до 18 кожні 30 хвилин", tz="Not/AZone"),  # falls back to DEFAULT_TZ
    )
    await jobs.water_tick(MONDAY_0700_UTC)
    assert [chat_id for chat_id, _ in bot.sent] == [1, 3]


async def test_forbidden_recipient_is_unsubscribed(water) -> None:
    jobs, bot, repo = water
    await _load(
        jobs,
        repo,
        _sub(1, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(2, "будні з 9 до 18 кожні 30 хвилин"),
    )
    bot.forbidden.add(1)
    await jobs.water_tick(MONDAY_0700_UTC)
    assert bot.sent == [(2, i18n.WATER_PING)]
    row = next(r for r in repo.rows["water"] if r["user_id"] == 1)
    assert row["active"] == "FALSE"

    bot.forbidden.clear()  # even if they unblock the bot, the next tick skips them
    await jobs.water_tick(MONDAY_0700_UTC)
    assert bot.sent == [(2, i18n.WATER_PING), (2, i18n.WATER_PING)]


async def test_deleted_chat_is_unsubscribed_but_other_bad_requests_are_not(water) -> None:
    jobs, bot, repo = water
    await _load(
        jobs,
        repo,
        _sub(1, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(2, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(3, "будні з 9 до 18 кожні 30 хвилин"),
    )
    bot.gone.add(1)
    bot.flaky.add(2)
    await jobs.water_tick(MONDAY_0700_UTC)
    assert bot.sent == [(3, i18n.WATER_PING)]
    active = {r["user_id"] for r in repo.rows["water"] if r["active"] == "TRUE"}
    assert active == {2, 3}  # "chat not found" is final, "message is too long" is our bug
    assert [s.user_id for s in jobs._water] == [2, 3]


async def test_sheet_failure_while_unsubscribing_does_not_stop_the_tick(water, monkeypatch):
    jobs, bot, repo = water
    await _load(
        jobs,
        repo,
        _sub(1, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(2, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(3, "будні з 9 до 18 кожні 30 хвилин"),
    )
    bot.forbidden.add(1)

    async def boom(user_id: int) -> bool:
        raise RuntimeError("Sheets 500 after retries")

    monkeypatch.setattr(repo, "deactivate_water_subscription", boom)
    await jobs.water_tick(MONDAY_0700_UTC)  # must not raise
    assert [chat_id for chat_id, _ in bot.sent] == [2, 3]
    assert [s.user_id for s in jobs._water] == [2, 3]  # dropped from memory even so


async def test_one_failing_send_does_not_stop_the_others(water) -> None:
    jobs, bot, repo = water
    await _load(
        jobs,
        repo,
        _sub(1, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(2, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(3, "будні з 9 до 18 кожні 30 хвилин"),
    )
    bot.failing.add(2)
    await jobs.water_tick(MONDAY_0700_UTC)
    assert [chat_id for chat_id, _ in bot.sent] == [1, 3]
    assert len(repo.rows["water"]) == 3  # a plain error does not unsubscribe anybody


async def test_reload_keeps_only_active_subscriptions(water) -> None:
    jobs, bot, repo = water
    await _load(
        jobs,
        repo,
        _sub(1, "будні з 9 до 18 кожні 30 хвилин"),
        _sub(2, "будні з 9 до 18 кожні 30 хвилин"),
    )
    await repo.deactivate_water_subscription(2)
    await jobs.reload_water_subscriptions()
    await jobs.water_tick(MONDAY_0700_UTC)
    assert bot.sent == [(1, i18n.WATER_PING)]


async def test_nightly_refresh_reloads_water_but_a_broken_tab_keeps_the_pings(
    water, monkeypatch
) -> None:
    """Startup and the 00:05 job both go through `refresh_ping_jobs`; that is the only reload
    point besides the handler, and it must never take the weigh-in pings down with it."""
    jobs, _, repo = water
    await repo.upsert_water_subscription(_sub(1, "кожні 30 хв"))
    await jobs.refresh_ping_jobs()
    assert [s.user_id for s in jobs._water] == [1]
    assert jobs.scheduler.get_job("ping:Europe/Kyiv") is not None

    async def boom() -> list[WaterSubscription]:
        raise RuntimeError("water tab unreadable")

    monkeypatch.setattr(repo, "get_water_subscriptions", boom)
    await jobs.refresh_ping_jobs()  # must not raise
    assert [s.user_id for s in jobs._water] == [1]  # previous list kept
    assert jobs.scheduler.get_job("ping:Europe/Kyiv") is not None


def test_build_scheduler_registers_the_minute_tick(repo: FakeRepo, settings: Settings) -> None:
    scheduler, _ = build_scheduler(FakeBot(), repo, None, settings)  # type: ignore[arg-type]
    job = scheduler.get_job(WATER_JOB_ID)
    assert job is not None
    assert "minute='*'" in str(job.trigger)
    assert job.misfire_grace_time == 30
    assert {j.id for j in scheduler.get_jobs()} == {
        "refresh_ping_jobs",
        WATER_JOB_ID,
        "weekly_report",
    }
