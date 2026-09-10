# Architecture

A single Python process, long-polling Telegram, storing everything in one Google Spreadsheet and
calling Gemini for the parts that need judgement (what is on the plate, what a sport sentence
means, how the week went).

```
Telegram  <-- long polling -->  bot (aiogram)  --->  Google Sheets (gspread, thread pool)
                                    |
                                    +--->  Gemini API (google-genai, async client)
                                    |
                               APScheduler (morning ping, water reminders, weekly report)
```

## Module map

| module | responsibility |
|---|---|
| `bot/__main__.py` | Entry point. Loads settings (fails fast with a readable message), ensures the sheet schema, builds `Bot`/`Dispatcher`, injects dependencies, starts the scheduler and polling. |
| `bot/config.py` | `Settings` (pydantic-settings). Parses `ALLOWED_CHAT_IDS`, validates `HH:MM` times, weekday, timezone and that Google credentials exist. |
| `bot/i18n.py` | Every user-facing string, in Ukrainian. Formatting helpers escape HTML. |
| `bot/parsing.py` | Pure functions: `parse_weight`, `parse_correction`, `looks_like_sport`, `parse_water_schedule` / `is_water_due` (+ the `WaterSchedule` value object). |
| `bot/met.py` | MET table (activity -> MET, keyword regexes, typical pace) and `kcal = MET * kg * h`. |
| `bot/ai.py` | `GeminiClient`: `estimate_food` (photo or text), `parse_sport`, `weekly_report`. Pydantic response schemas with clamping validators. |
| `bot/sheets.py` | `SheetsRepo`: async facade over gspread (`asyncio.to_thread`), retry with backoff on 429/5xx, 60 s cache of the `users` tab, tab/header definitions. |
| `bot/init_sheets.py` | `python -m bot.init_sheets` - idempotent schema creation, prints the sheet URL and row counts. |
| `bot/reports.py` | Aggregations: `day_food` (per-day food list/total for `/kcal` and the food replies), `today_summary` and `build_weekly_payload` (the JSON given to Gemini). |
| `bot/scheduler.py` | `Jobs` (ping per timezone, water tick, weekly report) and `build_scheduler`. |
| `bot/handlers/` | aiogram routers, one file per feature; `__init__.py` assembles them and holds the allowed-chat gate and the global error handler. |

## Data flow

**Message routing.** The root router has two children, tried in order. `guarded` carries the
`AllowedChat` filter and drops every message whose `chat.id` is not in `ALLOWED_CHAT_IDS` (a
private chat whose id is listed - e.g. the owner testing in a DM - is served like a group).
`private` then serves the two commands that make sense outside the group: `/start` (a member is
told the bot can now DM them, anybody else gets "works only in the group") and `/вода`.
Inside `guarded` the routers are tried in order:

1. `commands` - `/start /help /w /food /sport /today /kcal /target /week`. `/target` writes
   only the `daily_kcal_target` cell (`set_daily_kcal_target`), so other hand-edited columns
   are untouched; the target is optional and `i18n._target_suffix` hides it when it is
   missing, zero or not a finite number. `/food` and food photos
   share `photos.record_food`, which reads the sender's food rows for today so the `≈` reply
   ends with "Разом за сьогодні: N ккал" (the new entry included); `/kcal` lists those rows.
2. `water` - `/вода` (`/water`, `/voda`): show, set or cancel the sender's water reminders.
3. `corrections` - a reply to a bot message that starts with `≈` (the food-estimate prefix).
   A number -> `update_food_kcal(user_id, message_id)`. Any other text -> `get_food_entry`,
   re-download the photo by its stored `file_id` (if any), `GeminiClient.revise_food` with the
   earlier estimate + the user's text -> new `≈` reply -> `update_food_entry`, which also re-keys
   the row to the new reply's `message_id` so corrections can be chained. Both are scoped to the
   sender, so only the author of an entry can correct it and equal `message_id`s from different
   groups never collide. Both replies end with the corrected total for the day the entry
   belongs to ("за сьогодні" or "за <date>"), read back from the sheet after the update.
4. `weight` - a bare number in `[WEIGHT_MIN, WEIGHT_MAX]` that is not a reply, or a number in
   reply to the morning ping (recognised by the ping text, so it survives restarts) -> `add_weight`.
5. `photos` - any photo -> Gemini vision -> reply -> `add_food` with the *reply's* `message_id`
   so a later correction can find the row.
6. `sport` - text matching a sport keyword -> Gemini text parse -> kcal from the MET table
   -> `add_sport`.

Filters return a `dict` on match (`{"kg": 84.3, "source": "text"}`), which aiogram injects into
the handler - so parsing happens once and unmatched messages fall through to the next router.
Anything unmatched is ignored.

**Users.** Nobody has to run `/start`: the first weight/food/sport message registers the sender
(`ensure_user`). The `users` tab is editable by hand - `tz`, `height_cm`, `target_kg`,
`daily_kcal_target`, `active` are preserved on upsert.

**Dates.** Every timestamp is written in the user's timezone (`users.tz`, fallback
`DEFAULT_TZ`) and "today" is computed there. The `date` column is the lookup key for all
aggregations.

**Scheduler.** At startup and nightly at 00:05 the bot collects the distinct timezones of active
users and (re)creates one cron job per timezone at `WEIGH_IN_DEADLINE`. The job mentions
(`<a href="tg://user?id=...">`) everyone in that timezone without a `weight` row for today. The weekly job runs on `WEEKLY_REPORT_DAY` at
`WEEKLY_REPORT_TIME` in `DEFAULT_TZ`: build payload -> Gemini -> send -> `reports` tab. If Gemini
fails, the numeric summary is sent instead.

**Water reminders.** One `water_tick` job runs every minute and walks an in-memory list of the
active rows of the `water` tab, so a per-user interval costs neither a job per subscriber nor a
Sheets read per minute. Each subscription carries its own zone key (copied from `users.tz` when it
was created), the tick converts the current minute into that zone and `is_water_due` decides
whether this minute is on the schedule's grid; the ping is a private message. The list is reloaded
together with the ping jobs (startup, nightly 00:05) and by the `/вода` handler, so hand edits of
the tab are picked up. `TelegramForbiddenError` (blocked bot, deleted chat) deactivates the row and
drops it from memory; any other send error is logged and the remaining subscribers still get theirs.
Telegram refuses a DM to a user who never pressed Start, so the handler sends the confirmation
message first and stores the subscription only when it went through.

**Errors.** A global error handler logs the exception and replies with a short "не вийшло,
спробуй ще" (it only fires when a handler matched, so the sender was always waiting). The
polling loop never dies because of a handler.

**Sheets writes.** Everything is appended with `value_input_option=RAW`: names and dishes can
never be evaluated as formulas, and the ISO `date`/`ts` strings we filter on are not re-formatted
by the spreadsheet locale. Registration of a new user is serialised with an `asyncio.Lock`
because aiogram handles each update in its own task.

## Decisions

- **Long polling, not webhooks.** No public endpoint, no TLS, works from a home box or a
  free VM behind NAT. Only one instance may poll at a time.
- **Google Sheets as the database.** The group wants to look at and edit the data; a sheet is the
  UI. Reads scan whole tabs (`get_all_records`), which is fine for a handful of people for years;
  if it ever hurts, cache tabs the same way `users` is cached.
- **Gemini Flash on the free tier.** Vision for photos, text model for sport parsing and the
  report. Structured output (`response_schema`) keeps parsing deterministic; numbers are clamped
  rather than rejected so one odd field does not lose a meal.
- **Kcal for sport is computed locally** from the MET table using the user's last known weight,
  so the AI only has to extract *what* and *how long*.
- **Everything blocking runs off the event loop.** gspread via `asyncio.to_thread`, Gemini via
  `client.aio`.
- **No emojis** in bot output; plain Ukrainian text with HTML only for mentions and `<code>`.

## How to add a feature

1. Put the parsing/decision logic in a pure function (`parsing.py`, `met.py`, `reports.py`) and
   write a test in `tests/` first - `tests/conftest.py` has an in-memory `FakeRepo`.
2. Add the user-facing strings to `i18n.py`.
3. If new data is stored, add a column at the end of the relevant tab in `sheets.HEADERS`, a
   repo method, the same method on `FakeRepo`, and update the README table.
4. Add a handler module under `bot/handlers/` exposing `build() -> Router`, and include it in
   `handlers.build_router()` at the right position in the order above.
5. Run `ruff check .` and `pytest`; `tests/test_routing.py` shows how to drive a handler through
   a real `Dispatcher` with a mocked Telegram session.
