import pytest

from bot import met


def test_kcal_formula() -> None:
    # 9.8 MET * 80 kg * 0.5 h = 392 kcal
    assert met.kcal_burned(9.8, 80, 30) == pytest.approx(392)


def test_estimate_running_with_minutes() -> None:
    minutes, kcal = met.estimate_kcal("running", 30, 80)
    assert minutes == 30
    assert kcal == 392


def test_estimate_uses_distance_when_minutes_missing() -> None:
    minutes, kcal = met.estimate_kcal("running", None, 80, distance_km=5)
    assert minutes == 30  # 6 min/km default pace
    assert kcal == 392


def test_estimate_defaults_when_nothing_given() -> None:
    minutes, kcal = met.estimate_kcal("gym", None, None)
    assert minutes == met.DEFAULT_MINUTES
    assert kcal == round(5.0 * met.DEFAULT_WEIGHT_KG * 0.5)


def test_unknown_activity_falls_back_to_moderate_met() -> None:
    _, kcal = met.estimate_kcal("curling", 60, 100)
    assert kcal == 500


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("пробіг 5 км", "running"),
        ("бігав зранку", "running"),
        ("пройшов 10 000 кроків", "walking"),
        ("велосипед 20 км", "cycling"),
        ("плавання 45 хв", "swimming"),
        ("силове в залі", "gym"),
        ("йога", "yoga"),
        ("футбол з друзями", "football"),
        ("теніс 1 год", "tennis"),
        ("swam 1 km", "swimming"),
        ("gym session", "gym"),
    ],
)
def test_find_activity(text: str, key: str) -> None:
    activity = met.find_activity(text)
    assert activity is not None
    assert activity.key == key


def test_find_activity_none() -> None:
    assert met.find_activity("привіт, як справи?") is None


def test_every_activity_has_positive_met_and_title() -> None:
    for activity in met.ACTIVITIES.values():
        assert activity.met > 0
        assert activity.title
        assert activity.patterns
