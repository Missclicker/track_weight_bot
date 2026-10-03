"""Ukrainian user-facing strings, the default language. Messages are sent with parse_mode=HTML, so
every piece of user- or model-supplied text interpolated into a constant must be HTML-escaped by
the caller (the helper functions below escape their own text arguments).

`bot/i18n/en.py` mirrors this module name for name (tests/test_i18n.py checks the parity); the
comments explaining why a text is worded the way it is live here, not there.
"""

from __future__ import annotations

import math
from html import escape

from bot.i18n._common import FOOD_PREFIX, fmt_delta, fmt_kg
from bot.parsing import WATER_ALL_DAYS, WATER_WEEKDAYS, WATER_WEEKEND, WaterSchedule

# Everything a language module exports; `en.py` exports exactly the same names.
__all__ = [
    "AI_BUSY",
    "AI_BUSY_PHOTO",
    "AI_QUOTA_DAY",
    "AI_QUOTA_PHOTO_DAY",
    "AI_QUOTA_PHOTO_SOON",
    "AI_QUOTA_SOON",
    "AI_RETRYING",
    "CORRECTED_MARK",
    "CORRECTION_NOT_FOUND",
    "CORRECTION_NOT_UNDERSTOOD",
    "CORRECTION_SAVED",
    "DAY_TOTAL_DATE",
    "DAY_TOTAL_TODAY",
    "ERROR_TRY_AGAIN",
    "FOOD_DELETED",
    "FOOD_INPUT_PROMPT",
    "FOOD_INPUT_PROMPT_PREFIX",
    "FOOD_NOT_FOOD",
    "HELP",
    "KCAL_HEADER",
    "KCAL_HEADER_YESTERDAY",
    "KCAL_NO_DATA",
    "KCAL_NO_DATA_YESTERDAY",
    "KCAL_NO_TIME",
    "LANG_CURRENT",
    "LANG_NAME",
    "LANG_SET",
    "LANG_USAGE",
    "PING",
    "PING_PREFIX",
    "PRIVATE_CHAT_ONLY_GROUP",
    "PROFILE_CLEARED",
    "PROFILE_CURRENT",
    "PROFILE_NONE",
    "PROFILE_SET",
    "PROFILE_USAGE",
    "PROMPT_CANCELLED",
    "SPORT_DELETED",
    "SPORT_INPUT_PROMPT",
    "SPORT_INPUT_PROMPT_PREFIX",
    "SPORT_NOT_FOUND_FOR_DELETE",
    "SPORT_NOT_RECOGNIZED",
    "SPORT_PREFIX",
    "SPORT_SAVED",
    "SPORT_SAVED_DATE",
    "START_REGISTERED",
    "TARGET_CLEARED",
    "TARGET_CURRENT",
    "TARGET_NONE",
    "TARGET_SET",
    "TARGET_USAGE",
    "TODAY_HEADER",
    "TODAY_NO_DATA",
    "WATER_DM_READY",
    "WATER_NEED_DM",
    "WATER_NOT_SUBSCRIBED",
    "WATER_PING",
    "WATER_STATUS",
    "WATER_STOPPED",
    "WATER_SUBSCRIBED",
    "WATER_SUBSCRIBED_DM",
    "WATER_USAGE",
    "WEEKLY_AI_FAILED",
    "WEEKLY_HEADER",
    "WEEKLY_NO_DATA",
    "WEIGHT_FIRST",
    "WEIGHT_OUT_OF_RANGE",
    "WEIGHT_SAME",
    "WEIGHT_USAGE",
    "WEIGHT_WITH_DELTA",
    "busy_notice",
    "day_total",
    "fmt_profile",
    "fmt_water_schedule",
    "food_estimate",
    "kcal_today",
    "quota_notice",
    "sport_saved",
    "today_summary",
    "weekly_stats_block",
]

# Replies to bot messages starting with this can delete the activity. Like every other prefix in
# here it must not collide with the English one: routing recognises a bot message by its text,
# whichever language it was sent in (see `bot.i18n.SPORT_PREFIXES`).
SPORT_PREFIX = "Спорт:"

PRIVATE_CHAT_ONLY_GROUP = "Цей бот працює лише в груповому чаті."

HELP = (
    "Я записую їжу, вагу і спорт у спільну таблицю.\n\n"
    "Що я розумію:\n"
    "- фото їжі (можна з підписом) - оціню калорії і запишу;\n"
    "- число, наприклад <code>84.3</code> - запишу як вагу;\n"
    "- відповідь числом на моє повідомлення про їжу - виправлю оцінку.\n\n"
    "Команди (працюють і українською):\n"
    "/w 84.3 або /вага 84.3 - записати вагу\n"
    "/food борщ і два хліба або /їжа ... - записати їжу текстом; без тексту - запитаю\n"
    "/sport біг 5 км 30 хв або /спорт ... - записати активність; без тексту - запитаю\n"
    '/їжа вчора млинці або /спорт вчора волейбол 2 години - записати за вчора (слово "вчора" '
    "працює і у відповіді на запит, і в підписі до фото)\n"
    "/today або /сьогодні - мій підсумок за сьогодні\n"
    "/kcal або /калорії - що я з'їв сьогодні і скільки це ккал (/калорії вчора - за вчора)\n"
    "/target 2000 або /ціль 2000 - денна ціль ккал (необов'язково; /ціль стоп - прибрати)\n"
    "/profile 1981 ч 180 або /профіль ... - рік народження, стать і зріст для тижневого звіту "
    "(необов'язково; /профіль стоп - прибрати)\n"
    "/week або /тиждень - звіт за 7 останніх повних днів (без сьогодні)\n"
    "/water будні з 9 до 18 кожні 30 хв або /вода ... - нагадування пити воду в особисті\n"
    "/lang en або /мова en - перейти на англійську (switch to English)\n"
    "/help або /довідка - ця довідка\n\n"
    "Оцінки з фото приблизні (±30-50 %). Щоб виправити - відповідай на оцінку числом ккал.\n"
    'Щоб прибрати запис про їжу чи активність - відповідай на нього "видали" або '
    '"видали цей запис".'
)

START_REGISTERED = "Записав тебе, {name}. Щоранку до {deadline} чекаю на вагу.\n\n" + HELP

# `/lang`. The way to the other language is spelled in that language too: somebody who cannot
# read this one must still find the way out of it. LANG_SET is sent in the language just chosen.
LANG_NAME = "українська"
LANG_CURRENT = "Мова: українська. Switch to English: /lang en"
LANG_SET = "Готово, тепер відповідаю українською."
LANG_USAGE = "Не знаю такої мови. Можна: /мова uk (українська) або /мова en (English)."

ERROR_TRY_AGAIN = "Не вийшло, спробуй ще раз."
# Sent once mid-call, when a Gemini attempt failed and a longer one is starting. Deliberately
# says nothing about *why*: the same notice covers a deadline, a 5xx and a dropped connection.
AI_RETRYING = "AI не відповів, пробую ще раз."

# Two axes decide what to say when Gemini answers 429: *which* model ran out (only photos need the
# vision one, so describing the meal in text still works without it - unless the text model is
# unusable too, which `GeminiClient._is_vision_only_outage` checks: both env vars naming the same
# model, or that model being out of quota or overloaded itself) and *how long* it is gone
# (a per-day quota is back when Google's counter resets, a per-minute one within a minute). No
# time is promised for the daily case: that counter resets at midnight Pacific, which can be an
# hour from now or twenty - "спробуй завтра" would often be a lie. Nor is a duration
# interpolated for the short case: "за хвилину" reads right for any short cooldown, and a
# wrong count of minutes would be worse than none.
AI_QUOTA_PHOTO_DAY = (
    "Денний ліміт AI на фото вичерпано. Опиши їжу текстом: /їжа борщ і два шматки хліба."
)
AI_QUOTA_PHOTO_SOON = (
    "Забагато запитів до AI. Спробуй надіслати фото ще раз за хвилину "
    "або опиши їжу текстом: /їжа борщ і два шматки хліба."
)
AI_QUOTA_DAY = "Денний ліміт AI вичерпано. Спробуй пізніше."
AI_QUOTA_SOON = "Забагато запитів до AI. Спробуй ще раз за хвилину."


def quota_notice(vision: bool, daily: bool) -> str:
    """The message for a spent Gemini quota: `vision` is the photo path, `daily` the per-day one."""
    if vision:
        return AI_QUOTA_PHOTO_DAY if daily else AI_QUOTA_PHOTO_SOON
    return AI_QUOTA_DAY if daily else AI_QUOTA_SOON


# A 503 is not our budget running out but Google's model being swamped by everyone's traffic, so
# the quota wording ("ліміт вичерпано") would blame the wrong thing. Only one axis is left here:
# *which* model is gone. The photo variant keeps pointing at `/їжа <текст>`, but only when the
# text model is really usable (`GeminiClient._is_vision_only_outage` decides, the same test the
# quota wording above goes through). No duration is promised: a demand spike lasts as long as it
# lasts, and "за хвилину" reads right for the ~30 s
# set-aside without pretending to count it down.
AI_BUSY = "AI зараз перевантажений. Спробуй ще раз за хвилину."
AI_BUSY_PHOTO = (
    "AI зараз перевантажений. Спробуй надіслати фото ще раз за хвилину "
    "або опиши їжу текстом: /їжа борщ і два шматки хліба."
)


def busy_notice(vision: bool) -> str:
    """The message for an overloaded Gemini model: `vision` is the photo path."""
    return AI_BUSY_PHOTO if vision else AI_BUSY


WEIGHT_USAGE = "Напиши вагу так: /w 84.3"
WEIGHT_OUT_OF_RANGE = "Це не схоже на вагу. Очікую число від {lo:g} до {hi:g} кг."
WEIGHT_FIRST = "Записав {kg} кг. Це твій перший запис."
WEIGHT_WITH_DELTA = (
    "Записав {kg} кг. Зміна від попереднього запису ({prev} кг, {prev_date}): {delta} кг."
)
WEIGHT_SAME = "Записав {kg} кг. Без змін від попереднього запису ({prev_date})."

# A bare /food or /sport asks for the text instead of erroring. The prompt is recognised later by
# its prefix (see handlers.commands.InputPrompt), the same restart-safe trick PING_PREFIX uses.
FOOD_INPUT_PROMPT_PREFIX = "Чекаю опис їжі."
FOOD_INPUT_PROMPT = (
    FOOD_INPUT_PROMPT_PREFIX + " Відповідай на це повідомлення, наприклад: борщ і два шматки хліба."
)
FOOD_NOT_FOOD = "Не бачу тут їжі. Якщо це все ж їжа - підпиши фото."
CORRECTION_SAVED = "Виправив: {kcal} ккал."
DAY_TOTAL_TODAY = "Разом за сьогодні: {kcal} ккал{target}."
DAY_TOTAL_DATE = "Разом за {date}: {kcal} ккал{target}."
CORRECTION_NOT_FOUND = "Не знайшов запис для виправлення."
CORRECTION_NOT_UNDERSTOOD = (
    "Не зрозумів уточнення. Напиши, що змінити: вагу порції, склад або назву страви."
)
CORRECTED_MARK = "Виправлено за твоїм уточненням."
FOOD_DELETED = "Видалив запис."
PROMPT_CANCELLED = "Скасував."

SPORT_INPUT_PROMPT_PREFIX = "Чекаю опис активності."
SPORT_INPUT_PROMPT = (
    SPORT_INPUT_PROMPT_PREFIX + " Відповідай на це повідомлення, наприклад: біг 5 км 30 хв."
)
SPORT_NOT_RECOGNIZED = 'Не розпізнав активність. Спробуй так: "біг 5 км 30 хв" або "зал 1 година".'
SPORT_SAVED = SPORT_PREFIX + " {activity}, {minutes} хв{distance} - близько {kcal} ккал."
# The backdated variant. Still starts with SPORT_PREFIX, because that prefix is how a reply is
# recognised as a delete request for this row.
SPORT_SAVED_DATE = (
    SPORT_PREFIX + " {activity}, {minutes} хв{distance} - близько {kcal} ккал. Записано за {date}."
)
SPORT_DELETED = "Видалив запис про активність."
SPORT_NOT_FOUND_FOR_DELETE = "Не знайшов запис для видалення."

WATER_USAGE = (
    "Нагадування пити воду. Напиши, коли і як часто:\n"
    "/вода будні дні з 9 до 18 кожні 30 хвилин\n"
    "/water щодня з 8:30 до 22:00 кожну годину\n"
    "/voda пн, ср, пт 10-19 кожні 2 години\n\n"
    "Дні: будні, вихідні, щодня, список (пн, ср, пт) або проміжок (пн-пт); типово щодня.\n"
    "Час: з 9 до 18 або 9:00-18:00, в межах однієї доби; типово з 09:00 до 21:00.\n"
    "Інтервал обов'язковий: від 15 хв до 12 год.\n"
    "Мої нагадування: /вода. Вимкнути: /вода стоп."
)
WATER_SUBSCRIBED = (
    "Нагадування про воду: {schedule}. За твоїм часом ({tz}).\n"
    "Писатиму в особисті повідомлення. Вимкнути: /вода стоп."
)
WATER_SUBSCRIBED_DM = "Нагадування про воду: {schedule}. Писатиму сюди. Вимкнути: /вода стоп."
WATER_PING = "Час випити склянку води."
WATER_STATUS = (
    "Нагадування про воду: {schedule}. За твоїм часом ({tz}).\n"
    "Щоб змінити - надішли команду з новим розкладом. Вимкнути: /вода стоп."
)
WATER_STOPPED = "Вимкнув нагадування про воду."
WATER_NOT_SUBSCRIBED = "Нагадувань про воду в тебе немає."
WATER_NEED_DM = "Не можу написати тобі в особисті. Відкрий {link}, натисни Start і повтори команду."
WATER_DM_READY = (
    "Тепер я можу писати тобі сюди. Нагадування про воду: "
    "/вода будні з 9 до 18 кожні 30 хв - тут або в групі."
)

_WATER_DAY_NAMES = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")


def fmt_water_schedule(schedule: WaterSchedule) -> str:
    """One-line summary of a water schedule: "будні, 09:00-18:00, кожні 30 хв"."""
    if schedule.days == WATER_ALL_DAYS:
        days = "щодня"
    elif schedule.days == WATER_WEEKDAYS:
        days = "будні"
    elif schedule.days == WATER_WEEKEND:
        days = "вихідні"
    else:
        days = ", ".join(_WATER_DAY_NAMES[d] for d in sorted(schedule.days))
    if schedule.every_min % 60:
        every = f"кожні {schedule.every_min} хв"
    else:
        hours = schedule.every_min // 60
        every = "кожну годину" if hours == 1 else f"кожні {hours} год"
    window = f"{schedule.start:%H:%M}-{schedule.end:%H:%M}"
    return f"{days}, {window}, {every}"


PING_PREFIX = "Доброго ранку!"  # replies to bot messages starting with this count as weigh-ins
PING = PING_PREFIX + " Ще не зважилися сьогодні: {mentions}. Відповідай на це повідомлення числом."

WEEKLY_HEADER = "Тижневий звіт {start} - {end}"
WEEKLY_NO_DATA = "За цей тиждень записів немає."
WEEKLY_AI_FAILED = "(Рекомендації від AI недоступні, показую лише цифри.)"

TODAY_HEADER = "Твій день, {name} ({date}):"
TODAY_NO_DATA = "Сьогодні записів ще немає."

KCAL_HEADER = "Їжа за сьогодні, {name} ({date}):"
KCAL_NO_DATA = "Сьогодні їжі ще не записано."
KCAL_HEADER_YESTERDAY = "Їжа за вчора, {name} ({date}):"
KCAL_NO_DATA_YESTERDAY = "Вчора їжі не записано."
KCAL_NO_TIME = "--:--"  # shown instead of the time when the row has no usable ts

TARGET_USAGE = (
    "Денна ціль по калоріях: /ціль 2000 (від {lo} до {hi} ккал). "
    "Її можна не задавати - тоді я просто рахую. Прибрати: /ціль стоп."
)
TARGET_SET = "Записав ціль: {kcal} ккал на день. Показуватиму її поруч із підсумком."
TARGET_CLEARED = "Прибрав денну ціль. Далі просто рахую калорії."
TARGET_CURRENT = "Твоя денна ціль: {kcal} ккал. Змінити: /ціль 1800. Прибрати: /ціль стоп."
TARGET_NONE = "Денної цілі немає. Задати: /ціль 2000."

PROFILE_USAGE = (
    "Профіль для тижневого звіту, необов'язковий: рік народження (або вік), стать і зріст - "
    "у будь-якому порядку, можна й лише щось одне.\n"
    "/профіль 1981 ч 180\n"
    "/profile 45 жінка 165,5 см\n"
    "/профіль 182 - змінити лише зріст\n\n"
    "Рік народження: від {year_lo} до {year_hi}, або вік: від {age_lo} до {age_hi}.\n"
    "Стать: ч, чоловіча, m або ж, жіноча, f.\n"
    "Зріст: від {height_lo} до {height_hi} см.\n"
    "Звіт бере з профілю особисту норму білка й оцінку витрати енергії. Прибрати: /профіль стоп."
)
PROFILE_SET = (
    "Записав профіль: {profile}.\n"
    "Він необов'язковий: тижневий звіт візьме з нього особисту норму білка й оцінку витрати "
    "енергії. Прибрати: /профіль стоп."
)
PROFILE_CLEARED = "Прибрав профіль. Тижневий звіт обійдеться без нього."
PROFILE_CURRENT = (
    "Твій профіль: {profile}.\n"
    "Він необов'язковий: тижневий звіт бере з нього особисту норму білка й оцінку витрати "
    "енергії.\n"
    "Змінити: /профіль 1981 ч 180 (можна й одне поле, наприклад /профіль 45). "
    "Прибрати: /профіль стоп."
)
PROFILE_NONE = (
    "Профілю немає, він необов'язковий. З роком народження, статтю і зростом тижневий звіт "
    "порахує особисту норму білка й оцінку витрати енергії.\n"
    "Задати: /профіль 1981 ч 180 (або вік замість року: /профіль 45 ж 165)."
)
_SEX_NAMES = {"m": "чоловіча", "f": "жіноча"}


def fmt_profile(
    birth_year: int | None, sex: str | None, height_cm: float | None, current_year: int
) -> str:
    """One line, "рік народження 1981 (45 р.), стать чоловіча, зріст 180 см"; unknown fields are
    left out, and nothing known at all gives "".

    The age is `current_year - birth_year`, which is what `/profile 45` stored - so the reply
    confirms the age the user typed, while the year says what the report will actually use.
    """
    parts: list[str] = []
    if birth_year is not None:
        age = current_year - birth_year
        # a hand-typed year in the future would read as a negative age
        parts.append(f"рік народження {birth_year}" + (f" ({age} р.)" if age > 0 else ""))
    if sex in _SEX_NAMES:
        parts.append(f"стать {_SEX_NAMES[sex]}")
    if height_cm is not None:
        parts.append(f"зріст {fmt_kg(height_cm)} см")  # "180", "180.5": same shape as a weight
    return ", ".join(parts)


def _target_suffix(daily_target: float | None) -> str:
    """Suffix " (ціль 2000)" - or nothing when no (sane) target is set."""
    if daily_target is None or not math.isfinite(daily_target) or daily_target <= 0:
        return ""
    return f" (ціль {daily_target:.0f})"


def day_total(kcal: float, daily_target: float | None, date_str: str | None = None) -> str:
    """Line "Разом за сьогодні: 1130 ккал (ціль 2000)." - or "за <date>" for a past day."""
    target = _target_suffix(daily_target)
    if date_str is None:
        return DAY_TOTAL_TODAY.format(kcal=f"{kcal:.0f}", target=target)
    return DAY_TOTAL_DATE.format(date=date_str, kcal=f"{kcal:.0f}", target=target)


def food_estimate(
    dish: str,
    kcal: float,
    alcohol_kcal: float,
    protein_g: float,
    fat_g: float,
    carbs_g: float,
    veg_share: float,
    notes: str,
    portion: str,
    corrected: bool = False,
    day_total_line: str | None = None,
) -> str:
    """Bot reply to a food photo, `/food` text or a correction. Must start with FOOD_PREFIX.

    `portion` is the size the estimate covers ("400 г"); it is shown so the user can see what the
    calories were computed for and correct it. `day_total_line` is the `day_total(...)` text for
    the day this entry belongs to, including the entry itself.
    """
    dish_line = f"{FOOD_PREFIX} {kcal:.0f} ккал - {escape(dish)}"
    if portion:
        dish_line += f", {escape(portion)}"
    lines = [
        dish_line,
        f"Білки {protein_g:.0f} г, жири {fat_g:.0f} г, вуглеводи {carbs_g:.0f} г, "
        f"овочі {veg_share * 100:.0f} %.",
    ]
    if alcohol_kcal > 0:
        lines.append(f"З них алкоголь: {alcohol_kcal:.0f} ккал.")
    if notes:
        lines.append(escape(notes))
    if corrected:
        lines.append(CORRECTED_MARK)
    if day_total_line:
        lines.append(day_total_line)
    lines.append(
        "Щоб виправити - відповідай на це повідомлення числом ккал або уточненням "
        "(вага порції, склад, назва страви)."
    )
    return "\n".join(lines)


def sport_saved(
    activity_title: str,
    minutes: float,
    distance_km: float | None,
    kcal: float,
    date_str: str | None = None,
) -> str:
    """Confirmation for a stored activity; `date_str` names the day when it is not today."""
    distance = f", {distance_km:g} км" if distance_km else ""
    template = SPORT_SAVED if date_str is None else SPORT_SAVED_DATE
    return template.format(
        activity=escape(activity_title),
        minutes=f"{minutes:.0f}",
        distance=distance,
        kcal=f"{kcal:.0f}",
        date=date_str,
    )


def today_summary(
    name: str,
    date_str: str,
    kcal_in: float,
    alcohol_kcal: float,
    sport_kcal: float,
    sport_minutes: float,
    weight: float | None,
    food_entries: int,
    daily_target: float | None,
) -> str:
    lines = [TODAY_HEADER.format(name=escape(name), date=date_str)]
    if food_entries == 0 and sport_minutes == 0 and weight is None:
        lines.append(TODAY_NO_DATA)
        return "\n".join(lines)
    lines.append(f"Їжа: {kcal_in:.0f} ккал ({food_entries} записів)")
    if alcohol_kcal:
        lines.append(f"З них алкоголь: {alcohol_kcal:.0f} ккал")
    if sport_minutes:
        lines.append(f"Спорт: {sport_minutes:.0f} хв, -{sport_kcal:.0f} ккал")
    net = kcal_in - sport_kcal
    lines.append(f"Разом: {net:.0f} ккал{_target_suffix(daily_target)}")
    if weight is not None:
        lines.append(f"Вага: {fmt_kg(weight)} кг")
    return "\n".join(lines)


def kcal_today(
    name: str,
    date_str: str,
    items: list[tuple[str | None, str, float]],
    daily_target: float | None,
    *,
    yesterday: bool = False,
) -> str:
    """`/kcal`: the day's food entries one per line and the total. Never starts with FOOD_PREFIX.

    The day is today, or yesterday for "/калорії вчора" - only the header and the empty-day line
    say which. Each item is `(at, dish, kcal)` - `reports.FoodItem` - where `at` is the "HH:MM" of
    the entry's `ts` in the user's own timezone: when it was logged, or the meal time stated with
    it ("/їжа 14:00 борщ"). A row without a usable timestamp shows `KCAL_NO_TIME`.
    """
    header = KCAL_HEADER_YESTERDAY if yesterday else KCAL_HEADER
    lines = [header.format(name=escape(name), date=date_str)]
    if not items:
        lines.append(KCAL_NO_DATA_YESTERDAY if yesterday else KCAL_NO_DATA)
        return "\n".join(lines)
    lines.extend(
        f"{at or KCAL_NO_TIME} - {kcal:.0f} ккал - {escape(dish)}" for at, dish, kcal in items
    )
    total = sum(kcal for _, _, kcal in items)
    lines.append(f"Разом: {total:.0f} ккал{_target_suffix(daily_target)}.")
    return "\n".join(lines)


def weekly_stats_block(user: dict) -> str:
    """Plain numeric block per user, used as a fallback when the AI report fails."""
    name = str(user["name"])  # sent with parse_mode=None, so no escaping here
    # The average is per logged day, not per calendar day, so the day count is spelled out: a
    # week with three logged days must not read as an average over seven.
    days = user["days_with_food_logged"]
    total = f"{name}: {user['kcal_total']:.0f} ккал за тиждень"
    # With no food rows at all the average and the day count are both zero: the parenthetical
    # would say nothing, so it is dropped (such a user is here for sport or weight only).
    if days:
        total += f" (≈{user['kcal_avg_per_day']:.0f}/день за {days} дн. із записами)"
    parts = [total]
    # `.get`: the protein keys came later than the rest, and a payload without them still formats.
    # The average is per logged day like the kcal one, so it is shown only when there is one.
    protein = user.get("protein_g_avg_per_day")
    if days and protein is not None:
        part = f"білок ≈{protein:.0f} г/день"
        target = user.get("protein_target_g_per_day")
        if target is not None:
            part += f" (норма {target:.0f} г)"
        parts.append(part)
    if user["alcohol_kcal"]:
        parts.append(f"алкоголь {user['alcohol_kcal']:.0f} ккал")
    if user["sport_minutes"]:
        parts.append(f"спорт {user['sport_minutes']:.0f} хв, -{user['sport_kcal']:.0f} ккал")
    if user["weight_first"] is not None and user["weight_last"] is not None:
        parts.append(
            f"вага {fmt_kg(user['weight_first'])} -> {fmt_kg(user['weight_last'])} кг "
            f"({fmt_delta(user['weight_delta'])})"
        )
    return "; ".join(parts)
