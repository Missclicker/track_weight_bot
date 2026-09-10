from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from bot import i18n
from bot.ai import FoodEstimate
from bot.reports import build_weekly_payload, today_summary
from bot.sheets import User
from tests.conftest import FakeRepo

KYIV = ZoneInfo("Europe/Kyiv")


def _dt(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=KYIV)


def _food(kcal: float, alcohol: float = 0, veg: float = 0.3) -> FoodEstimate:
    return FoodEstimate(
        dish="тест",
        kcal=kcal,
        alcohol_kcal=alcohol,
        protein_g=20,
        fat_g=10,
        carbs_g=50,
        veg_share=veg,
        confidence=0.6,
    )


async def test_today_summary_empty(repo: FakeRepo, user: User) -> None:
    s = await today_summary(repo, user, date(2026, 9, 8))
    assert s.kcal_in == 0
    assert s.weight is None
    assert s.food_entries == 0
    assert s.net_kcal == 0


async def test_today_summary_sums_only_that_day(repo: FakeRepo, user: User) -> None:
    today = date(2026, 9, 8)
    yesterday = date(2026, 9, 7)
    await repo.add_food(user, _food(600, alcohol=150), _dt(today, 9), "photo", 1)
    await repo.add_food(user, _food(400), _dt(today, 13), "text", 2)
    await repo.add_food(user, _food(9000), _dt(yesterday), "photo", 3)
    await repo.add_sport(user, "біг", 30, 5, 390, _dt(today, 7), "text")
    await repo.add_weight(user, 84.5, _dt(today, 8), "text")
    await repo.add_weight(user, 84.9, _dt(yesterday, 8), "text")

    s = await today_summary(repo, user, today)
    assert s.kcal_in == 1000
    assert s.alcohol_kcal == 150
    assert s.sport_kcal == 390
    assert s.sport_minutes == 30
    assert s.net_kcal == 610
    assert s.weight == 84.5
    assert s.food_entries == 2


async def test_today_summary_ignores_other_users(repo: FakeRepo, user: User) -> None:
    other = User(user_id=2, chat_id=-100, name="Інший")
    today = date(2026, 9, 8)
    await repo.add_food(other, _food(700), _dt(today), "photo", 5)
    s = await today_summary(repo, user, today)
    assert s.food_entries == 0


async def test_weekly_payload(repo: FakeRepo, user: User) -> None:
    other = User(user_id=2, chat_id=-100, name="Марія")
    silent = User(user_id=3, chat_id=-100, name="Мовчун")
    stranger = User(user_id=4, chat_id=-200, name="Чужий")
    for u in (user, other, silent, stranger):
        await repo.upsert_user(u)

    week_start = date(2026, 9, 1)
    await repo.add_weight(user, 85.0, _dt(date(2026, 9, 1)), "text")
    await repo.add_weight(user, 84.2, _dt(date(2026, 9, 7)), "text")
    await repo.add_weight(user, 90.0, _dt(date(2026, 9, 8)), "text")  # outside the week
    await repo.add_food(user, _food(800, alcohol=200, veg=0.2), _dt(date(2026, 9, 1)), "photo", 1)
    await repo.add_food(user, _food(1200, veg=0.4), _dt(date(2026, 9, 1), 19), "photo", 2)
    await repo.add_food(user, _food(1000), _dt(date(2026, 9, 3)), "photo", 3)
    await repo.add_sport(user, "біг", 30, 5, 400, _dt(date(2026, 9, 2)), "text")
    await repo.add_sport(user, "зал", 60, None, 350, _dt(date(2026, 9, 4)), "text")
    await repo.add_food(other, _food(500), _dt(date(2026, 9, 5)), "text", 4)
    await repo.add_food(stranger, _food(500), _dt(date(2026, 9, 5)), "text", 6)

    payload = await build_weekly_payload(repo, -100, week_start)

    assert payload["week_start"] == "2026-09-01"
    assert payload["week_end"] == "2026-09-07"
    names = [u["name"] for u in payload["users"]]
    assert names == ["Олексій", "Марія"]  # silent user and other-chat user are excluded

    me = payload["users"][0]
    assert me["kcal_total"] == 3000
    assert me["days_with_food_logged"] == 2
    assert me["kcal_avg_per_day"] == 1500
    assert me["alcohol_kcal"] == 200
    assert me["sport_sessions"] == 2
    assert me["sport_minutes"] == 90
    assert me["sport_kcal"] == 750
    assert me["net_kcal"] == 2250
    assert me["weight_first"] == 85.0
    assert me["weight_last"] == 84.2
    assert me["weight_delta"] == pytest.approx(-0.8)
    assert me["weight_entries"] == 2
    assert me["veg_share_avg"] == pytest.approx(0.3, abs=0.01)
    assert me["daily_kcal_target"] == 2000

    maria = payload["users"][1]
    assert maria["weight_first"] is None
    assert maria["weight_delta"] is None
    assert maria["sport_minutes"] == 0


async def test_weekly_payload_no_users(repo: FakeRepo) -> None:
    payload = await build_weekly_payload(repo, -100, date(2026, 9, 1))
    assert payload["users"] == []


def test_weekly_stats_block_formats_delta() -> None:
    block = i18n.weekly_stats_block(
        {
            "name": "Олексій <b>",
            "kcal_total": 3000,
            "kcal_avg_per_day": 1500,
            "alcohol_kcal": 200,
            "sport_minutes": 90,
            "sport_kcal": 750,
            "weight_first": 85.0,
            "weight_last": 84.2,
            "weight_delta": -0.8,
        }
    )
    assert "Олексій <b>" in block  # sent with parse_mode=None, so not HTML-escaped
    assert "-0.8" in block
    assert "85 -> 84.2" in block
