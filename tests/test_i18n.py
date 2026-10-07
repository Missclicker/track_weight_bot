"""The i18n package: Ukrainian/English parity, the prefixes routing relies on, language choice."""

from __future__ import annotations

import ast
import inspect
import re
import string
from types import ModuleType
from typing import Any

import pytest

from bot import i18n
from bot.i18n import en, uk
from bot.parsing import is_target_clear_request, parse_profile, parse_water_schedule

CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _defined_here(module: ModuleType) -> set[str]:
    """Public constants and functions the module itself defines (not the ones it imports)."""
    names = set()
    for node in ast.parse(inspect.getsource(module)).body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name)
    return {name for name in names if not name.startswith("_")}


def _placeholders(text: str) -> set[tuple[str, str]]:
    return {
        (field, spec or "")
        for _, field, spec, _ in string.Formatter().parse(text)
        if field is not None
    }


# -- parity ---------------------------------------------------------------------------------------


def test_both_languages_export_the_same_names() -> None:
    assert set(uk.__all__) == set(en.__all__)
    for module in (uk, en):
        assert set(module.__all__) == _defined_here(module), module.__name__
        assert all(hasattr(module, name) for name in module.__all__)


@pytest.mark.parametrize("name", sorted(uk.__all__))
def test_same_placeholders_and_signatures(name: str) -> None:
    ours, theirs = getattr(uk, name), getattr(en, name)
    assert type(ours) is type(theirs)
    if isinstance(ours, str):
        assert _placeholders(ours) == _placeholders(theirs)
    elif callable(ours):
        assert inspect.signature(ours) == inspect.signature(theirs)


def test_no_language_specific_name_is_reachable_from_the_package_root() -> None:
    # `i18n.HELP` would silently be Ukrainian for everybody: a reader's text goes through t(lang)
    neutral = set(i18n.__all__)
    leaked = [name for name in uk.__all__ if name not in neutral and hasattr(i18n, name)]
    assert leaked == []
    assert i18n.FOOD_PREFIX == "≈"


def test_lang_command_has_all_three_spellings() -> None:
    assert i18n.COMMANDS["lang"] == ("lang", "language", "mova", "мова")


# -- prefixes -------------------------------------------------------------------------------------

PREFIX_NAMES = (
    "SPORT_PREFIX",
    "PING_PREFIX",
    "FOOD_INPUT_PROMPT_PREFIX",
    "SPORT_INPUT_PROMPT_PREFIX",
)


def test_no_prefix_of_one_language_overlaps_the_other() -> None:
    for ours in PREFIX_NAMES:
        for theirs in PREFIX_NAMES:
            a, b = getattr(uk, ours), getattr(en, theirs)
            assert not a.startswith(b) and not b.startswith(a), (ours, theirs)


def test_recognition_tuples_cover_both_languages() -> None:
    assert i18n.SPORT_PREFIXES == (uk.SPORT_PREFIX, en.SPORT_PREFIX)
    assert i18n.PING_PREFIXES == (uk.PING_PREFIX, en.PING_PREFIX)
    assert i18n.FOOD_INPUT_PROMPT_PREFIXES == (
        uk.FOOD_INPUT_PROMPT_PREFIX,
        en.FOOD_INPUT_PROMPT_PREFIX,
    )
    assert i18n.SPORT_INPUT_PROMPT_PREFIXES == (
        uk.SPORT_INPUT_PROMPT_PREFIX,
        en.SPORT_INPUT_PROMPT_PREFIX,
    )


@pytest.mark.parametrize("module", [uk, en], ids=["uk", "en"])
def test_every_sport_confirmation_ends_with_how_to_correct_it(module: ModuleType) -> None:
    plain = module.sport_saved("x", 30, 5, 300).splitlines()
    dated = module.sport_saved("x", 30, None, 300, "2026-09-18").splitlines()
    corrected = module.sport_saved("x", 30, None, 300, "2026-09-18", corrected=True).splitlines()
    hint = plain[-1]
    assert len(plain) == 2 and dated[-1] == hint
    assert corrected == [dated[0], module.CORRECTED_MARK, hint]


@pytest.mark.parametrize("module", [uk, en], ids=["uk", "en"])
def test_messages_start_with_their_prefix(module: ModuleType) -> None:
    assert module.SPORT_SAVED.startswith(module.SPORT_PREFIX)
    assert module.SPORT_SAVED_DATE.startswith(module.SPORT_PREFIX)
    assert module.PING.startswith(module.PING_PREFIX)
    assert module.FOOD_INPUT_PROMPT.startswith(module.FOOD_INPUT_PROMPT_PREFIX)
    assert module.SPORT_INPUT_PROMPT.startswith(module.SPORT_INPUT_PROMPT_PREFIX)
    assert module.sport_saved("x", 30, 5, 300).startswith(module.SPORT_PREFIX)
    assert module.sport_saved("x", 30, None, 300, "2026-09-18").startswith(module.SPORT_PREFIX)
    assert module.sport_saved("x", 30, 5, 300, corrected=True).startswith(module.SPORT_PREFIX)
    assert module.food_estimate("x", 500, 0, 1, 1, 1, 0.5, "", "").startswith(i18n.FOOD_PREFIX)
    # `/kcal` must never pass for a food estimate, even for somebody whose name starts with it
    items = [("08:00", "≈ x", 100.0)]
    assert not module.kcal_today("≈ A", "2026-09-18", items, None).startswith(i18n.FOOD_PREFIX)
    assert not module.kcal_today("≈ A", "2026-09-18", [], None, yesterday=True).startswith(
        i18n.FOOD_PREFIX
    )
    # nor for a sport confirmation: a reply "видали" to that deletes a sport row. The sport block
    # is never the first line, not even on a day with nothing but sport
    sport = [("18:00", "x", 30.0, None, 300.0)]
    for name in ("A", *i18n.SPORT_PREFIXES):
        for food in ([], items):
            text = module.kcal_today(name, "2026-09-18", food, 2000, sport)
            assert not text.startswith(i18n.SPORT_PREFIXES), text
            assert not text.startswith(i18n.FOOD_PREFIX), text


# -- choosing a language --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("uk", "uk"),
        ("UA", "uk"),
        (" ukr ", "uk"),
        ("Ukrainian.", "uk"),
        ("укр", "uk"),
        ("Українська", "uk"),
        ("українською", "uk"),
        ("en", "en"),
        ("EN", "en"),
        ("eng", "en"),
        (" English. ", "en"),
        ("англ", "en"),
        ("англійська", "en"),
        ("англійською", "en"),
        ("", None),
        ("fr", None),
        ("english please", None),
        (None, None),
        (1, None),
    ],
)
def test_normalize_lang(value: object, expected: str | None) -> None:
    assert i18n.normalize_lang(value) == expected


def test_t_picks_the_module() -> None:
    assert i18n.t("en") is en
    assert i18n.t("English") is en
    assert i18n.t("uk") is uk
    assert i18n.t(None) is uk  # unset: the default
    assert i18n.t("fr") is uk
    assert i18n.DEFAULT_LANG == "uk" and i18n.LANGS == ("uk", "en")
    assert "I log food" in i18n.t("en").HELP
    assert i18n.t(None).HELP == uk.HELP


@pytest.mark.parametrize(
    ("langs", "expected"),
    [
        (["uk", "en"], "en"),  # a tie goes to English
        (["uk", "uk", "en"], "uk"),
        (["en", "en", "uk"], "en"),
        ([None, None, "en"], "uk"),  # an unset language counts as Ukrainian
        ([None, "en"], "en"),
        (["", "garbage", "en", "en"], "en"),
        (["uk"], "uk"),
        ([None], "uk"),
        ([], "en"),
    ],
)
def test_majority_lang(langs: list[Any], expected: str) -> None:
    assert i18n.majority_lang(langs) == expected
    assert i18n.majority_lang(iter(langs)) == expected  # any iterable, consumed once


# -- the English text -----------------------------------------------------------------------------

SPORT: list[Any] = [("18:00", "running", 30.0, 5.0, 320.0), (None, "gym", 60.0, None, 250.0)]

WEEK_USER: dict[str, Any] = {
    "name": "Alex",
    "kcal_total": 14000,
    "kcal_avg_per_day": 2000,
    "days_with_food_logged": 7,
    "protein_g_avg_per_day": 90,
    "protein_target_g_per_day": 120,
    "alcohol_kcal": 300,
    "sport_minutes": 120,
    "sport_kcal": 800,
    "weight_first": 85.0,
    "weight_last": 84.2,
    "weight_delta": -0.8,
}


def _rendered(module: ModuleType) -> list[str]:
    """Every helper's output for a representative set of arguments."""
    texts = [module.quota_notice(v, d) for v in (True, False) for d in (True, False)]
    texts += [module.busy_notice(True), module.busy_notice(False)]
    for spec in (
        "every 30 min",
        "weekdays from 9 to 18 every hour",
        "weekend every 2 hours",
        "mon, wed, fri every 45 min",
    ):
        schedule = parse_water_schedule(spec)
        assert schedule is not None
        texts.append(module.fmt_water_schedule(schedule))
    texts += [
        module.fmt_profile(1981, "m", 180, 2026),
        module.fmt_profile(None, "f", 165.5, 2026),
        module.day_total(1130, 2000),
        module.day_total(1130, None, "2026-09-18"),
        module.food_estimate("pizza", 900, 100, 30, 40, 90, 0.1, "note", "400 g", True, "Total"),
        module.sport_saved("running", 30, 5, 390),
        module.sport_saved("running", 30, None, 390, "2026-09-18"),
        module.sport_saved("running", 45, 5, 585, "2026-09-18", corrected=True),
        module.today_summary("Alex", "2026-09-18", 2000, 100, 300, 30, 84.2, 1, 2000),
        module.today_summary("Alex", "2026-09-18", 0, 0, 0, 0, None, 0, None),
        module.kcal_today("Alex", "2026-09-18", [("08:00", "toast", 300), (None, "tea", 20)], 2000),
        module.kcal_today("Alex", "2026-09-18", [], None, yesterday=True),
        module.kcal_today("Alex", "2026-09-18", [("08:00", "toast", 300)], 2000, SPORT),
        module.kcal_today("Alex", "2026-09-18", [("08:00", "toast", 300)], None, SPORT),
        module.kcal_today("Alex", "2026-09-18", [], 2000, SPORT),
        module.today_summary("Alex", "2026-09-18", 2000, 0, 300, 30, None, 1, None),
        module.day_total(1130, 2000, None, 320),
        module.budget_line(2500, 0, 2000) or "",
        module.weekly_stats_block(WEEK_USER),
        module.weekly_stats_block({**WEEK_USER, "days_with_food_logged": 1}),
    ]
    return texts


def test_english_has_no_cyrillic() -> None:
    # the way back to Ukrainian is deliberately spelled in Ukrainian too, and nothing else is
    allowed = {"HELP", "START_REGISTERED", "LANG_CURRENT", "LANG_USAGE"}
    names = [name for name in en.__all__ if isinstance(getattr(en, name), str)]
    for text in [getattr(en, name) for name in names if name not in allowed] + _rendered(en):
        assert not CYRILLIC.search(text), text
    for name in allowed:
        words = set(re.findall(r"[Ѐ-ӿ]+", getattr(en, name)))
        assert words <= {"перейти", "на", "українську", "мова", "українська"}, (name, words)


def test_the_way_back_to_ukrainian_is_readable_in_ukrainian() -> None:
    assert "перейти на українську" in en.LANG_CURRENT
    assert "українську" in en.HELP


@pytest.mark.parametrize(
    "text",
    [
        "weekdays from 9 to 18 every 30 min",
        "daily from 8:30 to 22:00 every hour",
        "mon, wed, fri 10-19 every 2 hours",
    ],
)
def test_english_water_examples_parse(text: str) -> None:
    assert text in en.WATER_USAGE + en.HELP + en.WATER_DM_READY
    assert parse_water_schedule(text) is not None


@pytest.mark.parametrize("text", ["1981 m 180", "45 female 165.5 cm", "182", "45 f 165"])
def test_english_profile_examples_parse(text: str) -> None:
    assert f"/profile {text}" in en.PROFILE_USAGE + en.PROFILE_CURRENT + en.PROFILE_NONE + en.HELP
    assert parse_profile(text, 2026) is not None


def test_english_stop_examples_clear() -> None:
    assert "/target stop" in en.TARGET_USAGE and "/profile stop" in en.PROFILE_USAGE
    assert is_target_clear_request("stop")


def test_english_helpers_read_naturally() -> None:
    schedule = parse_water_schedule("weekdays from 9 to 18 every 30 min")
    assert schedule is not None
    assert en.fmt_water_schedule(schedule) == "weekdays, 09:00-18:00, every 30 min"
    assert en.fmt_profile(1981, "m", 180, 2026) == "born 1981 (age 45), sex male, height 180 cm"
    assert en.day_total(1130, 2000) == "Total for today: 1130 kcal (target 2000)."
    one_day = en.weekly_stats_block({**WEEK_USER, "days_with_food_logged": 1})
    assert "over 1 logged day)" in one_day
    assert "over 7 logged days)" in en.weekly_stats_block(WEEK_USER)
    assert "(1 entry)" in en.today_summary("A", "d", 100, 0, 0, 0, None, 1, None)


def test_ukrainian_help_mentions_the_language_switch() -> None:
    assert "/lang en" in uk.HELP and "/мова en" in uk.HELP
    assert "/lang uk" in en.HELP
    assert "/lang en" in uk.LANG_CURRENT and "/lang uk" in en.LANG_CURRENT
    assert (uk.LANG_NAME, en.LANG_NAME) == ("українська", "English")


def test_english_helpers_escape_user_and_model_text() -> None:
    evil = "<b>&"
    safe = "&lt;b&gt;&amp;"
    estimate = en.food_estimate(evil, 500, 0, 1, 1, 1, 0.5, evil, evil)
    assert evil not in estimate and estimate.count(safe) == 3
    assert safe in en.sport_saved(evil, 30, None, 300)
    assert evil not in en.sport_saved(evil, 30, None, 300)
    summary = en.today_summary(evil, "d", 100, 0, 0, 0, None, 1, None)
    assert safe in summary and evil not in summary
    kcal = en.kcal_today(evil, "d", [("08:00", evil, 100)], None, [("18:00", evil, 30, 5, 300)])
    assert kcal.count(safe) == 3 and evil not in kcal


@pytest.mark.parametrize("module", [uk, en], ids=["uk", "en"])
def test_kcal_today_escapes_activity_names(module: ModuleType) -> None:
    evil = "<b>&"
    kcal = module.kcal_today("A", "d", [], None, [("18:00", evil, 30, None, 300)])
    assert "&lt;b&gt;&amp;" in kcal and evil not in kcal


# -- the day's budget: target + sport -------------------------------------------------------------


def test_kcal_today_lists_sport_and_credits_it_to_the_target() -> None:
    food = [("08:10", "вівсянка", 390.0), ("13:30", "борщ", 650.0)]
    sport = [("18:00", "біг", 30.0, 5.0, 320.0)]
    assert uk.kcal_today("Олег", "2026-10-07", food, 2000, sport) == (
        "Їжа за сьогодні, Олег (2026-10-07):\n"
        "08:10 - 390 ккал - вівсянка\n"
        "13:30 - 650 ккал - борщ\n"
        "Разом: 1040 ккал.\n"
        "Спорт:\n"
        "18:00 - біг, 5 км, 30 хв - 320 ккал\n"
        "Ціль: 2000 + 320 за спорт = 2320 ккал, лишилось 1280."
    )
    food_en = [("08:10", "oatmeal", 390.0), ("13:30", "borscht", 650.0)]
    sport_en = [("18:00", "running", 30.0, 5.0, 320.0)]
    assert en.kcal_today("Oleh", "2026-10-07", food_en, 2000, sport_en) == (
        "Food today, Oleh (2026-10-07):\n"
        "08:10 - 390 kcal - oatmeal\n"
        "13:30 - 650 kcal - borscht\n"
        "Total: 1040 kcal.\n"
        "Sport:\n"
        "18:00 - running, 5 km, 30 min - 320 kcal\n"
        "Target: 2000 + 320 for sport = 2320 kcal, 1280 left."
    )


def test_kcal_today_sport_line_without_distance_or_time() -> None:
    text = uk.kcal_today("A", "d", [], None, [(None, "зал", 60.0, None, 250.4)])
    assert text.splitlines()[-1] == f"{uk.KCAL_NO_TIME} - зал, 60 хв - 250 ккал"


@pytest.mark.parametrize("module", [uk, en], ids=["uk", "en"])
def test_kcal_today_totals_are_sums_of_the_printed_lines(module: ModuleType) -> None:
    # 160.4 + 160.4 = 320.8 would print as 321 under two lines reading 160
    food = [("08:00", "a", 100.4), ("09:00", "b", 100.4)]
    sport = [("18:00", "x", 30.0, None, 160.4), ("19:00", "y", 30.0, None, 160.4)]
    lines = module.kcal_today("A", "d", food, 2000, sport).splitlines()
    assert "200" in lines[3]
    assert "2000 + 320" in lines[-1] and "2320" in lines[-1] and "2120" in lines[-1]


@pytest.mark.parametrize("module", [uk, en], ids=["uk", "en"])
def test_today_summary_shows_the_sport_it_credits(module: ModuleType) -> None:
    # a hand-edited sport row with a blank minutes cell still raises the target, so it is shown
    lines = module.today_summary("A", "d", 1000, 0, 300, 0, None, 1, 2000).splitlines()
    assert "300" in lines[2] and "+ 300" in lines[3]


def test_kcal_today_variants() -> None:
    food = [("08:00", "x", 1040.0)]
    sport = [("18:00", "біг", 30.0, None, 320.0)]
    # no target, sport: the food total stays as listed, the net is a line of its own
    no_target = uk.kcal_today("A", "d", food, None, sport)
    assert "Разом: 1040 ккал.\n" in no_target
    assert no_target.endswith("\nЗ урахуванням спорту: 720 ккал.")
    # a target, no sport: the target moved off the total onto its own line
    assert uk.kcal_today("A", "d", food, 2000).endswith(
        "\nРазом: 1040 ккал.\nЦіль: 2000 ккал, лишилось 960."
    )
    # over the budget, and exactly on it
    assert uk.kcal_today("A", "d", [("08:00", "x", 2470.0)], 2000, sport).endswith(
        "Ціль: 2000 + 320 за спорт = 2320 ккал, понад ціль на 150."
    )
    assert uk.kcal_today("A", "d", [("08:00", "x", 2000.0)], 2000).endswith(
        "Ціль: 2000 ккал, лишилось 0."
    )
    # sport only: the empty-day line, the sport block, the whole budget left
    sport_only = uk.kcal_today("A", "d", [], 2000, sport, yesterday=True)
    assert sport_only.splitlines()[1:] == [
        uk.KCAL_NO_DATA_YESTERDAY,
        "Спорт:",
        "18:00 - біг, 30 хв - 320 ккал",
        "Ціль: 2000 + 320 за спорт = 2320 ккал, лишилось 2320.",
    ]
    # ... and without a target there is nothing to net against
    no_food = uk.kcal_today("A", "d", [], None, sport)
    assert no_food.splitlines()[-1] == "18:00 - біг, 30 хв - 320 ккал"
    # nothing at all: unchanged, even with a target
    assert uk.kcal_today("A", "d", [], 2000) == (
        uk.KCAL_HEADER.format(name="A", date="d") + "\n" + uk.KCAL_NO_DATA
    )


@pytest.mark.parametrize("target", [None, 0, -500, float("nan"), float("inf")])
def test_a_broken_target_is_no_target(target: float | None) -> None:
    food = [("08:00", "x", 1040.0)]
    sport = [("18:00", "біг", 30.0, None, 320.0)]
    for module in (uk, en):
        for text in (
            module.kcal_today("A", "d", food, target, sport),
            module.kcal_today("A", "d", food, target),
            module.today_summary("A", "d", 1040, 0, 320, 30, None, 1, target),
            module.day_total(1040, target, None, 320),
        ):
            assert "nan" not in text and "inf" not in text
            assert "ціль" not in text.lower() and "target" not in text.lower()
    assert uk.kcal_today("A", "d", food, target).endswith("Разом: 1040 ккал.")


@pytest.mark.parametrize(
    ("food", "sport", "target", "uk_text", "en_text"),
    [
        (
            1040,
            320,
            2000,
            "Ціль: 2000 + 320 за спорт = 2320 ккал, лишилось 1280",
            "Target: 2000 + 320 for sport = 2320 kcal, 1280 left",
        ),
        (1040, 0, 2000, "Ціль: 2000 ккал, лишилось 960", "Target: 2000 kcal, 960 left"),
        (
            2470,
            320,
            2000,
            "Ціль: 2000 + 320 за спорт = 2320 ккал, понад ціль на 150",
            "Target: 2000 + 320 for sport = 2320 kcal, 150 over target",
        ),
        (2000, 0, 2000, "Ціль: 2000 ккал, лишилось 0", "Target: 2000 kcal, 0 left"),
        (1040, 320, None, "З урахуванням спорту: 720 ккал", "Net of sport: 720 kcal"),
        (0, 320, None, None, None),
        (1040, 0, None, None, None),
        (1040, 0.4, None, None, None),  # rounds to no sport at all
        # every figure is rounded as printed before the sums: 1000 + 300 = 1300, 1300 - 700 = 600
        (
            700.4,
            299.6,
            1000.4,
            "Ціль: 1000 + 300 за спорт = 1300 ккал, лишилось 600",
            "Target: 1000 + 300 for sport = 1300 kcal, 600 left",
        ),
        (1300.6, 0, 1300.4, "Ціль: 1300 ккал, понад ціль на 1", "Target: 1300 kcal, 1 over target"),
    ],
)
def test_budget_line(
    food: float, sport: float, target: float | None, uk_text: str | None, en_text: str | None
) -> None:
    assert uk.budget_line(food, sport, target) == uk_text
    assert en.budget_line(food, sport, target) == en_text


def test_today_summary_credits_sport_to_the_target() -> None:
    text = uk.today_summary("A", "d", 1040, 0, 320, 30, 84.2, 2, 2000)
    assert text.splitlines()[1:] == [
        "Їжа: 1040 ккал (2 записів)",
        "Спорт: 30 хв, 320 ккал",
        "Ціль: 2000 + 320 за спорт = 2320 ккал, лишилось 1280",
        "Вага: 84.2 кг",
    ]
    en_text = en.today_summary("A", "d", 1040, 0, 320, 30, None, 2, None)
    assert en_text.splitlines()[1:] == [
        "Food: 1040 kcal (2 entries)",
        "Sport: 30 min, 320 kcal",
        "Net of sport: 720 kcal",
    ]
    # without target or sport the food line is the total: no second line repeats it
    assert uk.today_summary("A", "d", 1040, 0, 0, 0, None, 2, None).splitlines()[1:] == [
        "Їжа: 1040 ккал (2 записів)"
    ]
    # nothing logged: unchanged
    assert uk.today_summary("A", "d", 0, 0, 0, 0, None, 0, 2000).splitlines()[1:] == [
        uk.TODAY_NO_DATA
    ]


def test_day_total_names_the_sport_in_the_target() -> None:
    assert uk.day_total(1130, 2000, None, 320) == (
        "Разом за сьогодні: 1130 ккал (ціль 2000 + 320 за спорт)."
    )
    assert en.day_total(1130, 2000, "2026-10-06", 320) == (
        "Total for 2026-10-06: 1130 kcal (target 2000 + 320 for sport)."
    )
    # no sport, or no target: exactly as before
    assert uk.day_total(1130, 2000, None, 0) == uk.day_total(1130, 2000)
    assert uk.day_total(1130, None, None, 320) == "Разом за сьогодні: 1130 ккал."
