"""Pure text-parsing helpers. No I/O, fully unit-tested in `tests/test_parsing.py`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

# "84.3", "84,3", "84.3 кг", "84 kg", "вага 84.3", "вага: 84,3кг", "weight 84.3"
# The label, the decimal part and the unit are named because `parse_weight(require_marker=True)`
# asks whether at least one of them is there; a second regex for that question would drift.
_WEIGHT = re.compile(
    r"""^\s*
    (?P<label>(?:вага|weight|в|w)\s*[:\-]?\s*)?   # optional label
    (?P<num>\d{2,3}(?P<dec>[.,]\d{1,2})?)         # 2-3 integer digits, up to 2 decimals
    \s*(?P<unit>кг|kg)?\.?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_TIME = re.compile(r"\d{1,2}:\d{2}")
_DATE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}")
_PHONE = re.compile(r"^\+?\d[\d\s\-()]{6,}$")

# A correction is a bare kcal number, optionally with a unit: "650", "650 ккал", "≈650", "1 200",
# "650 kcal", "650 cal", "650 calories"
_CORRECTION = re.compile(
    r"""^\s*≈?\s*
    (?P<num>\d{1,2}(?:[\ \u00a0]\d{3})|\d{1,5})   # "650" or "1 200" (space / nbsp thousands)
    (?:[.,]\d)?                                     # a stray decimal is tolerated
    \s*(?:ккал|kcal|кал|калорій|kcals|calories|calorie|cals?)?\.?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_MAX_CORRECTION_KCAL = 10_000


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ".").replace(" ", "").replace(" ", ""))


def parse_weight(
    text: str | None, lo: float, hi: float, *, require_marker: bool = False
) -> float | None:
    """Extract a body weight from a short message.

    Accepts a bare number (with `.` or `,` decimal separator), an optional "кг"/"kg" unit and an
    optional "вага"/"weight" label. Rejects anything that looks like a time, a date or a phone
    number, and any value outside `[lo, hi]`.

    `require_marker` demands that the number carry one of those three markers - a decimal part, a
    unit or a label. The caller needs it where a bare integer is ambiguous: replying to a food
    estimate with "84" is a kcal correction, because `[lo, hi]` (40..200 by default) overlaps
    plausible portion calories, while "84.3", "84 кг" and "вага 84" can only be a weigh-in.
    """
    if not text:
        return None
    stripped = text.strip()
    if _TIME.search(stripped) or _DATE.search(stripped) or _PHONE.match(stripped):
        return None
    match = _WEIGHT.match(stripped)
    if not match:
        return None
    if require_marker and not (match.group("label") or match.group("dec") or match.group("unit")):
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
# "/ціль стоп" and friends clear the daily target - and, through the same predicate, the profile
# ("/профіль стоп"). Whole-message only, so English "no" and "none" are never read out of a
# sentence.
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
        "stop",
        "remove",
        "delete",
        "unset",
        "none",
        "no",
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


# -- profile (birth year, sex, height) -----------------------------------------------------------

PROFILE_AGE_MIN, PROFILE_AGE_MAX = 14, 100
HEIGHT_CM_MIN, HEIGHT_CM_MAX = 120, 230

_SEX_WORDS = {
    **dict.fromkeys(("m", "male", "man", "ч", "чол", "чоловік", "чоловіча"), "m"),
    **dict.fromkeys(("f", "female", "woman", "ж", "жін", "жінка", "жіноча"), "f"),
}
# Labels people put around the values ("рік народження 1981, зріст 180 см"): they carry no value
# of their own, so they are skipped, while every other unknown word rejects the whole message.
_PROFILE_FILLERS = frozenset(
    {
        "рік",
        "року",
        "р",
        "народження",
        "вік",
        "років",
        "роки",
        "р.н",  # "1981 р.н.": the trailing dot is stripped off the token first
        "н",  # ... and "1981 р. н.", spaced
        "зріст",
        "стать",
        "см",
        # the English labels: "year of birth 1981", "born 1981", "age 45", "aged 45",
        # "45 years old", "45 yrs", "45 y.o." (dot stripped as above), "180 cm tall", "gender f"
        "born",
        "birth",
        "year",
        "of",
        "yr",
        "age",
        "aged",
        "years",
        "yrs",
        "old",
        "y",
        "yo",
        "y.o",
        "height",
        "tall",
        "sex",
        "gender",
        "cm",
    }
)
# Whitespace, semicolons, colons ("зріст:180") and commas separate the values - except a decimal
# comma ("180,5"): one between a digit and exactly one more digit that ends the number, since a
# height is never typed to more than a millimetre. "180,45" (a height and an age) and "1981,180"
# are two values.
_PROFILE_SPLIT = re.compile(r"[\s;:]+|(?<!\d),|,(?!\d(?!\d))")
_PROFILE_NUMBER = re.compile(r"(?P<num>\d+(?P<dec>[.,]\d+)?)(?P<unit>см|cm)?")


def parse_sex(value: object) -> str | None:
    """A sheet cell or a typed word -> "m" / "f"; None when it names neither."""
    if value is None:
        return None
    return _SEX_WORDS.get(str(value).strip().lower().rstrip(".!").strip())


@dataclass(frozen=True)
class ProfileUpdate:
    """The profile fields a `/profile` message named; None means "not mentioned"."""

    birth_year: int | None = None
    sex: str | None = None
    height_cm: float | None = None


def _profile_number(token: str, current_year: int) -> tuple[str, float] | None:
    """The field a numeric token fills and its value, or None when it fits no range."""
    match = _PROFILE_NUMBER.fullmatch(token)
    if match is None:
        return None
    value = _to_float(match.group("num"))
    if match.group("unit") or match.group("dec"):
        # a unit or a fraction can only be a height: an age and a year are whole numbers
        return ("height_cm", value) if HEIGHT_CM_MIN <= value <= HEIGHT_CM_MAX else None
    if len(match.group("num")) == 4:
        year = int(value)
        if current_year - PROFILE_AGE_MAX <= year <= current_year - PROFILE_AGE_MIN:
            return "birth_year", year
        return None
    if PROFILE_AGE_MIN <= value <= PROFILE_AGE_MAX:
        return "birth_year", current_year - int(value)
    if HEIGHT_CM_MIN <= value <= HEIGHT_CM_MAX:
        return "height_cm", value
    return None


def parse_profile(text: str | None, current_year: int) -> ProfileUpdate | None:
    """Parse "/профіль 1981 ч 180" (any order, any subset) into the fields it names.

    A number is read by its range: a four-digit one is a birth year, 14..100 an age (turned into
    a birth year, which may be a year off - the reply shows what was stored), 120..230 a height
    in cm ("180см", "180,5" too). Returns None unless *every* token is a value or a known label
    and each field is given at most once: a typo must not be half-applied, silently keeping the
    fields that happened to parse.
    """
    if not text:
        return None
    fields: dict[str, Any] = {}
    for raw in _PROFILE_SPLIT.split(text.strip().lower()):
        token = raw.rstrip(".!")  # "1981 р.н."
        if not token or token in _PROFILE_FILLERS:
            continue
        sex = parse_sex(token)
        found = ("sex", sex) if sex is not None else _profile_number(token, current_year)
        if found is None or found[0] in fields:
            return None
        fields[found[0]] = found[1]
    if not fields:
        return None
    return ProfileUpdate(**fields)


# A reply asking for a record to be thrown away: a verb from `DELETE_WORDS`, optionally padded
# with words that carry no information ("видали цей запис", "delete this please"). Anything else
# in the message disqualifies it, because a stray noun changes the meaning entirely: "прибери
# хліб" asks to drop the bread from the estimate, not to drop the record, and must still reach
# Gemini as a correction.
DELETE_WORDS = frozenset(
    {
        "видали",
        "видалити",
        "видаліть",
        "прибери",
        "прибрати",
        "заберіть",
        "забери",
        "забрати",
        "зітри",
        "зітріть",
        "стерти",
        "скасуй",
        "скасувати",
        "delete",
        "remove",
        "del",
        "cancel",
        "erase",
        # Only English verbs that mean "throw the record away" and nothing else. No "drop", "skip"
        # or "minus": those are how people edit the dish ("drop the sauce"), and although the
        # whole-message rule rejects that sentence, "drop it" under an estimate is as likely to
        # be about the ingredient just discussed as about the row.
        "undo",
        "scrap",
    }
)
# Whole-message phrases that ask for the same thing without a verb in them. Matched against the
# normalised text, which has its apostrophes removed: "that's not mine" is stored as "thats ...".
DELETE_PHRASES = frozenset(
    {
        "не моє",
        "це не моє",
        "не мій",
        "це не мій",
        "not mine",
        "not my food",
        "this is not mine",
        "that is not mine",
        "thats not mine",
        "it is not mine",
        "its not mine",
        # how a prompt or a just-sent record is called off in English; they name nothing to edit
        "never mind",
        "nevermind",
        "nvm",
        "forget it",
        # "one" is whole-phrase only: as a filler it would make "remove one" - fewer pieces of
        # the dish on the estimate - delete the whole record
        "delete this one",
        "remove this one",
        "delete that one",
        "remove that one",
    }
)
# Words allowed to accompany a delete verb. Deliberately narrow: no "не"/"dont" (so "не видали"
# and "don't delete" stay refusals, not deletions) and no ingredient or dish nouns - nor a word
# like "item" that could name one.
_DELETE_FILLERS = frozenset(
    {
        "цей",
        "це",
        "цю",
        "цього",
        "той",
        "запис",
        "записи",
        "будь",
        "ласка",
        "плз",
        "пліз",
        "this",
        "that",
        "the",
        "it",
        "entry",
        "entries",
        "record",
        "records",
        "message",
        "please",
        "pls",
        "plz",
    }
)
_WORD = re.compile(r"\w+")  # run on `_normalise` output, which has no apostrophes left
# The apostrophes people type: ASCII, the typographic one phones substitute and the Ukrainian
# modifier letter. All three are dropped, so "don't", "don’t" and "dont" are one token.
_APOSTROPHES = re.compile(r"['’ʼ]")


def _normalise(text: str) -> str:
    """Lowercase, single-spaced, without apostrophes or the trailing punctuation people type when
    annoyed. No vocabulary entry carries an apostrophe, so dropping them changes no Ukrainian
    match - it only lets the English contractions be stored once ("dont log")."""
    lowered = _APOSTROPHES.sub("", text.strip().lower())
    return " ".join(lowered.split()).rstrip(".!?")


def is_delete_request(text: str | None) -> bool:
    """True when the message asks for the record to be thrown away and says nothing else."""
    if not text:
        return False
    normalised = _normalise(text)
    if normalised in DELETE_PHRASES:
        return True
    tokens = _WORD.findall(normalised)
    if not any(token in DELETE_WORDS for token in tokens):
        return False
    return all(token in DELETE_WORDS or token in _DELETE_FILLERS for token in tokens)


# Regret, rather than an order: the message carries no delete verb at all, so `is_delete_request`
# cannot see it, but it means exactly the same for a food row. Matched as a whole message and by
# whole words, which is the entire safety net here - "не записуй хліб" asks to drop an ingredient
# from the estimate and "помилкова порція" says the size is wrong, and both must still reach
# Gemini as corrections.
FOOD_CANCEL_PHRASES = frozenset(
    {
        "не записуй",
        "не записуй це",
        "не записуйте",
        "не записувати",
        "не треба записувати",
        "не треба це записувати",
        "не рахуй",
        "не рахуй це",
        "не враховуй",
        "не враховуй це",
        "жарт",
        "це жарт",
        "це був жарт",
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
        "я випадково відправила",
        "я випадково надіслав",
        "я випадково надіслала",
        "помилка",
        "це помилка",
        "помилково",
        "я помилково",
        # English, in the normalised spelling (apostrophes dropped: "don't" -> "dont").
        # "не записуй / не рахуй (це)": every "don't <verb> [this]" form, written out below.
        *(
            f"{neg} {verb}{obj}"
            for neg in ("dont", "do not")
            for verb in ("log", "record", "count", "save", "track")
            for obj in ("", " this", " that", " it")
        ),
        "no need to log",
        "no need to log this",
        "no need to record",
        "no need to record this",
        # "жарт", "жартую"
        "joke",
        "a joke",
        "just a joke",
        "its a joke",
        "it was a joke",
        "that was a joke",
        "joking",
        "just joking",
        "im joking",
        "i was joking",
        "kidding",
        "just kidding",
        "im kidding",
        "i was kidding",
        "jk",
        # "випадково (відправив)"
        "accident",
        "an accident",
        "by accident",
        "it was an accident",
        "accidentally",
        "accidentally sent",
        "i accidentally sent",
        "i accidentally sent it",
        "i accidentally sent this",
        "sent by accident",
        "sent it by accident",
        "sent this by accident",
        "i sent it by accident",
        "i sent this by accident",
        # "помилка", "помилково"
        "mistake",
        "a mistake",
        "my mistake",
        "its a mistake",
        "it was a mistake",
        "that was a mistake",
        "by mistake",
        "sent by mistake",
        "sent it by mistake",
        "i sent it by mistake",
        "my bad",
        "oops",
        "oops wrong photo",
        # Only the picture itself: "wrong dish" or "wrong food" may just as well say the model
        # misread a right photo, which is a correction.
        "wrong photo",
        "wrong picture",
        "wrong pic",
        "wrong image",
    }
)
# Tolerated at either end of any of those phrases, the way `_DELETE_FILLERS` tolerates them
# around a delete verb: "будь ласка, не записуй" and "не записуй, будь ласка" are the same ask.
# "sorry" goes with them ("sorry, wrong photo"): alone it strips to nothing and matches nothing.
_POLITENESS = frozenset({"будь", "ласка", "плз", "пліз", "please", "pls", "plz", "sorry"})


def is_food_cancel_request(text: str | None) -> bool:
    """True when the message asks for a *food* record to be thrown away.

    A superset of `is_delete_request`: on top of the delete verbs it takes the regret phrases
    people actually type after sending the wrong photo. Deliberately used by the food corrections
    only - a sport row and a `/їжа` prompt keep the narrower vocabulary, because those replies are
    rarer and the regret phrases are the kind of thing somebody says in passing.
    """
    if not text:
        return False
    if is_delete_request(text):
        return True
    tokens = _WORD.findall(_normalise(text))
    while tokens and tokens[-1] in _POLITENESS:
        tokens.pop()
    while tokens and tokens[0] in _POLITENESS:
        tokens.pop(0)
    return " ".join(tokens) in FOOD_CANCEL_PHRASES


# The marker that moves an entry to the previous day. Matched by whole words only, with the
# punctuation people glue to either side swallowed along the way ("вчора, млинці", "вчора -
# млинці", "млинці, вчора" all leave a clean dish): a substring match would shift "позавчора
# борщ" by one day instead of two and turn "вчорашній борщ" - a dish cooked yesterday and eaten
# now - into a backdated record.
_YESTERDAY = re.compile(
    r"""[\s,;:.!—–-]*
    (?<![\w'’ʼ])(?:вчора|учора|yesterday)(?![\w'’ʼ])
    [\s,;:.!—–-]*""",
    re.IGNORECASE | re.VERBOSE,
)


def strip_yesterday(text: str | None) -> tuple[str, bool]:
    """Split "вчора млинці" into the text Gemini should see and "this belongs to yesterday".

    Returns the message without every occurrence of the marker (whitespace normalised) and
    whether one was there. The marker is cut out rather than passed on because the dish name and
    the activity title come back from the model: "вчора млинці" would be stored as a dish.
    """
    if not text:
        return "", False
    stripped, count = _YESTERDAY.subn(" ", text)
    return " ".join(stripped.split()), count > 0


# The clock a `/їжа` description may carry: "14:00 борщ", "яєчня 14-00". Only the first or the last
# whitespace-separated token counts, and only as H:MM / HH:MM with ":" or "-": a time in the middle
# is usually part of the description ("кава о 14:00 і тістечко"), and the narrow rule keeps the
# ordinary numbers of a dish ("яєчня з 3 яєць", "2-3 ложки") well away from it. A comma, period or
# semicolon glued to the token ("14:00, борщ") is what people type and goes with it.
_MEAL_TIME = re.compile(r"(?P<h>[01]?\d|2[0-3])[:-](?P<m>[0-5]\d)[,.;]?")


def strip_meal_time(text: str | None) -> tuple[str, time | None]:
    """Split "14:00 борщ" into the text Gemini should see and the stated time of the meal.

    The position is judged on the raw text, before "вчора" is stripped, so "14-00 вчора кава" and
    "вчора кавун 14:00" both carry a time while "вчора 14:00 кава" does not. Only one time is taken:
    when both ends are clocks the first one wins and the last stays in the text. An invalid clock
    ("25:00", "14:75", "7:5") is simply not a time and stays where it was, like any other word.
    Whitespace is normalised either way, as `strip_yesterday` does.
    """
    tokens = (text or "").split()
    if not tokens:
        return "", None
    for index in (0, len(tokens) - 1):
        found = _MEAL_TIME.fullmatch(tokens[index])
        if found:
            rest = tokens[:index] + tokens[index + 1 :]
            return " ".join(rest), time(int(found["h"]), int(found["m"]))
    return " ".join(tokens), None


# -- stored timestamps --------------------------------------------------------------------------


def ts_time(value: Any) -> str | None:
    """Wall-clock "HH:MM" of a stored `ts` cell, or None when it carries no usable time.

    The `ts` column is written in the user's own timezone, so its wall clock is already the time
    to show: the offset (when there is one) is never applied. A date-only value has no time in it
    and must not read as midnight. Deliberately not `sheets._parse_ts`, which does a different
    job - it insists on an offset so two timestamps can be ordered across a DST switch.
    """
    raw = str(value).strip()
    if not raw or ("T" not in raw and " " not in raw):
        return None
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        return None


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
# "через 30 хвилин", "every 30 min", "each 30 min", "every hour", "every 2 hours",
# "once an hour", and the one-word "hourly" / "half-hourly"
_WATER_EVERY = re.compile(
    r"""(?:(?:кожн\w*|раз\s+(?:на|в)|через|\b(?:every|each|once\s+an?))\s*
    (?:(?P<num>\d{1,3})\s*)?
    (?P<unit>хвилин\w*|хв|мін\w*|годин\w*|год|minutes?|mins?|m|hours?|hrs?|h)\b
    |\b(?P<half>half[\s-]?)?hourly\b)""",
    re.IGNORECASE | re.VERBOSE,
)
# "з 9 до 18", "з 9:30 до 18:00", "від 9 до 18", "from 9 to 18", "between 9 and 18"
_WATER_WINDOW = re.compile(
    r"""\b(?:з|із|від|from|between)\s*
    (?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*
    (?:до|to|till|until|and|[-–—])\s*
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
    (
        re.compile(
            r"будн|weekday|робоч\w*\s+дн|work(?:ing)?\s*days?|business\s+days?", re.IGNORECASE
        ),
        WATER_WEEKDAYS,
    ),
    (re.compile(r"вихідн|weekend", re.IGNORECASE), WATER_WEEKEND),
    (
        re.compile(r"щодн|щоденно|кожн\w*\s+(?:дн|день)|(?:every|each)\s*day|daily", re.IGNORECASE),
        WATER_ALL_DAYS,
    ),
)
# abbreviations, full names ("вівторок", "п'ятниці", "thursday", "tues"), English plurals
# ("on mondays") and the two-letter Ukrainian forms
_WATER_DAY = (
    r"(?:пн|вт|ср|чт|пт|сб|нд|вс|понеділ\w*|вівтор\w*|серед\w*|четвер\w*|п[’'ʼ]?ятниц\w*"
    r"|субот\w*|неділ\w*|mon(?:days?)?|tue(?:s|sdays?)?|wed(?:s|nesdays?)?|thu(?:rs?|rsdays?)?"
    r"|fri(?:days?)?|sat(?:urdays?)?|sun(?:days?)?)"
)
_WATER_DAY_TOKEN = re.compile(rf"\b({_WATER_DAY})\b", re.IGNORECASE)
# "пн-пт", "mon - fri", "monday to friday"; a range is expanded before single tokens are
# collected. The English connectors count only between two day names, and the window ("from 9
# to 18") is cut out before the days are read, so its "to" can never join two days.
_WATER_DAY_RANGE = re.compile(
    rf"\b({_WATER_DAY})\s*(?:[-–—]|\s(?:to|through|thru|till|until)\s)\s*({_WATER_DAY})\b",
    re.IGNORECASE,
)
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
    if match.group("unit") is None:  # "hourly" / "half-hourly": the word is the whole interval
        return 30 if match.group("half") else 60
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
