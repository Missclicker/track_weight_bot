"""Google Sheets storage.

`SheetsRepo` wraps the synchronous gspread client; every public method is `async` and runs the
blocking call in a worker thread so handlers never block the event loop. Transient API errors
(429 / 5xx) are retried with exponential backoff.

Tab layout (headers must match the README):

    users   user_id, chat_id, name, username, tz, active, joined_at, height_cm, target_kg,
            daily_kcal_target
    weight  ts, date, user_id, name, kg, source
    food    ts, date, user_id, name, dish, kcal, alcohol_kcal, protein_g, fat_g, carbs_g,
            veg_share, confidence, source, message_id, corrected, photo_file_id
    sport   ts, date, user_id, name, activity, minutes, distance_km, kcal, source
    reports ts, week_start, chat_id, text
    water   user_id, chat_id, name, tz, days, start, end, every_min, active, updated_at
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import ValueRenderOption

from bot.ai import FoodEstimate
from bot.config import WEEKDAYS, Settings, parse_hhmm
from bot.parsing import WATER_MAX_INTERVAL_MIN, WATER_MIN_INTERVAL_MIN, WaterSchedule

log = logging.getLogger(__name__)

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

Tab = Literal["users", "weight", "food", "sport", "reports", "water"]

HEADERS: dict[str, list[str]] = {
    "users": [
        "user_id",
        "chat_id",
        "name",
        "username",
        "tz",
        "active",
        "joined_at",
        "height_cm",
        "target_kg",
        "daily_kcal_target",
    ],
    "weight": ["ts", "date", "user_id", "name", "kg", "source"],
    "food": [
        "ts",
        "date",
        "user_id",
        "name",
        "dish",
        "kcal",
        "alcohol_kcal",
        "protein_g",
        "fat_g",
        "carbs_g",
        "veg_share",
        "confidence",
        "source",
        "message_id",
        "corrected",
        "photo_file_id",
    ],
    "sport": [
        "ts",
        "date",
        "user_id",
        "name",
        "activity",
        "minutes",
        "distance_km",
        "kcal",
        "source",
    ],
    "reports": ["ts", "week_start", "chat_id", "text"],
    "water": [
        "user_id",
        "chat_id",
        "name",
        "tz",
        "days",
        "start",
        "end",
        "every_min",
        "active",
        "updated_at",
    ],
}

USERS_CACHE_TTL_S = 60.0
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_DELAYS_S = (1.0, 2.0, 4.0)


@dataclass
class User:
    """A participant; one row of the `users` tab."""

    user_id: int
    chat_id: int
    name: str
    username: str = ""
    tz: str = ""
    active: bool = True
    joined_at: str = ""
    height_cm: float | None = None
    target_kg: float | None = None
    daily_kcal_target: float | None = None

    def to_row(self) -> list[Any]:
        return [
            self.user_id,
            self.chat_id,
            self.name,
            self.username,
            self.tz,
            "TRUE" if self.active else "FALSE",
            self.joined_at,
            _blank(self.height_cm),
            _blank(self.target_kg),
            _blank(self.daily_kcal_target),
        ]

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> User:
        return cls(
            user_id=int(rec["user_id"]),
            chat_id=int(rec["chat_id"]),
            name=str(rec.get("name", "")),
            username=str(rec.get("username", "") or ""),
            tz=str(rec.get("tz", "") or ""),
            active=str(rec.get("active", "TRUE")).strip().upper() in ("TRUE", "1", "YES"),
            joined_at=str(rec.get("joined_at", "") or ""),
            height_cm=num_or_none(rec.get("height_cm")),
            target_kg=num_or_none(rec.get("target_kg")),
            daily_kcal_target=num_or_none(rec.get("daily_kcal_target")),
        )


def _blank(value: float | None) -> Any:
    return "" if value is None else value


def num_or_none(value: Any) -> float | None:
    """Coerce a sheet cell (str / int / float / '') to float."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def num(value: Any) -> float:
    return num_or_none(value) or 0.0


def days_to_cell(days: frozenset[int]) -> str:
    """`{0, 2, 4}` -> `"mon,wed,fri"` (the `water.days` cell)."""
    return ",".join(WEEKDAYS[d] for d in sorted(days))


def days_from_cell(value: str) -> frozenset[int]:
    """Inverse of `days_to_cell`; unknown names are dropped."""
    names = [p.strip().lower()[:3] for p in value.split(",")]
    return frozenset(WEEKDAYS.index(n) for n in names if n in WEEKDAYS)


@dataclass
class WaterSubscription:
    """One person's water-reminder schedule; one row of the `water` tab.

    `tz` is a copy of the user's zone at subscribe time, so the per-minute scheduler tick never
    has to join with the `users` tab. There is one row per `user_id` (a person has a single
    schedule); `chat_id` only records where they subscribed.
    """

    user_id: int
    chat_id: int
    name: str
    tz: str
    schedule: WaterSchedule
    active: bool = True
    updated_at: str = ""

    def to_row(self) -> list[Any]:
        return [
            self.user_id,
            self.chat_id,
            self.name,
            self.tz,
            days_to_cell(self.schedule.days),
            self.schedule.start.strftime("%H:%M"),
            self.schedule.end.strftime("%H:%M"),
            self.schedule.every_min,
            "TRUE" if self.active else "FALSE",
            self.updated_at,
        ]

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> WaterSubscription | None:
        """Build a subscription from a sheet row, or None when the row is not usable.

        The tab is meant to be editable by hand, so a broken row must be skipped rather than
        take the whole tick down.
        """
        try:
            days = days_from_cell(str(rec.get("days", "")))
            start = parse_hhmm(str(rec.get("start", "")))
            end = parse_hhmm(str(rec.get("end", "")))
            every_min = int(num(rec.get("every_min")))
            user_id = int(num(rec["user_id"]))
        except (KeyError, TypeError, ValueError):
            return None
        # the same limits the command enforces: a hand-typed `1` must not turn into a DM a minute
        if not days or start >= end or not user_id:
            return None
        if not WATER_MIN_INTERVAL_MIN <= every_min <= WATER_MAX_INTERVAL_MIN:
            return None
        return cls(
            user_id=user_id,
            chat_id=int(num(rec.get("chat_id"))),
            name=str(rec.get("name", "")),
            tz=str(rec.get("tz", "") or ""),
            schedule=WaterSchedule(days=days, start=start, end=end, every_min=every_min),
            active=str(rec.get("active", "TRUE")).strip().upper() in ("TRUE", "1", "YES"),
            updated_at=str(rec.get("updated_at", "") or ""),
        )


def _status_code(exc: APIError) -> int | None:
    code = getattr(exc, "code", None)
    if code is None and getattr(exc, "response", None) is not None:
        code = exc.response.status_code
    return code


def with_retry(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call `fn`, retrying on rate-limit / server errors with exponential backoff."""
    for attempt, delay in enumerate((*_RETRY_DELAYS_S, None)):
        try:
            return fn(*args, **kwargs)
        except APIError as exc:
            if delay is None or _status_code(exc) not in _RETRY_STATUSES:
                raise
            log.warning("Sheets API %s, retry %d in %.0fs", _status_code(exc), attempt + 1, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def explain_startup_error(exc: Exception, settings: Settings, sa_email: str | None) -> str:
    """Turn the usual first-run Sheets failures into a plain-language hint (README steps 3-4)."""
    who = sa_email or "the service-account e-mail (client_email in the key file)"
    # gspread's open_by_key re-raises 403 as a bare PermissionError and 404 as SpreadsheetNotFound;
    # the useful text lives in the chained APIError.
    if not isinstance(exc, APIError) and isinstance(exc.__cause__, APIError):
        exc = exc.__cause__
    if isinstance(exc, APIError):
        code = _status_code(exc)
        text = str(exc)
        if code == 403 and ("has not been used" in text or "is disabled" in text):
            project = sa_email.split("@")[1].split(".")[0] if sa_email else "?"
            return (
                "Google Sheets API is not enabled in the service account's project.\n"
                f"  Key file project: {project} - enable the API in THAT project "
                "(README step 3), not in another one.\n"
                "  If you enabled it a moment ago, wait 2-5 minutes and start again."
            )
        if code == 403:
            return (
                "The service account cannot open the spreadsheet.\n"
                f"  Share the sheet with {who} as Editor (README step 4)."
            )
        if code == 404:
            return (
                f"Spreadsheet {settings.google_sheet_id!r} not found.\n"
                "  GOOGLE_SHEET_ID is the part of the URL between /d/ and /edit."
            )
        return f"Google Sheets API error {code}: {text}"
    if isinstance(exc, FileNotFoundError):
        return f"Service-account key file not found: {settings.google_service_account_file}"
    if isinstance(exc, ValueError):
        return f"Service-account key is not a valid JSON key file: {exc}"
    return f"{type(exc).__name__}: {exc}"


class SheetsRepo:
    """Async facade over one spreadsheet."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._spreadsheet: gspread.Spreadsheet | None = None
        self._worksheets: dict[str, gspread.Worksheet] = {}
        self._users_cache: tuple[float, list[User]] | None = None
        self.service_account_email: str | None = None

    # -- connection -------------------------------------------------------------------------

    def _connect(self) -> gspread.Spreadsheet:
        if self._spreadsheet is None:
            if self._settings.google_service_account_json:
                info = json.loads(self._settings.google_service_account_json)
                creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            else:
                creds = Credentials.from_service_account_file(
                    str(self._settings.google_service_account_file), scopes=SCOPES
                )
            self.service_account_email = creds.service_account_email
            client = gspread.authorize(creds)
            self._spreadsheet = with_retry(client.open_by_key, self._settings.google_sheet_id)
        return self._spreadsheet

    def _ws(self, tab: str) -> gspread.Worksheet:
        # `Spreadsheet.worksheet()` re-fetches the sheet metadata on every call; cache the handles.
        ws = self._worksheets.get(tab)
        if ws is None:
            ws = with_retry(self._connect().worksheet, tab)
            self._worksheets[tab] = ws
        return ws

    def _records(self, tab: str) -> list[dict[str, Any]]:
        # Read stored values, not the locale-formatted display text: in a uk/de/... spreadsheet
        # 109.4 displays as "109,4", and gspread's own numericising then reads the comma as a
        # thousands separator (-> 1094). `num()` handles the remaining string cases itself.
        return with_retry(
            self._ws(tab).get_all_records,
            expected_headers=HEADERS[tab],
            value_render_option=ValueRenderOption.unformatted,
            numericise_ignore=["all"],
        )

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.to_thread(fn, *args)

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self._settings.google_sheet_id}/edit"

    # -- schema -----------------------------------------------------------------------------

    def _ensure_schema_sync(self) -> dict[str, int]:
        """Create missing tabs and headers. Returns data row count per tab."""
        sheet = self._connect()
        counts: dict[str, int] = {}
        for tab, headers in HEADERS.items():
            try:
                ws = with_retry(sheet.worksheet, tab)
            except WorksheetNotFound:
                ws = with_retry(sheet.add_worksheet, title=tab, rows=1000, cols=len(headers))
                log.info("created tab %s", tab)
            if ws.col_count < len(headers):  # a column was added in a newer version
                with_retry(ws.add_cols, len(headers) - ws.col_count)
            first_row = with_retry(ws.row_values, 1)
            if first_row != headers:
                with_retry(ws.update, [headers], "A1")
                with_retry(ws.freeze, rows=1)
                log.info("wrote headers for tab %s", tab)
            self._worksheets[tab] = ws
            counts[tab] = max(len(with_retry(ws.col_values, 1)) - 1, 0)
        return counts

    async def ensure_schema(self) -> dict[str, int]:
        return await self._run(self._ensure_schema_sync)

    # -- users ------------------------------------------------------------------------------

    def _all_users_sync(self) -> list[User]:
        now = time.monotonic()
        if self._users_cache and now - self._users_cache[0] < USERS_CACHE_TTL_S:
            return self._users_cache[1]
        # Dedupe by (user_id, chat_id), last row wins — protects against duplicate rows that may
        # have been created by hand or by older versions.
        by_key: dict[tuple[int, int], User] = {}
        for rec in self._records("users"):
            if rec.get("user_id"):
                user = User.from_record(rec)
                by_key[(user.user_id, user.chat_id)] = user
        users = list(by_key.values())
        self._users_cache = (now, users)
        return users

    async def get_active_users(self, chat_id: int) -> list[User]:
        users = await self._run(self._all_users_sync)
        return [u for u in users if u.chat_id == chat_id and u.active]

    async def get_user(self, user_id: int, chat_id: int) -> User | None:
        users = await self._run(self._all_users_sync)
        return next((u for u in users if u.user_id == user_id and u.chat_id == chat_id), None)

    def _upsert_user_sync(self, user: User) -> None:
        ws = self._ws("users")
        rows = with_retry(ws.get_all_values)
        for idx, existing in enumerate(rows[1:], start=2):
            if (
                len(existing) >= 2
                and existing[0] == str(user.user_id)
                and existing[1] == str(user.chat_id)
            ):
                # keep manually edited columns (tz, height, targets) unless the caller set them
                merged = User.from_record(dict(zip(HEADERS["users"], existing, strict=False)))
                merged.chat_id = user.chat_id
                merged.name = user.name
                merged.username = user.username
                merged.active = user.active
                merged.tz = user.tz or merged.tz
                with_retry(ws.update, [merged.to_row()], f"A{idx}")
                break
        else:
            with_retry(ws.append_row, user.to_row(), value_input_option="RAW")
        self._users_cache = None

    async def upsert_user(self, user: User) -> None:
        await self._run(self._upsert_user_sync, user)

    # -- water reminders --------------------------------------------------------------------

    def _all_water_sync(self) -> list[WaterSubscription]:
        # Dedupe by user_id, last row wins - same protection as `_all_users_sync`.
        by_user: dict[int, WaterSubscription] = {}
        for rec in self._records("water"):
            if not rec.get("user_id"):
                continue
            sub = WaterSubscription.from_record(rec)
            if sub is None:
                log.warning("skipping unusable water row for user %r", rec.get("user_id"))
                continue
            by_user[sub.user_id] = sub
        return list(by_user.values())

    async def get_water_subscriptions(self) -> list[WaterSubscription]:
        """Every stored subscription, deduped by user; callers filter on `.active`."""
        return await self._run(self._all_water_sync)

    def _upsert_water_sync(self, sub: WaterSubscription) -> None:
        ws = self._ws("water")
        rows = with_retry(ws.get_all_values)
        for idx, existing in enumerate(rows[1:], start=2):
            if existing and existing[0] == str(sub.user_id):
                with_retry(ws.update, [sub.to_row()], f"A{idx}")
                return
        with_retry(ws.append_row, sub.to_row(), value_input_option="RAW")

    async def upsert_water_subscription(self, sub: WaterSubscription) -> None:
        """Replace the sender's row, or append one if they never subscribed."""
        await self._run(self._upsert_water_sync, sub)

    def _deactivate_water_sync(self, user_id: int) -> bool:
        ws = self._ws("water")
        rows = with_retry(ws.get_all_values)
        col = HEADERS["water"].index("active")
        for idx, existing in enumerate(rows[1:], start=2):
            if (
                len(existing) > col
                and existing[0] == str(user_id)
                and existing[col].strip().upper() in ("TRUE", "1", "YES")
            ):
                with_retry(
                    ws.batch_update,
                    [{"range": gspread.utils.rowcol_to_a1(idx, col + 1), "values": [["FALSE"]]}],
                )
                return True
        return False

    async def deactivate_water_subscription(self, user_id: int) -> bool:
        """Flip `active` to FALSE; False when there was no active row to switch off."""
        return await self._run(self._deactivate_water_sync, user_id)

    # -- appends ----------------------------------------------------------------------------

    def _append(self, tab: str, row: list[Any]) -> None:
        # RAW: strings stay strings (no formula evaluation of names/dishes starting with "=",
        # no locale-dependent date reformatting of the ISO `date`/`ts` columns we filter on);
        # numbers are sent as JSON numbers and stay numeric.
        with_retry(self._ws(tab).append_row, row, value_input_option="RAW")

    async def add_weight(self, user: User, kg: float, when: datetime, source: str) -> None:
        await self._run(
            self._append,
            "weight",
            [
                when.isoformat(timespec="seconds"),
                when.date().isoformat(),
                user.user_id,
                user.name,
                kg,
                source,
            ],
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
        # `photo_file_id` is Telegram's handle for the photo (only this bot can use it); it lets a
        # later free-text correction show the photo to the model again.
        row = [
            when.isoformat(timespec="seconds"),
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
        ]
        await self._run(self._append, "food", row)

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
        row = [
            when.isoformat(timespec="seconds"),
            when.date().isoformat(),
            user.user_id,
            user.name,
            activity,
            minutes,
            _blank(distance_km),
            kcal,
            source,
        ]
        await self._run(self._append, "sport", row)

    async def add_report(self, week_start: date, chat_id: int, text: str, when: datetime) -> None:
        await self._run(
            self._append,
            "reports",
            [when.isoformat(timespec="seconds"), week_start.isoformat(), chat_id, text],
        )

    # -- updates ----------------------------------------------------------------------------

    def _find_food_row_sync(
        self, user_id: int, message_id: int
    ) -> tuple[int, dict[str, str]] | None:
        """Locate the food row that belongs to `user_id` and was announced in `message_id`.

        Telegram message ids are per-chat counters, so `message_id` alone is ambiguous when the
        bot serves several groups; scoping by the owner also stops members from correcting each
        other's entries. Returns the 1-based sheet row number and the row as `{header: cell}`.
        """
        rows = with_retry(self._ws("food").get_all_values)
        col_uid = HEADERS["food"].index("user_id")
        col_msg = HEADERS["food"].index("message_id")
        for idx, row in enumerate(rows[1:], start=2):
            if (
                len(row) > col_msg
                and row[col_msg] == str(message_id)
                and row[col_uid] == str(user_id)
            ):
                return idx, dict(zip(HEADERS["food"], row, strict=False))
        return None

    def _update_food_cells_sync(
        self, user_id: int, message_id: int, values: dict[str, Any]
    ) -> bool:
        found = self._find_food_row_sync(user_id, message_id)
        if found is None:
            return False
        row_no, _ = found
        with_retry(
            self._ws("food").batch_update,
            [
                {
                    "range": gspread.utils.rowcol_to_a1(row_no, HEADERS["food"].index(col) + 1),
                    "values": [[value]],
                }
                for col, value in values.items()
            ],
        )
        return True

    async def get_food_entry(self, user_id: int, message_id: int) -> dict[str, Any] | None:
        """The stored estimate behind a bot food message (numbers as floats), or None."""
        found = await self._run(self._find_food_row_sync, user_id, message_id)
        if found is None:
            return None
        _, row = found
        numeric = (
            "kcal",
            "alcohol_kcal",
            "protein_g",
            "fat_g",
            "carbs_g",
            "veg_share",
            "confidence",
        )
        entry: dict[str, Any] = {k: num(row.get(k)) for k in numeric}
        entry["dish"] = row.get("dish", "")
        entry["source"] = row.get("source", "")
        entry["photo_file_id"] = row.get("photo_file_id", "")
        return entry

    async def update_food_kcal(self, user_id: int, message_id: int, kcal: float) -> bool:
        """Set `kcal` on the food row announced in `message_id`; False if it is not the sender's."""
        return await self._run(
            self._update_food_cells_sync, user_id, message_id, {"kcal": kcal, "corrected": "TRUE"}
        )

    async def update_food_entry(
        self, user_id: int, message_id: int, est: FoodEstimate, new_message_id: int
    ) -> bool:
        """Replace the estimate after a free-text correction and re-key the row to the bot's new
        reply, so the next correction can be made on that reply."""
        values: dict[str, Any] = {
            "dish": est.dish,
            "kcal": est.kcal,
            "alcohol_kcal": est.alcohol_kcal,
            "protein_g": est.protein_g,
            "fat_g": est.fat_g,
            "carbs_g": est.carbs_g,
            "veg_share": est.veg_share,
            "confidence": est.confidence,
            "message_id": new_message_id,
            "corrected": "TRUE",
        }
        return await self._run(self._update_food_cells_sync, user_id, message_id, values)

    # -- queries ----------------------------------------------------------------------------

    async def weights_for_date(self, chat_id: int, day: date) -> dict[int, float]:
        """`{user_id: kg}` for members of `chat_id` who weighed in on `day` (last entry wins)."""
        members = {u.user_id for u in await self.get_active_users(chat_id)}
        rows = await self._run(self._records, "weight")
        result: dict[int, float] = {}
        for r in rows:
            uid = int(num(r.get("user_id")))
            if uid in members and str(r.get("date")) == day.isoformat():
                result[uid] = num(r.get("kg"))
        return result

    async def user_rows_between(
        self, tab: Tab, user_id: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Rows of `tab` for `user_id` with `start <= date <= end`, ordered by `ts`."""
        rows = await self._run(self._records, tab)
        lo, hi = start.isoformat(), end.isoformat()
        picked = [
            r
            for r in rows
            if int(num(r.get("user_id"))) == user_id and lo <= str(r.get("date", "")) <= hi
        ]
        picked.sort(key=lambda r: str(r.get("ts", "")))
        return picked

    async def last_weight(self, user_id: int, before: datetime) -> tuple[date, float] | None:
        """Most recent weight entry strictly before `before`, or None."""
        rows = await self._run(self._records, "weight")
        mine: list[tuple[datetime, dict[str, Any]]] = []
        for r in rows:
            if int(num(r.get("user_id"))) != user_id:
                continue
            ts = _parse_ts(r.get("ts"))
            # compare as datetimes, not strings: offsets differ across DST / per-user tz changes
            if ts is not None and ts < before:
                mine.append((ts, r))
        if not mine:
            return None
        _, last = max(mine, key=lambda item: item[0])
        return date.fromisoformat(str(last["date"])), num(last.get("kg"))


def _parse_ts(value: Any) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None
