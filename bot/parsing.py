"""Pure text-parsing helpers. No I/O, fully unit-tested in `tests/test_parsing.py`."""

from __future__ import annotations

import re

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
