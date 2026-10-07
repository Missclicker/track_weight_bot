"""The Groq fallback: a call Gemini gives up on (overload or spent quota) goes to Groq once.

Both SDKs are scripted fakes from `conftest` (`FakeModels` for Gemini, `FakeCompletions` for
Groq), so nothing goes over the network, and `no_ai_backoff` zeroes the Gemini sleeps.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import time

import groq
import httpx
import pytest
from google.genai import types
from pydantic import BaseModel, Field

from bot import ai
from bot.ai import FoodEstimate, ModelOverloaded, QuotaExceeded, SportParse
from bot.config import Settings
from bot.fallback import GroqClient, _has_image, _to_parts, strict_schema
from tests.conftest import (
    GroqAnswer,
    client_error,
    client_with,
    client_with_fallback,
    server_error,
)

pytestmark = pytest.mark.usefixtures("no_ai_backoff")

# A complete answer, the only kind strict mode lets through: every key of `FoodEstimate` is there.
FOOD_JSON = json.dumps(
    {
        "dish": "борщ",
        "portion": "400 г",
        "kcal": 420,
        "alcohol_kcal": 0,
        "protein_g": 18,
        "fat_g": 16,
        "carbs_g": 45,
        "veg_share": 0.4,
        "confidence": 0.7,
        "notes": "",
        "is_food": True,
    },
    ensure_ascii=False,
)


def _groq_down() -> groq.APIConnectionError:
    return groq.APIConnectionError(
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    )


async def test_two_503s_hand_the_call_to_groq() -> None:
    client, models, groq_calls = client_with_fallback(
        [server_error(503), server_error(503)], ["groq answer"]
    )
    assert await client._generate(client.text_model, ["hi"], None) == "groq answer"
    assert models.calls == 2
    assert groq_calls.calls == 1
    assert groq_calls.requests[0]["model"] == "groq-text"
    assert groq_calls.requests[0]["temperature"] == 0.2
    assert groq_calls.requests[0]["messages"] == [{"role": "user", "content": "hi"}]


async def test_a_429_hands_the_call_to_groq_after_one_gemini_request() -> None:
    client, models, groq_calls = client_with_fallback([client_error(429)], ["groq answer"])
    assert await client._generate(client.text_model, ["hi"], None) == "groq answer"
    assert (models.calls, groq_calls.calls) == (1, 1)


async def test_during_an_overload_cooldown_gemini_is_not_asked_at_all() -> None:
    client, models, groq_calls = client_with_fallback([], ["groq answer"])
    client._overloads[client.text_model] = time.monotonic() + ai._OVERLOAD_COOLDOWN_S
    assert await client._generate(client.text_model, ["hi"], None) == "groq answer"
    assert (models.calls, groq_calls.calls) == (0, 1)


async def test_during_a_quota_cooldown_gemini_is_not_asked_at_all() -> None:
    client, models, groq_calls = client_with_fallback([], ["groq answer"])
    client._cooldowns[client.text_model] = (time.monotonic() + 3600, True)
    assert await client._generate(client.text_model, ["hi"], None) == "groq answer"
    assert (models.calls, groq_calls.calls) == (0, 1)


async def test_a_photo_goes_to_the_vision_model_as_a_data_url() -> None:
    image = b"\x89PNG\r\n\x1a\nfake"
    client, _, groq_calls = client_with_fallback([client_error(429)], [FOOD_JSON])
    est = await client.estimate_food(image, "image/png", "борщ")
    assert est.dish == "борщ"

    request = groq_calls.requests[0]
    assert request["model"] == "groq-vision"
    [message] = request["messages"]
    assert message["role"] == "user"
    image_part, text_part = message["content"]  # the original order: image, then prompt
    expected = f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}"
    assert image_part == {"type": "image_url", "image_url": {"url": expected}}
    assert text_part["type"] == "text"
    assert "in the photo" in text_part["text"]
    assert "борщ" in text_part["text"]  # the caption block travels with the prompt


async def test_the_system_instruction_becomes_the_first_message() -> None:
    client, _, groq_calls = client_with_fallback(
        [server_error(503), server_error(503)], ["  звіт від Groq \n"]
    )
    assert await client.weekly_report({"users": []}) == "звіт від Groq"
    messages = groq_calls.requests[0]["messages"]
    assert messages[0] == {"role": "system", "content": ai.REPORT_SYSTEM_INSTRUCTION}
    assert messages[1]["role"] == "user"
    assert "DATA:" in messages[1]["content"]
    assert "response_format" not in groq_calls.requests[0]  # free text, no schema


async def test_a_schema_asks_groq_for_strict_json_and_is_parsed() -> None:
    client, _, groq_calls = client_with_fallback([client_error(429)], [FOOD_JSON])
    est = await client.estimate_food(None, None, "борщ")
    assert isinstance(est, FoodEstimate)
    assert (est.dish, est.kcal, est.portion, est.protein_g) == ("борщ", 420, "400 г", 18)

    request = groq_calls.requests[0]
    assert request["model"] == "groq-text"
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "FoodEstimate",
            "schema": strict_schema(FoodEstimate),
            "strict": True,
        },
    }
    # The text model's hidden reasoning counts toward the cap too: 600 ran out before the JSON.
    assert request["max_completion_tokens"] == 2048


async def test_a_photo_schema_call_keeps_the_vision_models_smaller_cap() -> None:
    client, _, groq_calls = client_with_fallback([client_error(429)], [FOOD_JSON])
    await client.estimate_food(b"\xff\xd8\xff\xe0fake", "image/jpeg", None)
    request = groq_calls.requests[0]
    assert request["model"] == "groq-vision"
    assert request["max_completion_tokens"] == 600  # under the vision model's 1000 OTPM


async def test_free_text_gets_the_larger_output_cap() -> None:
    client, _, groq_calls = client_with_fallback([client_error(429)], ["звіт"])
    assert await client.weekly_report({"users": []}) == "звіт"
    assert groq_calls.requests[0]["max_completion_tokens"] == 4096


def _walk(node: object) -> list[dict[str, object]]:
    """Every dict in a JSON schema tree, the tree itself included."""
    if isinstance(node, list):
        return [found for item in node for found in _walk(item)]
    if isinstance(node, dict):
        return [node, *(found for value in node.values() for found in _walk(value))]
    return []


def test_strict_schema_requires_every_food_field_and_keeps_the_bounds() -> None:
    schema = strict_schema(FoodEstimate)
    properties = schema["properties"]
    assert schema["required"] == list(FoodEstimate.model_fields)
    assert schema["additionalProperties"] is False
    assert "description" not in schema  # the model docstring is not sent
    for node in _walk(schema):
        assert "default" not in node
        assert "title" not in node
    assert properties["kcal"]["minimum"] == 0
    assert properties["kcal"]["maximum"] == 10_000
    assert properties["veg_share"]["maximum"] == 1
    assert properties["dish"]["description"] == FoodEstimate.model_fields["dish"].description


def test_strict_schema_keeps_a_nullable_field_and_requires_it() -> None:
    schema = strict_schema(SportParse)
    assert schema["required"] == ["activity", "minutes", "distance_km"]
    assert schema["additionalProperties"] is False
    assert {"type": "null"} in schema["properties"]["minutes"]["anyOf"]
    assert {"type": "null"} in schema["properties"]["distance_km"]["anyOf"]


class _Inner(BaseModel):
    grams: float = Field(default=0, ge=0)
    title: str = ""  # a property named like the keyword it must not be confused with


class _Outer(BaseModel):
    name: str
    inner: _Inner
    extras: list[_Inner] = Field(default_factory=list)


def test_strict_schema_applies_the_rules_inside_defs() -> None:
    schema = strict_schema(_Outer)
    inner = schema["$defs"]["_Inner"]
    assert inner["required"] == ["grams", "title"]
    assert inner["additionalProperties"] is False
    assert "title" in inner["properties"]  # the property survives, only its own "title" goes
    assert "title" not in inner["properties"]["title"]
    assert schema["required"] == ["name", "inner", "extras"]
    for node in _walk(schema):
        assert "default" not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False


def test_strict_schema_does_not_touch_the_dict_it_is_handed(monkeypatch) -> None:
    # pydantic builds a fresh dict per call today; hand out one shared dict to prove the copy
    shared = FoodEstimate.model_json_schema()
    before = copy.deepcopy(shared)
    monkeypatch.setattr(FoodEstimate, "model_json_schema", classmethod(lambda cls: shared))
    strict_schema(FoodEstimate)
    assert shared == before
    assert shared["required"] == ["dish", "kcal"]


class _WithMapping(BaseModel):
    counts: dict[str, int]


def test_a_free_form_mapping_is_refused_rather_than_sent_non_strict() -> None:
    with pytest.raises(ValueError, match="free-form mapping"):
        strict_schema(_WithMapping)


@pytest.mark.parametrize("schema", [FoodEstimate, SportParse])
def test_every_schema_the_bot_sends_converts(schema: type[BaseModel]) -> None:
    # a field Groq strict mode cannot take would silently turn the fallback off for that call
    assert strict_schema(schema)["additionalProperties"] is False


@pytest.mark.parametrize(
    ("model", "params"),
    [
        ("openai/gpt-oss-120b", {"include_reasoning": False, "reasoning_effort": "low"}),
        ("qwen/qwen3.8-27b", {"reasoning_format": "hidden"}),
        ("minimax/minimax-m2", {"reasoning_format": "hidden"}),
    ],
)
async def test_the_default_models_are_asked_to_keep_their_reasoning_out(
    model: str, params: dict[str, object]
) -> None:
    client, _, groq_calls = client_with_fallback([client_error(429)], ["ok"])
    fallback = client._fallback
    assert fallback is not None
    original = fallback._text_model
    fallback._text_model = model
    try:
        await client._generate(client.text_model, ["hi"], None)
    finally:
        fallback._text_model = original
    request = groq_calls.requests[0]
    for key in ("include_reasoning", "reasoning_format", "reasoning_effort"):
        assert request.get(key) == params.get(key)  # and never the other family's switch


async def test_an_unknown_model_gets_no_reasoning_switch() -> None:
    client, _, groq_calls = client_with_fallback([client_error(429)], ["ok"])
    await client._generate(client.text_model, ["hi"], None)  # "groq-text"
    assert "include_reasoning" not in groq_calls.requests[0]
    assert "reasoning_format" not in groq_calls.requests[0]
    assert "reasoning_effort" not in groq_calls.requests[0]


async def test_a_leading_think_block_is_stripped() -> None:
    answer = f"<think>\nthe user wants borscht...\n</think>\n\n{FOOD_JSON}"
    client, _, _ = client_with_fallback([client_error(429)], [answer])
    est = await client.estimate_food(None, None, "борщ")
    assert est.dish == "борщ"


async def test_a_groq_failure_re_raises_the_original_overload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, models, groq_calls = client_with_fallback(
        [server_error(503), server_error(503)], [_groq_down()]
    )
    with caplog.at_level(logging.WARNING, logger="bot.ai"), pytest.raises(ModelOverloaded) as exc:
        await client._generate(client.vision_model, ["look"], None)
    assert exc.value.model == client.vision_model
    assert exc.value.retry_after_s == ai._OVERLOAD_COOLDOWN_S
    assert exc.value.vision is True
    assert getattr(exc.value.__cause__, "code", None) == 503  # Gemini's error, not Groq's
    assert (models.calls, groq_calls.calls) == (2, 1)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Groq" in r.getMessage() and "failed" in r.getMessage() for r in warnings)
    assert all(r.exc_info is None for r in warnings)  # expected condition: no traceback


async def test_a_groq_failure_re_raises_the_original_quota_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _, _ = client_with_fallback([client_error(429)], [_groq_down()])
    with caplog.at_level(logging.WARNING, logger="bot.ai"), pytest.raises(QuotaExceeded) as exc:
        await client._generate(client.text_model, ["hi"], None)
    assert exc.value.model == client.text_model
    assert exc.value.daily is False
    assert exc.value.retry_after_s == ai._QUOTA_COOLDOWN_DEFAULT_S
    assert getattr(exc.value.__cause__, "code", None) == 429
    assert any("fallback failed" in r.getMessage() for r in caplog.records)


async def test_invalid_json_from_groq_counts_as_a_failed_fallback() -> None:
    client, _, groq_calls = client_with_fallback([client_error(429)], ['{"kcal": "a lot"'])
    with pytest.raises(QuotaExceeded):
        await client.estimate_food(None, None, "борщ")
    assert groq_calls.calls == 1


async def test_valid_json_with_corrupt_keys_is_not_a_meal_of_zeros(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The production answer: a `\\"` closed no key, so `fat_g": 18, ` became one junk key.

    It parses, and pydantic would ignore the junk and default every missing field to 0 - so the
    completeness check must refuse it and the user get the outage reply instead.
    """
    corrupt = '{"dish": "Паста", "kcal": 550, "protein_g": 25, "fat_g\\": 18, ": 50}'
    assert set(json.loads(corrupt)) == {"dish", "kcal", "protein_g", 'fat_g": 18, '}
    client, _, groq_calls = client_with_fallback([client_error(429)], [corrupt])
    with caplog.at_level(logging.WARNING, logger="bot.ai"), pytest.raises(QuotaExceeded):
        await client.estimate_food(None, None, "паста")
    assert groq_calls.calls == 1
    [failure] = [r.getMessage() for r in caplog.records if "fallback failed" in r.getMessage()]
    # exactly the missing keys ("fat_g" among them: the junk key does not stand in for it)...
    missing = "portion, alcohol_kcal, fat_g, carbs_g, veg_share, confidence, notes, is_food"
    assert f"misses {missing}:" in failure
    # ...and the start of the raw answer, so the operator sees what came back
    assert '"dish": "Паста", "kcal": 550' in failure
    # ...with its stray backslash shown once, as it was sent, not doubled by a repr
    assert '"fat_g\\": 18' in failure


async def test_a_non_object_answer_to_a_schema_call_counts_as_a_failed_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _, _ = client_with_fallback([client_error(429)], ["[]"])
    with caplog.at_level(logging.WARNING, logger="bot.ai"), pytest.raises(QuotaExceeded):
        await client.estimate_food(None, None, "борщ")
    assert any("not a JSON object" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(("content", "schema"), [(FOOD_JSON, FoodEstimate), ("звіт...", None)])
async def test_an_answer_cut_off_at_the_cap_counts_as_a_failed_fallback(
    content: str, schema: type[BaseModel] | None
) -> None:
    # Even a complete-looking body: `length` means the model wanted to say more.
    client, _, _ = client_with_fallback(
        [server_error(503), server_error(503)], [GroqAnswer(content, finish_reason="length")]
    )
    with pytest.raises(ModelOverloaded):
        await client._generate(client.text_model, ["hi"], schema)


@pytest.mark.parametrize("content", ["", "   \n", None, "<think>only thoughts</think>"])
async def test_an_empty_groq_answer_counts_as_a_failed_fallback(content: str | None) -> None:
    client, _, _ = client_with_fallback([server_error(503), server_error(503)], [content])
    with pytest.raises(ModelOverloaded):
        await client._generate(client.text_model, ["hi"], None)


async def test_contents_groq_cannot_translate_count_as_a_failed_fallback() -> None:
    client, _, groq_calls = client_with_fallback([client_error(429)], ["never reached"])
    with pytest.raises(QuotaExceeded):
        await client._generate(client.text_model, [object()], None)
    assert groq_calls.calls == 0


async def test_a_400_does_not_reach_groq() -> None:
    bad = client_error(400)
    client, models, groq_calls = client_with_fallback([bad], ["never reached"])
    with pytest.raises(type(bad)) as exc:
        await client._generate(client.text_model, ["hi"], None)
    assert exc.value is bad
    assert (models.calls, groq_calls.calls) == (1, 0)


async def test_an_exhausted_504_ladder_does_not_reach_groq() -> None:
    last = server_error(504)
    client, models, groq_calls = client_with_fallback(
        [server_error(504), server_error(504), last], ["never reached"]
    )
    with pytest.raises(type(last)) as exc:
        await client._generate(client.text_model, ["hi"], None)
    assert exc.value is last
    assert (models.calls, groq_calls.calls) == (3, 0)


async def test_the_retry_notice_is_never_passed_on_to_groq() -> None:
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    # a 504 earns Gemini's own notice, the 503 after it ends the ladder and hands over to Groq
    client, models, groq_calls = client_with_fallback(
        [server_error(504), server_error(503)], ["groq answer"]
    )
    assert await client._generate(client.text_model, ["hi"], None, notice) == "groq answer"
    assert calls == [1]  # Gemini's one notice, nothing more on Groq's behalf
    assert (models.calls, groq_calls.calls) == (2, 1)


def test_the_groq_client_never_retries_on_its_own() -> None:
    """The SDK default (two retries with backoff) would stretch a wait the user already paid."""
    sdk = GroqClient("k", "v", "t")._client
    assert sdk.max_retries == 0
    assert sdk.timeout == 60.0


async def test_without_a_fallback_the_outage_is_raised_as_before() -> None:
    client, models = client_with([server_error(503), server_error(503)])
    assert client._fallback is None
    with pytest.raises(ModelOverloaded):
        await client._generate(client.text_model, ["hi"], None)
    assert models.calls == 2


async def test_check_available_raises_during_a_cooldown_without_a_fallback() -> None:
    client, _ = client_with([])
    client._overloads[client.vision_model] = time.monotonic() + ai._OVERLOAD_COOLDOWN_S
    with pytest.raises(ModelOverloaded):
        client.check_available(client.vision_model)

    client._overloads.clear()
    client._cooldowns[client.vision_model] = (time.monotonic() + 60, False)
    with pytest.raises(QuotaExceeded):
        client.check_available(client.vision_model)


async def test_check_available_lets_the_photo_through_with_a_fallback() -> None:
    client, _, _ = client_with_fallback([], [])
    client._overloads[client.vision_model] = time.monotonic() + ai._OVERLOAD_COOLDOWN_S
    client._cooldowns[client.vision_model] = (time.monotonic() + 60, False)
    client.check_available(client.vision_model)  # Groq can still answer, so no raise


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123:abc",
        allowed_chat_ids="-100",
        google_sheet_id="sheet",
        google_service_account_json='{"type": "service_account"}',
        gemini_api_key="key",
        **overrides,
    )


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_groq_key_turns_the_fallback_off(blank: str) -> None:
    assert _settings(groq_api_key=blank).groq_api_key is None


def test_a_groq_key_and_the_default_models_are_kept() -> None:
    settings = _settings(groq_api_key="gsk_test")
    assert settings.groq_api_key == "gsk_test"
    assert settings.groq_vision_model == "qwen/qwen3.8-27b"
    assert settings.groq_text_model == "openai/gpt-oss-120b"


def test_a_text_part_is_translated_as_text() -> None:
    """A text `Part` (not a plain str) is translated as text, so it goes to the text model."""
    contents = [types.Part(text="hello")]
    assert _to_parts(contents) == [{"type": "text", "text": "hello"}]
    assert not _has_image(contents)
