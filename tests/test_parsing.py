from datetime import datetime, time

import pytest

from bot.parsing import (
    DELETE_PHRASES,
    DELETE_WORDS,
    FOOD_CANCEL_PHRASES,
    ProfileUpdate,
    WaterSchedule,
    is_delete_request,
    is_food_cancel_request,
    is_target_clear_request,
    is_water_due,
    parse_correction,
    parse_kcal_target,
    parse_profile,
    parse_sex,
    parse_water_schedule,
    parse_weight,
    strip_meal_time,
    strip_yesterday,
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
        # the English units
        ("650 cal", 650.0),
        ("650 cals", 650.0),
        ("650 calories", 650.0),
        ("650 Calories.", 650.0),
        ("1 calorie", 1.0),
        ("650 kcals", 650.0),
    ],
)
def test_parse_correction_accepts(text: str, expected: float) -> None:
    assert parse_correction(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "abc",
        "650 грам",
        "12:30",
        "-100",
        "99999",
        "650 ккал будь ласка",
        "1e3",
        "650 calories please",
        "650 grams",
        "650 calz",
    ],
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
        # English: interval forms
        ("every 30 minutes", ALL_DAYS, time(9), time(21), 30),
        ("every 1 hour", ALL_DAYS, time(9), time(21), 60),
        ("each 30 min", ALL_DAYS, time(9), time(21), 30),
        ("once an hour", ALL_DAYS, time(9), time(21), 60),
        ("hourly", ALL_DAYS, time(9), time(21), 60),
        ("Hourly on weekdays", WEEKDAYS, time(9), time(21), 60),
        ("half-hourly", ALL_DAYS, time(9), time(21), 30),
        ("half hourly 9-17", ALL_DAYS, time(9), time(17), 30),
        # English: day groups
        ("weekends every 2 hours", WEEKEND, time(9), time(21), 120),
        ("working days every hour", WEEKDAYS, time(9), time(21), 60),
        ("workdays every hour", WEEKDAYS, time(9), time(21), 60),
        ("work days every hour", WEEKDAYS, time(9), time(21), 60),
        ("business days every hour", WEEKDAYS, time(9), time(21), 60),
        ("every day every hour", ALL_DAYS, time(9), time(21), 60),
        ("every 30 min every day", ALL_DAYS, time(9), time(21), 30),
        ("everyday every hour", ALL_DAYS, time(9), time(21), 60),
        ("each day each 45 minutes", ALL_DAYS, time(9), time(21), 45),
        # English: day names, plurals and short forms
        ("monday, wednesday, friday every hour", frozenset({0, 2, 4}), time(9), time(21), 60),
        ("on mondays and fridays hourly", frozenset({0, 4}), time(9), time(21), 60),
        ("tues, thurs every hour", frozenset({1, 3}), time(9), time(21), 60),
        ("every hour on weds", frozenset({2}), time(9), time(21), 60),
        ("monday to friday every hour", WEEKDAYS, time(9), time(21), 60),
        ("mon thru fri from 9 to 18 hourly", WEEKDAYS, time(9), time(18), 60),
        ("every 2 hours friday until sunday", frozenset({4, 5, 6}), time(9), time(21), 120),
        ("Saturdays and Sundays every 2 hours", WEEKEND, time(9), time(21), 120),
        ("mon-fri from 9 till 18 every 30 min", WEEKDAYS, time(9), time(18), 30),
        # English: windows
        ("between 9 and 18 every 30 min", ALL_DAYS, time(9), time(18), 30),
        ("every 30 min from 9 until 17", ALL_DAYS, time(9), time(17), 30),
        (
            "weekdays between 09:00 and 18:30 each 45 minutes",
            WEEKDAYS,
            time(9),
            time(18, 30),
            45,
        ),
        # the interval is cut out before the window is looked for, in English too
        ("every 2 hours between 10 and 20", ALL_DAYS, time(10), time(20), 120),
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
        "every day",  # a day group is not an interval
        "weekdays between 9 and 18",
        "between 18 and 9 hourly",  # inverted window
        "every 5 minutes",
        "hourlyish",
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


@pytest.mark.parametrize(
    "text",
    [
        "стоп",
        "Стоп.",
        "0",
        "скинути",
        "off",
        "немає",
        # English: "/target stop" and "/profile stop" clear just like "/ціль стоп"
        "stop",
        "Stop!",
        "none",
        "no",
        "remove",
        "delete",
        "unset",
        "reset",
        "clear",
    ],
)
def test_target_clear_words(text: str) -> None:
    assert is_target_clear_request(text)


@pytest.mark.parametrize(
    "text", [None, "", "2000", "стоп пити", "стопкран", "stop it now", "no target", "nope"]
)
def test_target_clear_words_reject(text: str | None) -> None:
    assert not is_target_clear_request(text)


@pytest.mark.parametrize(
    "value",
    ["m", "M", "male", "Man", "ч", "Ч.", "чол", "чол.", "чоловік", "Чоловіча", " ч ", "ч!"],
)
def test_parse_sex_male(value: str) -> None:
    assert parse_sex(value) == "m"


@pytest.mark.parametrize(
    "value", ["f", "F", "female", "Woman", "ж", "Ж.", "жін", "жін.", "жінка", "Жіноча", " ж "]
)
def test_parse_sex_female(value: str) -> None:
    assert parse_sex(value) == "f"


@pytest.mark.parametrize(
    "value", [None, "", "   ", ".", "x", "чоловіки", "жінки", "мж", "m f", "1", 1, 0.5]
)
def test_parse_sex_unknown(value: object) -> None:
    assert parse_sex(value) is None


YEAR = 2026  # the "current year" every profile case below is parsed against


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1981 ч 180", ProfileUpdate(birth_year=1981, sex="m", height_cm=180)),
        ("180 ч 1981", ProfileUpdate(birth_year=1981, sex="m", height_cm=180)),  # any order
        ("ж 165 1990", ProfileUpdate(birth_year=1990, sex="f", height_cm=165)),
        ("Чоловік", ProfileUpdate(sex="m")),
        ("female", ProfileUpdate(sex="f")),
        ("1981", ProfileUpdate(birth_year=1981)),
        ("1926", ProfileUpdate(birth_year=1926)),  # 100 years old: the oldest year taken
        ("2012", ProfileUpdate(birth_year=2012)),  # 14: the youngest
        # a bare age is what people type; it becomes a year (±1, the reply shows which)
        ("45", ProfileUpdate(birth_year=1981)),
        ("14", ProfileUpdate(birth_year=2012)),
        ("100", ProfileUpdate(birth_year=1926)),
        ("45 жінка 165", ProfileUpdate(birth_year=1981, sex="f", height_cm=165)),
        ("120", ProfileUpdate(height_cm=120)),
        ("230", ProfileUpdate(height_cm=230)),
        ("180см", ProfileUpdate(height_cm=180)),
        ("180 см", ProfileUpdate(height_cm=180)),
        ("180cm", ProfileUpdate(height_cm=180)),
        ("180,5", ProfileUpdate(height_cm=180.5)),  # a decimal comma is not a separator ...
        ("180.5 см", ProfileUpdate(height_cm=180.5)),
        ("180,5см", ProfileUpdate(height_cm=180.5)),
        ("1981, ч, 180", ProfileUpdate(birth_year=1981, sex="m", height_cm=180)),
        ("1981,180", ProfileUpdate(birth_year=1981, height_cm=180)),  # ... but this comma is
        # a decimal comma takes exactly one digit: two after it are a second value, here an age
        ("180,45", ProfileUpdate(birth_year=1981, height_cm=180)),
        ("180,5,45", ProfileUpdate(birth_year=1981, height_cm=180.5)),
        ("1981;ж;170", ProfileUpdate(birth_year=1981, sex="f", height_cm=170)),
        ("1981 р.", ProfileUpdate(birth_year=1981)),
        ("1981 р.н.", ProfileUpdate(birth_year=1981)),
        ("вік 45 років", ProfileUpdate(birth_year=1981)),
        ("зріст: 180", ProfileUpdate(height_cm=180)),  # a label may end with a colon
        ("зріст:180", ProfileUpdate(height_cm=180)),  # ... or be glued to its value
        ("стать:ж", ProfileUpdate(sex="f")),
        ("1981 р. н.", ProfileUpdate(birth_year=1981)),
        ("вік: 45", ProfileUpdate(birth_year=1981)),
        ("стать: ж", ProfileUpdate(sex="f")),
        (
            "рік народження: 1981, стать: ч, зріст: 180,5 см",
            ProfileUpdate(birth_year=1981, sex="m", height_cm=180.5),
        ),
        (
            "Рік народження 1981, стать чоловіча, зріст 180 см.",
            ProfileUpdate(birth_year=1981, sex="m", height_cm=180),
        ),
        (
            "born 1981 sex male height 180 cm",
            ProfileUpdate(birth_year=1981, sex="m", height_cm=180),
        ),
        ("  45   ж!  ", ProfileUpdate(birth_year=1981, sex="f")),
        # English labels parse to what the Ukrainian ones do
        ("1981 m 180", ProfileUpdate(birth_year=1981, sex="m", height_cm=180)),
        (
            "age 45 female height 165.5 cm",
            ProfileUpdate(birth_year=1981, sex="f", height_cm=165.5),
        ),
        (
            "born 1981, sex m, height 180cm",
            ProfileUpdate(birth_year=1981, sex="m", height_cm=180),
        ),
        ("45 years old f 165", ProfileUpdate(birth_year=1981, sex="f", height_cm=165)),
        (
            "Year of birth: 1981, gender: F, 170 cm tall",
            ProfileUpdate(birth_year=1981, sex="f", height_cm=170),
        ),
        ("birth year 1981", ProfileUpdate(birth_year=1981)),
        ("aged 45", ProfileUpdate(birth_year=1981)),
        ("age: 45", ProfileUpdate(birth_year=1981)),
        ("45 y.o.", ProfileUpdate(birth_year=1981)),  # the trailing dot goes, as in "р.н."
        ("45 yo, male", ProfileUpdate(birth_year=1981, sex="m")),
        ("45 yrs", ProfileUpdate(birth_year=1981)),
        ("45 y", ProfileUpdate(birth_year=1981)),
        ("45 yr old", ProfileUpdate(birth_year=1981)),
        ("height: 180", ProfileUpdate(height_cm=180)),
        ("180 tall", ProfileUpdate(height_cm=180)),
        ("gender:m", ProfileUpdate(sex="m")),
    ],
)
def test_parse_profile_accepts(text: str, expected: ProfileUpdate) -> None:
    assert parse_profile(text, YEAR) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        ",;",
        "рік зріст см",  # only filler words: nothing to store
        "абв",
        "1981 ч 180 кг",  # one unknown token spoils the whole message ...
        "1981 x 180",
        "1981-ч-180",
        "мені 45",
        "45 1981",  # ... and so does a field given twice
        "ч жінка",
        "180 175",
        "180 180см",
        "1925",  # a year outside the age range
        "2013",
        "13",  # too young ...
        "101",  # ... between the age and the height ranges
        "119",
        "231",  # too tall
        "45.5",  # an age is a whole number
        "45см",  # a unit makes it a height, and 45 cm is not one
        "1981.5",
        "0",
        "-45",
        "12345",
        # English: only labels are skipped, anything else still spoils the message
        "i am 45",
        "45 years old m 180 in",  # "in" is ambiguous (inches or a preposition), so not a label
        "45 years old boy",
        "age weight height",
        "age 45 aged 46",
    ],
)
def test_parse_profile_rejects(text: str | None) -> None:
    assert parse_profile(text, YEAR) is None


def test_parse_profile_ranges_follow_the_current_year() -> None:
    assert parse_profile("45", 2030) == ProfileUpdate(birth_year=1985)
    assert parse_profile("2016", 2030) == ProfileUpdate(birth_year=2016)
    assert parse_profile("2016", YEAR) is None  # 10 years old today
    assert parse_profile("1926", YEAR) == ProfileUpdate(birth_year=1926)
    assert parse_profile("1926", 2030) is None  # 104 years old by then


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
        # English
        "undo",
        "Undo that!",
        "scrap that",
        "delete this one",
        "remove this entry",
        "remove that one",
        "cancel this record plz",
        "delete this message",
        "Never mind.",
        "that's not mine",
        "That’s not mine",  # the typographic apostrophe phones substitute
        "thats not mine",
        "it's not mine",
        "Not my food",
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
        # English: the same safety net
        "remove bread",
        "remove the bread",
        "remove one",  # fewer pieces of the dish, not the record
        "delete one",
        "remove these",
        "delete the sauce please",
        "scrap the fries",
        "undo 300",
        "don't delete",
        "do not remove",
        "drop it",  # "drop" is an ingredient edit far more often than a delete
        "drop",
        "never mind the bread",
        "not mine, the bread",
        "remove item",
    ],
)
def test_delete_request_rejects(text: str | None) -> None:
    assert not is_delete_request(text)


@pytest.mark.parametrize("phrase", sorted(FOOD_CANCEL_PHRASES))
def test_every_regret_phrase_is_stored_in_canonical_form(phrase: str) -> None:
    """Not a check of the vocabulary - `test_food_cancel_request_accepts` spells that out from
    the spec. This pins the *shape* of the entries: the matcher joins normalised tokens, so an
    entry carrying capitals, punctuation or a double space could never be reached by a message."""
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
        "не записуй будь ласка",  # politeness is tolerated on any of them, at either end ...
        "це жарт, плз",
        "будь ласка, не записуй",  # ... and Ukrainian puts "будь ласка" in front just as often
        "плз не рахуй",
        "помилково pls",
        "видали",  # ... and everything the narrower predicate already took still counts
        "видали цей запис",
        "скасуй будь ласка",
        "це не моє",
        # English: "не записуй / не рахуй", in every apostrophe the keyboard produces
        "don't log",
        "Don’t log this.",
        "donʼt log",
        "dont log",
        "do not log",
        "don't log it",
        "don't record",
        "do not record this",
        "don't count",
        "don't count this",
        "don't save that",
        "dont track",
        "no need to log this",
        # "жарт"
        "joke",
        "it's a joke",
        "It was a joke!",
        "just a joke",
        "just kidding",
        "kidding",
        "jk",
        "I'm joking",
        # "випадково"
        "accident",
        "by accident",
        "accidentally",
        "sent by accident",
        "sent it by accident",
        "I sent it by accident",
        "I accidentally sent it",
        "it was an accident",
        # "помилка"
        "mistake",
        "my mistake",
        "by mistake",
        "it was a mistake",
        "sent it by mistake",
        "my bad",
        "oops",
        "Oops, wrong photo",
        "wrong photo",
        "wrong pic",
        # politeness on either side, like "будь ласка"
        "please don't log",
        "don't log this, please",
        "pls don't count it",
        "sorry, wrong photo",
        "my mistake, sorry",
        # the narrow English vocabulary counts here too
        "undo",
        "never mind",
        "not my food",
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
        # English: a content word turns the regret into a correction of the dish ...
        "don't log the bread",
        "don't count the sauce",
        "do not record the drink",
        "mistake in portion",
        "a mistake in the grams",
        "accidentally ate two",
        "it's a joke about borscht",
        "wrong portion",  # ... and so does any "wrong" other than the picture itself
        "wrong dish",
        "wrong food",
        "kidding aside it was 300 g",
        "log it",  # no negation, no regret
        "sorry",  # politeness alone is nothing
        "please",
        "mistaken",  # a near-miss word form is not the phrase
        "jokes",
        "remove bread",
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
    assert not is_delete_request("it's a joke")
    assert not is_delete_request("by accident")
    assert not is_delete_request("don't log")
    assert not is_delete_request("wrong photo")
    assert not is_delete_request("oops")


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("вчора млинці зі сметаною", ("млинці зі сметаною", True)),
        ("волейбол вчора 2 години", ("волейбол 2 години", True)),
        ("борщ вчора", ("борщ", True)),
        ("Вчора, млинці", ("млинці", True)),
        ("вчора - млинці", ("млинці", True)),
        ("вчора: млинці", ("млинці", True)),
        ("млинці, вчора", ("млинці", True)),
        ("учора борщ", ("борщ", True)),
        ("yesterday pancakes", ("pancakes", True)),
        ("YESTERDAY   борщ  і  хліб", ("борщ і хліб", True)),
        ("вчора і сьогодні вчора", ("і сьогодні", True)),  # every occurrence goes
        ("  борщ   і  хліб  ", ("борщ і хліб", False)),
        ("позавчора борщ", ("позавчора борщ", False)),
        ("вчорашній борщ", ("вчорашній борщ", False)),
        ("вчора", ("", True)),  # nothing left to describe
        ("", ("", False)),
        (None, ("", False)),
    ],
)
def test_strip_yesterday(text: str | None, expected: tuple[str, bool]) -> None:
    assert strip_yesterday(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("14:00 борщ і два бутерброди з салом", ("борщ і два бутерброди з салом", time(14, 0))),
        ("яєчня з 3 яєць 14-00", ("яєчня з 3 яєць", time(14, 0))),
        # the position is judged before "вчора" goes, which is `strip_yesterday`'s job afterwards
        ("14-00 вчора кава з молоком", ("вчора кава з молоком", time(14, 0))),
        ("вчора кавун 300г 14:00", ("вчора кавун 300г", time(14, 0))),
        ("вчора 14:00 кава", ("вчора 14:00 кава", None)),
        ("7:30 вівсянка", ("вівсянка", time(7, 30))),
        ("7-05 вівсянка", ("вівсянка", time(7, 5))),
        ("вівсянка 0:00", ("вівсянка", time(0, 0))),
        ("вівсянка 23:59", ("вівсянка", time(23, 59))),
        ("14:00, борщ", ("борщ", time(14, 0))),
        ("борщ 14:00.", ("борщ", time(14, 0))),
        ("  14:00   борщ  і  хліб ", ("борщ і хліб", time(14, 0))),
        ("14:00 борщ 19:30", ("борщ 19:30", time(14, 0))),  # one time only: the first wins
        ("кава 14:00 і тістечко", ("кава 14:00 і тістечко", None)),  # the middle is not a time
        ("кава 14:00 вчора", ("кава 14:00 вчора", None)),
        ("кава 14.00", ("кава 14.00", None)),
        ("кава 14 00", ("кава 14 00", None)),
        ("кава 14год", ("кава 14год", None)),
        ("о 14:00 кава", ("о 14:00 кава", None)),
        ("25:00 кава", ("25:00 кава", None)),
        ("кава 14:75", ("кава 14:75", None)),
        ("7:5 кава", ("7:5 кава", None)),
        ("114:00 кава", ("114:00 кава", None)),
        ("яєчня з 3 яєць", ("яєчня з 3 яєць", None)),
        ("14:00", ("", time(14, 0))),
        ("14:00 вчора", ("вчора", time(14, 0))),
        ("", ("", None)),
        ("   ", ("", None)),
        (None, ("", None)),
    ],
)
def test_strip_meal_time(text: str | None, expected: tuple[str, time | None]) -> None:
    assert strip_meal_time(text) == expected
