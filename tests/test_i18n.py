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
def test_messages_start_with_their_prefix(module: ModuleType) -> None:
    assert module.SPORT_SAVED.startswith(module.SPORT_PREFIX)
    assert module.SPORT_SAVED_DATE.startswith(module.SPORT_PREFIX)
    assert module.PING.startswith(module.PING_PREFIX)
    assert module.FOOD_INPUT_PROMPT.startswith(module.FOOD_INPUT_PROMPT_PREFIX)
    assert module.SPORT_INPUT_PROMPT.startswith(module.SPORT_INPUT_PROMPT_PREFIX)
    assert module.sport_saved("x", 30, 5, 300).startswith(module.SPORT_PREFIX)
    assert module.sport_saved("x", 30, None, 300, "2026-09-18").startswith(module.SPORT_PREFIX)
    assert module.food_estimate("x", 500, 0, 1, 1, 1, 0.5, "", "").startswith(i18n.FOOD_PREFIX)
    # `/kcal` must never pass for a food estimate, even for somebody whose name starts with it
    items = [("08:00", "≈ x", 100.0)]
    assert not module.kcal_today("≈ A", "2026-09-18", items, None).startswith(i18n.FOOD_PREFIX)
    assert not module.kcal_today("≈ A", "2026-09-18", [], None, yesterday=True).startswith(
        i18n.FOOD_PREFIX
    )


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
        module.today_summary("Alex", "2026-09-18", 2000, 100, 300, 30, 84.2, 1, 2000),
        module.today_summary("Alex", "2026-09-18", 0, 0, 0, 0, None, 0, None),
        module.kcal_today("Alex", "2026-09-18", [("08:00", "toast", 300), (None, "tea", 20)], 2000),
        module.kcal_today("Alex", "2026-09-18", [], None, yesterday=True),
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
    kcal = en.kcal_today(evil, "d", [("08:00", evil, 100)], None)
    assert kcal.count(safe) == 2 and evil not in kcal
