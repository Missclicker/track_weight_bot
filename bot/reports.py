"""Aggregations over the sheet for `/today`, `/kcal`, the food-reply totals and the weekly report.

Both functions only need the repository interface (`user_rows_between`, `get_active_users`), so
they are tested against the in-memory `FakeRepo` in `tests/conftest.py`. The two pure helpers at
the end prepare the weekly report's text: last week's advice for the prompt and the split into
Telegram-sized messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, NamedTuple, Protocol

from bot import i18n
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


async def build_weekly_payload(repo: Repo, chat_id: int, week_start: date) -> dict[str, Any]:
    """JSON-serialisable summary of 7 days starting at `week_start` for every active user."""
    week_end = week_start + timedelta(days=6)
    users_payload: list[dict[str, Any]] = []
    for user in await repo.get_active_users(chat_id):
        food = await repo.user_rows_between("food", user.user_id, week_start, week_end)
        sport = await repo.user_rows_between("sport", user.user_id, week_start, week_end)
        weights = await repo.user_rows_between("weight", user.user_id, week_start, week_end)
        if not (food or sport or weights):
            continue
        days_with_food = {str(r.get("date")) for r in food}
        kcal_total = sum(num(r.get("kcal")) for r in food)
        veg_values = [num(r.get("veg_share")) for r in food if r.get("veg_share") not in ("", None)]
        w_first = num_or_none(weights[0].get("kg")) if weights else None
        w_last = num_or_none(weights[-1].get("kg")) if weights else None
        users_payload.append(
            {
                "name": user.name,
                "days_with_food_logged": len(days_with_food),
                "food_entries": len(food),
                "kcal_total": round(kcal_total),
                "kcal_avg_per_day": round(kcal_total / len(days_with_food))
                if days_with_food
                else 0,
                "alcohol_kcal": round(sum(num(r.get("alcohol_kcal")) for r in food)),
                "protein_g": round(sum(num(r.get("protein_g")) for r in food)),
                "fat_g": round(sum(num(r.get("fat_g")) for r in food)),
                "carbs_g": round(sum(num(r.get("carbs_g")) for r in food)),
                "veg_share_avg": round(sum(veg_values) / len(veg_values), 2)
                if veg_values
                else None,
                "sport_sessions": len(sport),
                "sport_minutes": round(sum(num(r.get("minutes")) for r in sport)),
                "sport_kcal": round(sum(num(r.get("kcal")) for r in sport)),
                "net_kcal": round(kcal_total - sum(num(r.get("kcal")) for r in sport)),
                "weight_first": w_first,
                "weight_last": w_last,
                "weight_delta": round(w_last - w_first, 1)
                if w_first is not None and w_last is not None
                else None,
                "weight_entries": len(weights),
                "target_kg": user.target_kg,
                "daily_kcal_target": user.daily_kcal_target,
                "height_cm": user.height_cm,
            }
        )
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
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
