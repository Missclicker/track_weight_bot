"""Pure text-parsing helpers. No I/O, fully unit-tested in `tests/test_parsing.py`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

from bot import met

# "84.3", "84,3", "84.3 кг", "84 kg", "вага 84.3", "вага: 84,3кг", "weight 84.3"
_WEIGHT = re.compile(
    r"""^\s*
    (?:(?:вага|weight|в|w)\s*[:\-]?\s*)?   # optional label
    (?P<num>\d{2,3}(?:[.,]\d{1,2})?)       # 2-3 integer digits, up to 2 decimals
    \s*(?:кг|kg)?\.?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_TIME = re.compile(r"\d{1,2}:\d{2}")
_DATE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}")
_PHONE = re.compile(r"^\+?\d[\d\s\-()]{6,}$")

# A correction is a bare kcal number, optionally with a unit: "650", "650 ккал", "≈650", "1 200"
_CORRECTION = re.compile(
    r"""^\s*≈?\s*
    (?P<num>\d{1,2}(?:[\ \u00a0]\d{3})|\d{1,5})   # "650" or "1 200" (space / nbsp thousands)
    (?:[.,]\d)?                                     # a stray decimal is tolerated
    \s*(?:ккал|kcal|кал|калорій)?\.?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_MAX_CORRECTION_KCAL = 10_000


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ".").replace(" ", "").replace(" ", ""))


def parse_weight(text: str | None, lo: float, hi: float) -> float | None:
    """Extract a body weight from a short message.

    Accepts a bare number (with `.` or `,` decimal separator), an optional "кг"/"kg" unit and an
    optional "вага"/"weight" label. Rejects anything that looks like a time, a date or a phone
    number, and any value outside `[lo, hi]`.
    """
    if not text:
        return None
    stripped = text.strip()
    if _TIME.search(stripped) or _DATE.search(stripped) or _PHONE.match(stripped):
        return None
    match = _WEIGHT.match(stripped)
    if not match:
        return None
    value = _to_float(match.group("num"))
    if not lo <= value <= hi:
        return None
    return round(value, 2)


def parse_correction(text: str | None) -> float | None:
    """Parse a reply like "650" / "650 ккал" into a kcal number; None if it is not one."""
    if not text:
        return None
    match = _CORRECTION.match(text)
    if not match:
        return None
    value = _to_float(match.group("num"))
    if not 0 <= value <= _MAX_CORRECTION_KCAL:
        return None
    return value


KCAL_TARGET_MIN, KCAL_TARGET_MAX = 500, 10_000
# "/ціль стоп" and friends clear the daily target
TARGET_CLEAR_WORDS = frozenset(
    {
        "0",
        "стоп",
        "скинути",
        "скинь",
        "прибрати",
        "прибери",
        "немає",
        "без",
        "off",
        "reset",
        "clear",
    }
)


def parse_kcal_target(text: str | None) -> float | None:
    """Parse "/ціль 2000" / "2 000 ккал" into a daily kcal target; None if it is not a sane one."""
    if not text:
        return None
    match = _CORRECTION.match(text)
    if not match:
        return None
    value = _to_float(match.group("num"))
    if not KCAL_TARGET_MIN <= value <= KCAL_TARGET_MAX:
        return None
    return value


def is_target_clear_request(text: str | None) -> bool:
    return bool(text) and text.strip().lower().rstrip(".!") in TARGET_CLEAR_WORDS


def looks_like_sport(text: str | None) -> bool:
    """True when a free-text message mentions a sport keyword from the MET table.

    Commands, bare numbers and very long messages are not sport.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped or stripped.startswith("/") or len(stripped) > 300:
        return False
    if re.fullmatch(r"[\d\s.,:кгkg]+", stripped, re.IGNORECASE):
        return False
    return met.find_activity(stripped) is not None


# -- water reminders ----------------------------------------------------------------------------

WATER_MIN_INTERVAL_MIN = 15
WATER_MAX_INTERVAL_MIN = 720  # 12 hours
WATER_DEFAULT_START = time(9, 0)
WATER_DEFAULT_END = time(21, 0)
WATER_ALL_DAYS = frozenset(range(7))
WATER_WEEKDAYS = frozenset({0, 1, 2, 3, 4})
WATER_WEEKEND = frozenset({5, 6})


@dataclass(frozen=True)
class WaterSchedule:
    """When to remind someone to drink water.

    `days` holds weekday numbers (0=Monday .. 6=Sunday) and is never empty; both `start` and
    `end` are inclusive, so 09:00-18:00 every 30 minutes fires at 09:00, 09:30, ..., 18:00.
    """

    days: frozenset[int]
    start: time
    end: time
    every_min: int


# "кожні 30 хвилин", "кожні 30 хв", "кожну годину", "кожні 2 години", "раз на годину",
# "через 30 хвилин", "every 30 min", "every hour", "every 2 hours"
_WATER_EVERY = re.compile(
    r"""(?:кожн\w*|раз\s+(?:на|в)|через|every)\s*
    (?:(?P<num>\d{1,3})\s*)?
    (?P<unit>хвилин\w*|хв|мін\w*|годин\w*|год|minutes?|mins?|m|hours?|hrs?|h)\b""",
    re.IGNORECASE | re.VERBOSE,
)
# "з 9 до 18", "з 9:30 до 18:00", "від 9 до 18", "from 9 to 18"
_WATER_WINDOW = re.compile(
    r"""\b(?:з|із|від|from)\s*
    (?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*
    (?:до|to|[-–—])\s*
    (?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?""",
    re.IGNORECASE | re.VERBOSE,
)
# the bare form: "9-18", "09:00-18:00"
_WATER_WINDOW_DASH = re.compile(
    r"""(?<![\d:])(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*[-–—]\s*
    (?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?(?![\d:])""",
    re.VERBOSE,
)
_WATER_DAY_GROUPS: tuple[tuple[re.Pattern[str], frozenset[int]], ...] = (
    (re.compile(r"будн|weekday|робоч\w*\s+дн", re.IGNORECASE), WATER_WEEKDAYS),
    (re.compile(r"вихідн|weekend", re.IGNORECASE), WATER_WEEKEND),
    (
        re.compile(r"щодн|щоденно|кожн\w*\s+(?:дн|день)|every\s+day|daily", re.IGNORECASE),
        WATER_ALL_DAYS,
    ),
)
# abbreviations, full names ("вівторок", "п'ятниці", "thursday") and the two-letter Ukrainian forms
_WATER_DAY = (
    r"(?:пн|вт|ср|чт|пт|сб|нд|вс|понеділ\w*|вівтор\w*|серед\w*|четвер\w*|п[’'ʼ]?ятниц\w*"
    r"|субот\w*|неділ\w*|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?"
    r"|sat(?:urday)?|sun(?:day)?)"
)
_WATER_DAY_TOKEN = re.compile(rf"\b({_WATER_DAY})\b", re.IGNORECASE)
# "пн-пт", "mon - fri"; a range is expanded before single tokens are collected
_WATER_DAY_RANGE = re.compile(rf"\b({_WATER_DAY})\s*[-–—]\s*({_WATER_DAY})\b", re.IGNORECASE)
# looked up by the first three characters of the matched token (apostrophes removed), so
# "monday" == "mon" and "п'ятниця" == "пят"
_WATER_DAY_NUMBERS = {
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "нд": 6,
    "вс": 6,
    "пон": 0,
    "вів": 1,
    "сер": 2,
    "чет": 3,
    "пят": 4,
    "суб": 5,
    "нед": 6,
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _water_interval(match: re.Match[str]) -> int | None:
    """Minutes between reminders, or None when outside the allowed range."""
    count = int(match.group("num") or 1)
    unit = match.group("unit").lower()
    minutes = count * 60 if unit.startswith(("год", "h")) else count
    if not WATER_MIN_INTERVAL_MIN <= minutes <= WATER_MAX_INTERVAL_MIN:
        return None
    return minutes


def _water_time(hour: str, minute: str | None) -> time | None:
    h, m = int(hour), int(minute or 0)
    return time(h, m) if h <= 23 and m <= 59 else None


def _day_number(token: str) -> int:
    return _WATER_DAY_NUMBERS[re.sub(r"[’'ʼ]", "", token.lower())[:3]]


def _water_days(text: str) -> frozenset[int] | None:
    """Weekdays named in `text` (a group keyword wins over a list), or None when it names none."""
    for pattern, days in _WATER_DAY_GROUPS:
        if pattern.search(text):
            return days
    picked: set[int] = set()
    for m in _WATER_DAY_RANGE.finditer(text):
        first, last = _day_number(m.group(1)), _day_number(m.group(2))
        # "пт-пн" wraps around the weekend: fri, sat, sun, mon
        span = range(first, last + 1) if first <= last else [*range(first, 7), *range(0, last + 1)]
        picked.update(span)
    rest = _WATER_DAY_RANGE.sub(" ", text)
    picked.update(_day_number(m.group(1)) for m in _WATER_DAY_TOKEN.finditer(rest))
    return frozenset(picked) or None


def parse_water_schedule(text: str | None) -> WaterSchedule | None:
    """Parse a water-reminder schedule such as "будні дні з 9 до 18 кожні 30 хвилин".

    Ukrainian and English, any word order. The days default to every day and the window to
    09:00-21:00; the interval is the only required part. Returns None when there is no usable
    interval, when it is outside 15 minutes .. 12 hours, or when the window is inverted.
    """
    if not text:
        return None
    raw = text.strip().lower().replace(" ", " ")
    every = _WATER_EVERY.search(raw)
    if every is None:
        return None
    every_min = _water_interval(every)
    if every_min is None:
        return None
    # cut every recognised part out before looking for the next one, so a day name can never be
    # read out of the interval ("кожного дня") and a window never out of the interval's number
    rest = f"{raw[: every.start()]} {raw[every.end() :]}"
    start, end = WATER_DEFAULT_START, WATER_DEFAULT_END
    window = _WATER_WINDOW.search(rest) or _WATER_WINDOW_DASH.search(rest)
    if window is not None:
        first = _water_time(window.group("h1"), window.group("m1"))
        second = _water_time(window.group("h2"), window.group("m2"))
        if first is None or second is None or first >= second:
            return None
        start, end = first, second
        rest = f"{rest[: window.start()]} {rest[window.end() :]}"
    return WaterSchedule(
        days=_water_days(rest) or WATER_ALL_DAYS,
        start=start,
        end=end,
        every_min=every_min,
    )


def is_water_due(schedule: WaterSchedule, now: datetime) -> bool:
    """True when `now` (local wall time) is one of the schedule's reminder slots."""
    if now.weekday() not in schedule.days:
        return False
    minute = now.hour * 60 + now.minute
    start = schedule.start.hour * 60 + schedule.start.minute
    end = schedule.end.hour * 60 + schedule.end.minute
    if not start <= minute <= end:
        return False
    return (minute - start) % schedule.every_min == 0
