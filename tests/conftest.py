"""Shared test fixtures: an in-memory repository with the same interface as `SheetsRepo`."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from bot.ai import FoodEstimate
from bot.config import Settings
from bot.sheets import HEADERS, User


class FakeRepo:
    """In-memory stand-in for `SheetsRepo`; rows are dicts keyed by the tab headers."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {tab: [] for tab in HEADERS}
        self.users: list[User] = []

    def _append(self, tab: str, values: list[Any]) -> None:
        self.rows[tab].append(dict(zip(HEADERS[tab], values, strict=True)))

    async def ensure_schema(self) -> dict[str, int]:
        return {tab: len(rows) for tab, rows in self.rows.items()}

    async def upsert_user(self, user: User) -> None:
        self.users = [u for u in self.users if u.user_id != user.user_id] + [user]

    async def get_active_users(self, chat_id: int) -> list[User]:
        return [u for u in self.users if u.chat_id == chat_id and u.active]

    async def get_user(self, user_id: int, chat_id: int) -> User | None:
        return next((u for u in self.users if u.user_id == user_id and u.chat_id == chat_id), None)

    async def add_weight(self, user: User, kg: float, when: datetime, source: str) -> None:
        self._append(
            "weight",
            [when.isoformat(), when.date().isoformat(), user.user_id, user.name, kg, source],
        )

    async def add_food(
        self,
        user: User,
        est: FoodEstimate,
        when: datetime,
        source: str,
        message_id: int,
        photo_file_id: str = "",
    ) -> None:
        self._append(
            "food",
            [
                when.isoformat(),
                when.date().isoformat(),
                user.user_id,
                user.name,
                est.dish,
                est.kcal,
                est.alcohol_kcal,
                est.protein_g,
                est.fat_g,
                est.carbs_g,
                est.veg_share,
                est.confidence,
                source,
                message_id,
                "FALSE",
                photo_file_id,
            ],
        )

    async def add_sport(
        self,
        user: User,
        activity: str,
        minutes: float,
        distance_km: float | None,
        kcal: float,
        when: datetime,
        source: str,
    ) -> None:
        self._append(
            "sport",
            [
                when.isoformat(),
                when.date().isoformat(),
                user.user_id,
                user.name,
                activity,
                minutes,
                distance_km if distance_km is not None else "",
                kcal,
                source,
            ],
        )

    async def add_report(self, week_start: date, chat_id: int, text: str, when: datetime) -> None:
        self._append("reports", [when.isoformat(), week_start.isoformat(), chat_id, text])

    def _food_row(self, user_id: int, message_id: int) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.rows["food"]
                if row["message_id"] == message_id and row["user_id"] == user_id
            ),
            None,
        )

    async def get_food_entry(self, user_id: int, message_id: int) -> dict[str, Any] | None:
        row = self._food_row(user_id, message_id)
        return dict(row) if row is not None else None

    async def update_food_entry(
        self, user_id: int, message_id: int, est: FoodEstimate, new_message_id: int
    ) -> bool:
        row = self._food_row(user_id, message_id)
        if row is None:
            return False
        row.update(
            dish=est.dish,
            kcal=est.kcal,
            alcohol_kcal=est.alcohol_kcal,
            protein_g=est.protein_g,
            fat_g=est.fat_g,
            carbs_g=est.carbs_g,
            veg_share=est.veg_share,
            confidence=est.confidence,
            message_id=new_message_id,
            corrected="TRUE",
        )
        return True

    async def update_food_kcal(self, user_id: int, message_id: int, kcal: float) -> bool:
        for row in self.rows["food"]:
            if row["message_id"] == message_id and row["user_id"] == user_id:
                row["kcal"] = kcal
                row["corrected"] = "TRUE"
                return True
        return False

    async def weights_for_date(self, chat_id: int, day: date) -> dict[int, float]:
        members = {u.user_id for u in await self.get_active_users(chat_id)}
        return {
            int(r["user_id"]): float(r["kg"])
            for r in self.rows["weight"]
            if int(r["user_id"]) in members and r["date"] == day.isoformat()
        }

    async def user_rows_between(
        self, tab: str, user_id: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        lo, hi = start.isoformat(), end.isoformat()
        rows = [
            r for r in self.rows[tab] if int(r["user_id"]) == user_id and lo <= str(r["date"]) <= hi
        ]
        return sorted(rows, key=lambda r: str(r["ts"]))

    async def last_weight(self, user_id: int, before: datetime) -> tuple[date, float] | None:
        mine = [
            r
            for r in self.rows["weight"]
            if int(r["user_id"]) == user_id and str(r["ts"]) < before.isoformat()
        ]
        if not mine:
            return None
        last = max(mine, key=lambda r: str(r["ts"]))
        return date.fromisoformat(last["date"]), float(last["kg"])


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def user() -> User:
    return User(user_id=1, chat_id=-100, name="Олексій", tz="Europe/Kyiv", daily_kcal_target=2000)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123:abc",
        allowed_chat_ids="-100",
        google_sheet_id="sheet",
        google_service_account_json='{"type": "service_account"}',
        gemini_api_key="key",
    )
