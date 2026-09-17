"""The weekly report job: its window, the group/personal wording and who it is sent to.

`Jobs.run_weekly_report` takes an explicit `today`, so the whole window -> payload -> Gemini ->
send path is testable without a clock or a network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.ai import FoodEstimate
from bot.config import Settings
from bot.scheduler import Jobs, build_scheduler, is_personal_chat
from bot.sheets import User
from tests.conftest import FakeRepo

KYIV = ZoneInfo("Europe/Kyiv")
GROUP = -100
# a private chat carries the user's own id; ME is user 1, the `user` fixture
ME = 1
LONER = 7

# a Monday; the report must cover 2026-09-07..2026-09-13
MONDAY = date(2026, 9, 14)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent.append((chat_id, text))


class FakeAI:
    """Records the payload and the `personal` flag of every `weekly_report` call."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[dict[str, Any], bool]] = []
        self.notices: list[Any] = []
        self.fail = fail

    async def weekly_report(
        self, payload: dict[str, Any], personal: bool = False, on_retry: Any = None
    ) -> str:
        self.calls.append((payload, personal))
        self.notices.append(on_retry)
        if self.fail:
            raise RuntimeError("gemini is having a bad day")
        return "порада від AI"


def _settings(chat_ids: str) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123:abc",
        allowed_chat_ids=chat_ids,
        google_sheet_id="sheet",
        google_service_account_json='{"type": "service_account"}',
        gemini_api_key="key",
    )


def _jobs(repo: FakeRepo, settings: Settings, ai: FakeAI | None = None) -> tuple[Jobs, FakeBot]:
    bot = FakeBot()
    jobs = Jobs(bot, repo, ai or FakeAI(), settings, AsyncIOScheduler())  # type: ignore[arg-type]
    return jobs, bot


def _food(kcal: float) -> FoodEstimate:
    return FoodEstimate(dish="тест", kcal=kcal, protein_g=20, fat_g=10, carbs_g=50, veg_share=0.3)


def _dt(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=KYIV)


def _in_last_week() -> date:
    """A day inside the window a *real* `datetime.now()` produces, for the clock-driven job."""
    return datetime.now(KYIV).date() - timedelta(days=3)


async def test_window_is_the_seven_full_days_before_today(repo: FakeRepo, user: User) -> None:
    await repo.upsert_user(user)
    await repo.add_food(user, _food(2000), _dt(date(2026, 9, 6)), "text", 1)  # older than the week
    await repo.add_food(user, _food(1500), _dt(date(2026, 9, 7)), "text", 2)  # first day
    await repo.add_food(user, _food(2500), _dt(date(2026, 9, 13)), "text", 3)  # last day
    await repo.add_food(user, _food(9000), _dt(MONDAY), "text", 4)  # today, must be left out

    ai = FakeAI()
    jobs, bot = _jobs(repo, _settings(str(GROUP)), ai)
    text = await jobs.run_weekly_report(GROUP, today=MONDAY)

    payload, _ = ai.calls[0]
    assert payload["week_start"] == "2026-09-07"
    assert payload["week_end"] == "2026-09-13"
    assert payload["users"][0]["kcal_total"] == 4000
    assert "2026-09-07 - 2026-09-13" in text
    assert bot.sent == [(GROUP, text)]


async def test_average_ignores_days_without_food(repo: FakeRepo, user: User) -> None:
    await repo.upsert_user(user)
    for day in (date(2026, 9, 7), date(2026, 9, 9)):  # only 2 of the 7 days were logged
        await repo.add_food(user, _food(1800), _dt(day), "text", 1)

    ai = FakeAI()
    jobs, _ = _jobs(repo, _settings(str(GROUP)), ai)
    await jobs.run_weekly_report(GROUP, today=MONDAY)

    me = ai.calls[0][0]["users"][0]
    assert me["days_with_food_logged"] == 2
    assert me["kcal_avg_per_day"] == 1800  # 3600 / 2, not 3600 / 7


async def test_a_dm_gets_the_personal_wording(repo: FakeRepo) -> None:
    loner = User(user_id=LONER, chat_id=LONER, name="Сам", tz="Europe/Kyiv")
    await repo.upsert_user(loner)
    # `weekly_reports` takes no `today`, so this row has to sit inside the window the real clock
    # produces (the 7 full days before today) - a fixed date would rot into a failure.
    await repo.add_food(loner, _food(1800), _dt(_in_last_week()), "text", 1)

    ai = FakeAI()
    jobs, bot = _jobs(repo, _settings(f"{GROUP},{LONER}"), ai)
    await jobs.weekly_reports()

    assert LONER in {chat_id for chat_id, _ in bot.sent}
    assert len(ai.calls) == 1  # the group is empty, so only the DM reaches Gemini
    assert ai.calls[0][1] is True


async def test_a_group_report_is_not_personal(repo: FakeRepo, user: User) -> None:
    await repo.upsert_user(user)
    await repo.add_food(user, _food(1800), _dt(date(2026, 9, 9)), "text", 1)

    ai = FakeAI()
    jobs, _ = _jobs(repo, _settings(str(GROUP)), ai)
    await jobs.run_weekly_report(GROUP, today=MONDAY)

    assert ai.calls[0][1] is False


async def test_the_background_job_never_sends_a_retry_notice(repo: FakeRepo, user: User) -> None:
    """Nobody waits on the weekly job, and a failure already falls back to the numbers."""
    await repo.upsert_user(user)
    await repo.add_food(user, _food(1800), _dt(date(2026, 9, 9)), "text", 1)

    ai = FakeAI()
    jobs, _ = _jobs(repo, _settings(str(GROUP)), ai)
    await jobs.run_weekly_report(GROUP, today=MONDAY)

    assert ai.notices == [None]


async def test_no_personal_report_for_a_member_of_a_group(repo: FakeRepo, user: User) -> None:
    """User 1 tracks in the group and also has the bot in their DM: the group report is enough."""
    await repo.upsert_user(user)  # chat_id == GROUP
    await repo.upsert_user(User(user_id=LONER, chat_id=LONER, name="Сам", tz="Europe/Kyiv"))
    await repo.add_food(user, _food(1800), _dt(date(2026, 9, 9)), "text", 1)

    jobs, bot = _jobs(repo, _settings(f"{GROUP},{ME},{LONER}"))
    await jobs.weekly_reports()

    sent_to = {chat_id for chat_id, _ in bot.sent}
    assert ME not in sent_to
    assert GROUP in sent_to
    assert LONER in sent_to  # nothing logged, but they still get the "no entries" line


async def test_an_explicit_ask_is_always_answered(repo: FakeRepo, user: User) -> None:
    """`/week` in the DM of a group member still works - the skip is only for the cron job."""
    await repo.upsert_user(user)
    me_dm = User(user_id=ME, chat_id=ME, name="Олексій", tz="Europe/Kyiv")
    await repo.upsert_user(me_dm)
    await repo.add_food(me_dm, _food(1800), _dt(date(2026, 9, 9)), "text", 1)

    ai = FakeAI()
    jobs, bot = _jobs(repo, _settings(f"{GROUP},{ME}"), ai)
    await jobs.run_weekly_report(ME, today=MONDAY)

    assert [chat_id for chat_id, _ in bot.sent] == [ME]
    assert ai.calls[0][1] is True


async def test_a_failing_chat_does_not_stop_the_others(repo: FakeRepo, user: User) -> None:
    await repo.upsert_user(user)
    await repo.upsert_user(User(user_id=2, chat_id=-200, name="Марія", tz="Europe/Kyiv"))

    jobs, bot = _jobs(repo, _settings(f"{GROUP},-200"))
    original = jobs.run_weekly_report
    failed: list[int] = []

    async def flaky(chat_id: int, today: date | None = None) -> str:
        if chat_id == GROUP:
            failed.append(chat_id)
            raise RuntimeError("sheets is down")
        return await original(chat_id, today)

    jobs.run_weekly_report = flaky  # type: ignore[method-assign]
    await jobs.weekly_reports()

    assert failed == [GROUP]
    assert [chat_id for chat_id, _ in bot.sent] == [-200]


async def test_a_broken_roster_read_still_sends_every_report(repo: FakeRepo, user: User) -> None:
    """The group/DM skip is only an optimisation, so a failing roster read must not cost the week.

    A duplicated DM report is noise somebody can ignore; a week with no report at all is data
    nobody gets back. User 1 is in the group and would normally be skipped in their DM.
    """
    await repo.upsert_user(user)  # chat_id == GROUP
    await repo.upsert_user(User(user_id=ME, chat_id=ME, name="Олексій", tz="Europe/Kyiv"))
    original = repo.get_active_users
    reads: list[int] = []

    async def flaky(chat_id: int) -> list[User]:
        reads.append(chat_id)
        if len(reads) == 1:  # the roster read `weekly_reports` opens with
            raise RuntimeError("sheets quota exceeded")
        return await original(chat_id)

    repo.get_active_users = flaky  # type: ignore[method-assign]
    jobs, bot = _jobs(repo, _settings(f"{GROUP},{ME}"))
    await jobs.weekly_reports()

    assert {chat_id for chat_id, _ in bot.sent} == {GROUP, ME}


async def test_numbers_are_sent_when_gemini_fails(repo: FakeRepo, user: User) -> None:
    await repo.upsert_user(user)
    await repo.add_food(user, _food(1800), _dt(date(2026, 9, 9)), "text", 1)

    jobs, bot = _jobs(repo, _settings(str(GROUP)), FakeAI(fail=True))
    text = await jobs.run_weekly_report(GROUP, today=MONDAY)

    assert "1800 ккал за тиждень" in text
    assert bot.sent == [(GROUP, text)]


def test_the_weekly_job_runs_monday_morning(repo: FakeRepo) -> None:
    settings = _settings(str(GROUP))
    scheduler, _ = build_scheduler(FakeBot(), repo, None, settings)  # type: ignore[arg-type]
    job = scheduler.get_job("weekly_report")
    assert job is not None
    assert "day_of_week='mon'" in str(job.trigger)
    assert "hour='9'" in str(job.trigger)


@pytest.mark.parametrize(("chat_id", "personal"), [(-100, False), (1, True), (0, False)])
def test_only_positive_chat_ids_are_personal(chat_id: int, personal: bool) -> None:
    assert is_personal_chat(chat_id) is personal
