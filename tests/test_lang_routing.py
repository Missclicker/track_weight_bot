"""Per-person language end to end: `/lang`, replies in the sender's language, and recognition of
the bot's own messages in every language.

Drives a real Dispatcher with the harness pieces of `tests/test_routing.py`. Two people share the
group: `EN_ID` switched to English, `UK_ID` never ran `/lang` and so reads Ukrainian.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Chat, Message, PhotoSize, Update
from aiogram.types import User as TgUser

from bot.ai import FoodEstimate
from bot.config import Settings
from bot.handlers import build_router
from bot.i18n import FOOD_PREFIX, en, uk
from bot.sheets import User
from tests.conftest import FakeRepo
from tests.test_routing import BOT_ID, CHAT_ID, FakeAI, FakeJobs, MockSession

EN_ID = 7
UK_ID = 8
STRANGER_ID = 9
OTHER_CHAT_ID = -200  # a second group the English speaker is a member of
_NAMES = {EN_ID: "Alex", UK_ID: "Марія", STRANGER_ID: "Stranger"}

Harness = tuple[Dispatcher, Bot, MockSession, FakeAI]


@pytest.fixture
def harness(settings: Settings, repo: FakeRepo) -> Harness:
    session = MockSession()
    bot = Bot("123:abc", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot._me = TgUser(id=BOT_ID, is_bot=True, first_name="Bot", username="bot")  # skip getMe
    ai = FakeAI()
    dp = Dispatcher(repo=repo, ai=ai, settings=settings, jobs=FakeJobs())
    dp.include_router(build_router())
    return dp, bot, session, ai


_next_id = iter(range(1, 10_000))


def _message(
    text: str | None,
    sender: int,
    chat_id: int = CHAT_ID,
    reply_to: Message | None = None,
    **extra: Any,
) -> Update:
    message_id = next(_next_id)
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(),
            chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"),
            from_user=TgUser(id=sender, is_bot=False, first_name=_NAMES[sender]),
            text=text,
            reply_to_message=reply_to,
            **extra,
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


async def _english(harness: Harness, repo: FakeRepo) -> None:
    """Register `EN_ID` in the group and switch them to English."""
    dp, bot, _, _ = harness
    await dp.feed_update(bot, _message("/lang en", EN_ID))
    assert await repo.get_lang(EN_ID) == "en"


def _last(session: MockSession) -> str:
    return session.sent[-1]["text"]


# --- /lang ---------------------------------------------------------------------------------------


async def test_lang_switches_every_row_of_the_person_and_back(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await repo.upsert_user(User(user_id=EN_ID, chat_id=CHAT_ID, name="Alex"))
    repo.users.append(User(user_id=EN_ID, chat_id=OTHER_CHAT_ID, name="Alex"))

    await dp.feed_update(bot, _message("/lang en", EN_ID))
    assert _last(session) == en.LANG_SET
    assert [u.lang for u in repo.users if u.user_id == EN_ID] == ["en", "en"]

    await dp.feed_update(bot, _message("/мова uk", EN_ID))
    assert _last(session) == uk.LANG_SET
    assert [u.lang for u in repo.users if u.user_id == EN_ID] == ["uk", "uk"]


async def test_lang_registers_a_first_contact(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await dp.feed_update(bot, _message("/language English", EN_ID))
    assert _last(session) == en.LANG_SET
    assert [(u.chat_id, u.lang) for u in repo.users] == [(CHAT_ID, "en")]


async def test_bare_lang_shows_the_current_language_in_it(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await dp.feed_update(bot, _message("/lang", UK_ID))
    assert _last(session) == uk.LANG_CURRENT
    await _english(harness, repo)
    await dp.feed_update(bot, _message("/mova", EN_ID))
    assert _last(session) == en.LANG_CURRENT


async def test_an_unknown_language_gets_the_usage_and_changes_nothing(
    harness, repo: FakeRepo
) -> None:
    dp, bot, session, _ = harness
    await dp.feed_update(bot, _message("/lang klingon", UK_ID))
    assert _last(session) == uk.LANG_USAGE
    assert await repo.get_lang(UK_ID) == "uk"
    await _english(harness, repo)
    await dp.feed_update(bot, _message("/lang klingon", EN_ID))
    assert _last(session) == en.LANG_USAGE
    assert await repo.get_lang(EN_ID) == "en"


async def test_lang_from_a_members_dm_switches_the_group_too(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await repo.upsert_user(User(user_id=EN_ID, chat_id=CHAT_ID, name="Alex"))
    await dp.feed_update(bot, _message("/lang en", EN_ID, chat_id=EN_ID))
    assert _last(session) == en.LANG_SET
    assert session.sent[-1]["chat_id"] == EN_ID
    assert [(u.chat_id, u.lang) for u in repo.users] == [(CHAT_ID, "en")]  # nobody registered

    await dp.feed_update(bot, _message("/help", EN_ID))  # back in the group
    assert _last(session) == en.HELP


async def test_lang_from_a_strangers_dm_is_refused(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await dp.feed_update(bot, _message("/lang en", STRANGER_ID, chat_id=STRANGER_ID))
    assert _last(session) == uk.PRIVATE_CHAT_ONLY_GROUP
    assert repo.users == []


# --- replies in the sender's language ------------------------------------------------------------


async def test_commands_answer_each_person_in_their_language(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    for sender, strings in ((EN_ID, en), (UK_ID, uk)):
        await dp.feed_update(bot, _message("/help", sender))
        assert _last(session) == strings.HELP
        await dp.feed_update(bot, _message("/today", sender))
        assert strings.TODAY_NO_DATA in _last(session)
        await dp.feed_update(bot, _message("/kcal", sender))
        assert strings.KCAL_NO_DATA in _last(session)
        await dp.feed_update(bot, _message("/target", sender))
        assert _last(session) == strings.TARGET_NONE
        await dp.feed_update(bot, _message("/target 2000", sender))
        assert _last(session) == strings.TARGET_SET.format(kcal="2000")
        await dp.feed_update(bot, _message("/profile", sender))
        assert _last(session) == strings.PROFILE_NONE
        await dp.feed_update(bot, _message("/water", sender))
        assert _last(session) == strings.WATER_USAGE
        await dp.feed_update(bot, _message("/w", sender))
        assert _last(session) == strings.WEIGHT_USAGE


async def test_a_weigh_in_is_confirmed_in_the_senders_language(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    await dp.feed_update(bot, _message("84.3", EN_ID))
    assert _last(session) == en.WEIGHT_FIRST.format(kg="84.3")
    await dp.feed_update(bot, _message("71.5", UK_ID))
    assert _last(session) == uk.WEIGHT_FIRST.format(kg="71.5")


async def test_english_water_stop_words(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    for word in ("cancel", "disable", "remove", "delete", "Delete"):
        await dp.feed_update(bot, _message(f"/water {word}", EN_ID))
        assert _last(session) == en.WATER_NOT_SUBSCRIBED


async def test_a_food_photo_from_an_english_user(harness, repo: FakeRepo, monkeypatch) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)

    async def fake_download(file: Any, destination: Any) -> None:
        destination.write(b"jpeg")

    monkeypatch.setattr(bot, "download", fake_download)
    ai.food_estimates = [FoodEstimate(dish="borscht", portion="400 g", kcal=400)]
    photo = [PhotoSize(file_id="f1", file_unique_id="u1", width=800, height=600)]
    await dp.feed_update(bot, _message(None, EN_ID, photo=photo, caption="borscht"))
    assert ai.langs == ["en"]
    reply = _last(session)
    assert reply.startswith(f"{FOOD_PREFIX} 400 kcal - borscht, 400 g")
    assert en.day_total(400, None) in reply
    assert len(repo.rows["food"]) == 1


async def test_food_text_and_its_correction_from_an_english_user(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)
    ai.food_estimates = [FoodEstimate(dish="borscht", kcal=500)]
    await dp.feed_update(bot, _message("/food borscht", EN_ID))
    estimate = _last(session)
    assert estimate.startswith(f"{FOOD_PREFIX} 500 kcal - borscht")
    assert en.day_total(500, None) in estimate

    reply_id = repo.rows["food"][0]["message_id"]
    await dp.feed_update(
        bot, _message("with bread", EN_ID, reply_to=_bot_message(estimate, reply_id))
    )
    assert ai.langs == ["en", "en"]
    assert en.CORRECTED_MARK in _last(session)
    assert en.day_total(720, None) in _last(session)


async def test_a_ukrainian_user_still_gets_ukrainian_food_replies(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)
    ai.food_estimates = [FoodEstimate(dish="борщ", kcal=500)]
    await dp.feed_update(bot, _message("/food борщ", UK_ID))
    assert ai.langs == ["uk"]
    assert uk.day_total(500, None) in _last(session)


async def test_sport_from_an_english_user(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)
    await dp.feed_update(bot, _message("/sport running 5 km 30 min", EN_ID))
    assert ai.langs == ["en"]
    assert _last(session).startswith(f"{en.SPORT_PREFIX} running, 30 min")


# --- the bot's own messages are recognised in every language -------------------------------------


async def test_a_reply_to_a_ping_in_either_language_counts_as_ping(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    uk_ping = _bot_message(uk.PING.format(mentions="x"), 500)
    en_ping = _bot_message(en.PING.format(mentions="x"), 501)
    await dp.feed_update(bot, _message("85", EN_ID, reply_to=uk_ping))
    assert _last(session) == en.WEIGHT_FIRST.format(kg="85")
    await dp.feed_update(bot, _message("70", UK_ID, reply_to=en_ping))
    assert _last(session) == uk.WEIGHT_FIRST.format(kg="70")
    assert [row["source"] for row in repo.rows["weight"]] == ["ping", "ping"]


async def test_delete_under_a_sport_confirmation_in_either_language(
    harness, repo: FakeRepo
) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    # Alex logs in Ukrainian first (an old confirmation), then in English
    await repo.set_lang(EN_ID, "uk")
    await dp.feed_update(bot, _message("/sport біг 30 хв", EN_ID))
    uk_confirmation = _bot_message(_last(session), repo.rows["sport"][0]["message_id"])
    assert uk_confirmation.text is not None and uk_confirmation.text.startswith(uk.SPORT_PREFIX)
    await repo.set_lang(EN_ID, "en")
    await dp.feed_update(bot, _message("/sport running 30 min", EN_ID))
    en_confirmation = _bot_message(_last(session), repo.rows["sport"][1]["message_id"])
    assert en_confirmation.text is not None and en_confirmation.text.startswith(en.SPORT_PREFIX)

    await dp.feed_update(bot, _message("delete", EN_ID, reply_to=uk_confirmation))
    assert _last(session) == en.SPORT_DELETED
    await dp.feed_update(bot, _message("delete", EN_ID, reply_to=en_confirmation))
    assert _last(session) == en.SPORT_DELETED
    assert repo.rows["sport"] == []


async def test_an_english_users_sport_correction_is_in_english(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    hint = en.sport_saved("x", 1, None, 1).splitlines()[-1]
    await _english(harness, repo)
    await dp.feed_update(bot, _message("/sport running 5 km 30 min", EN_ID))
    confirmation = _last(session)
    assert confirmation.endswith(hint)
    sport_msg = _bot_message(confirmation, repo.rows["sport"][0]["message_id"])

    await dp.feed_update(bot, _message("it was 45 min", EN_ID, reply_to=sport_msg))
    assert ai.langs == ["en", "en"]
    corrected = _last(session)
    assert corrected.startswith(f"{en.SPORT_PREFIX} running, 45 min")
    assert en.CORRECTED_MARK in corrected and corrected.endswith(hint)

    ai.sport_revisions = [None]
    newer = _bot_message(corrected, repo.rows["sport"][0]["message_id"])
    await dp.feed_update(bot, _message("it was not sport", EN_ID, reply_to=newer))
    assert _last(session) == en.SPORT_CORRECTION_NOT_UNDERSTOOD


@pytest.mark.parametrize("prompt_strings", [en, uk])
async def test_both_languages_input_prompts_are_recorded(
    harness, repo: FakeRepo, prompt_strings
) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)
    ai.food_estimates = [FoodEstimate(dish="borscht", kcal=500)]
    food_prompt = _bot_message(prompt_strings.FOOD_INPUT_PROMPT, 600)
    await dp.feed_update(bot, _message("borscht", EN_ID, reply_to=food_prompt))
    assert _last(session).startswith(f"{FOOD_PREFIX} 500 kcal")
    sport_prompt = _bot_message(prompt_strings.SPORT_INPUT_PROMPT, 601)
    await dp.feed_update(bot, _message("running 30 min", EN_ID, reply_to=sport_prompt))
    assert _last(session).startswith(en.SPORT_PREFIX)
    assert (len(repo.rows["food"]), len(repo.rows["sport"])) == (1, 1)
    assert ai.langs == ["en", "en"]


@pytest.mark.parametrize("prompt_strings", [en, uk])
async def test_both_languages_input_prompts_are_cancellable(
    harness, repo: FakeRepo, prompt_strings
) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)
    for i, prompt in enumerate(
        (prompt_strings.FOOD_INPUT_PROMPT, prompt_strings.SPORT_INPUT_PROMPT)
    ):
        await dp.feed_update(bot, _message("cancel", EN_ID, reply_to=_bot_message(prompt, 600 + i)))
        assert _last(session) == en.PROMPT_CANCELLED
        await dp.feed_update(bot, _message("видали", UK_ID, reply_to=_bot_message(prompt, 700 + i)))
        assert _last(session) == uk.PROMPT_CANCELLED
    assert ai.langs == []
    assert (repo.rows["food"], repo.rows["sport"]) == ([], [])


async def test_an_english_users_bare_food_and_sport_prompt_in_english(
    harness, repo: FakeRepo
) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    await dp.feed_update(bot, _message("/food", EN_ID))
    assert _last(session) == en.FOOD_INPUT_PROMPT
    await dp.feed_update(bot, _message("/sport", EN_ID))
    assert _last(session) == en.SPORT_INPUT_PROMPT


# --- the error handler ---------------------------------------------------------------------------


async def test_an_error_is_reported_in_the_senders_language(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("gemini down")

    ai.parse_sport = boom  # type: ignore[method-assign]
    await dp.feed_update(bot, _message("/sport running 30 min", EN_ID))
    assert _last(session) == en.ERROR_TRY_AGAIN
    await dp.feed_update(bot, _message("/sport біг 30 хв", UK_ID))
    assert _last(session) == uk.ERROR_TRY_AGAIN


async def test_quota_and_busy_notices_in_the_senders_language(
    harness, repo: FakeRepo, monkeypatch
) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)
    ai.cooling[ai.text_model] = (7200.0, True)
    await dp.feed_update(bot, _message("/food borscht", EN_ID))
    assert _last(session) == en.AI_QUOTA_DAY
    ai.overloaded[ai.vision_model] = 30.0
    photo = [PhotoSize(file_id="f1", file_unique_id="u1", width=800, height=600)]
    await dp.feed_update(bot, _message(None, EN_ID, photo=photo))
    assert _last(session) == en.AI_BUSY_PHOTO
    await dp.feed_update(bot, _message(None, UK_ID, photo=photo))
    assert _last(session) == uk.AI_BUSY_PHOTO


async def test_a_failing_language_lookup_falls_back_to_the_default(
    harness, repo: FakeRepo, monkeypatch
) -> None:
    """A Sheets outage must not break handlers that need no Sheets: they answer in Ukrainian."""
    dp, bot, session, _ = harness
    await _english(harness, repo)

    async def sheets_down(user_id: int) -> str:
        raise RuntimeError("sheets down")

    monkeypatch.setattr(repo, "get_lang", sheets_down)
    await dp.feed_update(bot, _message("/help", EN_ID))
    assert _last(session) == uk.HELP

    # ... and a handler that does fail still gets its error reply, in the default language
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("gemini down")

    harness[3].parse_sport = boom  # type: ignore[method-assign]
    await dp.feed_update(bot, _message("/sport running 30 min", EN_ID))
    assert _last(session) == uk.ERROR_TRY_AGAIN


async def test_ignored_chatter_costs_no_language_lookup(harness, repo: FakeRepo, monkeypatch):
    """The middleware runs only once a handler matched: plain talk never reads the users tab."""
    dp, bot, session, _ = harness
    lookups: list[int] = []
    real_get_lang = repo.get_lang

    async def counting(user_id: int) -> str:
        lookups.append(user_id)
        return await real_get_lang(user_id)

    monkeypatch.setattr(repo, "get_lang", counting)
    await dp.feed_update(bot, _message("привіт усім", UK_ID))
    await dp.feed_update(bot, _message("/help", UK_ID, chat_id=-999))  # not an allowed chat
    assert lookups == []
    assert session.sent == []
    await dp.feed_update(bot, _message("/help", UK_ID))
    assert lookups == [UK_ID]


# --- the remaining reply paths, one English assertion each ---------------------------------------


async def test_numeric_correction_and_delete_in_english(harness, repo: FakeRepo) -> None:
    dp, bot, session, ai = harness
    await _english(harness, repo)
    ai.food_estimates = [FoodEstimate(dish="borscht", kcal=500)]
    await dp.feed_update(bot, _message("/food borscht", EN_ID))
    estimate = _last(session)
    reply_id = repo.rows["food"][0]["message_id"]

    await dp.feed_update(bot, _message("650", EN_ID, reply_to=_bot_message(estimate, reply_id)))
    assert _last(session).startswith(en.CORRECTION_SAVED.format(kcal="650"))

    await dp.feed_update(bot, _message("delete", EN_ID, reply_to=_bot_message(estimate, reply_id)))
    assert _last(session).startswith(en.FOOD_DELETED)
    assert repo.rows["food"] == []

    await dp.feed_update(bot, _message("delete", EN_ID, reply_to=_bot_message(estimate, reply_id)))
    assert _last(session) == en.CORRECTION_NOT_FOUND


async def test_start_in_english(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    await dp.feed_update(bot, _message("/start", EN_ID))
    assert _last(session).startswith("Registered you, Alex.")
    assert _last(session).endswith(en.HELP)
    # a member's /start in a DM outside ALLOWED_CHAT_IDS: the bot can now write there
    await dp.feed_update(bot, _message("/start", EN_ID, chat_id=EN_ID))
    assert _last(session) == en.WATER_DM_READY


async def test_water_subscribe_in_english(harness, repo: FakeRepo) -> None:
    dp, bot, session, _ = harness
    await _english(harness, repo)
    await dp.feed_update(bot, _message("/water weekdays from 9 to 18 every 30 min", EN_ID))
    dm, confirmation = session.sent[-2], session.sent[-1]
    summary = "weekdays, 09:00-18:00, every 30 min"
    assert dm["chat_id"] == EN_ID
    assert dm["text"] == en.WATER_SUBSCRIBED_DM.format(schedule=summary)
    assert confirmation["text"].startswith(f"Water reminders: {summary}.")
