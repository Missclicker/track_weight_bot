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
from bot.sheets import HEADERS, SheetsRepo, User, explain_startup_error


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
