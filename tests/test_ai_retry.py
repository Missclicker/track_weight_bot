"""`GeminiClient._generate`: three attempts, escalating server deadlines, retry notice.

The SDK call is replaced by `FakeModels`, so nothing goes over the network; the backoff sleeps
are zeroed with a monkeypatched `_RETRY_DELAYS_S` so the suite stays fast.
"""

from __future__ import annotations

import functools
from typing import Any

import aiohttp
import pytest
from google.genai import errors as genai_errors

from bot import ai


class FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class FakeModels:
    """Answers `generate_content` from a scripted list and records the deadline of every call.

    A list item is either an exception (raised) or a string (returned as the response text).
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.timeouts: list[int | None] = []

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> FakeResponse:
        http_options = config.http_options
        self.timeouts.append(None if http_options is None else http_options.timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    @property
    def calls(self) -> int:
        return len(self.timeouts)


class _Aio:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class _Client:
    def __init__(self, models: FakeModels) -> None:
        self.aio = _Aio(models)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch) -> None:
    monkeypatch.setattr(ai, "_RETRY_DELAYS_S", (0.0, 0.0))


@functools.cache
def _gemini() -> ai.GeminiClient:
    # Building a real `genai.Client` costs ~1 s, and the only state `_generate` touches is the SDK
    # object swapped out below - so one instance serves the whole module.
    return ai.GeminiClient("test-key", "vision-model", "text-model")


def client_with(outcomes: list[Any]) -> tuple[ai.GeminiClient, FakeModels]:
    client = _gemini()
    models = FakeModels(outcomes)
    client._client = _Client(models)  # type: ignore[assignment]
    return client, models


def server_error(code: int = 504) -> genai_errors.APIError:
    return genai_errors.ServerError(code, {"error": {"status": "DEADLINE_EXCEEDED"}})


def client_error(code: int) -> genai_errors.APIError:
    return genai_errors.ClientError(code, {"error": {"status": "INVALID_ARGUMENT"}})


async def test_retries_after_a_504_and_returns_the_second_answer() -> None:
    client, models = client_with([server_error(), "ok"])
    assert await client._generate("m", ["hi"], None) == "ok"
    assert models.calls == 2


async def test_deadlines_escalate_per_attempt() -> None:
    client, models = client_with([server_error(), server_error(), server_error()])
    with pytest.raises(genai_errors.APIError):
        await client._generate("m", ["hi"], None)
    # milliseconds, because that is the unit of `HttpOptions.timeout`
    assert models.timeouts == [45_000, 75_000, 120_000]


def test_outer_wait_has_a_grace_margin_over_every_deadline() -> None:
    # the SDK's own error must surface before the outer `asyncio.wait_for` fires
    assert ai._TIMEOUT_GRACE_S > 0
    assert all(deadline + ai._TIMEOUT_GRACE_S > deadline for deadline in ai._ATTEMPT_TIMEOUTS_S)


async def test_three_transient_failures_raise_the_last_one() -> None:
    last = server_error(503)
    client, models = client_with([server_error(), server_error(429), last])
    with pytest.raises(genai_errors.APIError) as excinfo:
        await client._generate("m", ["hi"], None)
    assert excinfo.value is last
    assert models.calls == 3


async def test_408_is_transient() -> None:
    client, models = client_with([client_error(408), "ok"])
    assert await client._generate("m", ["hi"], None) == "ok"
    assert models.calls == 2


@pytest.mark.parametrize("code", [400, 403, 404])
async def test_a_permanent_client_error_is_not_retried(code: int) -> None:
    client, models = client_with([client_error(code), "never reached"])
    with pytest.raises(genai_errors.APIError):
        await client._generate("m", ["hi"], None)
    assert models.calls == 1


@pytest.mark.parametrize("empty", ["", None])
async def test_an_empty_response_is_retried(empty: str | None) -> None:
    client, models = client_with([empty, "ok"])
    assert await client._generate("m", ["hi"], None) == "ok"
    assert models.calls == 2


async def test_an_aiohttp_transport_error_is_retried() -> None:
    client, models = client_with([aiohttp.ServerDisconnectedError(), "ok"])
    assert await client._generate("m", ["hi"], None) == "ok"
    assert models.calls == 2


async def test_an_httpx_transport_error_is_retried() -> None:
    import httpx

    client, models = client_with([httpx.ConnectError("no route"), "ok"])
    assert await client._generate("m", ["hi"], None) == "ok"
    assert models.calls == 2


async def test_the_notice_fires_once_even_with_two_retries() -> None:
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    client, models = client_with([server_error(), server_error(), "ok"])
    assert await client._generate("m", ["hi"], None, notice) == "ok"
    assert models.calls == 3
    assert calls == [1]


async def test_the_notice_is_silent_when_the_first_attempt_succeeds() -> None:
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    client, _ = client_with(["ok"])
    assert await client._generate("m", ["hi"], None, notice) == "ok"
    assert calls == []


async def test_the_notice_is_silent_on_a_permanent_error() -> None:
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    client, _ = client_with([client_error(400)])
    with pytest.raises(genai_errors.APIError):
        await client._generate("m", ["hi"], None, notice)
    assert calls == []


async def test_a_failing_notice_does_not_abort_the_retry_loop() -> None:
    async def notice() -> None:
        raise RuntimeError("telegram is down")

    client, models = client_with([server_error(), "ok"])
    assert await client._generate("m", ["hi"], None, notice) == "ok"
    assert models.calls == 2


async def test_public_methods_forward_the_notice() -> None:
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    client, models = client_with([server_error(), '{"dish": "борщ", "kcal": 500}'])
    est = await client.estimate_food(None, None, "борщ", on_retry=notice)
    assert est.dish == "борщ"
    assert models.calls == 2
    assert calls == [1]
