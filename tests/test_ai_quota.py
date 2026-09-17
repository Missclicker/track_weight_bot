"""A spent Gemini quota: the pure `quota_cooldown` parser and the per-model cooldown register.

The 429 bodies below are trimmed copies of what the free tier actually answers. The SDK call is
replaced by `conftest.FakeModels`, so nothing goes over the network.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from bot import ai
from bot.ai import QuotaExceeded, quota_cooldown
from tests.conftest import client_error, client_with

pytestmark = pytest.mark.usefixtures("no_ai_backoff")

PACIFIC = ZoneInfo("America/Los_Angeles")
_PER_DAY_ID = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
_PER_MINUTE_ID = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def _body(*details: dict[str, Any]) -> dict[str, Any]:
    return {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "You exceeded your current quota.",
            "details": list(details),
        }
    }


def _quota_failure(quota_id: str) -> dict[str, Any]:
    return {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [
            {
                "quotaMetric": (
                    "generativelanguage.googleapis.com/generate_content_free_tier_requests"
                ),
                "quotaId": quota_id,
                "quotaDimensions": {"model": "gemini-2.5-flash"},
                "quotaValue": "250",
            }
        ],
    }


def _retry_info(delay: Any) -> dict[str, Any]:
    return {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}


def _daily_429() -> Any:
    return client_error(429, _body(_quota_failure(_PER_DAY_ID), _retry_info("28s")))


def _minute_429() -> Any:
    return client_error(429, _body(_quota_failure(_PER_MINUTE_ID), _retry_info("45s")))


# --- quota_cooldown ------------------------------------------------------------------------


def test_a_short_retry_delay_is_raised_to_the_floor() -> None:
    assert quota_cooldown(_body(_retry_info("25s"))) == (ai._QUOTA_COOLDOWN_MIN_S, False)


def test_a_long_retry_delay_is_used_as_is() -> None:
    assert quota_cooldown(_body(_retry_info("90s"))) == (90.0, False)


@pytest.mark.parametrize("delay", ["1.5s", "1.5", 1.5, 90, "90"])
def test_a_retry_delay_may_be_fractional_or_bare(delay: Any) -> None:
    seconds, daily = quota_cooldown(_body(_retry_info(delay)))
    assert daily is False
    assert seconds == max(float(str(delay).removesuffix("s")), ai._QUOTA_COOLDOWN_MIN_S)


@pytest.mark.parametrize(
    "details",
    [
        None,
        "boom",
        {},
        [],
        [{"error": "nope"}],
        {"error": None},
        {"error": {"details": "not a list"}},
        {"error": {"details": [None, 42, {"@type": "unknown"}]}},
        _body(_retry_info("nonsense")),
        _body(_retry_info(None)),
        _body({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": "?"}),
        _body({"@type": "type.googleapis.com/google.rpc.Help", "links": []}),
    ],
)
def test_an_unparseable_body_falls_back_to_the_default(details: Any) -> None:
    assert quota_cooldown(details) == (ai._QUOTA_COOLDOWN_DEFAULT_S, False)


def test_a_per_day_quota_waits_for_the_pacific_midnight() -> None:
    now = datetime(2026, 9, 17, 21, 30, tzinfo=PACIFIC)
    assert quota_cooldown(_body(_quota_failure(_PER_DAY_ID)), now) == (2.5 * 3600, True)


def test_a_per_day_quota_beats_a_short_retry_delay() -> None:
    now = datetime(2026, 9, 17, 12, 0, tzinfo=PACIFIC)
    # Google sends the per-minute `retryDelay` next to a per-day violation; waking up in 28 s
    # would only burn another request against a counter that resets at midnight.
    seconds, daily = quota_cooldown(_body(_quota_failure(_PER_DAY_ID), _retry_info("28s")), now)
    assert (seconds, daily) == (12 * 3600, True)


def test_a_per_day_quota_just_before_midnight_keeps_the_floor() -> None:
    now = datetime(2026, 9, 17, 23, 59, 50, tzinfo=PACIFIC)
    assert quota_cooldown(_body(_quota_failure(_PER_DAY_ID)), now) == (
        ai._QUOTA_COOLDOWN_MIN_S,
        True,
    )


def test_a_per_minute_quota_is_not_daily() -> None:
    seconds, daily = quota_cooldown(_body(_quota_failure(_PER_MINUTE_ID), _retry_info("45s")))
    assert (seconds, daily) == (45.0, False)


def test_a_naive_now_is_read_as_pacific() -> None:
    """The parameter exists for tests; a naive clock must not crash the comparison."""
    seconds, daily = quota_cooldown(_body(_quota_failure(_PER_DAY_ID)), datetime(2026, 9, 17, 18))
    assert (seconds, daily) == (6 * 3600, True)


# --- the cooldown register -----------------------------------------------------------------


async def test_a_429_is_not_retried() -> None:
    client, models = client_with([_minute_429(), "never reached"])
    with pytest.raises(QuotaExceeded) as excinfo:
        await client._generate(client.text_model, ["hi"], None)
    assert models.calls == 1
    assert excinfo.value.model == "text-model"
    assert (excinfo.value.retry_after_s, excinfo.value.daily) == (45.0, False)


async def test_a_second_call_during_the_cooldown_never_reaches_the_sdk() -> None:
    client, models = client_with([_daily_429()])
    for _ in range(2):
        with pytest.raises(QuotaExceeded):
            await client._generate(client.text_model, ["hi"], None)
    assert models.calls == 1


async def test_the_other_model_keeps_working_while_one_cools() -> None:
    """The point of the whole change: a dark vision model must not stop text."""
    client, models = client_with([_daily_429(), "ok"])
    with pytest.raises(QuotaExceeded):
        await client._generate(client.vision_model, ["look"], None)
    assert await client._generate(client.text_model, ["hi"], None) == "ok"
    assert models.calls == 2


async def test_the_model_is_used_again_once_the_cooldown_passes() -> None:
    client, models = client_with([_daily_429(), "ok"])
    with pytest.raises(QuotaExceeded):
        await client._generate(client.text_model, ["hi"], None)
    _, daily = client._cooldowns["text-model"]
    client._cooldowns["text-model"] = (time.monotonic() - 0.01, daily)

    assert await client._generate(client.text_model, ["hi"], None) == "ok"
    assert client._cooldowns == {}  # the stale entry is dropped, not kept forever
    assert models.calls == 2


async def test_the_retry_notice_never_fires_for_a_429() -> None:
    calls: list[int] = []

    async def notice() -> None:
        calls.append(1)

    client, models = client_with([_minute_429()])
    with pytest.raises(QuotaExceeded):
        await client._generate(client.text_model, ["hi"], None, notice)
    assert (models.calls, calls) == (1, [])


@pytest.mark.parametrize("vision", [True, False])
async def test_the_exception_says_whether_the_vision_model_ran_out(vision: bool) -> None:
    client, _ = client_with([_daily_429()])
    model = client.vision_model if vision else client.text_model
    with pytest.raises(QuotaExceeded) as excinfo:
        await client._generate(model, ["hi"], None)
    assert excinfo.value.vision is vision


async def test_check_quota_is_a_noop_for_a_model_that_never_failed() -> None:
    client, _ = client_with([])
    assert client.check_quota(client.vision_model) is None
    assert client.check_quota("some-other-model") is None


async def test_check_quota_raises_while_the_model_cools() -> None:
    """`photos.on_photo` calls this before downloading anything."""
    client, _ = client_with([_daily_429()])
    with pytest.raises(QuotaExceeded):
        await client._generate(client.vision_model, ["look"], None)

    with pytest.raises(QuotaExceeded) as excinfo:
        client.check_quota(client.vision_model)
    assert excinfo.value.daily is True and excinfo.value.vision is True
    assert 0 < excinfo.value.retry_after_s <= ai._QUOTA_COOLDOWN_MAX_S
    client.check_quota(client.text_model)  # the other model is untouched


async def test_a_public_method_reports_the_quota() -> None:
    client, models = client_with([_daily_429()])
    with pytest.raises(QuotaExceeded):
        await client.estimate_food(b"jpeg", "image/jpeg", None)
    assert models.calls == 1
