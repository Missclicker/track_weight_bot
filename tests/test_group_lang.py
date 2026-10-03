"""The language of what the bot sends on its own to a whole chat: `Jobs.chat_lang` and the
morning weigh-in ping.

A group reads one text together, so it gets the language most of its active members chose (an
unset one counts as Ukrainian, a tie goes to English); a DM gets its owner's. The weekly report
and the water reminder have their own language tests next to their other ones.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import i18n
from bot.config import Settings
from bot.i18n import Lang
from bot.scheduler import Jobs
from bot.sheets import User
from tests.conftest import FakeRepo

GROUP = -100  # the `settings` fixture allows exactly this chat
KYIV = "Europe/Kyiv"
LISBON = "Europe/Lisbon"


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture
def jobs(repo: FakeRepo, settings: Settings) -> tuple[Jobs, FakeBot]:
    bot = FakeBot()
    return Jobs(bot, repo, None, settings, AsyncIOScheduler()), bot  # type: ignore[arg-type]


async def _member(
    repo: FakeRepo, uid: int, lang: Lang | None, tz: str = KYIV, *, active: bool = True
) -> User:
    user = User(user_id=uid, chat_id=GROUP, name=f"u{uid}", tz=tz, active=active, lang=lang)
    await repo.upsert_user(user)
    return user


def _ping(lang: str, *users: User) -> str:
    mentions = ", ".join(i18n.mention(u.user_id, u.name) for u in users)
    return i18n.t(lang).PING.format(mentions=mentions)


# -- chat_lang ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("langs", "expected"),
    [
        pytest.param(("en", "en", "uk"), "en", id="most-english"),
        pytest.param(("en", "uk"), "en", id="tie-goes-to-english"),
        pytest.param(("en", None, None), "uk", id="unset-counts-as-ukrainian"),
        pytest.param(("uk", "uk", "en"), "uk", id="most-ukrainian"),
        pytest.param((), "uk", id="nobody-active-keeps-the-default"),
    ],
)
async def test_a_group_speaks_the_language_most_members_use(
    jobs, repo: FakeRepo, langs: tuple[Lang | None, ...], expected: str
) -> None:
    job, _ = jobs
    for uid, lang in enumerate(langs, start=1):
        await _member(repo, uid, lang)
    assert await job.chat_lang(GROUP) == expected


async def test_members_who_left_do_not_vote(jobs, repo: FakeRepo) -> None:
    job, _ = jobs
    await _member(repo, 1, "en")
    await _member(repo, 2, "uk", active=False)
    await _member(repo, 3, "uk", active=False)
    assert await job.chat_lang(GROUP) == "en"


@pytest.mark.parametrize(("lang", "expected"), [("en", "en"), ("uk", "uk"), (None, "uk")])
async def test_a_dm_speaks_its_owners_language(
    jobs, repo: FakeRepo, lang: Lang | None, expected: str
) -> None:
    job, _ = jobs
    # an English group next to it does not count: a DM follows its owner alone
    await _member(repo, 2, "en")
    await _member(repo, 3, "en")
    await repo.upsert_user(User(user_id=7, chat_id=7, name="Сам", lang=lang))
    assert await job.chat_lang(7) == expected


async def test_a_roster_in_hand_is_not_read_again(jobs, repo: FakeRepo) -> None:
    job, _ = jobs

    async def no_reads(chat_id: int) -> list[User]:
        raise AssertionError("the roster was passed in")

    repo.get_active_users = no_reads  # type: ignore[method-assign]
    users = [User(user_id=1, chat_id=GROUP, name="a", lang="en")]
    assert await job.chat_lang(GROUP, users) == "en"


# -- morning ping ------------------------------------------------------------------------------


async def test_the_ping_speaks_the_whole_chats_language(jobs, repo: FakeRepo) -> None:
    """Everybody reads the ping, so the majority of the chat decides - not the one it mentions,
    and not just the people in the zone being pinged."""
    job, bot = jobs
    ukrainian = await _member(repo, 1, "uk", KYIV)
    await _member(repo, 2, "en", LISBON)
    await _member(repo, 3, "en", LISBON)

    await job.send_pings(KYIV)

    assert bot.sent == [(GROUP, _ping("en", ukrainian))]


async def test_people_who_weighed_in_still_vote(jobs, repo: FakeRepo) -> None:
    job, bot = jobs
    missing = await _member(repo, 1, "uk")
    for uid in (2, 3):
        weighed = await _member(repo, uid, "en")
        await repo.add_weight(weighed, 80.0, datetime.now(ZoneInfo(KYIV)), "text")

    await job.send_pings(KYIV)

    assert bot.sent == [(GROUP, _ping("en", missing))]


async def test_members_who_left_do_not_vote_on_the_ping(jobs, repo: FakeRepo) -> None:
    job, bot = jobs
    stays = await _member(repo, 1, "uk")
    await _member(repo, 2, "en", active=False)
    await _member(repo, 3, "en", active=False)

    await job.send_pings(KYIV)

    assert bot.sent == [(GROUP, _ping("uk", stays))]


async def test_a_chat_that_never_chose_keeps_the_ukrainian_ping(jobs, repo: FakeRepo) -> None:
    job, bot = jobs
    first = await _member(repo, 1, None)
    second = await _member(repo, 2, None)

    await job.send_pings(KYIV)

    assert bot.sent == [(GROUP, _ping("uk", first, second))]
    assert bot.sent[0][1].startswith(i18n.uk.PING_PREFIX)


def _dm_and_group_jobs(repo: FakeRepo) -> tuple[Jobs, FakeBot]:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="123:abc",
        allowed_chat_ids=f"7,{GROUP}",
        google_sheet_id="sheet",
        google_service_account_json='{"type": "service_account"}',
        gemini_api_key="key",
    )
    bot = FakeBot()
    return Jobs(bot, repo, None, settings, AsyncIOScheduler()), bot  # type: ignore[arg-type]


async def test_a_dm_ping_speaks_its_owners_language(repo: FakeRepo) -> None:
    owner = User(user_id=7, chat_id=7, name="Сам", tz=KYIV, lang="en")
    await repo.upsert_user(owner)
    job, bot = _dm_and_group_jobs(repo)

    await job.send_pings(KYIV)

    assert bot.sent == [(7, _ping("en", owner))]


async def test_a_failing_language_lookup_still_pings_everybody(
    repo: FakeRepo, caplog: pytest.LogCaptureFixture
) -> None:
    owner = User(user_id=7, chat_id=7, name="Сам", tz=KYIV, lang="en")
    await repo.upsert_user(owner)
    member = await _member(repo, 1, "en")

    async def broken(user_id: int) -> str:
        raise RuntimeError("sheets is down")

    repo.get_lang = broken  # type: ignore[method-assign]
    caplog.set_level(logging.WARNING, logger="bot.scheduler")
    job, bot = _dm_and_group_jobs(repo)

    await job.send_pings(KYIV)

    # the DM falls back to the default; the group needed no lookup and keeps its majority
    assert sorted(bot.sent) == sorted([(7, _ping("uk", owner)), (GROUP, _ping("en", member))])
    assert [r.levelname for r in caplog.records if r.exc_info] == ["WARNING"]
