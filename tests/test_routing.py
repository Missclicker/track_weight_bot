"""End-to-end routing through a real aiogram Dispatcher with a mocked Telegram session.

Checks that a text message lands in the right handler (weight / correction / sport / ignored)
and that the allowed-chat gate works. Gemini and Sheets are replaced by fakes.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from bot import i18n
from bot.ai import FoodEstimate, SportEntry
from bot.config import Settings
from bot.handlers import build_router
from bot.sheets import User
from tests.conftest import FakeRepo

BOT_ID = 123  # derived from the token "123:abc"
CHAT_ID = -100
ME = 7


class MockSession(BaseSession):
    """Records outgoing API calls and answers `sendMessage` with a synthetic message."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict[str, Any]] = []
        self._next_id = 1000

    async def close(self) -> None:
        return None

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        data = method.model_dump(exclude_none=True)
        if method.__api_method__ == "sendMessage":
            self.sent.append(data)
            self._next_id += 1
            return Message(  # type: ignore[return-value]
                message_id=self._next_id,
                date=datetime.now(),
                chat=Chat(id=data["chat_id"], type="supergroup"),
                from_user=TgUser(id=BOT_ID, is_bot=True, first_name="Bot"),
                text=data["text"],
            )
        raise AssertionError(f"unexpected API call {method.__api_method__}")

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:  # pragma: no cover - not used
        yield b""


class FakeAI:
    def __init__(self) -> None:
        self.sport_calls: list[str] = []
        self.revise_calls: list[tuple[bool, str, str]] = []

    async def revise_food(
        self, image: bytes | None, mime: str | None, previous: dict[str, Any], correction: str
    ) -> FoodEstimate:
        self.revise_calls.append((image is not None, str(previous["dish"]), correction))
        return FoodEstimate(dish="борщ з хлібом", kcal=720, carbs_g=60)

    async def parse_sport(self, text: str, weight_kg: float | None) -> SportEntry | None:
        self.sport_calls.append(text)
        return SportEntry(activity="running", title="біг", minutes=30, distance_km=5, kcal=390)


@pytest.fixture
def harness(settings: Settings, repo: FakeRepo) -> tuple[Dispatcher, Bot, MockSession, FakeAI]:
    session = MockSession()
    bot = Bot("123:abc", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot._me = TgUser(id=BOT_ID, is_bot=True, first_name="Bot", username="bot")  # skip getMe
    ai = FakeAI()
    dp = Dispatcher(repo=repo, ai=ai, settings=settings, jobs=None)
    dp.include_router(build_router())
    return dp, bot, session, ai


def _update(
    text: str,
    chat_id: int = CHAT_ID,
    chat_type: str = "supergroup",
    reply_to: Message | None = None,
    message_id: int = 1,
) -> Update:
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=TgUser(id=ME, is_bot=False, first_name="Олексій", username="ol"),
            text=text,
            reply_to_message=reply_to,
        ),
    )


def _bot_message(text: str, message_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(),
        chat=Chat(id=CHAT_ID, type="supergroup"),
        from_user=TgUser(id=BOT_ID, is_bot=True, first_name="Bot"),
        text=text,
    )


async def test_bare_number_is_weight(harness, repo: FakeRepo, monkeypatch) -> None:
    dp, bot, session, _ = harness
    # Two weigh-ins in one test: `datetime.now()` can return the same value twice on Windows,
    # which would make the second entry look like the first. Use strictly increasing clocks.
    ticks = (datetime(2026, 1, 5, 9, 0, i) for i in range(60))
    monkeypatch.setattr("bot.handlers.weight.user_now", lambda user, settings: next(ticks))
    await dp.feed_update(bot, _update("84,3"))
    assert len(repo.rows["weight"]) == 1
    assert repo.rows["weight"][0]["kg"] == 84.3
    assert repo.rows["weight"][0]["source"] == "text"
    assert session.sent[-1]["text"] == i18n.WEIGHT_FIRST.format(kg="84.3")
    assert len(repo.users) == 1  # auto-registered

    await dp.feed_update(bot, _update("83.8", message_id=2))
    assert "-0.5" in session.sent[-1]["text"]


async def test_reply_to_ping_is_weight_but_other_reply_is_not(harness, repo: FakeRepo) -> None:
    dp, bot, _, _ = harness
    ping = _bot_message(i18n.PING.format(mentions="x"), 500)
    await dp.feed_update(bot, _update("85", reply_to=ping))
    assert repo.rows["weight"][0]["source"] == "ping"

    human = _update("скільки?").message
    await dp.feed_update(bot, _update("85", reply_to=human, message_id=3))
    assert len(repo.rows["weight"]) == 1  # a number replying to a person is ignored


async def test_reply_to_food_estimate_is_correction(harness, repo: FakeRepo, user) -> None:
    dp, bot, _, _ = harness
    from bot.ai import FoodEstimate

    est = FoodEstimate(dish="борщ", kcal=600)
    me = User(user_id=ME, chat_id=CHAT_ID, name="Олексій")
    await repo.add_food(me, est, datetime.now(), "photo", 777)
    food_msg = _bot_message(i18n.FOOD_PREFIX + " 600 ккал - борщ", 777)
    await dp.feed_update(bot, _update("450 ккал", reply_to=food_msg))
    assert repo.rows["food"][0]["kcal"] == 450
    assert repo.rows["food"][0]["corrected"] == "TRUE"
    assert repo.rows["weight"] == []  # not treated as weight even though 450 > WEIGHT_MAX anyway


async def test_correction_of_someone_elses_entry_is_refused(harness, repo: FakeRepo, user) -> None:
    dp, bot, session, _ = harness
    from bot.ai import FoodEstimate

    # `user` (id 1) owns the entry; the sender (ME) replies to it
    await repo.add_food(user, FoodEstimate(dish="борщ", kcal=600), datetime.now(), "photo", 778)
    food_msg = _bot_message(i18n.FOOD_PREFIX + " 600 ккал - борщ", 778)
    await dp.feed_update(bot, _update("100", reply_to=food_msg))
    assert repo.rows["food"][0]["kcal"] == 600
    assert i18n.CORRECTION_NOT_FOUND in session.sent[-1]["text"]


async def test_text_reply_to_food_estimate_revises_via_ai(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    me = User(user_id=ME, chat_id=CHAT_ID, name="Олексій")
    await repo.add_food(me, FoodEstimate(dish="борщ", kcal=600), datetime.now(), "text", 777)
    food_msg = _bot_message(i18n.FOOD_PREFIX + " 600 ккал - борщ", 777)

    # a sport keyword inside the reply must not divert it to the sport router
    await dp.feed_update(bot, _update("плюс два шматки хліба, потім біг", reply_to=food_msg))
    assert ai.revise_calls == [(False, "борщ", "плюс два шматки хліба, потім біг")]
    assert ai.sport_calls == []
    row = repo.rows["food"][0]
    assert (row["dish"], row["kcal"], row["corrected"]) == ("борщ з хлібом", 720, "TRUE")
    reply = session.sent[-1]
    assert reply["text"].startswith(i18n.FOOD_PREFIX) and "720" in reply["text"]
    assert i18n.CORRECTED_MARK in reply["text"]
    assert row["message_id"] == 1001  # re-keyed to the bot's new reply (MockSession ids)

    # ... so the next correction replies to the new estimate
    newer = _bot_message(reply["text"], 1001)
    await dp.feed_update(bot, _update("500", reply_to=newer, message_id=3))
    assert repo.rows["food"][0]["kcal"] == 500


async def test_text_reply_to_someone_elses_estimate_is_refused(harness, repo: FakeRepo, user):
    dp, bot, session, ai = harness
    await repo.add_food(user, FoodEstimate(dish="борщ", kcal=600), datetime.now(), "photo", 778)
    food_msg = _bot_message(i18n.FOOD_PREFIX + " 600 ккал - борщ", 778)
    await dp.feed_update(bot, _update("це було 300 г", reply_to=food_msg))
    assert ai.revise_calls == []
    assert session.sent[-1]["text"] == i18n.CORRECTION_NOT_FOUND


async def test_sport_text_goes_to_ai(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await dp.feed_update(bot, _update("пробіг 5 км за 30 хв"))
    assert ai.sport_calls == ["пробіг 5 км за 30 хв"]
    assert repo.rows["sport"][0]["kcal"] == 390
    assert "390" in session.sent[-1]["text"]


async def test_sport_command(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await dp.feed_update(bot, _update("/sport зал 1 година"))
    assert ai.sport_calls == ["зал 1 година"]
    await dp.feed_update(bot, _update("/sport", message_id=2))
    assert session.sent[-1]["text"] == i18n.SPORT_USAGE


async def test_plain_chat_is_ignored(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await dp.feed_update(bot, _update("привіт усім, як справи?"))
    assert session.sent == []
    assert ai.sport_calls == []
    assert all(rows == [] for rows in repo.rows.values())


async def test_other_chat_is_dropped(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await dp.feed_update(bot, _update("84.3", chat_id=-999))
    assert session.sent == []
    assert repo.rows["weight"] == []


async def test_private_start_explains(harness) -> None:
    dp, bot, session, _ = harness
    await dp.feed_update(bot, _update("/start", chat_id=ME, chat_type="private"))
    assert session.sent[-1]["text"] == i18n.PRIVATE_CHAT_ONLY_GROUP
    await dp.feed_update(bot, _update("84.3", chat_id=ME, chat_type="private", message_id=2))
    assert len(session.sent) == 1


async def test_allow_listed_private_chat_is_served(settings: Settings, repo: FakeRepo) -> None:
    """A DM listed in ALLOWED_CHAT_IDS (owner testing) gets the real /start, not the refusal."""
    settings = settings.model_copy(update={"allowed_chat_ids": {ME}})
    session = MockSession()
    bot = Bot("123:abc", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot._me = TgUser(id=BOT_ID, is_bot=True, first_name="Bot", username="bot")
    dp = Dispatcher(repo=repo, ai=FakeAI(), settings=settings, jobs=None)
    dp.include_router(build_router())

    await dp.feed_update(bot, _update("/start", chat_id=ME, chat_type="private"))
    assert session.sent[-1]["text"] != i18n.PRIVATE_CHAT_ONLY_GROUP
    assert len(repo.users) == 1  # registered by cmd_start
    await dp.feed_update(bot, _update("84.3", chat_id=ME, chat_type="private", message_id=2))
    assert repo.rows["weight"][0]["kg"] == 84.3


async def test_w_and_today_commands(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await dp.feed_update(bot, _update("/w 84.3"))
    assert repo.rows["weight"][0]["source"] == "command"
    await dp.feed_update(bot, _update("/w 500", message_id=2))
    assert "40" in session.sent[-1]["text"] and "200" in session.sent[-1]["text"]
    await dp.feed_update(bot, _update("/today", message_id=3))
    assert "84.3" in session.sent[-1]["text"]


async def test_ukrainian_and_transliterated_command_aliases(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await dp.feed_update(bot, _update("/вага 83.9"))
    await dp.feed_update(bot, _update("/vaga 83.8", message_id=2))
    assert [r["kg"] for r in repo.rows["weight"]] == [83.9, 83.8]
    await dp.feed_update(bot, _update("/сьогодні", message_id=3))
    assert "83.8" in session.sent[-1]["text"]
    await dp.feed_update(bot, _update("/спорт біг 5 км", message_id=4))
    assert ai.sport_calls == ["біг 5 км"]
    await dp.feed_update(bot, _update("/довідка", message_id=5))
    assert session.sent[-1]["text"] == i18n.HELP
    await dp.feed_update(bot, _update("/старт", message_id=6))
    assert "Записав тебе" in session.sent[-1]["text"]
    # a bare Cyrillic "command" the bot does not know is left alone
    await dp.feed_update(bot, _update("/щось", message_id=7))
    assert "Записав тебе" in session.sent[-1]["text"]  # nothing new was sent


async def test_handler_error_is_reported_not_raised(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness

    async def boom(text: str, weight_kg: float | None) -> None:
        raise RuntimeError("gemini down")

    ai.parse_sport = boom  # type: ignore[method-assign]
    await dp.feed_update(bot, _update("біг 30 хв"))
    assert session.sent[-1]["text"] == i18n.ERROR_TRY_AGAIN
