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
| `bot/parsing.py` | Pure functions: `parse_weight`, `parse_correction`, `parse_kcal_target`, `parse_water_schedule` / `is_water_due` (+ the `WaterSchedule` value object). |
| `bot/met.py` | MET table (activity -> MET, keyword regexes, typical pace) and `kcal = MET * kg * h`. |
| `bot/ai.py` | `GeminiClient`: `estimate_food` (photo or text), `revise_food` (text only), `parse_sport`, `weekly_report`. Pydantic response schemas with clamping validators; three attempts with escalating server deadlines and an optional one-shot "retrying" callback (see *Gemini retries* below); a per-model quota cooldown with `check_quota` / `QuotaExceeded` and the pure `quota_cooldown` parser (see *Gemini quota*). |
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
   A bare `/food` or `/sport` (tapped from Telegram's command menu) answers with a `ForceReply`
   prompt and the reply to it is recorded - the `InputPrompt` filter recognises the prompt by its
   text prefix, so it survives a restart. Those two handlers are registered in `commands`, the
   first router inside `guarded`, on purpose: a bare number answering the food prompt must be read
   as food rather than grabbed by `weight`. A delete word answering either prompt (`PromptCancel`,
   registered before them) cancels the command instead: no Gemini call, no row.
2. `water` - `/вода` (`/water`, `/voda`): show, set or cancel the sender's water reminders.
3. `corrections` - a reply to a bot message that starts with `≈` (the food-estimate prefix).
   A number -> `update_food_kcal(user_id, message_id)`. A delete word (`parsing.is_delete_request`)
   -> `delete_food_entry`, registered first so "видали" is never shipped to Gemini as a correction.
   `parsing.is_delete_request` accepts a delete verb plus filler words only ("видали цей
   запис"), so "прибери хліб" - drop an ingredient from the estimate - stays a correction
   of the dish. Any other text -> `get_food_entry`,
   `GeminiClient.revise_food` with the earlier estimate + the user's text - text model only, the
   photo is never re-sent -> new `≈` reply -> `update_food_entry`, which also re-keys
   the row to the new reply's `message_id` so corrections can be chained. All three are scoped to
   the sender, so only the author of an entry can correct or delete it and equal `message_id`s from
   different groups never collide. All three replies end with the total for the day the entry
   belongs to ("за сьогодні" or "за <date>"), read back from the sheet after the write.
4. `weight` - a bare number in `[WEIGHT_MIN, WEIGHT_MAX]` that is not a reply, or a number in
   reply to the morning ping (recognised by the ping text, so it survives restarts) -> `add_weight`.
5. `photos` - any photo -> Gemini vision -> reply -> `add_food` with the *reply's* `message_id`
   so a later correction can find the row.
6. `sport` - a delete word in reply to a bot message starting with `Спорт:` -> `delete_sport_entry`.
   That is the whole router: any other reply to a sport confirmation is ignored, re-estimating an
   activity is not a thing the bot does.

Recording sport has no router of its own: free text is never scanned for sport keywords (too many
false positives in a chatty group), so `sport.record_sport` is reached only from `/sport` and its
prompt reply -> Gemini text parse -> kcal from the MET table -> reply -> `add_sport` with the
reply's `message_id`, the same ordering `photos.record_food` uses.

**Deletes are hard deletes** (`worksheet.delete_rows`), not a `deleted` flag: every aggregation
(`day_food`, `today_summary`, `build_weekly_payload`, `user_rows_between`) then stays correct
without learning about a flag. `delete_food_entry` returns the row it removed so the handler can
total that entry's day again without a second scan of the tab.

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
`WEEKLY_REPORT_TIME` in `DEFAULT_TZ` (default Monday 09:00): build payload -> Gemini -> send ->
`reports` tab. If Gemini fails, the numeric summary is sent instead. The window is always the 7
*full* days before the run (`today - 7 .. today - 1`), so the current, half-logged day never
skews the numbers. A chat with a positive id is a DM: it gets `PERSONAL_REPORT_PROMPT` instead of
the group one, and the scheduled run skips it entirely when that user is also an active member of
one of the allowed group chats (they already get their numbers there). `/week` ignores that skip -
an explicit ask is always answered.

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

**Gemini retries.** `GeminiClient._generate` makes up to three attempts (`_ATTEMPT_TIMEOUTS_S`)
with backoff sleeps of 1.5 s and 3 s, the same shape as `sheets.with_retry`. The deadline
*escalates* per attempt - 45 s, 75 s, 120 s - because the SDK turns `http_options.timeout` into an
`X-Server-Timeout` header: Google's backend enforces our own deadline and answers
`504 DEADLINE_EXCEEDED` when the model needs longer, so retrying with the same 45 s during busy
hours can never succeed. Each attempt therefore passes its own `types.HttpOptions` on the
`GenerateContentConfig`; per-request options win over the client-level ones in the SDK and drive
both the transport timeout and that header. The outer `asyncio.wait_for` keeps a `_TIMEOUT_GRACE_S`
margin over the attempt's deadline so it stays a backstop and the SDK's informative error surfaces
first. Retried: transient API codes (408/5xx), an empty response body, and aiohttp/httpx
transport failures (both arrive transitively, so the imports are guarded); 400/403/404 break out
after one attempt - a bad key or a retired model will not fix itself.

**Gemini quota.** A 429 is *not* transient: it means the model's free-tier budget is spent, so
`_generate` puts that **model** into a cooldown (`GeminiClient._cooldowns`) and raises
`QuotaExceeded` at once, without a second attempt and without the "retrying" notice - another
request would only burn another unit of the same counter. The cooldown is per model on purpose:
the vision model's requests-per-day runs out long before the text one's, and `/їжа <текст>`,
`/спорт`, corrections and the weekly report must keep working when photos no longer do. Its
length comes from the pure `quota_cooldown(details)`, which reads the 429 body: a
`google.rpc.QuotaFailure` whose `quotaId`/`quotaMetric` mentions "per day" means waiting for the
next midnight in `America/Los_Angeles` (where Google resets the daily counters), anything else
uses the `google.rpc.RetryInfo` `retryDelay`, floored at 30 s (a 1 s delay would make the cooldown
pointless) and defaulted to 60 s. Not every 429 carries either part, so the parser never raises
and falls through to that default. The daily wait is measured in *elapsed* seconds (both ends go
through UTC before the subtraction, because CPython ignores a shared `tzinfo` and would otherwise
count wall-clock hours across a DST switch) and capped at 24 h, which errs the safe way. The
register is in memory only: a restart forgets it and the next 429 simply re-arms it. `check_quota` is called at the top of every attempt (a concurrent call
may have armed the cooldown while this one slept) and, ahead of everything else, by
`photos.on_photo` - a photo download and a Sheets read are not worth paying for just to learn the
vision quota is gone. The user gets one of four Ukrainian messages (`i18n.quota_notice`, chosen by
*which* model ran out and *whether* it was a per-day limit); the photo ones point at `/їжа
<текст>`, which still works - unless `GEMINI_VISION_MODEL` and `GEMINI_TEXT_MODEL` name the
same model, in which case `_is_vision_outage` drops that advice, because text is equally gone. `scheduler.run_weekly_report` needs no change: it already catches
`Exception` around the Gemini call and degrades to the numbers-only fallback.

The four public methods take an optional `on_retry` callback that fires *once* per call, just
before the first backoff sleep. The interactive call sites (`photos.on_photo`,
`commands._record_food_text`, `corrections.on_text_correction`, `sport.record_sport`) pass
`partial(message.reply, i18n.AI_RETRYING)`, so somebody waiting on a slow estimate is told the
answer is late instead of staring at silence; the notice is left in the chat. Its wording names no
cause, because the same notice covers a deadline, a 5xx and a dropped connection. A send that fails is
logged and the retry continues. `scheduler.run_weekly_report` passes no callback: nobody waits on
a background job and it already degrades to the numbers-only fallback.

**Errors.** A global error handler logs the exception and replies with a short "не вийшло,
спробуй ще" (it only fires when a handler matched, so the sender was always waiting). One
exception is special-cased: `ai.QuotaExceeded` gets its own message (`i18n.quota_notice`) and a
WARNING-level log line with the model and the cooldown instead of a traceback - a spent free-tier
quota is an expected, self-healing condition, not a bug to hunt. The polling loop never dies
because of a handler.

**Trailing columns.** `food.portion` (the portion size the model priced, shown on the `≈` line so
the user can see what the calories were computed for) and `sport.message_id` (the confirmation a
delete replies to) are the *last* entries of their `HEADERS` lists: an existing spreadsheet then
only gains a trailing column instead of having every value shifted right.

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
