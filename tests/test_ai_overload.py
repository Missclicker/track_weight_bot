"""An overloaded Gemini model: the short-circuited retry ladder and the per-model 503 cooldown.

A 503 ("this model is currently experiencing high demand") is transient, so it still gets one
retry - but not the full escalating ladder, because a longer server deadline cannot help a model
that refuses at once. The SDK call is replaced by `conftest.FakeModels`, so nothing goes over the
network, and `no_ai_backoff` zeroes the sleeps so the suite stays fast.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from google.genai import errors as genai_errors

from bot import ai
from bot.ai import ModelOverloaded
from tests.conftest import client_with, server_error

pytestmark = pytest.mark.usefixtures("no_ai_backoff")


@contextmanager
def caplog_at_warning() -> Iterator[list[str]]:
    """Collect `bot.ai`'s WARNING messages. A plain handler rather than the `caplog` fixture: the
    assertions below are about *which* lines a give-up writes, so nothing else may be captured.
    """
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    ai.log.addHandler(handler)
    try:
        yield records
    finally:
        ai.log.removeHandler(handler)


def _overload() -> genai_errors.APIError:
    """A 503 as the API sends it. The body is never matched on: only `code` is a contract."""
    return server_error(503)


async def test_a_503_is_retried_once_and_the_second_answer_is_used() -> None:
    client, models = client_with([_overload(), "ok"])
    assert await client._generate("m", ["hi"], None) == "ok"
    assert models.calls == 2


async def test_two_503s_give_up_instead_of_climbing_the_whole_ladder() -> None:
    client, models = client_with([_overload(), _overload(), "never reached"])
    with pytest.raises(ModelOverloaded) as excinfo:
        await client._generate("m", ["hi"], None)
    assert models.calls == ai._OVERLOAD_MAX_ATTEMPTS == 2
    assert excinfo.value.model == "m"
    assert excinfo.value.retry_after_s == ai._OVERLOAD_COOLDOWN_S


async def test_a_second_call_during_the_cooldown_never_reaches_the_sdk() -> None:
    client, models = client_with([_overload(), _overload()])
    with pytest.raises(ModelOverloaded):
        await client._generate("m", ["hi"], None)

    with pytest.raises(ModelOverloaded):
        await client._generate("m", ["hi"], None)
    assert models.calls == 2  # the second call spent nothing at all


async def test_the_model_is_used_again_once_the_cooldown_passes() -> None:
    client, models = client_with([_overload(), _overload(), "ok"])
    with pytest.raises(ModelOverloaded):
        await client._generate("m", ["hi"], None)
    client._overloads["m"] = time.monotonic() - 0.01

    assert await client._generate("m", ["hi"], None) == "ok"
    assert client._overloads == {}  # the stale entry is dropped, not kept forever
    assert models.calls == 3


async def test_the_other_model_keeps_working_while_one_is_overloaded() -> None:
    """A spike usually hits one model: a dark vision model must not stop text."""
    client, models = client_with([_overload(), _overload(), "ok"])
    with pytest.raises(ModelOverloaded):
        await client._generate(client.vision_model, ["look"], None)
    assert await client._generate(client.text_model, ["hi"], None) == "ok"
    assert models.calls == 3


async def test_the_retry_notice_never_fires_for_a_503() -> None:
    """The whole call is over in ~2 s, so "AI не відповів" plus the overload reply would be two
    messages for nothing."""
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    client, models = client_with([_overload(), _overload()])
    with pytest.raises(ModelOverloaded):
        await client._generate("m", ["hi"], None, notice)
    assert (models.calls, calls) == (2, [])


async def test_the_notice_already_sent_for_another_failure_is_not_taken_back() -> None:
    """A 504 first, then a 503: the user has been told the answer is late, and that stands."""
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    client, models = client_with([server_error(504), _overload()])
    with pytest.raises(ModelOverloaded):
        await client._generate("m", ["hi"], None, notice)
    assert (models.calls, calls) == (2, [1])


async def test_the_exception_points_at_text_while_only_the_vision_model_is_overloaded() -> None:
    client, _ = client_with([_overload(), _overload()])
    with pytest.raises(ModelOverloaded) as excinfo:
        await client._generate(client.vision_model, ["look"], None)
    assert excinfo.value.vision is True


async def test_one_model_for_both_jobs_drops_the_photo_wording() -> None:
    """With GEMINI_VISION_MODEL == GEMINI_TEXT_MODEL, "describe it in text" cannot help."""
    client, _ = client_with([_overload(), _overload()])
    same, original = client.vision_model, client.text_model  # one client serves the whole suite
    client._text_model = same  # type: ignore[misc]
    try:
        with pytest.raises(ModelOverloaded) as excinfo:
            await client._generate(same, ["look"], None)
        assert excinfo.value.vision is False
    finally:
        client._text_model = original  # type: ignore[misc]


async def test_the_photo_wording_is_dropped_while_the_spike_took_the_text_model_too() -> None:
    """Advice that cannot work is worse than no advice: the same spike often takes both models."""
    client, _ = client_with([_overload(), _overload()])
    client._overloads[client.text_model] = time.monotonic() + ai._OVERLOAD_COOLDOWN_S
    with pytest.raises(ModelOverloaded) as excinfo:
        await client._generate(client.vision_model, ["look"], None)
    assert excinfo.value.vision is False


async def test_the_photo_wording_is_dropped_while_the_text_quota_is_spent() -> None:
    """The other way text can be unusable: its own budget, not the spike."""
    client, _ = client_with([_overload(), _overload()])
    client._cooldowns[client.text_model] = (time.monotonic() + 3600.0, True)
    with pytest.raises(ModelOverloaded) as excinfo:
        await client._generate(client.vision_model, ["look"], None)
    assert excinfo.value.vision is False


async def test_the_give_up_logs_one_warning_of_its_own() -> None:
    """The same treatment a 429 gets: one line naming the cooldown, not that plus a bare
    "attempt 2 failed" the reader then has to join up."""
    client, _ = client_with([_overload(), _overload()])
    with caplog_at_warning() as records, pytest.raises(ModelOverloaded):
        await client._generate("m", ["hi"], None)
    # the SDK's own error repr is appended, so match the part we word ourselves
    assert [r.split(": 503")[0] for r in records] == [
        "Gemini m failed (attempt 1)",
        f"Gemini m is overloaded, cooling down for {ai._OVERLOAD_COOLDOWN_S:.0f} s",
    ]


def test_the_budget_can_never_outgrow_the_ladder() -> None:
    """Both constants are tunables sitting 40 lines apart, and `attempt + 1 >= the budget` is the
    only thing that arms the cooldown: a budget larger than the ladder would mean no attempt ever
    reaches it, and the whole overload path would silently disappear. Pinned as the clamp itself,
    not as a bound that a bare `2` would satisfy today as well.
    """
    budget = min(2, len(ai._ATTEMPT_TIMEOUTS_S))
    assert budget == ai._OVERLOAD_MAX_ATTEMPTS  # ruff SIM300: the constant goes on the right
    assert 1 <= ai._OVERLOAD_MAX_ATTEMPTS <= len(ai._ATTEMPT_TIMEOUTS_S)


async def test_check_overload_is_a_noop_for_a_model_that_never_failed() -> None:
    client, _ = client_with([])
    assert client.check_overload(client.vision_model) is None
    assert client.check_overload("some-other-model") is None


async def test_check_overload_raises_while_the_model_cools() -> None:
    """`photos.on_photo` calls this before downloading anything."""
    client, _ = client_with([_overload(), _overload()])
    with pytest.raises(ModelOverloaded):
        await client._generate(client.vision_model, ["look"], None)

    with pytest.raises(ModelOverloaded) as excinfo:
        client.check_overload(client.vision_model)
    assert excinfo.value.vision is True
    assert 0 < excinfo.value.retry_after_s <= ai._OVERLOAD_COOLDOWN_S
    client.check_overload(client.text_model)  # the other model is untouched


@pytest.mark.parametrize("code", [500, 502, 504])
async def test_another_transient_code_still_gets_the_whole_ladder(code: int) -> None:
    """The fail-fast path is 503-only: a slow or broken backend is still worth three tries with
    growing deadlines, and it must surface as the SDK's own error."""
    client, models = client_with([server_error(code), server_error(code), server_error(code)])
    with pytest.raises(genai_errors.APIError) as excinfo:
        await client._generate("m", ["hi"], None)
    assert not isinstance(excinfo.value, ModelOverloaded)
    assert models.calls == 3
    assert client._overloads == {}


async def test_a_public_method_reports_the_overload() -> None:
    client, models = client_with([_overload(), _overload()])
    with pytest.raises(ModelOverloaded):
        await client.estimate_food(b"jpeg", "image/jpeg", None)
    assert models.calls == 2
