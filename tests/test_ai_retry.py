"""`GeminiClient._generate`: three attempts, escalating server deadlines, retry notice.

The SDK call is replaced by `conftest.FakeModels`, so nothing goes over the network; the backoff
sleeps are zeroed by the `no_ai_backoff` fixture so the suite stays fast.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from google.genai import errors as genai_errors

from bot import ai
from tests.conftest import client_error, client_with, server_error

# `bot.ai` guards both imports because they arrive transitively; the tests must agree that they
# are optional rather than failing collection when one is absent.
aiohttp = pytest.importorskip("aiohttp")

pytestmark = pytest.mark.usefixtures("no_ai_backoff")


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


async def test_outer_wait_has_a_grace_margin_over_every_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer `wait_for` must lose the race, or it masks the server's informative 504."""
    waits: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy(aw: Any, timeout: float) -> Any:
        waits.append(timeout)
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(asyncio, "wait_for", spy)
    client, models = client_with([server_error(), server_error(), server_error()])
    with pytest.raises(genai_errors.APIError):
        await client._generate("m", ["hi"], None)
    assert waits == [50.0, 80.0, 125.0]
    # every outer wait is strictly longer than the deadline the SDK itself was given
    assert waits == [t / 1000 + ai._TIMEOUT_GRACE_S for t in models.timeouts]


async def test_three_transient_failures_raise_the_last_one() -> None:
    # No 503 anywhere in the ladder: that code leaves after two attempts with `ModelOverloaded`
    # instead of the raw error (see `test_ai_overload.py`), so it cannot stand in for "transient".
    last = server_error(502)
    client, models = client_with([server_error(), server_error(500), last])
    with pytest.raises(genai_errors.APIError) as excinfo:
        await client._generate("m", ["hi"], None)
    assert excinfo.value is last
    assert models.calls == 3


async def test_a_429_is_not_transient() -> None:
    """A spent quota gets a cooldown instead (see `test_ai_quota.py`); retrying only burns
    another request against the same counter."""
    client, models = client_with([client_error(429), "never reached"])
    with pytest.raises(ai.QuotaExceeded):
        await client._generate("m", ["hi"], None)
    assert models.calls == 1
    assert 429 not in ai._TRANSIENT_CODES


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
    httpx = pytest.importorskip("httpx")

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
