from datetime import datetime, time

import pytest

from bot.parsing import (
    WaterSchedule,
    is_water_due,
    looks_like_sport,
    parse_correction,
    parse_water_schedule,
    parse_weight,
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
    "text",
    [
        "пробіг 5 км за 30 хв",
        "Побігав 40 хвилин",
        "ранкова пробіжка 7 км",
        "в залі 1 година",
        "був у залі",
        "силове тренування 45 хв",
        "тренувався годину",
        "велосипед 20 км",
        "вело 1.5 години",
        "плавання 45 хв",
        "поплавав у басейні",
        "ходьба 10000 кроків",
        "пройшов 8 км",
        "йога 30 хв",
        "футбол 90 хвилин",
        "теніс 1 год",
        "ran 5k in 25 min",
        "gym 1h",
        "30 min swim",
        "cycling 40 km",
        "yoga 1 hour",
        "кардіо 20 хв",
        "танці 2 години",
    ],
)
def test_looks_like_sport_positive(text: str) -> None:
    assert looks_like_sport(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "84.3",
        "84,3 кг",
        "/sport біг 5 км",  # commands go through the command handler
        "привіт усім",
        "залишилось два дні до вихідних",  # 'зал' must not match 'залишилось'
        "обід був смачний",
        "хто йде в кіно?",
        "тобіго",
        "скільки ккал у борщі?",
        "x" * 301,
        # everyday phrases that share a stem with a sport keyword
        "плавно перейдемо до справи",
        "похід у магазин за хлібом",
        "гуляли по місту з дітьми",
        "крок за кроком",
        "skip lunch today",
        "a great skill",
        "my weight is 84.3",
    ],
)
def test_looks_like_sport_negative(text: str | None) -> None:
    assert not looks_like_sport(text)


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
