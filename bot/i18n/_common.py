"""Language-neutral pieces shared by `bot.i18n` and its language modules.

They live in their own module, not in `bot/i18n/__init__.py`, so that `uk.py` and `en.py` can
import them while the package itself is still being initialised (the package imports both
language modules to build `t()` and the recognition tuples).
"""

from __future__ import annotations

from html import escape

FOOD_PREFIX = "≈"  # a reply to a message starting with this corrects, deletes or weighs in

# Every command has three spellings: the short English one, a Latin transliteration of the
# Ukrainian word (registrable in BotFather, which accepts only [a-z0-9_]) and the Cyrillic word
# itself (Telegram clients do not autocomplete or highlight it, but the bot understands it when
# typed). Keep README "/setcommands" block in sync with the first two.
COMMANDS: dict[str, tuple[str, ...]] = {
    "start": ("start", "старт"),
    "help": ("help", "dovidka", "довідка", "допомога"),
    "w": ("w", "vaga", "вага"),
    "food": ("food", "yizha", "їжа"),
    "sport": ("sport", "спорт"),
    "today": ("today", "sohodni", "сьогодні"),
    "kcal": ("kcal", "kalorii", "калорії"),
    "target": ("target", "tsil", "ціль"),
    "profile": ("profile", "profil", "профіль"),
    "week": ("week", "tyzhden", "тиждень", "звіт"),
    "water": ("water", "voda", "вода"),
    "lang": ("lang", "language", "mova", "мова"),
}


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


def fmt_kg(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.0f}"


def fmt_delta(value: float) -> str:
    sign = "+" if value > 0 else "-"
    return f"{sign}{abs(value):.1f}"
