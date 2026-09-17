from datetime import datetime, time

import pytest

from bot.parsing import (
    DELETE_PHRASES,
    DELETE_WORDS,
    FOOD_CANCEL_PHRASES,
    WaterSchedule,
    is_delete_request,
    is_food_cancel_request,
    is_target_clear_request,
    is_water_due,
    parse_correction,
    parse_kcal_target,
    parse_water_schedule,
    parse_weight,
    ts_time,
)

LO, HI = 40, 200
WEEKDAYS = frozenset({0, 1, 2, 3, 4})
WEEKEND = frozenset({5, 6})
ALL_DAYS = frozenset(range(7))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("84.3", 84.3),
        ("84,3", 84.3),
        ("84", 84.0),
        (" 84.3 ", 84.3),
        ("84.3 кг", 84.3),
        ("84.3кг", 84.3),
        ("84 kg", 84.0),
        ("84.3 кг.", 84.3),
        ("вага 84.3", 84.3),
        ("Вага: 84,3", 84.3),
        ("weight 84.3", 84.3),
        ("в 84.3", 84.3),
        ("40", 40.0),
        ("200", 200.0),
        ("120.55", 120.55),
    ],
)
def test_parse_weight_accepts(text: str, expected: float) -> None:
    assert parse_weight(text, LO, HI) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "39.9",  # below range
        "200.1",  # above range
        "250",  # integer above range
        "1000",
        "5",
        "11:00",  # time
        "84:30",
        "12.09.2026",  # dates
        "12/09/26",
        "2026-09-12",
        "+380501234567",  # phones
        "0501234567",
        "84.3 і ще щось",
        "я важу 84.3 сьогодні",
        "84.3.5",
        "84.345",  # too many decimals -> likely not a weight
        "/w 84.3",  # commands are handled elsewhere
        "-84.3",
        "84.3%",
        "84 хв",
        "1e2",
    ],
)
def test_parse_weight_rejects(text: str | None) -> None:
    assert parse_weight(text, LO, HI) is None


def test_parse_weight_respects_custom_range() -> None:
    assert parse_weight("35", 30, 60) == 35.0
    assert parse_weight("84", 30, 60) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("84.3", 84.3),  # a decimal part ...
        ("84,3", 84.3),
        ("84 кг", 84.0),  # ... a unit ...
        ("84кг", 84.0),
        ("84 kg", 84.0),
        ("вага 84", 84.0),  # ... or a label
        ("вага: 84,3кг", 84.3),
        ("weight 84", 84.0),
        ("в 84", 84.0),
        ("84", None),  # a bare integer is the ambiguous case the flag exists for
        ("150", None),
        ("40", None),
        ("  200  ", None),
    ],
)
def test_parse_weight_with_require_marker(text: str, expected: float | None) -> None:
    """Replying to a food estimate, only a number that says "this is kilograms" is a weigh-in:
    the 40..200 range overlaps perfectly plausible kcal corrections of a portion."""
    assert parse_weight(text, LO, HI, require_marker=True) == expected
    assert parse_weight(text, LO, HI) is not None  # ... all of them are weights without the flag


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("650", 650.0),
        (" 650 ", 650.0),
        ("650 ккал", 650.0),
        ("650ккал", 650.0),
        ("650 kcal", 650.0),
        ("650 кал", 650.0),
        ("≈650", 650.0),
        ("≈ 650 ккал", 650.0),
        ("0", 0.0),
        ("1 200", 1200.0),
        ("1200.5", 1200.0),  # a decimal is tolerated but truncated to the integer part
    ],
)
def test_parse_correction_accepts(text: str, expected: float) -> None:
    assert parse_correction(text) == expected


@pytest.mark.parametrize(
    "text",
    [None, "", "abc", "650 грам", "12:30", "-100", "99999", "650 ккал будь ласка", "1e3"],
)
def test_parse_correction_rejects(text: str | None) -> None:
    assert parse_correction(text) is None


@pytest.mark.parametrize(
    ("text", "days", "start", "end", "every"),
    [
        ("будні дні з 9 до 18 кожні 30 хвилин", WEEKDAYS, time(9), time(18), 30),
        ("weekdays from 9 to 18 every 30 min", WEEKDAYS, time(9), time(18), 30),
        ("по буднях 9-18 кожні 45 хв", WEEKDAYS, time(9), time(18), 45),
        ("робочі дні з 09:00 до 18:00 кожні 90 мін", WEEKDAYS, time(9), time(18), 90),
        ("щодня з 8:30 до 22:00 кожну годину", ALL_DAYS, time(8, 30), time(22), 60),
        ("кожного дня кожні 30 хвилин", ALL_DAYS, time(9), time(21), 30),
        ("daily every 15 minutes", ALL_DAYS, time(9), time(21), 15),
        ("вихідні кожні 2 години", WEEKEND, time(9), time(21), 120),
        ("weekends 10:00-20:00 every 2 hours", WEEKEND, time(10), time(20), 120),
        ("пн, ср, пт з 10 до 19 кожні 2 год", frozenset({0, 2, 4}), time(10), time(19), 120),
        ("mon,wed,fri 10-19 every 2 hours", frozenset({0, 2, 4}), time(10), time(19), 120),
        ("нд кожні 3 години", frozenset({6}), time(9), time(21), 180),
        # ranges and full day names
        ("пн-пт з 9 до 18 кожні 30 хв", WEEKDAYS, time(9), time(18), 30),
        ("mon - fri from 9 to 18 every 30 min", WEEKDAYS, time(9), time(18), 30),
        ("пт-пн кожні 2 години", frozenset({4, 5, 6, 0}), time(9), time(21), 120),
        ("вівторок, четвер з 9 до 17 кожну годину", frozenset({1, 3}), time(9), time(17), 60),
        ("у п'ятницю та суботу кожні 2 години", frozenset({4, 5}), time(9), time(21), 120),
        ("субота неділя кожні 2 години", WEEKEND, time(9), time(21), 120),
        ("tuesday and thursday every 45 minutes", frozenset({1, 3}), time(9), time(21), 45),
        # bare units
        ("every 30m", ALL_DAYS, time(9), time(21), 30),
        ("every 2h", ALL_DAYS, time(9), time(21), 120),
        # the interval alone is enough: days and window fall back to the defaults
        ("кожні 30 хв", ALL_DAYS, time(9), time(21), 30),
        ("раз на годину", ALL_DAYS, time(9), time(21), 60),
        ("every hour", ALL_DAYS, time(9), time(21), 60),
        # order does not matter, and the limits are inclusive
        ("кожні 30 хв будні", WEEKDAYS, time(9), time(21), 30),
        ("кожні 12 годин щодня", ALL_DAYS, time(9), time(21), 720),
        ("9:15-18:45 кожні 15 хвилин", ALL_DAYS, time(9, 15), time(18, 45), 15),
    ],
)
def test_parse_water_schedule_accepts(
    text: str, days: frozenset[int], start: time, end: time, every: int
) -> None:
    assert parse_water_schedule(text) == WaterSchedule(
        days=days, start=start, end=end, every_min=every
    )


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "будні з 9 до 18",  # no interval
        "щодня",
        "кожні 5 хвилин",  # below the 15-minute floor
        "кожні 13 годин",  # above the 12-hour ceiling
        "з 18 до 9 кожні 30 хв",  # inverted window
        "з 9 до 9 кожні 30 хв",  # empty window
        "кожні 30 хв з 25 до 26",  # impossible hours
        "абракадабра",
    ],
)
def test_parse_water_schedule_rejects(text: str | None) -> None:
    assert parse_water_schedule(text) is None


WORKDAY = parse_water_schedule("будні з 9 до 18 кожні 30 хвилин")


@pytest.mark.parametrize(
    ("schedule", "now", "expected"),
    [
        (WORKDAY, datetime(2026, 1, 5, 9, 0), True),  # Monday, first slot
        (WORKDAY, datetime(2026, 1, 5, 9, 30), True),
        (WORKDAY, datetime(2026, 1, 5, 18, 0), True),  # `end` is inclusive
        (WORKDAY, datetime(2026, 1, 10, 9, 0), False),  # Saturday
        (WORKDAY, datetime(2026, 1, 5, 8, 30), False),  # before the window
        (WORKDAY, datetime(2026, 1, 5, 18, 30), False),  # after the window
        (WORKDAY, datetime(2026, 1, 5, 9, 20), False),  # not on the grid
        (parse_water_schedule("кожні 45 хв"), datetime(2026, 1, 5, 9, 0), True),
        (parse_water_schedule("кожні 45 хв"), datetime(2026, 1, 5, 9, 45), True),
        (parse_water_schedule("кожні 45 хв"), datetime(2026, 1, 5, 10, 30), True),
        (parse_water_schedule("кожні 45 хв"), datetime(2026, 1, 5, 10, 0), False),
        (parse_water_schedule("вихідні кожні 2 години"), datetime(2026, 1, 10, 11, 0), True),
        (parse_water_schedule("вихідні кожні 2 години"), datetime(2026, 1, 10, 12, 0), False),
        (parse_water_schedule("вихідні кожні 2 години"), datetime(2026, 1, 9, 11, 0), False),
    ],
)
def test_is_water_due(schedule: WaterSchedule, now: datetime, expected: bool) -> None:
    assert is_water_due(schedule, now) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2000", 2000), ("1 800 ккал", 1800), ("2500 kcal", 2500), ("500", 500), ("10000", 10000)],
)
def test_parse_kcal_target_accepts(text: str, expected: float) -> None:
    assert parse_kcal_target(text) == expected


@pytest.mark.parametrize("text", [None, "", "0", "499", "10001", "2000 грам", "abc", "84.3"])
def test_parse_kcal_target_rejects(text: str | None) -> None:
    assert parse_kcal_target(text) is None


@pytest.mark.parametrize("text", ["стоп", "Стоп.", "0", "скинути", "off", "немає"])
def test_target_clear_words(text: str) -> None:
    assert is_target_clear_request(text)


@pytest.mark.parametrize("text", [None, "", "2000", "стоп пити", "стопкран"])
def test_target_clear_words_reject(text: str | None) -> None:
    assert not is_target_clear_request(text)


@pytest.mark.parametrize("word", sorted(DELETE_WORDS | DELETE_PHRASES))
def test_delete_words_all_match(word: str) -> None:
    assert is_delete_request(word)


@pytest.mark.parametrize("text", ["Видали", " ВИДАЛИ! ", "Скасуй.", "Delete", "Це не моє"])
def test_delete_request_ignores_case_space_and_punctuation(text: str) -> None:
    assert is_delete_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "видали цей запис",  # what a person actually types
        "ВИДАЛИ ЦЕЙ ЗАПИС!",
        "прибери цей запис",
        "скасуй будь ласка",
        "видали це",
        "delete this please",
    ],
)
def test_delete_request_allows_filler_around_the_verb(text: str) -> None:
    """A delete verb plus words that add nothing is still a delete."""
    assert is_delete_request(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "це 300 г",  # ordinary corrections must fall through to Gemini ...
        "без хліба",
        "видали хліб",  # ... and so must a delete verb carrying a noun: this one drops an
        "прибери хліб",  # ingredient from the estimate, it does not drop the record
        "видали 300",  # a number is not filler either
        "не видали",
        "650",
        "скасування",
    ],
)
def test_delete_request_rejects(text: str | None) -> None:
    assert not is_delete_request(text)


@pytest.mark.parametrize("phrase", sorted(FOOD_CANCEL_PHRASES))
def test_every_regret_phrase_cancels_food(phrase: str) -> None:
    assert is_food_cancel_request(phrase)


@pytest.mark.parametrize(
    "text",
    [
        "не записуй",
        "Не записуй це.",
        "не записуйте",
        "не записувати",
        "не треба записувати",
        "НЕ ТРЕБА ЦЕ ЗАПИСУВАТИ!",
        "не рахуй",
        "не рахуй це",
        "не враховуй",
        "не враховуй це",
        "жарт",
        "це жарт",
        "Це був жарт",
        "жартую",
        "я жартую",
        "жартував",
        "жартувала",
        "випадково",
        "я випадково",
        "це випадково",
        "випадково відправив",
        "випадково відправила",
        "випадково надіслав",
        "випадково надіслала",
        "я випадково відправив",
        "помилка",
        "це помилка",
        "помилково",
        "я помилково",
        "не записуй будь ласка",  # politeness is tolerated on any of them ...
        "це жарт, плз",
        "помилково pls",
        "видали",  # ... and everything the narrower predicate already took still counts
        "видали цей запис",
        "скасуй будь ласка",
        "це не моє",
    ],
)
def test_food_cancel_request_accepts(text: str) -> None:
    assert is_food_cancel_request(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "не видали",  # a refusal, not a deletion
        "не записуй хліб",  # a content noun changes the ask into a correction of the dish ...
        "не рахуй хліб",
        "це жарт про борщ",
        "випадково додав хліб",
        "помилка в грамах",
        "жартівливий салат",  # ... and a near-miss word form is not the phrase at all
        "помилкова порція",
        "записуй",
        "це 300 г",
        "без хліба",
        "видали хліб",
        "прибери хліб",
        "видали 300",
        "650",
        "скасування",
    ],
)
def test_food_cancel_request_rejects(text: str | None) -> None:
    assert not is_food_cancel_request(text)


def test_food_cancel_request_does_not_widen_the_delete_vocabulary() -> None:
    """The regret phrases delete food rows only: sport deletes and the `/їжа` prompt keep asking
    `is_delete_request`, so it must stay blind to them."""
    assert not is_delete_request("це жарт")
    assert not is_delete_request("я випадково")
    assert not is_delete_request("не записуй")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-17T07:54:00+03:00", "07:54"),  # what add_food writes; the offset is not applied
        ("2026-09-17T19:05:31+03:00", "19:05"),
        ("2026-09-17 08:30", "08:30"),  # hand-typed, naive: the wall clock is still the time
        ("  2026-09-17T07:54:00+03:00  ", "07:54"),
        ("2026-09-17", None),  # date only - not midnight
        ("", None),
        ("   ", None),
        ("пізніше", None),
        (None, None),
        (0, None),
    ],
)
def test_ts_time(value: object, expected: str | None) -> None:
    assert ts_time(value) == expected
