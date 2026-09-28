from datetime import date

import pytest

from bot import nutrition

# -- age_on --------------------------------------------------------------------------------------


def test_age_is_the_difference_of_years() -> None:
    assert nutrition.age_on(1981, date(2026, 9, 7)) == 45
    # only the year counts: no birthday is stored, so January and December give the same age
    assert nutrition.age_on(1981, date(2026, 1, 1)) == 45


def test_age_of_an_unknown_birth_year_is_none() -> None:
    assert nutrition.age_on(None, date(2026, 9, 7)) is None


@pytest.mark.parametrize(
    ("birth_year", "expected"),
    [(2026, 0), (1906, 120), (2027, None), (1905, None)],
    ids=["born-this-year", "120", "future", "over-120"],
)
def test_age_outside_0_to_120_is_none(birth_year: int, expected: int | None) -> None:
    assert nutrition.age_on(birth_year, date(2026, 9, 7)) == expected


# -- protein_g_per_kg ----------------------------------------------------------------------------


def test_protein_rule_switches_at_40() -> None:
    assert nutrition.protein_g_per_kg(39) == 1.2
    assert nutrition.protein_g_per_kg(40) == 1.5
    assert nutrition.protein_g_per_kg(66) == 1.5
    assert nutrition.protein_g_per_kg(None) is None


# -- reference_weight ----------------------------------------------------------------------------


def test_reference_is_the_target_when_it_is_lower() -> None:
    assert nutrition.reference_weight(95.0, 80.0) == 80.0


@pytest.mark.parametrize("target", [100.0, 95.0, 0.0, -5.0, None], ids=str)
def test_reference_is_the_current_weight_otherwise(target: float | None) -> None:
    """A target above (or at) the current weight, or none that makes sense: the body as it is."""
    assert nutrition.reference_weight(95.0, target) == 95.0


def test_reference_without_a_current_weight_is_none() -> None:
    assert nutrition.reference_weight(None, 80.0) is None
    assert nutrition.reference_weight(None, None) is None
    assert nutrition.reference_weight(0.0, None) is None  # a "0" typed into the sheet


# -- bmi -----------------------------------------------------------------------------------------


def test_bmi() -> None:
    # 84.2 / 1.8^2 = 25.99
    assert nutrition.bmi(84.2, 180) == 26.0
    assert nutrition.bmi(60, 165) == 22.0  # 22.04


@pytest.mark.parametrize(
    ("kg", "height_cm"), [(None, 180), (80, None), (80, 0), (80, -170), (0, 180)], ids=str
)
def test_bmi_needs_a_positive_weight_and_height(kg: float | None, height_cm: float | None) -> None:
    assert nutrition.bmi(kg, height_cm) is None


# -- bmr_mifflin / maintenance_kcal --------------------------------------------------------------


def test_bmr_mifflin_st_jeor() -> None:
    # 10*84 + 6.25*180 - 5*45 = 1740; men +5, women -161
    assert nutrition.bmr_mifflin(84, 180, 45, "m") == pytest.approx(1745)
    assert nutrition.bmr_mifflin(84, 180, 45, "f") == pytest.approx(1579)


@pytest.mark.parametrize(
    ("kg", "height_cm", "age", "sex"),
    [
        (None, 180, 45, "m"),
        (84, None, 45, "m"),
        (84, 180, None, "m"),
        (84, 180, 45, None),
        (84, 180, 45, "x"),
        (0, 180, 45, "m"),
        (84, 0, 45, "m"),
    ],
    ids=["no-weight", "no-height", "no-age", "no-sex", "odd-sex", "zero-weight", "zero-height"],
)
def test_bmr_is_none_when_an_input_is_missing(
    kg: float | None, height_cm: float | None, age: int | None, sex: str | None
) -> None:
    assert nutrition.bmr_mifflin(kg, height_cm, age, sex) is None


def test_maintenance_is_sedentary_bmr_plus_logged_sport() -> None:
    assert nutrition.maintenance_kcal(1745, 0) == pytest.approx(2094)
    assert nutrition.maintenance_kcal(1745, 100) == pytest.approx(2194)
    assert nutrition.maintenance_kcal(None, 100) is None


# -- energy_shares -------------------------------------------------------------------------------


def test_energy_shares_are_of_the_macro_energies() -> None:
    # 4*100 + 9*50 + 4*200 + 150 = 400 + 450 + 800 + 150 = 1800
    shares = nutrition.energy_shares(100, 50, 200, 150)
    assert shares == {"protein": 22, "fat": 25, "carbs": 44, "alcohol": 8}
    assert 98 <= sum(shares.values()) <= 102


def test_energy_shares_without_alcohol() -> None:
    # 4*20 + 9*10 + 4*50 = 80 + 90 + 200 = 370
    assert nutrition.energy_shares(20, 10, 50, 0) == {
        "protein": 22,
        "fat": 24,
        "carbs": 54,
        "alcohol": 0,
    }


def test_energy_shares_of_nothing_are_none() -> None:
    assert nutrition.energy_shares(0, 0, 0, 0) is None
