import json

import pytest
from pydantic import ValidationError

from bot import i18n
from bot.ai import FoodEstimate, SportParse

SAMPLE = {
    "dish": " Борщ з хлібом ",
    "kcal": 650,
    "alcohol_kcal": 0,
    "protein_g": 25,
    "fat_g": 20,
    "carbs_g": 70,
    "veg_share": 0.4,
    "confidence": 0.7,
    "notes": "Порція велика",
    "is_food": True,
}


def test_food_estimate_from_json() -> None:
    est = FoodEstimate.model_validate_json(json.dumps(SAMPLE))
    assert est.dish == "Борщ з хлібом"
    assert est.kcal == 650
    assert est.veg_share == 0.4
    assert est.is_food is True


def test_food_estimate_clamps_out_of_range() -> None:
    est = FoodEstimate.model_validate(
        {**SAMPLE, "veg_share": 40, "confidence": -1, "kcal": 50_000, "protein_g": None}
    )
    assert est.veg_share == 1
    assert est.confidence == 0
    assert est.kcal == 10_000
    assert est.protein_g == 0


def test_food_estimate_defaults_for_missing_optional_fields() -> None:
    est = FoodEstimate.model_validate({"dish": "яблуко", "kcal": 80})
    assert est.alcohol_kcal == 0
    assert est.notes == ""
    assert est.confidence == 0.5
    assert est.is_food is True


def test_food_estimate_requires_dish_and_kcal() -> None:
    with pytest.raises(ValidationError):
        FoodEstimate.model_validate({"dish": "щось"})


def test_food_estimate_rejects_garbage_number() -> None:
    with pytest.raises(ValidationError):
        FoodEstimate.model_validate({**SAMPLE, "kcal": "багато"})


def test_food_estimate_reply_text_starts_with_prefix_and_escapes() -> None:
    est = FoodEstimate.model_validate({**SAMPLE, "dish": "Салат <Цезар>", "alcohol_kcal": 120})
    text = i18n.food_estimate(
        est.dish,
        est.kcal,
        est.alcohol_kcal,
        est.protein_g,
        est.fat_g,
        est.carbs_g,
        est.veg_share,
        est.confidence,
        est.notes,
    )
    assert text.startswith(i18n.FOOD_PREFIX)
    assert "&lt;Цезар&gt;" in text
    assert "алкоголь: 120" in text
    assert "середня" in text  # confidence 0.7


def test_sport_parse_schema() -> None:
    parsed = SportParse.model_validate_json(
        '{"activity": "running", "minutes": 30, "distance_km": null}'
    )
    assert parsed.activity == "running"
    assert parsed.minutes == 30
    assert parsed.distance_km is None
