import random
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from bot import i18n
from bot.ai import FoodEstimate
from bot.reports import (
    build_weekly_payload,
    day_food,
    previous_advice,
    split_message,
    today_summary,
)
from bot.sheets import User
from tests.conftest import FakeRepo

KYIV = ZoneInfo("Europe/Kyiv")


def _dt(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=KYIV)


def _food(kcal: float, alcohol: float = 0, veg: float = 0.3) -> FoodEstimate:
    return FoodEstimate(
        dish="тест",
        kcal=kcal,
        alcohol_kcal=alcohol,
        protein_g=20,
        fat_g=10,
        carbs_g=50,
        veg_share=veg,
        confidence=0.6,
    )


async def test_today_summary_empty(repo: FakeRepo, user: User) -> None:
    s = await today_summary(repo, user, date(2026, 9, 8))
    assert s.kcal_in == 0
    assert s.weight is None
    assert s.food_entries == 0
    assert s.net_kcal == 0


async def test_today_summary_sums_only_that_day(repo: FakeRepo, user: User) -> None:
    today = date(2026, 9, 8)
    yesterday = date(2026, 9, 7)
    await repo.add_food(user, _food(600, alcohol=150), _dt(today, 9), "photo", 1)
    await repo.add_food(user, _food(400), _dt(today, 13), "text", 2)
    await repo.add_food(user, _food(9000), _dt(yesterday), "photo", 3)
    await repo.add_sport(user, "біг", 30, 5, 390, _dt(today, 7), "text")
    await repo.add_weight(user, 84.5, _dt(today, 8), "text")
    await repo.add_weight(user, 84.9, _dt(yesterday, 8), "text")

    s = await today_summary(repo, user, today)
    assert s.kcal_in == 1000
    assert s.alcohol_kcal == 150
    assert s.sport_kcal == 390
    assert s.sport_minutes == 30
    assert s.net_kcal == 610
    assert s.weight == 84.5
    assert s.food_entries == 2


async def test_today_summary_ignores_other_users(repo: FakeRepo, user: User) -> None:
    other = User(user_id=2, chat_id=-100, name="Інший")
    today = date(2026, 9, 8)
    await repo.add_food(other, _food(700), _dt(today), "photo", 5)
    s = await today_summary(repo, user, today)
    assert s.food_entries == 0


async def test_weekly_payload(repo: FakeRepo, user: User) -> None:
    other = User(user_id=2, chat_id=-100, name="Марія")
    silent = User(user_id=3, chat_id=-100, name="Мовчун")
    stranger = User(user_id=4, chat_id=-200, name="Чужий")
    for u in (user, other, silent, stranger):
        await repo.upsert_user(u)

    week_start = date(2026, 9, 1)
    await repo.add_weight(user, 85.0, _dt(date(2026, 9, 1)), "text")
    await repo.add_weight(user, 84.2, _dt(date(2026, 9, 7)), "text")
    await repo.add_weight(user, 90.0, _dt(date(2026, 9, 8)), "text")  # outside the week
    await repo.add_food(user, _food(800, alcohol=200, veg=0.2), _dt(date(2026, 9, 1)), "photo", 1)
    await repo.add_food(user, _food(1200, veg=0.4), _dt(date(2026, 9, 1), 19), "photo", 2)
    await repo.add_food(user, _food(1000), _dt(date(2026, 9, 3)), "photo", 3)
    await repo.add_sport(user, "біг", 30, 5, 400, _dt(date(2026, 9, 2)), "text")
    await repo.add_sport(user, "зал", 60, None, 350, _dt(date(2026, 9, 4)), "text")
    await repo.add_food(other, _food(500), _dt(date(2026, 9, 5)), "text", 4)
    await repo.add_food(stranger, _food(500), _dt(date(2026, 9, 5)), "text", 6)

    payload = await build_weekly_payload(repo, -100, week_start)

    assert payload["week_start"] == "2026-09-01"
    assert payload["week_end"] == "2026-09-07"
    names = [u["name"] for u in payload["users"]]
    assert names == ["Олексій", "Марія"]  # silent user and other-chat user are excluded

    me = payload["users"][0]
    assert me["kcal_total"] == 3000
    assert me["days_with_food_logged"] == 2
    assert me["kcal_avg_per_day"] == 1500
    assert me["alcohol_kcal"] == 200
    assert me["sport_sessions"] == 2
    assert me["sport_minutes"] == 90
    assert me["sport_kcal"] == 750
    assert me["net_kcal"] == 2250
    assert me["weight_first"] == 85.0
    assert me["weight_last"] == 84.2
    assert me["weight_delta"] == pytest.approx(-0.8)
    assert me["weight_entries"] == 2
    assert me["veg_share_avg"] == pytest.approx(0.3, abs=0.01)
    assert me["daily_kcal_target"] == 2000

    maria = payload["users"][1]
    assert maria["weight_first"] is None
    assert maria["weight_delta"] is None
    assert maria["sport_minutes"] == 0


async def test_weekly_payload_no_users(repo: FakeRepo) -> None:
    payload = await build_weekly_payload(repo, -100, date(2026, 9, 1))
    assert payload["users"] == []


def test_weekly_stats_block_formats_delta() -> None:
    block = i18n.weekly_stats_block(
        {
            "name": "Олексій <b>",
            "kcal_total": 3000,
            "kcal_avg_per_day": 1500,
            "days_with_food_logged": 2,
            "alcohol_kcal": 200,
            "sport_minutes": 90,
            "sport_kcal": 750,
            "weight_first": 85.0,
            "weight_last": 84.2,
            "weight_delta": -0.8,
        }
    )
    assert "Олексій <b>" in block  # sent with parse_mode=None, so not HTML-escaped
    assert "-0.8" in block
    assert "85 -> 84.2" in block
    assert "≈1500/день за 2 дн. із записами" in block  # not 3000/7


async def test_day_food_keeps_order_and_totals(repo: FakeRepo, user: User) -> None:
    today = date(2026, 9, 8)
    await repo.add_food(user, _food(400), _dt(today, 13), "text", 2)
    await repo.add_food(user, _food(600), _dt(today, 9), "photo", 1)
    await repo.add_food(user, _food(9000), _dt(date(2026, 9, 7)), "photo", 3)
    food = await day_food(repo, user.user_id, today)
    # the entries carry the wall-clock time they were logged at, in log order
    assert food.items == [("09:00", "тест", 600), ("13:00", "тест", 400)]
    assert food.total_kcal == 1000
    assert (await day_food(repo, user.user_id, date(2026, 9, 9))).total_kcal == 0


async def test_day_food_survives_a_row_without_a_timestamp(repo: FakeRepo, user: User) -> None:
    """A hand-edited row can have an empty `ts`; it still counts, it just has no time."""
    today = date(2026, 9, 8)
    await repo.add_food(user, _food(400), _dt(today, 13), "text", 2)
    repo.rows["food"].append(
        {**repo.rows["food"][0], "ts": "", "dish": "рукою", "kcal": 250, "message_id": 4}
    )
    food = await day_food(repo, user.user_id, today)
    assert food.items == [(None, "рукою", 250), ("13:00", "тест", 400)]
    assert food.total_kcal == 650


# -- previous_advice ---------------------------------------------------------------------------

HEADER = i18n.WEEKLY_HEADER.format(start="2026-08-31", end="2026-09-06")


@pytest.mark.parametrize("text", [None, "", "  \n\t "])
def test_previous_advice_of_nothing_is_none(text: str | None) -> None:
    assert previous_advice(text) is None


def test_previous_advice_drops_the_header_line() -> None:
    text = f"{HEADER}\n\nОлексій\nБілок: 95 г/день\n- 30 г білка на сніданок\n"
    assert previous_advice(text) == "Олексій\nБілок: 95 г/день\n- 30 г білка на сніданок"


def test_previous_advice_keeps_a_text_without_a_header() -> None:
    """Only a *first* line that is our header goes; a hand-written cell is kept whole."""
    assert previous_advice("  - більше овочів\n- менше пива  ") == "- більше овочів\n- менше пива"
    later = f"- більше овочів\n{HEADER}"
    assert previous_advice(later) == later


@pytest.mark.parametrize(
    "text",
    [
        f"{HEADER}\n{i18n.WEEKLY_AI_FAILED}\n\nОлексій: 12600 ккал за тиждень",
        f"{HEADER}\n{i18n.WEEKLY_NO_DATA}",
        HEADER,  # a header and nothing else carries no advice either
    ],
    ids=["numbers-only", "no-data", "header-only"],
)
def test_previous_advice_skips_reports_without_advice(text: str) -> None:
    assert previous_advice(text) is None


def test_previous_advice_is_capped() -> None:
    assert previous_advice(f"{HEADER}\n\n" + "б" * 5000) == "б" * 3000


# -- split_message -----------------------------------------------------------------------------


def _content(text: str) -> str:
    """Everything but whitespace, in order: what a split must neither lose nor repeat."""
    return "".join(text.split())


def test_a_short_text_is_one_message() -> None:
    assert split_message("Тижневий звіт\n\nвсе добре") == ["Тижневий звіт\n\nвсе добре"]
    assert split_message("я" * 4000) == ["я" * 4000]  # the default limit, inclusive
    assert split_message("я" * 4001) == ["я" * 4000, "я"]


def test_a_blank_text_is_no_message() -> None:
    assert split_message("") == []
    assert split_message(" \n\n ") == []


def test_paragraphs_are_kept_whole() -> None:
    paragraphs = ["а" * 30, "б" * 30, "в" * 30]
    text = "\n\n".join(paragraphs)
    chunks = split_message(text, limit=70)
    assert chunks == [f"{paragraphs[0]}\n\n{paragraphs[1]}", paragraphs[2]]
    assert "\n\n".join(chunks) == text


def test_a_paragraph_break_beats_a_later_line_break() -> None:
    text = "а" * 40 + "\n\n" + "б" * 10 + "\n" + "в" * 40
    assert split_message(text, limit=60) == ["а" * 40, "б" * 10 + "\n" + "в" * 40]


def test_a_lone_early_break_does_not_leave_the_header_alone() -> None:
    """A header line above one long paragraph: the cut goes deep into the paragraph instead."""
    lines = ["рядок " + str(i) * 20 for i in range(6)]  # 26 characters each
    text = "Тижневий звіт\n\n" + "\n".join(lines)
    chunks = split_message(text, limit=100)
    assert chunks[0].startswith("Тижневий звіт\n\n" + lines[0])
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert _content("".join(chunks)) == _content(text)


def test_a_paragraph_longer_than_the_limit_is_split_at_lines() -> None:
    lines = [str(i) * 30 for i in range(5)]
    text = "\n".join(lines)
    chunks = split_message(text, limit=70)
    assert chunks == ["\n".join(lines[0:2]), "\n".join(lines[2:4]), lines[4]]
    assert "\n".join(chunks) == text


def test_a_line_longer_than_the_limit_is_cut_hard() -> None:
    text = "ж" * 150
    chunks = split_message(text, limit=70)
    assert chunks == ["ж" * 70, "ж" * 70, "ж" * 10]
    assert "".join(chunks) == text


def test_no_chunk_is_empty_or_whitespace_only() -> None:
    text = "   \n\n" + "\n\n\n\n".join(["а" * 40] * 4) + "\n\n   \n\n  "
    chunks = split_message(text, limit=50)
    assert chunks == ["а" * 40] * 4


@pytest.mark.parametrize("seed", range(20))
def test_splitting_preserves_the_content_in_order(seed: int) -> None:
    """Random words, line breaks, blank lines and overlong lines, against small limits."""
    rng = random.Random(seed)
    pieces: list[str] = []
    for _ in range(rng.randint(20, 200)):
        pieces.append("".join(rng.choice("абвгґдеє") for _ in range(rng.choice([1, 5, 12, 90]))))
        pieces.append(rng.choice([" ", " ", " ", "\n", "\n\n", "\n\n\n", "  \n"]))
    text = "".join(pieces)
    limit = rng.choice([10, 40, 64, 100, 257])
    chunks = split_message(text, limit=limit)
    assert all(len(chunk) <= limit and chunk.strip() for chunk in chunks)
    assert _content("".join(chunks)) == _content(text)
