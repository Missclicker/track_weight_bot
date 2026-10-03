"""User-facing text in every supported language, plus the language-neutral helpers.

`t(lang)` picks the strings module for a reader: `bot.i18n.uk` (the default) or `bot.i18n.en`,
both exporting the same names. A person's language is stored in the `users.lang` column; an
unset one means Ukrainian, so everybody who registered before English existed sees no change.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from types import ModuleType
from typing import Literal

from bot.i18n import en, uk
from bot.i18n._common import COMMANDS, FOOD_PREFIX, fmt_delta, fmt_kg, mention

Lang = Literal["uk", "en"]
LANGS: tuple[Lang, ...] = ("uk", "en")
DEFAULT_LANG: Lang = "uk"

# What `/lang <arg>` and a hand-typed `users.lang` cell may say, after `normalize_lang` folded
# the case, the surrounding whitespace and a trailing dot away.
_LANG_ALIASES: dict[str, Lang] = {
    **dict.fromkeys(("uk", "ua", "ukr", "ukrainian", "укр", "українська", "українською"), "uk"),
    **dict.fromkeys(("en", "eng", "english", "англ", "англійська", "англійською"), "en"),
}

_MODULES: dict[Lang, ModuleType] = {"uk": uk, "en": en}

# Routing recognises a bot message by the start of its text, never by a stored message id, and a
# message sent in one language must still be recognised after its reader switches to the other
# one - so every recogniser checks all languages (`str.startswith` takes a tuple).
SPORT_PREFIXES: tuple[str, ...] = (uk.SPORT_PREFIX, en.SPORT_PREFIX)
PING_PREFIXES: tuple[str, ...] = (uk.PING_PREFIX, en.PING_PREFIX)
FOOD_INPUT_PROMPT_PREFIXES: tuple[str, ...] = (
    uk.FOOD_INPUT_PROMPT_PREFIX,
    en.FOOD_INPUT_PROMPT_PREFIX,
)
SPORT_INPUT_PROMPT_PREFIXES: tuple[str, ...] = (
    uk.SPORT_INPUT_PROMPT_PREFIX,
    en.SPORT_INPUT_PROMPT_PREFIX,
)


def normalize_lang(value: object) -> Lang | None:
    """A language code for "EN", " English. ", "укр" and the like; None for anything else.

    Used for `/lang <arg>` and for the hand-editable `users.lang` cell alike.
    """
    if not isinstance(value, str):
        return None
    return _LANG_ALIASES.get(value.strip().lower().rstrip(".").strip())


def t(lang: object) -> ModuleType:
    """The strings module for `lang`; an unknown or unset language gets the Ukrainian default."""
    return _MODULES[normalize_lang(lang) or DEFAULT_LANG]


def majority_lang(langs: Iterable[object]) -> Lang:
    """The language most of `langs` speak, for a message the whole group reads.

    An unset or unknown value counts as the default (Ukrainian), as it does for that person's own
    messages. A tie - including no people at all - goes to English, the user's explicit choice.
    """
    counts = Counter(normalize_lang(lang) or DEFAULT_LANG for lang in langs)
    if counts["uk"] > counts["en"]:
        return "uk"
    return "en"


__all__ = [
    "COMMANDS",
    "DEFAULT_LANG",
    "FOOD_INPUT_PROMPT_PREFIXES",
    "FOOD_PREFIX",
    "LANGS",
    "PING_PREFIXES",
    "SPORT_INPUT_PROMPT_PREFIXES",
    "SPORT_PREFIXES",
    "Lang",
    "en",
    "fmt_delta",
    "fmt_kg",
    "majority_lang",
    "mention",
    "normalize_lang",
    "t",
    "uk",
]
