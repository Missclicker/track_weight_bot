"""SheetsRepo against an in-memory fake `Worksheet`.

These cover the gspread-facing logic that FakeRepo (tests/conftest.py) does not exercise:
row matching by string cells, upsert dedupe, ownership-scoped corrections, datetime ordering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from gspread.exceptions import APIError

from bot.ai import FoodEstimate
from bot.config import Settings
from bot.parsing import parse_water_schedule
from bot.sheets import HEADERS, SheetsRepo, User, WaterSubscription, explain_startup_error


class FakeWorksheet:
    """Enough of gspread.Worksheet for SheetsRepo; cells are strings, like the real API."""

    def __init__(self, headers: list[str]) -> None:
        self.rows: list[list[str]] = [list(headers)]

    def get_all_values(self) -> list[list[str]]:
        return [list(r) for r in self.rows]

    def get_all_records(
        self, expected_headers: list[str], value_render_option: Any, numericise_ignore: list[str]
    ) -> list[dict[str, Any]]:
        # The real repo must read stored values and skip gspread's numericising, otherwise a
        # decimal-comma locale turns "109,4" into 1094 (see SheetsRepo._records).
        assert str(value_render_option) == "UNFORMATTED_VALUE"
        assert numericise_ignore == ["all"]
        return [dict(zip(self.rows[0], r, strict=False)) for r in self.rows[1:]]

    def append_row(self, row: list[Any], value_input_option: str) -> None:
        assert value_input_option == "RAW"
        self.rows.append([str(v) for v in row])

    def update(self, values: list[list[Any]], range_name: str) -> None:
        row_no = int("".join(ch for ch in range_name if ch.isdigit()))
        self.rows[row_no - 1] = [str(v) for v in values[0]]

    def batch_update(self, data: list[dict[str, Any]]) -> None:
        for item in data:
            col = ord(item["range"][0]) - ord("A")
            row_no = int(item["range"][1:])
            self.rows[row_no - 1][col] = str(item["values"][0][0])


@pytest.fixture
def repo(settings: Settings) -> SheetsRepo:
    r = SheetsRepo(settings)
    r._worksheets = {tab: FakeWorksheet(h) for tab, h in HEADERS.items()}  # type: ignore[misc]
    return r


def ws(repo: SheetsRepo, tab: str) -> FakeWorksheet:
    return repo._worksheets[tab]  # type: ignore[return-value]


async def test_upsert_user_matches_on_user_and_chat_and_keeps_manual_columns(repo: SheetsRepo):
    await repo.upsert_user(User(user_id=1, chat_id=-100, name="A", tz="Europe/Kyiv"))
    await repo.upsert_user(User(user_id=1, chat_id=-200, name="A"))  # same person, other group
    ws(repo, "users").rows[1][HEADERS["users"].index("target_kg")] = "80"  # manual edit

    await repo.upsert_user(User(user_id=1, chat_id=-100, name="A renamed"))
    rows = ws(repo, "users").rows
    assert len(rows) == 3  # header + two (user, chat) rows, no duplicate
    assert rows[1][HEADERS["users"].index("name")] == "A renamed"
    assert rows[1][HEADERS["users"].index("target_kg")] == "80.0"
    assert rows[1][HEADERS["users"].index("tz")] == "Europe/Kyiv"  # kept when caller left it empty


async def test_get_user_dedupes_damaged_sheet(repo: SheetsRepo):
    sheet = ws(repo, "users")
    for _ in range(3):
        sheet.append_row(User(user_id=5, chat_id=-100, name="dup").to_row(), "RAW")
    assert len(await repo.get_active_users(-100)) == 1


async def test_update_food_kcal_is_scoped_to_owner(repo: SheetsRepo):
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    alice = User(user_id=1, chat_id=-100, name="Alice")
    bob = User(user_id=2, chat_id=-200, name="Bob")
    await repo.add_food(bob, FoodEstimate(dish="pizza", kcal=900), now, "photo", 42)
    await repo.add_food(alice, FoodEstimate(dish="salad", kcal=300), now, "photo", 42)

    assert await repo.update_food_kcal(alice.user_id, 42, 350) is True
    food = ws(repo, "food").rows
    col = HEADERS["food"].index("kcal")
    assert food[1][col] == "900.0"  # Bob's row (same message_id, other chat) untouched
    assert food[2][col] == "350"
    assert await repo.update_food_kcal(user_id=3, message_id=42, kcal=1) is False


async def test_last_weight_orders_by_datetime_not_string(repo: SheetsRepo):
    user = User(user_id=1, chat_id=-100, name="A")
    from zoneinfo import ZoneInfo

    kyiv = ZoneInfo("Europe/Kyiv")
    # "+03:00" sorts after "+02:00" as a string although it is the earlier instant
    earlier = datetime(2026, 10, 25, 3, 30, tzinfo=ZoneInfo("Etc/GMT-3"))  # 00:30Z
    later = datetime(2026, 10, 25, 3, 30, tzinfo=ZoneInfo("Etc/GMT-2"))  # 01:30Z
    await repo.add_weight(user, 85.0, earlier, "text")
    await repo.add_weight(user, 84.0, later, "text")
    got = await repo.last_weight(1, datetime(2026, 10, 26, tzinfo=kyiv))
    assert got is not None and got[1] == 84.0


def _water(user_id: int, text: str, chat_id: int = -100) -> WaterSubscription:
    schedule = parse_water_schedule(text)
    assert schedule is not None
    return WaterSubscription(
        user_id=user_id,
        chat_id=chat_id,
        name=f"user{user_id}",
        tz="Europe/Kyiv",
        schedule=schedule,
        updated_at="2026-09-10T09:00:00+03:00",
    )


async def test_upsert_water_subscription_keeps_one_row_per_user(repo: SheetsRepo):
    await repo.upsert_water_subscription(_water(1, "будні з 9 до 18 кожні 30 хвилин"))
    await repo.upsert_water_subscription(_water(2, "вихідні кожні 2 години"))
    await repo.upsert_water_subscription(_water(1, "щодня з 8 до 20 кожну годину"))

    rows = ws(repo, "water").rows
    assert len(rows) == 3  # header + one row per user, the first one rewritten in place
    assert rows[1][HEADERS["water"].index("days")] == "mon,tue,wed,thu,fri,sat,sun"
    assert rows[1][HEADERS["water"].index("start")] == "08:00"
    assert rows[1][HEADERS["water"].index("every_min")] == "60"

    subs = {s.user_id: s for s in await repo.get_water_subscriptions()}
    assert subs[1].schedule.every_min == 60
    assert subs[2].schedule.days == frozenset({5, 6})
    assert subs[1].tz == "Europe/Kyiv" and subs[1].active


async def test_deactivate_water_subscription_flips_the_cell(repo: SheetsRepo):
    await repo.upsert_water_subscription(_water(1, "кожні 30 хв"))
    col = HEADERS["water"].index("active")

    assert await repo.deactivate_water_subscription(1) is True
    assert ws(repo, "water").rows[1][col] == "FALSE"
    assert await repo.deactivate_water_subscription(1) is False  # already off
    assert await repo.deactivate_water_subscription(99) is False
    stored = await repo.get_water_subscriptions()
    assert len(stored) == 1 and stored[0].active is False


async def test_get_water_subscriptions_skips_a_broken_row(repo: SheetsRepo):
    await repo.upsert_water_subscription(_water(1, "кожні 30 хв"))
    # somebody edited the tab by hand: no days left, and a nonsense time
    ws(repo, "water").rows.append(["2", "-100", "B", "Europe/Kyiv", "", "9", "", "30", "TRUE", ""])
    ws(repo, "water").rows.append(["3", "-100", "C", "", "mon", "25:00", "26:00", "30", "TRUE", ""])
    # ... or an interval the command would never accept (this would be a DM every minute)
    ws(repo, "water").rows.append(["4", "-100", "D", "", "mon", "09:00", "18:00", "1", "TRUE", ""])
    ws(repo, "water").rows.append(
        ["5", "-100", "E", "", "mon", "09:00", "18:00", "1440", "TRUE", ""]
    )

    stored = await repo.get_water_subscriptions()
    assert [s.user_id for s in stored] == [1]


class _Resp:
    def __init__(self, code: int, message: str) -> None:
        self.status_code = code
        self._body = {"error": {"code": code, "message": message, "status": "X"}}

    def json(self) -> dict[str, Any]:
        return self._body


SA = "weight-bot@track-weight-bot.iam.gserviceaccount.com"


def test_explain_startup_error_api_not_enabled(settings: Settings):
    exc = APIError(_Resp(403, "Google Sheets API has not been used in project 1 before"))
    text = explain_startup_error(exc, settings, SA)
    assert "not enabled" in text and "track-weight-bot" in text and "wait" in text


def test_explain_startup_error_unwraps_gspread_permission_error(settings: Settings):
    try:
        try:
            raise APIError(_Resp(403, "Google Sheets API has not been used in project 1 before"))
        except APIError as ex:
            raise PermissionError from ex
    except PermissionError as wrapped:
        assert "not enabled" in explain_startup_error(wrapped, settings, SA)


def test_explain_startup_error_not_shared_and_not_found(settings: Settings):
    shared = explain_startup_error(
        APIError(_Resp(403, "The caller does not have permission")), settings, SA
    )
    assert "Share the sheet with " + SA in shared
    missing = explain_startup_error(
        APIError(_Resp(404, "Requested entity was not found")), settings, SA
    )
    assert "GOOGLE_SHEET_ID" in missing
    nofile = explain_startup_error(FileNotFoundError("x"), settings, None)
    assert "key file not found" in nofile


async def test_last_weight_accepts_decimal_comma_cell(repo: SheetsRepo):
    # a member typed the value by hand in a uk-locale sheet -> the cell is the text "109,4"
    ws(repo, "weight").rows.append(
        ["2026-09-10T09:38:28+03:00", "2026-09-10", "1", "Олексій", "109,4", "text"]
    )
    result = await repo.last_weight(1, datetime(2026, 9, 10, 12, 0, tzinfo=UTC))
    assert result == (datetime(2026, 9, 10).date(), 109.4)


async def test_get_and_update_food_entry(repo: SheetsRepo):
    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    alice = User(user_id=1, chat_id=-100, name="Alice")
    est = FoodEstimate(dish="борщ", kcal=600, protein_g=20, veg_share=0.4)
    await repo.add_food(alice, est, now, "photo", 42, "AgACfile")

    entry = await repo.get_food_entry(1, 42)
    assert entry is not None
    assert (entry["dish"], entry["kcal"], entry["veg_share"]) == ("борщ", 600.0, 0.4)
    assert entry["photo_file_id"] == "AgACfile"
    assert await repo.get_food_entry(2, 42) is None  # someone else's

    revised = FoodEstimate(dish="борщ з хлібом", kcal=720, carbs_g=60)
    assert await repo.update_food_entry(1, 42, revised, new_message_id=43) is True
    assert await repo.get_food_entry(1, 42) is None  # re-keyed ...
    after = await repo.get_food_entry(1, 43)
    assert after is not None and (after["dish"], after["kcal"]) == ("борщ з хлібом", 720.0)
    row = ws(repo, "food").rows[1]
    assert row[HEADERS["food"].index("corrected")] == "TRUE"
    assert row[HEADERS["food"].index("photo_file_id")] == "AgACfile"  # kept for later corrections


async def test_set_daily_kcal_target_writes_only_that_cell(repo: SheetsRepo) -> None:
    await repo.upsert_user(User(user_id=1, chat_id=-100, name="Олексій", tz="Europe/Kyiv"))
    ws = repo._worksheets["users"]  # type: ignore[attr-defined]
    ws.rows[1][HEADERS["users"].index("height_cm")] = "180"  # hand-edited column

    assert await repo.set_daily_kcal_target(1, -100, 2000) is True
    user = await repo.get_user(1, -100)
    assert user is not None and user.daily_kcal_target == 2000
    assert user.height_cm == 180  # untouched
    assert user.tz == "Europe/Kyiv"

    assert await repo.set_daily_kcal_target(1, -100, None) is True
    user = await repo.get_user(1, -100)
    assert user is not None and user.daily_kcal_target is None
    assert ws.rows[1][HEADERS["users"].index("daily_kcal_target")] == ""

    assert await repo.set_daily_kcal_target(2, -100, 2000) is False  # unknown user


def test_num_or_none_rejects_nan_and_inf() -> None:
    from bot.sheets import num_or_none

    assert num_or_none("nan") is None
    assert num_or_none("inf") is None
    assert num_or_none("2000") == 2000
    assert num_or_none("") is None
