"""Nutrition numbers for the weekly report: age, protein target, BMI, BMR, energy shares.

Computed by the bot for the same reason `met.py` computes sport kcal: the model only interprets
these numbers and must never have to invent one. Every function is pure and takes None for
"unknown"; a result that cannot be computed from what is known is None as well, never a guess.
"""

from __future__ import annotations

from datetime import date

# The 0.8 g/kg RDA is set for weight-stable adults. A calorie deficit - where most of the group
# is - raises the need, because lean mass has to be defended while the body draws on its stores,
# and muscle answers less to the same dose with age (anabolic resistance), which is why guidance
# for older adults sits at 1.0-1.2 g/kg and higher with training or a deficit. Hence 1.2 below
# 40 and, as the group chose, 1.5 from 40. `reference_weight` says which kilograms these multiply.
PROTEIN_AGE_THRESHOLD = 40
PROTEIN_G_PER_KG_UNDER_40 = 1.2
PROTEIN_G_PER_KG_FROM_40 = 1.5
# Sport is logged and its kcal are added on top (`maintenance_kcal`), so the BMR multiplier has to
# cover everyday life only: an "active" factor would count the logged workouts twice.
SEDENTARY_FACTOR = 1.2
# A meal logged at or after this local time counts as late: for people who go to bed around
# 23:00-24:00 it is the last two or three hours before sleep, where evening snacking tends to pile
# up - a signal for the model to look at, not a rule. Compared with `parsing.ts_time`'s "HH:MM",
# which orders correctly as a string.
LATE_MEAL_FROM = "21:00"

_MAX_AGE = 120  # a birth year typed into the sheet can still be a typo; no age beyond this is real


def age_on(birth_year: int | None, day: date) -> int | None:
    """Age in whole years on `day`, from the birth year alone (no birthday is stored)."""
    if birth_year is None:
        return None
    age = day.year - birth_year
    return age if 0 <= age <= _MAX_AGE else None


def protein_g_per_kg(age: int | None) -> float | None:
    """Daily protein target in g per kg of `reference_weight`; None without an age."""
    if age is None:
        return None
    return PROTEIN_G_PER_KG_UNDER_40 if age < PROTEIN_AGE_THRESHOLD else PROTEIN_G_PER_KG_FROM_40


def reference_weight(current_kg: float | None, target_kg: float | None) -> float | None:
    """The kilograms a g/kg target multiplies.

    g/kg of a body weight that carries a lot of fat overshoots the need, since fat tissue needs
    next to no protein; the target weight is the practical proxy for the lean body behind it. A
    target at or above the current weight (someone gaining, or a typo) or one that makes no sense
    leaves the current weight. A zero or negative current weight (a hand-edited cell) is unknown:
    g/kg values are divided by this.
    """
    if current_kg is None or current_kg <= 0:
        return None
    if target_kg is not None and 0 < target_kg < current_kg:
        return target_kg
    return current_kg


def bmi(kg: float | None, height_cm: float | None) -> float | None:
    """Body-mass index, 1 decimal; None without a positive weight and height."""
    if kg is None or height_cm is None or kg <= 0 or height_cm <= 0:
        return None
    return round(kg / (height_cm / 100) ** 2, 1)


def bmr_mifflin(
    kg: float | None, height_cm: float | None, age: int | None, sex: str | None
) -> float | None:
    """Basal metabolic rate, kcal/day, by Mifflin-St Jeor; None when any input is unknown.

    A zero or negative weight or height (a hand-edited cell) is unknown too: the report treats an
    intake below this number as a red flag, so a nonsense BMR must not reach it.
    """
    if kg is None or height_cm is None or age is None or sex not in ("m", "f"):
        return None
    if kg <= 0 or height_cm <= 0:
        return None
    return 10 * kg + 6.25 * height_cm - 5 * age + (5 if sex == "m" else -161)


def maintenance_kcal(bmr: float | None, sport_kcal_per_day: float) -> float | None:
    """Rough daily energy need: sedentary BMR plus the day's share of the logged sport.

    Only an estimate (easily ±15-20 %): BMR formulas are population averages and the sport kcal
    come from a MET table.
    """
    if bmr is None:
        return None
    return bmr * SEDENTARY_FACTOR + sport_kcal_per_day


def energy_shares(
    protein_g: float, fat_g: float, carbs_g: float, alcohol_kcal: float
) -> dict[str, int] | None:
    """Percent of energy from protein, fat, carbs and alcohol (4 / 9 / 4 kcal per g + alcohol).

    The denominator is the sum of those energies, not the logged kcal: the model's kcal and its
    macros do not always agree, and only this way the four shares add up to about 100.
    """
    parts = {
        "protein": 4 * protein_g,
        "fat": 9 * fat_g,
        "carbs": 4 * carbs_g,
        "alcohol": alcohol_kcal,
    }
    total = sum(parts.values())
    if total <= 0:
        return None
    return {key: round(value / total * 100) for key, value in parts.items()}
