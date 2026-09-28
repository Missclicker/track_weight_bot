"""Aggregations over the sheet for `/today`, `/kcal`, the food-reply totals and the weekly report.

Both functions only need the repository interface (`user_rows_between`, `get_active_users`), so
they are tested against the in-memory `FakeRepo` in `tests/conftest.py`. The two pure helpers at
the end prepare the weekly report's text: last week's advice for the prompt and the split into
Telegram-sized messages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, NamedTuple, Protocol

from bot import i18n, nutrition
from bot.parsing import ts_time
from bot.sheets import User, num, num_or_none


class Repo(Protocol):
    """Subset of `SheetsRepo` used by the report builders."""

    async def get_active_users(self, chat_id: int) -> list[User]: ...

    async def user_rows_between(
        self, tab: Any, user_id: int, start: date, end: date
    ) -> list[dict[str, Any]]: ...


@dataclass
class TodaySummary:
    kcal_in: float
    alcohol_kcal: float
    sport_kcal: float
    sport_minutes: float
    weight: float | None
    food_entries: int

    @property
    def net_kcal(self) -> float:
        return self.kcal_in - self.sport_kcal


class FoodItem(NamedTuple):
    """One logged meal. A plain tuple, so `(at, dish, kcal)` still compares equal to it."""

    at: str | None  # "HH:MM" in the user's own timezone, None when the row has no usable ts
    dish: str
    kcal: float


@dataclass
class DayFood:
    """Food entries of one user for one calendar day, in the order they were logged."""

    items: list[FoodItem]

    @property
    def total_kcal(self) -> float:
        return sum(item.kcal for item in self.items)


async def day_food(repo: Repo, user_id: int, day: date) -> DayFood:
    rows = await repo.user_rows_between("food", user_id, day, day)
    return DayFood(
        [FoodItem(ts_time(r.get("ts")), str(r.get("dish", "")), num(r.get("kcal"))) for r in rows]
    )


async def today_summary(repo: Repo, user: User, today: date) -> TodaySummary:
    """Per-user totals for one calendar day (in the user's timezone)."""
    food = await repo.user_rows_between("food", user.user_id, today, today)
    sport = await repo.user_rows_between("sport", user.user_id, today, today)
    weight = await repo.user_rows_between("weight", user.user_id, today, today)
    return TodaySummary(
        kcal_in=sum(num(r.get("kcal")) for r in food),
        alcohol_kcal=sum(num(r.get("alcohol_kcal")) for r in food),
        sport_kcal=sum(num(r.get("kcal")) for r in sport),
        sport_minutes=sum(num(r.get("minutes")) for r in sport),
        weight=num_or_none(weight[-1].get("kg")) if weight else None,
        food_entries=len(food),
    )


_Rows = list[dict[str, Any]]

# What `previous_week` carries: a subset of what `_window_numbers` computes for any window, so the
# week-over-week comparison measures both weeks by one definition that cannot drift apart.
_PREVIOUS_WEEK_KEYS = (
    "days_with_food_logged",
    "kcal_avg_per_day",
    "protein_g_avg_per_day",
    "protein_g_per_kg_avg",
    "alcohol_kcal",
    "veg_share_avg",
    "sport_sessions",
    "sport_minutes",
    "weight_last",
    "weight_delta",
)


def _dated_between(rows: _Rows, start: date, end: date) -> _Rows:
    """The rows whose `date` falls in `start..end`, in their order.

    The same string test `user_rows_between` applies, so one wide read can be split into windows
    in memory and every window still gets exactly the rows its own read would have returned.
    """
    lo, hi = start.isoformat(), end.isoformat()
    return [r for r in rows if lo <= str(r.get("date", "")) <= hi]


def _is_late_meal(row: dict[str, Any]) -> bool:
    at = ts_time(row.get("ts"))
    if at is None or at < nutrition.LATE_MEAL_FROM:
        return False
    # A row backdated with "вчора" keeps the moment the message was sent in `ts`, on another day
    # than its `date`: that clock time says when it was typed, not when the meal was eaten.
    return str(row.get("ts")).strip()[:10] == str(row.get("date", ""))


def _window_numbers(
    food: _Rows, sport: _Rows, weights: _Rows, reference_kg: float | None
) -> dict[str, Any]:
    """Everything one window's rows say, by the one definition both weeks of the report share.

    `reference_kg` comes from the caller rather than from these rows: it is the current week's for
    both windows, so the two g/kg averages divide by the same kilograms and can be compared.
    """
    per_day: dict[str, dict[str, float]] = {}
    for r in food:
        day = per_day.setdefault(
            str(r.get("date")), {"kcal": 0.0, "protein_g": 0.0, "alcohol_kcal": 0.0, "entries": 0}
        )
        day["kcal"] += num(r.get("kcal"))
        day["protein_g"] += num(r.get("protein_g"))
        day["alcohol_kcal"] += num(r.get("alcohol_kcal"))
        day["entries"] += 1
    # The per-day figures are rounded before anything counts days against them, so a day the model
    # is shown at 120 g is never a day that "missed" a 120 g target.
    days = [
        {
            "date": day,
            "kcal": round(sums["kcal"]),
            "protein_g": round(sums["protein_g"]),
            "alcohol_kcal": round(sums["alcohol_kcal"]),
            "entries": int(sums["entries"]),
        }
        for day, sums in sorted(per_day.items())
    ]
    logged = len(days)

    def per_logged_day(total: float) -> int:
        # over the days food was logged, not over 7: an unlogged day is missed logging
        return round(total / logged) if logged else 0

    kcal_total = sum(num(r.get("kcal")) for r in food)
    alcohol_total = sum(num(r.get("alcohol_kcal")) for r in food)
    protein_total = sum(num(r.get("protein_g")) for r in food)
    fat_total = sum(num(r.get("fat_g")) for r in food)
    carbs_total = sum(num(r.get("carbs_g")) for r in food)
    sport_kcal = sum(num(r.get("kcal")) for r in sport)
    veg_values = [num(r.get("veg_share")) for r in food if r.get("veg_share") not in ("", None)]
    w_first = num_or_none(weights[0].get("kg")) if weights else None
    w_last = num_or_none(weights[-1].get("kg")) if weights else None
    w_delta = round(w_last - w_first, 1) if w_first is not None and w_last is not None else None
    protein_avg = per_logged_day(protein_total)
    return {
        "days_with_food_logged": logged,
        "food_entries": len(food),
        "kcal_total": round(kcal_total),
        "kcal_avg_per_day": per_logged_day(kcal_total),
        "alcohol_kcal": round(alcohol_total),
        "protein_g": round(protein_total),
        "fat_g": round(fat_total),
        "carbs_g": round(carbs_total),
        "veg_share_avg": round(sum(veg_values) / len(veg_values), 2) if veg_values else None,
        "sport_sessions": len(sport),
        "sport_minutes": round(sum(num(r.get("minutes")) for r in sport)),
        "sport_kcal": round(sport_kcal),
        "net_kcal": round(kcal_total - sport_kcal),
        "weight_first": w_first,
        "weight_last": w_last,
        "weight_delta": w_delta,
        "weight_entries": len(weights),
        "weight_change_pct": round(w_delta / w_first * 100, 1)
        if w_delta is not None and w_first
        else None,
        "protein_g_avg_per_day": protein_avg,
        "fat_g_avg_per_day": per_logged_day(fat_total),
        "carbs_g_avg_per_day": per_logged_day(carbs_total),
        "protein_g_per_kg_avg": round(protein_avg / reference_kg, 2)
        if logged and reference_kg
        else None,
        "energy_share_pct": nutrition.energy_shares(
            protein_total, fat_total, carbs_total, alcohol_total
        ),
        "meals_per_logged_day": round(len(food) / logged, 1) if logged else None,
        "alcohol_days": sum(1 for d in days if d["alcohol_kcal"] > 0),
        "late_meals": sum(1 for r in food if _is_late_meal(r)),
        "days": days,
    }


async def build_weekly_payload(repo: Repo, chat_id: int, week_start: date) -> dict[str, Any]:
    """JSON-serialisable summary of 7 days starting at `week_start` for every active user.

    Besides the week's own numbers every person gets the ones a nutritionist reads them against
    (protein target, BMI, BMR, a maintenance estimate - see `bot/nutrition.py`) and the previous 7
    days measured the same way. All of it is computed here, so the model only interprets numbers
    and never has to invent one; what cannot be computed is None.
    """
    week_end = week_start + timedelta(days=6)
    prev_start, prev_end = week_start - timedelta(days=7), week_start - timedelta(days=1)
    users_payload: list[dict[str, Any]] = []
    for user in await repo.get_active_users(chat_id):
        # Three reads per person, as many as for one week: each one scans a whole tab against the
        # Sheets per-minute read quota, so both windows come out of one wider read each. Weight
        # goes back to the first row ever - the current weight may be a weigh-in from long ago.
        food_rows = await repo.user_rows_between("food", user.user_id, prev_start, week_end)
        sport_rows = await repo.user_rows_between("sport", user.user_id, prev_start, week_end)
        weight_rows = await repo.user_rows_between("weight", user.user_id, date.min, week_end)
        food = _dated_between(food_rows, week_start, week_end)
        sport = _dated_between(sport_rows, week_start, week_end)
        weights = _dated_between(weight_rows, week_start, week_end)
        if not (food or sport or weights):
            continue

        weight_current = num_or_none(weight_rows[-1].get("kg")) if weight_rows else None
        age = nutrition.age_on(user.birth_year, week_end)
        reference_kg = nutrition.reference_weight(weight_current, user.target_kg)
        g_per_kg = nutrition.protein_g_per_kg(age)
        protein_target = (
            round(g_per_kg * reference_kg)
            if g_per_kg is not None and reference_kg is not None
            else None
        )
        bmr = nutrition.bmr_mifflin(weight_current, user.height_cm, age, user.sex)
        this = _window_numbers(food, sport, weights, reference_kg)
        maintenance = nutrition.maintenance_kcal(bmr, this["sport_kcal"] / 7)
        kcal_target = user.daily_kcal_target
        # the same sanity rule as `i18n._target_suffix`: a zero or non-finite cell is no target
        has_kcal_target = kcal_target is not None and math.isfinite(kcal_target) and kcal_target > 0

        prev_food = _dated_between(food_rows, prev_start, prev_end)
        prev_sport = _dated_between(sport_rows, prev_start, prev_end)
        prev_weights = _dated_between(weight_rows, prev_start, prev_end)
        previous: dict[str, Any] | None = None
        if prev_food or prev_sport or prev_weights:
            prev = _window_numbers(prev_food, prev_sport, prev_weights, reference_kg)
            previous = {key: prev[key] for key in _PREVIOUS_WEEK_KEYS}

        users_payload.append(
            {
                "name": user.name,
                **this,
                "target_kg": user.target_kg,
                "daily_kcal_target": user.daily_kcal_target,
                "height_cm": user.height_cm,
                "days_over_kcal_target": sum(1 for d in this["days"] if d["kcal"] > kcal_target)
                if has_kcal_target
                else None,
                "age": age,
                "sex": user.sex,
                "weight_current": weight_current,
                "bmi": nutrition.bmi(weight_current, user.height_cm),
                "bmr_kcal": round(bmr) if bmr is not None else None,
                "maintenance_kcal_est": round(maintenance) if maintenance is not None else None,
                "reference_weight_kg": reference_kg,
                "protein_target_g_per_kg": g_per_kg,
                "protein_target_g_per_day": protein_target,
                "days_protein_target_met": sum(
                    1 for d in this["days"] if d["protein_g"] >= protein_target
                )
                if protein_target is not None
                else None,
                "previous_week": previous,
            }
        )
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "previous_week_start": prev_start.isoformat(),
        "previous_week_end": prev_end.isoformat(),
        "users": users_payload,
    }


# Telegram refuses a message over 4096 characters, counted in UTF-16 code units. The bot sends no
# emojis, so its text is almost entirely in the BMP where both counts agree; the margin covers the
# odd astral character and keeps a message clear of the edge.
MESSAGE_LIMIT = 4000
# Only bounds a hand-edited cell: a report the bot stored stays far below it (~1100 characters a
# person in a group), and it must stay far above that, or the last people of a larger group would
# lose last week's advice to the cut.
_MAX_ADVICE_CHARS = 8000
# What the prompt wraps last week's text in (see `ai._PREVIOUS_REPORT_BLOCK`).
_BLOCK_MARKERS = ("<<<", ">>>")


def previous_advice(text: str | None) -> str | None:
    """Last week's stored report reduced to what the model should follow up on, or None.

    A numbers-only fallback (`i18n.WEEKLY_AI_FAILED`) and a no-data report (`i18n.WEEKLY_NO_DATA`)
    carry no advice, so they count as no previous report at all: handing one over would only
    invite the model to follow up on advice nobody gave. The header line is our own date range,
    not the model's words, and is dropped; so is every "<<<" and ">>>".
    """
    if text is None or not text.strip():
        return None
    if i18n.WEEKLY_AI_FAILED in text or i18n.WEEKLY_NO_DATA in text:
        return None
    header = i18n.WEEKLY_HEADER.split("{", 1)[0].strip()
    first, _, rest = text.strip().partition("\n")
    advice = rest if first.startswith(header) else text
    # The cell is editable by hand, and a ">>>" inside it would close the prompt's data block early
    # and let the rest read as instructions. No report needs the markers, so they go - in a loop,
    # because cutting one out can join its neighbours into another ("<<>>><" -> "<<<") - and before
    # the cap, so the cap counts only what the model is actually given.
    while any(marker in advice for marker in _BLOCK_MARKERS):
        for marker in _BLOCK_MARKERS:
            advice = advice.replace(marker, "")
    return advice.strip()[:_MAX_ADVICE_CHARS] or None


def _split_point(text: str, limit: int) -> int:
    """Where to cut `text` so the piece before the cut fits in `limit` characters.

    The *last* paragraph break that fits wins, wherever it is in the window, so a paragraph (a
    person's block) that fits in the next message is never split across two. The one it skips is
    a break right under the first line: that would send the header line alone. Without such a
    break the last line break in the second half of the window is taken - an early one would send
    a near-empty message - then the last line break anywhere, and a single line longer than the
    whole window is cut hard. The index points *at* the separator, which the caller then drops.
    """
    first_end = text.find("\n")
    if first_end < 0:
        return limit  # one line: nothing to cut at but the limit
    # where the text after the first line begins: a paragraph break must come after that
    body = len(text) - len(text[first_end:].lstrip())
    # `+ len(sep)` in the ends below: the separator itself may end past the limit, it is not sent
    cut = text.rfind("\n\n", body, limit + 2)
    if cut > 0:
        return cut
    cut = text.rfind("\n", limit // 2, limit + 1)
    if cut > 0:
        return cut
    cut = text.rfind("\n", 0, limit + 1)
    return cut if cut > 0 else limit


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """`text` as Telegram-sized messages, in order, each at most `limit` characters.

    Cuts at paragraph boundaries where possible, then at line breaks, then hard (see
    `_split_point`). A text that fits is returned as it is. Otherwise the only thing dropped is
    whitespace at the cuts and at the two ends - the separator plus any blank space around it,
    which would only open or close a message with empty lines - so no content is lost or sent
    twice, and no piece is empty or whitespace-only (Telegram rejects an empty message).
    """
    if limit < 1:
        raise ValueError(f"limit must be positive, got {limit}")
    if len(text) <= limit:
        return [text] if text.strip() else []
    chunks: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        cut = _split_point(rest, limit)
        # `rest` never starts with whitespace, so the piece is never blank; and every cut is > 0,
        # so the loop always makes progress
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks
