"""Aggregations over the sheet for `/today` and the weekly report.

Both functions only need the repository interface (`user_rows_between`, `get_active_users`), so
they are tested against the in-memory `FakeRepo` in `tests/conftest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

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
