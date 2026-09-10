"""Application settings loaded from environment variables / `.env`.

Every variable is documented in `.env.example` and in the README configuration table.
"""

from __future__ import annotations

import logging
import re
from datetime import time
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> time:
    """Parse `"HH:MM"` into a `datetime.time`; raise `ValueError` on bad input."""
    match = _HHMM.match(value.strip())
    if not match:
        raise ValueError(f"expected HH:MM, got {value!r}")
    return time(int(match.group(1)), int(match.group(2)))


class Settings(BaseSettings):
    """Runtime configuration. Field names map to upper-cased env vars."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    telegram_bot_token: str
    allowed_chat_ids: Annotated[set[int], NoDecode]
    google_sheet_id: str
    google_service_account_file: Path | None = Path("secrets/service_account.json")
    google_service_account_json: str | None = None
    gemini_api_key: str
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_text_model: str = "gemini-2.5-flash-lite"
    default_tz: str = "Europe/Kyiv"
    weigh_in_deadline: str = "11:00"
    weekly_report_day: str = "sun"
    weekly_report_time: str = "20:00"
    weight_min: float = Field(default=40, gt=0)
    weight_max: float = Field(default=200, gt=0)
    log_level: str = "INFO"

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def _parse_chat_ids(cls, value: object) -> set[int]:
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            if not parts:
                raise ValueError("ALLOWED_CHAT_IDS must contain at least one chat id")
            return {int(p) for p in parts}
        if isinstance(value, set | list | tuple):
            return {int(v) for v in value}
        raise ValueError("ALLOWED_CHAT_IDS must be a comma-separated list of integers")

    @field_validator("google_service_account_json", mode="before")
    @classmethod
    def _empty_json_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("weigh_in_deadline", "weekly_report_time")
    @classmethod
    def _valid_hhmm(cls, value: str) -> str:
        parse_hhmm(value)
        return value.strip()

    @field_validator("weekly_report_day")
    @classmethod
    def _valid_weekday(cls, value: str) -> str:
        day = value.strip().lower()[:3]
        if day not in WEEKDAYS:
            raise ValueError(f"WEEKLY_REPORT_DAY must be one of {', '.join(WEEKDAYS)}")
        return day

    @field_validator("default_tz")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in logging.getLevelNamesMapping():
            raise ValueError("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return level

    @model_validator(mode="after")
    def _check_credentials_and_range(self) -> Settings:
        has_json = bool(self.google_service_account_json)
        has_file = self.google_service_account_file is not None and (
            self.google_service_account_file.is_file()
        )
        if not (has_json or has_file):
            raise ValueError(
                "Google credentials missing: set GOOGLE_SERVICE_ACCOUNT_JSON or put the key at "
                f"GOOGLE_SERVICE_ACCOUNT_FILE ({self.google_service_account_file})"
            )
        if self.weight_min >= self.weight_max:
            raise ValueError("WEIGHT_MIN must be smaller than WEIGHT_MAX")
        return self

    @property
    def weigh_in_time(self) -> time:
        return parse_hhmm(self.weigh_in_deadline)

    @property
    def weekly_report_at(self) -> time:
        return parse_hhmm(self.weekly_report_time)

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.default_tz)
