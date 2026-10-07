"""English user-facing strings. Messages are sent with parse_mode=HTML, so every piece of user- or
model-supplied text interpolated into a constant must be HTML-escaped by the caller (the helper
functions below escape their own text arguments).

This module mirrors `bot/i18n/uk.py` name for name, with the same signatures and the same
`{placeholders}` (tests/test_i18n.py checks the parity). The comments explaining why a text is
worded the way it is - no promised durations in the AI notices, the per-logged-day averages in
the weekly block, the prefixes routing relies on - live in uk.py and apply here unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from html import escape

from bot.i18n._common import FOOD_PREFIX, fmt_delta, fmt_kg
from bot.parsing import WATER_ALL_DAYS, WATER_WEEKDAYS, WATER_WEEKEND, WaterSchedule

# the same names as `uk.__all__`
__all__ = [
    "AI_BUSY",
    "AI_BUSY_PHOTO",
    "AI_QUOTA_DAY",
    "AI_QUOTA_PHOTO_DAY",
    "AI_QUOTA_PHOTO_SOON",
    "AI_QUOTA_SOON",
    "AI_RETRYING",
    "BUDGET_LEFT",
    "BUDGET_NET",
    "BUDGET_OVER",
    "BUDGET_TARGET",
    "BUDGET_TARGET_SPORT",
    "CORRECTED_MARK",
    "CORRECTION_NOT_FOUND",
    "CORRECTION_NOT_UNDERSTOOD",
    "CORRECTION_SAVED",
    "DAY_TOTAL_DATE",
    "DAY_TOTAL_TODAY",
    "ERROR_TRY_AGAIN",
    "FOOD_DELETED",
    "FOOD_INPUT_PROMPT",
    "FOOD_INPUT_PROMPT_PREFIX",
    "FOOD_NOT_FOOD",
    "HELP",
    "KCAL_HEADER",
    "KCAL_HEADER_YESTERDAY",
    "KCAL_NO_DATA",
    "KCAL_NO_DATA_YESTERDAY",
    "KCAL_NO_TIME",
    "KCAL_SPORT_HEADER",
    "LANG_CURRENT",
    "LANG_NAME",
    "LANG_SET",
    "LANG_USAGE",
    "PING",
    "PING_PREFIX",
    "PRIVATE_CHAT_ONLY_GROUP",
    "PROFILE_CLEARED",
    "PROFILE_CURRENT",
    "PROFILE_NONE",
    "PROFILE_SET",
    "PROFILE_USAGE",
    "PROMPT_CANCELLED",
    "SPORT_DELETED",
    "SPORT_INPUT_PROMPT",
    "SPORT_INPUT_PROMPT_PREFIX",
    "SPORT_NOT_FOUND_FOR_DELETE",
    "SPORT_NOT_RECOGNIZED",
    "SPORT_PREFIX",
    "SPORT_SAVED",
    "SPORT_SAVED_DATE",
    "START_REGISTERED",
    "TARGET_CLEARED",
    "TARGET_CURRENT",
    "TARGET_NONE",
    "TARGET_SET",
    "TARGET_SPORT_SUFFIX",
    "TARGET_SUFFIX",
    "TARGET_USAGE",
    "TODAY_HEADER",
    "TODAY_NO_DATA",
    "WATER_DM_READY",
    "WATER_NEED_DM",
    "WATER_NOT_SUBSCRIBED",
    "WATER_PING",
    "WATER_STATUS",
    "WATER_STOPPED",
    "WATER_SUBSCRIBED",
    "WATER_SUBSCRIBED_DM",
    "WATER_USAGE",
    "WEEKLY_AI_FAILED",
    "WEEKLY_HEADER",
    "WEEKLY_NO_DATA",
    "WEIGHT_FIRST",
    "WEIGHT_OUT_OF_RANGE",
    "WEIGHT_SAME",
    "WEIGHT_USAGE",
    "WEIGHT_WITH_DELTA",
    "budget_line",
    "busy_notice",
    "day_total",
    "fmt_profile",
    "fmt_water_schedule",
    "food_estimate",
    "kcal_today",
    "quota_notice",
    "sport_saved",
    "today_summary",
    "weekly_stats_block",
]

SPORT_PREFIX = "Sport:"

PRIVATE_CHAT_ONLY_GROUP = "This bot works only in a group chat."

HELP = (
    "I log food, weight and sport to a shared spreadsheet.\n\n"
    "What I understand:\n"
    "- a food photo (a caption helps) - I estimate the calories and log them;\n"
    "- a number such as <code>84.3</code> - I log it as your weight;\n"
    "- a number in reply to my food message - I correct the estimate.\n\n"
    "Commands:\n"
    "/w 84.3 - log your weight\n"
    "/food borscht and two slices of bread - log food as text; without text I will ask\n"
    "/sport running 5 km 30 min - log an activity; without text I will ask\n"
    '/food yesterday pancakes - log it for yesterday (the word "yesterday" also works in /sport, '
    "in a reply to my question and in a photo caption)\n"
    "/today - my summary for today\n"
    "/kcal - what I ate today and how many kcal that is (/kcal yesterday - for yesterday)\n"
    "/target 2000 - daily kcal target (optional; /target stop - remove it)\n"
    "/profile 1981 m 180 - birth year, sex and height for the weekly report "
    "(optional; /profile stop - remove it)\n"
    "/week - report for the last 7 full days (today not included)\n"
    "/water weekdays from 9 to 18 every 30 min - reminders to drink water, in private messages\n"
    "/lang uk - switch to Ukrainian (перейти на українську)\n"
    "/help - this help\n\n"
    "Photo estimates are rough (±30-50%). To correct one, reply to it with a number of kcal.\n"
    'To remove a food or activity entry, reply "delete" to it.'
)

START_REGISTERED = (
    "Registered you, {name}. Every morning I expect your weight by {deadline}.\n\n" + HELP
)

# The way back to Ukrainian is also spelled in Ukrainian (see uk.LANG_CURRENT): somebody who
# switched here by mistake may not read English. These are the only Cyrillic lines in this module.
LANG_NAME = "English"
LANG_CURRENT = "Language: English. Switch to Ukrainian (перейти на українську): /lang uk"
LANG_SET = "Done, I will answer in English now."
LANG_USAGE = "Unknown language. Use /lang en (English) or /lang uk (українська)."

ERROR_TRY_AGAIN = "That did not work, please try again."
AI_RETRYING = "The AI did not answer, trying again."

AI_QUOTA_PHOTO_DAY = (
    "The daily AI limit for photos is used up. "
    "Describe the meal in text: /food borscht and two slices of bread."
)
AI_QUOTA_PHOTO_SOON = (
    "Too many AI requests. Try sending the photo again in a minute "
    "or describe the meal in text: /food borscht and two slices of bread."
)
AI_QUOTA_DAY = "The daily AI limit is used up. Try again later."
AI_QUOTA_SOON = "Too many AI requests. Try again in a minute."


def quota_notice(vision: bool, daily: bool) -> str:
    """The message for a spent Gemini quota: `vision` is the photo path, `daily` the per-day one."""
    if vision:
        return AI_QUOTA_PHOTO_DAY if daily else AI_QUOTA_PHOTO_SOON
    return AI_QUOTA_DAY if daily else AI_QUOTA_SOON


AI_BUSY = "The AI is overloaded right now. Try again in a minute."
AI_BUSY_PHOTO = (
    "The AI is overloaded right now. Try sending the photo again in a minute "
    "or describe the meal in text: /food borscht and two slices of bread."
)


def busy_notice(vision: bool) -> str:
    """The message for an overloaded Gemini model: `vision` is the photo path."""
    return AI_BUSY_PHOTO if vision else AI_BUSY


WEIGHT_USAGE = "Send your weight like this: /w 84.3"
WEIGHT_OUT_OF_RANGE = (
    "That does not look like a weight. I expect a number from {lo:g} to {hi:g} kg."
)
WEIGHT_FIRST = "Logged {kg} kg. This is your first entry."
WEIGHT_WITH_DELTA = (
    "Logged {kg} kg. Change since the previous entry ({prev} kg, {prev_date}): {delta} kg."
)
WEIGHT_SAME = "Logged {kg} kg. No change since the previous entry ({prev_date})."

FOOD_INPUT_PROMPT_PREFIX = "Waiting for the meal description."
FOOD_INPUT_PROMPT = (
    FOOD_INPUT_PROMPT_PREFIX
    + " Reply to this message, for example: borscht and two slices of bread."
)
FOOD_NOT_FOOD = "I do not see any food here. If it is food after all, add a caption to the photo."
CORRECTION_SAVED = "Corrected: {kcal} kcal."
DAY_TOTAL_TODAY = "Total for today: {kcal} kcal{target}."
DAY_TOTAL_DATE = "Total for {date}: {kcal} kcal{target}."
CORRECTION_NOT_FOUND = "Could not find the entry to correct."
CORRECTION_NOT_UNDERSTOOD = (
    "I did not understand the correction. "
    "Tell me what to change: the portion weight, the ingredients or the name of the dish."
)
CORRECTED_MARK = "Corrected as you described."
FOOD_DELETED = "Deleted the entry."
PROMPT_CANCELLED = "Cancelled."

SPORT_INPUT_PROMPT_PREFIX = "Waiting for the activity description."
SPORT_INPUT_PROMPT = (
    SPORT_INPUT_PROMPT_PREFIX + " Reply to this message, for example: running 5 km 30 min."
)
SPORT_NOT_RECOGNIZED = (
    'Could not recognise the activity. Try like this: "running 5 km 30 min" or "gym 1 hour".'
)
SPORT_SAVED = SPORT_PREFIX + " {activity}, {minutes} min{distance} - about {kcal} kcal."
SPORT_SAVED_DATE = (
    SPORT_PREFIX + " {activity}, {minutes} min{distance} - about {kcal} kcal. Logged for {date}."
)
SPORT_DELETED = "Deleted the activity entry."
SPORT_NOT_FOUND_FOR_DELETE = "Could not find the entry to delete."

WATER_USAGE = (
    "Reminders to drink water. Tell me when and how often:\n"
    "/water weekdays from 9 to 18 every 30 min\n"
    "/water daily from 8:30 to 22:00 every hour\n"
    "/water mon, wed, fri 10-19 every 2 hours\n\n"
    "Days: weekdays, weekend, daily, a list (mon, wed, fri) or a range (mon-fri); "
    "every day by default.\n"
    "Time: from 9 to 18 or 9:00-18:00, within one day; 09:00 to 21:00 by default.\n"
    "The interval is required: from 15 min to 12 h.\n"
    "My reminders: /water. Turn them off: /water stop."
)
WATER_SUBSCRIBED = (
    "Water reminders: {schedule}. In your time zone ({tz}).\n"
    "I will write to you in private messages. Turn them off: /water stop."
)
WATER_SUBSCRIBED_DM = (
    "Water reminders: {schedule}. I will write to you here. Turn them off: /water stop."
)
WATER_PING = "Time to drink a glass of water."
WATER_STATUS = (
    "Water reminders: {schedule}. In your time zone ({tz}).\n"
    "To change them, send the command with a new schedule. Turn them off: /water stop."
)
WATER_STOPPED = "Turned the water reminders off."
WATER_NOT_SUBSCRIBED = "You have no water reminders."
WATER_NEED_DM = (
    "I cannot write to you in private messages. Open {link}, press Start and repeat the command."
)
WATER_DM_READY = (
    "Now I can write to you here. Water reminders: "
    "/water weekdays from 9 to 18 every 30 min - here or in the group."
)

_WATER_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def fmt_water_schedule(schedule: WaterSchedule) -> str:
    """One-line summary of a water schedule: "weekdays, 09:00-18:00, every 30 min"."""
    if schedule.days == WATER_ALL_DAYS:
        days = "every day"
    elif schedule.days == WATER_WEEKDAYS:
        days = "weekdays"
    elif schedule.days == WATER_WEEKEND:
        days = "weekends"
    else:
        days = ", ".join(_WATER_DAY_NAMES[d] for d in sorted(schedule.days))
    if schedule.every_min % 60:
        every = f"every {schedule.every_min} min"
    else:
        hours = schedule.every_min // 60
        every = "every hour" if hours == 1 else f"every {hours} h"
    window = f"{schedule.start:%H:%M}-{schedule.end:%H:%M}"
    return f"{days}, {window}, {every}"


PING_PREFIX = "Good morning!"
PING = PING_PREFIX + " Not weighed in today yet: {mentions}. Reply to this message with a number."

WEEKLY_HEADER = "Weekly report {start} - {end}"
WEEKLY_NO_DATA = "No entries this week."
WEEKLY_AI_FAILED = "(AI recommendations are unavailable, showing the numbers only.)"

TODAY_HEADER = "Your day, {name} ({date}):"
TODAY_NO_DATA = "No entries today yet."

KCAL_HEADER = "Food today, {name} ({date}):"
KCAL_NO_DATA = "No food logged today yet."
KCAL_HEADER_YESTERDAY = "Food yesterday, {name} ({date}):"
KCAL_NO_DATA_YESTERDAY = "No food was logged yesterday."
KCAL_NO_TIME = "--:--"
KCAL_SPORT_HEADER = SPORT_PREFIX  # never the first line of `/kcal`, see uk.py

BUDGET_TARGET = "Target: {target} kcal"
BUDGET_TARGET_SPORT = "Target: {target} + {sport} for sport = {budget} kcal"
BUDGET_LEFT = "{kcal} left"
BUDGET_OVER = "{kcal} over target"
BUDGET_NET = "Net of sport: {kcal} kcal"
TARGET_SUFFIX = " (target {target})"
TARGET_SPORT_SUFFIX = " (target {target} + {sport} for sport)"

TARGET_USAGE = (
    "Daily calorie target: /target 2000 (from {lo} to {hi} kcal). "
    "It is optional - without one I just count. Remove it: /target stop."
)
TARGET_SET = "Target saved: {kcal} kcal a day. I will show it next to the total."
TARGET_CLEARED = "Removed the daily target. From now on I just count calories."
TARGET_CURRENT = "Your daily target: {kcal} kcal. Change it: /target 1800. Remove it: /target stop."
TARGET_NONE = "No daily target. Set one: /target 2000."

PROFILE_USAGE = (
    "Profile for the weekly report, optional: birth year (or age), sex and height - "
    "in any order, and any one of them alone works too.\n"
    "/profile 1981 m 180\n"
    "/profile 45 female 165.5 cm\n"
    "/profile 182 - change only the height\n\n"
    "Birth year: from {year_lo} to {year_hi}, or age: from {age_lo} to {age_hi}.\n"
    "Sex: m, male or f, female.\n"
    "Height: from {height_lo} to {height_hi} cm.\n"
    "The report takes your personal protein target and an energy expenditure estimate from the "
    "profile. Remove it: /profile stop."
)
PROFILE_SET = (
    "Profile saved: {profile}.\n"
    "It is optional: the weekly report takes your personal protein target and an energy "
    "expenditure estimate from it. Remove it: /profile stop."
)
PROFILE_CLEARED = "Removed the profile. The weekly report will do without it."
PROFILE_CURRENT = (
    "Your profile: {profile}.\n"
    "It is optional: the weekly report takes your personal protein target and an energy "
    "expenditure estimate from it.\n"
    "Change it: /profile 1981 m 180 (one field works too, e.g. /profile 45). "
    "Remove it: /profile stop."
)
PROFILE_NONE = (
    "No profile, it is optional. With your birth year, sex and height the weekly report works "
    "out a personal protein target and an energy expenditure estimate.\n"
    "Set one: /profile 1981 m 180 (or the age instead of the year: /profile 45 f 165)."
)
_SEX_NAMES = {"m": "male", "f": "female"}


def fmt_profile(
    birth_year: int | None, sex: str | None, height_cm: float | None, current_year: int
) -> str:
    """One line, "born 1981 (age 45), sex male, height 180 cm"; unknown fields are left out, and
    nothing known at all gives "". See `uk.fmt_profile` for why the age is shown."""
    parts: list[str] = []
    if birth_year is not None:
        age = current_year - birth_year
        parts.append(f"born {birth_year}" + (f" (age {age})" if age > 0 else ""))
    if sex in _SEX_NAMES:
        parts.append(f"sex {_SEX_NAMES[sex]}")
    if height_cm is not None:
        parts.append(f"height {fmt_kg(height_cm)} cm")
    return ", ".join(parts)


def _valid_target(daily_target: float | None) -> int | None:
    """The target as it is displayed, or None when no (sane) target is set."""
    if daily_target is None or not math.isfinite(daily_target) or daily_target <= 0:
        return None
    return round(daily_target)


def _target_suffix(daily_target: float | None, sport_kcal: float = 0) -> str:
    """Suffix " (target 2000)", or " (target 2000 + 320 for sport)" with sport that day - or
    nothing when no (sane) target is set."""
    target = _valid_target(daily_target)
    if target is None:
        return ""
    sport = round(sport_kcal)
    if sport > 0:
        return TARGET_SPORT_SUFFIX.format(target=target, sport=sport)
    return TARGET_SUFFIX.format(target=target)


def budget_line(food_kcal: float, sport_kcal: float, daily_target: float | None) -> str | None:
    """The day's food against target + sport, without a trailing period, or None.

    "Target: 2000 + 320 for sport = 2320 kcal, 1280 left", "Target: 2000 kcal, 960 left",
    "... 150 over target"; without a target "Net of sport: 720 kcal" on a day with both food and
    sport. The rules are `uk.budget_line`'s.
    """
    food, sport, target = round(food_kcal), round(sport_kcal), _valid_target(daily_target)
    if target is None:
        if sport > 0 and food > 0:
            return BUDGET_NET.format(kcal=food - sport)
        return None
    if sport > 0:
        budget = target + sport
        head = BUDGET_TARGET_SPORT.format(target=target, sport=sport, budget=budget)
    else:
        budget = target
        head = BUDGET_TARGET.format(target=target)
    rest = budget - food
    tail = BUDGET_LEFT.format(kcal=rest) if rest >= 0 else BUDGET_OVER.format(kcal=-rest)
    return f"{head}, {tail}"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def day_total(
    kcal: float, daily_target: float | None, date_str: str | None = None, sport_kcal: float = 0
) -> str:
    """Line "Total for today: 1130 kcal (target 2000)." - or "for <date>" for a past day; with
    sport that day the suffix is "(target 2000 + 320 for sport)"."""
    target = _target_suffix(daily_target, sport_kcal)
    if date_str is None:
        return DAY_TOTAL_TODAY.format(kcal=f"{kcal:.0f}", target=target)
    return DAY_TOTAL_DATE.format(date=date_str, kcal=f"{kcal:.0f}", target=target)


def food_estimate(
    dish: str,
    kcal: float,
    alcohol_kcal: float,
    protein_g: float,
    fat_g: float,
    carbs_g: float,
    veg_share: float,
    notes: str,
    portion: str,
    corrected: bool = False,
    day_total_line: str | None = None,
) -> str:
    """Bot reply to a food photo, `/food` text or a correction. Must start with FOOD_PREFIX."""
    dish_line = f"{FOOD_PREFIX} {kcal:.0f} kcal - {escape(dish)}"
    if portion:
        dish_line += f", {escape(portion)}"
    lines = [
        dish_line,
        f"Protein {protein_g:.0f} g, fat {fat_g:.0f} g, carbs {carbs_g:.0f} g, "
        f"vegetables {veg_share * 100:.0f}%.",
    ]
    if alcohol_kcal > 0:
        lines.append(f"Including alcohol: {alcohol_kcal:.0f} kcal.")
    if notes:
        lines.append(escape(notes))
    if corrected:
        lines.append(CORRECTED_MARK)
    if day_total_line:
        lines.append(day_total_line)
    lines.append(
        "To correct it, reply to this message with a number of kcal or a clarification "
        "(portion weight, ingredients, name of the dish)."
    )
    return "\n".join(lines)


def sport_saved(
    activity_title: str,
    minutes: float,
    distance_km: float | None,
    kcal: float,
    date_str: str | None = None,
) -> str:
    """Confirmation for a stored activity; `date_str` names the day when it is not today."""
    distance = f", {distance_km:g} km" if distance_km else ""
    template = SPORT_SAVED if date_str is None else SPORT_SAVED_DATE
    return template.format(
        activity=escape(activity_title),
        minutes=f"{minutes:.0f}",
        distance=distance,
        kcal=f"{kcal:.0f}",
        date=date_str,
    )


def today_summary(
    name: str,
    date_str: str,
    kcal_in: float,
    alcohol_kcal: float,
    sport_kcal: float,
    sport_minutes: float,
    weight: float | None,
    food_entries: int,
    daily_target: float | None,
) -> str:
    lines = [TODAY_HEADER.format(name=escape(name), date=date_str)]
    if food_entries == 0 and sport_minutes == 0 and weight is None:
        lines.append(TODAY_NO_DATA)
        return "\n".join(lines)
    lines.append(f"Food: {kcal_in:.0f} kcal ({_plural(food_entries, 'entry', 'entries')})")
    if alcohol_kcal:
        lines.append(f"Including alcohol: {alcohol_kcal:.0f} kcal")
    if sport_minutes:
        lines.append(f"Sport: {sport_minutes:.0f} min, {sport_kcal:.0f} kcal")
    budget = budget_line(kcal_in, sport_kcal, daily_target)
    if budget is not None:
        lines.append(budget)
    if weight is not None:
        lines.append(f"Weight: {fmt_kg(weight)} kg")
    return "\n".join(lines)


def kcal_today(
    name: str,
    date_str: str,
    items: list[tuple[str | None, str, float]],
    daily_target: float | None,
    sport: Sequence[tuple[str | None, str, float, float | None, float]] = (),
    *,
    yesterday: bool = False,
) -> str:
    """`/kcal`: the day's food entries and their total, the day's sport and the target line.
    Never starts with FOOD_PREFIX, nor with SPORT_PREFIX.

    Items are `(at, dish, kcal)` and sport `(at, activity, minutes, distance_km, kcal)` as in
    `uk.kcal_today`.
    """
    header = KCAL_HEADER_YESTERDAY if yesterday else KCAL_HEADER
    lines = [header.format(name=escape(name), date=date_str)]
    if items:
        lines.extend(
            f"{at or KCAL_NO_TIME} - {kcal:.0f} kcal - {escape(dish)}" for at, dish, kcal in items
        )
        lines.append(f"Total: {sum(kcal for *_, kcal in items):.0f} kcal.")
    else:
        lines.append(KCAL_NO_DATA_YESTERDAY if yesterday else KCAL_NO_DATA)
        if not sport:
            return "\n".join(lines)
    if sport:
        lines.append(KCAL_SPORT_HEADER)
        for at, activity, minutes, distance_km, kcal in sport:
            distance = f", {distance_km:g} km" if distance_km else ""
            lines.append(
                f"{at or KCAL_NO_TIME} - {escape(activity)}{distance}, {minutes:.0f} min - "
                f"{kcal:.0f} kcal"
            )
    budget = budget_line(
        sum(kcal for *_, kcal in items), sum(kcal for *_, kcal in sport), daily_target
    )
    if budget is not None:
        lines.append(budget + ".")
    return "\n".join(lines)


def weekly_stats_block(user: dict) -> str:
    """Plain numeric block per user, used as a fallback when the AI report fails."""
    name = str(user["name"])  # sent with parse_mode=None, so no escaping here
    days = user["days_with_food_logged"]
    total = f"{name}: {user['kcal_total']:.0f} kcal this week"
    if days:
        logged = _plural(days, "logged day", "logged days")
        total += f" (≈{user['kcal_avg_per_day']:.0f}/day over {logged})"
    parts = [total]
    protein = user.get("protein_g_avg_per_day")
    if days and protein is not None:
        part = f"protein ≈{protein:.0f} g/day"
        target = user.get("protein_target_g_per_day")
        if target is not None:
            part += f" (target {target:.0f} g)"
        parts.append(part)
    if user["alcohol_kcal"]:
        parts.append(f"alcohol {user['alcohol_kcal']:.0f} kcal")
    if user["sport_minutes"]:
        parts.append(f"sport {user['sport_minutes']:.0f} min, -{user['sport_kcal']:.0f} kcal")
    if user["weight_first"] is not None and user["weight_last"] is not None:
        parts.append(
            f"weight {fmt_kg(user['weight_first'])} -> {fmt_kg(user['weight_last'])} kg "
            f"({fmt_delta(user['weight_delta'])})"
        )
    return "; ".join(parts)
