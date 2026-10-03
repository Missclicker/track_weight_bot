"""Groq as a stand-in for Gemini while Gemini is out (groq SDK, OpenAI-compatible chat API).

`GeminiClient._generate` hands a call over here only when Gemini has given up on it because the
model is overloaded or its quota is spent (or the model is still inside the cooldown one of those
started). The call arrives in Gemini's shape - the same `contents` list and pydantic schema - and
is translated into one chat completion, so the public methods in `bot/ai.py` never know which
provider answered. The module is deliberately not called `groq.py`: that would shadow the SDK.
"""

from __future__ import annotations

import base64
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


def _reasoning_params(model: str) -> dict[str, Any]:
    """The request fields that keep a reasoning model's thinking out of `message.content`.

    The two families need different, mutually exclusive switches, and each rejects the other's:
    GPT-OSS never puts its reasoning into `content` but returns it next to the answer unless
    `include_reasoning` is off (and does not accept `reasoning_format` at all), while Qwen-style
    models inline it as a `<think>` block by default and *must* get `parsed` or `hidden` when the
    request uses JSON mode. Any other model gets neither field - a parameter it does not know
    could fail the whole request - and relies on `_THINK_BLOCK` instead.
    """
    name = model.lower()
    if "gpt-oss" in name:
        return {"include_reasoning": False}
    if "qwen" in name or "minimax" in name:
        return {"reasoning_format": "hidden"}
    return {}


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

        Raises whatever the SDK raises, and `ValueError` for an empty answer - the caller treats
        every failure the same way, so nothing here needs to tell them apart.
        """
        model = self.model_for(contents)
        parts = _to_parts(contents)
        # A plain string for text-only calls: every chat model accepts it, while a list of parts
        # is only guaranteed on the multimodal ones.
        content: str | list[dict[str, Any]] = (
            parts if _has_image(contents) else "\n\n".join(part["text"] for part in parts)
        )
        messages: list[dict[str, Any]] = []
        if system_instruction is not None:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": content})
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,  # the same as the Gemini call, so both providers answer alike
            **_reasoning_params(model),
        }
        if schema is not None:
            # Non-strict on purpose: strict mode demands that every property be required, and our
            # schemas give most fields a default so one missing number does not lose a meal. The
            # answer is validated against the pydantic model by the caller either way.
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": False,
                },
            }
        completion = await self._client.chat.completions.create(**request)
        text = _THINK_BLOCK.sub("", completion.choices[0].message.content or "", count=1).strip()
        if not text:
            raise ValueError("empty Groq response")
        return text
