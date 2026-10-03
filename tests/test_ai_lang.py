"""The food and sport calls write their text in the reader's language.

The dish, portion and notes come from the model, so the prompt names the language and gives
portion examples written in it; the response schema stays language-neutral, because it goes out
unchanged on every call. The sport title comes from the MET table, not from the model.
"""

from __future__ import annotations

import json

import pytest

from bot import met
from bot.ai import FoodEstimate
from tests.conftest import client_with

_FOOD = json.dumps({"dish": "borscht", "portion": "400 g", "kcal": 650})
_PREVIOUS = {"dish": "борщ", "portion": "400 г", "kcal": 600}


def _prompt(contents: list[object]) -> str:
    """The text part of a call's contents (a photo call also carries the image part)."""
    texts = [part for part in contents if isinstance(part, str)]
    assert len(texts) == 1
    return texts[0]


@pytest.mark.parametrize("photo", [True, False])
async def test_an_english_food_estimate_asks_for_english(photo: bool) -> None:
    client, models = client_with([_FOOD])
    image = b"jpeg" if photo else None
    await client.estimate_food(image, "image/jpeg" if photo else None, "борщ", lang="en")
    prompt = _prompt(models.contents[0])
    assert "dish (short name in English)" in prompt
    assert '"400 g", "2 pcs" or "330 ml"' in prompt
    assert "notes (one short remark in English or empty)" in prompt
    assert "Ukrainian" not in prompt
    assert "400 г" not in prompt


async def test_a_food_estimate_is_ukrainian_by_default() -> None:
    """Old callers pass no `lang` and keep today's prompt."""
    client, models = client_with([_FOOD, _FOOD])
    await client.estimate_food(None, None, "борщ")
    await client.estimate_food(None, None, "борщ", lang="uk")
    assert models.contents[0] == models.contents[1]
    prompt = _prompt(models.contents[0])
    assert "dish (short name in Ukrainian)" in prompt
    assert '"400 г", "2 шт" or "330 мл"' in prompt
    assert "notes (one short remark in Ukrainian or empty)" in prompt
    assert "English" not in prompt


async def test_an_unknown_language_gets_the_ukrainian_prompt() -> None:
    client, models = client_with([_FOOD, _FOOD])
    await client.estimate_food(None, None, "борщ", lang="klingon")
    await client.estimate_food(None, None, "борщ", lang="uk")
    assert models.contents[0] == models.contents[1]


async def test_a_revision_follows_the_language_too() -> None:
    client, models = client_with([_FOOD, _FOOD])
    await client.revise_food(_PREVIOUS, "без хліба", lang="en")
    await client.revise_food(_PREVIOUS, "без хліба")
    english, ukrainian = (_prompt(contents) for contents in models.contents)
    assert "dish (short name in English)" in english
    assert '"400 g", "2 pcs" or "330 ml"' in english
    assert "Ukrainian" not in english
    assert "dish (short name in Ukrainian)" in ukrainian
    assert '"400 г", "2 шт" or "330 мл"' in ukrainian
    # the earlier estimate and the correction still go in as data, braces and all
    assert '"dish": "борщ"' in english and "без хліба" in english


async def test_user_text_with_braces_is_not_formatted() -> None:
    """The prompts are `.format()`-ed; the user's text must be inserted, never parsed."""
    client, models = client_with([_FOOD, _FOOD])
    await client.estimate_food(None, None, "суп {language} {0}", lang="en")
    await client.revise_food(_PREVIOUS, "це {portions}", lang="en")
    assert "суп {language} {0}" in _prompt(models.contents[0])
    assert "це {portions}" in _prompt(models.contents[1])


def test_the_schema_descriptions_name_no_language() -> None:
    schema = FoodEstimate.model_json_schema()
    descriptions = json.dumps(
        {name: field.get("description", "") for name, field in schema["properties"].items()},
        ensure_ascii=False,
    )
    assert "Ukrainian" not in descriptions
    assert "English" not in descriptions
    assert "г" not in descriptions and "шт" not in descriptions


@pytest.mark.parametrize("lang", ["en", "uk"])
async def test_the_sport_title_is_in_the_readers_language(lang: str) -> None:
    client, _ = client_with([json.dumps({"activity": "running", "minutes": 30})])
    entry = await client.parse_sport("біг 30 хв", 80, lang=lang)
    assert entry is not None
    assert entry.title == met.activity_title("running", lang)
    assert entry.title == ("running" if lang == "en" else met.ACTIVITIES["running"].title)


async def test_the_sport_title_is_ukrainian_by_default() -> None:
    client, _ = client_with([json.dumps({"activity": "running", "minutes": 30})])
    entry = await client.parse_sport("біг 30 хв", 80)
    assert entry is not None
    assert entry.title == met.ACTIVITIES["running"].title
