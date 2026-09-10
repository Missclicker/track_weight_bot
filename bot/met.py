"""Small MET (metabolic equivalent) table for turning sport into burned kcal.

kcal = MET * body weight (kg) * hours. Values are rounded averages from the Compendium of
Physical Activities; they are good enough for a weekly trend, not for a lab.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_WEIGHT_KG = 80.0
DEFAULT_MINUTES = 30.0


@dataclass(frozen=True)
class Activity:
    """One row of the MET table."""

    key: str
    title: str  # Ukrainian display name
    met: float
    patterns: tuple[str, ...]  # regexes matched against lower-cased text
    min_per_km: float | None = None  # typical pace, used when only a distance is given


ACTIVITIES: dict[str, Activity] = {
    a.key: a
    for a in (
        Activity(
            "running",
            "біг",
            9.8,
            (
                r"\b(?:про|по)?біг\w*",
                r"\bбіга\w*",
                r"\bпробіжк\w*",
                r"\bjog\w*",
                r"\brun(?:ning|s)?\b",
                r"\bran\b",
            ),
            min_per_km=6.0,
        ),
        Activity(
            "walking",
            "ходьба",
            3.5,
            (
                # deliberately narrow: "крок за кроком", "гуляли по місту", "похід у магазин"
                # are everyday phrases, not workouts
                r"\bходьб\w*",
                r"\bпройш\w*",
                r"\bпрогулянк\w*",
                r"\bпрогулял\w*",
                r"\d\s*(?:тис\.?\s*)?крок\w*",
                r"\bwalk\w*",
                r"\bsteps?\b",
                r"\bhik\w*",
            ),
            min_per_km=12.0,
        ),
        Activity(
            "cycling",
            "велосипед",
            7.5,
            (r"\bвело\w*", r"\bвелик\b", r"\bbike\b", r"\bbicycl\w*", r"\bcycl\w*"),
            min_per_km=3.0,
        ),
        Activity(
            "swimming",
            "плавання",
            7.0,
            # not bare "плав\w*": "плавно" is an adverb
            (r"\bплава\w*", r"\bпоплав\w*", r"\bбасейн\w*", r"\bswim\w*", r"\bswam\b"),
            min_per_km=25.0,
        ),
        Activity(
            "gym",
            "зал / силове",
            5.0,
            (
                r"\b(?:в|у|до|з|із)\s+зал[іу]?\b",  # "в зал", "у залі" - not "зал суду"
                r"\bспортзал\w*",
                r"\bкачалк\w*",
                r"\bтренаж\w*",
                r"\bсилов\w*",
                r"\bтренув\w*",
                r"\bштанг\w*",
                r"\bприсід\w*",
                r"\bвідтиск\w*",
                r"\bпідтяг\w*",
                r"\bgym\b",
                r"\bworkout\w*",
                r"\blifting\b",
                r"\bstrength training\b",
            ),
        ),
        Activity(
            "cardio",
            "кардіо",
            8.0,
            (
                r"\bкардіо\w*",
                r"\bhiit\b",
                r"\bcardio\b",
                r"\bеліпс\w*",
                r"\bскакалк\w*",
                r"\bjump rope\b",
            ),
        ),
        Activity(
            "yoga",
            "йога / розтяжка",
            3.0,
            (
                r"\bйог\w*",
                r"\byoga\b",
                r"\bпілатес\w*",
                r"\bpilates\b",
                r"\bрозтяж\w*",
                r"\bstretch\w*",
            ),
        ),
        Activity("football", "футбол", 7.0, (r"\bфутбол\w*", r"\bfootball\b", r"\bsoccer\b")),
        Activity(
            "tennis",
            "теніс",
            7.3,
            (r"\bтеніс\w*", r"\btennis\b", r"\bбадмінтон\w*", r"\bпінг-?понг\w*", r"\bсквош\w*"),
        ),
        Activity("basketball", "баскетбол", 6.5, (r"\bбаскетбол\w*", r"\bbasketball\b")),
        Activity("volleyball", "волейбол", 4.0, (r"\bволейбол\w*", r"\bvolleyball\b")),
        Activity("dancing", "танці", 5.0, (r"\bтанц\w*", r"\bdanc\w*")),
        Activity("skiing", "лижі", 7.0, (r"\bлиж\w*", r"\bski(?:ing|ed|s)?\b")),
        Activity("skating", "ковзани / ролики", 7.0, (r"\bковзан\w*", r"\bролик\w*", r"\bskat\w*")),
        Activity("rowing", "гребля", 7.0, (r"\bгребл\w*", r"\brow(?:ing|ed)\b")),
    )
}

_COMPILED: list[tuple[Activity, re.Pattern[str]]] = [
    (activity, re.compile(pattern, re.IGNORECASE))
    for activity in ACTIVITIES.values()
    for pattern in activity.patterns
]


def find_activity(text: str) -> Activity | None:
    """Return the first activity whose keyword appears in `text`, or None."""
    lowered = text.lower()
    for activity, pattern in _COMPILED:
        if pattern.search(lowered):
            return activity
    return None


def kcal_burned(met: float, weight_kg: float, minutes: float) -> float:
    """Energy for an activity: MET * kg * hours."""
    return met * weight_kg * (minutes / 60.0)


def default_minutes(activity: Activity, distance_km: float | None) -> float:
    """Best-effort duration when the user gave only a distance (or nothing)."""
    if distance_km and activity.min_per_km:
        return distance_km * activity.min_per_km
    return DEFAULT_MINUTES


def estimate_kcal(
    activity_key: str,
    minutes: float | None,
    weight_kg: float | None,
    distance_km: float | None = None,
) -> tuple[float, float]:
    """Return `(minutes_used, kcal)` for an activity key from the table.

    Unknown keys fall back to a moderate 5 MET so a parsed entry is never lost.
    """
    activity = ACTIVITIES.get(activity_key) or Activity(activity_key, activity_key, 5.0, ())
    mins = minutes if minutes and minutes > 0 else default_minutes(activity, distance_km)
    kcal = kcal_burned(activity.met, weight_kg or DEFAULT_WEIGHT_KG, mins)
    return mins, round(kcal)
