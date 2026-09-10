import pytest

from bot.parsing import looks_like_sport, parse_correction, parse_weight

LO, HI = 40, 200


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("84.3", 84.3),
        ("84,3", 84.3),
        ("84", 84.0),
        (" 84.3 ", 84.3),
        ("84.3 кг", 84.3),
        ("84.3кг", 84.3),
        ("84 kg", 84.0),
        ("84.3 кг.", 84.3),
        ("вага 84.3", 84.3),
        ("Вага: 84,3", 84.3),
        ("weight 84.3", 84.3),
        ("в 84.3", 84.3),
        ("40", 40.0),
        ("200", 200.0),
        ("120.55", 120.55),
    ],
)
def test_parse_weight_accepts(text: str, expected: float) -> None:
    assert parse_weight(text, LO, HI) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "39.9",  # below range
        "200.1",  # above range
        "250",  # integer above range
        "1000",
        "5",
        "11:00",  # time
        "84:30",
        "12.09.2026",  # dates
        "12/09/26",
        "2026-09-12",
        "+380501234567",  # phones
        "0501234567",
        "84.3 і ще щось",
        "я важу 84.3 сьогодні",
        "84.3.5",
        "84.345",  # too many decimals -> likely not a weight
        "/w 84.3",  # commands are handled elsewhere
        "-84.3",
        "84.3%",
        "84 хв",
        "1e2",
    ],
)
def test_parse_weight_rejects(text: str | None) -> None:
    assert parse_weight(text, LO, HI) is None


def test_parse_weight_respects_custom_range() -> None:
    assert parse_weight("35", 30, 60) == 35.0
    assert parse_weight("84", 30, 60) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("650", 650.0),
        (" 650 ", 650.0),
        ("650 ккал", 650.0),
        ("650ккал", 650.0),
        ("650 kcal", 650.0),
        ("650 кал", 650.0),
        ("≈650", 650.0),
        ("≈ 650 ккал", 650.0),
        ("0", 0.0),
        ("1 200", 1200.0),
        ("1200.5", 1200.0),  # a decimal is tolerated but truncated to the integer part
    ],
)
def test_parse_correction_accepts(text: str, expected: float) -> None:
    assert parse_correction(text) == expected


@pytest.mark.parametrize(
    "text",
    [None, "", "abc", "650 грам", "12:30", "-100", "99999", "650 ккал будь ласка", "1e3"],
)
def test_parse_correction_rejects(text: str | None) -> None:
    assert parse_correction(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "пробіг 5 км за 30 хв",
        "Побігав 40 хвилин",
        "ранкова пробіжка 7 км",
        "в залі 1 година",
        "був у залі",
        "силове тренування 45 хв",
        "тренувався годину",
        "велосипед 20 км",
        "вело 1.5 години",
        "плавання 45 хв",
        "поплавав у басейні",
        "ходьба 10000 кроків",
        "пройшов 8 км",
        "йога 30 хв",
        "футбол 90 хвилин",
        "теніс 1 год",
        "ran 5k in 25 min",
        "gym 1h",
        "30 min swim",
        "cycling 40 km",
        "yoga 1 hour",
        "кардіо 20 хв",
        "танці 2 години",
    ],
)
def test_looks_like_sport_positive(text: str) -> None:
    assert looks_like_sport(text)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "84.3",
        "84,3 кг",
        "/sport біг 5 км",  # commands go through the command handler
        "привіт усім",
        "залишилось два дні до вихідних",  # 'зал' must not match 'залишилось'
        "обід був смачний",
        "хто йде в кіно?",
        "тобіго",
        "скільки ккал у борщі?",
        "x" * 301,
        # everyday phrases that share a stem with a sport keyword
        "плавно перейдемо до справи",
        "похід у магазин за хлібом",
        "гуляли по місту з дітьми",
        "крок за кроком",
        "skip lunch today",
        "a great skill",
        "my weight is 84.3",
    ],
)
def test_looks_like_sport_negative(text: str | None) -> None:
    assert not looks_like_sport(text)
