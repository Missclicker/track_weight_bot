"""All user-facing strings (Ukrainian). Messages are sent with parse_mode=HTML, so every piece of
user- or model-supplied text passed in here must already be HTML-escaped by the caller."""

from __future__ import annotations

import math
from html import escape

from bot.parsing import WATER_ALL_DAYS, WATER_WEEKDAYS, WATER_WEEKEND, WaterSchedule

FOOD_PREFIX = "≈"  # replies to messages starting with this are treated as kcal corrections

PRIVATE_CHAT_ONLY_GROUP = "Цей бот працює лише в груповому чаті."

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
    "week": ("week", "tyzhden", "тиждень", "звіт"),
    "water": ("water", "voda", "вода"),
}

HELP = (
    "Я записую їжу, вагу і спорт у спільну таблицю.\n\n"
    "Що я розумію:\n"
    "- фото їжі (можна з підписом) - оціню калорії і запишу;\n"
    "- число, наприклад <code>84.3</code> - запишу як вагу;\n"
    "- відповідь числом на моє повідомлення про їжу - виправлю оцінку;\n"
    '- текст про спорт ("пробіг 5 км за 30 хв", "зал 1 година") - запишу активність.\n\n'
    "Команди (працюють і українською):\n"
    "/w 84.3 або /вага 84.3 - записати вагу\n"
    "/food борщ і два хліба або /їжа ... - записати їжу текстом\n"
    "/sport біг 5 км 30 хв або /спорт ... - записати активність\n"
    "/today або /сьогодні - мій підсумок за сьогодні\n"
    "/kcal або /калорії - що я з'їв сьогодні і скільки це ккал\n"
    "/target 2000 або /ціль 2000 - денна ціль ккал (необов'язково; /ціль стоп - прибрати)\n"
    "/week або /тиждень - тижневий звіт зараз\n"
    "/water будні з 9 до 18 кожні 30 хв або /вода ... - нагадування пити воду в особисті\n"
    "/help або /довідка - ця довідка\n\n"
    "Оцінки з фото приблизні (±30-50 %). Щоб виправити - відповідай на оцінку числом ккал."
)

START_REGISTERED = "Записав тебе, {name}. Щоранку до {deadline} чекаю на вагу.\n\n" + HELP

ERROR_TRY_AGAIN = "Не вийшло, спробуй ще раз."

WEIGHT_USAGE = "Напиши вагу так: /w 84.3"
WEIGHT_OUT_OF_RANGE = "Це не схоже на вагу. Очікую число від {lo:g} до {hi:g} кг."
WEIGHT_FIRST = "Записав {kg} кг. Це твій перший запис."
WEIGHT_WITH_DELTA = (
    "Записав {kg} кг. Зміна від попереднього запису ({prev} кг, {prev_date}): {delta} кг."
)
WEIGHT_SAME = "Записав {kg} кг. Без змін від попереднього запису ({prev_date})."

FOOD_USAGE = "Опиши, що з'їв: /food борщ і два шматки хліба"
FOOD_NOT_FOOD = "Не бачу тут їжі. Якщо це все ж їжа - підпиши фото."
CORRECTION_SAVED = "Виправив: {kcal} ккал."
DAY_TOTAL_TODAY = "Разом за сьогодні: {kcal} ккал{target}."
DAY_TOTAL_DATE = "Разом за {date}: {kcal} ккал{target}."
CORRECTION_NOT_FOUND = "Не знайшов запис для виправлення."
CORRECTION_NOT_UNDERSTOOD = (
    "Не зрозумів уточнення. Напиши, що змінити: вагу порції, склад або назву страви."
)
CORRECTED_MARK = "Виправлено за твоїм уточненням."

SPORT_USAGE = "Опиши активність: /sport біг 5 км 30 хв"
SPORT_NOT_RECOGNIZED = 'Не розпізнав активність. Спробуй так: "біг 5 км 30 хв" або "зал 1 година".'
SPORT_SAVED = "Записав: {activity}, {minutes} хв{distance} - близько {kcal} ккал."

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

TARGET_USAGE = (
    "Денна ціль по калоріях: /ціль 2000 (від {lo} до {hi} ккал). "
    "Її можна не задавати - тоді я просто рахую. Прибрати: /ціль стоп."
)
TARGET_SET = "Записав ціль: {kcal} ккал на день. Показуватиму її поруч із підсумком."
TARGET_CLEARED = "Прибрав денну ціль. Далі просто рахую калорії."
TARGET_CURRENT = "Твоя денна ціль: {kcal} ккал. Змінити: /ціль 1800. Прибрати: /ціль стоп."
TARGET_NONE = "Денної цілі немає. Задати: /ціль 2000."

_CONFIDENCE = ((0.75, "висока"), (0.45, "середня"), (0.0, "низька"))


def confidence_label(value: float) -> str:
    for threshold, label in _CONFIDENCE:
        if value >= threshold:
            return label
    return "низька"


def fmt_kg(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.0f}"


def fmt_delta(value: float) -> str:
    sign = "+" if value > 0 else "-"
    return f"{sign}{abs(value):.1f}"


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
    confidence: float,
    notes: str,
    corrected: bool = False,
    day_total_line: str | None = None,
) -> str:
    """Bot reply to a food photo, `/food` text or a correction. Must start with FOOD_PREFIX.

    `day_total_line` is the `day_total(...)` text for the day this entry belongs to, including
    the entry itself.
    """
    lines = [
        f"{FOOD_PREFIX} {kcal:.0f} ккал - {escape(dish)}",
        f"Білки {protein_g:.0f} г, жири {fat_g:.0f} г, вуглеводи {carbs_g:.0f} г, "
        f"овочі {veg_share * 100:.0f} %.",
    ]
    if alcohol_kcal > 0:
        lines.append(f"З них алкоголь: {alcohol_kcal:.0f} ккал.")
    lines.append(f"Впевненість: {confidence_label(confidence)}.")
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


def sport_saved(activity_title: str, minutes: float, distance_km: float | None, kcal: float) -> str:
    distance = f", {distance_km:g} км" if distance_km else ""
    return SPORT_SAVED.format(
        activity=escape(activity_title),
        minutes=f"{minutes:.0f}",
        distance=distance,
        kcal=f"{kcal:.0f}",
    )


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


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
    name: str, date_str: str, items: list[tuple[str, float]], daily_target: float | None
) -> str:
    """`/kcal`: today's food entries one per line and the total. Never starts with FOOD_PREFIX."""
    lines = [KCAL_HEADER.format(name=escape(name), date=date_str)]
    if not items:
        lines.append(KCAL_NO_DATA)
        return "\n".join(lines)
    lines.extend(f"- {kcal:.0f} ккал - {escape(dish)}" for dish, kcal in items)
    total = sum(kcal for _, kcal in items)
    lines.append(f"Разом: {total:.0f} ккал{_target_suffix(daily_target)}.")
    return "\n".join(lines)


def weekly_stats_block(user: dict) -> str:
    """Plain numeric block per user, used as a fallback when the AI report fails."""
    name = str(user["name"])  # sent with parse_mode=None, so no escaping here
    parts = [
        f"{name}: {user['kcal_total']:.0f} ккал за тиждень (≈{user['kcal_avg_per_day']:.0f}/день)",
    ]
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
