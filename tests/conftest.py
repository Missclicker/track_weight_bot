"""Shared test fixtures: an in-memory repository with the same interface as `SheetsRepo`,
and a scripted stand-in for the Gemini SDK call.
"""

from __future__ import annotations

import functools
from datetime import date, datetime
from typing import Any

import pytest
from google.genai import errors as genai_errors

from bot import ai
from bot.ai import FoodEstimate
from bot.config import Settings
from bot.sheets import HEADERS, User, WaterSubscription


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

    async def set_daily_kcal_target(self, user_id: int, chat_id: int, target: float | None) -> bool:
        user = await self.get_user(user_id, chat_id)
        if user is None:
            return False
        user.daily_kcal_target = target
        return True

    async def get_active_users(self, chat_id: int) -> list[User]:
        return [u for u in self.users if u.chat_id == chat_id and u.active]

    async def get_user(self, user_id: int, chat_id: int) -> User | None:
        return next((u for u in self.users if u.user_id == user_id and u.chat_id == chat_id), None)

    async def get_water_subscriptions(self) -> list[WaterSubscription]:
        by_user: dict[int, WaterSubscription] = {}
        for row in self.rows["water"]:
            sub = WaterSubscription.from_record(row)
            if sub is not None:
                by_user[sub.user_id] = sub
        return list(by_user.values())

    async def upsert_water_subscription(self, sub: WaterSubscription) -> None:
        row = dict(zip(HEADERS["water"], sub.to_row(), strict=True))
        for idx, existing in enumerate(self.rows["water"]):
            if existing["user_id"] == sub.user_id:
                self.rows["water"][idx] = row
                return
        self.rows["water"].append(row)

    async def deactivate_water_subscription(self, user_id: int) -> bool:
        for row in self.rows["water"]:
            if row["user_id"] == user_id and row["active"] == "TRUE":
                row["active"] = "FALSE"
                return True
        return False

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
                est.portion,
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
        message_id: int = 0,
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
                message_id,
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
            portion=est.portion,
            message_id=new_message_id,
            corrected="TRUE",
        )
        return True

    async def delete_food_entry(self, user_id: int, message_id: int) -> dict[str, Any] | None:
        row = self._food_row(user_id, message_id)
        if row is None:
            return None
        self.rows["food"].remove(row)
        return dict(row)

    async def delete_sport_entry(self, user_id: int, message_id: int) -> bool:
        row = next(
            (
                r
                for r in self.rows["sport"]
                if r["message_id"] == message_id and r["user_id"] == user_id
            ),
            None,
        )
        if row is None:
            return False
        self.rows["sport"].remove(row)
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


class FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class FakeModels:
    """Answers `generate_content` from a scripted list and records what every call was given.

    A list item is either an exception (raised) or a string (returned as the response text).
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.timeouts: list[int | None] = []
        # which model each call went to, so a test can pin the vision/text routing itself
        self.models: list[str] = []
        self.contents: list[Any] = []

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> FakeResponse:
        http_options = config.http_options
        self.timeouts.append(None if http_options is None else http_options.timeout)
        self.models.append(model)
        self.contents.append(contents)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    @property
    def calls(self) -> int:
        return len(self.timeouts)


class _Aio:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class _Client:
    def __init__(self, models: FakeModels) -> None:
        self.aio = _Aio(models)


@functools.cache
def _gemini() -> ai.GeminiClient:
    # Building a real `genai.Client` costs ~1 s, and the only state `_generate` touches is the SDK
    # object swapped out below - so one instance serves the whole suite.
    return ai.GeminiClient("test-key", "vision-model", "text-model")


def client_with(outcomes: list[Any]) -> tuple[ai.GeminiClient, FakeModels]:
    """A `GeminiClient` whose SDK call answers `outcomes` in order, plus the recorder."""
    client = _gemini()
    # The one cached client is shared by the whole suite, so a cooldown armed by one test would
    # silence the next one's calls. Start every test with both registers empty.
    client._cooldowns.clear()
    client._overloads.clear()
    models = FakeModels(outcomes)
    client._client = _Client(models)  # type: ignore[assignment]
    return client, models


def server_error(code: int = 504) -> genai_errors.APIError:
    return genai_errors.ServerError(code, {"error": {"status": "DEADLINE_EXCEEDED"}})


def client_error(code: int, details: Any | None = None) -> genai_errors.APIError:
    """A `ClientError` with `code`; `details` replaces the default (unparsed) error body."""
    return genai_errors.ClientError(
        code, details if details is not None else {"error": {"status": "INVALID_ARGUMENT"}}
    )


@pytest.fixture
def no_ai_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero the Gemini backoff sleeps so a retry test costs nothing."""
    monkeypatch.setattr(ai, "_RETRY_DELAYS_S", (0.0, 0.0))


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
