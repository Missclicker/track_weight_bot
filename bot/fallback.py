"""Groq as a stand-in for Gemini while Gemini is out (groq SDK, OpenAI-compatible chat API).

`GeminiClient._generate` hands a call over here only when Gemini has given up on it because the
model is overloaded or its quota is spent (or the model is still inside the cooldown one of those
started). The call arrives in Gemini's shape - the same `contents` list and pydantic schema - and
is translated into one chat completion, so the public methods in `bot/ai.py` never know which
provider answered. The module is deliberately not called `groq.py`: that would shadow the SDK.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from typing import Any

import groq
from google.genai import types
from pydantic import BaseModel

# One attempt, one deadline. By the time a call lands here the user has already waited for Gemini
# to give up (up to two 503s, or nothing at all during a cooldown), so a ladder of our own would
# only stretch the silence; the generous deadline is for a vision model on a busy hour.
_TIMEOUT_S = 60.0

# Reasoning models may put their chain of thought in front of the answer. The request asks the
# known families not to (see `_reasoning_params`); this is the safety net for any other model an
# operator configures, which may still answer "<think>...</think>{...}" and break the JSON parse.
_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)

# Output caps, sent on every request. Groq's free tier counts the *requested* cap, not the tokens
# actually produced, against a model's output-tokens-per-minute limit, and with no cap the server
# reserves the model's own maximum: `qwen/qwen3.8-27b` (1000 OTPM on the free tier) refused a photo
# estimate outright as "Request too large ... Requested 2041". A schema call with an image goes to
# that vision model, so it gets 600, chosen to stay under the 1000. Its hidden reasoning counts
# toward the cap too: should photo fallbacks start failing with `json_validate_failed` or a
# `length` cut-off, this cap is the constraint.
_VISION_STRUCTURED_MAX_TOKENS = 600
# A schema call without an image (a text estimate, a revision, a sport parse) goes to the text
# model, where the reasoning counts toward the cap as well: `include_reasoning: False` only keeps
# the thinking out of the response, it is still generated. At 600 `openai/gpt-oss-120b` spent the
# whole budget thinking and the server refused with 400 `json_validate_failed`, "max completion
# tokens reached before generating a valid document". The text model is not under the vision
# model's 1000 OTPM limit, and the weekly report below already asks it for twice this cap.
_TEXT_STRUCTURED_MAX_TOKENS = 2048
# The weekly report is free text: a group report runs to several thousand characters of Ukrainian,
# which is a few thousand tokens. Should a model's limit refuse this cap, the report job already
# degrades to its numbers-only fallback, so the cap errs on the side of a complete report.
_FREE_TEXT_MAX_TOKENS = 4096

# How much of a rejected answer goes into the error (and so into the WARNING in `bot/ai.py`):
# enough to see what the model did, short enough for one log line.
_SNIPPET_CHARS = 300

# Keywords whose value maps names to subschemas rather than being a schema itself; a property
# literally called "title" or "default" lives in such a map and must not be stripped.
_SCHEMA_MAPS = frozenset({"properties", "$defs", "definitions", "patternProperties"})
# Keywords whose value is data (or a list of names), never a subschema.
_DATA_KEYWORDS = frozenset(
    {"enum", "const", "required", "examples", "description", "discriminator"}
)


def _reasoning_params(model: str) -> dict[str, Any]:
    """The request fields that keep a reasoning model's thinking out of `message.content`.

    The two families need different, mutually exclusive switches, and each rejects the other's:
    GPT-OSS never puts its reasoning into `content` but returns it next to the answer unless
    `include_reasoning` is off (and does not accept `reasoning_format` at all), while Qwen-style
    models inline it as a `<think>` block by default and *must* get `parsed` or `hidden` when the
    request uses JSON mode. GPT-OSS also gets `reasoning_effort: "low"`, on every call: the
    structured answers need no deep thinking, the weekly report is written from precomputed
    numbers, and reasoning kept out of the response is still generated, so at the default
    "medium" it spends the output cap and the user's wait all the same. Qwen takes other values
    for that field on Groq and MiniMax is left at its default too, so neither gets it. Any other
    model gets none of these fields - a parameter it does not know could fail the whole request -
    and relies on `_THINK_BLOCK` instead.
    """
    name = model.lower()
    if "gpt-oss" in name:
        return {"include_reasoning": False, "reasoning_effort": "low"}
    if "qwen" in name or "minimax" in name:
        return {"reasoning_format": "hidden"}
    return {}


def strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """`schema`'s JSON schema in the shape Groq's strict Structured Outputs accepts.

    Strict mode constrains decoding to the schema, which is the point (see `GroqClient.generate`),
    but it only takes a schema where every object lists all its properties in `required` and sets
    `additionalProperties: false`. That is applied to every object in the tree, `$defs` included,
    so a nested model added later cannot quietly go out non-strict. `default` and `title` go
    everywhere (a default means nothing once every key is required, and titles are noise), as does
    the model's top-level `description`; field descriptions, `minimum`/`maximum` and the
    `anyOf: [..., {"type": "null"}]` of an optional field stay - a nullable field is still
    required, the model just answers `null`. It works on a copy, so the dict `model_json_schema()`
    handed over is never changed, whoever else may be holding it.
    """
    result = copy.deepcopy(schema.model_json_schema())
    _make_strict(result)
    result.pop("description", None)
    return result


def _make_strict(node: Any) -> None:
    if isinstance(node, list):
        for item in node:
            _make_strict(item)
        return
    if not isinstance(node, dict):
        return
    node.pop("title", None)
    node.pop("default", None)
    if node.get("type") == "object" or "properties" in node:
        if node.get("additionalProperties") not in (None, False):
            # A free-form mapping (`dict[str, X]`) has no fixed key set strict mode could require;
            # forcing `additionalProperties: false` on it would make every answer an empty object.
            raise ValueError("a free-form mapping cannot be sent as a strict schema")
        node["required"] = list(node.get("properties", {}))
        node["additionalProperties"] = False
    for key, value in node.items():
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            for subschema in value.values():
                _make_strict(subschema)
        elif key not in _DATA_KEYWORDS:
            _make_strict(value)


def _check_complete(text: str, keys: list[str]) -> None:
    """Raise `ValueError` unless `text` is a JSON object carrying every one of `keys`.

    Pydantic alone would accept an answer that lost keys: every missing field with a default comes
    back as that default, so a corrupt answer reads as a meal of zeros. The message names what is
    missing and quotes the start of the answer as sent, newlines escaped so it stays on one log
    line - the corruption it shows is often a stray backslash, which a `repr` would double.
    """
    snippet = text[:_SNIPPET_CHARS].replace("\n", "\\n")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"Groq answer is not JSON ({exc}): {snippet}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Groq answer is not a JSON object: {snippet}")
    missing = [key for key in keys if key not in data]
    if missing:
        raise ValueError(f"Groq answer misses {', '.join(missing)}: {snippet}")


def _has_image(contents: list[Any]) -> bool:
    return any(isinstance(item, types.Part) and item.inline_data is not None for item in contents)


def _to_parts(contents: list[Any]) -> list[dict[str, Any]]:
    """Gemini `contents` -> OpenAI-style content parts, in the original order.

    Only the shapes `bot/ai.py` actually builds are understood: prompt strings and image bytes.
    Anything else raises, which the caller counts as a failed fallback rather than sending Groq a
    request that silently lost part of what Gemini would have seen.
    """
    parts: list[dict[str, Any]] = []
    for item in contents:
        if isinstance(item, str):
            parts.append({"type": "text", "text": item})
        elif isinstance(item, types.Part) and item.inline_data is not None:
            blob = item.inline_data
            if not blob.data or not blob.mime_type:
                raise ValueError("image part without data or mime type")
            encoded = base64.b64encode(blob.data).decode("ascii")
            url = f"data:{blob.mime_type};base64,{encoded}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif isinstance(item, types.Part) and item.text is not None:
            parts.append({"type": "text", "text": item.text})
        elif isinstance(item, types.Part):
            raise ValueError(f"unsupported Gemini part: {item!r}")
        else:
            raise TypeError(f"unsupported contents item: {type(item).__name__}")
    return parts


class GroqClient:
    """Thin async wrapper around `groq.AsyncGroq`, taking calls in `GeminiClient`'s shape."""

    def __init__(self, api_key: str, vision_model: str, text_model: str) -> None:
        # `max_retries=0`: the SDK would otherwise retry 429/5xx on its own with backoff, which is
        # exactly the extra waiting `_TIMEOUT_S` above is meant to rule out. Building the client
        # does no I/O, so tests can construct one and swap `_client` for a fake.
        self._client = groq.AsyncGroq(api_key=api_key, max_retries=0, timeout=_TIMEOUT_S)
        self._vision_model = vision_model
        self._text_model = text_model

    @property
    def vision_model(self) -> str:
        return self._vision_model

    @property
    def text_model(self) -> str:
        return self._text_model

    def model_for(self, contents: list[Any]) -> str:
        """The model a call with these `contents` goes to: an image needs the vision one.

        Decided from the contents rather than from which Gemini model gave up, because both
        Gemini env vars may name the same model and the question is only whether pixels are sent.
        """
        return self._vision_model if _has_image(contents) else self._text_model

    async def generate(
        self,
        contents: list[Any],
        schema: type[BaseModel] | None,
        *,
        system_instruction: str | None = None,
    ) -> str:
        """One chat completion for a Gemini-shaped call; the stripped final answer text.

        Raises whatever the SDK raises, and `ValueError` for an answer cut off at the output cap,
        an empty one, or (for a schema call) one that is not a JSON object with every property of
        the schema - the caller treats every failure the same way, so nothing here needs to tell
        them apart.
        """
        model = self.model_for(contents)
        parts = _to_parts(contents)
        # A plain string for text-only calls: every chat model accepts it, while a list of parts
        # is only guaranteed on the multimodal ones.
        has_image = _has_image(contents)
        content: str | list[dict[str, Any]] = (
            parts if has_image else "\n\n".join(part["text"] for part in parts)
        )
        messages: list[dict[str, Any]] = []
        if system_instruction is not None:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": content})
        # Picked the way `model_for` picks the model: an image means the vision model's cap.
        if schema is None:
            max_tokens = _FREE_TEXT_MAX_TOKENS
        elif has_image:
            max_tokens = _VISION_STRUCTURED_MAX_TOKENS
        else:
            max_tokens = _TEXT_STRUCTURED_MAX_TOKENS
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,  # the same as the Gemini call, so both providers answer alike
            "max_completion_tokens": max_tokens,
            **_reasoning_params(model),
        }
        json_schema: dict[str, Any] | None = None
        if schema is not None:
            # Strict on purpose. In best-effort mode a photo estimate from `qwen/qwen3.8-27b` came
            # back with its key quoting broken (`"fat_g\": 18, ": 50, ...`): sometimes still
            # valid JSON with junk keys, which pydantic ignored before filling every real field
            # with its default - a meal of zeros - and sometimes a loop until the token limit.
            # Strict mode constrains the decoding to the schema instead of hoping the model
            # follows it. The answer is still validated against the pydantic model by the caller.
            json_schema = strict_schema(schema)
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema, "strict": True},
            }
        completion = await self._client.chat.completions.create(**request)
        choice = completion.choices[0]
        if choice.finish_reason == "length":
            # Cut off at the cap: a partial report would read as a finished one, and a partial
            # JSON object is at best a parse error.
            raise ValueError("Groq answer cut off at the output token cap")
        text = _THINK_BLOCK.sub("", choice.message.content or "", count=1).strip()
        if not text:
            raise ValueError("empty Groq response")
        if json_schema is not None:
            # Defence in depth for an operator-configured model that ignores strict mode.
            _check_complete(text, list(json_schema["properties"]))
        return text
